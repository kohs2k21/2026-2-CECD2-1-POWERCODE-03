"""Source/capture admission before child aggregation, without false observations.

Valid definitions retain their original definition SHA when measurement scope
is unconfirmed. A separate exact code proof records why statistics were not
computed. Raw schema-only columns continue through the unchanged core path.
"""
from __future__ import annotations

import copy
from pathlib import Path

import generalized_measurement_v19 as core

ROOT = Path(__file__).resolve().parent
VERSION = core.VERSION
SCOPE_POLICY_VERSION = "capture-measurement-1.0.0"
digest = core.digest
validate_plan = core.validate_plan


def _nodes(node, depth=0):
    if not isinstance(node, dict) or depth > 16:
        return
    yield node
    for child in node.get("args", []) if isinstance(node.get("args"), list) else []:
        yield from _nodes(child, depth + 1)


def child_relationship_ids(plan):
    population = plan.get("population") if isinstance(plan.get("population"), dict) else {}
    return sorted({node["relationship_id"] for root in (plan.get("expression"), population.get("filter"))
                   for node in _nodes(root) if node.get("op") == "child_count"
                   and isinstance(node.get("relationship_id"), str)})


def _error(code, path, wrong, allowed, message):
    return {"code": code, "path": path, "actual_path": path, "wrong_value": copy.deepcopy(wrong),
            "allowed_values": copy.deepcopy(allowed), "message": message, "required_fields": []}


def _admit(inventory, source_policy, source_admission, project_root):
    if source_admission is not None:
        return source_admission(inventory, stage="measurement", project_root=project_root)
    if source_policy is not None:
        from generalized_source_policy_v21 import assert_source_admission
        return assert_source_admission(inventory, source_policy, "measurement", project_root=project_root)
    from generalized_runtime_context_v21 import admit
    return admit(inventory, stage="measurement", project_root=project_root)


def validate_child_aggregation_scope(plan, inventory, *, relationship_capture_scope=None,
                                     source_policy=None, source_admission=None, project_root=ROOT):
    """Check every child_count in the output or filter without computing it."""
    admission = _admit(inventory, source_policy, source_admission, project_root)
    if relationship_capture_scope is None:
        from generalized_runtime_context_v21 import relationship_scope
        relationship_capture_scope = relationship_scope()
    from generalized_rules_v21 import _load_scope_fact, CAPTURE_STATUSES
    from generalized_source_policy_v21 import relationship_definition, relationship_definition_sha256
    checked = validate_plan(plan, inventory)
    rids = child_relationship_ids(plan)
    errors, scopes = [], []
    tables = {table["id"]: table for table in inventory["tables"]}
    relations = {relationship["id"]: relationship for relationship in inventory["relationships"]}
    for rid in rids:
        relation = relations.get(rid)
        fact = (relationship_capture_scope or {}).get(rid)
        path = "/relationship_capture_scope/" + rid.replace("~", "~0").replace("/", "~1")
        if relation is None:
            errors.append(_error("measurement_relationship_missing", path, rid, list(relations), "The plan's relationship is not in the physical inventory"))
            continue
        capture, failures = _load_scope_fact(fact, source_policy=source_policy, source_admission=source_admission, project_root=project_root)
        for failure in failures:
            error = copy.deepcopy(failure)
            error["path"] = error["actual_path"] = path + str(error.get("actual_path", error.get("path", ""))).removeprefix("/relationship_capture_scope")
            errors.append(error)
        if capture is None:
            continue
        value = capture["value"]
        expected = {"relationship_id": rid,
                    "relationship_definition_sha": relationship_definition_sha256(relation),
                    "relationship_definition": relationship_definition(relation),
                    "schema_version": inventory.get("schema_version"),
                    "parent_source_sha256": (tables[relation["to_table"]].get("csv_source") or {}).get("sha256"),
                    "child_source_sha256": (tables[relation["from_table"]].get("csv_source") or {}).get("sha256")}
        if not isinstance(value, dict):
            errors.append(_error("measurement_capture_record", path + "/value", value, "object", "The capture scope must be an exact code-owned object"))
            continue
        for field, expected_value in expected.items():
            if not expected_value or value.get(field) != expected_value:
                errors.append(_error("measurement_capture_binding", path + "/value/" + field, value.get(field), expected_value,
                                     "Capture scope must bind the same current relationship definition, schema and parent/child source SHAs"))
        if value.get("status") not in CAPTURE_STATUSES:
            errors.append(_error("measurement_capture_incomplete", path + "/value/status", value.get("status"), list(CAPTURE_STATUSES),
                                 "Unconfirmed or incomplete capture does not permit child aggregation"))
        if value.get("child_count_allowed") is not True:
            errors.append(_error("measurement_child_aggregation_disallowed", path + "/value/child_count_allowed", value.get("child_count_allowed"), [True],
                                 "The code-reviewed relationship scope must explicitly allow child aggregation"))
        scopes.append({"relationship_id": rid, "capture_scope": capture})
    payload = {"version": SCOPE_POLICY_VERSION, "owner": "verified_code", "passed": not errors,
        "definition_status": checked.get("definition_status"), "definition_sha256": checked.get("definition_sha256"),
        "plan_proposal_sha256": digest(plan), "inventory_sha256": digest(inventory),
        "required_relationship_ids": rids, "relationship_capture_scope_sha256": digest(relationship_capture_scope or {}),
        "source_admission": copy.deepcopy(admission), "scopes": scopes, "errors": errors,
        "scope_only_no_statistics_computed": True, "model_assertions_used_as_scope": False}
    payload["scope_gate_sha256"] = digest(payload)
    return payload


def _deferred_record(plan, checked, gate):
    summary = {field: None for field in ("row_count", "valid_count", "missing_count", "true_count", "false_count",
                                        "ratio", "min", "max", "p95", "p99")}
    summary["variation"] = "unknown"
    return {"validator_version": VERSION, "candidate_id": plan.get("id"),
        "proposal_sha256": digest(plan), "definition_status": "valid",
        "definition_sha256": checked.get("definition_sha256"), "identity": copy.deepcopy(checked.get("identity")),
        "identity_sha256": checked.get("identity_sha256"), "errors": [], "status": "deferred",
        "definition_valid_measurement_deferred": True, "summary": summary, "groups": [], "source_evidence": None,
        "measurement_scope_gate": copy.deepcopy(gate), "defer_reasons": copy.deepcopy(gate["errors"]),
        "core_statistics_execution": "not_run_due_to_unconfirmed_capture_scope",
        "anomaly_execution": "not_run", "row_values_exported": False}


def measure_plan(plan, inventory, datasets=None, *, project_root=ROOT, max_rows=10000,
                 definition_validation=None, relationship_capture_scope=None,
                 source_policy=None, source_admission=None):
    _admit(inventory, source_policy, source_admission, project_root)
    checked = validate_plan(plan, inventory)
    if checked.get("passed") is not True or (definition_validation is not None and definition_validation.get("passed") is not True):
        return core.measure_plan(plan, inventory, datasets, project_root=project_root, max_rows=max_rows,
                                 definition_validation=definition_validation)
    gate = validate_child_aggregation_scope(plan, inventory,
        relationship_capture_scope=relationship_capture_scope, source_policy=source_policy,
        source_admission=source_admission, project_root=project_root)
    if gate["required_relationship_ids"] and gate["passed"] is not True:
        return _deferred_record(plan, checked, gate)
    return core.measure_plan(plan, inventory, datasets, project_root=project_root, max_rows=max_rows,
                             definition_validation=definition_validation)


def verify_scope_deferred_measurement(plan, inventory, measurement, *, project_root=ROOT,
        relationship_capture_scope=None, source_policy=None, source_admission=None):
    """Replay the admission proof, never the child aggregation it blocked."""
    checked = validate_plan(plan, inventory)
    gate = validate_child_aggregation_scope(plan, inventory,
        relationship_capture_scope=relationship_capture_scope, source_policy=source_policy,
        source_admission=source_admission, project_root=project_root)
    expected = _deferred_record(plan, checked, gate)
    passed = bool(checked.get("passed") is True and gate["required_relationship_ids"]
                  and gate["passed"] is False and measurement == expected)
    errors = [] if passed else [_error("scope_deferred_measurement_binding", "/measurement", measurement,
        {"definition_sha256": checked.get("definition_sha256"), "scope_gate_sha256": gate["scope_gate_sha256"],
         "status": "deferred", "core_statistics_execution": "not_run_due_to_unconfirmed_capture_scope"},
        "A capture-deferred record must exactly replay the current code-owned definition and blocked scope proof")]
    return {"passed": passed, "definition": checked, "scope": gate, "errors": errors,
            "child_statistics_replayed": False, "measurement_status": "deferred" if passed else "rejected",
            "known_definition_preserved": passed, "usable_observation": False}


def verify_measurement(plan, inventory, measurement, *, project_root=ROOT,
                       relationship_capture_scope=None, source_policy=None, source_admission=None):
    """Boolean API compatible with roles' verifier, with capture prechecks."""
    if isinstance(measurement, dict) and measurement.get("measurement_scope_gate") is not None:
        return verify_scope_deferred_measurement(plan, inventory, measurement, project_root=project_root,
            relationship_capture_scope=relationship_capture_scope, source_policy=source_policy,
            source_admission=source_admission)["passed"]
    replay = measure_plan(plan, inventory, project_root=project_root,
        relationship_capture_scope=relationship_capture_scope, source_policy=source_policy,
        source_admission=source_admission)
    return bool(isinstance(measurement, dict) and replay.get("status") in {"measured", "deferred"}
                and measurement == replay)
