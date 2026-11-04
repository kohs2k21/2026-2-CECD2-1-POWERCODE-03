"""내장 하드룰(화면: 이상 규칙) 목록과 행 단위 실행."""
from __future__ import annotations

from typing import Any, Mapping

from derived_expression import TABLE, evaluate as evaluate_derived
from detector.method_results import method_result, source_absent
from detector.status_rule import VERSION as STATUS_VERSION, default_policy, evaluate_status

PROCESS_STATUS = "hard:process-status"
NEGATIVE_DURATION = "hard:negative-duration"
NEGATIVE_DURATION_VERSION = "negative-duration-v1"

# 조건이나 단계를 바꾸면 새 methodVersion을 만든다. 과거 판정은 기록된 버전 그대로 둔다.
HARD_RULES = (
    {"id": PROCESS_STATUS, "methodVersion": STATUS_VERSION, "displayName": "상태가 S가 아님",
     "conditionText": "STATUS ≠ S",
     "severity": {"byCode": {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning",
                             "PROCESS_STATUS_N": "warning"}}},
    {"id": NEGATIVE_DURATION, "methodVersion": NEGATIVE_DURATION_VERSION,
     "displayName": "시작 시각이 종료 시각보다 늦음",
     "conditionText": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)", "severity": "critical"},
)
HARD_RULE_ORDER = {rule["id"]: index for index, rule in enumerate(HARD_RULES)}


def hard_rule_severity(rule: Mapping[str, Any], code: str) -> str:
    severity = rule["severity"]
    return severity["byCode"][code] if isinstance(severity, dict) else severity


def evaluate_process_status(raw: Mapping[str, Any], source_id: Any) -> dict[str, Any]:
    rule = HARD_RULES[HARD_RULE_ORDER[PROCESS_STATUS]]
    outcome = evaluate_status(default_policy(source_id), raw)
    item = outcome["evidence"][0]
    evidence = [{"observedStatus": item["observedStatus"], "result": item["result"]}]
    if outcome["status"] != "succeeded":
        cause = "input_missing" if source_absent(raw.get("status")) else "input_invalid"
        return method_result(PROCESS_STATUS, rule["methodVersion"], "not_ready", None, cause=cause, evidence=evidence)
    code = outcome["code"]
    return method_result(PROCESS_STATUS, rule["methodVersion"], "succeeded", outcome["anomalous"], code=code,
                         severity=hard_rule_severity(rule, code) if code else None, evidence=evidence)


def _time(raw: Mapping[str, Any], column: str):
    # 처리 시간(datetime_diff)과 같은 해석을 쓴다. 해석할 수 없으면 None이다.
    return evaluate_derived({"op": "column", "table": TABLE, "column": column}, raw)


def evaluate_negative_duration(raw: Mapping[str, Any]) -> dict[str, Any]:
    def undetermined(cause):
        return method_result(NEGATIVE_DURATION, NEGATIVE_DURATION_VERSION, "not_ready", None, cause=cause,
                             evidence=[{"result": "unavailable"}])

    if source_absent(raw.get("end_time_raw")):
        return method_result(NEGATIVE_DURATION, NEGATIVE_DURATION_VERSION, "succeeded", False,
                             evidence=[{"result": "not_applicable", "reason": "end_time_absent"}])
    if source_absent(raw.get("start_time_raw")):
        return undetermined("input_missing")
    start, end = _time(raw, "START_TIME"), _time(raw, "END_TIME")
    if start is None or end is None:
        return undetermined("input_invalid")
    try:
        hit = start > end
    except TypeError:
        # 시간대가 있는 값과 없는 값은 비교할 수 없다.
        return undetermined("input_invalid")
    rule = HARD_RULES[HARD_RULE_ORDER[NEGATIVE_DURATION]]
    code = "HARD_NEGATIVE_DURATION"
    return method_result(NEGATIVE_DURATION, NEGATIVE_DURATION_VERSION, "succeeded", hit, code=code,
                         severity=hard_rule_severity(rule, code), evidence=[{"result": "hit" if hit else "no_hit"}])


def evaluate_hard_rules(enabled_ids: Any, raw: Mapping[str, Any], source_id: Any) -> list[dict[str, Any]]:
    """켜진 하드룰을 내장 목록 순서로 실행한다."""
    enabled = set(enabled_ids)
    unknown = sorted(enabled - set(HARD_RULE_ORDER))
    if unknown:
        raise ValueError(f"hard_rule_unknown:{unknown[0]}")
    runners = {PROCESS_STATUS: lambda: evaluate_process_status(raw, source_id),
               NEGATIVE_DURATION: lambda: evaluate_negative_duration(raw)}
    return [runners[rule["id"]]() for rule in HARD_RULES if rule["id"] in enabled]
