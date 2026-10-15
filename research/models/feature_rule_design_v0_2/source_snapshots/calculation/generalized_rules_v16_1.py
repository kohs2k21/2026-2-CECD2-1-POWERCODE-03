"""Rule validation policy 3.0.1: trace numeric thresholds through variable ASTs.

The frozen v16 output contract, subject definitions and source measurement
policies are reused unchanged. This extension only closes a confirmed bypass:
an immutable boolean variable can hide an unverified numeric comparison from
the rule's outer condition. Model responses and input units are never rewritten.
"""
from __future__ import annotations

import copy

import generalized_rules_v16 as frozen

VERSION = "3.0.1"
OUTPUT_VERSION = frozen.VERSION
POLICY_SCOPE = "expanded_numeric_threshold"
ROOT = frozen.ROOT
AVAILABILITY = frozen.AVAILABILITY
CONTEXT_FIELDS = frozen.CONTEXT_FIELDS
build_subject_registry = frozen.build_subject_registry
public_subject_registry = frozen.public_subject_registry
validate_review_request = frozen.validate_review_request
_resolve_subject = frozen._resolve_subject
_context = frozen._context
_raw_plan = frozen._raw_plan
_nodes = frozen._nodes
_numeric_literal = frozen._numeric_literal


def _numeric_trace(expression, path):
    return [{"path": location, "value": copy.deepcopy(node["value"])}
            for node, location in _nodes(expression, path=path) if _numeric_literal(node)]


def validate_rule(rule, inventory, registry, *, project_root=ROOT):
    result = frozen.validate_rule(rule, inventory, registry, project_root=project_root)
    original = _numeric_trace(rule.get("condition") if isinstance(rule, dict) else None, "condition")
    predicate = result.get("predicate_plan")
    expanded = _numeric_trace(predicate.get("expression") if isinstance(predicate, dict) else None,
                              "predicate_plan.expression")
    policy = rule.get("threshold_policy") if isinstance(rule, dict) else None
    hidden = bool(expanded) and not original
    result["version"] = VERSION
    result["output_contract_version"] = OUTPUT_VERSION
    result["validation_policy_scope"] = POLICY_SCOPE
    result["numeric_threshold_trace"] = {
        "original_condition_numeric_literals": original,
        "expanded_condition_numeric_literals": expanded,
        "numeric_literal_hidden_by_subject_definition": hidden,
        "threshold_policy_supplied": policy is not None,
        "variable_definition_or_input_unit_rewritten": False,
        "policy_with_existing_business_or_baseline_proof": "frozen_v16_validation_preserved",
    }
    if hidden and policy is None:
        reason = ("Expanded variable definition contains a numeric threshold; its measurement proves the calculation, "
                  "but proposing HR/SR requires a verified baseline or confirmed business condition for that threshold")
        # This one old error depended on the bypassed observation eligibility.
        # All type, source, evidence and structural failures remain unchanged.
        errors = [copy.deepcopy(error) for error in result["errors"] if error.get("code") != "missed_supported_proposal"]
        result["eligibility"] = {"decision": "deferred", "basis": "insufficient_evidence", "reason": reason, "evidence_ids": []}
        if rule.get("decision") == "proposed":
            errors.append({"code": "rule_ineligible_proposal", "path": "condition", "message": reason})
        result["errors"] = errors
        result["passed"] = not errors
    return result
