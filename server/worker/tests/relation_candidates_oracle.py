# 시험 전용 오라클: 스트리밍 전환 직전 server/worker/trainer/relation_candidates.py(메모리 일괄 구현)를 고정한 사본.
# 스트리밍 구현(현재 trainer/relation_candidates.py)이 메모리 일괄 구현과 같은 결과 JSON을 내는지 비교하는 데만 쓴다.
# 런타임 코드는 이 파일을 import하지 않는다. 계산 내용을 고치지 말 것(비교 기준이 바뀐다). 결과 JSON의 설명 문자열은 현재 구현과 같게 둔다.
"""층 B(인터페이스별 정상 패턴) 관계 규칙 후보·처리 시간 임계값 후보 — 실험 작업 `recommend_relations`.

실험 기능. 근거: 계약 `contracts/process-graph.md`(그래프 id·없음 판정), `contracts/relation-rules.md`(Detector 판정 의미),
`contracts/rule-candidates-ui.md`(결과 읽기)와 11/12 사용자 결정(required_step 판정). 결과는 작업 결과 JSON에만 둔다(새 표·migration 없음). 후보에는 규칙 id가 없다
(승인 때 서버가 `hard:rel-<8hex>`를 만든다).

판정 단위는 PROCESS, 묶음은 (거래, PROCESS_ID)당 1개(재시도는 거래당 1회로 센다). 후보 기준:
적용 가능 거래 < 30 → `insufficient_evidence`(표시만), 충족률 ≥ 99% → `candidate`, 95~99% → `review_needed`,
그 아래 → 후보 아님(`not_candidate`, 그래프에만 남음). 건수 이름: applicable(= satisfied + violated),
satisfied, violated, undetermined. 충족률 = satisfied / applicable.

판정 후보 종류(사용자 결정 4, 11/12: 최종 판정은 TRANSACTION 행 도착 때 — PROCESS 행이 모두 먼저 기록된다):
- `precedes`: 후행의 DEPEND_PROCESS_ID가 선행 processId를 가리킴. evaluatedAt "successor"(후행 도착 첫 판정, 거래 도착 최종)
- `message_handoff`: 선행 OUT → 후행 IN, MESSAGE_ID 문자열 일치. evaluatedAt "successor". 짝 `precedes`를 항상 함께 낸다
- `required_step`: (인터페이스, 필수 단계)마다 1건, `to` null. 거래 전체 단계 집합에 그 단계가 있는가. evaluatedAt "transaction"
precedes·message_handoff의 대조 키는 PROCESS 필드만: from·to processId, scope.adapterType(= to 단계 관측 값, 없으면 null = 모든
adapter). from adapter는 대조하지 않고 분포(`fromAdapterTypes`)만 남긴다. required_step만 예외로 scope `{interfaceId}`
(TRANSACTION 행 interface_id와 대조하는 판정 키, Detector hash 입력).
`allowed_branch`는 정보(relationNotes)만. 처리 시간(`elapsed_ms`) 임계값은 소프트(이상 징후) 제안으로 `thresholdCandidates[]`.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

CONTRACT_VERSION = "relation-candidates-experimental-2"
MIN_APPLICABLE = 30
CANDIDATE_PERCENT = 99
REVIEW_PERCENT = 95
MAX_COUNTEREXAMPLES = 5
DEPEND_FIELD = "DEPEND_PROCESS_ID"
# graph_value_absent: ASCII 공백·탭·CR·LF 4종만 앞뒤에서 뺀다. 인자 없는 str.strip()은 U+0085·U+001C 등도 지워 계약과 갈린다.
_ASCII_WHITESPACE = " \t\r\n"
# 층 A 계약(process-graph.md hands_off)의 불일치 제외 구간: 거래 ID 끝자리가 0채움 2자리(JS /-0\d$/). 불일치에만 적용.
# Python에서는 `$`가 끝 줄바꿈 앞에도 맞고 `\d`가 유니코드 숫자에도 맞으므로 `[0-9]`·`\Z`로 같은 뜻을 만든다.
DEFAULT_HANDOFF_MISMATCH_SUFFIX_PATTERNS = (r"-0[0-9]\Z",)
MAX_SUFFIX_PATTERNS = 10
MAX_SUFFIX_PATTERN_LENGTH = 100
# 인계 결과를 거래 ID 끝자리 형태별로 나눠 보여 준다(반례 섞임 확인용, 제외 규칙과 별개). 먼저 맞는 분류 하나.
SUFFIX_CLASSES = (("zero_padded_two_digit", re.compile(r"-0[0-9]\Z")), ("one_digit", re.compile(r"-[0-9]\Z")),
                  ("four_digit", re.compile(r"-[0-9]{4}\Z")))
STANDARD_PROCESS_ID = re.compile(r".+_PR[0-9]{3}\Z")
PAIRING_POLICY = ("message_handoff 후보마다 같은 from·to·scope의 precedes 후보를 candidates에 함께 둔다. "
                  "precedes가 기준 미달(not_candidate)이어도 실제 상태 그대로 넣고 listedForPairing=true로 표시한다. "
                  "짝 precedes 없이도 handoff 저장은 허용되고 경고만 난다: 경고 코드 relation_handoff_without_precedes, "
                  "PUT /api/rule-set·POST /api/rule-set/preview 응답 warnings[]. pairApprovable은 그 경고를 미리 보여 주는 "
                  "정보이고 handoff 자신의 approvable은 자기 상태로만 정한다. 근거: Detector relation-rules.md")
JUDGEMENT_BASIS = ("final (TRANSACTION row present): 통계는 Detector 최종 판정(TRANSACTION 행 도착 때)을 따른다. "
                   "TRANSACTION 행이 있는 거래만 세고(없으면 unassigned.transactions.no_transaction_row), 그때도 선행 "
                   "PROCESS 행이 없으면 precedes 위반 predecessor_missing. 첫 판정(후행 도착 때)의 보류 collection_incomplete는 "
                   "통계에 쓰지 않는다")
SCOPE_NULL_POLICY = ("to 단계 adapter_type이 없으면 scope null 후보를 내고, 그 건수는 to processId의 모든 adapter 거래를 센다"
                     "(null = 모든 adapter에 적용, Detector 대조와 같은 뜻). adapter별 후보가 함께 있으면 둘 다 낸다")
CONSERVATIVE_NOTE = ("Trainer는 Detector보다 보수적이다: scope adapter가 맞는(적용되는) 후행 묶음이 재시도 모호면 판정 관측 값과 "
                     "무관하게 undetermined로 센다. adapter 불일치는 모호가 아니다 — 후행 관측 adapter가 scope와 다르면 적용 안 함"
                     "(건수에서 뺌), 같은 재시도 묶음 안 시도 간 adapter 차이는 판정에 영향 없고 withinGroupAdapterMismatch로만 보인다. "
                     "다만 묶음 단위로 세므로 다음 둘은 Detector(관측 단위)보다 보수적이다: 서로 다른 adapter 시도들의 DEPEND 값이 "
                     "다르면 dependency_conflict, 서로 다른 adapter 시도들의 메시지를 한 묶음으로 합쳐 OUT·IN이 여러 건이면 "
                     "multiple_messages가 될 수 있다")
WITHIN_GROUP_ADAPTER_MISMATCH_NOTE = ("withinGroupAdapterMismatch: 묶음 안 시도들의 adapter가 2종 이상인 거래 수(정보). "
                                      "to는 그 후보가 적용된 모든 거래(satisfied·violated·undetermined)에서, from은 "
                                      "satisfied 거래에서만 센다(from 묶음은 선행이 확인된 경우에만 정해진다)")
ADAPTER_POLICY = ("scope.adapterType은 판정(to 단계) PROCESS 관측 자신의 adapter와 원래 값 정확 일치일 때만 적용(Detector relation-rules.md). "
                  "후보 통계는 to 단계 관측 adapter 값별로 나눈다(한 거래의 to 묶음 시도들이 여러 adapter면 각 adapter 후보에 "
                  "한 번씩 들어간다). from은 processId만 대조")
TRANSACTION_ROW_RULE = ("TRANSACTION 실행이 여럿(재시도)이면 retry_count가 가장 큰 실행의 status·interface_id를 쓴다. "
                        "가장 큰 retry_count 실행이 2건 이상·retry_count가 숫자 아님·execution id 없는 행이 있으면 "
                        "transaction_row_ambiguous(required_step undetermined, interface 값이 하나로 모이지 않으면 unassigned). "
                        "Detector relation-rules.md 5절·7.2 [제안, 확인 필요]")
TRANSACTION_STATUS_RULE = "선택된 TRANSACTION 실행의 status 원래 값('S'와 'S '는 다르다), 없으면 unknown, 실행 모호면 ambiguous"
SUFFIX_PATTERN_LIMIT_NOTE = ("관리자 입력 정규식은 개수(10)·길이(100자)만 제한한다. 그 밖의 ReDoS 방어는 범위 밖(실험, 관리자 전용 작업)")
REQUIRED_STEP_SCOPE_POLICY = ("required_step만 scope {interfaceId}: TRANSACTION 행 interface_id와 대조하는 판정 키이고 Detector hash 입력에 "
                              "들어간다(relation-rules.md 2·8절, 예외). precedes·message_handoff는 scope.adapterType(to 단계) 또는 null")
# Trainer 사유 이름 → Detector relation-rules.md 어휘(3.1·5절). T = 방법 결과 위반, held = 수집 미완료 보류.
# Trainer 통계에서 held·not applied 사유는 undetermined로 센다(최종 판정 기준, JUDGEMENT_BASIS).
REASON_VOCABULARY = {
    "step_absent": "T (required_step)",
    "successor_is_start_step": "T (precedes: DEPEND_PROCESS_ID NONE)",
    "other_predecessor": "T (precedes: DEPEND_PROCESS_ID != from.processId)",
    "self_dependency": "T (precedes: DEPEND_PROCESS_ID != from.processId)",
    "message_id_mismatch": "T (message_handoff mismatch)",
    "predecessor_missing": "T at final judgement (first judgement: held collection_incomplete)",
    "no_in_message": "held collection_incomplete (거래 행 도착 뒤에도 없으면 보류로 끝)",
    "no_out_message": "held collection_incomplete (거래 행 도착 뒤에도 없으면 보류로 끝)",
    "dependency_conflict": "input_invalid precedes_ambiguous",
    "successor_ambiguous_retry": "input_invalid precedes_ambiguous",
    "predecessor_ambiguous_retry": "input_invalid precedes_ambiguous",
    "transaction_row_ambiguous": "input_invalid transaction_row_ambiguous",
    "depend_process_id_missing": "input_missing depend_process_id_missing",
    "missing_message_id": "input_missing message_id_missing",
    "multiple_messages": "input_invalid message_link_ambiguous",
    "excluded_transaction_suffix": "not applied (relationNotes not_applicable / excluded_transaction_suffix)",
    "unkeyed_rows": "Trainer only: transaction has PROCESS rows without PROCESS_ID",
}
_CONFLICT = object()
_START = object()  # DEPEND_PROCESS_ID가 정확히 NONE(시작 단계)
_MISSING = object()  # DEPEND_PROCESS_ID가 NULL·""·\N(데이터 결측, 시작 단계와 구분)


# ---------------------------------------------------------------------------
# 없음 판정·id (WebUI 계약과 같은 규칙, 공용 시험 벡터로 확인)
# ---------------------------------------------------------------------------

def graph_value_absent(value: Any, field_name: str | None = None) -> bool:
    """NULL, 또는 ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 ""·"\\N". DEPEND_PROCESS_ID만 정확히 "NONE"도 없음."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    stripped = value.strip(_ASCII_WHITESPACE)
    return stripped in ("", "\\N") or (field_name == DEPEND_FIELD and stripped == "NONE")


def escape_component(value: str) -> str:
    """구성 요소 이스케이프가 유일한 변환: '%' → '%25' 다음 ':' → '%3A'."""
    return value.replace("%", "%25").replace(":", "%3A")


def transaction_ref(transaction_id: str) -> str:
    return "transaction:" + escape_component(transaction_id)


def process_ref(transaction_id: str, process_id: str) -> str:
    return f"process:{escape_component(transaction_id)}:{escape_component(process_id)}"


def pattern_step_id(interface_id: Any, process_id: Any, adapter_type: Any) -> str | None:
    """`pattern_step:<interface>:<process>:<adapter>`. adapter 없음 → 빈 마지막 칸, interface·process 없음 → None."""
    if graph_value_absent(interface_id) or graph_value_absent(process_id):
        return None
    adapter = "" if graph_value_absent(adapter_type) else escape_component(adapter_type)
    return f"pattern_step:{escape_component(interface_id)}:{escape_component(process_id)}:{adapter}"


def edge_id(kind: str, from_id: str, to_id: str) -> str:
    """간선 id `<kind>:<from 노드 id>:<to 노드 id>`. 노드 id는 다시 이스케이프하지 않는다."""
    return f"{kind}:{from_id}:{to_id}"


def graph_id(kind: str, components: Mapping[str, Any]) -> str | None:
    """시험 벡터 형식({kind, components})의 id. 층 A 종류도 같은 규칙으로 만든다(벡터 대조용)."""
    c = components
    if kind == "transaction":
        return transaction_ref(c["transactionId"])
    if kind == "process":
        return process_ref(c["transactionId"], c["processId"])
    if kind == "message":
        return "message:" + escape_component(c["executionId"])
    if kind == "unkeyed":
        return "unkeyed:" + escape_component(c["observationId"])
    if kind == "missing":
        return f"missing:{c['referencingNodeId']}:{escape_component(c['processId'])}"
    if kind == "pattern_step":
        return pattern_step_id(c.get("interfaceId"), c.get("processId"), c.get("adapterType"))
    if kind in ("contains", "precedes", "branches_to"):
        return edge_id(kind, c["from"], c["to"])
    if kind == "hands_off":
        base = edge_id(kind, c["from"], c["to"])
        # 층 A는 OUT 메시지 노드 id를 덧붙인다(4칸). 층 B에는 메시지 노드가 없다(3칸).
        return f"{base}:{c['messageNodeId']}" if "messageNodeId" in c else base
    raise ValueError("unknown_graph_kind")


def depend_state(value: Any) -> Any:
    """DEPEND_PROCESS_ID 원래 값 분류: 정확히 NONE → 시작 단계, NULL·""·\\N → 결측(데이터 문제), 그 밖은 원래 값."""
    if value is None:
        return _MISSING
    if not isinstance(value, str):
        return value
    stripped = value.strip(_ASCII_WHITESPACE)
    if stripped == "NONE":
        return _START
    if stripped in ("", "\\N"):
        return _MISSING
    return value


def suffix_class(transaction_id: str) -> str:
    for name, pattern in SUFFIX_CLASSES:
        if pattern.search(transaction_id):
            return name
    return "other"


# ---------------------------------------------------------------------------
# 입력 행
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessRow:
    observation_id: str
    execution_id: str | None
    revision_no: int | None
    persisted_at: Any
    transaction_id: str | None
    process_id: str | None
    depend_process_id: str | None
    adapter_type: str | None
    retry_count: Any
    raw: Mapping[str, Any] = field(default_factory=dict)
    label: str | None = None
    split: str = "train"


@dataclass(frozen=True)
class TransactionRow:
    observation_id: str
    execution_id: str | None
    revision_no: int | None
    persisted_at: Any
    transaction_id: str | None
    interface_id: str | None
    status: str | None = None
    retry_count: Any = None


@dataclass(frozen=True)
class MessageRow:
    observation_id: str
    execution_id: str | None
    revision_no: int | None
    persisted_at: Any
    transaction_id: str | None
    process_id: str | None
    message_id: str | None
    direction: str | None


def latest_per_execution(rows: Iterable[Any]) -> tuple[list[Any], int]:
    """실행(execution_id)마다 최신 리비전 1건(revision_no, persisted_at, observation_id 내림차순, 스냅샷 규칙과 같음)."""
    chosen: dict[str, Any] = {}
    superseded = 0
    for row in rows:
        key = row.execution_id if row.execution_id is not None else "observation:" + row.observation_id
        current = chosen.get(key)
        if current is None:
            chosen[key] = row
            continue
        superseded += 1
        if _revision_key(row) > _revision_key(current):
            chosen[key] = row
    return sorted(chosen.values(), key=lambda item: item.observation_id), superseded


def _revision_key(row: Any) -> tuple[int, Any, str]:
    return (row.revision_no or 0, row.persisted_at or datetime.min.replace(tzinfo=timezone.utc), row.observation_id)


def _retry_number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip(_ASCII_WHITESPACE))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


# ---------------------------------------------------------------------------
# 거래·묶음
# ---------------------------------------------------------------------------

@dataclass
class _Group:
    process_id: str
    attempts: list[ProcessRow]
    ambiguous: bool  # 같은 retry_count 2건 이상 또는 번호 없는 시도가 섞임
    adapters: tuple[Any, ...]  # 시도들의 adapter 원래 값(없음 = None), 중복 없이. 시도 간 차이는 모호가 아니다
    depend: Any  # 원래 값 | _START | _MISSING | _CONFLICT

    @property
    def resolved(self) -> bool:
        return not self.ambiguous


@dataclass
class _Transaction:
    transaction_id: str
    interface_id: str
    status: str
    row_ambiguous: bool  # TRANSACTION 실행 선택 모호(transaction_row_ambiguous)
    groups: dict[str, _Group]
    unkeyed_rows: int
    messages: dict[str, list[tuple[str | None, str | None]]]


def _same_or_conflict(values: list[Any]) -> Any:
    first = values[0]
    return first if all(value is first or value == first for value in values[1:]) else _CONFLICT


def build_group(interface_id: str, process_id: str, attempts: list[ProcessRow]) -> _Group:
    """(거래, PROCESS_ID) 재시도 묶음. 같은 retry_count 2건 이상 또는 번호 없는 시도가 섞이면 모호(대표 선택 없음)."""
    retries = [_retry_number(row.retry_count) for row in attempts]
    ambiguous = len(attempts) > 1 and (None in retries or len(set(retries)) != len(retries))
    adapters = tuple(sorted({None if graph_value_absent(row.adapter_type) else row.adapter_type for row in attempts},
                            key=lambda value: (value is not None, value or "")))
    depend = _same_or_conflict([depend_state(row.depend_process_id) for row in attempts])
    ordered = sorted(attempts, key=lambda row: (_retry_number(row.retry_count) is None,
                                                _retry_number(row.retry_count) or 0, row.observation_id))
    return _Group(process_id, ordered, ambiguous, adapters, depend)


def _unresolved_reason(group: _Group, role: str) -> str:
    return f"{role}_ambiguous_retry"


def resolve_transactions(rows: Iterable[TransactionRow]) -> dict[str, dict[str, Any]]:
    """거래 ID 문자열 → interface·상태. 같은 원본·스냅샷 기준시각 안 TRANSACTION 행으로만 정하고 추정하지 않는다.

    TRANSACTION_ROW_RULE: 실행(실행마다 최신 리비전)이 하나면 그 행, 여럿이면 retry_count가 가장 큰 실행을 쓴다.
    여럿인데 가장 큰 번호가 2건 이상·번호가 숫자 아님·execution id 없는 행이 있으면 `rowAmbiguous`.
    모호해도 interface 값이 하나로 모이면 그 인터페이스에 넣고(required_step은 undetermined), 아니면 사유
    `transaction_row_ambiguous`로 미배정. 모호가 아니면 선택 실행의 interface_id(없으면 `interface_absent`)·status.
    """
    by_transaction: dict[str, list[TransactionRow]] = defaultdict(list)
    for row in rows:
        if not graph_value_absent(row.transaction_id):
            by_transaction[row.transaction_id].append(row)
    resolved: dict[str, dict[str, Any]] = {}
    for transaction_id, items in by_transaction.items():
        chosen, ambiguous = items[0], False
        if len(items) > 1:
            retries = [_retry_number(row.retry_count) for row in items]
            if any(row.execution_id is None for row in items) or None in retries:
                ambiguous = True
            else:
                top = max(retries)
                tops = [row for row, number in zip(items, retries) if number == top]
                ambiguous, chosen = len(tops) > 1, tops[0]
        if ambiguous:
            present = {row.interface_id for row in items if not graph_value_absent(row.interface_id)}
            interface_id = next(iter(present)) if len(present) == 1 else None
            resolved[transaction_id] = {"interfaceId": interface_id,
                                        "reason": None if interface_id else "transaction_row_ambiguous",
                                        "status": "ambiguous", "rowAmbiguous": True}
            continue
        interface_id = None if graph_value_absent(chosen.interface_id) else chosen.interface_id
        resolved[transaction_id] = {"interfaceId": interface_id, "reason": None if interface_id else "interface_absent",
                                    "status": "unknown" if graph_value_absent(chosen.status) else chosen.status,
                                    "rowAmbiguous": False}
    return resolved


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------

class _Tally:
    def __init__(self) -> None:
        self.satisfied = 0
        self.violated = 0
        self.undetermined = 0
        self.violation_reasons: Counter[str] = Counter()
        self.violations_by_status: Counter[str] = Counter()
        self.undetermined_reasons: Counter[str] = Counter()
        self.counterexamples: list[dict[str, Any]] = []

    def add(self, outcome: str, reason: str | None = None, refs: list[str] | None = None,
            transaction_status: str | None = None) -> None:
        if outcome == "satisfied":
            self.satisfied += 1
        elif outcome == "violated":
            self.violated += 1
            self.violation_reasons[reason or "violated"] += 1
            # 거래 상태별 분해: F 거래가 중간에 멈춘 위반을 S 거래의 위반과 나눠 본다.
            self.violations_by_status[transaction_status or "unknown"] += 1
            # 거래 ID 순으로 들어오므로 앞 5건이 대표 반례다. 원천 행은 복사하지 않고 참조 id만 둔다.
            if len(self.counterexamples) < MAX_COUNTEREXAMPLES:
                self.counterexamples.append({"refs": refs or [], "reason": reason,
                                             "transactionStatus": transaction_status})
        elif outcome == "undetermined":
            self.undetermined += 1
            self.undetermined_reasons[reason or "undetermined"] += 1
        else:
            raise ValueError("unknown_outcome")

    @property
    def applicable(self) -> int:
        return self.satisfied + self.violated

    def evidence(self) -> dict[str, Any]:
        applicable = self.applicable
        return {"applicable": applicable, "satisfied": self.satisfied, "violated": self.violated,
                "undetermined": self.undetermined,
                "satisfactionRate": round(self.satisfied / applicable, 6) if applicable else None,
                "status": candidate_status(applicable, self.satisfied),
                "violationReasons": dict(sorted(self.violation_reasons.items())),
                "violationsByTransactionStatus": dict(sorted(self.violations_by_status.items())),
                "undeterminedReasons": dict(sorted(self.undetermined_reasons.items())),
                "counterexampleRefs": list(self.counterexamples)}


def candidate_status(applicable: int, satisfied: int) -> str:
    """정수 비교로 경계를 고정한다(99%·95% 정확히 같으면 위 단계)."""
    if applicable < MIN_APPLICABLE:
        return "insufficient_evidence"
    if satisfied * 100 >= CANDIDATE_PERCENT * applicable:
        return "candidate"
    if satisfied * 100 >= REVIEW_PERCENT * applicable:
        return "review_needed"
    return "not_candidate"


def _required_outcome(tx: _Transaction, process_id: str) -> tuple[str, str | None]:
    """required_step(사용자 결정 4, 11/12): TRANSACTION 행이 도착했을 때 그 거래의 단계 집합으로 최종 판정한다.

    PROCESS 행이 TRANSACTION 행보다 먼저 모두 기록되므로, 마지막 단계도 다른 단계와 같이 판정한다.
    그 processId의 PROCESS 행(키 있음)이 하나라도 있으면 satisfied — 재시도 묶음이 모호해도 '있음'은 확실하다
    (총괄 결정 11/12). 거래 행 선택이 모호하면 먼저 undetermined `transaction_row_ambiguous`. 없으면 violated `step_absent`. 있음 자체를 정할 수 없는 경우만 undetermined: 키 있는 행은
    없고 PROCESS_ID 없는 행이 있으면 그 행이 이 단계일 수 있어 `unkeyed_rows`.
    """
    if tx.row_ambiguous:
        return "undetermined", "transaction_row_ambiguous"
    if process_id in tx.groups:
        return "satisfied", None
    return ("undetermined", "unkeyed_rows") if tx.unkeyed_rows else ("violated", "step_absent")


def _successor_group(tx: _Transaction, to_pid: str, to_adapter: str | None
                     ) -> tuple[_Group | None, tuple[str, str] | None]:
    """판정 위치(후행) 묶음. scope adapter가 있으면 후행 관측 adapter 중 원래 값이 같은 것이 있을 때만 적용, 없으면
    (scope null) 모든 adapter에 적용(SCOPE_NULL_POLICY). 적용되지 않으면 None(건수에서 뺌 — 보류·위반 아님).

    적용되는 후행 묶음이 재시도 모호면 undetermined(CONSERVATIVE_NOTE). 시도 간 adapter 차이는 모호가 아니다.
    """
    successor = tx.groups.get(to_pid)
    if successor is None:
        return None, None
    if to_adapter is not None and to_adapter not in successor.adapters:
        return None, None
    if not successor.resolved:
        return None, ("undetermined", _unresolved_reason(successor, "successor"))
    return successor, None


def _precedes_outcome(tx: _Transaction, from_pid: str, to_pid: str, to_adapter: str | None
                      ) -> tuple[str, str | None] | None:
    """후행 단계가 있는 거래에서만 판정(evaluatedAt successor). from은 processId만 대조한다(A1, 규칙 from = {processId})."""
    successor, early = _successor_group(tx, to_pid, to_adapter)
    if early is not None or successor is None:
        return early
    if successor.depend is _CONFLICT:
        return "undetermined", "dependency_conflict"
    if successor.depend is _START:
        return "violated", "successor_is_start_step"
    if successor.depend is _MISSING:
        return "undetermined", "depend_process_id_missing"
    if successor.depend == to_pid:
        return "violated", "self_dependency"
    if successor.depend != from_pid:
        return "violated", "other_predecessor"
    predecessor = tx.groups.get(from_pid)
    if predecessor is None:
        # 최종 판정 기준(Detector relation-rules.md 5절, JUDGEMENT_BASIS): 세는 거래는 모두 TRANSACTION 행이 있고
        # PROCESS 행은 TRANSACTION 행보다 먼저 모두 기록된다. 그때도 선행 행이 없으면 위반이다. 후행 도착 때의
        # 첫 판정에서는 같은 경우가 collection_incomplete(보류)지만 그 보류는 통계 대상이 아니다. 의도된 동작이다.
        return ("undetermined", "unkeyed_rows") if tx.unkeyed_rows else ("violated", "predecessor_missing")
    if not predecessor.resolved:
        return "undetermined", _unresolved_reason(predecessor, "predecessor")
    return "satisfied", None


def handoff_outcome(tx: _Transaction, from_pid: str, to_pid: str,
                    mismatch_suffix_patterns: Sequence[re.Pattern[str]]) -> tuple[str, str | None]:
    """선행 OUT × 후행 IN, MESSAGE_ID 문자열 비교(층 A hands_off와 같은 결과 어휘를 거래 단위로 요약).

    제외 끝자리 패턴은 **불일치에만** 적용한다(일치는 그대로 satisfied).
    """
    outs = [message_id for message_id, direction in tx.messages.get(from_pid, []) if direction == "OUT"]
    ins = [message_id for message_id, direction in tx.messages.get(to_pid, []) if direction == "IN"]
    if not outs:
        return "undetermined", "no_out_message"
    if not ins:
        return "undetermined", "no_in_message"
    out_ids = {value for value in outs if not graph_value_absent(value)}
    in_ids = {value for value in ins if not graph_value_absent(value)}
    if out_ids & in_ids:
        return "satisfied", None
    if len(outs) == 1 and len(ins) == 1 and out_ids and in_ids:
        if any(pattern.search(tx.transaction_id) for pattern in mismatch_suffix_patterns):
            return "undetermined", "excluded_transaction_suffix"
        return "violated", "message_id_mismatch"
    if len(outs) > 1 or len(ins) > 1:
        return "undetermined", "multiple_messages"
    return "undetermined", "missing_message_id"


def compile_suffix_patterns(patterns: Any) -> list[re.Pattern[str]]:
    """관리자 입력 정규식: 개수(10)·길이(100자)·중복·형식만 검사한다. 그 밖의 ReDoS 방어는 범위 밖(SUFFIX_PATTERN_LIMIT_NOTE)."""
    if not isinstance(patterns, (list, tuple)) or len(patterns) > MAX_SUFFIX_PATTERNS:
        raise ValueError("relation_input_invalid")
    if not all(isinstance(pattern, str) and pattern and len(pattern) <= MAX_SUFFIX_PATTERN_LENGTH for pattern in patterns):
        raise ValueError("relation_input_invalid")
    if len(set(patterns)) != len(patterns):
        raise ValueError("relation_input_invalid")
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.error as exc:
        raise ValueError("relation_input_invalid") from exc


def _non_standard(*process_ids: str | None) -> bool:
    return any(value is not None and not STANDARD_PROCESS_ID.match(value) for value in process_ids)


def _adapter_list(counts: Counter) -> list[dict[str, Any]]:
    """adapter 분포. 없음(None)은 adapterType null로 둔다(JSON 키로 만들 수 없어 목록)."""
    return [{"adapterType": adapter, "transactions": counts[adapter]}
            for adapter in sorted(counts, key=lambda value: (value is not None, value or ""))]


# ---------------------------------------------------------------------------
# 층 B 추출
# ---------------------------------------------------------------------------

def mine_relations(processes: Sequence[ProcessRow], transactions: Sequence[TransactionRow],
                   messages: Sequence[MessageRow], *, exclude_process_ids: Sequence[str] = (),
                   handoff_mismatch_suffix_patterns: Sequence[str] = DEFAULT_HANDOFF_MISMATCH_SUFFIX_PATTERNS,
                   excluded_transaction_ids: Mapping[str, str] | None = None) -> dict[str, Any]:
    """메모리 입력으로 층 B 그래프·관계 후보·분기 정보를 만든다. DB 접근 없음(단위 시험 대상).

    `excluded_transaction_ids`: 호출자가 정한 거래 제외(거래 ID → 사유, 예: 분할 경계·이상 라벨). 사유별로 센다.
    """
    suffix_patterns = compile_suffix_patterns(list(handoff_mismatch_suffix_patterns))
    excluded_values = set(exclude_process_ids)
    process_rows, superseded_p = latest_per_execution(processes)
    transaction_rows, superseded_t = latest_per_execution(transactions)
    message_rows, superseded_m = latest_per_execution(messages)
    excluded_counts: Counter[str] = Counter()
    excluded_message_counts: Counter[str] = Counter()
    kept: list[ProcessRow] = []
    for row in process_rows:
        if row.process_id in excluded_values:
            excluded_counts[row.process_id] += 1
        else:
            kept.append(row)
    caller_excluded = dict(excluded_transaction_ids or {})
    excluded_tx_reasons: Counter[str] = Counter()
    unassigned_tx: Counter[str] = Counter()
    unassigned_rows: Counter[str] = Counter()
    by_transaction: dict[str, list[ProcessRow]] = defaultdict(list)
    for row in kept:
        if graph_value_absent(row.transaction_id):
            unassigned_rows["processTransactionIdAbsent"] += 1
        else:
            by_transaction[row.transaction_id].append(row)
    resolved_tx = resolve_transactions(transaction_rows)
    message_index: dict[str, dict[str, list[tuple[str | None, str | None]]]] = defaultdict(lambda: defaultdict(list))
    for row in message_rows:
        if row.process_id in excluded_values:
            excluded_message_counts[row.process_id] += 1
            continue
        if graph_value_absent(row.transaction_id) or graph_value_absent(row.process_id):
            unassigned_rows["messageKeyAbsent"] += 1
            continue
        message_index[row.transaction_id][row.process_id].append((row.message_id, row.direction))

    per_interface: dict[str, list[_Transaction]] = defaultdict(list)
    for transaction_id in sorted(by_transaction):
        if transaction_id in caller_excluded:
            excluded_tx_reasons[caller_excluded[transaction_id]] += 1
            continue
        info = resolved_tx.get(transaction_id, {"interfaceId": None, "reason": "no_transaction_row", "status": "unknown",
                                                "rowAmbiguous": False})
        if info["interfaceId"] is None:
            unassigned_tx[info["reason"]] += 1
            continue
        interface_id = info["interfaceId"]
        grouped: dict[str, list[ProcessRow]] = defaultdict(list)
        unkeyed = 0
        for row in by_transaction[transaction_id]:
            if graph_value_absent(row.process_id):
                unkeyed += 1
            else:
                grouped[row.process_id].append(row)
        unassigned_rows["unkeyedProcessRows"] += unkeyed
        groups = {pid: build_group(interface_id, pid, attempts) for pid, attempts in grouped.items()}
        per_interface[interface_id].append(_Transaction(transaction_id, interface_id, info["status"], info["rowAmbiguous"],
                                                        groups, unkeyed, message_index.get(transaction_id, {})))

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    not_listed: Counter[str] = Counter()
    for interface_id in sorted(per_interface):
        txs = per_interface[interface_id]
        steps: dict[str, dict[str, Any]] = {}
        step_tx: Counter[str] = Counter()
        pid_adapters: dict[str, Counter] = defaultdict(Counter)
        observed: Counter[tuple[str, str]] = Counter()  # (from 단계, to 단계) — 그래프 표시용 관측 쌍
        for tx in txs:
            for group in tx.groups.values():
                # required_step 대상 processId는 모호 묶음도 포함한다(있음은 확실). adapter 분포는 해석 가능한 묶음만.
                adapters = pid_adapters[group.process_id]
                if not group.resolved:
                    continue
                for adapter in group.adapters:
                    step = pattern_step_id(interface_id, group.process_id, adapter)
                    steps.setdefault(step, {"processId": group.process_id, "adapterType": adapter, "step": step})
                    step_tx[step] += 1
                    adapters[adapter] += 1
            for group in tx.groups.values():
                if not group.resolved or group.depend in (_START, _MISSING, _CONFLICT) or group.depend == group.process_id:
                    continue
                predecessor = tx.groups.get(group.depend)
                if predecessor is not None and predecessor.resolved:
                    for from_adapter in predecessor.adapters:
                        for to_adapter in group.adapters:
                            observed[(pattern_step_id(interface_id, predecessor.process_id, from_adapter),
                                      pattern_step_id(interface_id, group.process_id, to_adapter))] += 1
        for step in sorted(steps):
            info = steps[step]
            nodes.append({"id": step, "kind": "pattern_step",
                          "attrs": {"interfaceId": interface_id, "processId": info["processId"],
                                    "adapterType": info["adapterType"], "transactions": step_tx[step],
                                    "nonStandardProcessId": _non_standard(info["processId"])}})

        # required_step: (인터페이스, 필수 단계 processId)마다 1건, 거래 전체 단계 집합으로 판정(evaluatedAt transaction).
        for process_id in sorted(pid_adapters):
            tally = _Tally()
            for tx in txs:
                outcome, reason = _required_outcome(tx, process_id)
                tally.add(outcome, reason, [transaction_ref(tx.transaction_id)], tx.status)
            evidence = {**tally.evidence(), "fromAdapterTypes": _adapter_list(pid_adapters[process_id])}
            if evidence["status"] != "not_candidate":
                pattern_nodes = sorted(step for step, value in steps.items() if value["processId"] == process_id)
                candidates.append(_candidate("required_step", interface_id, process_id, None, [], pattern_nodes,
                                             evidence, "transaction"))

        # precedes·message_handoff: (from processId, to 단계)마다 1건. from adapter는 대조하지 않고 분포만 남긴다(A1).
        pairs: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for (from_step, to_step), count in observed.items():
            pairs[(steps[from_step]["processId"], to_step)].append((from_step, count))
        for from_pid, to_step in sorted(pairs):
            target = steps[to_step]
            pair_edges = sorted(pairs[(from_pid, to_step)], key=lambda item: (-item[1], item[0]))
            precedes, handoff = _Tally(), _Tally()
            from_adapters: Counter = Counter()
            mismatch: Counter[str] = Counter()
            by_suffix: dict[str, Counter[str]] = defaultdict(Counter)
            for tx in txs:
                result = _precedes_outcome(tx, from_pid, target["processId"], target["adapterType"])
                if result is None:
                    continue
                refs = [transaction_ref(tx.transaction_id), process_ref(tx.transaction_id, target["processId"])]
                precedes.add(result[0], result[1], refs, tx.status)
                if len(tx.groups[target["processId"]].adapters) > 1:
                    mismatch["to"] += 1  # 정보: 후행 재시도 묶음 안 시도 간 adapter 차이
                if result[0] != "satisfied":
                    continue
                for adapter in tx.groups[from_pid].adapters:
                    from_adapters[adapter] += 1
                if len(tx.groups[from_pid].adapters) > 1:
                    mismatch["from"] += 1
                outcome, reason = handoff_outcome(tx, from_pid, target["processId"], suffix_patterns)
                handoff.add(outcome, reason, refs + [process_ref(tx.transaction_id, from_pid)], tx.status)
                bucket = by_suffix[suffix_class(tx.transaction_id)]
                if outcome == "satisfied":
                    bucket["match"] += 1
                elif outcome == "violated":
                    bucket["mismatch"] += 1
                elif reason == "excluded_transaction_suffix":
                    bucket["excludedMismatch"] += 1
            adapters = _adapter_list(from_adapters)
            within = {"from": mismatch["from"], "to": mismatch["to"]}
            precedes_evidence = {**precedes.evidence(), "fromAdapterTypes": adapters, "withinGroupAdapterMismatch": within}
            handoff_evidence = {**handoff.evidence(), "fromAdapterTypes": adapters, "withinGroupAdapterMismatch": within,
                                "bySuffixClass": {
                name: {key: by_suffix[name][key] for key in ("match", "mismatch", "excludedMismatch")}
                for name in ("one_digit", "zero_padded_two_digit", "four_digit", "other")}}
            precedes_edges = [edge_id("precedes", from_step, to_step) for from_step, _ in pair_edges]
            handoff_edges = [edge_id("hands_off", from_step, to_step) for from_step, _ in pair_edges]
            for from_step, count in pair_edges:
                for kind, evidence in (("precedes", precedes_evidence), ("hands_off", handoff_evidence)):
                    # 건수는 (from processId, to 단계) 단위라 같은 from processId의 adapter별 간선이 같은 값을 공유한다.
                    edges.append({"id": edge_id(kind, from_step, to_step), "kind": kind, "from": from_step,
                                  "to": to_step, "verification": None,
                                  "evidence": {**evidence, "observedTransactions": count,
                                               "countsKey": "from processId x to step"}})
            precedes_item = _candidate("precedes", interface_id, from_pid, target, precedes_edges, [],
                                       precedes_evidence, "successor")
            precedes_listed = precedes_evidence["status"] != "not_candidate"
            if precedes_listed:
                candidates.append(precedes_item)
            if handoff_evidence["applicable"] == 0:
                not_listed["messageHandoffNoApplicable"] += 1
                continue
            if handoff_evidence["status"] == "not_candidate":
                not_listed["messageHandoffNotCandidate"] += 1
                continue
            if not precedes_listed:
                candidates.append({**precedes_item, "listedForPairing": True})
            candidates.append({**_candidate("message_handoff", interface_id, from_pid, target, handoff_edges, [],
                                            handoff_evidence, "successor"),
                               "pairedPrecedesEdgeId": precedes_item["patternEdgeId"],
                               # 경고용 정보: 짝 precedes가 승인 가능한가. handoff의 approvable과 무관하다.
                               "pairApprovable": precedes_item["approvable"]})

        successors: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for from_step, to_step in sorted(observed):
            successors[from_step].append((to_step, observed[(from_step, to_step)]))
        for from_step in sorted(successors):
            branch = successors[from_step]
            if len(branch) < 2:
                continue
            for to_step, count in branch:
                edges.append({"id": edge_id("branches_to", from_step, to_step), "kind": "branches_to",
                              "from": from_step, "to": to_step, "verification": None,
                              "evidence": {"observedTransactions": count, "fromTransactions": step_tx[from_step]}})
            notes.append({"kind": "allowed_branch", "ruleCandidate": False,
                          "from": {"processId": steps[from_step]["processId"]},
                          "display": {"interfaceId": interface_id, "fromStep": from_step},
                          "fromTransactions": step_tx[from_step],
                          "successors": [{"to": {"processId": steps[to]["processId"]}, "toStep": to,
                                          "observedTransactions": count} for to, count in branch],
                          "nonStandardProcessId": _non_standard(steps[from_step]["processId"],
                                                                *(steps[to]["processId"] for to, _ in branch))})
    statuses = Counter(item["status"] for item in candidates)
    return {
        "nodes": nodes, "edges": edges, "candidates": candidates, "relationNotes": notes,
        "excluded": {"processIds": dict(sorted(excluded_counts.items())),
                     "messageRowsByProcessId": dict(sorted(excluded_message_counts.items())),
                     "transactions": dict(sorted(excluded_tx_reasons.items())),
                     "supersededRevisions": {"process": superseded_p, "transaction": superseded_t,
                                             "message": superseded_m}},
        "unassigned": {"transactions": dict(sorted(unassigned_tx.items())),
                       "rows": {key: unassigned_rows[key] for key in
                                ("processTransactionIdAbsent", "unkeyedProcessRows", "messageKeyAbsent")}},
        "interfaceTransactions": {interface_id: len(txs) for interface_id, txs in sorted(per_interface.items())},
        "notListed": {key: not_listed[key] for key in ("messageHandoffNoApplicable", "messageHandoffNotCandidate")},
        # 승인 가능한 것(candidate·review_needed)만 후보 수로 센다. 표시만 하는 항목은 따로 둔다.
        "candidateCounts": {"approvable": statuses["candidate"] + statuses["review_needed"],
                            "candidate": statuses["candidate"], "reviewNeeded": statuses["review_needed"],
                            "listedNotApprovable": {"insufficientEvidence": statuses["insufficient_evidence"],
                                                    "notCandidateForPairing": statuses["not_candidate"]},
                            "listedTotal": len(candidates)},
    }


def _candidate(kind: str, interface_id: str, from_pid: str, target: Mapping[str, Any] | None,
               pattern_edges: Sequence[str], pattern_nodes: Sequence[str], evidence: Mapping[str, Any],
               evaluated_at: str) -> dict[str, Any]:
    """후보 1건. 규칙 id 없음.

    precedes·message_handoff: 대조 키는 PROCESS 필드만(scope.adapterType = 판정 위치 to 단계 값, from/to processId),
    to 단계 adapter가 없으면 scope null, interfaceId는 display에만. required_step: to null, scope {interfaceId}
    (TRANSACTION 행 interface_id와 대조하는 판정 키, REQUIRED_STEP_SCOPE_POLICY).
    """
    counts = {key: evidence[key] for key in ("applicable", "satisfied", "violated", "undetermined")}
    to_adapter = None if target is None else target["adapterType"]
    item = {"kind": kind, "category": "hard", "status": evidence["status"],
            "approvable": evidence["status"] in ("candidate", "review_needed"),
            # required_step만 예외로 interfaceId가 판정 키(REQUIRED_STEP_SCOPE_POLICY). 나머지는 to 단계 adapter 또는 null.
            "scope": {"interfaceId": interface_id} if kind == "required_step" else
                     ({"adapterType": to_adapter} if to_adapter is not None else None),
            "from": {"processId": from_pid},
            "to": None if target is None else {"processId": target["processId"]},
            "match": {"from": "OUT", "to": "IN", "key": "message_id"} if kind == "message_handoff" else None,
            "patternEdgeId": pattern_edges[0] if pattern_edges else None, "patternEdgeIds": list(pattern_edges),
            "patternNodeIds": list(pattern_nodes),
            "counts": counts, "satisfactionRate": evidence["satisfactionRate"],
            "violationReasons": evidence["violationReasons"],
            "violationsByTransactionStatus": evidence["violationsByTransactionStatus"],
            "undeterminedReasons": evidence["undeterminedReasons"],
            "counterexampleRefs": evidence["counterexampleRefs"],
            "fromAdapterTypes": evidence["fromAdapterTypes"],
            "withinGroupAdapterMismatch": evidence.get("withinGroupAdapterMismatch"),
            # 판정 시점(사용자 결정 4): precedes·message_handoff는 후행 도착 때 첫 판정·TRANSACTION 도착 때 최종,
            # required_step은 TRANSACTION 도착 때 판정.
            "evaluatedAt": evaluated_at,
            "display": {"interfaceId": interface_id, "toStep": None if target is None else target["step"]},
            "attrs": {"nonStandardProcessId": _non_standard(from_pid, None if target is None else target["processId"])}}
    if "bySuffixClass" in evidence:
        item["bySuffixClass"] = evidence["bySuffixClass"]
    return item


# ---------------------------------------------------------------------------
# 처리 시간 임계값 후보(소프트, 출력 전용)
# ---------------------------------------------------------------------------

THRESHOLD_LIMITATION = (
    "elapsed_ms = END_TIME - START_TIME. END_TIME이 없으면 값이 없어 평가하지 않는다(methods/registry.py "
    "sourceMissingPolicy END_TIME=not_applicable). 늦게 끝난 단계만 잡고, 끝나지 않고 멈춘 단계는 잡지 못한다.")
THRESHOLD_EVIDENCE_NOTE = (
    "rows = scope 묶음의 train 행(실행마다 최신 리비전), referenceRows = 그중 status=S이고 명시 이상 라벨이 아닌 행. "
    "endTimeMissingReferenceRows·unavailableReferenceRows는 referenceRows 안에서 END_TIME 없음·계산 불가로 빠진 행 수")


def threshold_candidates(processes: Sequence[ProcessRow], interface_by_transaction: Mapping[str, str | None]
                         ) -> list[dict[str, Any]]:
    """scope 묶음(processId 필수, adapterType 선택)마다 기존 추천과 같은 표본·분위 기준으로 elapsed_ms p95를 계산한다.

    표본 규칙은 `threshold_recommendation.reference_sample`(status=S, 명시 이상 라벨 제외)과 같고, p95는
    `research_design.summary`(linear (n-1)*p), 직렬화는 `threshold_values`를 그대로 쓴다. 지지 기준은 관계 후보와 같은 30.
    통계는 scope 묶음별로 따로 낸다(기존 추천은 필드 전체). interfaceId는 PROCESS 행에 없으므로 display에만 둔다.
    PROCESS_ID가 없는 행은 scope를 만들 수 없어 빠진다(호출자가 unassigned에 센다).
    """
    import math

    from methods.registry import SOFT_RULE_FIELDS_BY_ID
    from research_design import summary
    from threshold_recommendation import EXCLUSION_RULE, reference_sample
    from threshold_values import ThresholdSerializationError, display_threshold, fixed_threshold

    elapsed = SOFT_RULE_FIELDS_BY_ID["elapsed_ms"]
    groups: dict[tuple[str, str | None], list[ProcessRow]] = defaultdict(list)
    for row in processes:
        if graph_value_absent(row.process_id):
            continue
        adapter = None if graph_value_absent(row.adapter_type) else row.adapter_type
        groups[(row.process_id, adapter)].append(row)
    items: list[dict[str, Any]] = []
    for (process_id, adapter) in sorted(groups, key=lambda key: (key[0], key[1] is not None, key[1] or "")):
        rows = groups[(process_id, adapter)]
        sample, excluded = reference_sample(
            [(row.observation_id, dict(row.raw), row.split, {"label": row.label} if row.label else None) for row in rows],
            [elapsed])
        measured = summary([item["features"]["elapsed_ms"] for item in sample])
        interfaces = sorted({value for value in (interface_by_transaction.get(row.transaction_id) for row in rows
                                                 if row.transaction_id is not None) if value is not None})
        status, threshold, display, reason = "candidate", None, None, None
        if measured["valid_count"] < MIN_APPLICABLE:
            status = "insufficient_evidence"
        elif measured["variation"] == "constant":
            status, reason = "no_variation", "all reference values are equal"
        elif measured["p95"] is None or not math.isfinite(measured["p95"]):
            status, reason = "rejected", "threshold_serialization_failed"
        else:
            try:
                threshold = fixed_threshold(measured["p95"], elapsed.value_type, elapsed.display_precision,
                                            elapsed.display_precision_mode)
                display = display_threshold(measured["p95"], elapsed.display_precision, elapsed.display_precision_mode)
            except ThresholdSerializationError as exc:
                status, reason = "rejected", str(exc)
        items.append({
            "field": "elapsed_ms", "category": "soft", "outputOnly": True, "status": status, "reason": reason,
            "scope": {"processId": process_id, **({"adapterType": adapter} if adapter is not None else {})},
            "display": {"interfaceIds": interfaces,
                        "patternStepIds": [pattern_step_id(value, process_id, adapter) for value in interfaces]},
            "scopeSharedAcrossInterfaces": len(interfaces) > 1,
            "operator": "gt" if threshold is not None else None, "threshold": threshold, "displayThreshold": display,
            "unit": elapsed.unit, "source": "data_p95",
            "evidence": {"rows": len(rows), "referenceRows": len(sample), "n": measured["valid_count"],
                         "min": measured["min"], "max": measured["max"], "p95": measured["p95"],
                         "quantileMethod": measured["quantile_method"], "exclusionRule": EXCLUSION_RULE,
                         "endTimeMissingReferenceRows": excluded["elapsed_ms"]["not_applicable"],
                         "unavailableReferenceRows": excluded["elapsed_ms"]["unavailable"],
                         "note": THRESHOLD_EVIDENCE_NOTE},
            "limitation": THRESHOLD_LIMITATION,
            "nonStandardProcessId": _non_standard(process_id)})
    return items


# ---------------------------------------------------------------------------
# 작업 실행(DB 읽기만)
# ---------------------------------------------------------------------------

def parse_input(spec: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"kind", "snapshotId", "sourceId", "excludeProcessIds", "handoffMismatchSuffixPatterns"}
    if not isinstance(spec, Mapping) or set(spec) - allowed:
        raise ValueError("relation_input_invalid")
    try:
        snapshot_id = uuid.UUID(str(spec["snapshotId"]))
        source_id = uuid.UUID(str(spec["sourceId"])) if spec.get("sourceId") is not None else None
    except (KeyError, ValueError) as exc:
        raise ValueError("relation_input_invalid") from exc
    exclude = spec.get("excludeProcessIds", [])
    patterns = spec.get("handoffMismatchSuffixPatterns", list(DEFAULT_HANDOFF_MISMATCH_SUFFIX_PATTERNS))
    if not isinstance(exclude, list) or not all(isinstance(value, str) for value in exclude) or \
            len(set(exclude)) != len(exclude) or not isinstance(patterns, list):  # 원소 검사는 compile_suffix_patterns
        raise ValueError("relation_input_invalid")
    compile_suffix_patterns(patterns)
    return {"snapshotId": snapshot_id, "sourceId": source_id, "excludeProcessIds": exclude,
            "handoffMismatchSuffixPatterns": patterns}


def load_inputs(conn: Any, snapshot_id: uuid.UUID) -> dict[str, Any]:
    """TRAIN 스냅샷의 PROCESS 멤버와, 같은 원본·기준시각 안 같은 거래 ID 문자열의 TRANSACTION·MESSAGE 행을 읽는다."""
    with conn.cursor() as cur:
        cur.execute("""SELECT source_id, status::text, schema_version, observation_cutoff_at, selection_spec
                         FROM data_snapshots WHERE id=%s""", (snapshot_id,))
        snapshot = cur.fetchone()
        if snapshot is None or snapshot[1] != "ready" or snapshot[2] != "process-v1":
            raise ValueError("relation_snapshot_not_ready")
        source_id, _, _, cutoff, selection = snapshot
        cur.execute(
            """SELECT o.id::text, o.execution_id::text, o.revision_no, o.persisted_at, m.split::text, m.label_evidence,
                      o.allowed_values, p.transaction_id, p.process_id, p.depend_process_id, p.adapter_type, p.retry_count
                 FROM data_snapshot_members AS m
                 JOIN data_observations AS o ON o.id = m.observation_id
                 JOIN data_processes AS p ON p.observation_id = o.id
                WHERE m.snapshot_id = %s ORDER BY o.id""", (snapshot_id,))
        processes = [ProcessRow(r[0], r[1], r[2], r[3], r[7], r[8], r[9], r[10], r[11], r[6] or {},
                                (r[5] or {}).get("label") if isinstance(r[5], dict) else None, r[4])
                     for r in cur.fetchall()]
        member_transactions = """SELECT p.transaction_id FROM data_snapshot_members AS m
                 JOIN data_processes AS p ON p.observation_id = m.observation_id
                WHERE m.snapshot_id = %s AND m.split = 'train' AND p.transaction_id IS NOT NULL"""
        cur.execute(
            f"""SELECT o.id::text, o.execution_id::text, o.revision_no, o.persisted_at, t.transaction_id, t.interface_id,
                       t.status, t.retry_count
                  FROM data_transactions AS t JOIN data_observations AS o ON o.id = t.observation_id
                 WHERE o.source_id = %s AND o.layer = 'T' AND o.persisted_at <= %s
                   AND t.transaction_id IN ({member_transactions})""", (source_id, cutoff, snapshot_id))
        transactions = [TransactionRow(*r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT o.id::text, o.execution_id::text, o.revision_no, o.persisted_at, d.transaction_id, d.process_id,
                       d.message_id, d.direction
                  FROM data_messages AS d JOIN data_observations AS o ON o.id = d.observation_id
                 WHERE o.source_id = %s AND o.layer = 'M' AND o.persisted_at <= %s
                   AND d.transaction_id IN ({member_transactions})""", (source_id, cutoff, snapshot_id))
        messages = [MessageRow(*r) for r in cur.fetchall()]
    return {"sourceId": source_id, "cutoff": cutoff, "selection": selection or {}, "processes": processes,
            "transactions": transactions, "messages": messages}


def build_result(loaded: Mapping[str, Any], parsed: Mapping[str, Any], *, job_id: Any,
                 generated_at: datetime | None = None) -> dict[str, Any]:
    processes: list[ProcessRow] = loaded["processes"]
    latest, superseded = latest_per_execution(processes)
    train = [row for row in latest if row.split == "train"]
    # 분할 경계에 걸린 거래는 단계 일부가 train 밖에 있어 누락처럼 보이므로 관계 집계에서 뺀다.
    excluded: dict[str, str] = {}
    for row in latest:
        if row.split != "train" and not graph_value_absent(row.transaction_id):
            excluded[row.transaction_id] = "cross_split"
    for row in train:
        if row.label == "anomaly" and not graph_value_absent(row.transaction_id):
            excluded.setdefault(row.transaction_id, "anomaly_label")
    train_ids = {row.transaction_id for row in train}
    excluded = {key: value for key, value in excluded.items() if key in train_ids}
    mined = mine_relations(train, loaded["transactions"], loaded["messages"],
                           exclude_process_ids=parsed["excludeProcessIds"],
                           handoff_mismatch_suffix_patterns=parsed["handoffMismatchSuffixPatterns"],
                           excluded_transaction_ids=excluded)
    # 스냅샷이 이미 실행마다 최신 리비전만 담지만, 방어 규칙으로 다시 줄인 수를 여기서 기록한다.
    mined["excluded"]["supersededRevisions"]["process"] = superseded
    selection = loaded["selection"]
    period = {"from": selection.get("from"), "until": selection.get("until"),
              "timeBasis": selection.get("timeBasis")}
    snapshot_id = str(parsed["snapshotId"])
    for candidate in mined["candidates"]:
        candidate["source"] = {"jobId": str(job_id), "snapshotId": snapshot_id,
                               "patternEdgeId": candidate["patternEdgeId"], "patternNodeIds": candidate["patternNodeIds"],
                               "counts": candidate["counts"], "period": period}
    resolved = resolve_transactions(latest_per_execution(loaded["transactions"])[0])
    interfaces = {tid: info["interfaceId"] for tid, info in resolved.items()}
    excluded_values = set(parsed["excludeProcessIds"])
    elapsed_rows = [row for row in train if row.process_id not in excluded_values]
    mined["unassigned"]["rows"]["elapsedProcessIdAbsent"] = sum(graph_value_absent(row.process_id) for row in elapsed_rows)
    thresholds = threshold_candidates(elapsed_rows, interfaces)
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "relationContractVersion": CONTRACT_VERSION, "experimental": True,
        "snapshotId": snapshot_id, "sourceId": str(loaded["sourceId"]), "period": period,
        "parameters": {"minApplicable": MIN_APPLICABLE, "candidatePercent": CANDIDATE_PERCENT,
                       "reviewPercent": REVIEW_PERCENT, "maxCounterexamples": MAX_COUNTEREXAMPLES,
                       "excludeProcessIds": list(parsed["excludeProcessIds"]),
                       "handoffMismatchSuffixPatterns": list(parsed["handoffMismatchSuffixPatterns"]),
                       "handoffMismatchSuffixScope": "TRANSACTION_ID, mismatch only (match stays satisfied)",
                       "split": "train", "retryRule": "one bundle per (transaction, process_id), latest revision per execution",
                       "pairingPolicy": PAIRING_POLICY, "scopeNullPolicy": SCOPE_NULL_POLICY,
                       "judgementBasis": JUDGEMENT_BASIS, "adapterPolicy": ADAPTER_POLICY,
                       "transactionRowRule": TRANSACTION_ROW_RULE, "requiredStepScopePolicy": REQUIRED_STEP_SCOPE_POLICY,
                       "suffixPatternLimitNote": SUFFIX_PATTERN_LIMIT_NOTE,
                       "withinGroupAdapterMismatchNote": WITHIN_GROUP_ADAPTER_MISMATCH_NOTE,
                       "conservativeNote": CONSERVATIVE_NOTE, "transactionStatusRule": TRANSACTION_STATUS_RULE,
                       "reasonVocabulary": REASON_VOCABULARY,
                       "evaluatedAt": {"precedes": "successor", "message_handoff": "successor",
                                       "required_step": "transaction"},
                       "fromMatching": "from = processId only; observed from adapters in fromAdapterTypes",
                       "ruleIdPolicy": "candidates carry no rule id; the server assigns hard:rel-<8hex> on approval",
                       "scopePolicy": "precedes/message_handoff: scope.adapterType = evaluation step (to) adapter_type, "
                                      "null when absent, interfaceId display-only; required_step: scope.interfaceId"},
        "data": {"processRows": len(processes), "trainProcessRows": len(train),
                 "transactionRows": len(loaded["transactions"]), "messageRows": len(loaded["messages"]),
                 "interfaceTransactions": mined["interfaceTransactions"]},
        "excluded": mined["excluded"], "unassigned": mined["unassigned"], "notListed": mined["notListed"],
        "graph": {"graph": {"layer": "pattern", "id": snapshot_id, "version": None, "sourceId": str(loaded["sourceId"]),
                            "snapshotId": snapshot_id, "period": period, "ruleSetRevision": None,
                            "generatedAt": generated.isoformat(timespec="milliseconds").replace("+00:00", "Z")},
                  "nodes": mined["nodes"], "edges": mined["edges"]},
        "candidates": mined["candidates"], "relationNotes": mined["relationNotes"],
        "candidateCounts": mined["candidateCounts"],
        "thresholdCandidates": thresholds,
        "counts": {"approvable": mined["candidateCounts"]["approvable"], "relationNotes": len(mined["relationNotes"]),
                   "thresholdCandidateCount": sum(item["status"] == "candidate" for item in thresholds),
                   "thresholdItems": len(thresholds)},
    }


def execute(conn: Any, job: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_input(job["input"])
    loaded = load_inputs(conn, parsed["snapshotId"])
    conn.commit()
    if parsed["sourceId"] is not None and parsed["sourceId"] != loaded["sourceId"]:
        raise ValueError("snapshot_conditions_mismatch")
    if not any(row.split == "train" for row in loaded["processes"]):
        raise ValueError("recommendation_input_empty")
    return build_result(loaded, parsed, job_id=job["id"])
