"""피처 v5·Rule v2 응답을 측정 근거와 운영 변수 계약에 연결한다."""
from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from derived_expression import TABLE, FIELDS, validate, unit
from generation_client import generate
from research_bundle import policy, verify
from feature_runtime import calculate_features


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=2)
def adapter_sha(root: str, kind: str):
    bundle = verify(Path(root))
    manifest = json.loads((bundle / "design/manifest.json").read_text())
    return manifest["adapters"]["feature_v5" if kind == "feature" else "rule_v2"]["sha256"]


def checked_generate(kind, request):
    receipt = generate(kind, request)
    if receipt.get("adapterSha") != adapter_sha(os.environ["RESEARCH_BUNDLE_ROOT"], kind):
        raise ValueError("generation_adapter_version_mismatch")
    return receipt


def summary(values):
    finite = [float(v) for v in values if v is not None and not isinstance(v, bool) and math.isfinite(float(v))]
    return {"row_count": len(values), "valid_count": len(finite), "missing_count": len(values) - len(finite),
            "true_count": None, "false_count": None, "variation": "variable" if len(set(finite)) > 1 else "constant",
            "min": min(finite) if finite else None, "max": max(finite) if finite else None,
            "p95": float(np.quantile(finite, .95)) if finite else None, "quantile_method": "linear_(n-1)*p"}


def feature_request(rows):
    columns = []
    for physical, (key, kind) in FIELDS.items():
        present = [row.get(key) for row in rows]
        numeric = []
        if kind == "number":
            for value in present:
                try:
                    numeric.append(float(value) if value is not None and not isinstance(value, bool) and math.isfinite(float(value)) else None)
                except (ValueError, TypeError, OverflowError):
                    numeric.append(None)
        columns.append({"name": physical, "type": "number" if kind == "number" else "datetime",
                        "nullable": None, "unit": {"name": "count" if kind == "number" else None,
                                                    "status": "verified" if kind == "number" else "unknown",
                                                    "evidence_ids": ["process-count-contract-v1"] if kind == "number" else []},
                        "statistics": {"row_count": len(rows),
                                       "missing_count": sum(v is None for v in present),
                                       "distinct_nonmissing": len({str(v) for v in present if v is not None}),
                                       "invalid_type_count": sum(v is not None for v in present) - sum(v is not None for v in numeric) if kind == "number" else 0,
                                       "numeric_summary": summary(numeric) if numeric else None}})
    columns.append({"name": "OBSERVATION_ID", "type": "string", "nullable": False,
                    "unit": {"name": None, "status": "unknown", "evidence_ids": []},
                    "statistics": {"row_count": len(rows), "missing_count": 0,
                                   "distinct_nonmissing": len(rows), "invalid_type_count": 0,
                                   "numeric_summary": None},
                    "description": "Internal immutable observation key; not a model value or business identifier"})
    schema_version = digest(columns)
    source_version = digest(rows)
    return {"contract": "feature-request-discovery-2.0", "schema_version": schema_version,
            "source_version": source_version, "task_kind": "discovery",
            "task": "Propose up to two nonduplicate derived Process row calculations from supplied physical fields.",
            "tables": [{"table": TABLE, "primary_key": ["OBSERVATION_ID"], "rows": len(rows), "columns": columns}],
            "relationships": [], "capture": {}, "source_context": {"source_kind": "persisted_process_snapshot",
            "table_rows": {TABLE: len(rows)}}, "unknown": ["business constraints", "common source clock"],
            "existing_definitions": [], "supported_ops": ["column", "literal", "is_missing", "subtract", "divide",
            "eq", "lt", "lte", "gt", "gte", "and", "datetime_diff", "convert_unit"], "confirmed_evidence": [],
            "analysis_request": {"kind": "discovery", "grain_table": TABLE}}


def compile_features(request, receipt):
    output = receipt["output"]
    candidates = output.get("candidates")
    if set(output) != {"candidates", "stop_reason"} or not isinstance(candidates, list) or len(candidates) > 2:
        raise ValueError("invalid_feature_model_response")
    definitions, reviews = [], []
    for candidate in candidates:
        try:
            if not isinstance(candidate, dict) or set(candidate) != {
                "id", "purpose", "meaning", "sources", "expected_type", "expected_unit",
                "availability", "required_evidence", "plan"
            } or candidate["availability"] != "offline_captured_scope":
                raise ValueError("invalid_feature_candidate")
            if not isinstance(candidate["meaning"], str) or not 0 < len(candidate["meaning"]) <= 2000:
                raise ValueError("invalid_feature_meaning")
            if not isinstance(candidate["required_evidence"], list) or not all(isinstance(value, str) for value in candidate["required_evidence"]):
                raise ValueError("invalid_feature_required_evidence")
            plan = candidate["plan"]
            if plan["schema_version"] != request["schema_version"] or plan["source_version"] != request["source_version"]:
                raise ValueError("feature_source_version_mismatch")
            if plan["grain"] != {"table": TABLE, "keys": ["OBSERVATION_ID"]} or plan["population"] != {"table": TABLE, "filter": None} or plan["group_by"] != []:
                raise ValueError("unsupported_live_feature_population")
            missing = plan["missing_policy"]
            if missing["input"] not in {"propagate", "count_missing"} or missing["unknown_predicate"] != "defer":
                raise ValueError("unsupported_live_missing_policy")
            ast = plan["expression"]
            kind = validate(ast)
            def operations(node):
                found = {node["op"]}
                for child in node.get("args", []):
                    found |= operations(child)
                return found
            if "divide" in operations(ast) and missing.get("denominator_zero") != "missing":
                raise ValueError("unsupported_live_zero_division_policy")
            if candidate["expected_unit"] != unit(ast):
                raise ValueError("feature_output_unit_mismatch")
            if missing.get("empty_group") is not None or missing.get("child_missing") != "propagate":
                raise ValueError("unsupported_live_missing_policy")
            def dependencies(node):
                found = {(node["table"], node["column"])} if node["op"] == "column" else set()
                for child in node.get("args", []):
                    found |= dependencies(child)
                return found
            if set(map(tuple, candidate["sources"]["columns"])) != dependencies(ast):
                raise ValueError("feature_source_dependency_mismatch")
            expected = "number" if candidate["expected_type"] in {"integer", "number"} else candidate["expected_type"]
            if expected != kind or kind not in {"number", "boolean"}:
                raise ValueError("feature_output_type_mismatch")
            if ast["op"] in {"column", "literal"}:
                raise ValueError("feature_not_derived")
            if candidate["sources"]["relationships"]:
                raise ValueError("unsupported_live_relationship")
            expression = {"op": "derived", "ast": ast, "outputType": kind,
                          "unit": candidate["expected_unit"], "provenance": {
                          "adapterSha": receipt.get("adapterSha"), "model": "feature_v5",
                          "sourceVersion": request["source_version"], "schemaVersion": request["schema_version"],
                          "meaning": candidate["meaning"], "requiredEvidence": candidate["required_evidence"],
                          "availability": "process_observation", "originalPlan": plan}}
            name = "fv5_" + digest(expression)[:24]
            if name not in {d["name"] for d in definitions}:
                definitions.append({"name": name, "expression": expression})
        except (KeyError, TypeError, ValueError) as exc:
            reviews.append({"state": "deferred", "reason": str(exc) if isinstance(exc, ValueError) else "invalid_feature_candidate"})
    return {"definitions": definitions, "reviews": reviews, "stopReason": output["stop_reason"], "provider": "feature_v5"}


def recommend_features(rows):
    request = feature_request(rows)
    return compile_features(request, checked_generate("feature", request))


def rule_request(rows, definition, feature_set_id):
    name, expression = definition["name"], definition["expression"]
    metadata = expression.get("provenance", {})
    typ = expression.get("outputType", "number")
    unit = expression.get("unit") if expression.get("op") == "derived" else "count" if expression.get("op") == "field" else None
    values = []
    for row in rows:
        value = row["features"].get(name)
        try:
            values.append(float(value) if value is not None and math.isfinite(float(value)) else None)
        except (ValueError, TypeError, OverflowError):
            values.append(None)
    measured = summary(values)
    reference = summary([value for value, row in zip(values, rows) if row.get("label") != "anomaly"])
    if typ == "boolean":
        measured["true_count"] = sum(v == 1 for v in values)
        measured["false_count"] = sum(v == 0 for v in values)
    feature_sha = digest(definition)
    measurement_id = "m_" + digest(measured)[:24]
    ast = expression.get("ast", {"op": "column", "table": TABLE, "column": name})
    baseline = {"state": "unavailable", "needed": ["physical_or_comparable_calculation_unit"]}
    if unit in {"count", "ratio", "ms", "s", "us"}:
        if reference["valid_count"] >= 20:
            baseline = {"state": "verified", "id": "b_" + digest([feature_sha, reference])[:24],
                        "unit": unit, "n": reference["valid_count"], "minimum_n": 20,
                        "value": reference["p95"], "value_exact": str(reference["p95"]), "method": reference["quantile_method"],
                        "selection": "train split, known anomaly labels excluded; unlabeled values retained"}
        else:
            baseline = {"state": "unavailable", "needed": ["valid_reference_values" if not reference["valid_count"] else "minimum_reference_sample"]}
    return {"input_contract": "feature-evidence-bundle-4.0.0",
            "feature": {"id": name, "sha": feature_sha, "meaning": metadata.get("meaning", name),
                        "dtype": typ, "unit": unit, "expression": ast},
            "measurement_id": measurement_id, "measurement": measured, "measurement_sha": digest(measured),
            "scope": {"selection": "snapshot train split only", "rows": len(rows), "featureSetId": feature_set_id},
            "sources": {"columns": [], "relationships": []}, "constraints": [], "baseline": baseline,
            "request": "observational_diagnostic" if typ == "boolean" else "numeric_reference_comparison",
            "reference_policy": {"minimum_n": 20, "operator": "gt"},
            "range_proof": {"kind": typ, "lower": 0 if typ == "boolean" else None, "upper": 1 if typ == "boolean" else None},
            "needed_evidence_catalog": {"usable_calculated_observations": "Usable outputs required",
             "valid_reference_values": "Eligible reference outputs required", "minimum_reference_sample": "At least 20 values required",
             "physical_or_comparable_calculation_unit": "Verified unit required"},
            "version": {"source": metadata.get("sourceVersion", digest(measured)), "calculation": "safe-derived-process-v2"},
            "availability": {"mode": "process_observation"}, "unknown": ["business constraints"],
            "evidence_contract": {"reference_p95": "measurement and baseline IDs"}, "output_contract_version": "bounded-rule-1.2.0"}


def recommend_rules(rows, feature_set_id, definitions):
    validator = policy(Path(os.environ["RESEARCH_BUNDLE_ROOT"]))
    suggestions, reviews = [], []
    for definition in definitions:
        request = rule_request(rows, definition, feature_set_id)
        receipt = checked_generate("rule", request)
        output = receipt["output"]
        validation = validator.validate_rule(request, output)
        facts = validator.factual_review(request, output) if validation["schema_passed"] else {
            "passed": False, "errors": ["invalid_rule_schema"]}
        visible_output = output if validation["schema_passed"] else {
            "state": "rejected", "reason": "Rule output schema validation failed"}
        review = {"featureName": definition["name"], "output": visible_output,
                  **({"rawOutput": output} if not validation["schema_passed"] else {}),
                  "validation": validation, "facts": facts, "adapterSha": receipt.get("adapterSha")}
        reviews.append(review)
        if not validation["passed"] or not facts["passed"] or output["state"] != "proposed":
            continue
        condition = output["condition"]
        if condition.get("baseline"):
            operator, value = "gt", str(request["baseline"]["value"])
        elif condition.get("op") == "eq" and isinstance(condition.get("value"), bool):
            operator, value = "eq", str(int(condition["value"]))
        else:
            # 보류·적용 불가·현재 실행 DSL에 없는 조건은 운영 초안으로 바꾸지 않는다.
            continue
        key = "sr_" + digest([definition, condition, request["baseline"]])[:24]
        suggestions.append({"ruleKey": key, "displayName": request["feature"]["meaning"][:120],
                            "condition": {"field": "F." + definition["name"], "operator": operator, "value": value},
                            "outcome": {"decisionCode": "SR_DIAGNOSTIC", "severity": "warning"},
                            "evidenceSpec": {"model": "rule_v2", "request": request, "decision": output,
                                             "adapterSha": receipt.get("adapterSha"), "meaningReviewRequired": True}})
    return {"suggestions": suggestions, "reviews": reviews, "provider": "rule_v2"}
