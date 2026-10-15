"""Code-owned trigger orientation and bounded truth checks.

No model, detector, arbitrary Python/SQL or original dataset is executed here.
Current output ASTs retain computational2.2 operations. Standalone truth
checks additionally support NOT/OR, which are explicitly not output operators.
The row-value evaluator is pluggable for a future separately versioned trigger
measurement path; its synthetic truth table is never source measurement proof.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import math
from pathlib import Path

from generalized_content_policy_v16 import assert_profile_admission
from generalized_measurement_v19 import digest, validate_plan, _UNITS
from generalized_roles_v19 import verify_registered_evidence
from generalized_rule_subjects_v19 import _resolve_subject, _context, _business_contract

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
POLICY_SCOPE = "true_is_trigger_and_separate_cannot_calculate"
OUTPUT_OPERATORS = frozenset({"column", "variable", "literal", "is_missing", "eq", "lt", "lte", "gt", "gte",
    "and", "subtract", "divide", "datetime_diff", "convert_unit", "child_count"})
STANDALONE_ONLY_OPERATORS = frozenset({"not", "or"})
MAX_NODES, MAX_DEPTH, MAX_CASES = 128, 16, 128


def trigger_contract(role):
    if role not in {"HR", "SR"}:
        raise ValueError("Trigger meaning is defined for HR/SR only")
    return {"version": VERSION, "owner": "verified_code", "role": role,
        "true_state": "confirmed_constraint_violation" if role == "HR" else "diagnostic_trigger",
        "false_state": "not_triggered", "cannot_calculate_state": "cannot_calculate",
        "orientation": "true_is_trigger", "unknown_is_false": False,
        "and_policy": "propagate_any_unknown_compatible_with_computational2.2",
        "standalone_not_or_policy": "three_valued_non_output_controls"}


def _error(code, path, message):
    return {"code": code, "path": path, "message": message}


def _shape_errors(expression, *, output_only=False):
    errors, count = [], [0]
    fields = {"column": {"op", "table", "column"}, "variable": {"op", "id"},
        "literal": {"op", "value", "logical_type"}, "child_count": {"op", "relationship_id"},
        "datetime_diff": {"op", "args", "unit"}, "convert_unit": {"op", "args", "from_unit", "to_unit"}}
    unary = {"is_missing", "not", "convert_unit"}
    def walk(node, path, depth):
        count[0] += 1
        if depth > MAX_DEPTH or count[0] > MAX_NODES:
            errors.append(_error("ast_limit", path, "Truth evaluation requires a bounded AST"))
            return
        if not isinstance(node, dict):
            errors.append(_error("ast_object", path, "Every calculation node must be an AST object"))
            return
        op = node.get("op")
        if not isinstance(op, str) or op not in OUTPUT_OPERATORS | STANDALONE_ONLY_OPERATORS:
            errors.append(_error("unsupported_operator", path + ".op", "Operator is not in the closed truth evaluator"))
            return
        if output_only and op in STANDALONE_ONLY_OPERATORS:
            errors.append(_error("standalone_only_operator", path + ".op", "NOT/OR are truth-table controls, not computational2.2 output operators"))
        allowed = fields.get(op, {"op", "args"})
        required = allowed - {"logical_type"}
        for key in sorted(required - set(node)):
            errors.append(_error("required_ast_field", path + "." + key, "Operator field is required"))
        for key in sorted(set(node) - allowed):
            errors.append(_error("ast_extra_field", path + "." + str(key), "AST fields cannot carry executable text"))
        if op in {"column", "variable", "child_count"}:
            for key in required - {"op"}:
                if not isinstance(node.get(key), str) or not node.get(key):
                    errors.append(_error("ast_reference", path + "." + key, "References require exact nonempty identifiers"))
        elif op == "literal":
            value = node.get("value")
            if not (type(value) in {bool, int} or isinstance(value, str) or type(value) is float and math.isfinite(value)):
                errors.append(_error("literal_type", path + ".value", "Only finite scalar literals are supported; null is not an unknown-value literal"))
            if "logical_type" in node and node["logical_type"] != "datetime":
                errors.append(_error("literal_logical_type", path + ".logical_type", "Only explicit datetime literal typing is supported"))
        else:
            args = node.get("args")
            if not isinstance(args, list) or len(args) != (1 if op in unary else 2):
                errors.append(_error("ast_arity", path + ".args", "Unary/binary operands must have their exact required arity"))
            if isinstance(args, list):
                for index, child in enumerate(args[:MAX_NODES + 1]):
                    walk(child, f"{path}.args[{index}]", depth + 1)
            if op == "datetime_diff" and node.get("unit") != "ms":
                errors.append(_error("datetime_unit", path + ".unit", "Datetime coordinate differences require explicit ms"))
            if op == "convert_unit" and any(not isinstance(node.get(key), str) for key in ("from_unit", "to_unit")):
                errors.append(_error("unit_conversion", path, "Conversion units must be explicit registered names"))
    walk(expression, "condition", 0)
    return errors


def validate_measurement_semantics(expression):
    """Gate output expressions whose current evaluator conflates unavailable.

The frozen evaluator tests lexical missingness exactly for raw columns. An
is_missing over arithmetic, counts or another derived expression instead sees
None for both missing and failed computation. Keep those proposals for review
until a separately versioned measurement engine can preserve their states.
"""
    errors = _shape_errors(expression, output_only=True)
    if not errors:
        def walk(node, path):
            if node["op"] == "is_missing" and node["args"][0]["op"] != "column":
                errors.append(_error("computed_unavailability_is_not_missing", path + ".args[0]",
                    "Current computational2.2 cannot replay is_missing over a computed operand without conflating unavailable with observed missing; retain for review"))
            for index, child in enumerate(node.get("args", [])):
                walk(child, f"{path}.args[{index}]")
        walk(expression, "condition")
    return {"version": VERSION, "passed": not errors, "errors": errors,
            "policy": "preserve_current_measurement_and_unknown_states", "expression_rewritten": False}


@dataclass(frozen=True)
class _Value:
    status: str
    value: object = None
    reasons: tuple = ()


def _unknown(reason, *, invalid=False):
    return _Value("invalid" if invalid else "cannot_calculate", None, (reason,))


def _number(value):
    if type(value) not in {int, float, Decimal}:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def _timestamp(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve(node, resolver):
    op = node["op"]
    key = ((node["table"], node["column"]) if op == "column" else
           ("variable", node["id"]) if op == "variable" else ("relationship", node["relationship_id"]))
    absent = object()
    if callable(resolver):
        try:
            raw = resolver(copy.deepcopy(node))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            return _unknown("input_resolver_unavailable")
    elif isinstance(resolver, dict):
        raw = resolver.get(key, absent)
    elif isinstance(resolver, list):
        fields = ("table", "column") if op == "column" else ("id",) if op == "variable" else ("relationship_id",)
        matches = [item for item in resolver if isinstance(item, dict) and item.get("op") == op
                   and all(item.get(field) == node[field] for field in fields)]
        if len(matches) > 1:
            return _unknown("duplicate_input_binding", invalid=True)
        raw = {key: value for key, value in matches[0].items() if key not in {"op", *fields}} if matches else absent
    else:
        return _unknown("resolver_shape", invalid=True)
    if raw is absent:
        return _unknown("input_not_provided")
    if isinstance(raw, _Value):
        return raw
    status, reasons = "known", ()
    if isinstance(raw, dict):
        status = raw.get("status", "known")
        if status in {"cannot_calculate", "unavailable"}:
            return _unknown(raw.get("reason") or "input_unavailable")
        if status == "missing":
            return _Value("missing", None, (raw.get("reason") or "observed_input_missing",))
        if status != "known" or "value" not in raw:
            return _unknown("input_value_shape", invalid=True)
        reasons = tuple(raw.get("reasons", [])) if isinstance(raw.get("reasons", []), list) else ()
        raw = raw["value"]
    if raw is None:
        return (_Value("missing", None, ("observed_input_missing",)) if op == "column" else _unknown("derived_value_unavailable"))
    if not (type(raw) in {bool, int} or isinstance(raw, (str, datetime)) or _number(raw) is not None):
        return _unknown("input_type_or_finiteness", invalid=True)
    if op == "child_count" and (type(raw) is not int or raw < 0):
        return _unknown("child_count_type", invalid=True)
    return _Value("known", raw, reasons)


def evaluate_ast_value(expression, resolver):
    """Safely evaluate a typed AST using code-provided scalar leaf values.

The resolver is a trusted callable, tuple-keyed mapping or JSON-friendly list
of {op,...reference fields,status,value}. Raw missing and unavailable derived
values differ. Native Decimal/datetime values stay native for row adapters.
This function does not verify units, lineage or source: validate them first.
"""
    shape = _shape_errors(expression)
    if shape:
        return {"status": "invalid", "value": None, "reasons": [error["code"] for error in shape], "errors": shape}
    def run(node):
        op = node["op"]
        if op in {"column", "variable", "child_count"}:
            return _resolve(node, resolver)
        if op == "literal":
            value = _timestamp(node["value"]) if node.get("logical_type") == "datetime" else node["value"]
            return _unknown("literal_datetime", invalid=True) if value is None else _Value("known", value)
        args = [run(child) for child in node["args"]]
        reasons = tuple(dict.fromkeys(reason for arg in args for reason in arg.reasons))
        if any(arg.status == "invalid" for arg in args):
            return _Value("invalid", None, reasons)
        if op == "is_missing":
            if args[0].status == "cannot_calculate":
                return args[0]
            return _Value("known", args[0].status == "missing", reasons)
        if op in {"and", "or", "not"}:
            if any(arg.status == "known" and type(arg.value) is not bool for arg in args):
                return _unknown("boolean_type", invalid=True)
            values = [arg.value if arg.status == "known" else None for arg in args]
            if op == "not":
                result = None if values[0] is None else not values[0]
            elif op == "and":
                # Match the current statistical backend, including
                # False AND unknown = unknown. No old proof is reinterpreted.
                result = None if None in values else values[0] and values[1]
            else:
                result = True if True in values else False if values == [False, False] else None
            return _Value("known", result, reasons) if result is not None else _Value("cannot_calculate", None, reasons or ("boolean_operand_unknown",))
        if any(arg.status != "known" for arg in args):
            return _Value("cannot_calculate", None, reasons or ("input_unavailable",))
        left, right = args[0].value, args[1].value if len(args) == 2 else None
        if op in {"subtract", "divide"}:
            a, b = _number(left), _number(right)
            if a is None or b is None:
                return _unknown("numeric_type", invalid=True)
            if op == "divide" and b <= 0:
                return _unknown("zero_denominator" if b == 0 else "non_positive_denominator")
            return _Value("known", a - b if op == "subtract" else a / b, reasons)
        if op in {"eq", "lt", "lte", "gt", "gte"}:
            a, b = _number(left), _number(right)
            if a is not None and b is not None:
                left, right = a, b
            elif type(left) is not type(right):
                return _unknown("comparison_type", invalid=True)
            if isinstance(left, datetime) and (left.tzinfo is None) != (right.tzinfo is None):
                return _unknown("timestamp_timezone_mismatch")
            if op != "eq" and not (a is not None and b is not None or isinstance(left, datetime)):
                return _unknown("ordered_comparison_type", invalid=True)
            result = {"eq": lambda: left == right, "lt": lambda: left < right, "lte": lambda: left <= right,
                      "gt": lambda: left > right, "gte": lambda: left >= right}[op]()
            return _Value("known", result, reasons)
        if op == "convert_unit":
            old, new, numeric = node["from_unit"], node["to_unit"], _number(left)
            if numeric is None or old not in _UNITS or new not in _UNITS or _UNITS[old][0] != _UNITS[new][0]:
                return _unknown("unit_conversion", invalid=True)
            return _Value("known", numeric * _UNITS[old][1] / _UNITS[new][1], reasons)
        if op == "datetime_diff":
            a, b = _timestamp(left), _timestamp(right)
            if a is None or b is None:
                return _unknown("datetime_type", invalid=True)
            if (a.tzinfo is None) != (b.tzinfo is None):
                return _unknown("timestamp_timezone_mismatch")
            delta = a - b
            return _Value("known", Decimal(delta.days * 86400000 + delta.seconds * 1000) + Decimal(delta.microseconds) / Decimal(1000), reasons)
        return _unknown("unsupported_operator", invalid=True)
    with localcontext() as context:
        context.prec = 50
        value = run(expression)
    return {"status": value.status, "value": value.value, "reasons": list(value.reasons), "errors": []}


def evaluate_trigger_ast(expression, resolver, *, role="SR"):
    contract = trigger_contract(role)
    value = evaluate_ast_value(expression, resolver)
    boolean = value["value"] if value["status"] == "known" and type(value["value"]) is bool else None
    reasons = list(value["reasons"])
    errors = list(value["errors"])
    if value["status"] == "known" and boolean is None:
        reasons.append("condition_not_boolean")
        errors.append(_error("condition_not_boolean", "condition", "A trigger result must be boolean"))
    elif value["status"] == "invalid" and not errors:
        errors.append(_error("invalid_typed_expression", "condition", ",".join(reasons)))
    return {"version": VERSION, "contract": contract, "calculation_status": "calculated" if boolean is not None else "cannot_calculate",
        "value": boolean, "trigger_state": contract["true_state"] if boolean is True else contract["false_state"] if boolean is False else "cannot_calculate",
        "reasons": reasons, "errors": errors, "source_measurement_proof": False,
        "standalone_only_operators": sorted(STANDALONE_ONLY_OPERATORS)}


def _missing_orientation(node):
    """Recognize only structurally proved equivalent/reversed missing tests."""
    if not isinstance(node, dict):
        return None
    op, args = node.get("op"), node.get("args", [])
    if op == "is_missing" and len(args) == 1 and args[0].get("op") == "column":
        return args[0], 1
    if op == "not" and len(args) == 1:
        child = _missing_orientation(args[0])
        return (child[0], -child[1]) if child else None
    if op == "eq" and len(args) == 2:
        for index in (0, 1):
            literal = args[index]
            if literal.get("op") == "literal" and type(literal.get("value")) is bool:
                child = _missing_orientation(args[1 - index])
                return (child[0], child[1] if literal["value"] else -child[1]) if child else None
    return None


def _expanded_condition(rule, item):
    # The original plan was already bound by _resolve_subject; its static
    # identity supplies the real lineage without guessing from an ID/name.
    allowed = {(ref["table"], ref["column"]) for ref in item["lineage"]}
    errors, captured = [], [False]
    source = rule["input"]
    def walk(node, path):
        if node["op"] == "variable":
            if source["kind"] != "variable" or node["id"] != source["variable_id"]:
                errors.append(_error("rule_variable_scope", path, "A trigger cannot capture another variable"))
                return copy.deepcopy(node)
            captured[0] = True
            return copy.deepcopy(item["plan"]["expression"])
        if node["op"] == "column":
            if (node["table"], node["column"]) not in allowed:
                errors.append(_error("rule_column_scope", path, "The trigger must retain its bound input lineage"))
            else:
                captured[0] = True
        result = copy.deepcopy(node)
        if "args" in result:
            result["args"] = [walk(child, f"{path}.args[{index}]") for index, child in enumerate(node["args"])]
        return result
    result = walk(rule["condition"], "condition")
    if not captured[0]:
        errors.append(_error("rule_input_unused", "condition", "The trigger must evaluate its immutable bound input"))
    return result, errors


def validate_trigger_semantics(rule, inventory, registry, validation_report=None, *, project_root=ROOT):
    """Validate direction against actual declarations/confirmed business facts.

This is an additional gate, not a replacement for rule/type/unit/role validation.
Unconfirmed HR direction is deferred for review and never marked executable.
"""
    assert_profile_admission(inventory, project_root=project_root)
    role = rule.get("role") if isinstance(rule, dict) else None
    report = {"version": VERSION, "policy_scope": POLICY_SCOPE, "passed": False, "direction_status": "deferred",
        "executable": False, "review_required": True, "errors": [], "evidence_ids": [],
        "contract": trigger_contract(role) if role in {"HR", "SR"} else None,
        "original_condition": copy.deepcopy(rule.get("condition")) if isinstance(rule, dict) else None,
        "condition_rewritten": False, "source_measurement_proof": False,
        "current_output_operators": sorted(OUTPUT_OPERATORS), "standalone_only_operators": sorted(STANDALONE_ONLY_OPERATORS)}
    if role not in {"HR", "SR"}:
        report["errors"] = [_error("trigger_role", "role", "Only HR/SR have trigger contracts")]
        return report
    shape = _shape_errors(rule.get("condition"), output_only=True)
    if shape:
        report.update(errors=shape, direction_status="rejected")
        return report
    item, errors = _resolve_subject(rule.get("input"), inventory, registry)
    if errors:
        report.update(errors=errors, direction_status="rejected")
        return report
    checked = validate_plan(item["plan"], inventory)
    item = {**item, "lineage": checked["identity"]["source_columns"]}
    expression, errors = _expanded_condition(rule, item)
    errors += validate_measurement_semantics(expression)["errors"]
    report["condition_sha256"] = digest(rule["condition"])
    report["expanded_condition_sha256"] = digest(expression)
    report["input_definition_sha256"] = item["definition_sha256"]
    report["resolved_condition"] = expression
    predicate = copy.deepcopy(item["plan"])
    predicate.update(id="trigger_" + str(rule.get("id", "unlabelled")), purpose="constraint_check" if role == "HR" else "observational_diagnostic", expression=expression,
        unit={"name": "boolean", "status": "verified", "evidence_ids": []})
    predicate["missing_policy"] = {**predicate["missing_policy"], "input": "count_missing" if expression.get("op") == "is_missing" else "propagate"}
    predicate_check = validate_plan(predicate, inventory)
    errors += [copy.deepcopy(error) for error in predicate_check["errors"]]
    if predicate_check["inferred_type"] != "boolean":
        errors.append(_error("condition_not_boolean", "condition", "Trigger predicates must infer boolean"))
    if validation_report is not None:
        if (not isinstance(validation_report, dict) or validation_report.get("passed") is not True or
                validation_report.get("input_definition_sha256") != item["definition_sha256"] or
                (validation_report.get("predicate_plan") or {}).get("expression") != expression):
            errors.append(_error("base_rule_validation", "validation_report", "The rule/type/role report must bind this exact validated subject and resolved predicate"))
    if errors:
        report.update(errors=errors, direction_status="rejected")
        return report
    orientation = _missing_orientation(expression)
    confirmed = []
    if role == "HR" and orientation:
        column = orientation[0]
        actual = next((value for table in inventory["tables"] if table["id"] == column["table"]
                       for value in table["columns"] if value["name"] == column["column"]), None)
        if actual and actual.get("nullable") is False:
            for fact in inventory.get("evidence", []):
                if (fact.get("kind") == "schema_declaration" and fact.get("target") == {"table": column["table"], "column": column["column"]}
                        and isinstance(fact.get("value"), dict) and fact["value"].get("name") == column["column"]
                        and fact["value"].get("nullable") is False and verify_registered_evidence(fact, project_root=project_root)):
                    confirmed.append(fact["id"])
        if confirmed and orientation[1] < 0:
            report.update(direction_status="rejected", errors=[_error("inverted_constraint_trigger", "condition", "Confirmed NOT NULL violations are missing values; this condition triggers on normal values")], evidence_ids=confirmed)
            return report
    if role == "HR" and not confirmed:
        policy = rule.get("threshold_policy")
        if isinstance(policy, dict) and policy.get("kind") == "business_contract":
            okay, refs, reason = _business_contract(rule, inventory, item["plan"], expression, _context(item["plan"]), project_root=project_root)
            if okay:
                confirmed = refs
            else:
                report["required_evidence"] = ["source-bound confirmed business condition with violation_when_true=true"]
                report["reason"] = reason
    if role == "HR" and not confirmed:
        report["errors"] = [_error("unconfirmed_trigger_direction", "condition", "No exact declaration or independent confirmed violation_when_true contract establishes HR trigger direction")]
        report["required_evidence"] = ["confirmed constraint violation direction"]
        return report
    report.update(passed=True, direction_status="verified", executable=True, review_required=False, evidence_ids=confirmed)
    return report


def evaluate_truth_table(rule, cases, validation_report=None):
    """Check code-defined counterexamples; never label them observed evidence."""
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise ValueError("Truth tables require1..128 bounded code-defined cases")
    role = rule.get("role") if isinstance(rule, dict) else None
    contract = trigger_contract(role)
    rows = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("inputs"), list):
            raise ValueError("Each truth case requires explicit JSON-friendly input bindings")
        result = evaluate_trigger_ast(rule.get("condition"), case["inputs"], role=role)
        expected = case.get("expected_trigger_state")
        rows.append({"case_index": index, "label": case.get("label", str(index)), "kind": case.get("kind", "code_control"),
            "inputs": copy.deepcopy(case["inputs"]), "result": result,
            "expected_trigger_state": expected, "assertion_passed": expected == result["trigger_state"] if expected is not None else None})
    return {"version": VERSION, "contract": contract, "condition_sha256": digest(rule.get("condition")), "cases": rows,
        "passed": all(row["assertion_passed"] is not False and not row["result"]["errors"] for row in rows)
                  and (validation_report is None or validation_report.get("passed") is True),
        "origin": "code_defined_synthetic_truth_cases", "source_measurement_proof": False,
        "business_constraint_proof": False, "anomaly_training_or_inference": "not_run", "condition_rewritten": False}
