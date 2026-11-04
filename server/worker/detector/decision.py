"""v2 정책의 조합·등급·위험도와 판정 행 열 값을 계산한다. DB를 읽지 않는 순수 함수다."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from detector.hard_rules import HARD_RULE_ORDER
from detector.method_results import CATEGORIES, method_category
from detector.policy import PolicyV2

RISK_POLICY = "risk-max-v1"
RISK_LEVEL_TABLE = "method-defined-v2"
RISK_RANK = {"info": 1, "warning": 2, "critical": 3}


def ensure_executable(policy: PolicyV2, method_ids: Iterable[str]) -> None:
    """고정한 리비전으로 다시 센 활성 방법이 저장 규칙을 어기면 판정 없이 시도를 실패시킨다."""
    ids = list(method_ids)
    categories = [method_category(method_id) for method_id in ids]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_method_id")
    if not ids:
        raise ValueError("detection_methods_empty")
    for category in CATEGORIES:
        n = categories.count(category)
        # min(k, n)으로 바꿔 평가하지 않는다. n = 0이면 그 대분류의 k는 무시한다.
        if n and not 1 <= policy.k[category] <= n:
            raise ValueError(f"combination_k_out_of_range:{category}")


def component_status(methods: list[Mapping[str, Any]]) -> str:
    if not methods:
        return "skipped"
    if all(method["status"] == "succeeded" for method in methods):
        return "succeeded"
    return "failed" if any(method["status"] == "failed" for method in methods) else "not_ready"


def requested_mode(methods: list[Mapping[str, Any]]) -> str:
    categories = {method["category"] for method in methods}
    if "ml" not in categories:
        return "rule_only"
    return "parallel" if categories & {"hard", "soft"} else "ml_only"


def _rank(method: Mapping[str, Any]) -> tuple[int, int]:
    method_id = method["methodId"]
    category = method_category(method_id)
    if method["category"] != category:
        raise ValueError(f"method_category_mismatch:{method_id}")
    if category == "hard" and method_id not in HARD_RULE_ORDER:
        raise ValueError(f"hard_rule_unknown:{method_id}")
    return CATEGORIES.index(category), HARD_RULE_ORDER.get(method_id, 0)


def _check_elapsed(method: Mapping[str, Any]) -> None:
    elapsed = method["elapsedMs"]
    if method["cause"] == "pending":
        if elapsed is not None:
            raise ValueError("pending_method_has_elapsed")
    elif isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed):
        raise ValueError("method_elapsed_required")


def _category_result(k: int, methods: list[Mapping[str, Any]]) -> dict[str, Any]:
    status = component_status(methods)
    if not methods:
        return {"n": 0, "k": k, "result": "skipped", "status": status, "anomalous": None}
    if sum(method["anomalous"] is True for method in methods) >= k:
        result, anomalous = "anomalous", True
    elif status == "succeeded":
        result, anomalous = "normal", False
    else:
        result, anomalous = "undetermined", None
    return {"n": len(methods), "k": k, "result": result, "status": status, "anomalous": anomalous}


def _grade(categories: Mapping[str, Mapping[str, Any]], anomalous: bool | None) -> str:
    if categories["hard"]["result"] == "anomalous":
        return "anomaly"
    if anomalous is True:
        return "anomaly_sign"
    return "normal" if anomalous is False else "undetermined"


def _risk(anomalous: bool | None, basis: list[Mapping[str, Any]]) -> dict[str, Any]:
    risk = {"policy": RISK_POLICY, "levelTable": RISK_LEVEL_TABLE}
    if anomalous is not True:
        return {**risk, "status": "not_applicable", "level": None, "determinedBy": [], "unratedMethods": []}
    rated = [method for method in basis if method["riskLevel"] is not None]
    unrated = [method["methodId"] for method in basis if method["riskLevel"] is None]
    if not rated:
        return {**risk, "status": "unrated", "level": None, "determinedBy": [], "unratedMethods": unrated}
    level = max((method["riskLevel"] for method in rated), key=RISK_RANK.__getitem__)
    return {**risk, "status": "rated", "level": level, "unratedMethods": unrated,
            "determinedBy": [method["methodId"] for method in rated if method["riskLevel"] == level]}


def _first_anomaly_ms(policy: PolicyV2, categories: Mapping[str, Mapping[str, Any]],
                      basis: list[Mapping[str, Any]]) -> float | None:
    """T 방법은 늘기만 하므로 대분류는 k번째 T가 도착한 순간 T가 된다."""
    moments = []
    for category in CATEGORIES:
        if categories[category]["result"] == "anomalous":
            arrivals = sorted(method["elapsedMs"] for method in basis if method["category"] == category)
            moments.append(arrivals[policy.k[category] - 1])
    return min(moments, default=None)


def decide(policy: PolicyV2, method_results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """활성 방법마다 결과 1개를 받아 methodResults·categoryResults·composite를 만든다."""
    methods = sorted(method_results, key=_rank)
    ensure_executable(policy, [method["methodId"] for method in methods])
    for method in methods:
        _check_elapsed(method)
    by_category = {category: [method for method in methods if method["category"] == category]
                   for category in CATEGORIES}
    categories = {category: _category_result(policy.k[category], by_category[category]) for category in CATEGORIES}
    active = [result for result in categories.values() if result["result"] != "skipped"]
    anomalous = True if any(result["anomalous"] is True for result in active) else \
        False if all(result["anomalous"] is False for result in active) else None
    complete = all(method["status"] == "succeeded" for method in methods)
    decided = any(method["status"] == "succeeded" for method in methods) or anomalous is True
    # k 미달 대분류의 T 방법은 근거가 아니다.
    basis = [method for method in methods
             if method["anomalous"] is True and categories[method["category"]]["result"] == "anomalous"]
    composite = {"policy": policy.combination(),
                 "status": "complete" if complete else "partial" if decided else "unavailable",
                 "anomalous": anomalous, "grade": _grade(categories, anomalous),
                 "basisMethods": [method["methodId"] for method in basis],
                 "firstAnomalyElapsedMs": _first_anomaly_ms(policy, categories, basis),
                 "risk": _risk(anomalous, basis)}
    return {"methodResults": methods, "categoryResults": categories, "composite": composite}


def decision_columns(decided: Mapping[str, Any]) -> dict[str, Any]:
    """decide 결과나 evidence에서 run_process_decisions 열 값을 뽑는다."""
    methods = decided["methodResults"]
    ml = [method for method in methods if method["category"] == "ml"]
    first_hit = next((method for method in methods if method["anomalous"] is True), None)
    return {"status": decided["composite"]["status"],
            "rule_status": component_status([method for method in methods if method["category"] != "ml"]),
            "ml_status": component_status(ml),
            "decision_code": first_hit["code"] if first_hit else None,
            "anomaly_score": ml[0].get("score") if len(ml) == 1 else None}
