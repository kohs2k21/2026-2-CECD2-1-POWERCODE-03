"""관계 규칙(`relationRules[]`) 스냅샷 읽기·판정 선택·판정 — contracts/relation-rules.md 2·3·5절, protocol-rules.md 10절 5단계.

- 판정 kind 셋(`precedes`·`message_handoff`·`required_step`)은 hard 방법(`hard:rel-<8hex>`), 위반 T = 이상.
  `allowed_branch`는 정보 표시만(relationNotes, 방법 아님).
- DB를 읽지 않는 순수 함수다. 입력은 시도 시작 때 읽은 관계 입력(relation_inputs)과 판정 관측 원래 값(raw)이다.
- 결과는 둘 중 하나다: 방법 결과(N·T·방법 결과 보류 — 과제로 만든다) 또는 relationNotes 항목(수집 미완료 보류·
  적용 안 함·정보 기록 — 과제·n에 넣지 않는다, relation-rules.md 3.3).
- 첫 판정의 `held`·`deferred_to_transaction` 기록은 재판정 탐색(10절 7단계, recheck.py·recheck_store.py)이 읽어
  거래 행 도착 확정 뒤 종합이 바뀌는 판정만 1회 다시 판정한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from derived_expression import TABLE, evaluate as evaluate_derived
from detector.hard_rules import is_relation_rule_id
from detector.method_results import SEVERITIES, method_result
from detector.relation_graph import (edge_id, graph_trim, graph_value_absent, missing_id, process_id,
                                     transaction_id, unkeyed_id)
from detector.relation_inputs import RelationInputs

PRECEDES = "precedes"
MESSAGE_HANDOFF = "message_handoff"
REQUIRED_STEP = "required_step"
ALLOWED_BRANCH = "allowed_branch"
KINDS = (PRECEDES, MESSAGE_HANDOFF, REQUIRED_STEP, ALLOWED_BRANCH)
JUDGMENT_KINDS = frozenset({PRECEDES, MESSAGE_HANDOFF, REQUIRED_STEP})
# 후행(`to`) PROCESS 판정에서 고르는 kind. required_step은 거래 단위(거래 행 interface_id)로 고른다.
SUCCESSOR_KINDS = frozenset({PRECEDES, MESSAGE_HANDOFF, ALLOWED_BRANCH})
HANDOFF_MATCH = {"from": "OUT", "to": "IN", "key": "message_id"}
_SUCCESSOR_SCOPE_KEYS = frozenset({"interfaceId", "adapterType"})


@dataclass(frozen=True)
class RelationRule:
    """검증한 관계 규칙 1개(켜진 것만 시도에 고정). scope 값은 판정 키만: required_step은 interfaceId, 나머지는 adapterType."""
    id: str
    kind: str
    from_process_id: str
    to_process_id: str | None
    interface_id: str | None
    adapter_type: str | None
    severity: str

    @property
    def method_version(self) -> str:
        return f"relation-{self.kind}-v1"

    @property
    def code(self) -> str:
        return "REL_" + self.kind.upper()


def _present_text(value: Any) -> bool:
    return isinstance(value, str) and not graph_value_absent(value)


def _step(value: Any) -> str | None:
    """정규형 `{processId}`만(다른 키는 저장 거절 대상 — 판정 시 fail-closed)."""
    if not isinstance(value, Mapping) or set(value) != {"processId"} or not _present_text(value["processId"]):
        return None
    return value["processId"]


def _read_rule(item: Any) -> tuple[RelationRule, bool]:
    """(규칙, enabled). 모르는 kind는 꺼져 있어도 relation_rule_kind_unknown, 모양 위반은 invalid_relation_rule."""
    rule_id = item.get("id") if isinstance(item, Mapping) else None
    kind = item.get("kind") if isinstance(item, Mapping) else None
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError(f"relation_rule_kind_unknown:{rule_id}")
    invalid = ValueError(f"invalid_relation_rule:{rule_id}")
    if not is_relation_rule_id(rule_id) or type(item.get("enabled")) is not bool or \
            not isinstance(item.get("severity"), str) or item["severity"] not in SEVERITIES:
        raise invalid
    source, target, scope, match = _step(item.get("from")), item.get("to"), item.get("scope"), item.get("match")
    if source is None:
        raise invalid
    if kind == REQUIRED_STEP:
        if target is not None or match is not None or not isinstance(scope, Mapping) or \
                set(scope) != {"interfaceId"} or not _present_text(scope["interfaceId"]):
            raise invalid
        return RelationRule(rule_id, kind, source, None, scope["interfaceId"], None, item["severity"]), item["enabled"]
    target = _step(target)
    if target is None or (match != HANDOFF_MATCH if kind == MESSAGE_HANDOFF else match is not None):
        raise invalid
    if target == source:
        # 자기 자신이 선행인 규칙은 비교할 간선이 없다(그래프 selfDependency). 저장 422(rule-set.md v3.6)와 같이 거부.
        raise invalid
    if scope is not None and (not isinstance(scope, Mapping) or not scope or set(scope) - _SUCCESSOR_SCOPE_KEYS or
                              not all(_present_text(value) for value in scope.values())):
        raise invalid
    adapter = scope.get("adapterType") if scope else None
    return RelationRule(rule_id, kind, source, target, None, adapter, item["severity"]), item["enabled"]


def read_relation_rules(items: Any) -> tuple[RelationRule, ...]:
    """v3 스냅샷 relationRules[]를 저장 순(id 사전순 저장)으로 검증하고 켜진 규칙만 돌려준다.

    꺼진 규칙도 검사한다(저장 검증을 거치지 않은 행이면 판정 전체를 막는다 — hard_rule_unknown과 같은 취급).
    """
    if not isinstance(items, list):
        raise ValueError("invalid_rule_set_snapshot:relationRules")
    seen: set[str] = set()
    enabled = []
    for item in items:
        rule, on = _read_rule(item)
        if rule.id in seen:
            raise ValueError("duplicate_method_id")
        seen.add(rule.id)
        if on:
            enabled.append(rule)
    return tuple(enabled)


def _own(raw: Mapping[str, Any], key: str) -> str | None:
    """판정 관측 원래 값. 문자열이 아니면 data_processes 열과 같이 None(collector `_typed_values`)."""
    value = raw.get(key)
    return value if isinstance(value, str) else None


def successor_selected(rule: RelationRule, raw: Mapping[str, Any]) -> bool:
    """relation-rules.md 2절: `to.processId` = 판정 PROCESS `process_id`(원래 값 정확 일치, 없음이면 어떤 규칙도 아님)
    ∧ `scope.adapterType`이 있으면 `adapter_type` 정확 일치."""
    if rule.kind not in SUCCESSOR_KINDS:
        return False
    own = _own(raw, "process_id")
    if graph_value_absent(own) or own != rule.to_process_id:
        return False
    if rule.adapter_type is None:
        return True
    adapter = _own(raw, "adapter_type")
    return not graph_value_absent(adapter) and adapter == rule.adapter_type


def relation_rules_need_inputs(rules: tuple[RelationRule, ...], raw: Mapping[str, Any] | None) -> bool:
    """required_step은 거래 행으로 고르므로 켜져 있으면 항상 읽는다. 후행 kind는 이 판정에서 골린 것이 있을 때만."""
    return any(rule.kind == REQUIRED_STEP or raw is None or successor_selected(rule, raw) for rule in rules)


def note(rule_id: str | None, kind: str, result: str, reason: str | None,
         waiting_for: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """relationNotes 항목 `{ruleId, kind, result, reason, waitingFor}`(relation-rules.md 5절)."""
    return {"ruleId": rule_id, "kind": kind, "result": result, "reason": reason,
            "waitingFor": None if waiting_for is None else dict(waiting_for)}


Outcome = tuple[str, dict[str, Any]]  # ("method", 방법 결과) | ("note", relationNotes 항목)


def _hold(rule: RelationRule, cause: str, reason: str) -> Outcome:
    """방법 결과 보류(데이터 문제·입력 상한): not_ready, 기존 CAUSES + evidence [{reason}](relation-rules.md 5절)."""
    return "method", method_result(rule.id, rule.method_version, "not_ready", None, cause=cause,
                                   evidence=[{"reason": reason}])


def _decided(rule: RelationRule, hit: bool, summary: Mapping[str, Any]) -> Outcome:
    """N·T. evidence에는 비교한 노드·간선 id와 결과만 둔다(원천 값 복사 없음, detection-decision.md 11.5)."""
    return "method", method_result(rule.id, rule.method_version, "succeeded", hit, code=rule.code,
                                   severity=rule.severity,
                                   evidence=[{"result": "hit" if hit else "no_hit", **summary}])


def _note(rule: RelationRule, result: str, reason: str | None, waiting_for: Mapping[str, Any] | None = None) -> Outcome:
    return "note", note(rule.id, rule.kind, result, reason, waiting_for)


class _Context:
    """판정 1건의 공통 값. 그래프 간선 색인은 한 번만 만든다."""

    def __init__(self, inputs: RelationInputs, raw: Mapping[str, Any]):
        self.inputs, self.raw = inputs, raw
        self.process = _own(raw, "process_id")
        self.depends = _own(raw, "depend_process_id")
        self.tx = inputs.focus_transaction_id
        keyed = inputs.transaction_keyed and not graph_value_absent(self.process)
        self.node = process_id(self.tx, self.process) if keyed else unkeyed_id(inputs.focus_observation_id)
        # 거래 행 도착 확정(최소 지연 뒤, relation_inputs.TRANSACTION_SETTLE_SECONDS) — 구조 전제 "그 거래의 PROCESS가
        # 다 기록됨"(3.1)을 이때만 쓴다. 확정 전 "없음"은 위반이 아니라 수집 미완료 보류(재판정 대상).
        self.arrived = inputs.transaction_confirmed
        self._edges: dict[str, Mapping[str, Any]] | None = None

    @property
    def edges(self) -> dict[str, Mapping[str, Any]]:
        if self._edges is None:
            self._edges = {edge["id"]: edge for edge in self.inputs.graph["edges"]}
        return self._edges

    def depends_class(self) -> str:
        """판정 관측 자신의 DEPEND_PROCESS_ID: start(정확히 NONE) / absent(NULL·""·\\N) / value."""
        if self.depends is None:
            return "absent"
        trimmed = graph_trim(self.depends)
        if trimmed == "NONE":
            return "start"
        return "absent" if graph_value_absent(self.depends) else "value"

    def predecessor_edge(self, from_process_id: str) -> tuple[str, Mapping[str, Any] | None]:
        """from→판정 PROCESS precedes 간선(선행 묶음이 있으면 그 노드, 없으면 missing 노드에서 온 간선)."""
        predecessor = process_id(self.tx, from_process_id)
        edge = self.edges.get(edge_id("precedes", predecessor, self.node))
        if edge is None:
            edge = self.edges.get(edge_id("precedes", missing_id(self.node, from_process_id), self.node))
        if edge is None:
            # 자신의 값 = from이고 from ≠ to(스냅샷 검증)이면 그래프에 이 간선이 항상 있다. 없으면 입력·그래프 불일치.
            raise ValueError("relation_graph_inconsistent")
        return predecessor, edge


def _common_keys(rule: RelationRule, ctx: _Context) -> Outcome | None:
    """3.1 "키 값이 없을 때" → 방법 결과 보류(데이터 문제), 그다음 PROCESS 입력 상한 → 입력 상한."""
    if ctx.depends_class() == "absent":
        return _hold(rule, "input_missing", "depend_process_id_missing")
    if not ctx.inputs.transaction_keyed:
        return _hold(rule, "input_missing", "transaction_id_missing")
    if ctx.inputs.processes_truncated:
        # 그래프가 잘렸으면 간선이 verified여도 N을 내지 않는다(5절 표). 판단은 읽은 건수(relationInputs.truncated).
        return _hold(rule, "capacity", "graph_truncated")
    return None


def _precedes(rule: RelationRule, ctx: _Context) -> Outcome:
    """3.1 precedes 평가 순서: (1) 자신의 값으로 T → (2) 키 결측 → (3) 간선 상태."""
    kind = ctx.depends_class()
    if kind == "start" or (kind == "value" and ctx.depends != rule.from_process_id):
        return _decided(rule, True, {"violation": "start_recorded" if kind == "start" else "other_predecessor",
                                     "node": ctx.node, "edge": None})
    held = _common_keys(rule, ctx)
    if held:
        return held
    predecessor, edge = ctx.predecessor_edge(rule.from_process_id)
    if edge["verification"] == "verified":
        return _decided(rule, False, {"node": ctx.node, "from": predecessor, "edge": edge["id"]})
    if edge["verification"] == "ambiguous":
        return _hold(rule, "input_invalid", "precedes_ambiguous")
    # 남은 경우는 missing 노드에서 온 unmatched 간선(not_logged). not_loaded는 PROCESS 상한 초과라 위에서 보류됐다.
    if ctx.arrived:
        # [확정 11/12 사용자] 거래 행이 왔는데 선행 단계 행이 없음 = 위반(수집 미완료가 아님, 3.1 구조 전제).
        return _decided(rule, True, {"violation": "predecessor_missing", "node": ctx.node, "from": predecessor,
                                     "edge": edge["id"]})
    return _note(rule, "held", "collection_incomplete", {"processId": rule.from_process_id})


def _message_handoff(rule: RelationRule, ctx: _Context) -> Outcome:
    """3.1 message_handoff: from→to precedes 간선이 verified일 때만, hands_off 간선별 result로 판단."""
    kind = ctx.depends_class()
    if kind == "start" or (kind == "value" and ctx.depends != rule.from_process_id):
        return _note(rule, "not_applicable", "precedes_absent")  # 그 위반은 precedes 규칙 몫
    held = _common_keys(rule, ctx)
    if held:
        return held
    predecessor, edge = ctx.predecessor_edge(rule.from_process_id)
    if edge["verification"] == "ambiguous":
        return _hold(rule, "input_invalid", "precedes_ambiguous")
    if edge["verification"] != "verified":
        # missing 노드에서 온 unmatched 간선(not_logged) — not_loaded는 상한 초과라 위에서 보류됐다.
        if ctx.arrived:
            # 거래 행 도착 확정 뒤에도 선행 행 없음: 선행 위반은 precedes 몫이고 인계는 확인할 대상이 없다.
            return _note(rule, "not_applicable", "predecessor_missing")
        return _note(rule, "held", "collection_incomplete", {"processId": rule.from_process_id})
    if ctx.inputs.messages_truncated:
        return _hold(rule, "capacity", "graph_truncated")
    handoffs = [e for e in ctx.inputs.graph["edges"]
                if e["kind"] == "hands_off" and e["from"] == predecessor and e["to"] == ctx.node]
    if not handoffs:
        # hands_off 간선이 없으면 precedes 간선 evidence(no_out_message)에만 사유가 있다(그래프 hands_off 절).
        return _note(rule, "held", "collection_incomplete", {"processId": rule.from_process_id, "direction": "OUT"})
    results = [(e["evidence"]["result"], e["evidence"]["reason"]) for e in handoffs]
    summary = {"node": ctx.node, "from": predecessor, "edge": edge["id"],
               "handoffs": [{"edge": e["id"], "result": e["evidence"]["result"]} for e in handoffs]}
    reasons = {reason for _, reason in results}
    if any(result == "match" for result, _ in results):
        return _decided(rule, False, summary)
    if any(result == "mismatch" for result, _ in results):
        return _decided(rule, True, summary)
    if "missing_message_id" in reasons:
        return _hold(rule, "input_missing", "message_id_missing")
    if "multiple_messages" in reasons:
        return _hold(rule, "input_invalid", "message_link_ambiguous")
    if "excluded_transaction_suffix" in reasons:
        return _note(rule, "not_applicable", "excluded_transaction_suffix")
    # no_in_message: 거래 행 뒤에도 없으면 보류로 끝(메시지가 거래 행보다 먼저 기록되는지 확인 전, 5절 표).
    return _note(rule, "held", "collection_incomplete", {"processId": rule.to_process_id, "direction": "IN"})


def _allowed_branch(rule: RelationRule, ctx: _Context) -> Outcome:
    """정보 표시만: 판정 관측의 선행이 이 분기(from)이면 observed, 아니면 not_in_pattern.

    선행 값이 없음(NULL·""·\\N)이면 어느 분기인지 알 수 없어 not_applicable / depend_process_id_missing.
    """
    kind = ctx.depends_class()
    if kind == "absent":
        return _note(rule, "not_applicable", "depend_process_id_missing")
    taken = kind == "value" and ctx.depends == rule.from_process_id
    return _note(rule, "observed" if taken else "not_in_pattern", None)


_TIME_KEYS = {"START_TIME": ("start_time_raw", "startTimeRaw"), "END_TIME": ("end_time_raw", "endTimeRaw")}


def _parsed(rows: tuple[Mapping[str, Any], ...], column: str) -> list[datetime | None]:
    """처리 시간(datetime_diff)과 같은 해석. 시간대 있는 값과 없는 값이 섞이면 비교할 수 없어 모두 해석 불가로 둔다.

    relation-rules.md 3.1은 관측 `parsed_times`를 적었지만 수집은 그 열을 비워 둔다(`'{}'`, collector storage) —
    원천 시각 문자열을 쓴다(백엔드 thresholdStats와 같은 이유).
    """
    raw_key, row_key = _TIME_KEYS[column]
    values = [evaluate_derived({"op": "column", "table": TABLE, "column": column}, {raw_key: row.get(row_key)})
              for row in rows]
    aware = {value.tzinfo is not None and value.utcoffset() is not None for value in values if value is not None}
    return [None] * len(values) if len(aware) > 1 else values


def latest_ended_is_focus(inputs: RelationInputs) -> bool:
    """3.1 "붙이는 판정": END_TIME 최대 → START_TIME 최대 → 관측 id 문자열 내림차순. 결측·해석 불가는 값 있는 것 뒤."""
    rows = inputs.processes
    ends, starts = _parsed(rows, "END_TIME"), _parsed(rows, "START_TIME")

    def key(index: int) -> tuple:
        # 있음 여부를 먼저 비교하므로 결측 자리값(datetime.min)은 결측끼리만 만난다. 값끼리는 _parsed가 시간대 유무를 맞춘다.
        end, start = ends[index], starts[index]
        return (end is not None, end or datetime.min, start is not None, start or datetime.min,
                rows[index]["observationId"])

    winner = max(range(len(rows)), key=key, default=None)
    return winner is not None and rows[winner].get("focus") is True


def _required_step_context(inputs: RelationInputs) -> tuple[str, Any]:
    """거래 단위 검사 상태(3.1 required_step). 반환: (상태, 값)."""
    if not inputs.transaction_keyed:
        return "hold_all", ("input_missing", "transaction_id_missing")
    if inputs.transaction_executions == 0 or not inputs.transaction_confirmed:
        # 거래 행이 없거나 도착 확정 전(최소 지연 안): 그 거래의 PROCESS가 다 왔다고 볼 수 없어 평가를 미룬다.
        return "deferred", None
    if inputs.processes_truncated:
        # 가장 늦게 끝난 PROCESS를 정할 수 없다(입력 상한). 모든 판정에서 방법 결과 보류.
        return ("hold_all" if inputs.transaction_ambiguous else "hold_selected"), ("capacity", "graph_truncated")
    if not latest_ended_is_focus(inputs):
        return "skip", None
    if inputs.transaction_ambiguous:
        return "hold_all", ("input_invalid", "transaction_row_ambiguous")
    return "evaluate", None


def judge_relations(rules: tuple[RelationRule, ...], inputs: RelationInputs,
                    raw: Mapping[str, Any]) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    """켜진 관계 규칙을 저장 순으로 판정한다. 반환: (과제로 만들 [(methodId, 방법 결과)], relationNotes 항목들).

    required_step의 `deferred_to_transaction`(거래 행 없음)은 인터페이스를 아직 정하지 못해 규칙마다가 아니라 한
    항목(`ruleId: null`)으로 남긴다(protocol-rules.md 5.1과 같은 모양). 거래 행이 있는데 가장 늦게 끝난 PROCESS가
    아닌 판정은 기록 없이 뺀다. 거래 ID 없음·거래 행 모호·입력 상한은 규칙마다 방법 결과 보류다.
    """
    ctx = _Context(inputs, raw)
    judge: dict[str, Callable[[RelationRule, _Context], Outcome]] = {
        PRECEDES: _precedes, MESSAGE_HANDOFF: _message_handoff, ALLOWED_BRANCH: _allowed_branch}
    methods: list[tuple[str, dict[str, Any]]] = []
    notes: list[dict[str, Any]] = []
    required: tuple[str, Any] | None = None
    for rule in rules:
        if rule.kind == REQUIRED_STEP:
            if required is None:
                required = _required_step_context(inputs)
                if required[0] == "deferred":
                    # 첫 판정 때 거래 행이 아직 없음: 평가하지 않음, 방법·보류 아님(n 밖). 거래 행 도착 재판정(7단계) 대상.
                    notes.append(note(None, REQUIRED_STEP, "deferred_to_transaction", None, {"layer": "T"}))
            state, value = required
            interface = inputs.transaction.get("interfaceId") if inputs.transaction else None
            matched = interface is not None and interface == rule.interface_id
            if state == "hold_all" or (state == "hold_selected" and matched):
                methods.append((rule.id, _hold(rule, *value)[1]))
            elif state == "evaluate" and matched:
                steps = {row.get("processId") for row in inputs.processes}
                present = rule.from_process_id in steps
                outcome = _decided(rule, not present, {
                    "node": ctx.node, "transaction": transaction_id(ctx.tx),
                    "step": process_id(ctx.tx, rule.from_process_id), "present": present})
                methods.append((rule.id, outcome[1]))
            continue
        if not successor_selected(rule, raw):
            continue  # 선택되지 않은 규칙은 methodResults·n·relationNotes 어디에도 넣지 않는다(2절)
        kind, value = judge[rule.kind](rule, ctx)
        if kind == "method":
            methods.append((rule.id, value))
        else:
            notes.append(value)
    return methods, notes
