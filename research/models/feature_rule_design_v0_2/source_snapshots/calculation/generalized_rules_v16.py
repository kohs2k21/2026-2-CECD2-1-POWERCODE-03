"""Version 3.0 rule proposals are separate from immutable feature variables.

A rule closes over one raw subject or one verified variable. Its boolean
predicate is compiled into a new statistical plan. Original variables and
historical catalog results are never rewritten. No detector or model runs here.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal
from pathlib import Path

from generalized_measurement_v16 import canonical, digest, measure_plan, validate_plan
from generalized_roles_v16 import (evaluate_roles, validate_baseline,
                                   verify_measurement, verify_registered_evidence)

ROOT = Path(__file__).resolve().parent
VERSION = "3.0.0"
CONTEXT_FIELDS = ("grain", "population", "group_by", "missing_policy", "unit",
                  "schema_version", "source_version", "mapping_version")
RULE_FIELDS = {"id", "version", "role", "decision", "basis", "input", "condition",
               "shared_context", "threshold_policy", "evidence_ids", "reason", "required_evidence"}
BASES = {"ddl_constraint", "observational_review", "measured_baseline",
         "business_contract", "insufficient_evidence"}
AVAILABILITY = {"query_visibility": "unknown", "required_event_phase": "unknown"}


def _error(code, path, message):
    return {"code": code, "path": path, "message": message}


def _deferred(reason="Role-specific evidence is incomplete"):
    return {"decision": "deferred", "basis": "insufficient_evidence", "reason": reason, "evidence_ids": []}


def _numeric_literal(node):
    return (isinstance(node, dict) and node.get("op") == "literal" and
            type(node.get("value")) in {int, float} and math.isfinite(node["value"]))


def _nodes(node, path="condition", depth=0):
    if depth > 16 or not isinstance(node, dict):
        return
    yield node, path
    for index, child in enumerate(node.get("args", []) if isinstance(node.get("args"), list) else []):
        yield from _nodes(child, f"{path}.args[{index}]", depth + 1)


def _missing_column(expression):
    """Recognize exact missing predicates, including equality to boolean true."""
    node = expression
    if isinstance(node, dict) and node.get("op") == "eq":
        args = node.get("args", [])
        if len(args) == 2:
            for index in (0, 1):
                if args[index] == {"op": "literal", "value": True}:
                    node = args[1 - index]
                    break
    if isinstance(node, dict) and node.get("op") == "is_missing":
        args = node.get("args", [])
        if len(args) == 1 and isinstance(args[0], dict) and args[0].get("op") == "column":
            return args[0]
    return None


def _threshold_comparison(condition, subject):
    if not isinstance(condition, dict) or condition.get("op") not in {"eq", "lt", "lte", "gt", "gte"}:
        return None
    args = condition.get("args", [])
    if len(args) == 2 and args[0] == subject and _numeric_literal(args[1]):
        return condition["op"], args[1]["value"]
    return None


def _business_contract(rule, inventory, base, resolved, context, *, project_root):
    policy = rule["threshold_policy"]
    refs = policy.get("evidence_ids", [])
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
        return False, [], "A business policy requires explicit registered document facts"
    checked = validate_plan(base, inventory)
    source_columns = checked["identity"]["source_columns"] if checked["passed"] else []
    table = context["grain"]["table"]
    if any(ref["table"] != table for ref in source_columns):
        return False, [], "Cross-table business contracts require a future explicit multi-table scope contract"
    names = sorted({ref["column"] for ref in source_columns})
    target = {"table": table, "columns": names}
    expected = {**target, "input_measure": copy.deepcopy(base["expression"]),
                "condition": copy.deepcopy(resolved), "grain": copy.deepcopy(context["grain"]),
                "population": copy.deepcopy(context["population"]), "group_by": copy.deepcopy(context["group_by"]),
                "missing_policy": copy.deepcopy(context["missing_policy"]), "unit": copy.deepcopy(context["unit"]),
                "contract_status": "confirmed", "violation_when_true": True}
    evidence = {item["id"]: item for item in inventory.get("evidence", [])}
    for ref in refs:
        fact = evidence.get(ref, {})
        if (fact.get("kind") != "business_document" or
                any(fact.get("target", {}).get(key) != value for key, value in target.items()) or
                fact.get("value") != expected or not verify_registered_evidence(fact, project_root=project_root)):
            return False, [], "Business condition, input measure, unit and scope must exactly match a current independent document"
    return True, refs, "Exact scope-bound confirmed business condition was verified"


def _baseline_threshold(rule, inventory, base, original_condition, subject, current_measurement, *, project_root):
    policy = rule["threshold_policy"]
    baseline = policy.get("baseline")
    quantile = policy.get("quantile")
    if (quantile not in (.95, .99) or not isinstance(baseline, dict) or
            not isinstance(baseline.get("quantiles"), list) or quantile not in baseline["quantiles"]):
        return False, [], "A baseline policy requires an explicit p95/p99 reference specification"
    review = validate_baseline(base, inventory, baseline, project_root=project_root)
    if not review["passed"]:
        return False, [], "Baseline verification failed: " + ", ".join(error["code"] for error in review["errors"])
    comparison = _threshold_comparison(original_condition, subject)
    if comparison is None or comparison[0] != baseline["comparison_operator"]:
        return False, [], "A baseline condition must compare the bound input against the declared quantile in the declared direction"
    metric = "p95" if quantile == .95 else "p99"
    reference = baseline["measurement"]
    if base["group_by"]:
        wanted = {canonical(group["group_values"]) for group in current_measurement.get("groups", [])}
        values = [group["value"].get(metric) for group in reference.get("groups", [])
                  if canonical(group["group_values"]) in wanted]
    else:
        values = [reference["summary"].get(metric)]
    if not values or any(value is None or Decimal(str(value)) != Decimal(str(comparison[1])) for value in values):
        return False, [], "The literal must equal every applicable measured quantile; differing group thresholds require a future explicit group threshold expression"
    return True, [reference["source_evidence"]["id"]], "Exact measured p95/p99 reference, comparison and scope were verified"


def _validate_bound_rule(rule, inventory, variables, measurements, *, project_root=ROOT):
    """Validate one rule, preserving variable calculations and their provenance."""
    errors = []
    result = {"passed": False, "version": VERSION, "errors": errors,
              "eligibility": _deferred(), "predicate_plan": None,
              "predicate_measurement": None, "shared_identity_sha256": None,
              "variable_rewritten": False, "anomaly_execution": "not_run"}
    def fail(code, path, message):
        errors.append(_error(code, path, message))
    if not isinstance(rule, dict):
        fail("rule_object", "$", "A rule must be an object")
        return result
    for field in sorted(RULE_FIELDS - set(rule)):
        fail("required_field", field, "Rule field is required")
    for field in sorted(set(rule) - RULE_FIELDS):
        fail("rule_extra_field", field, "Unknown fields cannot change rule semantics")
    if rule.get("version") != VERSION:
        fail("rule_version", "version", "This policy applies only to separate 3.0 rules")
    if not isinstance(rule.get("id"), str) or not rule["id"]:
        fail("rule_id", "id", "Any unique nonempty lineage ID is allowed")
    if rule.get("role") not in {"HR", "SR"}:
        fail("rule_role", "role", "IF input proposals belong to the variable stage")
    if rule.get("decision") not in {"proposed", "deferred", "excluded"} or rule.get("basis") not in BASES:
        fail("rule_decision", "decision", "Decision and role basis must use the separate rule contract")
    for field in ("evidence_ids", "required_evidence"):
        if not isinstance(rule.get(field), list) or any(not isinstance(value, str) for value in rule.get(field, [])):
            fail("rule_evidence_shape", field, "Explicit evidence labels must be strings")
    if not isinstance(rule.get("reason"), str) or not rule["reason"]:
        fail("rule_reason", "reason", "The proposal must explain its conditions or missing evidence")
    context = rule.get("shared_context")
    if not isinstance(context, dict):
        fail("shared_context", "shared_context", "A rule requires an explicit input context")
        return result
    for field in CONTEXT_FIELDS + ("availability",):
        if field not in context:
            fail("required_field", "shared_context." + field, "Input context field is required")
    for field in set(context) - set(CONTEXT_FIELDS) - {"availability"}:
        fail("shared_context_extra", "shared_context." + field, "Unknown context fields cannot alter provenance")
    if context.get("availability") != AVAILABILITY:
        fail("availability_unverified", "shared_context.availability", "Current rule statistics do not verify operating availability")
    for field in ("grain", "population", "missing_policy", "unit"):
        if not isinstance(context.get(field), dict):
            fail("shared_context_shape", "shared_context." + field, "Input metadata field must be an object")
    if not isinstance(context.get("group_by"), list):
        fail("shared_context_shape", "shared_context.group_by", "Input grouping must be an explicit list")
    try:
        result["shared_identity_sha256"] = digest(context)
    except (ValueError, TypeError):
        fail("context_json", "shared_context", "Context must be finite canonical JSON")
    if errors:
        return result
    tables = {table["id"]: table for table in inventory.get("tables", [])}
    columns = {(table["id"], column["name"]): column for table in inventory.get("tables", []) for column in table["columns"]}
    source = rule.get("input")
    if not isinstance(source, dict):
        fail("rule_input", "input", "A rule closes over exactly one explicit subject")
        return result
    base = None
    source_measurement = None
    subject = None
    source_validation = None
    if source.get("kind") == "raw_column":
        if set(source) != {"kind", "table", "column"}:
            fail("input_fields", "input", "Raw input uses only kind/table/column")
        reference = (source.get("table"), source.get("column"))
        column = columns.get(reference) if all(isinstance(x, str) for x in reference) else None
        if column is None or column.get("excluded"):
            fail("raw_input_reference", "input.column", "Raw subject must be a real included physical column")
            return result
        if context.get("grain", {}).get("table") != source["table"] or context.get("grain", {}).get("keys") != tables[source["table"]]["primary_key"]:
            fail("raw_input_grain", "shared_context.grain", "Raw input requires the complete physical table key")
        expected_missing = {"input": "propagate", "empty_group": None, "denominator_zero": None}
        if context.get("missing_policy") != expected_missing:
            fail("raw_input_missing_policy", "shared_context.missing_policy", "Raw reading must preserve missing input and null empty/denominator policies")
        if context.get("unit") != column["unit"]:
            fail("raw_input_unit", "shared_context.unit", "Raw input context must preserve the source column unit, including unknown")
        subject = {"op": "column", "table": source["table"], "column": source["column"]}
        base = {"id": "rule_subject_" + rule.get("id", "invalid"), "purpose": "feature_calculation",
                "expression": copy.deepcopy(subject), "plan_version": "2.2.0",
                "availability": copy.deepcopy(context["availability"]),
                **{field: copy.deepcopy(context.get(field)) for field in CONTEXT_FIELDS}}
        source_validation = validate_plan(base, inventory)
        source_measurement = measure_plan(base, inventory, project_root=project_root) if source_validation["passed"] else None
    elif source.get("kind") == "variable":
        if set(source) != {"kind", "variable_id"}:
            fail("input_fields", "input", "Variable input uses only kind/variable_id")
        matches = [value for value in variables if isinstance(value, dict) and value.get("id") == source.get("variable_id")] if isinstance(variables, list) else []
        if len(matches) != 1:
            fail("variable_reference", "input.variable_id", "Variable IDs must resolve uniquely to an immutable feature plan")
            return result
        base = copy.deepcopy(matches[0])
        if base.get("purpose") != "feature_calculation":
            fail("variable_stage", "input.variable_id", "Main-stage variables are feature calculations only")
        source_validation = validate_plan(base, inventory)
        candidates = [value for value in measurements if isinstance(value, dict) and value.get("candidate_id") == source["variable_id"]] if isinstance(measurements, list) else []
        if len(candidates) != 1:
            fail("variable_measurement", "input.variable_id", "Variable input requires exactly one code-owned measurement status record")
        elif candidates[0].get("status") == "measured":
            if not verify_measurement(base, inventory, candidates[0], project_root=project_root):
                fail("variable_measurement", "input.variable_id", "Measured input must match its exact current code-replayed proof")
            else:
                source_measurement = candidates[0]
        elif candidates[0].get("status") == "deferred":
            replay = measure_plan(base, inventory, project_root=project_root)
            if replay.get("status") != "deferred" or canonical(replay) != canonical(candidates[0]):
                fail("variable_measurement", "input.variable_id", "Deferred measurement must match the current code-owned replay, not fabricated observations")
            else:
                source_measurement = candidates[0]
        else:
            fail("variable_measurement", "input.variable_id", "Rejected measurements cannot support executable variable rules")
        for field in CONTEXT_FIELDS:
            if context.get(field) != base.get(field):
                fail("variable_shared_context", "shared_context." + field, "Rule context must match the original variable exactly")
        subject = {"op": "variable", "id": source["variable_id"]}
    else:
        fail("rule_input_kind", "input.kind", "Rule input must be a raw column or a referenced variable")
        return result
    if source_validation and not source_validation["passed"]:
        for error in source_validation["errors"]:
            fail("input_" + error["code"], "shared_context." + error["path"], error["message"])
    if errors:
        return result
    allowed = {(ref["table"], ref["column"]) for ref in source_validation["identity"]["source_columns"]}
    condition = rule.get("condition")
    count = [0]
    captured = [False]
    def substitute(node, path="condition", depth=0):
        count[0] += 1
        if count[0] > 128 or depth > 16 or not isinstance(node, dict):
            fail("rule_condition_shape", path, "Rule conditions must be bounded AST objects")
            return None
        if node.get("op") == "variable":
            if set(node) != {"op", "id"} or source["kind"] != "variable" or node.get("id") != source["variable_id"]:
                fail("rule_variable_scope", path, "A condition cannot capture another variable")
                return None
            captured[0] = True
            return copy.deepcopy(base["expression"])
        if node.get("op") == "column" and (node.get("table"), node.get("column")) not in allowed:
            fail("rule_column_scope", path, "Only the bound input and its exact source descendants may occur")
        elif node.get("op") == "column":
            captured[0] = True
        if source["kind"] == "variable" and node.get("op") == "is_missing" and node.get("args") == [subject] and base["expression"].get("op") == "column" and source_validation["inferred_type"] != "category":
            fail("variable_missing_semantics", path, "A typed raw variable can be unavailable without being lexically missing; use a separately defined variable or raw missing rule")
        result_node = copy.deepcopy(node)
        if "args" in node:
            if not isinstance(node["args"], list):
                fail("rule_condition_args", path + ".args", "AST arguments must be a list")
                return None
            result_node["args"] = [substitute(child, f"{path}.args[{index}]", depth + 1) for index, child in enumerate(node["args"])]
        return result_node
    resolved = substitute(condition)
    if not captured[0]:
        fail("rule_input_unused", "condition", "A constant condition does not test its bound input")
    predicate = {"id": "rule_predicate_" + rule["id"], "purpose": "constraint_check" if rule["role"] == "HR" else "observational_diagnostic",
                 "expression": resolved, "plan_version": "2.2.0",
                 "availability": copy.deepcopy(context["availability"]),
                 **{field: copy.deepcopy(context[field]) for field in CONTEXT_FIELDS}}
    # These are the new condition output's unit/policy, never the input's.
    predicate["unit"] = {"name": "boolean", "status": "verified", "evidence_ids": []}
    predicate["missing_policy"]["input"] = "count_missing" if isinstance(resolved, dict) and resolved.get("op") == "is_missing" else "propagate"
    result["predicate_plan"] = predicate
    checked = validate_plan(predicate, inventory)
    for error in checked["errors"]:
        path = "condition" + error["path"][len("expression"):] if error["path"].startswith("expression") else "predicate_plan." + error["path"]
        fail("predicate_" + error["code"], path, error["message"])
    if checked["inferred_type"] != "boolean":
        fail("rule_condition_boolean", "condition", "A rule condition must produce a typed boolean, not a feature value")
    if errors:
        return result
    measured = measure_plan(predicate, inventory, project_root=project_root)
    result["predicate_measurement"] = measured
    policy = rule.get("threshold_policy")
    policy_kind = policy.get("kind") if isinstance(policy, dict) else None
    if policy is not None and (not isinstance(policy_kind, str) or policy_kind not in {"baseline", "business_contract"}):
        fail("threshold_policy", "threshold_policy", "A numeric threshold needs a supported explicit policy")
    numeric_threshold = any(_numeric_literal(node) for node, _ in _nodes(condition))
    eligibility = _deferred()
    missing = _missing_column(resolved)
    if rule["role"] == "HR" and missing:
        # Equivalent boolean wrappers are normalized only for declaration proof.
        proof = copy.deepcopy(predicate)
        proof["expression"] = {"op": "is_missing", "args": [copy.deepcopy(missing)]}
        proof["missing_policy"]["input"] = "count_missing"
        judged = evaluate_roles(proof, inventory, project_root=project_root)
        eligibility = judged["roles"]["HR"]
    elif rule["role"] == "SR" and not numeric_threshold and policy is None:
        if measured["status"] == "measured" and verify_measurement(predicate, inventory, measured, project_root=project_root):
            eligibility = {"decision": "proposed", "basis": "observational_review",
                           "reason": "Separate closed boolean diagnostic was measured in its exact input context; no baseline is required",
                           "evidence_ids": [measured["source_evidence"]["id"]]}
    if isinstance(policy_kind, str) and policy_kind in {"baseline", "business_contract"}:
        okay = False
        refs = []
        reason = "Threshold policy is not verified"
        if numeric_threshold and context["unit"].get("status") != "verified":
            reason = "Numeric threshold requires a verified input unit; names and statistics do not establish a scale"
        elif policy["kind"] == "baseline":
            if rule["role"] != "SR":
                reason = "Historical quantiles support SR comparison, not an HR contract"
            elif source_measurement is None or source_measurement.get("status") != "measured":
                reason = "Reference comparison requires a measured original numeric input"
            else:
                okay, refs, reason = _baseline_threshold(rule, inventory, base, condition, subject, source_measurement, project_root=project_root)
        else:
            okay, refs, reason = _business_contract(rule, inventory, base, resolved, context, project_root=project_root)
        if okay and measured["status"] == "measured":
            eligibility = {"decision": "proposed", "basis": "measured_baseline" if policy["kind"] == "baseline" else "business_contract",
                           "reason": reason, "evidence_ids": refs + [measured["source_evidence"]["id"]]}
        elif eligibility["decision"] != "proposed":
            eligibility = _deferred(reason)
    elif numeric_threshold:
        eligibility = _deferred("A numeric threshold is an unverified proposal without a measured baseline or confirmed business condition")
    if eligibility["decision"] == "proposed" and source["kind"] == "variable" and source_measurement and source_measurement.get("source_evidence"):
        eligibility["evidence_ids"] = list(dict.fromkeys(eligibility["evidence_ids"] + [source_measurement["source_evidence"]["id"]]))
    # A raw ALL-row missing statistic can support this exact new missing rule,
    # after its count is independently replayed. It proves no other formula.
    if (source["kind"] == "raw_column" and rule["role"] == "SR" and
            eligibility["decision"] == "proposed" and policy is None):
        existing_refs = []
        if (missing == subject and isinstance(resolved, dict) and resolved.get("op") == "is_missing" and
                context["population"] == {"table": source["table"], "filter": None} and not context["group_by"]):
            column = columns[(source["table"], source["column"])]
            stats = column.get("statistics") or {}
            csv_source = tables[source["table"]].get("csv_source") or {}
            for fact in inventory.get("evidence", []):
                if (fact.get("kind") == "data_profile" and fact.get("verified_by") == "code" and
                        fact.get("target") == {"table": source["table"], "column": source["column"]} and
                        fact.get("value") == stats and fact.get("source", {}).get("path") == csv_source.get("path") and
                        fact.get("source", {}).get("sha256") == csv_source.get("sha256") and
                        stats.get("population") == {"table": source["table"], "selection": None} and
                        stats.get("row_count") == measured["summary"]["row_count"] and
                        stats.get("missing_count") == measured["summary"]["true_count"]):
                    existing_refs.append(fact["id"])
        if existing_refs:
            eligibility["evidence_ids"] = list(dict.fromkeys(eligibility["evidence_ids"] + existing_refs))
        elif measured["source_evidence"]["id"] not in rule["evidence_ids"]:
            eligibility = _deferred("This raw condition needs a separately named measured variable or the exact predicate measurement before proposing SR")
    result["eligibility"] = eligibility
    allowed_evidence = set(eligibility["evidence_ids"])
    if source_measurement and source_measurement.get("source_evidence"):
        allowed_evidence.add(source_measurement["source_evidence"]["id"])
    relevant_schema = {fact["id"] for fact in inventory.get("evidence", []) if fact.get("kind") == "schema_declaration" and
                       (fact.get("target", {}).get("table"), fact.get("target", {}).get("column")) in allowed and
                       verify_registered_evidence(fact, project_root=project_root)}
    allowed_evidence |= relevant_schema
    for index, ref in enumerate(rule["evidence_ids"]):
        if ref not in allowed_evidence:
            fail("rule_evidence_scope", f"evidence_ids[{index}]", "Evidence must bind this exact input, condition and role")
    if rule["decision"] == "proposed":
        if eligibility["decision"] != "proposed":
            fail("rule_ineligible_proposal", "decision", eligibility["reason"])
        elif rule["basis"] != eligibility["basis"]:
            fail("rule_basis", "basis", "Proposed role basis differs from the verified role-specific support")
        if not set(rule["evidence_ids"]) & set(eligibility["evidence_ids"]):
            fail("rule_proposal_evidence", "evidence_ids", "A proposed rule must cite at least one given proof relevant to its supported role")
    elif eligibility["decision"] == "proposed":
        fail("missed_supported_proposal", "decision", "A supported HR/SR proposal was removed by blanket deferral or exclusion")
    result["passed"] = not errors
    return result


def _context(plan):
    return {**{field: copy.deepcopy(plan[field]) for field in CONTEXT_FIELDS},
            "availability": copy.deepcopy(plan.get("availability", AVAILABILITY))}


def _raw_plan(inventory, table, column):
    return {"id": "raw_subject_" + digest({"table": table["id"], "column": column["name"]})[:24],
            "purpose": "feature_calculation", "expression": {"op": "column", "table": table["id"], "column": column["name"]},
            "grain": {"table": table["id"], "keys": copy.deepcopy(table["primary_key"])},
            "population": {"table": table["id"], "filter": None}, "group_by": [],
            "missing_policy": {"input": "propagate", "empty_group": None, "denominator_zero": None},
            "unit": copy.deepcopy(column["unit"]), "schema_version": inventory["schema_version"],
            "source_version": inventory["source_version"], "mapping_version": inventory.get("mapping_version"),
            "plan_version": "2.2.0", "availability": copy.deepcopy(AVAILABILITY)}


def build_subject_registry(inventory, variables, validation, measurements, *, project_root=ROOT):
    """Code-owned subject definitions, including invalid and unmeasured states.

Model proposals and validation records remain separate. A proposal SHA never
substitutes for a validated executable definition SHA. Raw references are
compiled independently so a broken variable cannot erase a physical DDL HR.
"""
    from generalized_profile_v16 import assert_profile_admission
    assert_profile_admission(inventory)
    subjects = []
    checks = validation.get("variables", []) if isinstance(validation, dict) else validation
    checks = checks if isinstance(checks, list) else []
    variables = variables if isinstance(variables, list) else []
    measurements = measurements if isinstance(measurements, list) else []
    ids = [value.get("id") for value in variables if isinstance(value, dict)]
    for index, original in enumerate(variables):
        plan = copy.deepcopy(original)
        candidate_id = plan.get("id") if isinstance(plan, dict) else None
        checked = validate_plan(plan, inventory)
        matches = [value for value in checks if value.get("plan_id") == candidate_id and value.get("index", index) == index]
        provided = matches[0] if len(matches) == 1 else None
        valid = (checked["passed"] and candidate_id is not None and ids.count(candidate_id) == 1
                 and provided is not None and provided.get("passed") is True)
        definition = checked.get("definition_sha256", checked.get("identity_sha256")) if valid else None
        if provided and provided.get("definition_sha256", provided.get("identity_sha256")) != definition and valid:
            valid = False
            definition = None
        existing = [value for value in measurements if value.get("candidate_id") == candidate_id]
        measured = copy.deepcopy(existing[0]) if len(existing) == 1 else None
        measure_valid = False
        if valid and measured:
            if measured.get("status") == "measured":
                measure_valid = verify_measurement(plan, inventory, measured, project_root=project_root)
            elif measured.get("status") == "deferred":
                replay = measure_plan(plan, inventory, project_root=project_root)
                measure_valid = replay.get("status") == "deferred" and canonical(replay) == canonical(measured)
        subjects.append({"reference": {"kind": "variable", "variable_id": candidate_id, "definition_sha256": definition},
                         "proposal_sha256": checked.get("proposal_sha256", digest(original)), "definition_sha256": definition,
                         "definition_status": "valid" if valid else "invalid",
                         "measurement_status": measured.get("status", "rejected") if measured and measure_valid else "rejected",
                         "inferred_type": checked.get("inferred_type"), "shared_context": _context(plan) if valid else None,
                         "plan": plan, "measurement": measured if measure_valid else None,
                         "validation_errors": copy.deepcopy((provided or checked).get("errors", [])),
                         "evidence_ids": [measured["source_evidence"]["id"]] if measure_valid and measured.get("source_evidence") else []})
    for table in inventory.get("tables", []):
        for column in table.get("columns", []):
            if column.get("excluded"):
                continue
            plan = _raw_plan(inventory, table, column)
            checked = validate_plan(plan, inventory)
            valid = checked["passed"]
            definition = checked.get("definition_sha256", checked.get("identity_sha256")) if valid else None
            measured = measure_plan(plan, inventory, project_root=project_root) if valid else None
            subjects.append({"reference": {"kind": "raw_column", "table": table["id"], "column": column["name"], "definition_sha256": definition},
                             "proposal_sha256": checked.get("proposal_sha256", digest(plan)), "definition_sha256": definition,
                             "definition_status": "valid" if valid else "invalid", "measurement_status": measured["status"] if measured else "rejected",
                             "inferred_type": checked.get("inferred_type"), "shared_context": _context(plan) if valid else None,
                             "plan": plan, "measurement": measured, "validation_errors": copy.deepcopy(checked["errors"]),
                             "evidence_ids": copy.deepcopy(column.get("evidence_ids", [])) +
                                ([measured["source_evidence"]["id"]] if measured and measured.get("source_evidence") else [])})
    return {"version": VERSION, "schema_version": inventory["schema_version"], "source_version": inventory["source_version"],
            "subjects": subjects, "registry_sha256": digest(subjects), "owner": "verified_code",
            "automatic_application": False}


def public_subject_registry(registry):
    keys = ("reference", "proposal_sha256", "definition_sha256", "definition_status", "measurement_status",
            "inferred_type", "shared_context", "validation_errors", "evidence_ids")
    return {"version": registry["version"], "schema_version": registry["schema_version"], "source_version": registry["source_version"],
            "registry_sha256": registry["registry_sha256"],
            "subjects": [{key: copy.deepcopy(value.get(key)) for key in keys} for value in registry["subjects"]]}


def _resolve_subject(reference, inventory, registry):
    if not isinstance(reference, dict) or not isinstance(registry, dict):
        return None, [_error("subject_reference", "input", "A rule needs a code-issued immutable subject reference")]
    expected = ({"kind", "variable_id", "definition_sha256"} if reference.get("kind") == "variable" else
                {"kind", "table", "column", "definition_sha256"} if reference.get("kind") == "raw_column" else set())
    if not expected or set(reference) != expected:
        return None, [_error("subject_reference", "input", "Input must use only kind, physical/variable ID and definition_sha256")]
    if registry.get("source_version") != inventory.get("source_version") or registry.get("schema_version") != inventory.get("schema_version"):
        return None, [_error("stale_registry", "input", "Subject registry belongs to another source or schema version")]
    if registry.get("registry_sha256") != digest(registry.get("subjects", [])):
        return None, [_error("registry_integrity", "input", "Code-owned subject records changed after compilation")]
    matches = [value for value in registry.get("subjects", []) if value.get("reference") == reference]
    if len(matches) != 1:
        return None, [_error("definition_sha256", "input.definition_sha256", "Reference and validated definition SHA must resolve uniquely in the current code registry")]
    item = matches[0]
    if item["definition_status"] != "valid" or not item.get("definition_sha256"):
        return None, [_error("invalid_variable_definition", "input", "Invalid variables remain recorded but cannot support executable rules")]
    checked = validate_plan(item["plan"], inventory)
    if not checked["passed"] or checked.get("definition_sha256", checked.get("identity_sha256")) != reference["definition_sha256"]:
        return None, [_error("definition_changed", "input.definition_sha256", "The original validated definition or physical source has changed")]
    if item["shared_context"] != _context(item["plan"]):
        return None, [_error("input_context_changed", "input", "Code-owned input context must equal the validated original definition")]
    return item, []


def validate_rule(rule, inventory, registry, *, project_root=ROOT):
    """Attach verified context in code; the model cannot copy or change it."""
    from generalized_profile_v16 import assert_profile_admission
    from generalized_protocol_v16 import rule_schema
    import jsonschema
    assert_profile_admission(inventory)
    schema = rule_schema()
    single = copy.deepcopy(schema["properties"]["rules"]["items"])
    single["$defs"] = schema["$defs"]
    errors = [_error("rule_output_schema", ".".join(map(str, error.absolute_path)), error.message)
              for error in jsonschema.Draft202012Validator(single).iter_errors(rule)]
    empty = {"passed": False, "version": VERSION, "errors": errors, "eligibility": _deferred(),
             "predicate_plan": None, "predicate_measurement": None, "variable_rewritten": False,
             "input_definition_sha256": None, "bound_input_context": None, "condition_output_context": None,
             "anomaly_execution": "not_run"}
    if errors:
        return empty
    item, errors = _resolve_subject(rule["input"], inventory, registry)
    if errors:
        empty["errors"] = errors
        return empty
    context = copy.deepcopy(item["shared_context"])
    compiled = copy.deepcopy(rule)
    compiled["input"].pop("definition_sha256")
    compiled["shared_context"] = context
    variables = [item["plan"]] if rule["input"]["kind"] == "variable" else []
    measurements = [item["measurement"]] if variables and item.get("measurement") else []
    result = _validate_bound_rule(compiled, inventory, variables, measurements, project_root=project_root)
    result["input_definition_sha256"] = item["definition_sha256"]
    result["bound_input_context"] = context
    result["input_definition_status"] = item["definition_status"]
    result["input_measurement_status"] = item["measurement_status"]
    predicate = result.get("predicate_plan")
    result["condition_output_context"] = ({"inferred_type": "boolean", "unit": copy.deepcopy(predicate["unit"]),
                                            "missing_policy": copy.deepcopy(predicate["missing_policy"])} if predicate else None)
    result["input_context_copied_by_model"] = False
    result["compiled_rule_sha256"] = digest(compiled)
    return result


def validate_review_request(review, inventory, registry, *, project_root=ROOT):
    """Preserve unresolved analysis requests without counting executable rules."""
    from generalized_protocol_v16 import rule_schema
    from generalized_profile_v16 import assert_profile_admission
    import jsonschema
    assert_profile_admission(inventory, project_root=project_root)
    schema = rule_schema()["properties"]["review_requests"]["items"]
    errors = [_error("review_schema", ".".join(map(str, error.absolute_path)), error.message)
              for error in jsonschema.Draft202012Validator(schema).iter_errors(review)]
    item = None
    if not errors:
        item, errors = _resolve_subject(review["input"], inventory, registry)
    return {"passed": not errors, "errors": errors, "version": VERSION,
            "state": "review_requested", "executable": False, "counts_as_valid_rule": False,
            "input_definition_sha256": item["definition_sha256"] if item else None}
