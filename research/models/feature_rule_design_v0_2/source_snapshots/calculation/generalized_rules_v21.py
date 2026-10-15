"""A narrowly scoped structural-zero SR policy, with all old gates retained.

The literal zero in child_count == 0 or child_count > 0 identifies captured
absence/presence. It is not a learned latency threshold and establishes no HR
business contract. Source admission, pinned capture scope, exact measurements,
Boolean type, evidence, input lineage and trigger direction remain independent
mandatory checks. Existing responses and source files are never edited.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import generalized_rules_v19 as frozen
import generalized_rule_subjects_v19 as subjects
from generalized_measurement_v19 import digest, validate_plan
from generalized_roles_v19 import evaluate_observation
from generalized_trigger_semantics_v19 import validate_trigger_semantics

ROOT = Path(__file__).resolve().parent
VERSION = frozen.VERSION
VALIDATION_POLICY_VERSION = "4.4.1"
STRUCTURAL_ZERO_POLICY_VERSION = "1.0.0"
NUMERIC_ZERO_POLICY_VERSION = "zero-diagnostic1.0.0"
NUMERIC_ZERO_POLICY_SCOPE = "verified_numeric_physical_zero_definition_observational_SR_only"
NUMERIC_ZERO_SOURCE_UNITS = {"s", "ms", "us", "B", "byte", "KiB", "MiB", "count", "ratio"}
POLICY_SCOPE = "source_verified_child_presence_absence_not_numeric_business_threshold"
CAPTURE_STATUSES = ("complete_for_selected_parents", "bounded_capture")


def _error(code, path, message, wrong_value=None, allowed_values=None, required_fields=()):
    return {"code": code, "path": path, "actual_path": path, "message": message,
            "wrong_value": copy.deepcopy(wrong_value), "allowed_values": copy.deepcopy(allowed_values),
            "required_fields": list(required_fields)}


def _admit(value, *, source_policy, source_admission, project_root):
    try:
        if source_admission is not None:
            proof = source_admission(value, stage="measurement", project_root=project_root)
        elif source_policy is not None:
            from generalized_source_policy_v21 import assert_source_admission
            proof = assert_source_admission(value, source_policy, "measurement", project_root=project_root)
        else:
            from generalized_runtime_context_v21 import admit
            proof = admit(value, stage="measurement", project_root=project_root)
        if proof is None or proof is False or (isinstance(proof, dict) and proof.get("passed") is False):
            return None, [_error("source_admission", "/source_policy", "원천 승인 검사를 통과하지 못했습니다.", proof)]
        return copy.deepcopy(proof), []
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return None, [_error("source_admission", "/source_policy", str(exc))]


def lower_ordinary_rule(rule):
    """Explicit null-marker boundary only; conditions/context stay byte-stable."""
    if not isinstance(rule, dict):
        return None, [_error("rule_object", "/", "규칙은 객체여야 합니다.", rule, ["object"])], None
    if rule.get("baseline_reference") is not None:
        return None, [_error("baseline_reference_requires_compilation", "/baseline_reference",
            "기준선 참조 제안은 별도 코드 컴파일을 먼저 거쳐야 합니다.", rule["baseline_reference"],
            ["resolve_baseline_rule then validate compiled_rule"])], None
    lowered = {k: copy.deepcopy(v) for k, v in rule.items() if k != "baseline_reference"}
    boundary = {"version": "1.0.0", "original_proposal_sha256": digest(rule),
                "native_rule_sha256": digest(lowered), "removed_fields": ["baseline_reference"] if "baseline_reference" in rule else [],
                "removed_marker_value": None, "condition_changed": False, "input_context_changed": False,
                "original_proposal_changed": False}
    return lowered, [], boundary



def numeric_zero_expression(expression):
    """Recognize zero-value equality syntax; source/type/unit proof is separate."""
    original = copy.deepcopy(expression)
    wrappers = []
    for _ in range(8):
        if (not isinstance(expression, dict) or set(expression) != {"op", "args"}
                or expression.get("op") != "eq" or not isinstance(expression.get("args"), list)
                or len(expression["args"]) != 2):
            break
        args = expression["args"]
        indices = [i for i, node in enumerate(args) if isinstance(node, dict)
            and set(node) == {"op", "value"} and node.get("op") == "literal"
            and node.get("value") is True]
        if len(indices) != 1:
            break
        wrappers.append({"op": "eq", "true_literal_position": indices[0]})
        expression = args[1 - indices[0]]
    if (not isinstance(expression, dict) or set(expression) != {"op", "args"}
            or expression.get("op") != "eq" or not isinstance(expression.get("args"), list)
            or len(expression["args"]) != 2):
        return None
    for position in (0, 1):
        column, zero = expression["args"][position], expression["args"][1 - position]
        if (isinstance(column, dict) and set(column) == {"op", "table", "column"}
                and column.get("op") == "column" and isinstance(column.get("table"), str)
                and isinstance(column.get("column"), str) and isinstance(zero, dict)
                and set(zero) == {"op", "value"} and zero.get("op") == "literal"
                and type(zero.get("value")) in {int, float} and zero["value"] == 0):
            return {"version": NUMERIC_ZERO_POLICY_VERSION, "column": {"table": column["table"], "column": column["column"]},
                "expression": original, "recognized_boundary_expression": copy.deepcopy(expression),
                "equivalent_true_wrappers": wrappers, "column_position": position,
                "meaning": "source_value_equals_zero", "original_AST_changed": False,
                "establishes_HR_contract": False, "source_eligibility_granted_by_shape": False}
    return None


def numeric_zero_support(plan, inventory, measurement, *, project_root=ROOT, source_policy=None,
                         source_admission=None, relationship_capture_scope=None):
    """Verify a numeric zero definition and exact measured/pending source proof.

    No candidate name, Boolean type alone or numeric literal alone grants this
    policy. A verified supported source unit, physical numeric column, valid
    exact definition, admitted capture/population and canonical measurement
    replay are all required. Missing inputs remain cannot-calculate.
    """
    result = {"version": NUMERIC_ZERO_POLICY_VERSION, "policy_scope": NUMERIC_ZERO_POLICY_SCOPE,
        "passed": False, "known_condition": False, "usable_observation": False,
        "numeric_zero": numeric_zero_expression((plan or {}).get("expression")),
        "source_unit": None, "observation": None, "errors": [], "anomaly_execution": "not_run"}
    proof, failures = _admit(inventory, source_policy=source_policy, source_admission=source_admission, project_root=project_root)
    result["source_admission"] = proof
    result["errors"].extend(failures)
    if failures:
        return result
    from generalized_content_policy_v16 import assert_profile_admission
    try:
        assert_profile_admission(inventory, project_root=project_root)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result["errors"].append(_error("zero_content_admission", "/inventory", str(exc)))
        return result
    if result["numeric_zero"] is None:
        result["errors"].append(_error("not_numeric_zero_definition", "/expression",
            "이 정책은 원시 숫자 컬럼과 숫자0의 정확한 등가 Boolean 정의에만 적용합니다.", (plan or {}).get("expression")))
        return result
    checked = validate_plan(plan, inventory)
    result["definition_validation_sha256"] = digest(checked)
    result["definition_sha256"] = checked.get("definition_sha256")
    result["errors"].extend(copy.deepcopy(checked.get("errors", [])))
    if checked.get("inferred_type") != "boolean":
        result["errors"].append(_error("zero_definition_boolean", "/expression", "0 진단의 정의는 Boolean이어야 합니다.", checked.get("inferred_type"), ["boolean"]))
    reference = result["numeric_zero"]["column"]
    columns = [column for table in inventory.get("tables", []) if table.get("id") == reference["table"]
        for column in table.get("columns", []) if column.get("name") == reference["column"]]
    if len(columns) != 1 or columns[0].get("logical_type") not in {"integer", "number"}:
        result["errors"].append(_error("zero_physical_numeric_column", "/expression", "유일한 물리 숫자 컬럼이 필요합니다.", reference))
        return result
    unit = columns[0].get("unit") or {}
    result["source_unit"] = copy.deepcopy(unit)
    result["output_unit"] = copy.deepcopy(plan.get("unit"))
    if unit.get("status") != "verified" or unit.get("name") not in NUMERIC_ZERO_SOURCE_UNITS:
        result["errors"].append(_error("zero_verified_source_unit", "/expression",
            "원시 단위와 척도는 지원 단위의 정확한 원천 근거로 확인되어야 합니다.", unit, sorted(NUMERIC_ZERO_SOURCE_UNITS)))
    evidence = {fact.get("id"): fact for fact in inventory.get("evidence", [])}
    refs = unit.get("evidence_ids")
    from generalized_roles_v19 import verify_registered_evidence
    unit_facts = [evidence.get(ref) for ref in refs] if isinstance(refs, list) else []
    if not unit_facts or not all(isinstance(fact, dict) and fact.get("kind") in {"unit_contract", "business_document"}
            and fact.get("target", {}).get("table") == reference["table"]
            and fact.get("target", {}).get("column") == reference["column"]
            and fact.get("value") == {**reference, "unit": unit.get("name")}
            and verify_registered_evidence(fact, project_root=project_root) for fact in unit_facts):
        result["errors"].append(_error("zero_source_unit_proof", "/expression", "단위 확인 표지만이 아니라 동일 컬럼·단위의 원천 사실을 재검증해야 합니다.", refs))
    if result["errors"]:
        return result
    from generalized_measurement_v21 import measure_plan, validate_child_aggregation_scope
    kwargs = dict(project_root=project_root, source_policy=source_policy,
        source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
    capture = validate_child_aggregation_scope(plan, inventory, **kwargs)
    result["capture_scope"] = capture
    if not capture["passed"]:
        result["errors"].extend(copy.deepcopy(capture["errors"]))
        return result
    replay = measure_plan(plan, inventory, **kwargs)
    exact = isinstance(measurement, dict) and replay.get("status") in {"measured", "deferred"} and replay == measurement
    valid_count = replay.get("summary", {}).get("valid_count") if exact else None
    usable = exact and replay["status"] == "measured" and type(valid_count) is int and valid_count > 0
    result["observation"] = {"measurement_verified": exact, "measurement_status": replay.get("status") if exact else None,
        "valid_count": valid_count, "usable_observation": usable,
        "state": "observed" if usable else "known_unobserved" if exact else "rejected_measurement",
        "measurement_record_sha256": digest(measurement) if exact else None}
    if not exact:
        result["errors"].append(_error("zero_exact_measurement_replay", "/measurement", "정의·모집단·결측·원천 버전이 같은 측정 또는 보류 기록을 재현해야 합니다."))
    result.update(passed=not result["errors"], known_condition=not result["errors"], usable_observation=usable)
    return result


def _apply_numeric_zero_rule_policy(native, inventory, registry, report, *, project_root,
                                    source_policy, source_admission, relationship_capture_scope):
    if native.get("role") != "SR" or native.get("threshold_policy") is not None:
        return report
    subject, failures = subjects._resolve_subject(native.get("input"), inventory, registry)
    if failures or not subject or native.get("input", {}).get("kind") != "variable":
        return report
    expected = numeric_zero_expression(subject["plan"].get("expression"))
    predicate = report.get("predicate_plan")
    actual = numeric_zero_expression((predicate or {}).get("expression"))
    if expected is None or actual is None or expected["column"] != actual["column"]:
        return report
    scope_fields = ("grain", "population", "group_by", "schema_version", "source_version", "mapping_version", "availability", "unit", "missing_policy")
    if any(predicate.get(field) != subject["plan"].get(field) for field in scope_fields):
        return report
    support = numeric_zero_support(subject["plan"], inventory, subject.get("measurement"), project_root=project_root,
        source_policy=source_policy, source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
    report["numeric_zero_definition_support"] = support
    if not support["passed"]:
        return report
    predicate_support = numeric_zero_support(predicate, inventory, report.get("predicate_measurement"), project_root=project_root,
        source_policy=source_policy, source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
    report["numeric_zero_predicate_support"] = predicate_support
    if not predicate_support["passed"]:
        return report
    observed = predicate_support["usable_observation"]
    exact_evidence = []
    for measurement in (report.get("predicate_measurement"), subject.get("measurement")):
        if measurement and measurement.get("source_evidence"):
            exact_evidence.append(measurement["source_evidence"]["id"])
    source_columns = {(expected["column"]["table"], expected["column"]["column"])}
    from generalized_roles_v19 import verify_registered_evidence
    schema_refs = [fact["id"] for fact in inventory.get("evidence", []) if fact.get("kind") == "schema_declaration"
        and (fact.get("target", {}).get("table"), fact.get("target", {}).get("column")) in source_columns
        and verify_registered_evidence(fact, project_root=project_root)]
    allowed = set(exact_evidence + schema_refs)
    retained, removed = [], []
    for error in report["errors"]:
        remove = error.get("code") in {"unknown_condition_requires_review", "rule_ineligible_proposal", "base_rule_validation"}
        if error.get("code") == "rule_proposal_evidence":
            remove = observed and bool(set(native["evidence_ids"]) & set(exact_evidence))
        if error.get("code") == "rule_evidence_scope":
            try:
                index = int(error.get("actual_path", "").rsplit("/", 1)[-1])
                remove = native["evidence_ids"][index] in allowed
            except (ValueError, IndexError):
                remove = False
        (removed if remove else retained).append(copy.deepcopy(error))
    report["errors"] = retained
    report["eligibility"] = ({"decision": "proposed", "basis": "observational_review", "evidence_ids": list(dict.fromkeys(exact_evidence)),
        "reason": "검증된 원시 숫자값의 0 등가 Boolean 정의와 같은 모집단에서 관측한 진단입니다. 학습 cutoff와 HR 계약이 아닙니다."}
        if observed else subjects._deferred("0 조건과 원천 단위는 확인했으나 유효 관측이 없어 조건을 보존하고 보류합니다."))
    choice = "proposed" if observed else "deferred"
    if native.get("decision") != choice:
        report["errors"].append(_error("missed_supported_proposal" if observed else "rule_ineligible_proposal", "/decision", "유효 관측이 있으면 선택된 0 진단을 제안하고, 없으면 같은 조건을 보존해 보류합니다.", native.get("decision"), [choice]))
    if observed and native.get("basis") != "observational_review":
        report["errors"].append(_error("rule_basis", "/basis", "0 여부의 선택된 관측 진단은 observational_review입니다.", native.get("basis"), ["observational_review"]))
    for index, ref in enumerate(native.get("evidence_ids", [])):
        if ref not in allowed:
            report["errors"].append(_error("rule_evidence_scope", f"/evidence_ids/{index}", "동일 정의·조건의 코드 근거만 사용합니다.", ref, sorted(allowed)))
    if observed and not set(native["evidence_ids"]) & set(exact_evidence):
        if not any(e.get("code") == "rule_proposal_evidence" for e in report["errors"]):
            report["errors"].append(_error("rule_proposal_evidence", "/evidence_ids", "관측 0 진단은 정확한 코드 측정 근거가 필요합니다.", native["evidence_ids"], exact_evidence))
    report["passed"] = not report["errors"]
    semantics = validate_trigger_semantics(native, inventory, registry, validation_report=report, project_root=project_root)
    report["trigger_semantics"] = semantics
    report["errors"].extend(copy.deepcopy(semantics.get("errors", [])))
    report["passed"] = not report["errors"]
    report["executable"] = report["passed"] and native["decision"] == "proposed" and semantics.get("executable", False)
    report["counts_as_valid_rule"] = report["executable"]
    report["numeric_zero_policy_application"] = {"version": NUMERIC_ZERO_POLICY_VERSION, "policy_scope": NUMERIC_ZERO_POLICY_SCOPE,
        "removed_legacy_policy_errors": removed, "all_unrelated_errors_retained": True, "condition_changed": False,
        "input_definition_changed": False, "numeric_threshold_general_exemption": False, "establishes_HR_contract": False}
    return report


def structural_zero_expression(expression):
    """Exact AST recognition; names, prose and general numeric zero do not count."""
    original = copy.deepcopy(expression)
    wrappers = []
    for _ in range(8):
        if not isinstance(expression, dict) or set(expression) != {"op", "args"} or expression.get("op") != "eq":
            break
        args = expression.get("args")
        if not isinstance(args, list) or len(args) != 2:
            break
        indices = [i for i, arg in enumerate(args) if isinstance(arg, dict) and
                   set(arg) == {"op", "value"} and arg.get("op") == "literal" and arg.get("value") is True]
        if len(indices) != 1:
            break
        wrappers.append({"op": "eq", "true_literal_position": indices[0]})
        expression = args[1 - indices[0]]
    if (not isinstance(expression, dict) or set(expression) != {"op", "args"}
            or expression.get("op") not in {"eq", "gt"}
            or not isinstance(expression.get("args"), list) or len(expression["args"]) != 2):
        return None
    count, zero = expression["args"]
    if (not isinstance(count, dict) or set(count) != {"op", "relationship_id"} or count.get("op") != "child_count"
            or not isinstance(count.get("relationship_id"), str) or not count["relationship_id"]
            or not isinstance(zero, dict) or set(zero) != {"op", "value"} or zero.get("op") != "literal"
            or type(zero.get("value")) not in {int, float} or zero["value"] != 0):
        return None
    return {"relationship_id": count["relationship_id"], "meaning": "child_absence" if expression["op"] == "eq" else "child_presence",
            "expression": original, "recognized_boundary_expression": copy.deepcopy(expression),
            "equivalent_true_wrappers": wrappers, "original_AST_changed": False,
            "literal_role": "structural_presence_boundary",
            "analysis_scope": "within_verified_source_capture", "establishes_HR_contract": False}


def _load_scope_fact(fact, *, source_policy, source_admission, project_root):
    errors = []
    if not isinstance(fact, dict) or not isinstance(fact.get("source"), dict) or "value" not in fact:
        return None, [_error("relationship_capture_fact", "/relationship_capture_scope",
            "관계 범위는 원천 경로·SHA·JSON pointer와 정확한 값으로 확인해야 합니다.", fact,
            ["{source:{path,sha256,pointer},value:exact code capture record}"])]
    proof, failures = _admit(fact["source"], source_policy=source_policy,
                            source_admission=source_admission, project_root=project_root)
    errors.extend(failures)
    if errors:
        return None, errors
    try:
        root = Path(project_root).resolve()
        path = (root / fact["source"]["path"]).resolve()
        path.relative_to(root)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != fact["source"].get("sha256"):
            return None, [_error("relationship_capture_sha256", "/relationship_capture_scope/source/sha256",
                "관계 범위 기록의 SHA가 현재 파일과 일치해야 합니다.", fact["source"].get("sha256"))]
        actual = json.loads(raw)
        pointer = fact["source"].get("pointer", "")
        if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
            raise ValueError("Scope pointer must be an exact JSON pointer")
        for part in pointer.strip("/").split("/") if pointer else []:
            part = part.replace("~1", "/").replace("~0", "~")
            actual = actual[int(part)] if isinstance(actual, list) else actual[part]
        if actual != fact["value"]:
            return None, [_error("relationship_capture_value", "/relationship_capture_scope/value",
                "관계 범위 설명은 정확한 코드 원천 값과 같아야 합니다.", fact["value"], [actual])]
        return {"value": copy.deepcopy(actual), "source": copy.deepcopy(fact["source"]), "admission": proof}, []
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        return None, [_error("relationship_capture_source", "/relationship_capture_scope/source", str(exc), fact.get("source"))]


def validate_structural_zero_scope(plan, inventory, *, relationship_capture_scope=None,
                                   source_policy=None, source_admission=None, project_root=ROOT):
    if relationship_capture_scope is None:
        from generalized_runtime_context_v21 import relationship_scope
        relationship_capture_scope = relationship_scope()
    result = {"passed": False, "errors": [], "structural_zero": structural_zero_expression(plan.get("expression")),
              "capture_scope": None, "source_admission": None, "policy_version": STRUCTURAL_ZERO_POLICY_VERSION}
    if result["structural_zero"] is None:
        result["errors"] = [_error("not_structural_zero", "/expression", "이 정책은 정확한 child_count==0 또는 child_count>0에만 적용합니다.", plan.get("expression"))]
        return result
    proof, errors = _admit(inventory, source_policy=source_policy, source_admission=source_admission, project_root=project_root)
    result["source_admission"] = proof
    result["errors"].extend(errors)
    checked = validate_plan(plan, inventory)
    result["definition_validation_sha256"] = digest(checked)
    result["errors"].extend(copy.deepcopy(checked.get("errors", [])))
    if checked.get("inferred_type") != "boolean":
        result["errors"].append(_error("structural_zero_boolean", "/expression", "존재·부재 진단은 Boolean 결과여야 합니다.", checked.get("inferred_type"), ["boolean"]))
    rid = result["structural_zero"]["relationship_id"]
    candidates = [r for r in inventory.get("relationships", []) if r.get("id") == rid]
    fact = (relationship_capture_scope or {}).get(rid) if isinstance(relationship_capture_scope, dict) else None
    if len(candidates) != 1:
        result["errors"].append(_error("structural_zero_relationship", "/expression/args/0/relationship_id", "유일하고 확인된 물리 관계가 필요합니다.", rid))
    if result["errors"]:
        return result
    capture, errors = _load_scope_fact(fact, source_policy=source_policy, source_admission=source_admission, project_root=project_root)
    result["errors"].extend(errors)
    if capture is None:
        return result
    relation = candidates[0]
    value = capture["value"]
    tables = {t["id"]: t for t in inventory["tables"]}
    from generalized_source_policy_v21 import relationship_definition, relationship_definition_sha256
    expected = {"relationship_id": rid,
                "parent_source_sha256": (tables[relation["to_table"]].get("csv_source") or {}).get("sha256"),
                "child_source_sha256": (tables[relation["from_table"]].get("csv_source") or {}).get("sha256"),
                "relationship_definition_sha": relationship_definition_sha256(relation),
                "relationship_definition": relationship_definition(relation),
                "schema_version": inventory.get("schema_version")}
    if not isinstance(value, dict):
        result["errors"].append(_error("relationship_capture_record", "/relationship_capture_scope/value", "관계 범위 기록은 객체여야 합니다.", value, ["object"]))
    else:
        for field, actual in expected.items():
            if not actual or value.get(field) != actual:
                result["errors"].append(_error("relationship_capture_binding", "/relationship_capture_scope/value/" + field,
                    "관계 범위는 현재 부모·자식 원천의 동일 SHA를 참조해야 합니다.", value.get(field), [actual]))
        if value.get("status") not in CAPTURE_STATUSES:
            result["errors"].append(_error("relationship_capture_status", "/relationship_capture_scope/value/status",
                "정보가 불완전하거나 미확인된 관계 범위는 자식 부재의 근거가 아닙니다.", value.get("status"), list(CAPTURE_STATUSES)))
        if value.get("child_count_allowed") is not True:
            result["errors"].append(_error("relationship_child_count_not_allowed", "/relationship_capture_scope/value/child_count_allowed",
                "표본의 코드 검토가 자식 집계를 허용한 경우만 진단에 사용할 수 있습니다.", value.get("child_count_allowed"), [True]))
    result["capture_scope"] = capture
    result["passed"] = not result["errors"]
    result["capture_semantics"] = {"scope": "within_verified_source_capture", "operational_completion_verified": False,
                                   "business_must_have_child_contract_verified": False, "p95_required": False}
    return result


def evaluate_structural_zero_support(plan, inventory, measurement, **kwargs):
    scope = validate_structural_zero_scope(plan, inventory, **kwargs)
    result = {**scope, "known_condition": scope["passed"], "usable_observation": False, "observation": None}
    if not scope["passed"]:
        return result
    # The recognized diagnostic can mention one relationship while its
    # population filter mentions another. Both must be admitted before replay.
    from generalized_measurement_v21 import validate_child_aggregation_scope
    aggregate_scope = validate_child_aggregation_scope(plan, inventory, **kwargs)
    result["aggregation_scope"] = aggregate_scope
    if not aggregate_scope["passed"]:
        result["errors"].extend(copy.deepcopy(aggregate_scope["errors"]))
        result.update(passed=False, child_statistics_execution="not_run")
        return result
    observation = evaluate_observation(plan, inventory, measurement, project_root=kwargs.get("project_root", ROOT))
    result["observation"] = observation
    result["usable_observation"] = observation.get("usable_observation", False)
    if not observation.get("measurement_verified"):
        result["errors"].append(_error("structural_zero_measurement_replay", "/measurement",
            "정확한 Boolean 진단 측정을 현재 원천에서 재현해야 합니다.", measurement))
    result["passed"] = not result["errors"]
    return result


def validate_rule(rule, inventory, registry, *, project_root=ROOT, source_policy=None,
                  source_admission=None, relationship_capture_scope=None):
    proof, failures = _admit(inventory, source_policy=source_policy, source_admission=source_admission, project_root=project_root)
    native, boundary_errors, boundary = lower_ordinary_rule(rule)
    if failures or boundary_errors:
        return {"passed": False, "version": VERSION, "errors": failures + boundary_errors, "executable": False,
                "counts_as_valid_rule": False, "source_admission": proof, "native_boundary": boundary,
                "eligibility": subjects._deferred(), "anomaly_execution": "not_run"}
    item, resolution_errors = subjects._resolve_subject(native.get("input"), inventory, registry)
    if item and (item.get("measurement") or {}).get("measurement_scope_gate"):
        report = _validate_scope_deferred_rule(native, inventory, registry, item,
            project_root=project_root, source_policy=source_policy, source_admission=source_admission,
            relationship_capture_scope=relationship_capture_scope)
        report["source_admission"] = proof
        report["native_boundary"] = boundary
        return report
    # Old measured registries are not proof of current capture admission.
    # Inspect both the bound definition and the expanded proposed predicate
    # before any frozen validator can recompute their child aggregates.
    capture_gates = []
    if item is not None and not resolution_errors and not frozen._schema_report(native, "rules"):
        from generalized_measurement_v21 import validate_child_aggregation_scope
        from generalized_trigger_semantics_v19 import _expanded_condition
        gate_kwargs = dict(project_root=project_root, source_policy=source_policy,
            source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
        capture_gates.append(validate_child_aggregation_scope(item["plan"], inventory, **gate_kwargs))
        annotated = {**item, "lineage": validate_plan(item["plan"], inventory).get("identity", {}).get("source_columns", [])}
        expanded, expansion_errors = _expanded_condition(native, annotated)
        if not expansion_errors:
            predicate = copy.deepcopy(item["plan"])
            predicate.update(id="rule_scope_predicate_" + native["id"], purpose="observational_diagnostic",
                expression=expanded, unit={"name": "boolean", "status": "verified", "evidence_ids": []})
            predicate["missing_policy"]["input"] = "count_missing" if isinstance(expanded, dict) and expanded.get("op") == "is_missing" else "propagate"
            capture_gates.append(validate_child_aggregation_scope(predicate, inventory, **gate_kwargs))
        scope_errors = [copy.deepcopy(error) for gate in capture_gates for error in gate["errors"]]
        if scope_errors:
            return {"passed": False, "version": VERSION, "validation_policy_version": VALIDATION_POLICY_VERSION,
                "errors": scope_errors, "executable": False, "counts_as_valid_rule": False,
                "source_admission": proof, "native_boundary": boundary, "eligibility": subjects._deferred(),
                "input_definition_sha256": item.get("definition_sha256"), "input_definition_status": item.get("definition_status"),
                "input_measurement_status": item.get("measurement_status"), "capture_scope_gates": capture_gates,
                "scope_gate_before_legacy_validation": True, "child_statistics_execution": "not_run",
                "original_proposal_changed": False, "anomaly_execution": "not_run"}
    report = frozen.validate_rule(native, inventory, registry, project_root=project_root)
    report["source_admission"] = proof
    report["native_boundary"] = boundary
    report["validation_policy_version"] = VALIDATION_POLICY_VERSION
    report["structural_zero_policy_version"] = STRUCTURAL_ZERO_POLICY_VERSION
    report["capture_scope_gates"] = capture_gates
    report = _apply_numeric_zero_rule_policy(native, inventory, registry, report, project_root=project_root,
        source_policy=source_policy, source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
    predicate = report.get("predicate_plan")
    special = structural_zero_expression((predicate or {}).get("expression"))
    if native.get("role") != "SR" or native.get("threshold_policy") is not None or special is None:
        return report
    support = evaluate_structural_zero_support(predicate, inventory, report.get("predicate_measurement"),
        project_root=project_root, source_policy=source_policy, source_admission=source_admission,
        relationship_capture_scope=relationship_capture_scope)
    report["structural_zero_support"] = support
    if not support["passed"]:
        report["errors"].extend(copy.deepcopy(support["errors"]))
        report.update(passed=False, executable=False, counts_as_valid_rule=False)
        return report
    subject, resolution_errors = subjects._resolve_subject(native["input"], inventory, registry)
    if resolution_errors:
        report["errors"].extend(copy.deepcopy(resolution_errors))
        report.update(passed=False, executable=False, counts_as_valid_rule=False)
        return report
    observed = support["usable_observation"]
    evidence = [report["predicate_measurement"]["source_evidence"]["id"]] if observed else []
    source_measurement = subject.get("measurement") or {}
    if source_measurement.get("source_evidence"):
        evidence.append(source_measurement["source_evidence"]["id"])
    allowed = set(evidence)
    source_columns = {(x["table"], x["column"]) for x in validate_plan(subject["plan"], inventory)["identity"]["source_columns"]}
    from generalized_roles_v19 import verify_registered_evidence
    allowed.update(fact["id"] for fact in inventory.get("evidence", [])
                   if fact.get("kind") == "schema_declaration"
                   and (fact.get("target", {}).get("table"), fact.get("target", {}).get("column")) in source_columns
                   and verify_registered_evidence(fact, project_root=project_root))
    expected = "proposed" if observed else "deferred"
    corrected_eligibility = ({"decision": "proposed", "basis": "observational_review", "evidence_ids": list(dict.fromkeys(evidence)),
        "reason": "현재 확인된 관계 포착 범위의 자식 존재·부재 조건을 정확히 측정했습니다. 0은 존재 경계이며 과거 분위수나 HR 계약이 아닙니다."}
        if observed else subjects._deferred("관계 범위와 Boolean 조건은 확인했으나 사용할 수 있는 관측이 없어 조건을 보존하고 보류합니다."))
    removable_codes = {"unknown_condition_requires_review", "rule_ineligible_proposal", "base_rule_validation"}
    removed, retained = [], []
    for error in report["errors"]:
        code = error.get("code")
        remove = code in removable_codes
        if code == "rule_evidence_scope":
            path = error.get("actual_path", "")
            try:
                index = int(path.rsplit("/", 1)[-1])
                remove = native["evidence_ids"][index] in allowed
            except (ValueError, IndexError):
                remove = False
        if code == "rule_proposal_evidence":
            remove = bool(set(native["evidence_ids"]) & set(evidence))
        (removed if remove else retained).append(copy.deepcopy(error))
    report["errors"] = retained
    report["eligibility"] = corrected_eligibility
    if native["decision"] != expected:
        report["errors"].append(_error("missed_supported_proposal" if observed else "rule_ineligible_proposal", "/decision",
            "정확한 유효 관측 여부에 맞게 제안 또는 조건 보존 보류를 선택해야 합니다.", native["decision"], [expected]))
    if native["decision"] == "proposed" and native["basis"] != "observational_review":
        report["errors"].append(_error("rule_basis", "/basis", "존재·부재 경계는 관측 SR입니다.", native["basis"], ["observational_review"]))
    for index, supplied in enumerate(native["evidence_ids"]):
        if supplied not in allowed and not any(e.get("code") == "rule_evidence_scope" and e.get("actual_path") == f"/evidence_ids/{index}" for e in report["errors"]):
            report["errors"].append(_error("rule_evidence_scope", f"/evidence_ids/{index}", "동일 변수·조건에 속한 근거만 사용할 수 있습니다.", supplied, sorted(allowed)))
    if native["decision"] == "proposed" and not set(native["evidence_ids"]) & set(evidence):
        if not any(e.get("code") == "rule_proposal_evidence" for e in report["errors"]):
            report["errors"].append(_error("rule_proposal_evidence", "/evidence_ids", "관측 SR는 코드 측정 근거를 참조해야 합니다.", native["evidence_ids"], evidence))
    report["passed"] = not report["errors"]
    semantics = validate_trigger_semantics(native, inventory, registry, validation_report=report, project_root=project_root)
    report["trigger_semantics"] = semantics
    report["errors"].extend(copy.deepcopy(semantics.get("errors", [])))
    report["passed"] = not report["errors"]
    report["executable"] = report["passed"] and native["decision"] == "proposed" and semantics.get("executable", False)
    report["counts_as_valid_rule"] = report["executable"]
    report["structural_zero_policy_application"] = {"version": STRUCTURAL_ZERO_POLICY_VERSION, "policy_scope": POLICY_SCOPE,
        "removed_legacy_policy_errors": removed, "all_unrelated_errors_retained": True,
        "condition_changed": False, "input_definition_changed": False, "old_policy_changed": False,
        "numeric_threshold_general_exemption": False, "historical_outputs_changed": False}
    return report


def validate_review_request(review, inventory, registry, *, project_root=ROOT, source_policy=None,
                            source_admission=None, relationship_capture_scope=None):
    proof, failures = _admit(inventory, source_policy=source_policy, source_admission=source_admission, project_root=project_root)
    if failures:
        return {"passed": False, "version": VERSION, "errors": failures, "executable": False,
                "counts_as_valid_rule": False, "source_admission": proof}
    result = frozen.validate_review_request(review, inventory, registry, project_root=project_root)
    result["source_admission"] = proof
    return result


def build_subject_registry(inventory, variables, validation, measurements, *, project_root=ROOT,
                           bindings=None, source_policy=None, source_admission=None, relationship_capture_scope=None):
    proof, failures = _admit(inventory, source_policy=source_policy,
                            source_admission=source_admission, project_root=project_root)
    if failures:
        raise ValueError("Source admission rejected before rule subject replay: " + str(failures))
    from generalized_measurement_v21 import measure_plan, verify_scope_deferred_measurement
    checks = validation.get("variables", []) if isinstance(validation, dict) else validation
    checks = checks if isinstance(checks, list) else []
    variables = variables if isinstance(variables, list) else []
    measurements = measurements if isinstance(measurements, list) else []
    ids = [value.get("id") for value in variables if isinstance(value, dict)]
    # Raw NOT NULL paths have no child_count; replay them independently even
    # when a derived variable's valid definition has a blocked capture scope.
    raw_registry = frozen.build_subject_registry(inventory, [], {"variables": []}, [], project_root=project_root)
    compiled_subjects = []
    for index, original in enumerate(variables):
        plan = copy.deepcopy(original)
        candidate_id = plan.get("id") if isinstance(plan, dict) else None
        checked = validate_plan(plan, inventory)
        provided = [value for value in checks if value.get("plan_id") == candidate_id and value.get("index", index) == index]
        supplied = provided[0] if len(provided) == 1 else None
        valid = bool(checked.get("passed") and candidate_id is not None and ids.count(candidate_id) == 1
                     and supplied is not None and supplied.get("passed") is True)
        definition = checked.get("definition_sha256") if valid else None
        if valid and supplied.get("definition_sha256", supplied.get("identity_sha256")) != definition:
            valid, definition = False, None
        matching = [value for value in measurements if value.get("candidate_id") == candidate_id]
        measured = copy.deepcopy(matching[0]) if len(matching) == 1 else None
        binding = bindings[index] if bindings and index < len(bindings) else None
        measure_valid, deferred_scope_proof, rejected_proof = False, None, None
        if valid and measured:
            if measured.get("measurement_scope_gate"):
                deferred_scope_proof = verify_scope_deferred_measurement(plan, inventory, measured, project_root=project_root,
                    relationship_capture_scope=relationship_capture_scope, source_policy=source_policy, source_admission=source_admission)
                measure_valid = deferred_scope_proof["passed"]
            else:
                replay = measure_plan(plan, inventory, project_root=project_root,
                    relationship_capture_scope=relationship_capture_scope, source_policy=source_policy, source_admission=source_admission)
                measure_valid = replay.get("status") in {"measured", "deferred"} and replay == measured
        elif measured and measured.get("status") == "rejected" and supplied is not None and supplied.get("passed") is False:
            # Reproduce the exact code rejection, including the outer
            # definition gate, before keeping a failed record in model input.
            # This does not read rows or admit an executable definition.
            replay = measure_plan(plan, inventory, project_root=project_root, definition_validation=supplied,
                relationship_capture_scope=relationship_capture_scope, source_policy=source_policy, source_admission=source_admission)
            binding_valid = True
            if binding is not None:
                intent = binding.get("intent") if isinstance(binding, dict) else None
                adapter = binding.get("plan") or binding.get("rejected_adapter") if isinstance(binding, dict) else None
                binding_valid = (isinstance(intent, dict) and intent.get("id") == candidate_id
                    and binding.get("passed") is False and binding.get("intent_sha256") == digest(intent)
                    and isinstance(adapter, dict) and adapter == plan
                    and supplied.get("intent_sha256") == binding["intent_sha256"])
                if binding_valid:
                    # The runner archives this same explicit compiler lineage.
                    # Replay it on a fresh code record, never on the response.
                    replay["rejected_adapter_sha256"] = replay.get("proposal_sha256")
                    replay["proposal_sha256"] = binding["intent_sha256"]
                    replay["intent_sha256"] = binding["intent_sha256"]
                    replay["failed_proposal_origin"] = "unchanged_model_intent"
            measure_valid = bool(binding_valid and replay.get("status") == "rejected"
                and replay.get("definition_sha256") is None and replay.get("source_evidence") is None
                and replay == measured)
            rejected_proof = {"version": "1.0.0", "passed": measure_valid,
                "code_rejection_replay_sha256": digest(replay), "provided_rejected_record_sha256": digest(measured),
                "outer_definition_validation_sha256": digest(supplied), "intent_binding_verified": binding_valid,
                "execution_reference_issued": False, "usable_observation": False,
                "source_rows_read_for_failed_definition": False, "original_model_proposal_changed": False}
        item = {"reference": {"kind": "variable", "variable_id": candidate_id, "definition_sha256": definition},
            "proposal_sha256": checked.get("proposal_sha256", digest(original)), "definition_sha256": definition,
            "definition_status": "valid" if valid else "invalid", "measurement_status": measured.get("status", "rejected") if measured and measure_valid else "rejected",
            "inferred_type": checked.get("inferred_type"), "shared_context": subjects._context(plan) if valid else None,
            "plan": plan, "measurement": measured if measure_valid else None,
            "validation_errors": copy.deepcopy((supplied or checked).get("errors", [])),
            "evidence_ids": [measured["source_evidence"]["id"]] if measure_valid and measured.get("source_evidence") else []}
        if deferred_scope_proof is not None:
            item["measurement_scope_verification"] = deferred_scope_proof
        if rejected_proof is not None:
            item["rejected_measurement_verification"] = rejected_proof
        item["intent_sha256"] = binding.get("intent_sha256") if binding else None
        item["code_materialization"] = copy.deepcopy(binding.get("code_materialization")) if binding else None
        if binding and not valid:
            item["rejected_adapter_sha256"] = item["proposal_sha256"]
            item["proposal_sha256"] = binding["intent_sha256"]
            item["failed_proposal_origin"] = "unchanged_model_intent"
        item["execution_reference"] = copy.deepcopy(item["reference"]) if valid else None
        item["failed_proposal_reference"] = ({"kind": "failed_proposal", "variable_id": candidate_id,
            "proposal_sha256": item["proposal_sha256"]} if not valid else None)
        compiled_subjects.append(item)
    raw_registry["subjects"] = compiled_subjects + raw_registry["subjects"]
    raw_registry["registry_sha256"] = digest(raw_registry["subjects"])
    raw_registry["scope_measurement_policy"] = "capture-measurement-1.0.0"
    return raw_registry


def _validate_scope_deferred_rule(rule, inventory, registry, subject, *, project_root,
                                  source_policy, source_admission, relationship_capture_scope):
    """Known SR conditions over blocked capture: static validation, no aggregate.

    This is deliberately not a way to approve an unknown numeric criterion or
    HR contract. It preserves valid definitions and known Boolean conditions,
    with no executable rule and no fabricated observed counts.
    """
    from generalized_measurement_v21 import measure_plan, verify_scope_deferred_measurement
    from generalized_sr_evidence_v19 import diagnostic_expression
    from generalized_trigger_semantics_v19 import _expanded_condition, validate_measurement_semantics
    result = {"passed": False, "version": VERSION, "validation_policy_version": VALIDATION_POLICY_VERSION,
        "errors": frozen._schema_report(rule, "rules"), "eligibility": subjects._deferred(),
        "predicate_plan": None, "predicate_measurement": None, "condition_output_context": None,
        "input_definition_sha256": subject["definition_sha256"], "input_definition_status": subject["definition_status"],
        "input_measurement_status": subject["measurement_status"], "bound_input_context": copy.deepcopy(subject["shared_context"]),
        "executable": False, "counts_as_valid_rule": False, "variable_rewritten": False,
        "scope_deferred_static_validation": True, "child_statistics_execution": "not_run",
        "anomaly_execution": "not_run"}
    if result["errors"]:
        return result
    verified = verify_scope_deferred_measurement(subject["plan"], inventory, subject["measurement"],
        project_root=project_root, source_policy=source_policy, source_admission=source_admission,
        relationship_capture_scope=relationship_capture_scope)
    result["input_scope_verification"] = verified
    result["errors"].extend(copy.deepcopy(verified["errors"]))
    checked = validate_plan(subject["plan"], inventory)
    annotated = {**subject, "lineage": checked.get("identity", {}).get("source_columns", [])}
    expanded, failures = _expanded_condition(rule, annotated)
    result["errors"].extend(copy.deepcopy(failures))
    result["errors"].extend(copy.deepcopy(validate_measurement_semantics(expanded)["errors"]))
    known = structural_zero_expression(expanded) is not None or diagnostic_expression(expanded) is not None
    if not known or rule.get("threshold_policy") is not None:
        result["errors"].append(_error("scope_deferred_unknown_criterion", "/condition",
            "알려진 조건만 관측 보류로 보존합니다. 확인되지 않은 수치 기준은 조건 없는 근거 요청이 필요합니다.", rule.get("condition"),
            ["known Boolean missing/categorical/structural-presence diagnostic", "conditionless criterion review"]))
    if rule.get("role") != "SR":
        result["errors"].append(_error("scope_deferred_HR_requires_contract_review", "/role",
            "관측 범위가 보류된 파생값으로 HR 계약을 만들 수 없습니다. 원시 NOT NULL 경로는 별도로 유지합니다.", rule.get("role"), ["SR"]))
    if rule.get("decision") != "deferred" or rule.get("basis") != "insufficient_evidence":
        result["errors"].append(_error("scope_deferred_rule_state", "/decision",
            "관계 범위와 관측을 확인하기 전에는 조건 보존 SR 보류를 사용해야 합니다.",
            {"decision": rule.get("decision"), "basis": rule.get("basis")}, [{"decision": "deferred", "basis": "insufficient_evidence"}]))
    if rule.get("evidence_ids"):
        result["errors"].append(_error("scope_deferred_observation_evidence", "/evidence_ids",
            "집계를 실행하지 않았으므로 관측 근거를 주장하지 않습니다.", rule["evidence_ids"], [[]]))
    predicate = copy.deepcopy(subject["plan"])
    predicate.update(id="rule_predicate_" + rule["id"], purpose="observational_diagnostic", expression=expanded,
                     unit={"name": "boolean", "status": "verified", "evidence_ids": []})
    predicate["missing_policy"]["input"] = "count_missing" if isinstance(expanded, dict) and expanded.get("op") == "is_missing" else "propagate"
    physical = validate_plan(predicate, inventory)
    result["errors"].extend(copy.deepcopy(physical["errors"]))
    if physical.get("inferred_type") != "boolean":
        result["errors"].append(_error("rule_condition_boolean", "/condition", "조건은 Boolean 결과여야 합니다.", physical.get("inferred_type"), ["boolean"]))
    result["predicate_plan"] = predicate
    result["condition_output_context"] = {"inferred_type": physical.get("inferred_type"), "unit": copy.deepcopy(predicate["unit"]),
                                         "missing_policy": copy.deepcopy(predicate["missing_policy"]), "type_validated": physical["passed"]}
    if result["errors"]:
        return result
    measured = measure_plan(predicate, inventory, project_root=project_root, source_policy=source_policy,
                            source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
    predicate_proof = verify_scope_deferred_measurement(predicate, inventory, measured, project_root=project_root,
        source_policy=source_policy, source_admission=source_admission, relationship_capture_scope=relationship_capture_scope)
    result["predicate_measurement"] = measured
    result["predicate_scope_verification"] = predicate_proof
    result["errors"].extend(copy.deepcopy(predicate_proof["errors"]))
    result["eligibility"] = subjects._deferred("계산식은 유효하지만 관계 포착 범위가 미확인되어 측정하지 않았습니다. 알려진 조건을 보존하고 추가 관계 근거를 요청합니다.")
    result["passed"] = not result["errors"]
    if result["passed"]:
        semantics = validate_trigger_semantics(rule, inventory, registry, validation_report=result, project_root=project_root)
        result["trigger_semantics"] = semantics
        result["errors"].extend(copy.deepcopy(semantics.get("errors", [])))
        result["passed"] = not result["errors"]
    return result


public_subject_registry = frozen.public_subject_registry
