"""Versioned, ID-independent role and dependency checks for proposed analyses.

This module proposes specifications only. It never trains or runs a detector.
Its policy is separate from the frozen v8--v14 catalog evaluation policies.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "2.3.0"
OBSERVATION_POLICY_VERSION = "1.0.0"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _error(code, path, message):
    return {"code": code, "path": path, "message": message}


def verify_registered_evidence(evidence, *, project_root=ROOT):
    """An evidence label alone never proves that a fact came from a source."""
    try:
        source = evidence["source"]
        root = Path(project_root).resolve()
        path = (root / source["path"]).resolve()
        path.relative_to(root)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            return False
        pointer = source.get("pointer", "")
        if source.get("format", "json") == "text":
            # Text cannot be promoted from a substring into a structured claim.
            return False
        document = json.loads(raw)
        value = document
        for key in pointer.lstrip("/").split("/") if pointer else []:
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        if value != evidence["value"] or evidence.get("verified_by") != "code":
            return False
        target = evidence.get("target", {})
        if evidence.get("kind") == "schema_declaration" and "column" in target:
            parts = pointer.split("/")
            if len(parts) != 5 or parts[1] != "tables" or parts[3] != "columns":
                return False
            return (document["tables"][int(parts[2])]["id"] == target.get("table")
                    and value.get("name") == target["column"])
        if evidence.get("kind") in {"business_document", "unit_contract"}:
            return (isinstance(value, dict) and value.get("table") == target.get("table")
                    and all(value.get(key) == target[key] for key in ("column", "columns") if key in target))
        if evidence.get("kind") == "operating_contract":
            return isinstance(value, dict) and value.get("identity_sha256") == target.get("identity_sha256")
        return True
    except (KeyError, ValueError, OSError, TypeError, IndexError):
        return False


def verify_measurement(plan, inventory, measurement, *, project_root=ROOT):
    """Replay bounded source statistics instead of accepting model numbers."""
    from generalized_measurement_v19 import measure_plan, validate_plan
    checked = validate_plan(plan, inventory)
    if not checked["passed"] or not isinstance(measurement, dict) or measurement.get("status") != "measured":
        return False
    replay = measure_plan(plan, inventory, project_root=project_root)
    if replay.get("status") != "measured":
        return False
    try:
        return canonical(measurement) == canonical(replay)
    except (ValueError, TypeError):
        return False


def evaluate_observation(plan, inventory, measurement, *, project_root=ROOT, require_boolean=True):
    """Replay the complete record and keep empty observations separate.

    ``measured`` records can legitimately contain zero usable outcomes.  That
    status proves a calculation ran; only a positive, exact integer valid_count
    proves observation support.  The record and its expression remain unchanged.
    """
    from generalized_measurement_v19 import digest, measure_plan, validate_plan
    checked = validate_plan(plan, inventory)
    result = {"policy_version": OBSERVATION_POLICY_VERSION,
              "measurement_verified": False, "measurement_status": None,
              "inferred_type": checked.get("inferred_type"), "valid_count": None,
              "usable_observation": False, "state": "rejected_measurement",
              "measurement_record_sha256": None, "errors": []}
    if not checked.get("passed"):
        result["errors"] = copy.deepcopy(checked.get("errors", []))
        return result
    replay = measure_plan(plan, inventory, project_root=project_root)
    result["measurement_status"] = replay.get("status")
    try:
        exact = (isinstance(measurement, dict)
                 and replay.get("status") in {"measured", "deferred"}
                 and canonical(measurement) == canonical(replay))
    except (ValueError, TypeError):
        exact = False
    if not exact:
        result["errors"] = [_error("observation_replay", "/measurement",
            "Preserve the complete current code-replayed record; status alone is not observation proof")]
        return result
    result.update(measurement_verified=True, measurement_record_sha256=digest(measurement),
                  valid_count=replay.get("summary", {}).get("valid_count"))
    usable = (replay["status"] == "measured" and type(result["valid_count"]) is int
              and result["valid_count"] > 0
              and (not require_boolean or checked.get("inferred_type") == "boolean"))
    result.update(usable_observation=usable,
                  state="observed" if usable else "known_unobserved")
    return result


def validate_baseline(plan, inventory, baseline, *, project_root=ROOT):
    """Validate a reference calculation, its selection and comparison contract."""
    errors = []
    required = ("version", "reference_plan", "measurement", "reference_period", "minimum_samples",
                "quantiles", "method", "comparison_operator", "unit", "missing_policy", "refresh_policy")
    if not isinstance(baseline, dict):
        return {"passed": False, "errors": [_error("baseline_missing", "baseline", "A numeric comparison requires an explicit reference specification") ]}
    for field in required:
        if field not in baseline:
            errors.append(_error("baseline_field", "baseline." + field, "Required reference field is absent"))
    if errors:
        return {"passed": False, "errors": errors}
    for field in ("reference_plan", "measurement", "reference_period", "unit", "missing_policy", "refresh_policy"):
        if not isinstance(baseline[field], dict):
            errors.append(_error("baseline_field_type", "baseline." + field, "Reference contract field must be an object"))
    if errors:
        return {"passed": False, "errors": errors}
    measured_record = baseline["measurement"]
    if (not isinstance(measured_record.get("summary"), dict) or not isinstance(measured_record.get("groups"), list)
            or any(not isinstance(group, dict) or not isinstance(group.get("value"), dict) for group in measured_record.get("groups", []))):
        return {"passed": False, "errors": [_error("baseline_measurement_shape", "baseline.measurement", "Reference measurement summaries and groups must preserve the code output shape")]}
    reference = baseline["reference_plan"]
    for field in ("expression", "grain", "group_by", "unit", "missing_policy", "schema_version", "source_version", "mapping_version"):
        if reference.get(field) != plan.get(field):
            errors.append(_error("baseline_scope", "baseline.reference_plan." + field, "Reference and comparison must preserve exact measure, grain, grouping, unit and versions"))
    if not verify_measurement(reference, inventory, baseline["measurement"], project_root=project_root):
        errors.append(_error("baseline_measurement", "baseline.measurement", "Reference statistics do not replay from pinned data"))
    if type(baseline["minimum_samples"]) is not int or baseline["minimum_samples"] < 2:
        errors.append(_error("baseline_sample", "baseline.minimum_samples", "Minimum sample count must be an integer of at least two"))
    if baseline["quantiles"] not in ([0.95], [0.99], [0.95, 0.99]) or baseline["method"] != "linear":
        errors.append(_error("baseline_quantile", "baseline.quantiles", "Supported quantiles are p95/p99 with linear interpolation"))
    if baseline["comparison_operator"] not in {"gt", "gte", "lt", "lte"}:
        errors.append(_error("baseline_operator", "baseline.comparison_operator", "Explicit comparison direction is required"))
    if baseline["unit"] != plan["unit"] or plan["unit"].get("status") != "verified":
        errors.append(_error("baseline_unit", "baseline.unit", "Numeric reference comparison requires the same verified unit"))
    if baseline["missing_policy"] != plan["missing_policy"]:
        errors.append(_error("baseline_missing_policy", "baseline.missing_policy", "Reference missing policy must match"))
    period = baseline["reference_period"]
    # The time selection is an AST, not a prose claim about the reference period.
    if not isinstance(period, dict) or not all(period.get(k) is not None for k in ("column", "start", "end", "time_zone", "selection_ast")):
        errors.append(_error("baseline_period", "baseline.reference_period", "Column, period, zone and exact selection AST are required"))
    else:
        def conjuncts(node):
            if isinstance(node, dict) and node.get("op") == "and":
                return [part for child in node.get("args", []) for part in conjuncts(child)]
            return [node]
        column = {"op": "column", **period["column"]} if isinstance(period["column"], dict) else None
        low = {"op": "gte", "args": [column, {"op": "literal", "value": period["start"], "logical_type": "datetime"}]}
        high = {"op": "lt", "args": [column, {"op": "literal", "value": period["end"], "logical_type": "datetime"}]}
        predicates = conjuncts(reference.get("population", {}).get("filter"))
        chronological = False
        try:
            start_dt, end_dt = [datetime.fromisoformat(period[key].replace("Z", "+00:00")) for key in ("start", "end")]
            chronological = start_dt < end_dt
            if period["time_zone"] == "UTC":
                chronological = chronological and all(d.utcoffset() is not None and d.utcoffset().total_seconds() == 0 for d in (start_dt, end_dt))
            elif period["time_zone"] == "unspecified":
                chronological = chronological and all(d.tzinfo is None for d in (start_dt, end_dt))
            else:
                chronological = False
        except (TypeError, ValueError):
            pass
        if (period["selection_ast"] != reference.get("population", {}).get("filter")
                or not isinstance(period["start"], str) or not isinstance(period["end"], str)
                or not chronological or low not in predicates or high not in predicates):
            errors.append(_error("baseline_selection", "baseline.reference_period.selection_ast", "Reference selection must contain exact start-inclusive/end-exclusive timestamp bounds"))
    if not isinstance(baseline["refresh_policy"], dict) or not baseline["refresh_policy"].get("mode") or not baseline["refresh_policy"].get("on_schema_or_unit_change"):
        errors.append(_error("baseline_refresh", "baseline.refresh_policy", "Refresh and schema/unit invalidation policies are required"))
    summaries = [g.get("summary", g.get("value", {})) for g in baseline["measurement"].get("groups", [])]
    if not plan.get("group_by"):
        summaries = [baseline["measurement"].get("summary", {})]
    minimum = baseline["minimum_samples"] if type(baseline["minimum_samples"]) is int else 2
    if not summaries or any(not isinstance(x, dict) or type(x.get("valid_count")) is not int or x["valid_count"] < minimum for x in summaries):
        errors.append(_error("baseline_insufficient_samples", "baseline.measurement", "Every used reference group needs the declared minimum valid samples"))
    from generalized_measurement_v19 import measure_plan
    current = measure_plan(plan, inventory, project_root=project_root)
    if plan.get("group_by"):
        reference_groups = {canonical(row.get("group_values")) for row in baseline["measurement"].get("groups", [])}
        current_groups = {canonical(row.get("group_values")) for row in current.get("groups", [])}
        if not current_groups or not current_groups <= reference_groups:
            errors.append(_error("baseline_group_coverage", "baseline.measurement.groups", "Every comparison group requires a matching reference group"))
    if baseline["measurement"].get("summary", {}).get("selection_unavailable_count", 0):
        errors.append(_error("baseline_selection_unavailable", "baseline.measurement.summary.selection_unavailable_count", "Unparseable reference selection cannot silently redefine the reference population"))
    return {"passed": not errors, "errors": errors, "baseline_version": baseline["version"],
            "baseline_fingerprint": fingerprint(baseline), "anomaly_execution": "not_run"}


def evaluate_roles(plan, inventory, measurement=None, *, baseline=None, operating_evidence=None, project_root=ROOT):
    from generalized_measurement_v19 import validate_plan
    validation = validate_plan(plan, inventory)
    roles = {role: {"decision": "deferred", "basis": "insufficient_evidence", "reason": "Role-specific verified evidence is incomplete", "evidence_ids": []}
             for role in ("HR", "SR", "IFR")}
    if not validation["passed"]:
        return {"passed": False, "version": VERSION, "roles": roles, "errors": validation["errors"]}
    from generalized_content_policy_v16 import validate_profile_admission
    admission = validate_profile_admission(inventory, project_root=project_root)
    if not admission["passed"]:
        return {"passed": False, "version": VERSION, "roles": roles, "errors": admission["errors"],
                "definition_status": validation["definition_status"], "measurement_status": "rejected"}
    purpose, expression = plan["purpose"], plan.get("expression")
    if purpose == "contract_review":
        roles["IFR"].update(decision="excluded", basis="review_only", reason="Contract review defines no numeric input")
        return {"passed": True, "version": VERSION, "roles": roles, "errors": []}
    observation = evaluate_observation(plan, inventory, measurement, project_root=project_root, require_boolean=False)
    measured = observation["measurement_verified"] and observation["measurement_status"] == "measured"
    usable = observation["usable_observation"]
    evidence_ids = [measurement["source_evidence"]["id"]] if measured else []
    # Declaration support is based on the exact physical predicate, never an ID.
    if purpose == "constraint_check" and expression.get("op") == "is_missing":
        args = expression.get("args", [])
        if len(args) == 1 and args[0].get("op") == "column":
            table = next((t for t in inventory["tables"] if t["id"] == args[0]["table"]), None)
            column = next((c for c in table["columns"] if c["name"] == args[0]["column"]), None) if table else None
            declaration_ids = []
            if column and column.get("nullable") is False:
                for fact in inventory.get("evidence", []):
                    target = fact.get("target", {})
                    if (fact.get("kind") == "schema_declaration" and target.get("table") == table["id"]
                            and (target.get("column") == column["name"] or target.get("columns") == [column["name"]])
                            and isinstance(fact.get("value"), dict) and fact["value"].get("nullable") is False
                            and fact["value"].get("name") == column["name"]
                            and verify_registered_evidence(fact, project_root=project_root)):
                        declaration_ids.append(fact["id"])
            if declaration_ids:
                roles["HR"].update(decision="proposed", basis="ddl_constraint", reason="Exact missing predicate tests a source-bound non-null declaration", evidence_ids=declaration_ids)
        roles["SR"]["reason"] = "A constraint check is not an independently defined observational diagnostic; a separate plan may reuse the same expression"
    if purpose == "observational_diagnostic" and usable and validation.get("inferred_type") == "boolean" and expression.get("op") != "column":
        roles["SR"].update(decision="proposed", basis="observational_review", reason="Exact diagnostic expression was measured in its declared population; no historical baseline is required", evidence_ids=evidence_ids)
    elif purpose == "observational_diagnostic" and validation.get("inferred_type") == "boolean" and observation["state"] == "known_unobserved":
        roles["SR"]["reason"] = "The boolean diagnostic is defined but the exact source replay contains no usable outcomes; preserve its condition under deferred"
    baseline_review = None
    if purpose == "baseline_comparison":
        baseline_review = validate_baseline(plan, inventory, baseline, project_root=project_root)
        if baseline_review["passed"] and usable:
            roles["SR"].update(decision="proposed", basis="measured_baseline", reason="Reference specification and comparison scope are verified", evidence_ids=evidence_ids + [baseline["measurement"]["source_evidence"]["id"]])
    variation = measurement.get("summary", {}).get("variation") if measured else None
    if variation == "constant":
        roles["IFR"].update(decision="excluded", basis="constant", reason="Measured input is constant in this population; HR/SR decisions remain separate", evidence_ids=evidence_ids)
    elif usable and purpose == "feature_calculation":
        required = {"query_visibility", "completion", "unit", "missing_policy", "leakage", "encoding"}
        verified = {}
        for fact in operating_evidence or []:
            if not isinstance(fact, dict):
                continue
            value, target = fact.get("value", {}), fact.get("target", {})
            if (fact.get("kind") == "operating_contract" and target.get("identity_sha256") == validation["identity_sha256"]
                    and verify_registered_evidence(fact, project_root=project_root) and isinstance(value, dict)
                    and value.get("status") == "verified"
                    and value.get("identity_sha256") == validation["identity_sha256"]):
                condition = value.get("condition")
                okay = False
                if condition == "query_visibility":
                    trace = value.get("trace", {})
                    try:
                        written, committed, queried = [datetime.fromisoformat(trace[k]) for k in ("written_at", "committed_at", "first_query_at")]
                        okay = (written <= committed <= queried and value.get("visibility") == "measured"
                                and value.get("phase") == "post_completion_query" and bool(value.get("trace_id")))
                    except (KeyError, TypeError, ValueError):
                        pass
                elif condition == "completion":
                    okay = (bool(value.get("event")) and bool(value.get("watermark"))
                            and value.get("late_arrival_policy") in {"wait", "recompute", "reject"})
                elif condition == "unit":
                    okay = value.get("name") == plan["unit"].get("name") and bool(value.get("name"))
                elif condition == "missing_policy":
                    okay = value.get("policy") == plan["missing_policy"] and value.get("validity_mask") is True
                elif condition == "leakage":
                    okay = (value.get("future_inputs") == [] and value.get("outcome_proxy") is False
                            and bool(value.get("prediction_cutoff")) and bool(value.get("review_method")))
                elif condition == "encoding":
                    okay = (value.get("dtype") in {"number", "boolean_01"} and bool(value.get("transform"))
                            and value.get("unseen_policy") in {"defer", "explicit_unknown"})
                if okay:
                    verified[condition] = fact["id"]
        if required <= set(verified) and variation == "variable" and plan["unit"].get("status") == "verified":
            roles["IFR"].update(decision="proposed", basis="verified_input_conditions", reason="Variable input has identity-bound operating availability, completion, unit, missing, leakage and encoding evidence", evidence_ids=evidence_ids + [verified[k] for k in sorted(required)])
    return {"passed": True, "version": VERSION, "roles": roles, "errors": [],
            "definition_status": validation["definition_status"],
            "definition_sha256": validation["definition_sha256"],
            "proposal_sha256": validation["proposal_sha256"],
            "measurement_status": measurement.get("status") if isinstance(measurement, dict) else "not_requested",
            "measurement_verified": measured, "observation_support": observation, "baseline_review": baseline_review,
            "policy_scope": "new generalized plans only; historical v8-v14 scores remain unchanged",
            "feature_vector_materialized": False, "detection_execution": "not_run"}


def specification_manifest(plan, inventory, *, semantic_mapping=None, baseline=None):
    """A stale specification cannot silently cross schema/mapping/unit revisions."""
    from generalized_measurement_v19 import validate_plan
    checked = validate_plan(plan, inventory)
    if not checked["passed"]:
        raise ValueError("Cannot version an invalid plan")
    return {"version": VERSION, "plan_id": plan["id"], "plan_version": plan["plan_version"],
            "content_policy_version": inventory.get("content_policy", {}).get("version"),
            "admission_identity_sha256": inventory.get("scope_admission", {}).get("admission_identity_sha256"),
            "identity": checked["identity"], "identity_sha256": checked["identity_sha256"],
            "schema_version": inventory["schema_version"], "source_version": inventory["source_version"],
            "mapping_version": fingerprint(semantic_mapping) if semantic_mapping else None,
            "baseline_version": baseline.get("version") if baseline else None,
            "baseline_sha256": fingerprint(baseline) if baseline else None,
            "dependencies": copy.deepcopy(inventory.get("sources", [])),
            "automatic_deployment": False}


def review_schema_change(manifest, inventory, *, semantic_mapping=None, baseline=None):
    from generalized_measurement_v19 import VERSION as COMPUTATIONAL_VERSION
    changed = []
    expected = {"version": VERSION, "plan_version": COMPUTATIONAL_VERSION,
                "content_policy_version": inventory.get("content_policy", {}).get("version"),
                "admission_identity_sha256": inventory.get("scope_admission", {}).get("admission_identity_sha256"),
                "schema_version": inventory["schema_version"], "source_version": inventory["source_version"],
                "mapping_version": fingerprint(semantic_mapping) if semantic_mapping else None,
                "baseline_version": baseline.get("version") if baseline else None,
                "baseline_sha256": fingerprint(baseline) if baseline else None}
    for field, value in expected.items():
        if manifest.get(field) != value:
            changed.append(field)
    return {"passed": not changed, "status": "current" if not changed else "revalidation_required",
            "changed_dependencies": changed, "allow_application": not changed,
            "automatic_rewrite": False, "detection_execution": "not_run"}
