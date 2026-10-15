"""Rule 추천 모델 출력을 저장하기 전에 안전한 초안 형식으로 제한한다."""

from __future__ import annotations

import re
from typing import Any

NUMERIC = frozenset({"P.success_count", "P.error_count", "P.total_count", "P.retry_count"})
TEXT = frozenset({"P.status", "P.response_code", "P.adapter_type", "P.channel_id",
                  "P.process_hub_id", "P.process_id"})
KEY = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
FEATURE_FIELD = re.compile(r"^F\.([a-z][a-z0-9_]{1,63})$")


def _condition(value: Any, allowed_features: set[str], depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict) or depth > 3:
        raise ValueError("invalid_recommended_rule")
    if set(value) in ({"all"}, {"any"}):
        operator = "all" if "all" in value else "any"
        children = value[operator]
        if not isinstance(children, list) or not 1 <= len(children) <= 8:
            raise ValueError("invalid_recommended_rule")
        return {operator: [_condition(child, allowed_features, depth + 1) for child in children]}
    if set(value) != {"field", "operator", "value"}:
        raise ValueError("invalid_recommended_rule")
    field, operator, operand = value["field"], value["operator"], value["value"]
    if not isinstance(field, str) or not isinstance(operator, str):
        raise ValueError("invalid_recommended_rule")
    feature_match = FEATURE_FIELD.fullmatch(field)
    if feature_match and feature_match.group(1) not in allowed_features:
        raise ValueError("unsupported_recommended_feature")
    if field in NUMERIC or feature_match:
        if operator not in {"eq", "ne", "gt", "gte", "lt", "lte"} or \
                not isinstance(operand, str) or not DECIMAL.fullmatch(operand):
            raise ValueError("invalid_recommended_rule")
    elif field in TEXT:
        if operator == "in":
            if not isinstance(operand, list) or not 1 <= len(operand) <= 20 or \
                    not all(isinstance(item, str) and 0 < len(item) <= 128 for item in operand):
                raise ValueError("invalid_recommended_rule")
        elif operator not in {"eq", "ne"} or not isinstance(operand, str) or not 0 < len(operand) <= 128:
            raise ValueError("invalid_recommended_rule")
    else:
        raise ValueError("unsupported_recommended_rule_field")
    return {"field": field, "operator": operator, "value": operand}


def validate_suggestions(value: Any, allowed_feature_names: list[str] | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("invalid_rule_recommendations")
    keys: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not {"ruleKey", "displayName", "condition", "outcome"}.issubset(item) or set(item) - {"ruleKey", "displayName", "condition", "outcome", "evidenceSpec"}:
            raise ValueError("invalid_recommended_rule")
        key, name, outcome = item["ruleKey"], item["displayName"], item["outcome"]
        if not isinstance(key, str) or not KEY.fullmatch(key) or key in keys or \
                not isinstance(name, str) or not 0 < len(name) <= 120 or \
                not isinstance(outcome, dict) or set(outcome) != {"decisionCode", "severity"} or \
                not isinstance(outcome["decisionCode"], str) or not CODE.fullmatch(outcome["decisionCode"]) or \
                not isinstance(outcome["severity"], str) or outcome["severity"] not in {"info", "warning", "critical"}:
            raise ValueError("invalid_recommended_rule")
        keys.add(key)
        result.append({"ruleKey": key, "displayName": name,
                       "condition": _condition(item["condition"], set(allowed_feature_names or [])), "outcome": outcome,
                       **({"evidenceSpec": item["evidenceSpec"]} if isinstance(item.get("evidenceSpec"), dict) else {})})
    return result
