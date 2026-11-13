"""프로토콜 규칙(정상 경로) 초안 집계 — `recommend_relations` 결과 항목 `protocolPathDrafts`(실험 기능).

인터페이스별로 학습 스냅샷 train 분할에서 관측된 채널 경로를 센다. 화면 [관측 경로로 초안 만들기]가 사용자 확인·저장용
초안으로 쓴다. 판정은 Detector 몫이고 여기서는 집계만 한다(자동 채택 기준 없음).

정의는 Detector 계약 `contracts/protocol-rules.md`(3.1·3.1a·3.2 경로 만들기, 3.4 사유, 9절 Trainer 요청)를 따른다.
- 채널 = PROCESS 행 `channel_id` 원래 값. 경로 = 시작 단계(DEPEND_PROCESS_ID가 정확히 `NONE`)부터 DEPEND 사슬을 따라
  끝 단계까지 간 채널 목록. 연속 같은 채널을 합치지 않는다(위치 = 단계). 갈라지면 끝 단계마다 경로 1개
- 한 거래에서 어느 단계든 걸리면 그 거래 경로 전체를 버리고 사유 하나만 센다. 사유가 여럿이면 3.2 단계 번호가 작은 것,
  같은 단계면 3.4 표 순서(`graph_truncated`는 가장 먼저) — protocol-rules.md 3.4 사유 선택 전체 순서
- 인터페이스별 출력 경로는 저장 상한(4절, 항목당 64개)에 맞춰 앞 64개만 내고 나머지는 `omittedPaths`로 요약한다
- 거래 단위로 경로를 계산해 인터페이스별 카운터(경로마다 건수·예시 ≤ 5)만 남긴다. 거래 원본은 보관하지 않는다
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Sequence

from trainer.relation_candidates import (
    _CONFLICT, _MISSING, _START, MAX_COUNTEREXAMPLES, MIN_APPLICABLE, ProcessRow, TransactionRow, TxBundle, build_group,
    escape_component, graph_value_absent, process_ref, select_transaction_row, transaction_ref)

DRAFT_VERSION = 1
MAX_PROCESS_ROWS = 200  # 거래당 PROCESS(실행마다 최신 리비전) 상한, 넘으면 graph_truncated (protocol-rules.md 3.1)
MAX_PATH_STEPS = 64  # 경로 하나의 단계 수 상한, 넘으면 path_too_long (3.2 8단계)
MAX_ROUTES = 64  # 거래당 경로 수 상한, 넘으면 routes_too_many (3.2 8단계)
RARE_PERCENT = 1  # 경로 비율이 이 % 미만이면 rare: true(표시만)
MAX_OUTPUT_PATHS = 64  # 인터페이스별 출력 경로 수(protocol-rules.md 4절 저장 상한 항목당 경로 64개), 나머지는 omittedPaths

# 3.1a(인터페이스 정하기) 앞뒤 제외 — 인터페이스를 모르므로 전체 건수에만 센다(거래 단위).
# TRANSACTION_ID 없는 PROCESS 행(3.4 transaction_id_missing)은 거래로 묶이지 않아 행 수로 따로 센다(transactionIdMissingRows).
# cross_split·anomaly_label은 관계 후보와 같은 호출자 제외(train_bundle), no_transaction_row는 "거래 행이 있는 거래만" 규칙.
PRE_INTERFACE_REASONS = ("cross_split", "anomaly_label", "no_transaction_row",
                         "transaction_row_ambiguous", "interface_absent")
# 3.2 사유(3.4 표 순서). graph_truncated를 맨 앞에 둔다(입력이 잘렸으면 다른 판단을 믿을 수 없다).
PATH_REASONS = ("graph_truncated", "process_id_missing", "precedes_ambiguous", "depend_process_id_missing",
                "start_ambiguous", "chain_broken", "channel_absent", "channel_ambiguous", "path_too_long",
                "routes_too_many")
REASON_ORDER = PRE_INTERFACE_REASONS + PATH_REASONS
DEFINITION = ("protocol-rules.md 3.2: channel = data_processes.channel_id raw value; path = channels from the start step "
              "(DEPEND_PROCESS_ID exactly NONE after ASCII space/tab/CR/LF trim) along the DEPEND chain to each end step; "
              "consecutive equal channels are not merged; one path per end step when branching")
REASON_RULE = ("one reason per excluded transaction: smallest protocol-rules.md 3.2 step, ties by the 3.4 table order "
               "(graph_truncated first) - protocol-rules.md 3.4 full reason selection order")
COUNTING_RULE = ("transactions with a TRANSACTION row only, latest revision per execution (same rows as relation "
                 "candidates). A path is counted once per transaction even if several end steps give the same channels. "
                 "ratio = path transactions / pathTransactions (branching transactions add to several paths, so ratios "
                 "can sum above 1). excludeProcessIds is not applied (Detector judges with all PROCESS rows)")
UNIT_NOTE = ("excludedReasons counts transactions only. PROCESS rows without TRANSACTION_ID (protocol-rules.md 3.4 "
             "transaction_id_missing) are not grouped into transactions and are counted as rows in "
             "transactionIdMissingRows (each such row is its own unkeyed node in the graph)")
ENDPOINT_RULE = ("endpoint_mismatch (information only, not an exclusion): representative TRANSACTION row start_channel_id/"
                 "end_channel_id raw values vs first/last channel of every path. Not compared when either value is absent "
                 "or the representative row is ambiguous")
SELECTION_POLICY = ("no automatic adoption: observed paths are listed for the user to confirm and save. "
                    "status insufficient_evidence when pathTransactions < minTransactions (display only)")
OUTPUT_LIMIT_RULE = ("per interface, paths sorted by transactions desc then pathKey (code point) asc; only the first "
                     "maxOutputPaths are listed (protocol-rules.md 4 save limit: 64 paths per entry), the rest are "
                     "summarized in omittedPaths {paths, transactions}. ratio, rare, status and distinctPaths use all "
                     "paths before the cut. omittedPaths.transactions is the sum of per-path transaction counts, so a "
                     "branching transaction can be counted more than once")


def path_key(channels: Sequence[str]) -> str:
    """알림 묶음 키와 같은 형식(protocol-rules.md 5.3): 채널마다 그래프 id 이스케이프 뒤 ':'로 잇는다. 쪼개 읽지 않는다."""
    return ":".join(escape_component(channel) for channel in channels)


def transaction_interface(rows: Sequence[TransactionRow]) -> tuple[str | None, str | None, tuple[Any, Any] | None]:
    """protocol-rules.md 3.1a: (인터페이스, 제외 사유, 대표 행 (start, end) 채널 | None = 모호).

    대표 행은 관계 후보와 같은 `select_transaction_row`. 모호하면 후보 행 전부의 interface_id가 같은 값이고 없음이 아닐 때만
    그 인터페이스(하나라도 없음이거나 서로 다르면 transaction_row_ambiguous). 관계 후보 `resolve_transactions`는 없음 값을
    빼고 모이는 값을 쓰지만 프로토콜 계약은 "하나라도 없음이면 모름"이라 여기서는 계약을 따른다.
    """
    if not rows:
        return None, "no_transaction_row", None
    chosen, ambiguous = select_transaction_row(rows)
    if ambiguous:
        values = {row.interface_id for row in rows}
        value = next(iter(values))
        if len(values) == 1 and not graph_value_absent(value):
            return value, None, None
        return None, "transaction_row_ambiguous", None
    if graph_value_absent(chosen.interface_id):
        return None, "interface_absent", None
    return chosen.interface_id, None, (chosen.start_channel_id, chosen.end_channel_id)


def transaction_routes(processes: Sequence[ProcessRow]) -> tuple[str | None, list[tuple[tuple[str, ...], list[str]]]]:
    """거래 1건의 PROCESS 행(같은 거래 ID, 실행마다 최신 리비전) → (사유 | None, [(채널 목록, 단계 processId 목록)]).

    protocol-rules.md 3.2 단계 순서대로 검사하고 처음 걸린 사유를 돌려준다(같은 단계 안에서는 3.4 표 순서로 모든 묶음을 본다).
    경로 순서: 시작 단계에서 깊이 우선, 갈래는 processId 코드포인트 순.
    """
    if len(processes) > MAX_PROCESS_ROWS:
        return "graph_truncated", []
    grouped: dict[str, list[ProcessRow]] = defaultdict(list)
    for row in processes:
        if graph_value_absent(row.process_id):
            return "process_id_missing", []  # 2단계
        grouped[row.process_id].append(row)
    # 재시도 묶음·모호·DEPEND 분류는 관계 후보와 같은 build_group(process-graph.md 층 A 묶음 규칙).
    groups = {pid: build_group("", pid, rows) for pid, rows in grouped.items()}
    if any(group.ambiguous for group in groups.values()):
        return "precedes_ambiguous", []  # 2단계: 묶음 모호
    if any(group.depend is _CONFLICT for group in groups.values()):
        return "precedes_ambiguous", []  # 3단계: 시도마다 분류(start·absent·값)가 다름
    if any(group.depend is _MISSING for group in groups.values()):
        return "depend_process_id_missing", []  # 3단계: NULL·""·\N
    starts = [pid for pid, group in groups.items() if group.depend is _START]
    if len(starts) != 1:
        return "start_ambiguous", []  # 4단계
    children: dict[str, list[str]] = defaultdict(list)
    for pid, group in groups.items():
        if group.depend is _START:
            continue
        if group.depend == pid or group.depend not in groups:
            return "chain_broken", []  # 5단계: 자기 자신·없는 선행
        children[group.depend].append(pid)
    reached, stack = set(), [starts[0]]
    while stack:
        pid = stack.pop()
        reached.add(pid)
        stack.extend(children.get(pid, ()))
    if len(reached) != len(groups):
        return "chain_broken", []  # 5단계: 시작에서 닿지 않는 묶음(순환 포함)
    channels: dict[str, str] = {}
    mixed = False
    for pid, group in groups.items():
        values = [row.channel_id for row in group.attempts]
        present = [value for value in values if not graph_value_absent(value)]
        if not present:
            return "channel_absent", []  # 6단계(표 순서: 없음이 모호보다 앞)
        if len(present) != len(values) or len(set(present)) > 1:
            mixed = True
        channels[pid] = present[0]
    if mixed:
        return "channel_ambiguous", []  # 6단계
    routes: list[list[str]] = []
    pending: list[list[str]] = [[starts[0]]]
    while pending:
        route = pending.pop()
        nexts = sorted(children.get(route[-1], ()))
        if not nexts:
            routes.append(route)
        # 뒤에서 꺼내므로 역순으로 넣어 processId 순 깊이 우선이 되게 한다.
        pending.extend(route + [pid] for pid in reversed(nexts))
    if any(len(route) > MAX_PATH_STEPS for route in routes):
        return "path_too_long", []  # 8단계
    if len(routes) > MAX_ROUTES:
        return "routes_too_many", []  # 8단계
    return None, [(tuple(channels[pid] for pid in route), route) for route in routes]


def _ordered_reasons(counter: Counter) -> dict[str, int]:
    return {reason: counter[reason] for reason in REASON_ORDER if counter[reason]}


class _PathTally:
    __slots__ = ("transactions", "from_branching", "examples")

    def __init__(self) -> None:
        self.transactions = 0
        self.from_branching = 0
        self.examples: list[dict[str, Any]] = []


class _InterfacePaths:
    def __init__(self) -> None:
        self.transactions = 0  # 3.1a에서 이 인터페이스로 정해진 거래
        self.path_transactions = 0  # 그중 경로를 만든 거래
        self.branching = 0
        self.excluded: Counter[str] = Counter()
        self.endpoints: Counter[str] = Counter()
        self.paths: dict[tuple[str, ...], _PathTally] = {}

    def add(self, transaction_id: str, reason: str | None, routes: list[tuple[tuple[str, ...], list[str]]],
            endpoints: tuple[Any, Any] | None) -> None:
        self.transactions += 1
        if reason is not None:
            self.excluded[reason] += 1
            return
        self.path_transactions += 1
        branching = len(routes) > 1
        self.branching += branching
        seen: set[tuple[str, ...]] = set()
        for channels, steps in routes:
            if channels in seen:
                continue  # 같은 거래의 다른 끝 단계가 같은 채널 목록 → 거래당 1회
            seen.add(channels)
            tally = self.paths.get(channels)
            if tally is None:
                tally = self.paths[channels] = _PathTally()
            tally.transactions += 1
            tally.from_branching += branching
            # 거래 ID 순으로 오므로 앞 5건이 예시. 원천 행은 복사하지 않고 그래프 id만 둔다.
            if len(tally.examples) < MAX_COUNTEREXAMPLES:
                tally.examples.append({"refs": [transaction_ref(transaction_id)],
                                       "steps": [process_ref(transaction_id, pid) for pid in steps]})
        start, end = endpoints if endpoints is not None else (None, None)
        if endpoints is None or graph_value_absent(start) or graph_value_absent(end):
            self.endpoints["notCompared"] += 1
            return
        self.endpoints["compared"] += 1
        if any(channels[0] != start or channels[-1] != end for channels, _ in routes):
            self.endpoints["mismatch"] += 1

    def result(self, interface_id: str) -> dict[str, Any]:
        total = self.path_transactions
        paths = []
        for channels, tally in self.paths.items():
            paths.append({"channels": list(channels), "pathKey": path_key(channels), "transactions": tally.transactions,
                          "ratio": round(tally.transactions / total, 6),
                          # 정수 비교로 경계를 고정한다(정확히 1%는 rare 아님).
                          "rare": tally.transactions * 100 < RARE_PERCENT * total,
                          "fromBranching": tally.from_branching, "exampleRefs": tally.examples})
        paths.sort(key=lambda item: (-item["transactions"], item["pathKey"]))
        listed, omitted = paths[:MAX_OUTPUT_PATHS], paths[MAX_OUTPUT_PATHS:]
        return {"interfaceId": interface_id,
                "status": "insufficient_evidence" if total < MIN_APPLICABLE else "draft",
                "transactions": self.transactions, "pathTransactions": total,
                "branchingTransactions": self.branching, "distinctPaths": len(paths),
                "excludedReasons": _ordered_reasons(self.excluded),
                "endpoints": {"compared": self.endpoints["compared"], "mismatch": self.endpoints["mismatch"],
                              "notCompared": self.endpoints["notCompared"]},
                "paths": listed,
                "omittedPaths": {"paths": len(omitted), "transactions": sum(item["transactions"] for item in omitted)}}


class ProtocolPathDrafts:
    """거래 묶음(TxBundle, 실행마다 최신 리비전, train PROCESS 행)을 1건씩 받아 인터페이스별 경로 카운터만 유지한다."""

    def __init__(self) -> None:
        self.excluded: Counter[str] = Counter()  # 거래 단위
        self.transaction_id_missing_rows = 0  # 행 단위(3.4 transaction_id_missing)
        self.interfaces: dict[str, _InterfacePaths] = {}

    def add(self, bundle: TxBundle) -> None:
        if not bundle.processes:
            return  # train PROCESS 행이 없는 묶음(거래 행만·다른 분할만)은 학습 기간 관측이 아니다
        if graph_value_absent(bundle.key):
            self.transaction_id_missing_rows += len(bundle.processes)
            return
        if bundle.excluded_reason is not None:
            self.excluded[bundle.excluded_reason] += 1
            return
        interface_id, reason, endpoints = transaction_interface(bundle.transactions)
        if interface_id is None:
            self.excluded[reason] += 1
            return
        route_reason, routes = transaction_routes(bundle.processes)
        state = self.interfaces.get(interface_id)
        if state is None:
            state = self.interfaces[interface_id] = _InterfacePaths()
        state.add(bundle.key, route_reason, routes, endpoints)
        if route_reason is not None:
            self.excluded[route_reason] += 1

    def result(self) -> dict[str, Any]:
        interfaces = [self.interfaces[key].result(key) for key in sorted(self.interfaces)]
        return {"version": DRAFT_VERSION, "experimental": True, "split": "train",
                "parameters": {"minTransactions": MIN_APPLICABLE, "rarePercent": RARE_PERCENT,
                               "maxPathSteps": MAX_PATH_STEPS, "maxRoutes": MAX_ROUTES,
                               "maxProcessRows": MAX_PROCESS_ROWS, "maxExampleRefs": MAX_COUNTEREXAMPLES,
                               "maxOutputPaths": MAX_OUTPUT_PATHS,
                               "definition": DEFINITION, "reasonRule": REASON_RULE, "countingRule": COUNTING_RULE,
                               "unitNote": UNIT_NOTE, "endpointRule": ENDPOINT_RULE,
                               "selectionPolicy": SELECTION_POLICY, "outputLimitRule": OUTPUT_LIMIT_RULE,
                               "reasonOrder": list(REASON_ORDER)},
                "interfaces": interfaces,
                "excludedReasons": _ordered_reasons(self.excluded),
                "transactionIdMissingRows": self.transaction_id_missing_rows,
                "endpointMismatch": sum(item["endpoints"]["mismatch"] for item in interfaces)}
