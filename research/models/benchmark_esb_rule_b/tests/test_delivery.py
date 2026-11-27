"""Delivery behavior checks; synthetic controls are not model generation scores."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.models.benchmark_esb_rule_b import (
    build_messages, build_service_request, convert_suggestion, evaluate_draft,
    validate_input, validate_prediction,
)
from research.models.benchmark_esb_rule_b.contract import PACKAGE, digest
from research.models.benchmark_esb_rule_b.inference import BRuleModel, verify_package


def actual():
    items = json.loads((PACKAGE / "examples/public_pfs_saved_responses.json").read_text())["items"]
    return copy.deepcopy(next(x for x in items if x["id"] == "pfs_runtime"))


def control_prediction(inp):
    # Test fixture only: never used by recommend(), conversion or detection.
    ds = [{"request": "hr", "role": "HR", "state": "no_applicable", "basis": "none",
           "condition": None, "evidence": [], "needed": [], "reason": "No confirmed source constraint supplied."}]
    valid = inp["measurement"]["valid_count"] > 0
    ds.append({"request": "observed", "role": "SR", "state": "proposed" if valid else "deferred",
               "basis": "observed_diagnostic", "condition": {"op": "is_missing"},
               "evidence": [inp["measurement_id"]], "needed": [] if valid else ["need_valid_observations"],
               "reason": "Calculated availability only; incident cause is unconfirmed."})
    b = inp["baseline"]
    if inp["feature"]["unit"] is None:
        state, need = "request_evidence", ["need_verified_unit"]
    elif b["state"] == "insufficient_sample":
        state, need = "request_evidence", ["need_reference_minimum_n"]
    elif b["state"] != "verified":
        state, need = "request_evidence", ["need_compatible_train_reference"]
    else:
        state, need = ("proposed", []) if valid else ("deferred", ["need_valid_observations"])
    active = state in {"proposed", "deferred"}
    ds.append({"request": "baseline", "role": "SR", "state": state,
               "basis": "reference_p95" if active else "insufficient",
               "condition": {"op": "gt", "value": b["value"]} if active else None,
               "evidence": [inp["measurement_id"], b["id"]] if active else [], "needed": need,
               "reason": "TRAIN-only supplied comparison; operational fault meaning is unconfirmed."})
    return {"v": "2", "feature": inp["feature"]["id"], "sha": inp["feature"]["sha"], "decisions": ds}


class DeliveryTests(unittest.TestCase):
    def test_package_hashes(self):
        self.assertEqual(verify_package()["model"]["weight_sha256"],
                         "36589b07d9fb8ee3b955598ea652a08de1f72243ceb066fd793c62e39692c11e")

    def test_eight_actual_saved_responses(self):
        items = json.loads((PACKAGE / "examples/public_pfs_saved_responses.json").read_text())["items"]
        self.assertEqual(len(items), 8)
        for row in items:
            with self.subTest(row=row["id"]):
                self.assertEqual(digest(row["input"]), row["input_sha"])
                self.assertEqual(validate_prediction(row["input"], row["prediction"]), [])
                draft = convert_suggestion(row["input"], row["prediction"])
                self.assertEqual(draft["outcome"]["decisionCode"], "SR_BASELINE")
                self.assertFalse(draft["evidenceSpec"]["automaticActivation"])

    def test_strict_gt_boundary_and_missing(self):
        row = actual(); inp = row["input"]
        draft = convert_suggestion(inp, row["prediction"])
        kwargs = {"definition_sha": inp["feature"]["sha"], "unit": "second"}
        for value, expected in [(417, "nonwarning"), (418, "nonwarning"), (419, "warning"),
                                (None, "unavailable"), (float("nan"), "unavailable"),
                                (float("inf"), "unavailable"), (True, "unavailable"),
                                ("not-a-number", "unavailable")]:
            with self.subTest(value=value):
                self.assertEqual(evaluate_draft(draft, value, **kwargs)["state"], expected)

    def test_definition_and_unit_mismatch(self):
        row = actual(); draft = convert_suggestion(row["input"], row["prediction"])
        for sha, unit in [("f" * 64, "second"), (row["input"]["feature"]["sha"], "ms")]:
            self.assertEqual(evaluate_draft(draft, 419, definition_sha=sha, unit=unit)["state"], "unavailable")

    def test_edited_threshold_rejected(self):
        row = actual(); row["prediction"]["decisions"][2]["condition"]["value"] = 9999
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))
        with self.assertRaises(ValueError):
            convert_suggestion(row["input"], row["prediction"])

    def test_draft_tampering_unavailable(self):
        row = actual(); draft = convert_suggestion(row["input"], row["prediction"])
        draft["condition"]["value"] = "0"
        self.assertEqual(evaluate_draft(draft, 419, definition_sha=row["input"]["feature"]["sha"], unit="second")["state"], "unavailable")

    def test_v3_is_not_v2(self):
        row = actual(); row["prediction"]["v"] = "3"
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_duplicate_role_rejected(self):
        row = actual(); row["prediction"]["decisions"][1] = copy.deepcopy(row["prediction"]["decisions"][2])
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_boolean_not_numeric_one(self):
        row = actual(); row["prediction"]["decisions"][2]["condition"]["value"] = True
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_observed_condition_not_rewritten(self):
        row = actual(); row["prediction"]["decisions"][1]["condition"] = {"op": "gt", "value": 0}
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_unprovided_evidence_rejected(self):
        row = actual(); row["prediction"]["decisions"][2]["evidence"].append("invented")
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_missing_role_rejected(self):
        row = actual(); row["prediction"]["decisions"].pop()
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_unknown_unit_requests_evidence(self):
        row = actual(); inp = row["input"]; inp["feature"]["unit"] = None
        inp["baseline"]["state"] = "unverified_unit"; inp["baseline"]["unit"] = None
        pred = control_prediction(inp)
        self.assertEqual(validate_prediction(inp, pred), [])
        self.assertIsNone(convert_suggestion(inp, pred))

    def test_insufficient_reference(self):
        row = actual(); inp = row["input"]; inp["baseline"]["state"] = "insufficient_sample"
        inp["baseline"]["reference_valid_n"] = 19
        pred = control_prediction(inp)
        self.assertEqual(validate_prediction(inp, pred), [])
        self.assertIsNone(convert_suggestion(inp, pred))

    def test_zero_observations_deferred(self):
        row = actual(); inp = row["input"]; inp["measurement"]["valid_count"] = 0
        inp["measurement"]["missing_count"] = inp["measurement"]["row_count"]
        pred = control_prediction(inp)
        self.assertEqual(validate_prediction(inp, pred), [])
        self.assertIsNone(convert_suggestion(inp, pred))
        self.assertEqual(pred["decisions"][2]["condition"]["value"], 418)

    def test_baseline_exact_encoding_is_bound_without_replacing_json_boundary(self):
        row = actual(); row["input"]["baseline"]["value_exact"] = "9999"
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))
        row = actual(); row["input"]["baseline"]["value_exact"] = "418.000000000000001"
        self.assertEqual(validate_prediction(row["input"], row["prediction"]), [])
        draft = convert_suggestion(row["input"], row["prediction"])
        self.assertEqual(draft["condition"]["value"], "418.0")

    def test_measurement_counts_and_declared_summary_hash(self):
        row = actual(); row["input"]["measurement"]["valid_count"] += 1
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))
        row = actual(); row["input"]["measurement_sha_scheme"] = "canonical_summary_v1"
        row["input"]["measurement_sha"] = "f" * 64
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_unit_population_and_reference_split(self):
        for key, value in [("unit", "ms"), ("reference_population", {"table": "OTHER", "filter": None}),
                           ("reference_split", "test"), ("minimum_n", 1),
                           ("application_semantic_definition_sha", "f" * 64)]:
            with self.subTest(key=key):
                row = actual(); row["input"]["baseline"][key] = value
                self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_model_cannot_make_new_hr_contract(self):
        row = actual(); d = row["prediction"]["decisions"][0]
        d.update(state="proposed", basis="ddl_not_null", condition={"op": "eq", "value": True})
        self.assertTrue(validate_prediction(row["input"], row["prediction"]))

    def test_body_and_eval_fields_blocked(self):
        for field in ("label", "injection_applied", "response_body", "password"):
            row = actual(); row["input"][field] = "x"
            self.assertTrue(validate_input(row["input"]))
        row = actual(); row["input"]["feature"]["expression"] = {"op": "column", "table": "M", "column": "BODY"}
        self.assertTrue(validate_input(row["input"]))
        row = actual(); row["input"]["sources"]["calculation_and_relationship_key_columns"].append(["M", "BODY"])
        self.assertTrue(validate_input(row["input"]))

    def test_explicit_service_field_binding(self):
        row = actual(); f = row["input"]["feature"]
        binding = {"feature_id": f["id"], "definition_sha": f["sha"], "unit": "second", "field_name": "runtime"}
        draft = convert_suggestion(row["input"], row["prediction"], binding)
        self.assertEqual(draft["condition"]["field"], "F.runtime")
        binding["definition_sha"] = "f" * 64
        with self.assertRaises(ValueError):
            convert_suggestion(row["input"], row["prediction"], binding)

    def test_service_stats_builder_and_native_validation(self):
        records = []
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(100):
            records.append({"observation_id": "synthetic_" + str(i), "transaction_key": "tx_" + str(i),
                            "event_time": (t0 + timedelta(seconds=i)).isoformat(),
                            "features": {"total_count": i}, "missing": [], "label": None})
        definition = {"name": "total_count", "definition_sha": "a" * 64, "dtype": "integer", "unit": "count",
                      "meaning": "Synthetic test fixture count", "expression": {"op": "column", "table": "P", "column": "TOTAL_COUNT"},
                      "grain": {"table": "P", "keys": ["observation_id"]},
                      "population": {"table": "P", "filter": None}, "group_by": [],
                      "missing_policy": {"input": "propagate"}, "source_columns": [["P", "TOTAL_COUNT"]]}
        context = {"source_id": "synthetic_fixture", "snapshot_id": "fixture1", "feature_set_id": "fixture1", "time_basis": "persisted_at"}
        request = build_service_request(records, definition, context)
        inp = request["input"]
        self.assertEqual(inp["feature"]["dtype"], "integer")
        self.assertEqual(inp["baseline"]["reference_valid_n"], 80)
        self.assertEqual(inp["measurement"]["valid_count"], 20)
        self.assertAlmostEqual(inp["baseline"]["value"], 75.05)
        self.assertEqual(validate_prediction(inp, control_prediction(inp)), [])
        self.assertNotIn("tx_99", request["messages"][1]["content"])
        definition.pop("grain")
        with self.assertRaisesRegex(ValueError, "grain"):
            build_service_request(records, definition, context)

    def test_messages_contain_no_teacher_answer(self):
        row = actual(); messages = build_messages(row["input"])
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(json.loads(messages[1]["content"]), row["input"])

    def test_one_repair_and_original_failure_preserved(self):
        row = actual(); model = BRuleModel.__new__(BRuleModel)
        model.manifest = {"model": {"weight_sha256": "test-fixture"}, "generation": {"maximum_repairs": 1}}
        bad = {"prediction": None, "raw_output": "not-json", "length_capped": False}
        good = {"prediction": row["prediction"], "raw_output": json.dumps(row["prediction"]), "length_capped": False}
        with tempfile.TemporaryDirectory() as parent:
            out = Path(parent) / "new"
            with patch.object(model, "_load"), patch.object(model, "_generate", side_effect=[bad, good]) as generate:
                result = model.recommend(row["input"], out)
            self.assertEqual(result["status"], "validated")
            self.assertEqual(generate.call_count, 2)
            self.assertEqual(json.loads((out / "attempt_0.json").read_text())["raw_output"], "not-json")
            self.assertNotIn("target", generate.call_args.args[0][1]["content"])

    def test_no_generated_answer_fallback(self):
        row = actual(); model = BRuleModel.__new__(BRuleModel)
        model.manifest = {"model": {"weight_sha256": "test-fixture"}, "generation": {"maximum_repairs": 1}}
        bad = {"prediction": None, "raw_output": "not-json", "length_capped": False}
        with tempfile.TemporaryDirectory() as parent:
            with patch.object(model, "_load"), patch.object(model, "_generate", return_value=bad) as generate:
                result = model.recommend(row["input"], Path(parent) / "new")
            self.assertEqual(result["status"], "invalid")
            self.assertIsNone(result["prediction"])
            self.assertEqual(generate.call_count, 2)

    def test_truncated_json_not_accepted(self):
        row = actual(); model = BRuleModel.__new__(BRuleModel)
        model.manifest = {"model": {"weight_sha256": "test-fixture"}, "generation": {}}
        capped = {"prediction": row["prediction"], "raw_output": json.dumps(row["prediction"]), "length_capped": True}
        with tempfile.TemporaryDirectory() as parent:
            with patch.object(model, "_load"), patch.object(model, "_generate", return_value=capped):
                result = model.recommend(row["input"], Path(parent) / "new")
            self.assertEqual(result["status"], "invalid")
            self.assertEqual(result["errors"], ["output_truncated"])


if __name__ == "__main__":
    unittest.main()
