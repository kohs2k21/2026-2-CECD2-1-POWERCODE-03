"""Source-bound SR closure, separate from IF and operational action contracts.

The code identifies already defined missing/categorical boolean diagnostics.
It neither invents candidates nor edits a model response. Exact observations
support an observational SR; a known unobserved predicate stays in a deferred
rule; an unsupported numeric criterion belongs to a conditionless review.
Existing rule and evidence validation remains an independent mandatory gate.
"""
from __future__ import annotations

from collections import Counter
import copy
from pathlib import Path

from generalized_content_policy_v16 import assert_profile_admission
from generalized_measurement_v19 import canonical, digest, measure_plan, validate_plan
from generalized_roles_v19 import evaluate_observation

ROOT = Path(__file__).resolve().parent
VERSION = "1.1.0"
POLICY_SCOPE = "source_bound_observed_unobserved_and_unknown_criterion_SR"
SCOPE_FIELDS = ("grain", "population", "group_by", "schema_version", "source_version", "mapping_version", "availability")


def _nodes(node, depth=0):
    if depth > 16 or not isinstance(node, dict):
        return
    yield node
    for child in node.get("args", []) if isinstance(node.get("args"), list) else []:
        yield from _nodes(child, depth + 1)


def _column(node):
    return (isinstance(node, dict) and set(node) == {"op", "table", "column"}
            and node.get("op") == "column" and isinstance(node.get("table"), str)
            and isinstance(node.get("column"), str))


def _unwrap_true(expression):
    """Compare semantics without rewriting or returning a modified proposal."""
    while (isinstance(expression, dict) and expression.get("op") == "eq"
           and isinstance(expression.get("args"), list) and len(expression["args"]) == 2):
        args = expression["args"]
        indices = [i for i, child in enumerate(args) if isinstance(child, dict)
                   and set(child) == {"op", "value"} and child.get("op") == "literal"
                   and type(child.get("value")) is bool and child["value"] is True]
        if len(indices) != 1:
            break
        expression = args[1 - indices[0]]
    return expression


def diagnostic_expression(expression):
    """Recognize supported computations, never descriptions or candidate IDs.

    Numeric comparisons, raw boolean columns and computed unavailability are
    deliberately outside this closure. Their meanings need separate evidence.
    """
    expression = _unwrap_true(expression)
    if not isinstance(expression, dict):
        return None
    args = expression.get("args")
    if expression.get("op") == "is_missing" and isinstance(args, list) and len(args) == 1 and _column(args[0]):
        return copy.deepcopy(expression)
    if expression.get("op") == "eq" and isinstance(args, list) and len(args) == 2:
        if any(_column(args[i]) and isinstance(args[1 - i], dict)
               and set(args[1 - i]) == {"op", "value"} and args[1 - i].get("op") == "literal"
               and isinstance(args[1 - i].get("value"), str) for i in (0, 1)):
            return copy.deepcopy(expression)
    if expression.get("op") == "and" and isinstance(args, list) and len(args) == 2:
        if all(diagnostic_expression(child) is not None for child in args):
            return copy.deepcopy(expression)
    return None


def _normalized(expression):
    expression = _unwrap_true(expression)
    if not isinstance(expression, dict):
        return expression
    result = copy.deepcopy(expression)
    if isinstance(result.get("args"), list):
        result["args"] = [_normalized(child) for child in result["args"]]
        if result.get("op") in {"eq", "and"}:
            result["args"] = sorted(result["args"], key=canonical)
    return result


def _numeric_criterion(expression):
    return any(node.get("op") in {"eq", "lt", "lte", "gt", "gte"}
               and isinstance(node.get("args"), list)
               and any(isinstance(child, dict) and child.get("op") == "literal"
                       and type(child.get("value")) in {int, float} for child in node["args"])
               for node in _nodes(expression))


def _error(code, path, message, wrong_value=None, allowed_values=None, required_fields=None, **context):
    return {"code": code, "path": path, "actual_path": path, "message": message,
            "wrong_value": copy.deepcopy(wrong_value), "allowed_values": copy.deepcopy(allowed_values),
            "required_fields": copy.deepcopy(required_fields or []), **copy.deepcopy(context)}


def _intact_registry(registry):
    try:
        return (isinstance(registry, dict) and isinstance(registry.get("subjects"), list)
                and all(isinstance(s, dict) and isinstance(s.get("reference"), dict) for s in registry["subjects"])
                and registry.get("registry_sha256") == digest(registry["subjects"]))
    except (ValueError, TypeError):
        return False


def build_sr_obligations(registry, inventory, *, project_root=ROOT):
    """Build code-owned input obligations after admitted source measurement.

    Replay uses the guarded bounded-statistics path. No model-created counts,
    action contract, historical baseline or IF variability is substituted for
    observation evidence. Rejected proofs fail closed while their IDs remain.
    """
    assert_profile_admission(inventory, project_root=project_root)
    result = {"version": VERSION, "policy_scope": POLICY_SCOPE, "owner": "verified_code",
              "registry_sha256": registry.get("registry_sha256") if isinstance(registry, dict) else None,
              "schema_version": inventory.get("schema_version"), "source_version": inventory.get("source_version"),
              "passed": False, "errors": [], "obligations": [], "forces_rule_for_every_variable": False,
              "candidate_ids_used_as_truth": False, "action_contract_required_for_observational_SR": False,
              "historical_baseline_required_for_observational_SR": False, "anomaly_execution": "not_run"}
    if not _intact_registry(registry):
        result["errors"].append(_error("sr_registry_integrity", "/registry", "Code-owned source registry is not intact"))
    elif any(registry.get(field) != inventory.get(field) for field in ("schema_version", "source_version")):
        result["errors"].append(_error("sr_registry_stale", "/registry", "Registry belongs to another source/schema version"))
    else:
        for index, subject in enumerate(registry["subjects"]):
            reference = subject["reference"]
            if reference.get("kind") != "variable":
                continue
            plan = subject.get("plan") or {}
            diagnostic = diagnostic_expression(plan.get("expression"))
            item = {"variable_id": reference.get("variable_id"), "proposal_sha256": subject.get("proposal_sha256"),
                    "definition_sha256": subject.get("definition_sha256"), "definition_status": subject.get("definition_status"),
                    "execution_reference": copy.deepcopy(subject.get("execution_reference", reference))
                        if subject.get("definition_status") == "valid" else None,
                    "failed_proposal_reference": copy.deepcopy(subject.get("failed_proposal_reference"))
                        if subject.get("definition_status") == "invalid" else None,
                    "measurement_status": subject.get("measurement_status"), "measurement_record_sha256": None,
                    "measurement_evidence_id": None, "measurement_replayed": False,
                    "diagnostic_expression": diagnostic, "condition_when_true": "diagnostic_trigger",
                    "source_columns": [],
                    "input_unit": copy.deepcopy(plan.get("unit")), "input_missing_policy": copy.deepcopy(plan.get("missing_policy")),
                    "scope": {field: copy.deepcopy(plan.get(field)) for field in SCOPE_FIELDS},
                    "state": "invalid_definition", "expected_SR_decision": "deferred",
                    "required_artifact": "failed_proposal_review", "reason": "Invalid proposals cannot be executable definition references"}
            if subject.get("definition_status") == "invalid":
                if item["failed_proposal_reference"] is None:
                    item["failed_proposal_reference"] = {"kind": "failed_proposal", "variable_id": item["variable_id"],
                                                         "proposal_sha256": item["proposal_sha256"]}
            else:
                checked = validate_plan(plan, inventory)
                definition = checked.get("definition_sha256", checked.get("identity_sha256"))
                valid = (checked.get("passed") is True and subject.get("definition_sha256") == definition
                         and reference.get("definition_sha256") == definition
                         and subject.get("inferred_type") == checked.get("inferred_type"))
                if not valid:
                    result["errors"].append(_error("sr_definition_binding", f"/registry/subjects/{index}/definition_sha256",
                        "Registered definition/type no longer matches the exact current source", subject.get("definition_sha256"), [definition]))
                    item.update(state="rejected_measurement", required_artifact="measurement_evidence_review",
                                reason="Definition proof failed; source-bound rules cannot be promoted")
                else:
                    item["source_columns"] = copy.deepcopy(checked.get("identity", {}).get("source_columns", []))
                    measurement = subject.get("measurement")
                    observation = evaluate_observation(plan, inventory, measurement, project_root=project_root)
                    replay_status = observation["measurement_status"]
                    exact = observation["measurement_verified"] and subject.get("measurement_status") == replay_status
                    item["observation_support"] = copy.deepcopy(observation)
                    if exact:
                        item.update(measurement_replayed=True, measurement_record_sha256=digest(measurement),
                                    measurement_evidence_id=(measurement.get("source_evidence") or {}).get("id"))
                    if not exact:
                        item.update(state="rejected_measurement", required_artifact="measurement_evidence_review",
                                    reason="Measurement status/proof did not match guarded code replay; no observation claim is allowed")
                        result["errors"].append(_error("sr_measurement_binding", f"/registry/subjects/{index}/measurement",
                            item["reason"], subject.get("measurement_status"), [replay_status]))
                    elif checked.get("inferred_type") == "boolean" and diagnostic is not None:
                        observed = observation["usable_observation"]
                        item.update(state="known_observed_diagnostic" if observed else "known_unobserved_diagnostic",
                            expected_SR_decision="proposed" if observed else "deferred",
                            required_artifact="exact_observational_SR" if observed else "exact_condition_deferred_SR",
                            reason=("This defined diagnostic has exact code-observed boolean outcomes; no action contract or historical baseline is needed"
                                    if observed else "The exact diagnostic predicate is known but has no code-observed usable boolean outcomes"))
                    elif checked.get("inferred_type") == "boolean" and _numeric_criterion(plan.get("expression")):
                        item.update(state="numeric_criterion_requires_evidence", expected_SR_decision="deferred_or_verified_proposed",
                            required_artifact="conditionless_criterion_review_or_verified_threshold_SR",
                            reason="Observed numeric comparison results do not establish that the numeric criterion is justified")
                    else:
                        item.update(state="no_mandatory_diagnostic", expected_SR_decision="explicit_role_decision",
                            required_artifact=None, reason="This variable does not itself define a supported missing/categorical diagnostic or numeric criterion")
            item["obligation_sha256"] = digest(item)
            result["obligations"].append(item)
    result["passed"] = not result["errors"]
    result["state_counts"] = dict(Counter(item["state"] for item in result["obligations"]))
    result["obligations_sha256"] = digest(result)
    return result


def _condition_matches(report, obligation):
    plan = report.get("predicate_plan") or {}
    try:
        return (_normalized(plan.get("expression")) == _normalized(obligation["diagnostic_expression"])
                and all(plan.get(field) == value for field, value in obligation["scope"].items()))
    except (ValueError, TypeError):
        return False


def _binding(reference, obligation, *, failed=False):
    expected = obligation.get("failed_proposal_reference") if failed else obligation.get("execution_reference")
    return isinstance(reference, dict) and reference == expected


def _valid_related_binding(reference, obligation, registry):
    """Exact raw descendants can close the same predicate, never a new scope."""
    if _binding(reference, obligation):
        return True
    if not isinstance(reference, dict) or reference.get("kind") != "raw_column":
        return False
    if {"table": reference.get("table"), "column": reference.get("column")} not in obligation["source_columns"]:
        return False
    matches = [subject for subject in registry["subjects"] if subject.get("reference") == reference]
    return (len(matches) == 1 and matches[0].get("definition_status") == "valid"
            and matches[0].get("definition_sha256") == reference.get("definition_sha256"))


def validate_sr_evidence(proposal, registry, obligations, rule_reports, review_reports, *, inventory=None, project_root=ROOT):
    """Require explicit role decisions and original passed condition/proof links.

    Caller must additionally retain contract, general role coverage, rule/source
    and semantic validators. This report never raises an earlier failed result
    to passed and never changes expressions, missing policies or source units.
    """
    errors = []
    result = {"version": VERSION, "policy_scope": POLICY_SCOPE, "passed": False, "errors": errors,
              "diagnostic_obligations": [], "rule_states": [], "review_states": [],
              "forces_rule_for_every_variable": False, "silent_AST_or_policy_repair": False,
              "review_requests_counted_as_valid_rules": False}
    def fail(code, path, message, wrong=None, allowed=None, required=None, **context):
        errors.append(_error(code, path, message, wrong, allowed, required, **context))
    if not isinstance(inventory, dict):
        fail("sr_source_inventory_required", "/sr_obligations", "Current inventory is required for the shared exact observation evaluator")
        return result
    current_obligations = build_sr_obligations(registry, inventory, project_root=project_root)
    if obligations != current_obligations:
        fail("sr_obligations_replay", "/sr_obligations", "Supplied obligations must equal the complete current source-replayed record")
        return result
    try:
        commitment = copy.deepcopy(obligations)
        recorded = commitment.pop("obligations_sha256")
        intact = recorded == digest(commitment)
    except (TypeError, KeyError, ValueError, AttributeError):
        intact = False
    if (not _intact_registry(registry) or not intact or obligations.get("registry_sha256") != registry.get("registry_sha256")
            or any(obligations.get(key) != registry.get(key) for key in ("schema_version", "source_version"))):
        fail("sr_obligations_binding", "/sr_obligations", "Use the unchanged code-owned obligations for this exact registry")
        return result
    if obligations.get("passed") is not True:
        fail("sr_obligations_source_failed", "/sr_obligations", "Source/definition/measurement replay failed; review the original errors",
             obligations.get("errors"), ["source-replayed obligations"])
    if not isinstance(proposal, dict):
        fail("sr_proposal_object", "/", "Role output must be an object")
        return result
    fields = {"rules": rule_reports, "review_requests": review_reports}
    outputs = {}
    for field, reports in fields.items():
        rows = proposal.get(field)
        if (not isinstance(rows, list) or not isinstance(reports, list) or len(rows) != len(reports)
                or any(not isinstance(row, dict) for row in rows)):
            fail("sr_production_report_binding", "/" + field, "Original outputs and production reports must align one-to-one")
            return result
        labels = [row.get("id") for row in rows]
        if any(not isinstance(label, str) or not label for label in labels) or len(set(labels)) != len(labels):
            fail("sr_output_identity", "/" + field, "Output IDs must be unique nonempty strings", labels)
            return result
        outputs[field] = {row["id"]: (index, row, reports[index]) for index, row in enumerate(rows)}
    decisions = proposal.get("role_decisions")
    if not isinstance(decisions, list):
        fail("sr_decisions_required", "/role_decisions", "Every variable requires its own explicit SR decision")
        return result
    for obligation in obligations["obligations"]:
        identifier = obligation["variable_id"]
        matches = [(index, row) for index, row in enumerate(decisions) if isinstance(row, dict) and row.get("variable_id") == identifier]
        if len(matches) != 1:
            fail("sr_decision_cardinality", "/role_decisions", "Exactly one SR decision is required for this actual variable", identifier, [identifier])
            continue
        index, decision = matches[0]
        path = f"/role_decisions/{index}/SR"
        branch = decision.get("SR")
        context = {"variable_id": identifier, "definition_sha256": obligation["definition_sha256"],
                   "confirmed_input_unit": obligation["input_unit"], "confirmed_missing_policy": obligation["input_missing_policy"],
                   "expected_condition": obligation["diagnostic_expression"], "measurement_state": obligation["state"]}
        if decision.get("proposal_sha256") != obligation["proposal_sha256"]:
            fail("sr_proposal_identity", f"/role_decisions/{index}/proposal_sha256", "Role decisions reference the original proposal SHA",
                 decision.get("proposal_sha256"), [obligation["proposal_sha256"]], **context)
        if not isinstance(branch, dict):
            fail("sr_branch_required", path, "Explicit SR decision and links are required", branch, **context)
            continue
        linked = {"rules": [], "review_requests": []}
        for key, field in (("rule_ids", "rules"), ("review_request_ids", "review_requests")):
            labels = branch.get(key)
            if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
                fail("sr_link_shape", path + "/" + key, "Use a list of original output IDs", labels, **context)
                continue
            for offset, label in enumerate(labels):
                value = outputs[field].get(label)
                if value is None:
                    fail("sr_link_unknown", f"{path}/{key}/{offset}", "Linked artifact is absent from original outputs", label,
                         list(outputs[field]), **context)
                    continue
                output_index, output, report = value
                if output.get("role") != "SR" or not isinstance(report, dict) or report.get("passed") is not True:
                    fail("sr_link_validation", f"{path}/{key}/{offset}", "The original SR production validation must pass", label, **context)
                    continue
                linked[field].append(value)
        state = obligation["state"]
        good_rules = [(i, rule, report) for i, rule, report in linked["rules"]
                      if _valid_related_binding(rule.get("input"), obligation, registry) and _condition_matches(report, obligation)]
        expected = obligation["expected_SR_decision"]
        if state in {"known_observed_diagnostic", "known_unobserved_diagnostic"}:
            required = "proposed" if state == "known_observed_diagnostic" else "deferred"
            if branch.get("decision") != required:
                fail("observed_diagnostic_requires_proposal" if required == "proposed" else "unobserved_diagnostic_requires_deferred_condition",
                    path + "/decision", obligation["reason"], branch.get("decision"), [required], ["rule_ids"], **context)
            qualifying = [value for value in good_rules if value[1].get("decision") == required
                          and (value[1].get("basis") == "observational_review" if required == "proposed" else True)]
            if not qualifying:
                fail("observed_diagnostic_rule_missing" if required == "proposed" else "unobserved_diagnostic_condition_missing",
                    path + "/rule_ids", "Link a passed SR preserving this exact expanded predicate and source population; a conditionless review cannot replace it",
                    branch.get("rule_ids"), [required + " exact-condition SR"], ["rules", "condition", "input", "evidence_ids"], **context)
        elif state in {"invalid_definition", "rejected_measurement"}:
            failed = state == "invalid_definition"
            reviews = [value for value in linked["review_requests"] if _binding(value[1].get("input"), obligation, failed=failed)
                       and value[1].get("review_kind") == ("failed_proposal" if failed else "condition_evidence")]
            if branch.get("decision") != "deferred" or not reviews or linked["rules"]:
                fail("invalid_or_unverified_SR_requires_review", path, "Keep failed proposal identity or missing measurement evidence separate from executable definitions",
                     branch, ["deferred with exact failed_proposal review" if failed else "deferred with condition_evidence review"], **context)
        elif state == "numeric_criterion_requires_evidence":
            supported = [value for value in linked["rules"] if _valid_related_binding(value[1].get("input"), obligation, registry)
                         and value[1].get("decision") == "proposed" and value[1].get("threshold_policy") is not None
                         and value[2].get("eligibility", {}).get("decision") == "proposed"]
            reviews = [value for value in linked["review_requests"] if _valid_related_binding(value[1].get("input"), obligation, registry)
                       and value[1].get("review_kind") == "condition_evidence" and "condition" not in value[1]
                       and value[1].get("required_evidence")]
            if not ((branch.get("decision") == "proposed" and supported)
                    or (branch.get("decision") == "deferred" and reviews and not linked["rules"])):
                fail("numeric_criterion_requires_conditionless_review", path,
                     "Measured numeric outcomes do not justify a threshold; preserve a conditionless evidence request or a production-verified threshold policy",
                     branch, ["deferred with condition_evidence review", "proposed with verified threshold policy"], **context)
        result["diagnostic_obligations"].append({"variable_id": identifier, "state": state,
            "expected_SR_decision": expected, "actual_SR_decision": branch.get("decision"),
            "matching_valid_rule_ids": [row[1]["id"] for row in good_rules]})
    # Classify each supplied SR independently of whether it closes a mandatory
    # derived-variable obligation. Raw source missing rules remain supported.
    for index, rule in enumerate(proposal["rules"]):
        if rule.get("role") != "SR":
            continue
        report = rule_reports[index]
        if not isinstance(report, dict) or report.get("passed") is not True:
            result["rule_states"].append({"id": rule["id"], "state": "validation_failed"})
            continue
        measured = report.get("predicate_measurement") or {}
        predicate = report.get("predicate_plan") or {}
        expression = predicate.get("expression")
        observation = evaluate_observation(predicate, inventory, measured, project_root=project_root)
        if not observation["measurement_verified"]:
            fail("sr_rule_observation_replay", f"/rules/{index}/decision", "Predicate observation must equal the complete current source-replayed record")
        kind = ("verified_threshold_SR" if rule.get("decision") == "proposed"
                and report.get("eligibility", {}).get("decision") == "proposed"
                else "threshold_condition_deferred_pending_evidence") if rule.get("threshold_policy") is not None else (
            "observed_diagnostic_SR" if rule.get("decision") == "proposed" else "known_condition_unobserved_SR")
        if _numeric_criterion(expression) and rule.get("threshold_policy") is None:
            fail("sr_unknown_threshold_condition_forbidden", f"/rules/{index}/condition",
                 "An unsupported numeric criterion belongs in a conditionless condition_evidence review", rule.get("condition"),
                 ["conditionless condition_evidence review"])
        if rule.get("decision") == "proposed" and rule.get("threshold_policy") is None:
            if rule.get("basis") != "observational_review" or not observation["usable_observation"]:
                fail("sr_observation_missing", f"/rules/{index}/decision", "Proposed observational SR requires code-measured usable boolean outcomes",
                     rule.get("decision"), ["deferred when no usable outcome exists"])
        result["rule_states"].append({"id": rule["id"], "state": kind,
            "decision": rule.get("decision"), "measurement_status": measured.get("status"),
            "measurement_evidence_id": (measured.get("source_evidence") or {}).get("id")})
    for index, review in enumerate(proposal["review_requests"]):
        if review.get("role") != "SR":
            continue
        report = review_reports[index]
        valid = isinstance(report, dict) and report.get("passed") is True
        if valid and "condition" in review:
            fail("sr_review_condition_forbidden", f"/review_requests/{index}/condition", "Evidence reviews carry no executable condition", review["condition"])
        result["review_states"].append({"id": review["id"], "state": "failed_proposal_review" if review.get("review_kind") == "failed_proposal"
                                       else "condition_evidence_review", "passed_production_validation": valid})
    result["passed"] = not errors
    return result
