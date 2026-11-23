"""판정 구조 v2의 방법 결과(evidence v4 `methodResults[]` 항목)를 만든다."""
from __future__ import annotations

import math
import numbers
import re
from typing import Any, Mapping

CATEGORIES = ("hard", "soft", "ml")
STATUSES = frozenset({"succeeded", "not_ready", "failed"})
SEVERITIES = frozenset({"info", "warning", "critical"})
CAUSES = frozenset({"input_missing", "input_invalid", "feature_unavailable", "not_configured",
                    "not_executable", "timeout", "capacity", "failure", "pending"})
FAILURE_CODES = frozenset({"invalid_rule_definition", "invalid_threshold", "invalid_score",
                           "artifact_invalid", "exception"})
METHOD_ID = re.compile(r"^(hard|soft|ml):[a-z][a-z0-9_-]{1,63}$")


def method_category(method_id: Any) -> str:
    """sr:* 등 새 식별자가 아니면 시도를 실패시키도록 예외를 낸다."""
    match = METHOD_ID.fullmatch(method_id) if isinstance(method_id, str) else None
    if match is None:
        raise ValueError(f"method_kind_not_allowed:{method_id}")
    return match.group(1)


def source_absent(value: Any) -> bool:
    """수집 경로에 따라 NULL이 빈 문자열이나 CSV 표기 \\N으로 남으므로 같이 본다."""
    return value is None or isinstance(value, str) and value.strip() in ("", "\\N")


def method_result(method_id: str, method_version: str, status: str, anomalous: bool | None, *,
                  code: str | None = None, severity: str | None = None, cause: str | None = None,
                  failure_code: str | None = None, evidence: Any = (), **extra: Any) -> dict[str, Any]:
    """code·severity·위험도는 T일 때만 남긴다. elapsedMs는 측정하는 호출자가 with_elapsed로 채운다."""
    if status not in STATUSES:
        raise ValueError("invalid_method_status")
    if status == "succeeded":
        if type(anomalous) is not bool or cause is not None:
            raise ValueError("invalid_method_result")
    elif cause not in CAUSES:
        raise ValueError("invalid_method_cause")
    elif anomalous is False or (cause == "pending" and (status != "not_ready" or anomalous is not None)):
        # 정상 확인은 succeeded뿐이다. 미완료여도 확인된 이상(true)은 남길 수 있으나 미도착 방법은 결과가 없다.
        raise ValueError("invalid_method_result")
    if (cause == "failure" and (status != "failed" or failure_code not in FAILURE_CODES)) or \
            (cause != "failure" and failure_code is not None):
        raise ValueError("invalid_failure_code")
    hit = anomalous is True
    level = severity if hit else None
    return {"methodId": method_id, "category": method_category(method_id), "methodVersion": method_version,
            "status": status, "anomalous": anomalous, "code": code if hit else None, "severity": level,
            "riskLevel": level, "riskLevelReason": "level_undefined" if hit and level is None else None,
            "cause": cause, "failureCode": failure_code, "elapsedMs": None, "evidence": list(evidence), **extra}


def with_elapsed(result: Mapping[str, Any], elapsed_ms: float) -> dict[str, Any]:
    return {**result, "elapsedMs": elapsed_ms}


def unresolved_result(method_id: str, method_version: str, cause: str, *, status: str = "not_ready",
                      failure_code: str | None = None, **extra: Any) -> dict[str, Any]:
    """기한 초과·슬롯 부족·미도착처럼 방법이 결과를 내지 못한 경우."""
    return method_result(method_id, method_version, status, None, cause=cause, failure_code=failure_code, **extra)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalized(raw: float, boundary: float, direction: str) -> dict[str, Any] | None:
    top, bottom = (raw, boundary) if direction == "higher_is_anomaly" else (boundary, raw)
    ratio = top / bottom if bottom > 0 else math.inf
    return {"kind": "threshold_ratio", "value": ratio} if math.isfinite(ratio) else None


def ml_method_result(method_id: str, method_version: str, model_version_id: Any, score: Any,
                     threshold_spec: Any, threshold_policy: Any, *, code: str) -> dict[str, Any]:
    """이미 계산한 점수를 판정선과 비교한다. 준비물이 없으면 정상으로 바꾸지 않는다."""
    if not isinstance(code, str) or not code:
        raise ValueError("ml_result_code_required")
    extra = {"modelVersionId": model_version_id, "score": None, "scoreDetail": None}
    spec = threshold_spec if isinstance(threshold_spec, Mapping) else {}
    direction = spec.get("direction")
    if direction not in ("higher_is_anomaly", "lower_is_anomaly") or spec.get("value") is None or not threshold_policy:
        return unresolved_result(method_id, method_version, "not_configured", **extra)
    boundary = _finite(spec["value"])
    if boundary is None:
        return unresolved_result(method_id, method_version, "failure", status="failed",
                                 failure_code="invalid_threshold", **extra)
    # np.float32는 float의 하위 형식이 아니다. raw는 float로 바뀌어 evidence 직렬화·hash에 numpy 값이 남지 않는다.
    raw = _finite(score) if isinstance(score, numbers.Real) else None
    if raw is None:
        return unresolved_result(method_id, method_version, "failure", status="failed",
                                 failure_code="invalid_score", **extra)
    anomalous = raw >= boundary if direction == "higher_is_anomaly" else raw <= boundary
    return method_result(method_id, method_version, "succeeded", anomalous, code=code,
                         modelVersionId=model_version_id, score=raw,
                         scoreDetail={"raw": raw, "threshold": str(spec["value"]), "direction": direction,
                                      "thresholdPolicy": threshold_policy,
                                      "normalized": _normalized(raw, boundary, direction)})
