"""Connect B's native output to the existing baseline service draft DTO.

HR and observational decisions remain review records. No generated rule is
activated here. Existing Feature, AE, ML and logical SR paths stay independent.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import re

from .contract import PROVIDER, build_messages, digest, validate_input, validate_prediction

FIELD = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def build_service_request(records, definition, context=None):
    """Reuse the existing full-TRAIN split/statistics code, not v3 predictions.

    Upstream supplies verified grain/population/missing policy. This function
    does not infer physical schema, units, constraints or feature definitions.
    """
    from ..baseline_rule_recommendation import build_request

    for field in ("source_id", "snapshot_id", "feature_set_id"):
        if not (context or {}).get(field):
            raise ValueError("source identity required:" + field)
    for field in ("grain", "population", "group_by", "missing_policy", "source_columns"):
        if field not in definition:
            raise ValueError("verified feature definition required:" + field)
    old = build_request(records, definition, context)
    source = old["input"]
    if source["blocked"]:
        raise ValueError("input_partition_blocked:" + ",".join(source["partition_issues"]))
    if str(definition["dtype"]).lower() not in {"int", "integer", "float", "number", "numeric", "decimal", "double"}:
        raise ValueError("service bridge supports numeric baseline requests only; use full native input for Boolean")
    f = source["feature"]
    unit = f["unit"].get("name") if isinstance(f["unit"], dict) else f["unit"]
    feature = {"id": f["id"], "sha": f["sha"], "meaning": f["meaning"],
               "dtype": "integer" if str(definition["dtype"]).lower() in {"int", "integer"} else "number",
               "unit": unit, "expression": copy.deepcopy(f["expression"]),
               **{key: copy.deepcopy(definition[key]) for key in ("grain", "population", "group_by", "missing_policy")}}
    prior = source["baseline"]
    state = ("verified" if prior["state"] == "verified" else "unverified_unit" if unit is None else
             "insufficient_sample" if prior["valid_reference_n"] < 20 else "missing")
    # No finite upper bound is inferred from sample maxima or unit names.
    bounds = {"lower": None, "upper": None, "proof": "No universal bound supplied to this service bridge."}
    baseline = {"id": prior["id"], "state": state, "reference_split": "train",
                "reference_feature_definition_sha": f["sha"], "semantic_definition_sha": f["sha"],
                "application_semantic_definition_sha": f["sha"],
                "reference_source_version": source["application_context"]["source_sha"],
                "reference_manifest": source["application_context"]["reference"],
                "reference_period": prior["period"], "application_period": source["measurement"]["period"],
                "reference_population": feature["population"], "application_population": feature["population"],
                "group": prior["group"], "reference_valid_n": prior["valid_reference_n"], "minimum_n": 20,
                "value": prior["value"], "value_exact": prior["value_exact"],
                "numeric_encoding": prior["numeric_encoding"], "quantile": 0.95,
                "operator": "gt", "unit": unit, "method": prior["method"], "missing": prior["missing"],
                "refresh": prior["update_policy"], "expiry": prior["expiry_policy"],
                "range_proof": bounds, "scope": "TRAIN-only internal reference/current split; unlabeled does not mean confirmed normal."}
    baseline["sha256"] = digest(baseline)
    inp = {"feature": feature, "measurement_id": source["measurement_id"],
           "measurement": source["measurement"], "measurement_sha": digest(source["measurement"]),
           "measurement_sha_scheme": "canonical_summary_v1",
           "source_version": source["application_context"]["source_sha"],
           "scope": {"reference_period": prior["period"], "application_period": source["measurement"]["period"],
                     "partition_policy": source["application_context"]["partition_policy"], "offline_only": True},
           "sources": {"calculation_and_relationship_key_columns": copy.deepcopy(definition["source_columns"]),
                       "output_row_identifiers": [[feature["grain"]["table"], k] for k in feature["grain"]["keys"]]},
           "constraints": [], "baseline": baseline, "availability": "offline_captured_scope",
           "range_proof": bounds, "verified_relationship_bounds": {},
           "observed_diagnostic_contract": {"condition": {"op": "is_missing"},
                                            "meaning": "Computed feature unavailable; cause and incident meaning unconfirmed."},
           "requests": ["hr", "observed", "baseline"],
           "reference_policy": {"minimum_n": 20, "normality_required": False, "independence_required": False},
           "delivery_context": {"source_id": (context or {}).get("source_id"),
                                "snapshot_id": (context or {}).get("snapshot_id"),
                                "feature_set_id": (context or {}).get("feature_set_id"),
                                "legacy_input_sha": digest(source)}}
    errors = validate_input(inp)
    if errors:
        raise ValueError(";".join(errors))
    return {"input": inp, "messages": build_messages(inp), "input_sha": digest(inp), "provider": PROVIDER}


def convert_suggestion(inp, prediction, field_binding=None):
    """Export only validated, proposed baseline SR; preserve all three decisions."""
    errors = validate_prediction(inp, prediction)
    if errors:
        raise ValueError("invalid_prediction:" + ";".join(errors))
    f = inp["feature"]
    binding = field_binding or {"feature_id": f["id"], "definition_sha": f["sha"],
                                "unit": f["unit"], "field_name": f["id"]}
    if (binding.get("feature_id") != f["id"] or binding.get("definition_sha") != f["sha"]
            or binding.get("unit") != f["unit"] or not FIELD.fullmatch(binding.get("field_name", ""))):
        raise ValueError("service_feature_binding_mismatch")
    decision = next(d for d in prediction["decisions"] if d["request"] == "baseline")
    if decision["state"] != "proposed":
        return None
    b = inp["baseline"]
    value = format(Decimal(str(decision["condition"]["value"])), "f")
    if len(value) > 40:
        raise ValueError("service_decimal_value_exceeds_40_characters")
    identity = {"provider": PROVIDER, "feature": f["id"], "sha": f["sha"],
                "input_sha": digest(inp), "prediction_sha": digest(prediction), "binding": binding}
    return {"ruleKey": "baseline_b_" + digest(identity)[:24],
            "displayName": "TRAIN 기준 초과: " + binding["field_name"],
            "condition": {"field": "F." + binding["field_name"], "operator": "gt", "value": value},
            "outcome": {"decisionCode": "SR_BASELINE", "severity": "warning"},
            "evidenceSpec": {"kind": "train_reference_p95", "provider": PROVIDER,
                             "request": copy.deepcopy(inp), "decision": copy.deepcopy(prediction),
                             "fieldBinding": copy.deepcopy(binding), "inputSha": digest(inp),
                             "predictionSha": digest(prediction), "baselineId": b["id"],
                             "measurementId": inp["measurement_id"], "referencePeriod": b["reference_period"],
                             "applicationPeriod": b["application_period"], "referenceN": b["reference_valid_n"],
                             "unit": f["unit"], "definitionSha": f["sha"],
                             "unavailableAction": "preserve_unassessed", "meaningReviewRequired": True,
                             "automaticActivation": False,
                             "metadataOrigin": "Code-derived service DTO; original model JSON remains native v2."}}


def evaluate_draft(draft, value, *, definition_sha, unit):
    """Research execution check on an already computed scalar; no activation/API.

    Missing and invalid values are unavailable. This checks numerical behavior,
    not operational approval, incident truth or an XAI service event contract.
    """
    try:
        evidence = draft["evidenceSpec"]
        inp, prediction = evidence["request"], evidence["decision"]
        expected = convert_suggestion(inp, prediction, evidence["fieldBinding"])
        if expected != draft:
            raise ValueError("draft_integrity_mismatch")
        if definition_sha != evidence["definitionSha"] or unit != evidence["unit"]:
            raise ValueError("feature_definition_or_unit_mismatch")
        if value is None:
            raise ValueError("missing_feature_value")
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError("invalid_feature_value")
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("nonfinite_feature_value")
        threshold = Decimal(draft["condition"]["value"])
        return {"state": "warning" if number > threshold else "nonwarning",
                "rule_id": draft["ruleKey"], "observed_value": str(number),
                "threshold": str(threshold), "operator": "gt", "unit": unit,
                "definition_sha": definition_sha, "reason": None}
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        return {"state": "unavailable", "reason": str(exc),
                "rule_id": draft.get("ruleKey") if isinstance(draft, dict) else None}
