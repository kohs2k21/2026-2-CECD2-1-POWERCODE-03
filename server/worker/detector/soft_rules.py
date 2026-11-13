"""소프트룰(화면: 이상 징후 규칙) 1개를 기존 Rule DSL 숫자 비교로 실행한다."""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Mapping

from derived_expression import FIELDS, evaluate as evaluate_derived, validate as validate_derived
from detector.method_results import SEVERITIES, method_category, method_result, source_absent, unresolved_result
from detector.relation_graph import graph_value_absent
from detector.rules import evaluate_rules
from methods.registry import SOFT_RULE_FIELDS

METHOD_VERSION = "soft-threshold-v1"
OPERATORS = frozenset({"gte", "gt", "lte", "lt"})
MISSING_POLICIES = frozenset({"not_applicable", "unavailable"})
FIXED_DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def _fixed(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if value == 0 else text


def fixed_decimal(value: Any, *, integer: bool = False) -> str:
    """룰 세트의 number 임계값을 지수 표기 없는 문자열로 바꾼다(rule-set.md 변환 규칙)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_threshold")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("invalid_threshold")
    text = _fixed(number)
    if len(text) > 40 or not FIXED_DECIMAL.fullmatch(text) or (integer and "." in text):
        raise ValueError("invalid_threshold")
    return text


def _member(value: Any, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _ast_columns(ast: Mapping[str, Any]) -> list[str]:
    if ast["op"] == "column":
        return [ast["column"]]
    return [column for child in ast.get("args", ()) for column in _ast_columns(child)]


def _source_columns(field: Any) -> list[str] | None:
    """필드 정의가 실행 가능하면 값이 쓰는 원천 열을 돌려준다."""
    source = field.get("source") if isinstance(field, Mapping) else None
    if not isinstance(source, Mapping):
        return None
    if source.get("kind") == "column":
        column = source.get("column")
        known = isinstance(column, str) and column in FIELDS
        return [column] if known and source.get("ruleDslField") == "P." + FIELDS[column][0] else None
    if source.get("kind") == "derived":
        try:
            return _ast_columns(source["ast"]) if validate_derived(source.get("ast")) == "number" else None
        except (TypeError, ValueError):
            # op나 column 자리에 리스트가 오면 validate의 집합 검사가 TypeError를 낸다.
            return None
    return None


def _valid_definition(rule: Mapping[str, Any], field: Any, columns: list[str] | None) -> bool:
    if columns is None or field.get("fieldId") != rule.get("field") or field.get("unit") != rule.get("unit"):
        return False
    policy = field.get("sourceMissingPolicy", {})
    return _member(rule.get("operator"), OPERATORS) and _member(rule.get("severity"), SEVERITIES) and \
        isinstance(policy, Mapping) and all(column in columns and _member(value, MISSING_POLICIES)
                                            for column, value in policy.items())


def soft_rule_fields() -> dict[str, dict[str, Any]]:
    """카탈로그 softRuleFields 항목을 fieldId별로 돌려준다. 정본은 #111 레지스트리다."""
    return {item.field_id: item.to_catalog() for item in SOFT_RULE_FIELDS}


# scope 키 → 판정 PROCESS 열(relation-rules.md 4절). interfaceId는 표시용이라 대조하지 않는다.
SCOPE_COLUMNS = (("processId", "process_id"), ("adapterType", "adapter_type"), ("processHubId", "process_hub_id"))


def in_scope(rule: Mapping[str, Any], raw: Mapping[str, Any]) -> bool:
    """scope가 없으면 전역. 있으면 값이 있는 판정 키마다 판정 PROCESS 원래 값과 정확 일치(trim 없음).

    대조 값이 없음(graph_value_absent)이거나 문자열이 아니면 적용 안 함. 맞지 않는 규칙은 그 판정의
    methodResults·n에서 빠진다(n 부풀림 방지).
    """
    scope = rule.get("scope")
    if scope is None:
        return True
    for key, column in SCOPE_COLUMNS:
        expected = scope.get(key)
        if expected is None:
            continue
        actual = raw.get(column)
        if not isinstance(actual, str) or graph_value_absent(actual) or actual != expected:
            return False
    return True


def unresolved_soft_result(rule: Mapping[str, Any], cause: str, *, status: str = "not_ready",
                           failure_code: str | None = None) -> dict[str, Any]:
    """규칙을 평가하지 못한 경우. 시도에서 쓴 정의는 그대로 남긴다."""
    try:
        threshold = fixed_decimal(rule.get("threshold"))
    except ValueError:
        threshold = None
    return unresolved_result(rule["id"], METHOD_VERSION, cause, status=status, failure_code=failure_code,
                             observedValue=None,
                             condition={"field": rule.get("field"), "operator": rule.get("operator"),
                                        "threshold": threshold, "unit": rule.get("unit")})


def evaluate_soft_rule(rule: Mapping[str, Any], fields: Mapping[str, Mapping[str, Any]],
                       raw: Mapping[str, Any]) -> dict[str, Any]:
    """규칙 하나의 오류는 그 규칙의 결과로만 남긴다. fields는 fieldId별 카탈로그 softRuleFields 항목이다."""
    method_id = rule.get("id")
    if method_category(method_id) != "soft":
        raise ValueError(f"method_kind_not_allowed:{method_id}")
    key = method_id.split(":", 1)[1]
    code = "SOFT_" + key.upper().replace("-", "_")
    threshold = None

    def result(status, anomalous, observed=None, **outcome):
        return method_result(method_id, METHOD_VERSION, status, anomalous, observedValue=observed,
                             condition={"field": rule.get("field"), "operator": rule.get("operator"),
                                        "threshold": threshold, "unit": rule.get("unit")}, **outcome)

    field_id = rule.get("field")
    field = fields.get(field_id) if isinstance(field_id, str) else None
    columns = _source_columns(field)
    if not _valid_definition(rule, field, columns):
        return result("failed", None, cause="failure", failure_code="invalid_rule_definition")
    try:
        threshold = fixed_decimal(rule.get("threshold"), integer=field.get("valueType") == "integer")
    except ValueError:
        return result("failed", None, cause="failure", failure_code="invalid_threshold")

    # 다른 열이 함께 없어도 not_applicable 열의 누락을 먼저 본다.
    for column, policy in field.get("sourceMissingPolicy", {}).items():
        if policy == "not_applicable" and source_absent(raw.get(FIELDS[column][0])):
            return result("succeeded", False,
                          evidence=[{"result": "not_applicable", "reason": column.lower() + "_absent"}])
    if any(source_absent(raw.get(FIELDS[column][0])) for column in columns):
        return result("not_ready", None, cause="input_missing")

    source = field["source"]
    if source["kind"] == "column":
        dsl_field, features, actual = source["ruleDslField"], {}, raw.get(FIELDS[source["column"]][0])
    else:
        try:
            actual = evaluate_derived(source["ast"], raw)
        except (ArithmeticError, TypeError, ValueError):
            actual = None
        if actual is None:
            return result("not_ready", None, cause="feature_unavailable")
        dsl_field, features = "F." + field_id, {field_id: actual}
    spec = {"schemaVersion": 1, "condition": {"field": dsl_field, "operator": rule["operator"], "value": threshold},
            "outcome": {"decisionCode": code}}
    try:
        _, decided, _ = evaluate_rules([(key, 0, spec)], raw, features)
    except ValueError as exc:
        if str(exc) == "invalid_rule_numeric":
            return result("failed", None, cause="input_invalid")
        return result("failed", None, cause="failure", failure_code="invalid_rule_definition")
    return result("succeeded", decided is not None, _fixed(Decimal(str(actual))), code=code,
                  severity=rule["severity"])
