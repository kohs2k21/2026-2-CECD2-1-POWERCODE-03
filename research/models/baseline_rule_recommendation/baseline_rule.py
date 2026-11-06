"""One-slot, baseline-only Rule v3 interface for TRAIN reference evidence.

No source compiler is present: upstream row values and their lineage are never
represented as compiler-authoritative facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import jsonschema

CONTRACT = "baseline-rule-recommendation1.0"
MIN_REFERENCE_N = 20
REFERENCE_QUANTILE = Decimal("0.95")
NEEDED_KEYS = (
    "need_reference", "need_reference_minimum_n", "need_compatible_reference",
    "need_verified_unit", "need_valid_observations", "need_confirmed_constraint",
    "need_business_contract",
)
NEEDED_KEY_LABELS_KO = {
    "need_reference": "TRAIN 기준자료 필요",
    "need_reference_minimum_n": "기준 계산에 필요한 최소 유효 TRAIN 표본 부족",
    "need_compatible_reference": "정의·기간·분할이 호환되는 기준자료 필요",
    "need_verified_unit": "검증된 단위 필요",
    "need_valid_observations": "조건을 평가할 유효 관측 필요",
    "need_confirmed_constraint": "확정된 제약 근거 필요",
    "need_business_contract": "업무 계약 근거 필요",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_NORMAL_LABELS = {"normal", "normality", "non_anomaly", "non-anomaly", "0"}
_ANOMALY_LABELS = {"anomaly", "abnormal", "incident", "1"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _schema_path() -> Path:
    return Path(__file__).resolve().parent / "contracts/rule.schema.json"


def output_schema() -> dict[str, Any]:
    """Reuse the canonical full Rule v3 schema with exactly one baseline slot."""
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    decisions = schema["properties"]["decisions"]
    decisions["minItems"] = decisions["maxItems"] = 1
    decisions["items"]["properties"]["request"] = {"enum": ["baseline"]}
    decisions["items"]["properties"]["role"] = {"enum": ["SR"]}
    decisions["items"]["properties"]["basis"] = {"enum": ["reference_p95", "none", "insufficient"]}
    decisions["items"]["properties"]["needed"]["items"]["enum"] = list(NEEDED_KEYS)
    return schema


RULE_SCHEMA = output_schema()


def _event_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    else:
        raise ValueError("missing or invalid event_time")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event_time must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _tx_key(row: dict[str, Any]) -> Any:
    for field in ("transaction_key", "source_transaction_id", "transaction_id"):
        value = row.get(field)
        if value is not None and str(value).strip():
            return value
    raise ValueError("missing transaction relationship key")


def _label_class(value: Any) -> str:
    if value is None:
        return "unknown"
    if type(value) is bool:
        return "normal" if not value else "anomaly"
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return "normal" if value == 0 else "anomaly" if value == 1 else "unknown"
    if isinstance(value, str):
        label = value.strip().lower()
        if label in _NORMAL_LABELS:
            return "normal"
        if label in _ANOMALY_LABELS:
            return "anomaly"
    return "unknown"


def split_train(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Split chronological 80:20 by whole transaction/timestamp groups.

    Invalid timestamps, transaction keys, or observation IDs block the split;
    there is no random, UUID-order, or input-order fallback.
    """
    policy = {"name": "train-internal-80-20-chronological-v1", "reference_fraction": 0.8,
              "time_basis": "persisted_at", "field": "event_time",
              "grouping": ["transaction_key", "equal_event_time"],
              "boundary": "nearest_group_boundary_to_80_percent",
              "input_order_used": False, "identifier_order_used": False}
    rows = list(records or [])
    issues: list[str] = []
    if not rows:
        issues.append("empty_train_records")
    parsed: list[tuple[datetime, str, Any]] = []
    seen: set[str] = set()
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("record must be an object")
            obs = row.get("observation_id")
            if obs is None or not str(obs).strip():
                raise ValueError("missing observation_id")
            obs = str(obs)
            if obs in seen:
                raise ValueError("duplicate observation_id")
            seen.add(obs)
            parsed.append((_event_time(row.get("event_time")), obs, _tx_key(row)))
    except (TypeError, ValueError, OverflowError) as exc:
        return {"reference": [], "application": [], "policy": policy, "issues": [*issues, str(exc)]}

    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_tx: dict[bytes, int] = {}
    by_time: dict[datetime, int] = {}
    for i, (when, _obs, tx) in enumerate(parsed):
        tx_token = _canonical(tx)
        if tx_token in by_tx:
            union(i, by_tx[tx_token])
        else:
            by_tx[tx_token] = i
        if when in by_time:
            union(i, by_time[when])
        else:
            by_time[when] = i
    components: dict[int, list[int]] = {}
    for i in range(len(rows)):
        components.setdefault(find(i), []).append(i)
    ordered = sorted(components.values(), key=lambda ids: min(parsed[i][0] for i in ids))
    if len(ordered) < 2:
        return {"reference": [], "application": [], "policy": policy,
                "issues": ["fewer_than_two_chronological_groups"]}
    total = len(rows)
    choices = []
    cumulative = 0
    ends = [max(parsed[i][0] for i in group) for group in ordered]
    starts = [min(parsed[i][0] for i in group) for group in ordered]
    suffix_start = starts[:]
    for index in range(len(starts) - 2, -1, -1):
        suffix_start[index] = min(starts[index], suffix_start[index + 1])
    ref_end = ends[0]
    for cut, group in enumerate(ordered[:-1], start=1):
        cumulative += len(group)
        ref_end = max(ref_end, ends[cut - 1])
        if ref_end >= suffix_start[cut]:
            continue
        choices.append((abs(Decimal(cumulative) / Decimal(total) - Decimal("0.8")), cumulative, cut))
    if not choices:
        return {"reference": [], "application": [], "policy": policy,
                "issues": ["no_nonoverlapping_group_boundary"]}
    _distance, _size, cut = min(choices, key=lambda item: (item[0], item[1]))
    ref_indices = [i for group in ordered[:cut] for i in group]
    app_indices = [i for group in ordered[cut:] for i in group]
    reference = [rows[i] for i in sorted(ref_indices, key=lambda i: parsed[i][0])]
    application = [rows[i] for i in sorted(app_indices, key=lambda i: parsed[i][0])]
    reference_end = max(parsed[i][0] for i in ref_indices)
    application_start = min(parsed[i][0] for i in app_indices)
    policy.update(reference_rows=len(reference), application_rows=len(application),
                  reference_max_time_inclusive=reference_end.isoformat(),
                  application_start_inclusive=application_start.isoformat(),
                  reference_fraction_actual=len(reference) / total)
    if reference_end >= application_start:
        issues.append("chronological_period_overlap")
    if {_canonical(parsed[i][2]) for i in ref_indices} & {_canonical(parsed[i][2]) for i in app_indices}:
        issues.append("transaction_crosses_partition")
    if set(ref_indices) & set(app_indices) or len(ref_indices) + len(app_indices) != total:
        issues.append("partition_coverage_error")
    return {"reference": reference, "application": application, "policy": policy, "issues": issues}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _p95(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("p95 requires values")
    with localcontext() as ctx:
        ctx.prec = 50
        pos = Decimal(len(ordered) - 1) * REFERENCE_QUANTILE
        lo = int(pos)
        fraction = pos - lo
        hi = min(lo + 1, len(ordered) - 1)
        return +(ordered[lo] + (ordered[hi] - ordered[lo]) * fraction)


def _unit_known(unit: Any) -> bool:
    if isinstance(unit, str):
        return bool(unit.strip()) and unit.strip().lower() not in {"unknown", "unverified", "unconfirmed", "unspecified", "n/a", "null", "none"}
    if isinstance(unit, dict):
        return (isinstance(unit.get("name"), str) and _unit_known(unit["name"])
                and str(unit.get("status", "")).lower() in {"verified", "confirmed"})
    return False


def _evidence_id(prefix: str, payload: Any) -> str:
    return prefix + "_" + _digest(payload)[:24]


def _partition_for(records: list[dict[str, Any]], context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    supplied = context.get("partition")
    partition = supplied if isinstance(supplied, dict) else split_train(records)
    issues = list(partition.get("issues") or [])
    ref, app = partition.get("reference"), partition.get("application")
    if not isinstance(ref, list) or not isinstance(app, list):
        return {"reference": [], "application": [], "policy": partition.get("policy", {})}, [*issues, "invalid_partition_shape"]
    try:
        all_ids = [str(row["observation_id"]) for row in records]
        part_ids = [str(row["observation_id"]) for row in ref + app]
    except (TypeError, KeyError):
        return {"reference": [], "application": [], "policy": partition.get("policy", {})}, [*issues, "partition_missing_observation_id"]
    if len(set(all_ids)) != len(all_ids) or len(set(part_ids)) != len(part_ids) or set(all_ids) != set(part_ids):
        issues.append("partition_does_not_cover_train_rows_exactly")
    originals = {str(row.get("observation_id")): row for row in records}
    if any(originals.get(str(row.get("observation_id"))) != row for row in ref + app):
        issues.append("partition_row_content_mismatch")
    policy = partition.get("policy") or {}
    declared_policy = context.get("split_policy")
    if isinstance(declared_policy, str) and declared_policy != policy.get("name"):
        issues.append("partition_policy_mismatch")
    elif isinstance(declared_policy, dict) and declared_policy != policy:
        issues.append("partition_policy_mismatch")
    try:
        if max(_event_time(row["event_time"]) for row in ref) >= min(_event_time(row["event_time"]) for row in app):
            issues.append("chronological_period_overlap")
        ref_tx = {_canonical(_tx_key(row)) for row in ref}
        app_tx = {_canonical(_tx_key(row)) for row in app}
        if ref_tx & app_tx:
            issues.append("transaction_crosses_partition")
    except (ValueError, KeyError, TypeError):
        issues.append("partition_missing_time_or_transaction_key")
    return {"reference": ref, "application": app, "policy": policy}, sorted(set(issues))


NEEDED_LABELS = NEEDED_KEY_LABELS_KO


def _finite_values(rows, feature, *, reference=False):
    values = []
    for row in rows:
        if reference and _label_class(row.get("label")) == "anomaly":
            continue
        features = row.get("features") or {}
        value = None if feature in (row.get("missing") or []) else _decimal(features.get(feature))
        if value is not None:
            values.append(value)
    return values


def _rows_sha(rows):
    # Bounded model input: no row IDs or per-row hashes go to the LLM.
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: str(r.get("observation_id"))):
        h.update(_canonical(row)); h.update(b"\n")
    return h.hexdigest()


def _period(rows):
    if not rows:
        return None
    times = [_event_time(row["event_time"]) for row in rows]
    return {"start_inclusive": min(times).isoformat(), "end_inclusive": max(times).isoformat()}


def _purpose(unit):
    unit = unit.get("name") if isinstance(unit, dict) else unit
    return "count" if unit == "count" else "ratio" if unit == "ratio" else "other"


def build_request(records, definition, context=None):
    """Measure full TRAIN partitions, expose only a bounded evidence summary."""
    context = dict(context or {})
    name, sha = definition.get("name"), definition.get("definition_sha")
    if not isinstance(name, str) or not _FIELD.fullmatch(name) or not isinstance(sha, str) or not _HEX64.fullmatch(sha):
        raise ValueError("definition requires a service feature name and sha256")
    partition, issues = _partition_for(records, context)
    if context.get("time_basis", "persisted_at") != "persisted_at":
        issues.append("unconfirmed_collection_time_basis")
    reference, application = partition["reference"], partition["application"]
    numeric = str(definition.get("dtype", "")).lower() in {"int", "integer", "float", "number", "numeric", "decimal", "double"}
    unit = definition.get("unit")
    known_unit = _unit_known(unit)
    vals = _finite_values(reference, name, reference=True) if numeric else []
    current = _finite_values(application, name) if numeric else []
    exact = _p95(vals) if len(vals) >= MIN_REFERENCE_N else None
    threshold = None
    if exact is not None:
        try:
            threshold = float(exact)
            if not math.isfinite(threshold) or (threshold == 0 and exact != 0):
                raise ValueError("threshold_float_not_representable")
            if len(format(Decimal(str(threshold)), "f")) > 40:
                raise ValueError("service_threshold_exceeds_40_characters")
        except (ValueError, OverflowError) as exc:
            issues.append(str(exc)); threshold = None
    source_sha = _digest({"source": context.get("source_id"), "snapshot": context.get("snapshot_id"),
                          "rows": _rows_sha(records), "time_basis": "persisted_at"})
    source_ref = "source_" + source_sha[:24]
    population = definition.get("population_ref") or _evidence_id("population", {"source": source_sha, "feature_set": context.get("feature_set_id"), "grain": "process_observation"})
    feature = {"id": name, "name": name, "sha": sha, "definition_sha": sha,
               "meaning": definition.get("meaning", name), "dtype": definition.get("dtype"),
               "unit": unit if known_unit else None, "unit_status": "verified" if known_unit else "unknown",
               "expression": definition.get("expression"), "population_ref": population}
    ref_period, app_period = _period(reference), _period(application)
    ref_hash, app_hash = _rows_sha(reference), _rows_sha(application)
    ref_id = _evidence_id("reference", {"source": source_sha, "rows": ref_hash, "sha": sha})
    app_id = _evidence_id("application", {"source": source_sha, "rows": app_hash, "sha": sha})
    measurement_id = _evidence_id("measurement", {"application": app_id, "sha": sha, "unit": unit})
    baseline_id = _evidence_id("baseline", {"reference": ref_id, "sha": sha, "unit": unit, "p95_exact": str(exact), "policy": partition["policy"]})
    baseline = {"id": baseline_id, "state": "verified" if threshold is not None and known_unit and not issues else "unavailable",
                "quantile": "0.95", "method": "linear_n_minus_1_p", "operator": "gt",
                "minimum_reference_n": MIN_REFERENCE_N, "valid_reference_n": len(vals),
                "value": threshold, "value_exact": str(exact) if exact is not None else None,
                "numeric_encoding": "JSON float roundtrip; service uses the same boundary, no substitution of exact Decimal",
                "unit": unit, "reference_ref": ref_id, "measurement_id": measurement_id, "period": ref_period,
                "definition_sha": sha, "population_ref": population, "group": "whole_snapshot_train",
                "missing": "exclude unavailable/nonfinite reference values; preserve unavailable application values",
                "update_policy": "frozen recommendation; explicit new snapshot and review required for update",
                "expiry_policy": "invalidate on feature/source/unit/population version mismatch; no automatic time extension",
                "anomaly_labels_excluded": True, "unlabeled_reference_included_without_normality_claim": True,
                "current_finite_numeric_n": len(current)}
    measurement = {"row_count": len(application), "valid_count": len(current), "missing_count": len(application)-len(current),
                   "min_exact": str(min(current)) if current else None, "max_exact": str(max(current)) if current else None,
                   "anomaly_labels_retained": True, "period": app_period, "rows_sha": app_hash}
    allowed_refs = [source_ref, population, ref_id, app_id, measurement_id, baseline_id]
    plan = {"purpose": _purpose(unit), "population_ref": population, "precondition_refs": [source_ref],
            "required_evaluation_basis": [measurement_id, baseline_id], "business_scope": "offline_process_train_snapshot"}
    evidence = {"reference": {"id": ref_id, "period": ref_period, "rows": len(reference), "finite_non_anomaly_n": len(vals), "rows_sha": ref_hash},
                "application": {"id": app_id, "period": app_period, "rows": len(application), "finite_numeric_n": len(current), "rows_sha": app_hash},
                "measurement_id": measurement_id, "source_ref": source_ref, "population_ref": population,
                "partition_policy": partition["policy"], "source_sha": source_sha}
    input_record = {"contract": CONTRACT, "requests": ["baseline"], "feature": feature, "baseline": baseline,
                    "measurement_id": measurement_id, "measurement": measurement,
                    "application_context": evidence, "analysis_plan": plan, "allowed_evidence_refs": allowed_refs,
                    "needed_keys": list(NEEDED_KEYS), "partition_issues": sorted(set(issues)), "blocked": bool(issues)}
    user_input = json.dumps(input_record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return {"input": input_record, "messages": [{"role": "system", "content": (Path(__file__).parent / "contracts/baseline_rule_system.txt").read_text()}, {"role": "user", "content": user_input}],
            "schema": output_schema(), "record": input_record, "blocked": bool(issues), "issues": sorted(set(issues)),
            "model_reference": {"key": "fixedRule", "weight_sha256": "d816cf6a1f264149fb3d4db9c7cc929a76b12ce6f04f0407a4c4b459dbd573c9"}}


def _record(record):
    return record.get("record", record.get("input", record))


def _requirements(source):
    feature, baseline = source["feature"], source["baseline"]
    numeric = str(feature.get("dtype", "")).lower() in {"int", "integer", "float", "number", "numeric", "decimal", "double"}
    if not numeric:
        return "no_applicable", []
    needed = []
    if source.get("blocked"):
        needed.append("need_compatible_reference")
    if not _unit_known(feature.get("unit")):
        needed.append("need_verified_unit")
    if not baseline["valid_reference_n"]:
        needed.append("need_reference")
    elif baseline["valid_reference_n"] < MIN_REFERENCE_N:
        needed.append("need_reference_minimum_n")
    if baseline["value"] is None and not needed:
        needed.append("need_compatible_reference")
    if needed:
        return "request_evidence", needed
    if baseline["current_finite_numeric_n"] == 0:
        return "deferred", ["need_valid_observations"]
    return "proposed", []


def validate_prediction(record, pred):
    """Validate the actual response; never synthesize or fix a model decision."""
    source = _record(record)
    try:
        json.dumps(pred, allow_nan=False)
        jsonschema.validate(pred, RULE_SCHEMA)
    except (jsonschema.ValidationError, ValueError, TypeError) as exc:
        return ["schema:" + str(getattr(exc, "path", "/"))]
    feature, baseline = source["feature"], source["baseline"]
    d = pred["decisions"][0]
    errors = []
    if pred["feature"] != feature["name"] or pred["sha"] != feature["definition_sha"]:
        errors.append("feature_definition_mismatch")
    expected_state, needed = _requirements(source)
    if d["state"] != expected_state:
        errors.append("state_inconsistent_with_evidence:" + expected_state)
    if set(d["needed"]) != set(needed) or len(d["needed"]) != len(needed):
        errors.append("needed_evidence_mismatch:" + ",".join(needed))
    if d["population_ref"] != feature["population_ref"]:
        errors.append("population_mismatch")
    if d["purpose"] != source["analysis_plan"]["purpose"]:
        errors.append("purpose_mismatch")
    refs = set(source["allowed_evidence_refs"])
    if any(not set(d[key]) <= refs for key in ("evidence", "precondition_refs", "required_evaluation_basis")):
        errors.append("fabricated_evidence_reference")
    if not set(source["analysis_plan"]["precondition_refs"]) <= set(d["precondition_refs"]):
        errors.append("missing_precondition_reference")
    active_condition = d["state"] in {"proposed", "deferred"}
    if active_condition:
        if d["unavailable_action"] != "preserve_unassessed" or d["basis"] != "reference_p95":
            errors.append("state_action_basis_mismatch")
        condition = d["condition"]
        if not isinstance(condition, dict) or condition.get("op") != "gt" or type(condition.get("value")) not in (float, int) or condition.get("value") != baseline["value"]:
            errors.append("threshold_mismatch")
        if d["baseline_use_ref"] != baseline["id"]:
            errors.append("baseline_reference_mismatch")
        required = {baseline["id"], baseline["measurement_id"]}
        if not required <= set(d["evidence"]) or not required <= set(d["required_evaluation_basis"]):
            errors.append("required_evidence_missing")
        # An available observation cannot be described as currently absent.
        # Use a conditional form for future unavailability, not a false current fact.
        if d["state"] == "proposed" and baseline["current_finite_numeric_n"] > 0 and not d["unavailable_reason"].startswith(("If ", "When ")):
            errors.append("unavailable_reason_requires_future_conditional_for_available_observations")
    elif d["condition"] is not None or d["unavailable_action"] != "no_rule" or d["baseline_use_ref"] is not None or d["basis"] != ("none" if d["state"] == "no_applicable" else "insufficient"):
        errors.append("no_rule_contract_mismatch")
    return errors


def convert_suggestion(record, pred):
    errors = validate_prediction(record, pred)
    if errors:
        raise ValueError("invalid_prediction:" + ",".join(errors))
    d = pred["decisions"][0]
    if d["state"] != "proposed":
        return None
    source = _record(record)
    feature, baseline = source["feature"], source["baseline"]
    value = format(Decimal(str(d["condition"]["value"])), "f")
    if len(value) > 40:
        raise ValueError("service_decimal_value_exceeds_40_characters")
    return {"ruleKey": "baseline_" + _digest({"feature": feature["name"], "sha": feature["sha"]})[:24],
            "displayName": "TRAIN 기준 초과: " + feature["name"],
            "condition": {"field": "F." + feature["name"], "operator": "gt", "value": value},
            "outcome": {"decisionCode": "SR_BASELINE", "severity": "warning"},
            "evidenceSpec": {"kind": "train_reference_p95", "request": source, "decision": pred,
                             "baselineId": baseline["id"], "measurementId": baseline["measurement_id"],
                             "referencePeriod": baseline["period"], "applicationPeriod": source["measurement"]["period"],
                             "referenceN": baseline["valid_reference_n"], "unit": baseline["unit"],
                             "definitionSha": feature["definition_sha"], "unavailableAction": d["unavailable_action"],
                             "meaningReviewRequired": True}}
