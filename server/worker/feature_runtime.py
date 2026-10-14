"""승인된 변수 정의의 제한된 수치 DSL 실행과 입력 manifest 계산."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from derived_expression import validate as validate_derived, evaluate as evaluate_derived, unit as derived_unit

NUMERIC_SOURCE_FIELDS = frozenset({
    "P.success_count", "P.error_count", "P.total_count", "P.retry_count",
})
FEATURE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class InvalidFeatureDefinition(ValueError):
    """안전한 변수 계산 계약을 벗어난 정의."""


def validate_definitions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """모델의 제안을 저장하기 전에 허용 필드·연산·개수를 검사한다."""
    if not isinstance(definitions, list) or not 1 <= len(definitions) <= 32:
        raise InvalidFeatureDefinition("invalid_feature_count")
    names: set[str] = set()
    for item in definitions:
        if not isinstance(item, dict) or set(item) != {"name", "expression"}:
            raise InvalidFeatureDefinition("invalid_feature_definition")
        name = item["name"]
        if not isinstance(name, str) or not FEATURE_NAME.fullmatch(name) or name in names:
            raise InvalidFeatureDefinition("invalid_feature_name")
        names.add(name)
        _validate_expression(item["expression"], 0)
    return definitions


def _validate_expression(expression: Any, depth: int) -> None:
    if not isinstance(expression, dict) or depth > 2:
        raise InvalidFeatureDefinition("invalid_expression")
    op = expression.get("op")
    if op == "derived" and depth == 0 and set(expression) == {"op", "ast", "outputType", "unit", "provenance"}:
        kind = validate_derived(expression["ast"])
        if kind not in {"number", "boolean"} or expression["outputType"] != kind or expression["unit"] not in {None, "count", "ratio", "boolean", "ms", "s", "us"}:
            raise InvalidFeatureDefinition("derived_type_or_unit_mismatch")
        if expression["unit"] != derived_unit(expression["ast"]):
            raise InvalidFeatureDefinition("derived_unit_mismatch")
        if not isinstance(expression["provenance"], dict):
            raise InvalidFeatureDefinition("invalid_derived_provenance")
        return
    if op == "field" and set(expression) == {"op", "field"}:
        if not isinstance(expression["field"], str) or expression["field"] not in NUMERIC_SOURCE_FIELDS:
            raise InvalidFeatureDefinition("unsupported_feature_field")
        return
    if op in {"add", "subtract", "ratio"} and set(expression) == {"op", "left", "right"}:
        _validate_expression(expression["left"], depth + 1)
        _validate_expression(expression["right"], depth + 1)
        return
    raise InvalidFeatureDefinition("unsupported_feature_operation")


def _number(raw: Any) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _evaluate(expression: dict[str, Any], row: Mapping[str, Any]) -> Decimal | None:
    if expression["op"] == "derived":
        value = evaluate_derived(expression["ast"], row)
        return Decimal(int(value)) if isinstance(value, bool) else value
    if expression["op"] == "field":
        return _number(row.get(expression["field"].split(".", 1)[1]))
    left = _evaluate(expression["left"], row)
    right = _evaluate(expression["right"], row)
    if left is None or right is None:
        return None
    if expression["op"] == "add":
        return left + right
    if expression["op"] == "subtract":
        return left - right
    if right == 0:
        return None
    return left / right


def calculate_features(definitions: list[dict[str, Any]], row: Mapping[str, Any]) -> tuple[dict[str, str | None], list[str]]:
    """결측값을 0으로 대체하지 않고 NULL과 결측 목록으로 보존한다."""
    validate_definitions(definitions)
    values: dict[str, str | None] = {}
    missing: list[str] = []
    for item in definitions:
        value = _evaluate(item["expression"], row)
        values[item["name"]] = str(value) if value is not None and value.is_finite() else None
        if values[item["name"]] is None:
            missing.append(item["name"])
    return values, missing


def input_manifest_hash(observation_id: str, content_hash: str, feature_set_hash: str) -> str:
    payload = json.dumps([observation_id, content_hash, feature_set_hash], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
