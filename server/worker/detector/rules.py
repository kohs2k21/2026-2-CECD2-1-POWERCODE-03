"""저장된 제한적 Rule DSL을 원천 PROCESS 관측에 적용한다."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

NUMERIC = frozenset({"P.success_count", "P.error_count", "P.total_count", "P.retry_count"})
TEXT = frozenset({"P.status", "P.response_code", "P.adapter_type", "P.channel_id",
                  "P.process_hub_id", "P.process_id"})
FEATURE_FIELD = re.compile(r"^F\.([a-z][a-z0-9_]{1,63})$")


def _condition(spec: Mapping[str, Any], values: Mapping[str, Any],
               features: Mapping[str, Any], depth: int = 0) -> bool | None:
    if depth > 3 or not isinstance(spec, dict):
        raise ValueError("invalid_rule_condition")
    if set(spec) == {"all"} or set(spec) == {"any"}:
        op = "all" if "all" in spec else "any"
        children = spec[op]
        if not isinstance(children, list) or not 1 <= len(children) <= 8:
            raise ValueError("invalid_rule_condition")
        results = [_condition(child, values, features, depth + 1) for child in children]
        if op == "all":
            return False if False in results else None if None in results else True
        return True if True in results else None if None in results else False
    if set(spec) != {"field", "operator", "value"}:
        raise ValueError("invalid_rule_condition")
    field = spec["field"]
    operator = spec["operator"]
    feature_match = FEATURE_FIELD.fullmatch(field) if isinstance(field, str) else None
    if field not in NUMERIC | TEXT and not feature_match:
        raise ValueError("unsupported_rule_field")
    actual = features.get(feature_match.group(1)) if feature_match else values.get(field.split(".", 1)[1])
    if actual is None:
        return None
    if field in NUMERIC or feature_match:
        if operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
            raise ValueError("invalid_rule_operator")
        try:
            left, right = Decimal(str(actual)), Decimal(str(spec["value"]))
        except (InvalidOperation, ValueError, KeyError) as exc:
            raise ValueError("invalid_rule_numeric") from exc
        if not left.is_finite() or not right.is_finite():
            raise ValueError("invalid_rule_numeric")
        return {"eq": left == right, "ne": left != right, "gt": left > right,
                "gte": left >= right, "lt": left < right, "lte": left <= right}[operator]
    if not isinstance(actual, str):
        return None
    if operator == "in" and isinstance(spec["value"], list):
        return actual in spec["value"]
    if operator == "eq" and isinstance(spec["value"], str):
        return actual == spec["value"]
    if operator == "ne" and isinstance(spec["value"], str):
        return actual != spec["value"]
    raise ValueError("invalid_rule_operator")


def evaluate_rules(rules: list[tuple[str, int, dict[str, Any]]], values: Mapping[str, Any],
                   features: Mapping[str, Any] | None = None) -> tuple[str, str | None, list[dict[str, Any]]]:
    """미확인 입력이 있으면 전체 Rule 정상 판정으로 바꾸지 않는다."""
    evidence: list[dict[str, Any]] = []
    any_unavailable = False
    decision_code: str | None = None
    for key, version, spec in rules:
        if spec.get("schemaVersion") != 1 or spec.get("state") == "retired":
            raise ValueError("invalid_active_rule")
        hit = _condition(spec.get("condition"), values, features or {})
        evidence.append({"ruleKey": key, "version": version,
                         "result": "unavailable" if hit is None else "hit" if hit else "no_hit"})
        if hit is None:
            any_unavailable = True
        elif hit and decision_code is None:
            outcome = spec.get("outcome")
            if not isinstance(outcome, dict) or not isinstance(outcome.get("decisionCode"), str):
                raise ValueError("invalid_rule_outcome")
            decision_code = outcome["decisionCode"]
    status = "not_ready" if any_unavailable else "succeeded"
    return status, decision_code, evidence
