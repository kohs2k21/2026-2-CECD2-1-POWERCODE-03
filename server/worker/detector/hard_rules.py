"""내장 하드룰(화면: 이상 규칙) 목록과 행 단위 실행."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, MAX_EMAX, MIN_EMIN
from typing import Any, Callable, Mapping

from derived_expression import TABLE, evaluate as evaluate_derived, number
from detector.method_results import SEVERITIES, method_result, source_absent, unresolved_result
from detector.status_rule import VERSION as LEGACY_STATUS_VERSION

PROCESS_STATUS = "hard:process-status"
NEGATIVE_DURATION = "hard:negative-duration"
SUCCESS_WITH_ERROR = "hard:success-with-error"
COUNT_CONSERVATION = "hard:count-conservation"
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
)
HARD_RULE_ORDER = {rule["id"]: index for index, rule in enumerate(HARD_RULES)}
STATUS_LEVELS = HARD_RULES[HARD_RULE_ORDER[PROCESS_STATUS]]["severity"]["byCode"]


@dataclass(frozen=True)
class HardRuleSelection:
    """시도에 고정한 하드룰 입력. 평가와 evidence 기록이 같은 객체를 받아 단계가 어긋나지 않게 한다."""
    enabled_ids: tuple[str, ...]
    status_level_items: tuple[tuple[str, str], ...]

    @property
    def status_levels(self) -> dict[str, str]:
        return dict(self.status_level_items)


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


def read_hard_rule_snapshot(snapshot_rules: Any) -> HardRuleSelection:
    """룰 세트 스냅샷 hardRules[]에서 켜진 규칙(내장 목록 순)과 상태 규칙의 코드별 단계를 읽는다.

    스냅샷에 없는 내장 규칙은 꺼짐이다. 그 밖의 조건·단계는 스냅샷 값을 보지 않고 내장 목록을 쓴다.
    """
    if not isinstance(snapshot_rules, list):
        raise ValueError("invalid_hard_rule_snapshot")
    enabled: dict[str, bool] = {}
    levels = dict(STATUS_LEVELS)
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
    return HardRuleSelection(tuple(rule["id"] for rule in HARD_RULES if enabled.get(rule["id"])),
                             tuple((code, levels[code]) for code in STATUS_LEVELS))


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


def hard_rule_runners(hard_rules: HardRuleSelection,
                      raw: Mapping[str, Any]) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
    """켜진 하드룰을 내장 목록 순서의 (methodId, 실행 함수)로 돌려준다. 방법마다 따로 실행·측정하기 위해서다."""
    runners = {PROCESS_STATUS: lambda: evaluate_process_status(raw, hard_rules),
               NEGATIVE_DURATION: lambda: evaluate_negative_duration(raw),
               SUCCESS_WITH_ERROR: lambda: evaluate_success_with_error(raw),
               COUNT_CONSERVATION: lambda: evaluate_count_conservation(raw)}
    return [(rule_id, runners[rule_id]) for rule_id in hard_rules.enabled_ids]


def evaluate_hard_rules(hard_rules: HardRuleSelection, raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [run() for _, run in hard_rule_runners(hard_rules, raw)]


def unresolved_hard_result(rule_id: str, cause: str, *, status: str = "not_ready",
                           failure_code: str | None = None) -> dict[str, Any]:
    """기한 초과처럼 입력을 읽기 전에 끝난 경우. 상태 규칙은 observedStatus를 null로 남긴다."""
    summary = {"observedStatus": None, "result": "unavailable"} if rule_id == PROCESS_STATUS else \
        {"result": "unavailable"}
    return unresolved_result(rule_id, _rule(rule_id)["methodVersion"], cause, status=status,
                             failure_code=failure_code, evidence=[summary])
