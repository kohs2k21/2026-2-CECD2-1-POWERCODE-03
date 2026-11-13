"""프로토콜 규칙(`protocolRules[]`, 인터페이스별 허용 경로) 스냅샷 읽기·경로 만들기·판정 — contracts/protocol-rules.md.

- 경로(3.2): 그래프 층 A의 PROCESS 묶음(같은 거래·같은 `process_id`, 재시도는 한 묶음)에서 `DEPEND_PROCESS_ID` 사슬을
  시작 단계(정확히 `NONE`)부터 끝 단계까지 따라간 `channel_id` 원래 값 목록. 연속 같은 채널을 합치지 않고, 갈라지면
  끝 단계마다 경로 1개. 한 단계라도 걸리면 그 거래는 경로 없음(사유 하나 — 3.4 "사유 선택 전체 순서").
  Trainer 초안 집계(`protocolPathDrafts`)와 같은 정의·같은 사유 이름·같은 순서다.
- 판정 자리(5.1): 관계 규칙 `required_step`과 같다 — 거래 행 도착 확정(relation-rules.md 3.4) 뒤, 그 거래에서 가장 늦게
  끝난 PROCESS의 판정. 확정 전은 relationNotes `deferred_to_transaction`.
- 결과는 방법 결과(N·T·방법 결과 보류) 또는 relationNotes 항목(적용 안 함·정보 기록)이다. 한 판정에 프로토콜 방법은
  많아야 1개(그 인터페이스 항목 `hard:proto-<id>` 또는 내장 `hard:unregistered-interface`).
- DB를 읽지 않는 순수 함수다. 입력은 시도 시작 때 읽은 관계 입력(relation_inputs)이다. 첫 판정의 `deferred_to_transaction`은
  재판정 탐색(10절 7단계, recheck_store)이 거래 행 도착 확정 뒤 1회 다시 판정한다.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from detector.hard_rules import UNREGISTERED_INTERFACE, is_protocol_rule_id
from detector.method_results import SEVERITIES, method_result
from detector.relation_graph import (escape_id_component, graph_trim, graph_value_absent, process_id,
                                     retry_group_ambiguous)
from detector.relation_inputs import RelationInputs
from detector.relation_rules import latest_ended_is_focus, note

PROTOCOL = "protocol"
PROTOCOL_PATH_VERSION = "protocol-path-v1"
UNREGISTERED_VERSION = "unregistered-interface-v1"
PATH_UNMATCHED_CODE = "PROTO_PATH_UNMATCHED"
UNREGISTERED_CODE = "PROTO_INTERFACE_UNREGISTERED"
REVIEW_VERSION = 1
MAX_PATH_STEPS = 64  # 경로 하나의 단계 수 상한(3.2 8단계)
MAX_ROUTES = 64  # 거래 하나의 경로 수 상한(3.2 8단계)
MAX_STORED_CHANNELS = 64  # 저장 경로 하나의 채널 수 상한(4절)
MAX_STORED_PATHS = 64  # 항목 하나의 허용 경로 수 상한(4절)

# 3.4 "사유 선택 전체 순서" 중 판정이 쓰는 부분: 3.1a 사유 → 3.2 사유(graph_truncated가 맨 앞).
# Trainer 사전 제외(cross_split·anomaly_label·no_transaction_row)는 판정에 없다.
INTERFACE_REASONS = ("transaction_row_ambiguous", "interface_absent")
PATH_REASONS = ("graph_truncated", "process_id_missing", "precedes_ambiguous", "depend_process_id_missing",
                "start_ambiguous", "chain_broken", "channel_absent", "channel_ambiguous", "path_too_long",
                "routes_too_many")
REASON_ORDER = INTERFACE_REASONS + PATH_REASONS
# 3.4 표의 판정 cause(기존 닫힌 집합 CAUSES만).
REASON_CAUSES = {"transaction_row_ambiguous": "input_invalid", "interface_absent": "input_missing",
                 "graph_truncated": "capacity", "process_id_missing": "input_missing",
                 "precedes_ambiguous": "input_invalid", "depend_process_id_missing": "input_missing",
                 "start_ambiguous": "input_invalid", "chain_broken": "input_invalid",
                 "channel_absent": "input_missing", "channel_ambiguous": "input_invalid",
                 "path_too_long": "capacity", "routes_too_many": "capacity"}


@dataclass(frozen=True)
class ProtocolRule:
    """검증한 프로토콜 항목 1개(꺼진 항목도 담는다 — 꺼진 항목이 있는 인터페이스는 적용 안 함, 4.1)."""
    id: str
    interface_id: str
    paths: frozenset[tuple[str, ...]]
    severity: str
    enabled: bool


def _present_text(value: Any) -> bool:
    return isinstance(value, str) and not graph_value_absent(value)


def _read_entry(item: Any) -> ProtocolRule:
    """4절 모양. 저장 검증(rule-set.md v3 422)을 거치지 않은 값이면 시도 실패 `invalid_protocol_rule:<id>`."""
    rule_id = item.get("id") if isinstance(item, Mapping) else None
    invalid = ValueError(f"invalid_protocol_rule:{rule_id}")
    if not is_protocol_rule_id(rule_id) or type(item.get("enabled")) is not bool or \
            not isinstance(item.get("severity"), str) or item["severity"] not in SEVERITIES or \
            not _present_text(item.get("interfaceId")):
        raise invalid
    paths = item.get("paths")
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_STORED_PATHS:
        raise invalid
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        channels = path.get("channels") if isinstance(path, Mapping) else None
        if not isinstance(channels, list) or not 1 <= len(channels) <= MAX_STORED_CHANNELS or \
                not all(_present_text(channel) for channel in channels) or tuple(channels) in seen:
            raise invalid
        seen.add(tuple(channels))
    return ProtocolRule(rule_id, item["interfaceId"], frozenset(seen), item["severity"], item["enabled"])


def read_protocol_rules(items: Any) -> tuple[ProtocolRule, ...]:
    """v3 스냅샷 protocolRules[]를 저장 순으로 검증한다(꺼진 항목도 검사, 같은 id → duplicate_method_id,
    같은 interfaceId 항목 2개 → 뒤 항목 invalid_protocol_rule)."""
    if not isinstance(items, list):
        raise ValueError("invalid_rule_set_snapshot:protocolRules")
    rules, ids, interfaces = [], set(), set()
    for item in items:
        rule = _read_entry(item)
        if rule.id in ids:
            raise ValueError("duplicate_method_id")
        if rule.interface_id in interfaces:
            raise ValueError(f"invalid_protocol_rule:{rule.id}")
        ids.add(rule.id)
        interfaces.add(rule.interface_id)
        rules.append(rule)
    return tuple(rules)


def feature_on(rules: Iterable[ProtocolRule]) -> bool:
    """4.1: 프로토콜 기능 켜짐 = 켜진 항목 1개 이상."""
    return any(rule.enabled for rule in rules)


def path_key(channels: Iterable[str]) -> str:
    """5.3 pathKey: 채널마다 process-graph.md id 이스케이프 뒤 ':'로 잇는다(룰 세트 hash 정렬 키와 같은 함수)."""
    return ":".join(escape_id_component(channel) for channel in channels)


@dataclass(frozen=True)
class Route:
    """경로 하나: 채널 목록과 같은 위치의 단계(`process_id` 원래 값)."""
    channels: tuple[str, ...]
    steps: tuple[str, ...]


_START, _ABSENT = object(), object()


def _depend_class(value: Any) -> Any:
    """3.2 3단계 시도별 분류: 정확히 NONE(ASCII 공백류만 앞뒤에서 뺌) → start, NULL·""·\\N → absent, 그 밖 → 원래 값."""
    if not isinstance(value, str):
        return _ABSENT
    if graph_trim(value) == "NONE":
        return _START
    return _ABSENT if graph_value_absent(value) else value


_CONFLICT = object()


def _same(values: list[Any]) -> Any:
    first = values[0]
    return first if all(value is first or value == first for value in values[1:]) else _CONFLICT


def build_routes(processes: Iterable[Mapping[str, Any]], truncated: bool) -> tuple[str | None, tuple[Route, ...]]:
    """3.2 2~8단계. (사유 | None, 경로들). 사유가 있으면 경로 없음.

    사유 고르기: graph_truncated 먼저 → 단계 번호가 작은 것 → 같은 단계는 3.4 표 순서(모든 묶음을 본 뒤).
    아래 주석의 "n단계"는 protocol-rules.md 3.2 목록 번호다(10절 구현 단계 번호가 아님).
    경로 순서: 시작 단계에서 깊이 우선, 갈래는 process_id 코드 포인트 순(Trainer와 같음).
    """
    if truncated:
        return "graph_truncated", ()
    rows = list(processes)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not _present_text(row.get("processId")):
            return "process_id_missing", ()  # 2단계(같은 단계의 precedes_ambiguous보다 앞)
        grouped[row["processId"]].append(row)
    if any(retry_group_ambiguous(attempts) for attempts in grouped.values()):
        return "precedes_ambiguous", ()  # 2단계: 묶음 모호(process-graph.md)
    depends = {pid: _same([_depend_class(row.get("dependsOn")) for row in attempts])
               for pid, attempts in grouped.items()}
    if any(value is _CONFLICT for value in depends.values()):
        return "precedes_ambiguous", ()  # 3단계: 시도마다 분류가 다름
    if any(value is _ABSENT for value in depends.values()):
        return "depend_process_id_missing", ()  # 3단계
    starts = [pid for pid, value in depends.items() if value is _START]
    if len(starts) != 1:
        return "start_ambiguous", ()  # 4단계
    children: dict[str, list[str]] = defaultdict(list)
    for pid, value in depends.items():
        if value is _START:
            continue
        if value == pid or value not in grouped:
            return "chain_broken", ()  # 5단계: 자기 자신·없는 선행(그래프 selfDependency·missing)
        children[value].append(pid)
    reached, stack = set(), [starts[0]]
    while stack:
        pid = stack.pop()
        reached.add(pid)
        stack.extend(children.get(pid, ()))
    if len(reached) != len(grouped):
        return "chain_broken", ()  # 5단계: 시작에서 닿지 않는 묶음(순환 포함)
    channels: dict[str, str] = {}
    mixed = False
    for pid, attempts in grouped.items():
        values = [row.get("channelId") for row in attempts]
        present = [value for value in values if _present_text(value)]
        if not present:
            return "channel_absent", ()  # 6단계(같은 단계의 channel_ambiguous보다 앞 — 모든 묶음을 먼저 본다)
        mixed = mixed or len(present) != len(values) or len(set(present)) > 1
        channels[pid] = present[0]
    if mixed:
        return "channel_ambiguous", ()  # 6단계
    routes: list[list[str]] = []
    pending: list[list[str]] = [[starts[0]]]
    while pending:
        route = pending.pop()
        nexts = sorted(children.get(route[-1], ()))
        if not nexts:
            routes.append(route)
        pending.extend(route + [pid] for pid in reversed(nexts))  # 뒤에서 꺼내므로 역순으로 넣는다
    if any(len(route) > MAX_PATH_STEPS for route in routes):
        return "path_too_long", ()  # 8단계
    if len(routes) > MAX_ROUTES:
        return "routes_too_many", ()  # 8단계
    return None, tuple(Route(tuple(channels[pid] for pid in route), tuple(route)) for route in routes)


@dataclass(frozen=True)
class ProtocolOutcome:
    """판정 1건의 프로토콜 결과. methods는 0개 또는 1개, review는 T일 때만(evidence.protocolReview)."""
    methods: tuple[tuple[str, dict[str, Any]], ...] = ()
    notes: tuple[dict[str, Any], ...] = ()
    review: dict[str, Any] | None = None
    # 경로를 만들었으면 그 경로들(시험·근거용). 만들지 않았으면 None.
    routes: tuple[Route, ...] | None = field(default=None, compare=False)


def _note(result: str, reason: str | None, rule_id: str | None = None,
          waiting_for: Mapping[str, Any] | None = None) -> ProtocolOutcome:
    return ProtocolOutcome(notes=(note(rule_id, PROTOCOL, result, reason, waiting_for),))


def _hold(method_id: str, version: str, reason: str) -> ProtocolOutcome:
    """방법 결과 보류: not_ready, 3.4 표 cause + evidence [{reason}](relation-rules.md 5절 모양)."""
    return ProtocolOutcome(methods=((method_id, method_result(method_id, version, "not_ready", None,
                                                              cause=REASON_CAUSES[reason],
                                                              evidence=[{"reason": reason}])),))


def _interface(inputs: RelationInputs) -> tuple[str | None, str | None]:
    """3.1a: (인터페이스, 사유). 대표 행 모호면 모든 실행의 값이 같고 있을 때만 그 값."""
    if inputs.transaction_ambiguous:
        if inputs.common_interface_id is None or graph_value_absent(inputs.common_interface_id):
            return None, "transaction_row_ambiguous"
        return inputs.common_interface_id, None
    value = inputs.transaction.get("interfaceId") if inputs.transaction else None
    if not _present_text(value):
        return None, "interface_absent"
    return value, None


def _endpoints(inputs: RelationInputs, routes: tuple[Route, ...]) -> dict[str, Any]:
    """5.3 endpoints — 기록만, 판정에 쓰지 않는다(3.3 (b))."""
    if inputs.transaction_ambiguous or inputs.transaction is None:
        return {"start": None, "end": None, "matchesRoutes": None, "ambiguous": True}
    start, end = (value if _present_text(value) else None
                  for value in (inputs.transaction.get("channelId"), inputs.transaction.get("endChannelId")))
    matches = None if start is None or end is None or not routes else all(
        route.channels[0] == start and route.channels[-1] == end for route in routes)
    return {"start": start, "end": end, "matchesRoutes": matches, "ambiguous": False}


def _decided(method_id: str, version: str, code: str, severity: str, hit: bool, inputs: RelationInputs,
             matched_entry: bool, routes: tuple[Route, ...], matched: list[bool]) -> dict[str, Any]:
    tx = inputs.focus_transaction_id
    return method_result(method_id, version, "succeeded", hit, code=code, severity=severity, evidence=[{
        "interfaceMatched": matched_entry,
        "routes": [{"steps": [process_id(tx, step) for step in route.steps], "matched": ok}
                   for route, ok in zip(routes, matched)]}])


def judge_protocol(rules: tuple[ProtocolRule, ...], unregistered_level: str | None, inputs: RelationInputs, *,
                   rule_set_revision: int) -> ProtocolOutcome:
    """4.1 표·5절. unregistered_level: 켜진 `hard:unregistered-interface`의 위험도(꺼져 있으면 None)."""
    if not feature_on(rules):
        return ProtocolOutcome()  # 기능 꺼짐: 아래 모든 행보다 먼저, 기록 없음
    if not inputs.transaction_keyed:
        return _note("not_applicable", "transaction_id_missing")  # 3.1a: 평가하지 않음(풀릴 일이 없다)
    if inputs.transaction_executions == 0 or not inputs.transaction_confirmed:
        # 거래 행이 없거나 도착 확정 전: 인터페이스를 아직 모른다(ruleId null). 거래 행 도착 재판정(7단계) 대상.
        return _note("deferred_to_transaction", None, waiting_for={"layer": "T"})
    if not inputs.processes_truncated and not latest_ended_is_focus(inputs):
        return ProtocolOutcome()  # 붙이는 판정이 아님: 기록 없음. 입력 상한이면 붙이는 판정을 정할 수 없어 모든 판정에서 본다
    interface, reason = _interface(inputs)
    if interface is None:
        if unregistered_level is None:
            return _note("not_applicable", reason)
        return _hold(UNREGISTERED_INTERFACE, UNREGISTERED_VERSION, reason)
    entry = next((rule for rule in rules if rule.interface_id == interface), None)
    if entry is not None and not entry.enabled:
        return _note("not_applicable", "protocol_disabled", entry.id)
    if entry is None and unregistered_level is None:
        return ProtocolOutcome()  # 항목 없음 ∧ 미등록 규칙 꺼짐: 적용 안 함, 기록 없음
    routes_reason, routes = build_routes(inputs.processes, inputs.processes_truncated)
    if entry is not None:
        if routes_reason is not None:
            return _hold(entry.id, PROTOCOL_PATH_VERSION, routes_reason)
        matched = [route.channels in entry.paths for route in routes]
        hit = not all(matched)
        result = _decided(entry.id, PROTOCOL_PATH_VERSION, PATH_UNMATCHED_CODE, entry.severity, hit, inputs, True,
                          routes, matched)
        kind, rule_id = "path_unmatched", entry.id
    elif routes_reason == "graph_truncated":
        # 입력 상한: 붙이는 판정을 정할 수 없어 그 거래의 모든 판정에서 평가하므로 T가 아니라 보류로 둔다
        # (켜진 항목·required_step과 같은 규칙, 4.1). 판정마다 이상이 반복되는 것을 막는다.
        return _hold(UNREGISTERED_INTERFACE, UNREGISTERED_VERSION, routes_reason)
    else:
        # 미등록 인터페이스: 경로를 만들 수 없어도 T(인터페이스가 처음이라는 사실만으로 이상, 4.1). 입력 상한만 예외.
        matched = [False] * len(routes)
        hit = True
        result = _decided(UNREGISTERED_INTERFACE, UNREGISTERED_VERSION, UNREGISTERED_CODE, unregistered_level, True,
                          inputs, False, routes, matched)
        kind, rule_id = "interface_unregistered", None
    review = None
    if hit:
        review = {"version": REVIEW_VERSION, "kind": kind, "interfaceId": interface, "protocolRuleId": rule_id,
                  "ruleSetRevision": rule_set_revision,
                  "transactionObservationId": inputs.transaction["observationId"] if inputs.transaction else None,
                  "routes": [{"channels": list(route.channels), "pathKey": path_key(route.channels), "matched": ok}
                             for route, ok in zip(routes, matched)],
                  "routesReason": routes_reason,
                  "endpoints": _endpoints(inputs, routes)}
    return ProtocolOutcome(methods=((result["methodId"], result),), review=review, routes=routes)
