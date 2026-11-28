"""내장 하드룰(화면: 이상 규칙) 목록과 행 단위 실행."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, MAX_EMAX, MIN_EMIN
import re
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from derived_expression import TABLE, evaluate as evaluate_derived, number
from detector.method_results import SEVERITIES, method_result, source_absent, unresolved_result
from detector.relation_graph import graph_trim, graph_value_absent
from detector.status_rule import VERSION as LEGACY_STATUS_VERSION

if TYPE_CHECKING:
    from detector.relation_inputs import RelationInputs

PROCESS_STATUS = "hard:process-status"
NEGATIVE_DURATION = "hard:negative-duration"
SUCCESS_WITH_ERROR = "hard:success-with-error"
COUNT_CONSERVATION = "hard:count-conservation"
# 룰 세트 v3에서 생긴 내장 하드룰(rule-set.md v3.5). v1·v2 행에 있으면 시도 실패(operational_state).
TRANSACTION_FAILED = "hard:transaction-failed"
UNREGISTERED_INTERFACE = "hard:unregistered-interface"
# v1·legacy 판정은 status_rule.py를 그대로 쓴다. 이 v2는 v2 정책 판정 전용이다.
PROCESS_STATUS_VERSION = "process-status-hr-v2"
KNOWN_STATUSES = frozenset({"S", "F", "P", "N", "H", "Z"})

# 조건이나 기본 단계를 바꾸면 새 methodVersion을 만든다. 과거 판정은 기록된 버전 그대로 둔다.
HARD_RULES = (
    {"id": PROCESS_STATUS, "methodVersion": PROCESS_STATUS_VERSION, "displayName": "상태가 S가 아님",
     "conditionText": "STATUS ≠ S",
     "severity": {"byCode": {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning",
                             "PROCESS_STATUS_N": "info", "PROCESS_STATUS_H": "info",
                             "PROCESS_STATUS_Z": "warning"}}},
    {"id": NEGATIVE_DURATION, "methodVersion": "negative-duration-v1",
     "displayName": "시작 시각이 종료 시각보다 늦음",
     "conditionText": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)", "severity": "critical"},
    {"id": SUCCESS_WITH_ERROR, "methodVersion": "success-with-error-v1",
     "displayName": "성공 상태이나 오류 건수가 기록됨", "conditionText": "STATUS = S, ERROR_COUNT > 0", "severity": "critical"},
    {"id": COUNT_CONSERVATION, "methodVersion": "count-conservation-v1",
     "displayName": "건수 합계 불일치", "conditionText": "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT",
     "severity": "critical"},
    # relation-rules.md 7절. 거래 행이 있어야 평가한다(없으면 수집 미완료 보류 — 방법 결과가 아니다).
    {"id": TRANSACTION_FAILED, "methodVersion": "transaction-failed-v1", "displayName": "거래 실패",
     "conditionText": "거래 STATUS = F, 응답 코드가 0000·S·없음이 아님",
     "severity": {"byCode": {}, "default": "warning"}},
    # protocol-rules.md 4.1. 프로토콜 기능(켜진 protocolRules 항목)이 켜진 판정에서만 평가한다.
    {"id": UNREGISTERED_INTERFACE, "methodVersion": "unregistered-interface-v1",
     "displayName": "등록되지 않은 인터페이스",
     "conditionText": "프로토콜 설정에 없는 인터페이스의 거래(켜진 프로토콜 항목이 1개 이상일 때만)",
     "severity": "critical"},
)
HARD_RULE_ORDER = {rule["id"]: index for index, rule in enumerate(HARD_RULES)}
V3_HARD_RULE_IDS = (TRANSACTION_FAILED, UNREGISTERED_INTERFACE)
# 켜져 있어도 판정마다 평가하지 않을 수 있는 내장 규칙(수집 미완료 보류·프로토콜 기능 꺼짐 등, evidence_v4 대조 집합).
CONDITIONAL_HARD_RULE_IDS = frozenset(V3_HARD_RULE_IDS)
# 판정이 아직 없는 v3 요소가 켜져 있으면 쓰는 시도 실패 코드(protocol-rules.md 10절 1단계).
RULE_SET_V3_UNSUPPORTED = "rule_set_v3_unsupported"
# 승인 저장 때 API가 만드는 하드룰 id(rule-set.md v3.4). 판정 순서: 내장 목록 → 관계 규칙 저장 순 → 프로토콜 저장 순.
# 정규식은 앵커가 없으므로(Python `$`는 끝 줄바꿈 앞에서도 맞는다) 직접 쓰지 말고 아래 함수(fullmatch)로만 본다.
_RELATION_RULE_ID = re.compile(r"hard:rel-[0-9a-f]{8}")
_PROTOCOL_RULE_ID = re.compile(r"hard:proto-[0-9a-f]{8}")
STATUS_LEVELS = HARD_RULES[HARD_RULE_ORDER[PROCESS_STATUS]]["severity"]["byCode"]
TRANSACTION_FAILED_SUCCESS_CODES = frozenset({"0000", "S"})


def is_relation_rule_id(value: Any) -> bool:
    """`^hard:rel-[0-9a-f]{8}$` 전체 일치(끝 줄바꿈·접두만 맞는 값 거부)."""
    return isinstance(value, str) and _RELATION_RULE_ID.fullmatch(value) is not None


def is_protocol_rule_id(value: Any) -> bool:
    """`^hard:proto-[0-9a-f]{8}$` 전체 일치."""
    return isinstance(value, str) and _PROTOCOL_RULE_ID.fullmatch(value) is not None


@dataclass(frozen=True)
class HardRuleSelection:
    """시도에 고정한 하드룰 입력. 평가와 evidence 기록이 같은 객체를 받아 단계가 어긋나지 않게 한다."""
    enabled_ids: tuple[str, ...]
    status_level_items: tuple[tuple[str, str], ...]
    # hard:transaction-failed 위험도 {byCode, default}(코드 순). 스냅샷에 없으면 내장 기본값.
    transaction_failed_level_items: tuple[tuple[str, str], ...] = ()
    transaction_failed_default: str = "warning"
    unregistered_interface_level: str = "critical"
    # 켜진 관계 규칙 중 판정 kind(hard:rel-*)의 위험도(룰 세트 저장 순). 운영 상태 고정이 채운다.
    relation_rule_levels: tuple[tuple[str, str], ...] = ()
    # 켜진 프로토콜 항목(hard:proto-*)의 위험도(룰 세트 저장 순). 운영 상태 고정이 채운다.
    protocol_rule_levels: tuple[tuple[str, str], ...] = ()

    @property
    def status_levels(self) -> dict[str, str]:
        return dict(self.status_level_items)

    def transaction_failed_level(self, response_code: str) -> str:
        """응답 코드(ASCII 공백류를 앞뒤에서 뺀 값)로 byCode를 찾고, 없으면 default."""
        return dict(self.transaction_failed_level_items).get(graph_trim(response_code),
                                                             self.transaction_failed_default)


def _rule(rule_id: str) -> Mapping[str, Any]:
    return HARD_RULES[HARD_RULE_ORDER[rule_id]]


def _snapshot_status_levels(item: Mapping[str, Any]) -> dict[str, str]:
    """v2 항목의 severity만 사용자 설정이다. v1 항목의 값은 그때 내장값의 투영이라 쓰지 않는다."""
    version = item.get("methodVersion")
    if version is None or version == LEGACY_STATUS_VERSION:
        return dict(STATUS_LEVELS)
    if version != PROCESS_STATUS_VERSION:
        raise ValueError(f"hard_rule_version_unknown:{PROCESS_STATUS}")
    severity = item.get("severity")
    by_code = severity.get("byCode") if isinstance(severity, Mapping) and set(severity) == {"byCode"} else None
    if not isinstance(by_code, Mapping) or set(by_code) != set(STATUS_LEVELS) or any(
            not isinstance(level, str) or level not in SEVERITIES for level in by_code.values()):
        raise ValueError(f"invalid_hard_rule_severity:{PROCESS_STATUS}")
    return dict(by_code)


def _check_version(item: Mapping[str, Any]) -> None:
    """v3 내장 규칙 항목의 methodVersion은 없거나 지금 정의와 같아야 한다(다른 조건으로 저장된 값을 쓰지 않는다)."""
    rule = _rule(item["id"])
    if item.get("methodVersion") not in (None, rule["methodVersion"]):
        raise ValueError(f"hard_rule_version_unknown:{rule['id']}")


def _snapshot_transaction_failed(item: Mapping[str, Any]) -> tuple[tuple[tuple[str, str], ...], str]:
    """rule-set.md v3.6: severity는 정확히 {byCode, default}. 저장 검증을 거치지 않은 모양이면 시도 실패."""
    _check_version(item)
    severity = item.get("severity")
    by_code = severity.get("byCode") if isinstance(severity, Mapping) and set(severity) == {"byCode", "default"} \
        else None
    if not isinstance(by_code, Mapping) or not isinstance(severity["default"], str) or \
            severity["default"] not in SEVERITIES or any(
            not isinstance(code, str) or not isinstance(level, str) or level not in SEVERITIES
            for code, level in by_code.items()):
        raise ValueError(f"invalid_hard_rule_severity:{TRANSACTION_FAILED}")
    return tuple(sorted(by_code.items())), severity["default"]


def _snapshot_single_level(item: Mapping[str, Any]) -> str:
    _check_version(item)
    level = item.get("severity")
    if not isinstance(level, str) or level not in SEVERITIES:
        raise ValueError(f"invalid_hard_rule_severity:{item['id']}")
    return level


def read_hard_rule_snapshot(snapshot_rules: Any, relation_rule_levels: tuple[tuple[str, str], ...] = (),
                            protocol_rule_levels: tuple[tuple[str, str], ...] = ()) -> HardRuleSelection:
    """룰 세트 스냅샷 hardRules[]에서 켜진 규칙(내장 목록 순)과 상태 규칙의 코드별 단계를 읽는다.

    스냅샷에 없는 내장 규칙은 꺼짐이다. 위험도는 편집 가능한 규칙(상태·거래 실패·미등록 인터페이스)만 스냅샷
    값을 쓰고, 그 밖의 조건·단계는 내장 목록을 쓴다. v1·v2 행에 v3 내장 규칙이 있는지는 호출자(operational_state)가
    버전과 함께 먼저 거른다. relation_rule_levels는 운영 상태 고정이 검증한 켜진 관계 판정 규칙의 (id, severity)다
    (판정·evidence 대조가 같은 객체를 쓰도록 여기서 함께 담는다). protocol_rule_levels는 켜진 프로토콜 항목의 같은 값이다.
    """
    if not isinstance(snapshot_rules, list):
        raise ValueError("invalid_hard_rule_snapshot")
    enabled: dict[str, bool] = {}
    levels = dict(STATUS_LEVELS)
    failed_levels, failed_default = (), _rule(TRANSACTION_FAILED)["severity"]["default"]
    unregistered_level = _rule(UNREGISTERED_INTERFACE)["severity"]
    for item in snapshot_rules:
        rule_id = item.get("id") if isinstance(item, Mapping) else None
        if not isinstance(rule_id, str) or rule_id not in HARD_RULE_ORDER:
            raise ValueError(f"hard_rule_unknown:{rule_id}")
        if rule_id in enabled:
            raise ValueError("duplicate_method_id")
        if type(item.get("enabled")) is not bool:
            raise ValueError(f"invalid_hard_rule_enabled:{rule_id}")
        enabled[rule_id] = item["enabled"]
        if rule_id == PROCESS_STATUS:
            levels = _snapshot_status_levels(item)
        elif rule_id == TRANSACTION_FAILED:
            failed_levels, failed_default = _snapshot_transaction_failed(item)
        elif rule_id == UNREGISTERED_INTERFACE:
            unregistered_level = _snapshot_single_level(item)
    return HardRuleSelection(tuple(rule["id"] for rule in HARD_RULES if enabled.get(rule["id"])),
                             tuple((code, levels[code]) for code in STATUS_LEVELS),
                             failed_levels, failed_default, unregistered_level, tuple(relation_rule_levels),
                             tuple(protocol_rule_levels))


def status_rule_policy(source_id: Any, hard_rules: HardRuleSelection) -> dict[str, Any]:
    """그 판정에 쓴 상태 규칙 정책. evidence statusRulePolicy에 그대로 남긴다."""
    mapping = {code.removeprefix("PROCESS_STATUS_"): level for code, level in hard_rules.status_level_items}
    return {"version": PROCESS_STATUS_VERSION, "sourceId": str(source_id), "mapping": {"S": "no_hit", **mapping}}


def _undetermined(rule: Mapping[str, Any], cause: str) -> dict[str, Any]:
    return method_result(rule["id"], rule["methodVersion"], "not_ready", None, cause=cause,
                         evidence=[{"result": "unavailable"}])


def _decided(rule: Mapping[str, Any], hit: bool) -> dict[str, Any]:
    code = "HARD_" + rule["id"].split(":", 1)[1].upper().replace("-", "_")
    return method_result(rule["id"], rule["methodVersion"], "succeeded", hit, code=code, severity=rule["severity"],
                         evidence=[{"result": "hit" if hit else "no_hit"}])


def evaluate_process_status(raw: Mapping[str, Any], hard_rules: HardRuleSelection) -> dict[str, Any]:
    rule = _rule(PROCESS_STATUS)
    value = raw.get("status")

    def result(status, anomalous, outcome, **fields):
        evidence = {"observedStatus": value, "result": outcome}
        if "reason" in fields:
            evidence["reason"] = fields.pop("reason")
        return method_result(PROCESS_STATUS, rule["methodVersion"], status, anomalous, evidence=[evidence], **fields)

    if source_absent(value):
        return result("not_ready", None, "unavailable", cause="input_missing")
    if not isinstance(value, str) or value not in KNOWN_STATUSES:
        return result("not_ready", None, "unavailable", cause="input_invalid")
    if value == "S":
        return result("succeeded", False, "no_hit")
    if value == "N" and source_absent(raw.get("end_time_raw")):
        # 미처리로 찍힌 뒤 종료 시각이 없는 행은 이상이 아니다.
        return result("succeeded", False, "not_applicable", reason="end_time_absent")
    code = "PROCESS_STATUS_" + value
    return result("succeeded", True, "hit", code=code, severity=hard_rules.status_levels[code])


def _time(raw: Mapping[str, Any], column: str):
    # 처리 시간(datetime_diff)과 같은 해석을 쓴다. 해석할 수 없으면 None이다.
    return evaluate_derived({"op": "column", "table": TABLE, "column": column}, raw)


def evaluate_negative_duration(raw: Mapping[str, Any]) -> dict[str, Any]:
    rule = _rule(NEGATIVE_DURATION)
    if source_absent(raw.get("end_time_raw")):
        return method_result(rule["id"], rule["methodVersion"], "succeeded", False,
                             evidence=[{"result": "not_applicable", "reason": "end_time_absent"}])
    if source_absent(raw.get("start_time_raw")):
        return _undetermined(rule, "input_missing")
    start, end = _time(raw, "START_TIME"), _time(raw, "END_TIME")
    if start is None or end is None:
        return _undetermined(rule, "input_invalid")
    try:
        hit = start > end
    except TypeError:
        # 시간대가 있는 값과 없는 값은 비교할 수 없다.
        return _undetermined(rule, "input_invalid")
    return _decided(rule, hit)


def _count_rule(rule: Mapping[str, Any], raw: Mapping[str, Any], targets: frozenset[str], fields: tuple[str, ...],
                condition: Callable[..., bool]) -> dict[str, Any]:
    status = raw.get("status")
    if source_absent(status):
        return _undetermined(rule, "input_missing")
    if not isinstance(status, str) or status not in KNOWN_STATUSES:
        return _undetermined(rule, "input_invalid")
    if status not in targets:
        return _decided(rule, False)
    if any(source_absent(raw.get(field)) for field in fields):
        return _undetermined(rule, "input_missing")
    counts = [number(raw.get(field)) for field in fields]
    if None in counts:
        return _undetermined(rule, "input_invalid")
    try:
        return _decided(rule, condition(*counts))
    except Inexact:
        return _undetermined(rule, "input_invalid")


def _exact_sum(left: Decimal, right: Decimal) -> Decimal:
    # 기본 문맥(28자리)은 합계를 반올림한다. 정확히 더할 수 없는 값은 해석 불가로 둔다.
    return Context(prec=100, Emax=MAX_EMAX, Emin=MIN_EMIN, traps=[Inexact]).add(left, right)


def evaluate_success_with_error(raw: Mapping[str, Any]) -> dict[str, Any]:
    return _count_rule(_rule(SUCCESS_WITH_ERROR), raw, frozenset({"S"}), ("error_count",), lambda error: error > 0)


def evaluate_count_conservation(raw: Mapping[str, Any]) -> dict[str, Any]:
    return _count_rule(_rule(COUNT_CONSERVATION), raw, frozenset({"S", "F"}),
                       ("total_count", "success_count", "error_count"),
                       lambda total, success, error: total != _exact_sum(success, error))


def evaluate_transaction_failed(inputs: "RelationInputs", hard_rules: HardRuleSelection) -> dict[str, Any]:
    """relation-rules.md 7.2. 거래 행이 아직 없는 판정은 과제가 아니다(relation_plan이 수집 미완료 보류로 뺀다).

    판정 관측 거래 ID 없음 → input_missing / 대표 행 모호 → input_invalid / STATUS 없음 → input_missing.
    STATUS F이고 응답 코드가 제외 목록(0000·S·없음) 밖이면 T, 그 밖은 N. 비교는 ASCII 공백류를 앞뒤에서 뺀 값
    (incidents.py 성공 코드 비교와 같음), evidence의 응답 코드는 원래 값.
    """
    rule = _rule(TRANSACTION_FAILED)

    def held(cause: str, reason: str) -> dict[str, Any]:
        return method_result(rule["id"], rule["methodVersion"], "not_ready", None, cause=cause,
                             evidence=[{"reason": reason}])

    if not inputs.transaction_keyed:
        return held("input_missing", "transaction_id_missing")
    if inputs.transaction_executions < 1:
        raise ValueError("transaction_failed_without_transaction_row")
    if inputs.transaction_ambiguous:
        return held("input_invalid", "transaction_row_ambiguous")
    row = inputs.transaction
    status, code = row.get("status"), row.get("responseCode")
    if graph_value_absent(status):
        return held("input_missing", "transaction_status_missing")
    summary = {"transactionObservationId": row["observationId"]}
    failed = graph_trim(status) == "F" and not graph_value_absent(code) and \
        graph_trim(code) not in TRANSACTION_FAILED_SUCCESS_CODES
    if not failed:
        return method_result(rule["id"], rule["methodVersion"], "succeeded", False,
                             evidence=[{"result": "no_hit", **summary}])
    return method_result(rule["id"], rule["methodVersion"], "succeeded", True, code="HARD_TRANSACTION_FAILED",
                         severity=hard_rules.transaction_failed_level(code),
                         evidence=[{"result": "hit", **summary, "responseCode": code}])


def hard_rule_runners(hard_rules: HardRuleSelection, raw: Mapping[str, Any],
                      evaluated_ids: Iterable[str] | None = None, relation_inputs: "RelationInputs | None" = None,
                      relation_results: Mapping[str, Mapping[str, Any]] | None = None,
                      ) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
    """이 판정에서 평가할 하드룰을 (methodId, 실행 함수)로 돌려준다. 방법마다 따로 실행·측정하기 위해서다.

    evaluated_ids가 없으면 켜진 내장 규칙 전부(내장 목록 순). 실행기가 없는 규칙이 들어오면 정상처럼 넘기지 않고
    시도를 실패시킨다(아직 판정이 없는 요소, protocol-rules.md 10절). relation_results는 관계 규칙(hard:rel-*)·
    프로토콜 규칙(hard:proto-*, hard:unregistered-interface)을 시도 시작 때 판정한 결과(relation_plan) — 그 과제는
    이 결과를 돌려준다.
    """
    runners = {PROCESS_STATUS: lambda: evaluate_process_status(raw, hard_rules),
               NEGATIVE_DURATION: lambda: evaluate_negative_duration(raw),
               SUCCESS_WITH_ERROR: lambda: evaluate_success_with_error(raw),
               COUNT_CONSERVATION: lambda: evaluate_count_conservation(raw)}
    if relation_inputs is not None:
        runners[TRANSACTION_FAILED] = lambda: evaluate_transaction_failed(relation_inputs, hard_rules)
    for rule_id, result in (relation_results or {}).items():
        if is_relation_rule_id(rule_id) or is_protocol_rule_id(rule_id) or rule_id == UNREGISTERED_INTERFACE:
            runners[rule_id] = lambda result=result: dict(result)
    selected = tuple(hard_rules.enabled_ids if evaluated_ids is None else evaluated_ids)
    for rule_id in selected:
        if rule_id not in runners:
            raise ValueError(f"{RULE_SET_V3_UNSUPPORTED}:{rule_id}")
    return [(rule_id, runners[rule_id]) for rule_id in selected]


def evaluate_hard_rules(hard_rules: HardRuleSelection, raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [run() for _, run in hard_rule_runners(hard_rules, raw)]


def unresolved_hard_result(rule_id: str, cause: str, *, status: str = "not_ready",
                           failure_code: str | None = None, method_version: str | None = None) -> dict[str, Any]:
    """기한 초과처럼 입력을 읽기 전에 끝난 경우. 상태 규칙은 observedStatus를 null로 남긴다.

    method_version: 내장 목록 밖 하드룰(관계 규칙 hard:rel-*)의 버전. 없으면 내장 목록 값.
    """
    summary = {"observedStatus": None, "result": "unavailable"} if rule_id == PROCESS_STATUS else \
        {"result": "unavailable"}
    version = _rule(rule_id)["methodVersion"] if method_version is None else method_version
    return unresolved_result(rule_id, version, cause, status=status, failure_code=failure_code, evidence=[summary])
