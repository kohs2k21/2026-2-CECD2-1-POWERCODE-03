"""Minimal model variable intents bound to source-owned computational2.2.0 plans.

The model selects meaning, an AST and analysis scope. Code owns physical keys,
types, source versions, required calculation policies and confirmed input units.
An old full model plan is rejected rather than repaired into the new contract.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import re

import jsonschema

from generalized_content_policy_v16 import assert_profile_admission
from generalized_measurement_v16 import validate_plan, digest
from generalized_profile_v16 import validate_semantic_mapping
from generalized_protocol_v16 import ast_definitions

ROOT = Path(__file__).resolve().parent
INTENT_VERSION = "1.0.0"
OUTPUT_VERSION = "4.0.0"
PLAN_VERSION = "2.2.0"
AVAILABILITY = {"query_visibility": "unknown", "required_event_phase": "unknown"}


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def variable_intent_schema():
    """Versions and computed input policies are deliberately absent from AI output."""
    reference = _object({"table": {"type": "string", "minLength": 1}, "column": {"type": "string", "minLength": 1}})
    text_ids = {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 16}
    schema = _object({
        "id": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "purpose": {"const": "feature_calculation"},
        "expression": {"$ref": "#/$defs/expression"},
        "grain_table": {"type": "string", "minLength": 1},
        "population_filter": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/boolean_expression"}]},
        "group_by": {"type": "array", "items": reference, "maxItems": 5},
        "semantic_mapping_ids": copy.deepcopy(text_ids),
        "unit_proposal": {"anyOf": [{"type": "null"}, _object({
            "name": {"type": "string", "minLength": 1}, "reason": {"type": "string", "minLength": 1},
            "evidence_ids": copy.deepcopy(text_ids)})]},
        "required_evidence": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 12},
    })
    schema["$defs"] = ast_definitions()
    return schema


def _sha(value):
    try:
        return digest(value)
    except (ValueError, TypeError, RecursionError):
        return None


def _error(code, path, message, **details):
    return {"code": code, "path": path, "message": message, **details}


def _value_at(value, path):
    if path == "$":
        return value
    try:
        for part in re.findall(r"([^.[\]]+)|\[(\d+)\]", path):
            value = value[int(part[1])] if part[1] else value[part[0]]
        return value
    except (KeyError, IndexError, TypeError):
        return None


def _finish(record, inventory):
    """Feedback preserves actual intent values; code context never repairs them."""
    for error in record["errors"]:
        path = error["path"]
        error.setdefault("actual_path", path)
        actual = _value_at(record["intent"], path)
        if actual is None and record.get("plan") is not None:
            actual = _value_at(record["plan"], error.get("materialized_plan_path", path))
        error.setdefault("wrong_value", copy.deepcopy(error.get("received_value", actual)))
        allowed = error.get("allowed_values")
        if allowed is None:
            if error["code"] == "intent_forbidden_field":
                allowed = []
            elif error["code"] == "ast_arity":
                allowed = {"arity": error["expected_arity"]}
            elif error["code"] == "semantic_mapping_reference":
                allowed = error.get("supplied_ids", [])
            elif error["code"] == "unknown_table":
                allowed = [table["id"] for table in inventory.get("tables", [])] if isinstance(inventory, dict) else []
            elif error["code"] == "unknown_column":
                allowed = [{"table": table["id"], "column": column["name"]} for table in inventory.get("tables", [])
                           for column in table["columns"] if not column.get("excluded")]
            elif error["code"] in ("required_ast_field", "intent_required_field"):
                allowed = {"required_field": error.get("required_field"), "field_must_be_present": True}
            elif error["code"] == "ast_args":
                allowed = {"type": "array", "arity": error["expected_arity"]}
            elif error["code"] == "ast_extra_field":
                allowed = {"fields": error.get("allowed_fields", [])}
            elif error["code"] == "computed_unavailability_is_not_missing":
                allowed = {"supported_argument_op": "column", "computed_unavailability_is_lexical_missing": False}
            elif error["code"] == "population_filter":
                allowed = {"inferred_type": "boolean"}
            else:
                allowed = {"requirement": error["message"]}
            error["allowed_values"] = copy.deepcopy(allowed)
        error.setdefault("required_fields", [error["required_field"]] if error.get("required_field") else [])
    return record


def _unavailable_missing_errors(expression, path="expression"):
    from generalized_trigger_semantics_v17 import validate_measurement_semantics
    checked = validate_measurement_semantics(expression)
    errors = copy.deepcopy(checked["errors"])
    for error in errors:
        if error["path"].startswith("condition"):
            error["path"] = path + error["path"][len("condition"):]
    return errors


def _path(parts):
    text = ""
    for part in parts:
        text += f"[{part}]" if isinstance(part, int) else ("." if text else "") + str(part)
    return text or "$"


def _walk(node, path="expression", depth=0):
    if isinstance(node, dict) and depth <= 16:
        yield node, path
        args = node.get("args")
        for index, child in enumerate(args if isinstance(args, list) else []):
            yield from _walk(child, f"{path}.args[{index}]", depth + 1)


def _ast_errors(node, path="expression", depth=0, budget=None):
    """Give exact supported-operation structure errors instead of anyOf noise."""
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 16 or budget[0] > 128:
        return [_error("ast_limit", path, "AST exceeds bounded depth16 or node count128")]
    if not isinstance(node, dict):
        return [_error("ast_object", path, "AST child must be an object")]
    fields = {"column": {"op", "table", "column"}, "literal": {"op", "value"},
        "child_count": {"op", "relationship_id"}, "datetime_diff": {"op", "args", "unit"},
        "convert_unit": {"op", "args", "from_unit", "to_unit"},
        **{op: {"op", "args"} for op in ("is_missing", "subtract", "divide", "eq", "lt", "lte", "gt", "gte", "and")}}
    if "op" not in node:
        return [_error("required_ast_field", path + ".op", "Supported parent operations still require an op on every child", required_field="op")]
    op = node["op"]
    if not isinstance(op, str) or op not in fields:
        return [_error("unsupported_operator", path + ".op", "Operator is outside the computational AST contract", allowed_values=sorted(fields))]
    allowed = fields[op] | ({"logical_type"} if op == "literal" else set())
    errors = [_error("ast_extra_field", path + "." + str(key), "AST field is not allowed", allowed_fields=sorted(allowed)) for key in sorted(set(node) - allowed)]
    errors += [_error("required_ast_field", path + "." + key, "Required operator field is missing", required_field=key) for key in sorted(fields[op] - set(node))]
    if op == "column":
        for field in ("table", "column"):
            if field in node and (not isinstance(node[field], str) or not node[field]):
                errors.append(_error("column_reference", path + "." + field, "Physical reference must be a nonempty string"))
    elif op == "child_count":
        if "relationship_id" in node and (not isinstance(node["relationship_id"], str) or not node["relationship_id"]):
            errors.append(_error("relationship_id", path + ".relationship_id", "Relationship ID must be a nonempty string"))
    elif op == "literal":
        value = node.get("value")
        if "logical_type" in node:
            if node["logical_type"] != "datetime" or not isinstance(value, str):
                errors.append(_error("literal_logical_type", path + ".logical_type", "Only string datetime literal typing is supported", allowed_values=["datetime"]))
        elif "value" in node and not (isinstance(value, str) or type(value) in (int, bool) or type(value) is float and math.isfinite(value)):
            errors.append(_error("literal_type", path + ".value", "Literal must be a finite string, number or boolean"))
    else:
        args = node.get("args")
        expected = 1 if op in ("is_missing", "convert_unit") else 2
        if "args" in node:
            if not isinstance(args, list):
                errors.append(_error("ast_args", path + ".args", "Operator args must be an array", expected_arity=expected))
            elif len(args) != expected:
                errors.append(_error("ast_arity", path + ".args", "Supported operator has incorrect argument count", expected_arity=expected, actual_arity=len(args)))
            for index, child in enumerate(args if isinstance(args, list) else []):
                errors.extend(_ast_errors(child, f"{path}.args[{index}]", depth + 1, budget))
        if op == "datetime_diff" and "unit" in node and node["unit"] != "ms":
            errors.append(_error("datetime_unit", path + ".unit", "datetime_diff is supported with explicit ms output", allowed_values=["ms"]))
        if op == "convert_unit":
            for field in ("from_unit", "to_unit"):
                if field in node and (not isinstance(node[field], str) or not node[field]):
                    errors.append(_error("unit_conversion", path + "." + field, "Explicit conversion unit must be a nonempty string"))
    return errors


def _required_policies(expression, population_filter):
    expression_ops = {node["op"] for node, _ in _walk(expression) if isinstance(node.get("op"), str)}
    filter_ops = {node["op"] for node, _ in _walk(population_filter) if isinstance(node.get("op"), str)}
    return {"input": "count_missing" if isinstance(expression, dict) and expression.get("op") == "is_missing" else "propagate",
        "empty_group": "zero" if "child_count" in expression_ops else None,
        "denominator_zero": "defer" if "divide" in expression_ops | filter_ops else None}


def _unit(expression, columns):
    unknown = {"name": None, "status": "unknown", "evidence_ids": []}
    if not isinstance(expression, dict):
        return unknown
    op = expression.get("op")
    if op == "column":
        return copy.deepcopy(columns.get((expression.get("table"), expression.get("column")), {}).get("unit", unknown))
    if op in ("is_missing", "eq", "lt", "lte", "gt", "gte", "and"):
        return {"name": "boolean", "status": "verified", "evidence_ids": []}
    if op in ("child_count", "divide", "datetime_diff"):
        return {"name": {"child_count": "count", "divide": "ratio", "datetime_diff": "ms"}[op], "status": "verified", "evidence_ids": []}
    if op == "literal":
        value = expression.get("value")
        if expression.get("logical_type") == "datetime":
            return unknown
        name = "boolean" if type(value) is bool else "dimensionless" if type(value) in (int, float) else "category" if isinstance(value, str) else None
        return {"name": name, "status": "verified" if name else "unknown", "evidence_ids": []}
    args = expression.get("args", [])
    units = [_unit(arg, columns) for arg in args] if isinstance(args, list) else []
    if op == "convert_unit":
        if units and units[0]["status"] == "verified":
            return {"name": expression.get("to_unit"), "status": "verified", "evidence_ids": copy.deepcopy(units[0]["evidence_ids"])}
        return unknown
    if op == "subtract" and units:
        status = "verified" if all(unit["status"] == "verified" for unit in units) else "declared" if all(unit["status"] in ("verified", "declared") for unit in units) else "unknown"
        return {"name": units[0]["name"] if status != "unknown" else None, "status": status,
            "evidence_ids": list(dict.fromkeys(eid for unit in units for eid in unit["evidence_ids"])) if status != "unknown" else []}
    return unknown


def _mapping(mapping, inventory, project_root):
    if not isinstance(inventory, dict):
        return {"passed": False, "status": "invalid", "errors": [_error("inventory_object", "inventory", "Inventory must be an object")], "mapping_version": None}, {}
    if mapping is None:
        return {"passed": True, "status": "not_requested", "errors": [], "mapping_version": inventory.get("mapping_version")}, {}
    checked = validate_semantic_mapping(mapping, inventory, project_root=project_root)
    ids = [value.get("id") for value in mapping.get("bindings", []) if isinstance(value, dict)] if isinstance(mapping, dict) else []
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(str(value) for value in ids)):
        checked = copy.deepcopy(checked)
        checked["passed"] = False
        checked["status"] = "invalid"
        checked["errors"].append(_error("semantic_mapping_id", "mapping.bindings", "Every supplied semantic binding needs a unique nonempty ID"))
    resolved = {value["id"]: value for value in mapping.get("bindings", []) if isinstance(value, dict) and isinstance(value.get("id"), str)} if isinstance(mapping, dict) else {}
    return checked, resolved


def validate_variable_intent(intent, inventory, semantic_mapping=None, *, project_root=ROOT):
    """Alias for complete binding validation; no source CSV is read or measured."""
    return bind_variable_intent(intent, inventory, semantic_mapping, project_root=project_root)


def bind_variable_intent(intent, inventory, semantic_mapping=None, *, project_root=ROOT):
    original = copy.deepcopy(intent)
    intent_sha = _sha(original)
    candidate_id = intent.get("id") if isinstance(intent, dict) else None
    record = {"version": INTENT_VERSION, "passed": False, "candidate_id": candidate_id,
        "intent": original, "intent_sha256": intent_sha, "plan": None, "materialized_plan_sha256": None,
        "definition_sha256": None, "definition_status": "invalid", "errors": [],
        "core_validation": None, "semantic_status": "not_requested", "code_materialization": None}
    errors = record["errors"]
    try:
        admission = assert_profile_admission(inventory, project_root=project_root)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        errors.append(_error("content_admission", "inventory.scope_admission", str(exc)))
        return _finish(record, inventory)
    if intent_sha is None:
        errors.append(_error("intent_not_json", "$", "Intent must be finite canonical JSON"))
        return _finish(record, inventory)
    schema = variable_intent_schema()
    expression_errors = _ast_errors(intent.get("expression")) if isinstance(intent, dict) and "expression" in intent else []
    filter_errors = _ast_errors(intent["population_filter"], "population_filter") if isinstance(intent, dict) and intent.get("population_filter") is not None else []
    if any(error["code"] == "ast_limit" for error in expression_errors + filter_errors):
        errors.extend(expression_errors + filter_errors)
        return _finish(record, inventory)
    raw_errors = list(jsonschema.Draft202012Validator(schema).iter_errors(intent))
    for error in raw_errors:
        parts = list(error.absolute_path)
        if parts and parts[0] == "expression" and expression_errors:
            continue
        if parts and parts[0] == "population_filter" and filter_errors:
            continue
        if error.validator == "additionalProperties" and not parts and isinstance(intent, dict):
            for field in sorted(set(intent) - set(schema["properties"])):
                value = intent[field]
                expected = _required_policies(intent.get("expression"), intent.get("population_filter")) if field == "missing_policy" else None
                if field == "unit" and not expression_errors:
                    physical_columns = {(table["id"], column["name"]): column for table in inventory["tables"] for column in table["columns"]}
                    expected = _unit(intent.get("expression"), physical_columns)
                if isinstance(value, dict) and value:
                    for child in sorted(value):
                        errors.append(_error("intent_forbidden_field", field + "." + str(child), "This field belongs to code materialization and is forbidden in model intent", received_value=copy.deepcopy(value[child]), code_owned=True, field_allowed=False, expected_code_value=expected.get(child) if expected else None))
                else:
                    errors.append(_error("intent_forbidden_field", field, "Code-owned or unregistered field is forbidden in model intent", code_owned=True, field_allowed=False))
        elif error.validator == "required":
            for field in error.validator_value:
                if isinstance(error.instance, dict) and field not in error.instance:
                    path = _path(parts + [field])
                    if not any(item["path"] == path for item in errors):
                        errors.append(_error("intent_required_field", path, "Required variable intent field is missing", required_field=field))
        else:
            allowed = {"const": error.validator_value} if error.validator == "const" else error.validator_value if error.validator == "enum" else {"schema": copy.deepcopy(error.schema)}
            errors.append(_error("intent_output_schema", _path(parts), error.message, allowed_values=allowed))
    errors.extend(expression_errors + filter_errors)
    if errors:
        return _finish(record, inventory)
    tables = {table["id"]: table for table in inventory["tables"]}
    columns = {(table["id"], column["name"]): column for table in inventory["tables"] for column in table["columns"]}
    table = tables.get(intent["grain_table"])
    if table is None:
        errors.append(_error("unknown_table", "grain_table", "Analysis grain must select an actual included physical table"))
        return _finish(record, inventory)
    mapping_check, bindings = _mapping(semantic_mapping, inventory, project_root)
    semantic_ids = intent["semantic_mapping_ids"]
    if len(set(semantic_ids)) != len(semantic_ids):
        errors.append(_error("semantic_mapping_duplicate", "semantic_mapping_ids", "Semantic references must be unique"))
    for index, mapping_id in enumerate(semantic_ids):
        if mapping_id not in bindings:
            errors.append(_error("semantic_mapping_reference", f"semantic_mapping_ids[{index}]", "Semantic ID must resolve to an exact supplied binding", supplied_ids=sorted(bindings)))
    if semantic_ids and not mapping_check["passed"]:
        errors.append(_error("semantic_mapping_invalid", "semantic_mapping_ids", "Referenced semantic proposals did not validate", mapping_errors=copy.deepcopy(mapping_check["errors"])))
    unit_proposal = intent["unit_proposal"]
    if unit_proposal is not None:
        evidence_ids = {fact["id"] for fact in inventory.get("evidence", [])}
        for index, evidence_id in enumerate(unit_proposal["evidence_ids"]):
            if evidence_id not in evidence_ids:
                errors.append(_error("unit_proposal_evidence_reference", f"unit_proposal.evidence_ids[{index}]", "Unit proposal can cite only supplied evidence IDs; no cited fact upgrades the unit automatically"))
    if errors:
        return _finish(record, inventory)
    semantic_refs = [copy.deepcopy(bindings[mapping_id]) for mapping_id in semantic_ids]
    semantic_status = "not_requested" if not semantic_refs else "verified" if all(ref.get("status") == "verified" for ref in semantic_refs) else "proposed_review_only"
    plan = {"id": intent["id"], "description": intent["description"], "purpose": intent["purpose"],
        "expression": copy.deepcopy(intent["expression"]), "grain": {"table": table["id"], "keys": copy.deepcopy(table["primary_key"])},
        "population": {"table": table["id"], "filter": copy.deepcopy(intent["population_filter"])}, "group_by": copy.deepcopy(intent["group_by"]),
        "missing_policy": _required_policies(intent["expression"], intent["population_filter"]),
        "unit": _unit(intent["expression"], columns), "schema_version": inventory["schema_version"],
        "source_version": inventory["source_version"], "mapping_version": inventory.get("mapping_version"), "plan_version": PLAN_VERSION,
        "availability": copy.deepcopy(AVAILABILITY), "required_evidence": copy.deepcopy(intent["required_evidence"])}
    checked = validate_plan(plan, inventory)
    # Source declarations are compiler output, not a model lineage claim.
    plan["source_columns"] = copy.deepcopy(checked["identity"]["source_columns"]) if checked["passed"] else []
    checked = validate_plan(plan, inventory)
    static_validation = copy.deepcopy(checked)
    measurement_semantics_errors = _unavailable_missing_errors(intent["expression"])
    if intent["population_filter"] is not None:
        measurement_semantics_errors.extend(_unavailable_missing_errors(intent["population_filter"], "population_filter"))
    if measurement_semantics_errors:
        checked["errors"].extend(measurement_semantics_errors)
        checked.update(passed=False, definition_status="invalid", definition_sha256=None, identity=None, identity_sha256=None)
    for error in checked["errors"]:
        error["materialized_plan_path"] = error["path"]
        if error["path"].startswith("grain."):
            error["path"] = "grain_table"
        elif error["path"].startswith("population.filter"):
            error["path"] = "population_filter" + error["path"][len("population.filter"):]
        error["intent_path"] = error["path"]
    source_contexts = [{"table": ref["table"], "column": ref["column"],
        "logical_type": columns[(ref["table"], ref["column"])]["logical_type"],
        "unit": copy.deepcopy(columns[(ref["table"], ref["column"])]["unit"]),
        "missing_tokens": copy.deepcopy(columns[(ref["table"], ref["column"])].get("missing_tokens", [""]))}
        for ref in plan["source_columns"]]
    materialization = {"version": INTENT_VERSION, "owner": "verified_code", "intent_sha256": intent_sha,
        "materialized_plan_sha256": _sha(plan), "definition_sha256": checked["definition_sha256"],
        "computational_policy_version": PLAN_VERSION, "inferred_type": checked["inferred_type"],
        "source_contexts": source_contexts, "admission_identity_sha256": admission["admission_identity_sha256"],
        "schema_version": inventory["schema_version"], "source_version": inventory["source_version"], "mapping_version": inventory.get("mapping_version"),
        "semantic_mapping_refs": semantic_refs, "semantic_status": semantic_status,
        "description_status": "unverified_author_proposal_text",
        "measurement_semantics_policy": "preserve_current_measurement_and_unknown_states",
        "measurement_semantics_passed": not measurement_semantics_errors,
        "static_type_validation_passed": static_validation["passed"],
        "unit_proposal": copy.deepcopy(unit_proposal), "unit_proposal_status": "unverified_review_only" if unit_proposal else "not_requested",
        "model_original_intent_unchanged": True, "model_explicit_metadata_repaired": False,
        "code_owned_fields": ["grain.keys", "population.table", "missing_policy", "unit", "source_columns", "schema_version", "source_version", "mapping_version", "plan_version", "availability", "inferred_type"],
        "definition_measurement_IF_decisions_separate": True}
    record.update(passed=checked["passed"], plan=plan, materialized_plan_sha256=_sha(plan),
        definition_sha256=checked["definition_sha256"], definition_status=checked["definition_status"],
        errors=copy.deepcopy(checked["errors"]), core_validation=checked, semantic_status=semantic_status, code_materialization=materialization)
    record["static_validation"] = static_validation
    return _finish(record, inventory)


def bind_variable_intents(intents, inventory, semantic_mapping=None, *, project_root=ROOT):
    """Aligned code plans, existing-validator adapters and immutable intent lineage."""
    if not isinstance(intents, list):
        return {"passed": False, "version": INTENT_VERSION, "variables": [], "bindings": [],
            "validation": {"passed": False, "version": OUTPUT_VERSION, "schema_errors": [_error("intent_list", "variables", "Variable intents must be an array", wrong_value=copy.deepcopy(intents), allowed_values={"type": "array", "minItems": 1}, actual_path="variables", required_fields=[])], "mapping": None, "variables": []}}
    bindings = [bind_variable_intent(intent, inventory, semantic_mapping, project_root=project_root) for intent in intents]
    ids = [intent.get("id") if isinstance(intent, dict) else None for intent in intents]
    variables, checks, schema_errors = [], [], []
    for index, (intent, binding) in enumerate(zip(intents, bindings)):
        if isinstance(ids[index], str) and ids.count(ids[index]) > 1:
            duplicate = _error("duplicate_plan", "id", "Variable lineage IDs must be unique")
            binding["passed"] = False; binding["definition_status"] = "invalid"; binding["definition_sha256"] = None
            binding["errors"].append(duplicate)
            if binding["code_materialization"] is not None:
                binding["code_materialization"]["definition_sha256"] = None
                binding["code_materialization"]["binding_status"] = "invalid_duplicate_id"
            _finish(binding, inventory)
        if binding["plan"] is not None:
            variable = copy.deepcopy(binding["plan"])
        else:
            # A rejected intent is never treated as an executable old plan.
            # A complete source context keeps the frozen validator safe when
            # the original AST can be retained. Otherwise a dedicated invalid
            # adapter keeps the original expression in a separate field.
            variable = _rejected_adapter(intent, inventory)
            binding["rejected_adapter"] = copy.deepcopy(variable)
            binding["rejected_adapter_sha256"] = _sha(variable)
            binding["original_failed_proposal_sha256"] = binding["intent_sha256"]
        variables.append(variable)
        checked = copy.deepcopy(binding["core_validation"] or validate_plan(variable, inventory))
        checked.update(passed=binding["passed"], definition_status=binding["definition_status"],
            definition_sha256=binding["definition_sha256"], errors=copy.deepcopy(binding["errors"]),
            candidate_id=ids[index], proposal_sha256=_sha(variable), plan_id=ids[index], index=index,
            intent_sha256=binding["intent_sha256"], code_materialized=binding["plan"] is not None)
        if not binding["passed"]:
            checked["original_failed_proposal_sha256"] = binding["intent_sha256"]
            checked["adapter_sha256"] = _sha(variable)
        if not checked["passed"]:
            checked["identity"] = None; checked["identity_sha256"] = None
        checks.append(checked)
    mapping, _ = _mapping(semantic_mapping, inventory, project_root)
    passed = bool(bindings) and mapping["passed"] and all(binding["passed"] for binding in bindings)
    if not intents:
        schema_errors.append(_error("intent_list_empty", "variables", "At least one variable intent is required", wrong_value=[], allowed_values={"type": "array", "minItems": 1}, actual_path="variables", required_fields=[]))
    return {"passed": passed, "version": INTENT_VERSION, "variables": variables, "bindings": bindings,
        "validation": {"passed": passed, "version": OUTPUT_VERSION, "schema_errors": schema_errors, "mapping": mapping, "variables": checks}}


def _rejected_adapter(intent, inventory):
    """Explicitly non-executable input for old validators, retaining the intent.

Even if its AST is statically computable, outer passed=false prohibits reading
source rows, issuing a definition SHA, or admitting a rule execution reference.
This adapter is compiler output, never a replacement for the model response.
"""
    original = copy.deepcopy(intent)
    value = intent if isinstance(intent, dict) else {}
    tables = {table["id"]: table for table in inventory.get("tables", [])} if isinstance(inventory, dict) else {}
    columns = {(table["id"], column["name"]): column for table in tables.values() for column in table["columns"]}
    table = tables.get(value.get("grain_table")) if isinstance(value.get("grain_table"), str) else None
    expression = value.get("expression")
    can_retain = table is not None and isinstance(expression, dict) and not _ast_errors(expression)
    filter_node = value.get("population_filter")
    filter_safe = filter_node is None or isinstance(filter_node, dict) and not _ast_errors(filter_node)
    expression = copy.deepcopy(expression) if can_retain else None
    population_filter = copy.deepcopy(filter_node) if can_retain and filter_safe else None
    return {"id": value.get("id"), "description": value.get("description"), "purpose": "feature_calculation",
        "expression": expression, "grain": {"table": table["id"] if table else None, "keys": copy.deepcopy(table["primary_key"]) if table else []},
        "population": {"table": table["id"] if table else None, "filter": population_filter}, "group_by": [],
        "missing_policy": _required_policies(expression, population_filter), "unit": _unit(expression, columns),
        "schema_version": inventory.get("schema_version") if isinstance(inventory, dict) else None,
        "source_version": inventory.get("source_version") if isinstance(inventory, dict) else None,
        "mapping_version": inventory.get("mapping_version") if isinstance(inventory, dict) else None,
        "plan_version": PLAN_VERSION, "availability": copy.deepcopy(AVAILABILITY), "source_columns": [],
        "adapter_kind": "rejected_intent_non_executable", "execution_allowed": False,
        "original_intent": original, "original_intent_sha256": _sha(original),
        "original_expression": copy.deepcopy(value.get("expression")), "expression_retained": can_retain,
        "code_adapter_is_model_repair": False}
