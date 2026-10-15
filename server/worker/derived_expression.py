"""피처 v5의 Process 행 계산 AST를 실행한다. 임의 Python 실행은 없다."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

TABLE = "EC_PROCESS_MONITORING"
FIELDS = {"SUCCESS_COUNT": ("success_count", "number"), "ERROR_COUNT": ("error_count", "number"),
          "TOTAL_COUNT": ("total_count", "number"), "RETRY_COUNT": ("retry_count", "number"),
          "START_TIME": ("start_time_raw", "timestamp"), "END_TIME": ("end_time_raw", "timestamp")}
BINARY = {"subtract", "divide", "eq", "lt", "lte", "gt", "gte", "and", "datetime_diff"}
FACTORS = {"s": Decimal(1), "ms": Decimal("0.001"), "us": Decimal("0.000001")}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def validate(ast: Any, depth=0, budget=None) -> str:
    budget = [0] if budget is None else budget
    budget[0] += 1
    if not isinstance(ast, dict) or depth > 8 or budget[0] > 64:
        raise ValueError("invalid_derived_expression")
    op = ast.get("op")
    if op == "column" and set(ast) == {"op", "table", "column"}:
        if ast["table"] != TABLE or ast["column"] not in FIELDS:
            raise ValueError("unsupported_derived_source")
        return FIELDS[ast["column"]][1]
    if op == "literal" and set(ast) == {"op", "value"}:
        if isinstance(ast["value"], bool):
            return "boolean"
        if number(ast["value"]) is None or isinstance(ast["value"], str):
            raise ValueError("invalid_derived_literal")
        return "number"
    if op not in BINARY | {"is_missing", "convert_unit"}:
        raise ValueError("unsupported_derived_operation")
    extra = {"unit"} if op == "datetime_diff" else {"from_unit", "to_unit"} if op == "convert_unit" else set()
    if set(ast) != {"op", "args"} | extra or not isinstance(ast["args"], list) or len(ast["args"]) != (2 if op in BINARY else 1):
        raise ValueError("invalid_derived_expression")
    types = [validate(child, depth + 1, budget) for child in ast["args"]]
    if op == "is_missing":
        return "boolean"
    if op == "datetime_diff":
        if types != ["timestamp", "timestamp"] or ast["unit"] not in FACTORS:
            raise ValueError("derived_type_or_unit_mismatch")
        return "number"
    if op == "and":
        if types != ["boolean", "boolean"]:
            raise ValueError("derived_type_mismatch")
        return "boolean"
    if op == "convert_unit":
        if types != ["number"] or ast["from_unit"] not in FACTORS or ast["to_unit"] not in FACTORS:
            raise ValueError("derived_type_or_unit_mismatch")
        return "number"
    if types != ["number", "number"]:
        raise ValueError("derived_type_mismatch")
    return "number" if op in {"subtract", "divide"} else "boolean"


def evaluate(ast, row):
    op = ast["op"]
    if op == "literal":
        return ast["value"] if isinstance(ast["value"], bool) else number(ast["value"])
    if op == "column":
        key, kind = FIELDS[ast["column"]]
        value = row.get(key)
        if kind == "number":
            return number(value)
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    values = [evaluate(child, row) for child in ast["args"]]
    if op == "is_missing":
        return values[0] is None
    if op == "and":
        return False if False in values else None if None in values else True
    if any(value is None for value in values):
        return None
    if op == "datetime_diff":
        try:
            delta = values[0] - values[1]
            seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
            return seconds / FACTORS[ast["unit"]]
        except TypeError:
            return None
    if op == "convert_unit":
        return values[0] * FACTORS[ast["from_unit"]] / FACTORS[ast["to_unit"]]
    left, right = values
    if op == "subtract":
        return left - right
    if op == "divide":
        return left / right if right != 0 else None
    return {"eq": left == right, "lt": left < right, "lte": left <= right,
            "gt": left > right, "gte": left >= right}[op]


def unit(ast):
    op = ast["op"]
    if op == "column":
        return "count" if FIELDS[ast["column"]][1] == "number" else None
    if op == "literal":
        return "boolean" if isinstance(ast["value"], bool) else None
    if op in {"is_missing", "eq", "lt", "lte", "gt", "gte", "and"}:
        return "boolean"
    if op == "datetime_diff":
        return ast["unit"]
    if op == "convert_unit":
        if unit(ast["args"][0]) != ast["from_unit"]:
            raise ValueError("derived_unit_mismatch")
        return ast["to_unit"]
    left, right = [unit(child) for child in ast["args"]]
    if op == "subtract":
        if left != right:
            raise ValueError("derived_unit_mismatch")
        return left
    return "ratio" if left == right and left is not None else None
