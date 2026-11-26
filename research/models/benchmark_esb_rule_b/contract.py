"""Native v2 admission, preserving the original public Rule validator.

This is not a v3 prediction upgrader. Additional checks protect the delivery
boundary; they do not rewrite a response or establish source/business truth.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

import jsonschema

from .native_policy import RULE_SYSTEM, rule_check

PACKAGE = Path(__file__).resolve().parent
PROVIDER = "rule_b_native_v2"
SHA = re.compile(r"^[0-9a-f]{64}$")
REQUESTS = ["hr", "observed", "baseline"]
BLOCKED = {"body", "request_body", "response_body", "message_body", "ec_message_body",
           "authorization", "password", "access_token", "refresh_token",
           "label", "labels", "target", "targets", "injection_applied", "pair_id", "arm"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def finite_number(value):
    if type(value) not in (int, float, Decimal):
        raise ValueError("numeric value required; Boolean/string is not a JSON number")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("finite numeric value required")
    return number


def _body_guard(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in BLOCKED:
                raise ValueError("body_secret_or_evaluation_field_rejected")
            # Column/table AST references must not circumvent key-only checking.
            if key in ("column", "table") and isinstance(child, str) and child.lower() in BLOCKED:
                raise ValueError("body_column_rejected")
            _body_guard(child)
    elif isinstance(value, list):
        for child in value:
            _body_guard(child)
    elif isinstance(value, str) and value.lower() in {"body", "request_body", "response_body", "message_body", "ec_message_body"}:
        raise ValueError("body_reference_rejected")


def validate_input(inp):
    errors = []
    try:
        canonical(inp)
        _body_guard(inp)
        if not isinstance(inp, dict) or inp.get("requests") != REQUESTS:
            raise ValueError("native_three_role_requests_required")
        required = {"feature", "measurement_id", "measurement", "measurement_sha", "source_version", "scope",
                    "sources", "constraints", "baseline", "range_proof",
                    "verified_relationship_bounds", "observed_diagnostic_contract"}
        if not required <= inp.keys():
            raise ValueError("missing_native_input_fields:" + ",".join(sorted(required - inp.keys())))
        f = inp["feature"]
        if not isinstance(f.get("id"), str) or not f["id"] or not SHA.fullmatch(f.get("sha", "")):
            raise ValueError("invalid_feature_binding")
        if not {"expression", "grain", "population", "group_by", "missing_policy", "meaning", "dtype", "unit"} <= f.keys():
            raise ValueError("incomplete_feature_definition")
        if f["dtype"] not in ("number", "integer", "boolean"):
            raise ValueError("unsupported_native_dtype")
        if f["unit"] is not None and (not isinstance(f["unit"], str) or f["unit"].lower() in {"unknown", "unverified", "unconfirmed"}):
            raise ValueError("native_unknown_unit_must_be_null")
        if not isinstance(f["expression"], dict) or not isinstance(f["expression"].get("op"), str):
            raise ValueError("feature_expression_required")
        if not isinstance(f["grain"], dict) or not f["grain"].get("table") or not f["grain"].get("keys"):
            raise ValueError("verified_grain_required")
        if not isinstance(f["population"], dict) or not f["population"].get("table"):
            raise ValueError("verified_population_required")
        if not inp["measurement_id"] or not inp["source_version"]:
            raise ValueError("measurement_and_source_version_required")
        for key in ("valid_count", "missing_count"):
            if type(inp["measurement"].get(key)) is not int or inp["measurement"][key] < 0:
                raise ValueError("invalid_measurement_count:" + key)
        stats = inp["measurement"]
        if (type(stats.get("row_count")) is not int or stats["row_count"] < 0
                or stats["row_count"] != stats["valid_count"] + stats["missing_count"]):
            raise ValueError("measurement_row_count_mismatch")
        if not SHA.fullmatch(inp["measurement_sha"]):
            raise ValueError("measurement_sha_required")
        # Historical native hashes include more lineage than the summary alone.
        # Only the new bridge's explicitly declared scheme is recomputed here.
        if (inp.get("measurement_sha_scheme") == "canonical_summary_v1"
                and inp["measurement_sha"] != digest(stats)):
            raise ValueError("measurement_summary_sha_mismatch")
        if not isinstance(inp["constraints"], list):
            raise ValueError("invalid_constraints")
        b = inp["baseline"]
        if b.get("state") == "verified":
            finite_number(b["value"])
            exact = Decimal(str(b["value_exact"]))
            if (not exact.is_finite() or (type(b["value"]) is int and exact != Decimal(b["value"]))
                    or (type(b["value"]) is float and float(exact) != b["value"])
                    or (exact != 0 and b["value"] == 0)):
                raise ValueError("baseline_numeric_encoding_mismatch")
            if (b.get("reference_split") != "train" or b.get("operator") != "gt"
                    or Decimal(str(b.get("quantile"))) != Decimal("0.95")):
                raise ValueError("train_p95_strict_gt_required")
            if (type(b.get("minimum_n")) is not int or b["minimum_n"] < 20
                    or type(b.get("reference_valid_n")) is not int
                    or b["reference_valid_n"] < b["minimum_n"]):
                raise ValueError("insufficient_verified_reference")
            if f["unit"] in (None, "unknown") or b.get("unit") != f["unit"]:
                raise ValueError("baseline_unit_mismatch")
            if (b.get("semantic_definition_sha") != b.get("application_semantic_definition_sha")
                    or not SHA.fullmatch(b.get("semantic_definition_sha", ""))):
                raise ValueError("baseline_definition_mismatch")
            if b.get("reference_population") != f["population"] or b.get("application_population") != f["population"]:
                raise ValueError("baseline_population_mismatch")
            for field in ("id", "reference_feature_definition_sha", "reference_source_version",
                          "reference_period", "application_period", "method", "missing", "refresh", "expiry"):
                if field not in b or b[field] is None:
                    raise ValueError("missing_baseline_policy:" + field)
            if "range_proof" not in b or b["range_proof"] != inp["range_proof"]:
                raise ValueError("range_proof_binding_mismatch")
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        errors.append("input/" + str(exc))
    return errors


def output_schema():
    return json.loads((PACKAGE / "contracts/rule_v2.schema.json").read_text())


def validate_prediction(inp, prediction):
    errors = validate_input(inp)
    if errors:
        return errors
    try:
        canonical(prediction)
        jsonschema.Draft202012Validator(output_schema()).validate(prediction)
    except (jsonschema.ValidationError, TypeError, ValueError) as exc:
        return ["output/schema:" + str(getattr(exc, "message", exc))]
    check = rule_check({"input": inp}, prediction)
    errors.extend(check["errors"])
    # Python bool == 1 must never make a Boolean condition equal a numeric one.
    for d in prediction["decisions"]:
        if d["state"] not in ("proposed", "deferred"):
            continue
        expected = ({"op": "eq", "value": True} if d["request"] == "hr" else
                    inp["observed_diagnostic_contract"]["condition"] if d["request"] == "observed" else
                    {"op": "gt", "value": inp["baseline"].get("value")})
        if canonical(d["condition"]) != canonical(expected):
            # Numeric 418 and 418.0 represent the same boundary; bool never does.
            a, b = d["condition"], expected
            try:
                equal = (a["op"] == b["op"] and set(a) == set(b) == {"op", "value"}
                         and finite_number(a["value"]) == finite_number(b["value"]))
            except (KeyError, ValueError):
                equal = False
            if not equal:
                errors.append("typed_condition_mismatch/" + d["request"])
        required_basis = {"hr": "ddl_not_null", "observed": "observed_diagnostic", "baseline": "reference_p95"}
        if d["basis"] != required_basis[d["request"]]:
            errors.append("active_basis_mismatch/" + d["request"])
        if d["request"] == "observed" and inp["measurement_id"] not in d["evidence"]:
            errors.append("observed_measurement_reference_missing")
    return sorted(set(errors))


def build_messages(inp):
    errors = validate_input(inp)
    if errors:
        raise ValueError(";".join(errors))
    return [{"role": "system", "content": RULE_SYSTEM},
            {"role": "user", "content": canonical(inp).decode("utf-8")}]


def repair_messages(inp, raw, errors):
    # Public input and validator feedback only; never a teacher target.
    payload = {"original_input": copy.deepcopy(inp), "failed_response": raw,
               "feedback": {"contract": "bundle2", "errors": errors,
                            "instruction": "Correct the failed structure/refs using the supplied public input; preserve the requested purpose, filters and grain. Output only JSON."}}
    return [{"role": "system", "content": RULE_SYSTEM},
            {"role": "user", "content": canonical(payload).decode("utf-8")}]
