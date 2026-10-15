"""computational2.2.1: bounded statistics including child aggregation filters.

The model proposes plans; this module verifies a closed AST and computes only
statistics from SHA-checked project CSVs. It never runs SQL, eval, anomaly
models, or exports source rows/individual feature values. Candidate IDs are
labels and cannot grant eligibility. Old finite-catalog policies are untouched.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "2.2.1"
_NUMERIC = {"integer", "number"}
_PURPOSES = {"feature_calculation", "constraint_check", "observational_diagnostic", "baseline_comparison", "contract_review"}
_UNITS = {"s": ("time", Decimal(1)), "ms": ("time", Decimal("0.001")),
          "us": ("time", Decimal("0.000001")), "B": ("size", Decimal(1)),
          "byte": ("size", Decimal(1)), "KiB": ("size", Decimal(1024)),
          "MiB": ("size", Decimal(1048576))}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _tables(inventory):
    return {t["id"]: t for t in inventory.get("tables", [])}


def _columns(inventory):
    return {(t["id"], c["name"]): c for t in inventory.get("tables", []) for c in t.get("columns", [])}


def _type(column):
    return {"string": "category", "datetime": "timestamp"}.get(column.get("logical_type"), column.get("logical_type", "unknown"))


def _unit(column):
    value = column.get("unit", {})
    return value.get("name") if value.get("status") in {"declared", "verified"} else None


def validate_plan(plan, inventory, mapping=None):
    """Validate a plan without evaluating data; errors always name exact fields."""
    errors = []
    def fail(code, path, message, **details):
        errors.append({"code": code, "path": path, "message": message, **details})
    if not isinstance(plan, dict) or not isinstance(inventory, dict):
        return {"passed": False, "errors": [{"code": "object_required", "path": "$", "message": "Plan and inventory must be objects"}],
                "candidate_id": plan.get("id") if isinstance(plan, dict) else None,
                "proposal_sha256": _proposal_sha(plan), "definition_status": "invalid", "definition_sha256": None,
                "inferred_type": "unknown", "identity": None, "identity_sha256": None}
    tables, columns = _tables(inventory), _columns(inventory)
    relations = {r["id"]: r for r in inventory.get("relationships", [])}
    evidence = {e["id"]: e for e in inventory.get("evidence", [])}
    required = {"id", "purpose", "expression", "grain", "population", "group_by", "missing_policy", "unit", "schema_version", "source_version", "mapping_version", "plan_version"}
    for field in sorted(required - set(plan)):
        fail("required_field", field, "Required plan field is missing")
    if not isinstance(plan.get("id"), str) or not plan.get("id"):
        fail("candidate_id", "id", "An arbitrary nonempty lineage label is required")
    if not isinstance(plan.get("purpose"), str) or plan.get("purpose") not in _PURPOSES:
        fail("purpose", "purpose", "Unknown plan purpose")
    if plan.get("plan_version") != VERSION:
        fail("plan_version", "plan_version", "This validator accepts only plan 2.2.1")
    for field in ("schema_version", "source_version"):
        if plan.get(field) != inventory.get(field) or not isinstance(plan.get(field), str):
            fail("stale_version", field, "Plan must bind the exact current inventory version")
    expected_mapping = inventory.get("mapping_version") if mapping is None else mapping.get("mapping_version") if isinstance(mapping, dict) else None
    if mapping is not None and not isinstance(mapping, dict):
        fail("mapping_object", "mapping_version", "Reviewed mappings must be objects")
    if plan.get("mapping_version") != expected_mapping:
        fail("stale_mapping", "mapping_version", "Mapping version differs from the reviewed mapping")
    availability = plan.get("availability", {"query_visibility": "unknown", "required_event_phase": "unknown"})
    if availability != {"query_visibility": "unknown", "required_event_phase": "unknown"}:
        fail("availability_unverified", "availability", "This path has no source-bound operating availability contract; preserve unknown rather than infer a query/completion time")
    grain = plan.get("grain") if isinstance(plan.get("grain"), dict) else {}
    table = grain.get("table")
    if not isinstance(table, str):
        fail("table_type", "grain.table", "Physical table IDs must be strings")
        table = None
    if table not in tables:
        fail("unknown_table", "grain.table", "Unknown physical table")
    keys = grain.get("keys")
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        fail("grain_keys", "grain.keys", "Explicit unique analysis key columns are required")
        keys = []
    for index, key in enumerate(keys):
        if (table, key) not in columns or columns.get((table, key), {}).get("excluded"):
            fail("unknown_column", f"grain.keys[{index}]", "Analysis key is unknown or excluded")
    if table in tables and keys != tables[table].get("primary_key"):
        fail("grain_key_unverified", "grain.keys", "Current implementation requires the complete ordered declared primary key")
    population = plan.get("population") if isinstance(plan.get("population"), dict) else {}
    if population.get("table") != table:
        fail("population_grain", "population.table", "Output population must use the output grain table")
    if "filter" not in population:
        fail("required_field", "population.filter", "Population selection must be explicit, including null")
    missing = plan.get("missing_policy") if isinstance(plan.get("missing_policy"), dict) else {}
    for field in ("input", "empty_group", "denominator_zero"):
        if field not in missing:
            fail("required_field", "missing_policy." + field, "Missingness and denominator decisions must be explicit")
    if missing.get("input") not in ("propagate", "count_missing"):
        fail("missing_policy", "missing_policy.input", "Missing input must propagate or be an explicit missing predicate")
    if missing.get("empty_group") not in (None, "zero"):
        fail("empty_group", "missing_policy.empty_group", "Only null or measured empty child count zero is allowed")
    if missing.get("denominator_zero") not in (None, "defer"):
        fail("denominator_policy", "missing_policy.denominator_zero", "Zero denominator cannot be imputed")
    output_unit = plan.get("unit") if isinstance(plan.get("unit"), dict) else {}
    if "name" not in output_unit:
        fail("required_field", "unit.name", "Output unit must be explicit, including null")
    if output_unit.get("status") not in ("verified", "declared", "unknown") or not isinstance(output_unit.get("evidence_ids"), list):
        fail("unit_policy", "unit", "Unit requires status and evidence_ids")
    if output_unit.get("status") == "unknown" and output_unit.get("name") is not None:
        fail("unknown_unit", "unit.name", "Unknown units must retain a null name")
    if output_unit.get("status") in ("verified", "declared") and (not isinstance(output_unit.get("name"), str) or not output_unit.get("name")):
        fail("known_unit_name", "unit.name", "Known units require a nonempty explicit name")
    used, used_relations, operators, conversion_refs, nodes = set(), set(), set(), set(), [0]

    def walk(node, path, allowed_table=table, depth=0):
        nodes[0] += 1
        if depth > 16 or nodes[0] > 128:
            fail("ast_limit", path, "AST exceeds the bounded depth or node limit")
            return "unknown", None
        if not isinstance(node, dict):
            fail("ast_object", path, "AST nodes must be objects")
            return "unknown", None
        op = node.get("op")
        known_fields = {
            "column": {"op", "table", "column"}, "literal": {"op", "value", "logical_type"},
            "is_missing": {"op", "args"}, "subtract": {"op", "args"}, "divide": {"op", "args"},
            "eq": {"op", "args"}, "lt": {"op", "args"}, "lte": {"op", "args"}, "gt": {"op", "args"}, "gte": {"op", "args"}, "and": {"op", "args"},
            "convert_unit": {"op", "args", "from_unit", "to_unit"},
            "datetime_diff": {"op", "args", "unit"}, "child_count": {"op", "relationship_id"}}
        if "op" not in node:
            fail("required_ast_field", path + ".op", "Every AST child requires its own op; supported parent operations still require correctly structured child nodes")
            return "unknown", None
        if not isinstance(op, str) or op not in known_fields:
            fail("unsupported_operator", path + ".op", "Operator is not in the restricted statistical registry")
            return "unknown", None
        operators.add(op)
        for extra in set(node) - known_fields[op]:
            fail("ast_extra_field", path + "." + str(extra), "Unrecognized AST field cannot carry executable text")
        if op == "column":
            key = (node.get("table"), node.get("column"))
            if any(not isinstance(part, str) for part in key):
                fail("column_reference", path, "Physical column references require string table and column IDs")
                return "unknown", None
            column = columns.get(key)
            if not column or column.get("excluded"):
                fail("unknown_column", path, "Column is unknown or excluded from scope")
                return "unknown", None
            used.add(key)
            if key[0] != allowed_table:
                fail("cross_table_scalar", path + ".table", "Cross-table values require an explicit verified relationship operator")
            return _type(column), _unit(column)
        if op == "literal":
            value = node.get("value")
            if node.get("logical_type") == "datetime":
                if _timestamp(value) is None:
                    fail("literal_datetime", path + ".value", "Datetime literals require a parseable ISO timestamp")
                return "timestamp", None
            if node.get("logical_type") is not None:
                fail("literal_logical_type", path + ".logical_type", "Only explicit datetime literal typing is supported")
            dtype = "boolean" if type(value) is bool else "integer" if type(value) is int else "number" if type(value) is float and math.isfinite(value) else "category" if isinstance(value, str) else "unknown"
            if dtype == "unknown":
                fail("literal_type", path + ".value", "Only finite scalar string, number or boolean literals are supported")
            return dtype, "boolean" if dtype == "boolean" else "dimensionless" if dtype in _NUMERIC else "category"
        if op == "child_count":
            rid = node.get("relationship_id")
            relation = relations.get(rid) if isinstance(rid, str) else None
            if not relation:
                fail("unknown_relationship", path + ".relationship_id", "Relationship is not in the inventory")
                return "integer", "count"
            used_relations.add(relation["id"])
            if relation.get("status") not in {"declared", "measured"}:
                fail("unverified_relationship", path + ".relationship_id", "A proposed semantic relationship cannot be executed")
            if relation.get("to_table") != allowed_table or relation.get("to_columns") != keys:
                fail("relationship_grain", path, "Child aggregation must point to the complete current output parent key")
            child_table = relation.get("from_table")
            from_cols, to_cols = relation.get("from_columns"), relation.get("to_columns")
            if not isinstance(from_cols, list) or not isinstance(to_cols, list) or not from_cols or len(from_cols) != len(to_cols):
                fail("relationship_key", path, "Full ordered child and parent relationship keys are required")
            else:
                for left, right in zip(from_cols, to_cols):
                    ck, pk = (child_table, left), (allowed_table, right)
                    used.update([ck, pk])
                    if ck not in columns or pk not in columns or columns.get(ck, {}).get("excluded") or columns.get(pk, {}).get("excluded"):
                        fail("relationship_column", path, "Relationship keys must resolve to included columns")
                    elif _type(columns[ck]) != _type(columns[pk]):
                        fail("relationship_type", path, "Ordered parent and child key types must match")
            return "integer", "count"
        arguments = node.get("args")
        if not isinstance(arguments, list):
            fail("ast_args", path + ".args", "Operator arguments must be a list")
            arguments = []
        arity = 1 if op in {"is_missing", "convert_unit"} else 2
        if len(arguments) != arity:
            fail("ast_arity", path + ".args", "Wrong operator arity")
        inferred = [walk(arg, f"{path}.args[{i}]", allowed_table, depth + 1) for i, arg in enumerate(arguments)]
        types, units = [t for t, _ in inferred], [u for _, u in inferred]
        if op == "is_missing":
            return "boolean", "boolean"
        if op == "and":
            if types != ["boolean", "boolean"]:
                fail("boolean_type", path, "and requires two boolean predicates")
            return "boolean", "boolean"
        if op in {"subtract", "divide"}:
            if len(types) != 2 or any(t not in _NUMERIC for t in types):
                fail("numeric_type", path, "Arithmetic cannot reinterpret category identifiers as numbers")
            if len(units) == 2 and units[0] != units[1]:
                fail("incompatible_unit", path, "Arithmetic requires matching confirmed units")
            if len(units) == 2 and any(unit is None for unit in units):
                fail("unknown_arithmetic_unit", path, "Raw numeric statistics allow unknown units; arithmetic between columns requires confirmed compatible units")
            if op == "divide" and missing.get("denominator_zero") != "defer":
                fail("denominator_policy", "missing_policy.denominator_zero", "Division requires finite positive denominator, otherwise unavailable")
            return "integer" if op == "subtract" and types == ["integer", "integer"] else "number", units[0] if op == "subtract" and units else "ratio"
        if op in {"eq", "lt", "lte", "gt", "gte"}:
            if len(types) == 2 and types[0] != types[1] and not set(types) <= _NUMERIC:
                fail("comparison_type", path, "Comparison operands have incompatible types")
            if op != "eq" and any(t not in _NUMERIC | {"timestamp"} for t in types):
                fail("ordered_type", path, "Ordered comparisons require numeric or timestamp inputs")
            literal_operand = any(isinstance(arg, dict) and arg.get("op") == "literal" for arg in arguments)
            if len(units) == 2 and units[0] != units[1] and not literal_operand:
                fail("incompatible_unit", path, "Comparison operands must use the same unit")
            return "boolean", "boolean"
        if op == "datetime_diff":
            if types != ["timestamp", "timestamp"] or node.get("unit") != "ms":
                fail("datetime_type", path, "Datetime difference needs ordered timestamp inputs and explicit ms output")
            return "number", "ms"
        if op == "convert_unit":
            old, new = node.get("from_unit"), node.get("to_unit")
            if types not in [["integer"], ["number"]]:
                fail("numeric_type", path, "Unit conversion requires a numeric source")
            if not isinstance(old, str) or not isinstance(new, str) or old not in _UNITS or new not in _UNITS or _UNITS.get(old, (None,))[0] != _UNITS.get(new, (None,))[0]:
                fail("unit_conversion", path, "Only registered compatible unit conversions are supported")
            if not units or units[0] != old:
                fail("source_unit", path + ".from_unit", "Conversion unit must equal the source unit")
            refs = [(arg.get("table"), arg.get("column")) for arg in arguments if isinstance(arg, dict) and arg.get("op") == "column" and isinstance(arg.get("table"), str) and isinstance(arg.get("column"), str)]
            if len(refs) != 1 or columns.get(refs[0], {}).get("unit", {}).get("status") != "verified":
                fail("unit_unverified", path, "Conversion requires an exactly bound verified source-column unit")
            else:
                conversion_refs.add(refs[0])
                unit_refs = columns[refs[0]].get("unit", {}).get("evidence_ids")
                expected_target = {"table": refs[0][0], "column": refs[0][1]}
                expected_value = dict(expected_target, unit=old)
                def same_unit_target(target):
                    return isinstance(target, dict) and all(target.get(k) == v for k, v in expected_target.items()) and set(target) <= {"table", "column", "property"} and target.get("property", "unit") == "unit"
                if not unit_refs or any(eid not in evidence or evidence[eid].get("verified_by") != "code" or
                    evidence[eid].get("kind") not in {"business_document", "unit_contract"} or
                    not same_unit_target(evidence[eid].get("target")) or evidence[eid].get("value") != expected_value for eid in unit_refs):
                    fail("unit_evidence", path, "Verified units require exact physical-column, unit and current code-checked evidence")
            return "number", new
        return "unknown", None

    expression = plan.get("expression")
    if expression is None:
        inferred_type, inferred_unit = "unknown", None
        if plan.get("purpose") != "contract_review":
            fail("expression_required", "expression", "Computable purposes need an explicit AST; unknown contracts stay review-only")
    else:
        inferred_type, inferred_unit = walk(expression, "expression")
    def unit_status(node, depth=0):
        if not isinstance(node, dict) or depth > 16:
            return "unknown"
        op = node.get("op")
        if op in ("is_missing", "eq", "lt", "lte", "gt", "gte", "and", "child_count", "divide", "datetime_diff"):
            return "verified"
        if op == "column":
            key = (node.get("table"), node.get("column"))
            if any(not isinstance(part, str) for part in key):
                return "unknown"
            return columns.get(key, {}).get("unit", {}).get("status", "unknown")
        if op == "literal":
            return "verified"
        if op == "convert_unit":
            return "verified" if isinstance(node.get("args"), list) and len(node["args"]) == 1 and unit_status(node["args"][0], depth + 1) == "verified" else "unknown"
        if op == "subtract":
            statuses = [unit_status(arg, depth + 1) for arg in node.get("args", [])] if isinstance(node.get("args"), list) else []
            return "verified" if statuses and all(s == "verified" for s in statuses) else "declared" if statuses and all(s in ("verified", "declared") for s in statuses) else "unknown"
        return "unknown"
    if output_unit.get("status") == "verified" and unit_status(expression) != "verified":
        fail("unit_status_upgrade", "unit.status", "A proposed output cannot promote a declared or unknown source unit to verified")
    if missing.get("input") == "count_missing" and (not isinstance(expression, dict) or expression.get("op") != "is_missing"):
        fail("missing_predicate", "missing_policy.input", "count_missing belongs to a separate explicit is_missing candidate")
    if output_unit.get("name") != inferred_unit and not (output_unit.get("status") == "unknown" and inferred_unit is None):
        fail("output_unit", "unit.name", "Declared output unit differs from the exact AST unit")
    if population.get("filter") is not None:
        filter_type, _ = walk(population["filter"], "population.filter")
        if filter_type != "boolean":
            fail("population_filter", "population.filter", "Population selection must be a boolean AST")
    # computational2.2.1 covers expression AND selection before deciding the
    # code-owned empty child policy. This does not alter either original AST.
    if "child_count" in operators and missing.get("empty_group") != "zero":
        fail("empty_group_required_zero", "missing_policy.empty_group",
             "Actual child aggregation in expression or population filter requires empty-group zero",
             wrong_value=copy.deepcopy(missing.get("empty_group")), allowed_values=["zero"])
    if "child_count" not in operators and missing.get("empty_group") == "zero":
        fail("empty_group_zero", "missing_policy.empty_group",
             "Only an actual child aggregation in expression or population filter can equal zero",
             wrong_value=copy.deepcopy(missing.get("empty_group")), allowed_values=[None])
    groups = plan.get("group_by")
    if not isinstance(groups, list):
        fail("group_by", "group_by", "Grouping columns must be an explicit list")
        groups = []
    seen_groups = set()
    for i, group in enumerate(groups):
        if not isinstance(group, dict):
            fail("group_column", f"group_by[{i}]", "Grouping reference must be an object")
            continue
        key = (group.get("table"), group.get("column"))
        if any(not isinstance(part, str) for part in key):
            fail("group_column", f"group_by[{i}]", "Grouping requires string physical table and column IDs")
            continue
        if key not in columns or key[0] != table or columns.get(key, {}).get("excluded") or key in seen_groups:
            fail("group_column", f"group_by[{i}]", "Grouping must use unique included columns in the output table")
        seen_groups.add(key)
        used.add(key)
    source_files = sorted([copy.deepcopy(s) for s in inventory.get("sources", [])], key=lambda s: (s.get("path", ""), s.get("sha256", "")))
    identity = {"plan_version": VERSION, "schema_version": plan.get("schema_version"), "source_version": plan.get("source_version"),
                "inferred_type": inferred_type, "availability": copy.deepcopy(availability),
                "content_policy_version": inventory.get("content_policy", {}).get("version"),
                "admission_identity_sha256": inventory.get("scope_admission", {}).get("admission_identity_sha256"),
                "mapping_version": plan.get("mapping_version"), "purpose": plan.get("purpose"), "expression": copy.deepcopy(expression),
                "grain": copy.deepcopy(grain), "population": copy.deepcopy(population), "group_by": copy.deepcopy(groups),
                "missing_policy": copy.deepcopy(missing), "unit": copy.deepcopy(output_unit),
                "source_columns": [{"table": t, "column": c} for t, c in sorted(used)],
                "conversion_unit_columns": [{"table": t, "column": c} for t, c in sorted(conversion_refs)],
                "relationships": [{field: copy.deepcopy(relations[r].get(field)) for field in
                                  ("id", "from_table", "from_columns", "to_table", "to_columns", "status", "evidence_ids", "no_reference_values")}
                                  for r in sorted(used_relations)], "source_files": source_files,
                "deduplication": "none_preserve_source_rows", "measurement_kind": "bounded_statistics_only"}
    try:
        identity_sha = digest(identity)
    except (ValueError, TypeError):
        fail("non_json_identity", "$", "Plan identity must be finite canonical JSON")
        identity_sha = None
    return {"passed": not errors, "errors": errors, "inferred_type": inferred_type,
            "candidate_id": plan.get("id"), "proposal_sha256": _proposal_sha(plan),
            "definition_status": "invalid" if errors else "valid", "definition_sha256": None if errors else identity_sha,
            "identity": identity if not errors else None, "identity_sha256": identity_sha if not errors else None}


def _proposal_sha(plan):
    try:
        return digest(plan)
    except (TypeError, ValueError):
        return None


def _missing(value):
    return value is None or value == ""


def _number(value):
    if _missing(value) or type(value) is bool:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _timestamp(value):
    if _missing(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _json_number(number):
    if number is None:
        return None
    if isinstance(number, Decimal):
        return int(number) if number == number.to_integral_value() else float(number)
    return number


def _summary(values):
    valid = [v for v in values if v is not None]
    booleans = bool(valid) and all(type(v) is bool for v in valid)
    numeric = bool(valid) and all(isinstance(v, (Decimal, int, float)) and type(v) is not bool for v in valid)
    numbers = sorted(Decimal(str(v)) for v in valid) if numeric else []
    def percentile(p):
        if not numbers:
            return None
        position = Decimal(len(numbers) - 1) * Decimal(p)
        lower = int(position)
        upper = min(lower + 1, len(numbers) - 1)
        return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)
    true_count = sum(v is True for v in valid) if booleans else None
    denominator = len(valid) if booleans else None
    ratio = Decimal(true_count) / Decimal(denominator) if booleans and denominator else None
    p95, p99 = percentile("0.95"), percentile("0.99")
    result = {"row_count": len(values), "valid_count": len(valid), "missing_count": len(values) - len(valid),
              "true_count": true_count, "false_count": len(valid) - true_count if booleans else None,
              "ratio": _json_number(ratio), "numerator": true_count, "denominator": denominator,
              "min": _json_number(numbers[0]) if numbers else None, "max": _json_number(numbers[-1]) if numbers else None,
              "p95": _json_number(p95), "p99": _json_number(p99), "variation": "unknown" if not valid else "constant" if len(set(valid)) == 1 else "variable",
              "quantile_method": "linear_(n-1)*p", "exact_decimal": {"ratio": str(ratio) if ratio is not None else None,
              "p95": str(p95) if p95 is not None else None, "p99": str(p99) if p99 is not None else None}}
    return result


def _unmeasured_summary():
    return {key: None for key in ("row_count", "valid_count", "missing_count", "true_count", "false_count", "ratio", "numerator", "denominator", "min", "max", "p95", "p99", "variation")}


def _verify_unit_source(record, project_root):
    try:
        source = record["source"]
        root = Path(project_root).resolve()
        path = (root / source["path"]).resolve()
        path.relative_to(root)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            return False
        actual = json.loads(raw)
        pointer = source.get("pointer", "")
        if pointer and not pointer.startswith("/"):
            return False
        for part in pointer.lstrip("/").split("/") if pointer else []:
            part = part.replace("~1", "/").replace("~0", "~")
            actual = actual[int(part)] if isinstance(actual, list) else actual[part]
        return actual == record["value"]
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        return False


def measure_plan(plan, inventory, datasets=None, *, project_root=ROOT, max_rows=10000, definition_validation=None):
    """Compute exact bounded statistics only after static and source verification.

An optional datasets argument is accepted only if byte-verified CSV reads
produce the same canonical rows. This is never a path for model-made numbers.
"""
    checked = validate_plan(plan, inventory)
    base = {"validator_version": VERSION, "candidate_id": plan.get("id") if isinstance(plan, dict) else None,
            "proposal_sha256": checked["proposal_sha256"], "definition_status": checked["definition_status"],
            "definition_sha256": checked["definition_sha256"],
            "errors": checked["errors"], "identity": checked["identity"], "identity_sha256": checked["identity_sha256"],
            "summary": _unmeasured_summary(), "source_evidence": None, "groups": [], "anomaly_execution": "not_run", "row_values_exported": False}
    if definition_validation is not None:
        restriction_errors = []
        if not isinstance(definition_validation, dict):
            restriction_errors = [{"code": "definition_validation_shape", "path": "definition_validation", "message": "Outer definition validation must be an object"}]
        elif not definition_validation.get("passed"):
            supplied = definition_validation.get("errors")
            restriction_errors = copy.deepcopy(supplied) if isinstance(supplied, list) and supplied else [{"code": "outer_definition_invalid", "path": "definition_validation", "message": "Outer proposal contract did not validate"}]
        else:
            if definition_validation.get("proposal_sha256") != base["proposal_sha256"]:
                restriction_errors.append({"code": "proposal_validation_binding", "path": "definition_validation.proposal_sha256", "message": "Outer validation must bind this exact unchanged model proposal"})
            if definition_validation.get("candidate_id") != base["candidate_id"]:
                restriction_errors.append({"code": "definition_validation_id", "path": "definition_validation.candidate_id", "message": "Outer validation belongs to another variable ID"})
            supplied_sha = definition_validation.get("definition_sha256")
            if supplied_sha != checked["definition_sha256"]:
                restriction_errors.append({"code": "definition_validation_binding", "path": "definition_validation.definition_sha256", "message": "Outer validation must bind the current executable definition"})
        if restriction_errors:
            return dict(base, status="rejected", errors=restriction_errors,
                        definition_status="invalid", definition_sha256=None, identity=None, identity_sha256=None)
    if not checked["passed"]:
        return dict(base, status="rejected")
    try:
        from generalized_content_policy_v16 import assert_profile_admission
        assert_profile_admission(inventory, project_root=project_root)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        return dict(base, status="rejected", errors=[{"code": "content_admission", "path": "inventory.scope_admission", "message": str(exc)}])
    if plan.get("expression") is None:
        return dict(base, status="deferred", reason="contract_review_has_no_computable_expression")
    if type(max_rows) is not int or not 1 <= max_rows <= 100000:
        return dict(base, status="rejected", errors=[{"code": "row_limit", "path": "max_rows", "message": "Bounded row limit must be 1..100000"}])
    tables, columns = _tables(inventory), _columns(inventory)
    def column_missing(raw, table, column):
        return raw is None or raw in columns[(table, column)].get("missing_tokens", [""])
    relevant = {plan["grain"]["table"]} | {c["table"] for c in checked["identity"]["source_columns"]}
    if any(tables[t].get("csv_source") is None for t in relevant):
        return dict(base, status="deferred", reason="schema_only_no_observed_dataset")
    evidence = {e["id"]: e for e in inventory.get("evidence", [])}
    for ref in checked["identity"].get("conversion_unit_columns", []):
        unit = columns[(ref["table"], ref["column"])]["unit"]
        if any(not _verify_unit_source(evidence[eid], project_root) for eid in unit["evidence_ids"]):
            return dict(base, status="rejected", errors=[{"code": "unit_source_verification", "path": "expression", "message": "Unit facts do not match their current project source SHA and exact JSON pointer"}])
    try:
        from generalized_profile_v16 import read_dataset
        loaded = read_dataset(inventory, project_root=project_root, max_rows=max_rows)
        if datasets is not None and canonical(datasets) != canonical(loaded):
            return dict(base, status="rejected", errors=[{"code": "dataset_source_mismatch", "path": "datasets", "message": "Input rows differ from SHA-verified source CSVs"}])
        rows_by_table = loaded
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        return dict(base, status="rejected", errors=[{"code": "source_verification", "path": "inventory.sources", "message": str(exc)}])
    if any(len(rows_by_table.get(t, [])) > max_rows for t in relevant):
        return dict(base, status="rejected", errors=[{"code": "row_limit", "path": "datasets", "message": "Dataset exceeds declared finite measurement limit"}])
    relation_counts = {}
    for relation in checked["identity"]["relationships"]:
        parent_rows = rows_by_table.get(relation["to_table"], [])
        parent_keys = [tuple(row.get(c) for c in relation["to_columns"]) for row in parent_rows]
        if any(any(column_missing(v, relation["to_table"], c) for v, c in zip(key, relation["to_columns"])) for key in parent_keys) or len(set(parent_keys)) != len(parent_keys):
            return dict(base, status="deferred", errors=[{"code": "parent_key_ambiguous", "path": "grain.keys", "message": "Missing or duplicate parent keys prevent an unambiguous child aggregation"}], reason="relationship_parent_key_unverified")
        counts = Counter()
        for row in rows_by_table.get(relation["from_table"], []):
            key = tuple(row.get(c) for c in relation["from_columns"])
            if not any(column_missing(v, relation["from_table"], c) or v in (relation.get("no_reference_values") or []) for v, c in zip(key, relation["from_columns"])):
                counts[key] += 1
        relation_counts[relation["id"]] = counts
    output_table = plan["grain"]["table"]
    output_keys = [tuple(row.get(c) for c in plan["grain"]["keys"]) for row in rows_by_table[output_table]]
    if any(any(column_missing(v, output_table, c) for v, c in zip(key, plan["grain"]["keys"])) for key in output_keys) or len(set(output_keys)) != len(output_keys):
        return dict(base, status="deferred", errors=[{"code": "grain_key_ambiguous", "path": "grain.keys", "message": "Missing or duplicate source keys do not identify the declared output entities"}], reason="output_entity_key_unverified")

    def value(node, row):
        op = node["op"]
        if op == "column":
            raw = row.get(node["column"])
            if column_missing(raw, node["table"], node["column"]):
                return None
            dtype = _type(columns[(node["table"], node["column"])])
            if dtype in _NUMERIC:
                result = _number(raw)
                if dtype == "integer" and result is not None and result != result.to_integral_value():
                    return None
                return result
            if dtype == "timestamp":
                return _timestamp(raw)
            if dtype == "boolean":
                if type(raw) is bool:
                    return raw
                return {"true": True, "false": False, "1": True, "0": False}.get(str(raw).lower()) if not _missing(raw) else None
            return None if _missing(raw) else str(raw)
        if op == "literal":
            raw = node["value"]
            if node.get("logical_type") == "datetime":
                return _timestamp(raw)
            return Decimal(str(raw)) if type(raw) in {int, float} else raw
        if op == "child_count":
            relation = next(r for r in checked["identity"]["relationships"] if r["id"] == node["relationship_id"])
            key = tuple(row.get(c) for c in relation["to_columns"])
            count = relation_counts[relation["id"]].get(key, 0)
            return count if count or plan["missing_policy"]["empty_group"] == "zero" else None
        args = [value(arg, row) for arg in node.get("args", [])]
        if op == "is_missing":
            argument = node["args"][0]
            if argument.get("op") == "column":
                return column_missing(row.get(argument["column"]), argument["table"], argument["column"])
            return args[0] is None
        if any(arg is None for arg in args):
            return None
        if op == "subtract":
            return _number(args[0] - args[1])
        if op == "divide":
            return _number(args[0] / args[1]) if args[1] > 0 else None
        if op == "eq":
            return args[0] == args[1]
        if op in {"lt", "lte", "gt", "gte"}:
            if isinstance(args[0], datetime) and (args[0].tzinfo is None) != (args[1].tzinfo is None):
                return None
            return {"lt": lambda: args[0] < args[1], "lte": lambda: args[0] <= args[1],
                    "gt": lambda: args[0] > args[1], "gte": lambda: args[0] >= args[1]}[op]()
        if op == "and":
            return args[0] and args[1]
        if op == "convert_unit":
            return _number(args[0] * _UNITS[node["from_unit"]][1] / _UNITS[node["to_unit"]][1])
        if op == "datetime_diff":
            if (args[0].tzinfo is None) != (args[1].tzinfo is None):
                return None
            delta = args[0] - args[1]
            return Decimal(delta.days * 86400000 + delta.seconds * 1000) + Decimal(delta.microseconds) / Decimal(1000)
        raise ValueError("Unsupported verified operator")

    output_rows = rows_by_table[plan["grain"]["table"]]
    values, grouped, selected, unavailable_selection = [], defaultdict(list), 0, 0
    with localcontext() as context:
        context.prec = 50
        for row in output_rows:
            selection = value(plan["population"]["filter"], row) if plan["population"]["filter"] is not None else True
            if selection is None:
                unavailable_selection += 1
                continue
            if selection is not True:
                continue
            selected += 1
            result = value(plan["expression"], row)
            values.append(result)
            if plan["group_by"]:
                key = tuple(None if column_missing(row.get(c["column"]), c["table"], c["column"]) else row.get(c["column"]) for c in plan["group_by"])
                grouped[key].append(result)
        summary = _summary(values)
        groups = [{"group_values": list(key), "value": _summary(group_values)} for key, group_values in sorted(grouped.items(), key=lambda pair: canonical(pair[0]))]
    summary["population_rows_before_selection"] = len(output_rows)
    summary["selection_unavailable_count"] = unavailable_selection
    summary["source_rows_preserved"] = True
    if "datetime_diff" in canonical(plan["expression"]):
        summary["datetime_limitations"] = {"calculation": "parsed_timestamp_coordinate_difference", "business_duration_contract": "unknown", "common_producer_clock": "unknown"}
    source_evidence = {"id": "measurement_" + digest({"identity": checked["identity"], "summary": summary, "groups": groups}),
                       "kind": "measurement", "target": copy.deepcopy(checked["identity"]), "value": summary,
                       "groups": groups, "source_version": inventory["source_version"], "schema_version": inventory["schema_version"],
                       "mapping_version": plan.get("mapping_version"), "source_files": copy.deepcopy(checked["identity"]["source_files"]),
                       "verified_by": "code", "statistics_only": True, "anomaly_execution": "not_run"}
    return dict(base, status="measured", summary=summary, groups=groups, source_evidence=source_evidence)


def verify_measurement_binding(plan, inventory, measurement):
    """Reject proof transfer to another formula, grain, population or version."""
    checked = validate_plan(plan, inventory)
    errors = list(checked["errors"])
    evidence = measurement.get("source_evidence") if isinstance(measurement, dict) else None
    if not checked["passed"] or not isinstance(measurement, dict) or measurement.get("status") != "measured" or not isinstance(evidence, dict):
        errors.append({"code": "measurement_required", "path": "source_evidence", "message": "A current code-generated measurement is required"})
    elif measurement.get("identity_sha256") != checked["identity_sha256"] or evidence.get("target") != checked["identity"]:
        errors.append({"code": "measurement_identity", "path": "source_evidence.target", "message": "Measurement does not match the exact plan identity"})
    else:
        expected_id = "measurement_" + digest({"identity": checked["identity"], "summary": measurement.get("summary"), "groups": measurement.get("groups", [])})
        if evidence.get("verified_by") != "code" or evidence.get("kind") != "measurement" or evidence.get("value") != measurement.get("summary") or evidence.get("id") != expected_id:
            errors.append({"code": "measurement_integrity", "path": "source_evidence", "message": "Measurement record differs from its code-generated commitment"})
    return {"passed": not errors, "errors": errors, "policy_version": VERSION}
