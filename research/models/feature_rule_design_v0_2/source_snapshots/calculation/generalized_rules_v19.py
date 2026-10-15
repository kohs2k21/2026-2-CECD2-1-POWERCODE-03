"""New rule policy: trigger polarity, role completeness and failed references.

The explicit compatibility bridge copies a NEW rule's version into the frozen
validator's contract. It never changes the expression, unit or old model JSON.
"""
from __future__ import annotations

import copy

import generalized_rule_subjects_v19 as subjects
from generalized_content_policy_v16 import assert_profile_admission
from generalized_measurement_v19 import digest, validate_plan
import re

VERSION = "4.0.0"
VALIDATION_POLICY_VERSION = "4.2.0"
POLICY_SCOPE = "trigger_direction_condition_vs_review_and_source_usable_observation"
ROOT = subjects.ROOT


def _detailed_errors(errors, proposal, registry, *, report=None):
    """Keep validator codes while exposing the actual model field and values."""
    for error in errors:
        path = str(error.get("path", ""))
        error.setdefault("original_path", path)
        actual = path if path.startswith("/") else "/" + path.replace(".", "/").replace("[", "/").replace("]", "")
        if path == "validation_report" or path.startswith("predicate_plan") or path.startswith("shared_context"):
            actual = "/condition"
        error.setdefault("actual_path", actual)
        value = proposal
        for part in actual.strip("/").split("/"):
            try: value = value[int(part)] if isinstance(value, list) else value[part]
            except (KeyError, TypeError, IndexError, ValueError): value = None; break
        error.setdefault("wrong_value", copy.deepcopy(value))
        if "allowed_values" not in error:
            if "sha256" in path:
                ref = proposal.get("input", {})
                expected = [s.get("definition_sha256") for s in registry.get("subjects", []) if s.get("definition_status") == "valid"
                    and (s["reference"].get("variable_id") == ref.get("variable_id") if ref.get("kind") == "variable" else
                         s["reference"].get("table") == ref.get("table") and s["reference"].get("column") == ref.get("column"))]
                error["allowed_values"] = [v for v in expected if v]
            elif path.startswith("evidence_ids"):
                error["allowed_values"] = copy.deepcopy((report or {}).get("eligibility", {}).get("evidence_ids", []))
            elif error.get("code") == "inverted_constraint_trigger":
                from generalized_trigger_semantics_v19 import _missing_orientation
                expanded = (report or {}).get("predicate_plan", {}).get("expression")
                orientation = _missing_orientation(expanded)
                error["allowed_values"] = [{"op": "is_missing", "args": [orientation[0]]}] if orientation else ["Confirmed violation_when_true predicate"]
            else: error["allowed_values"] = copy.deepcopy(error.get("allowed_fields", error.get("required_fields")))
        error.setdefault("required_fields", [])
    return errors


def build_subject_registry(inventory, variables, validation, measurements, *, project_root=ROOT, bindings=None):
    registry = subjects.build_subject_registry(inventory, variables, validation, measurements, project_root=project_root)
    registry["version"] = VERSION
    for index, item in enumerate(s for s in registry["subjects"] if s["reference"].get("kind") == "variable"):
        binding = bindings[index] if bindings and index < len(bindings) else None
        item["intent_sha256"] = binding.get("intent_sha256") if binding else None
        item["code_materialization"] = copy.deepcopy(binding.get("code_materialization")) if binding else None
        if binding and item["definition_status"] == "invalid":
            item["rejected_adapter_sha256"] = item["proposal_sha256"]
            item["proposal_sha256"] = binding["intent_sha256"]
            item["failed_proposal_origin"] = "unchanged_model_intent"
        item["execution_reference"] = copy.deepcopy(item["reference"]) if item["definition_status"] == "valid" else None
        item["failed_proposal_reference"] = ({"kind": "failed_proposal", "variable_id": item["reference"]["variable_id"],
             "proposal_sha256": item["proposal_sha256"]} if item["definition_status"] == "invalid" else None)
    for item in registry["subjects"]:
        if item["reference"].get("kind") == "raw_column":
            item["execution_reference"] = copy.deepcopy(item["reference"]) if item["definition_status"] == "valid" else None
    registry["registry_sha256"] = digest(registry["subjects"])
    return registry


def public_subject_registry(registry):
    if registry is None: raise ValueError("Rules require a code-owned subject registry")
    public = subjects.public_subject_registry(registry)
    for original, item in zip(registry["subjects"], public["subjects"]):
        item.update(execution_reference=copy.deepcopy(original.get("execution_reference")),
            failed_proposal_reference=copy.deepcopy(original.get("failed_proposal_reference")),
            intent_sha256=original.get("intent_sha256"))
        if original["definition_status"] == "valid":
            item["definition"] = {k: copy.deepcopy(original["plan"].get(k)) for k in
                ("expression", "source_columns", "purpose", "description")}
            item["definition_description_status"] = "unverified_author_proposal_text"
        else:
            item["definition"] = None
        proof = (original.get("measurement") or {}).get("source_evidence")
        item["measurement_evidence_id"] = proof.get("id") if proof else None
    return public


def _schema_report(value, field):
    from generalized_protocol_v17 import rule_schema, schema_errors
    schema = rule_schema()
    single = copy.deepcopy(schema["properties"][field]["items"])
    single["$defs"] = schema["$defs"]
    return schema_errors(value, single)


def _expanded_numeric_validation(rule, inventory, registry, *, project_root=ROOT):
    """Retain the frozen hidden-threshold protection with the new evaluator."""
    result = subjects.validate_rule(rule, inventory, registry, project_root=project_root)
    def trace(expression, path):
        return [{"path": location, "value": copy.deepcopy(node["value"])}
                for node, location in subjects._nodes(expression, path=path) if subjects._numeric_literal(node)]
    original = trace(rule.get("condition") if isinstance(rule, dict) else None, "condition")
    predicate = result.get("predicate_plan")
    expanded = trace(predicate.get("expression") if isinstance(predicate, dict) else None,
                     "predicate_plan.expression")
    policy = rule.get("threshold_policy") if isinstance(rule, dict) else None
    hidden = bool(expanded) and not original
    result["numeric_threshold_trace"] = {
        "original_condition_numeric_literals": original,
        "expanded_condition_numeric_literals": expanded,
        "numeric_literal_hidden_by_subject_definition": hidden,
        "threshold_policy_supplied": policy is not None,
        "variable_definition_or_input_unit_rewritten": False,
        "policy_with_existing_business_or_baseline_proof": "v19_source_verified_validation_preserved"}
    if hidden and policy is None:
        reason = ("Expanded variable definition contains a numeric threshold; its measurement proves the calculation, "
                  "but proposing HR/SR requires a verified baseline or confirmed business condition for that threshold")
        errors = [copy.deepcopy(error) for error in result["errors"] if error.get("code") != "missed_supported_proposal"]
        result["eligibility"] = subjects._deferred(reason)
        if rule.get("decision") == "proposed":
            errors.append({"code": "rule_ineligible_proposal", "path": "condition", "message": reason})
        result.update(errors=errors, passed=not errors)
    return result


def validate_rule(rule, inventory, registry, *, project_root=ROOT):
    from generalized_trigger_semantics_v19 import validate_trigger_semantics, trigger_contract
    assert_profile_admission(inventory, project_root=project_root)
    errors = _schema_report(rule, "rules")
    if errors:
        return {"passed": False, "version": VERSION, "errors": errors, "eligibility": subjects._deferred(),
            "condition_output_context": None, "executable": False, "counts_as_valid_rule": False}
    bridge = copy.deepcopy(rule)
    bridge["version"] = "3.0.0"
    report = _expanded_numeric_validation(bridge, inventory, registry, project_root=project_root)
    report["version"] = VERSION
    report["output_contract_version"] = VERSION
    report["validation_policy_scope"] = POLICY_SCOPE
    report["validation_policy_version"] = VALIDATION_POLICY_VERSION
    report["compatibility_bridge"] = {"from_version": VERSION, "to_validator_contract": "3.0.0",
        "original_proposal_sha256": digest(rule), "bridge_sha256": digest(bridge),
        "expression_changed": False, "input_context_changed": False, "historical_output_changed": False}
    # Never label a rejected numeric predicate as boolean merely because it is
    # intended to be a condition. Preserve the actual inferred result type.
    predicate = report.get("predicate_plan")
    if predicate:
        checked = validate_plan(predicate, inventory)
        report["condition_output_context"] = {"inferred_type": checked.get("inferred_type"),
            "unit": copy.deepcopy(predicate["unit"]), "missing_policy": copy.deepcopy(predicate["missing_policy"]),
            "type_validated": checked["passed"]}
    numeric = report.get("numeric_threshold_trace", {}).get("expanded_condition_numeric_literals", [])
    if numeric and rule.get("threshold_policy") is None:
        report["errors"].append({"code": "unknown_condition_requires_review", "path": "/condition",
            "wrong_value": copy.deepcopy(rule["condition"]), "allowed_values": "conditionless condition_evidence review request",
            "message": "A numerical threshold without a verified criterion cannot be kept as a condition, even under deferred"})
    semantics = validate_trigger_semantics(rule, inventory, registry, validation_report=report, project_root=project_root)
    report["trigger_semantics"] = semantics
    report["trigger_contract"] = trigger_contract(rule["role"])
    report["errors"].extend(copy.deepcopy(semantics.get("errors", [])))
    _detailed_errors(report["errors"], rule, registry, report=report)
    report["passed"] = not report["errors"]
    report["executable"] = report["passed"] and rule["decision"] == "proposed" and semantics.get("executable", False)
    report["counts_as_valid_rule"] = report["executable"]
    return report


def validate_review_request(review, inventory, registry, *, project_root=ROOT):
    assert_profile_admission(inventory, project_root=project_root)
    errors = _schema_report(review, "review_requests")
    result = {"passed": False, "version": VERSION, "errors": errors, "state": "review_requested",
        "executable": False, "counts_as_valid_rule": False, "input_definition_sha256": None,
        "reviewed_proposal_sha256": None}
    if errors: return result
    reference = review["input"]
    if review["review_kind"] == "failed_proposal":
        if reference.get("kind") != "failed_proposal":
            errors.append({"code": "failed_reference_kind", "path": "/input/kind", "wrong_value": reference.get("kind"),
                "allowed_values": ["failed_proposal"], "message": "Failure review is separate from executable definition references"})
        elif registry.get("registry_sha256") != digest(registry.get("subjects", [])):
            errors.append({"code": "registry_integrity", "path": "/input", "message": "Code registry changed"})
        else:
            matches = [s for s in registry["subjects"] if s.get("failed_proposal_reference") == reference and s["definition_status"] == "invalid"]
            if len(matches) != 1:
                errors.append({"code": "proposal_sha256", "path": "/input/proposal_sha256", "wrong_value": reference.get("proposal_sha256"),
                    "allowed_values": [s["proposal_sha256"] for s in registry["subjects"] if s["reference"].get("variable_id") == reference["variable_id"] and s["definition_status"] == "invalid"],
                    "message": "Failed reviews require the exact failed proposal ID and original proposal SHA"})
            else: result["reviewed_proposal_sha256"] = reference["proposal_sha256"]
    else:
        item, failures = subjects._resolve_subject(reference, inventory, registry)
        errors.extend(failures)
        if item: result["input_definition_sha256"] = item["definition_sha256"]
    _detailed_errors(errors, review, registry, report=result)
    result["passed"] = not errors
    return result
