"""내장 하드룰(화면: 이상 규칙) 목록과 행 단위 실행."""
from __future__ import annotations

from decimal import Context, Decimal, Inexact, MAX_EMAX, MIN_EMIN
from typing import Any, Callable, Mapping

from derived_expression import TABLE, evaluate as evaluate_derived, number
from detector.method_results import SEVERITIES, method_result, source_absent

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
     "displayName": "성공인데 오류 건수가 있음", "conditionText": "STATUS = S, ERROR_COUNT > 0", "severity": "critical"},
    {"id": COUNT_CONSERVATION, "methodVersion": "count-conservation-v1",
     "displayName": "건수 합계가 맞지 않음", "conditionText": "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT",
     "severity": "critical"},
)
HARD_RULE_ORDER = {rule["id"]: index for index, rule in enumerate(HARD_RULES)}
STATUS_LEVELS = HARD_RULES[HARD_RULE_ORDER[PROCESS_STATUS]]["severity"]["byCode"]


def _rule(rule_id: str) -> Mapping[str, Any]:
    return HARD_RULES[HARD_RULE_ORDER[rule_id]]


def _snapshot_status_levels(severity: Any) -> dict[str, str]:
    if severity is None:
        return {}
    by_code = severity.get("byCode") if isinstance(severity, Mapping) and set(severity) == {"byCode"} else None
    if not isinstance(by_code, Mapping) or any(
            code not in STATUS_LEVELS or not isinstance(level, str) or level not in SEVERITIES
            for code, level in by_code.items()):
        raise ValueError(f"invalid_hard_rule_severity:{PROCESS_STATUS}")
    return dict(by_code)


def read_hard_rule_snapshot(snapshot_rules: Any) -> tuple[list[str], dict[str, str]]:
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
            levels.update(_snapshot_status_levels(item.get("severity")))
    return [rule["id"] for rule in HARD_RULES if enabled.get(rule["id"])], levels


def status_rule_policy(source_id: Any, status_levels: Mapping[str, str] | None = None) -> dict[str, Any]:
    """그 판정에 쓴 상태 규칙 정책. evidence statusRulePolicy에 그대로 남긴다."""
    levels = {**STATUS_LEVELS, **(status_levels or {})}
    mapping = {code.removeprefix("PROCESS_STATUS_"): level for code, level in levels.items()}
    return {"version": PROCESS_STATUS_VERSION, "sourceId": str(source_id), "mapping": {"S": "no_hit", **mapping}}


def _undetermined(rule: Mapping[str, Any], cause: str) -> dict[str, Any]:
    return method_result(rule["id"], rule["methodVersion"], "not_ready", None, cause=cause,
                         evidence=[{"result": "unavailable"}])


def _decided(rule: Mapping[str, Any], hit: bool) -> dict[str, Any]:
    code = "HARD_" + rule["id"].split(":", 1)[1].upper().replace("-", "_")
    return method_result(rule["id"], rule["methodVersion"], "succeeded", hit, code=code, severity=rule["severity"],
                         evidence=[{"result": "hit" if hit else "no_hit"}])


def evaluate_process_status(raw: Mapping[str, Any], status_levels: Mapping[str, str] | None = None) -> dict[str, Any]:
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
    return result("succeeded", True, "hit", code=code, severity={**STATUS_LEVELS, **(status_levels or {})}[code])


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


def evaluate_hard_rules(enabled_ids: Any, raw: Mapping[str, Any],
                        status_levels: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """켜진 하드룰을 내장 목록 순서로 실행한다."""
    enabled = set(enabled_ids)
    unknown = sorted(enabled - set(HARD_RULE_ORDER))
    if unknown:
        raise ValueError(f"hard_rule_unknown:{unknown[0]}")
    runners = {PROCESS_STATUS: lambda: evaluate_process_status(raw, status_levels),
               NEGATIVE_DURATION: lambda: evaluate_negative_duration(raw),
               SUCCESS_WITH_ERROR: lambda: evaluate_success_with_error(raw),
               COUNT_CONSERVATION: lambda: evaluate_count_conservation(raw)}
    return [runners[rule["id"]]() for rule in HARD_RULES if rule["id"] in enabled]
