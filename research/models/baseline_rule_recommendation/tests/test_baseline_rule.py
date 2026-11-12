import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(PACKAGE_PARENT))

from baseline_rule_recommendation import (  # noqa: E402
    build_request,
    convert_suggestion,
    split_train,
    validate_prediction,
)


def rows(n=30):
    return [
        {
            "observation_id": f"obs-{i:03d}",
            "transaction_key": f"txn-{i:03d}",
            "event_time": (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)).isoformat(),
            "features": {"amount": i},
            "label": None,
        }
        for i in range(n)
    ]


def definition():
    return {
        "name": "amount",
        "definition_sha": "a" * 64,
        "dtype": "numeric",
        "unit": "USD",
        "meaning": "transaction amount",
        "expression": {"op": "source"},
        "population_ref": "population-v1",
    }


class BaselineRuleTests(unittest.TestCase):
    def prediction(self, request, state="proposed"):
        source = request["record"]
        baseline = source["baseline"]
        plan = source["analysis_plan"]
        active = state in {"proposed", "deferred"}
        required = [baseline["id"], baseline["measurement_id"]] if active else []
        basis = "reference_p95" if active else "none" if state == "no_applicable" else "insufficient"
        return {
            "v": "3", "feature": source["feature"]["name"], "sha": source["feature"]["definition_sha"],
            "decisions": [{
                "request": "baseline", "role": "SR", "state": state, "basis": basis,
                "condition": {"op": "gt", "value": baseline["value"]} if active else None,
                "evidence": required[:], "needed": [], "reason": "Reference p95 threshold proposal.",
                "purpose": plan["purpose"], "warning_meaning": "Value exceeds the reference threshold.",
                "population_ref": plan["population_ref"], "precondition_refs": plan["precondition_refs"][:],
                "required_evaluation_basis": plan["required_evaluation_basis"][:],
                "unavailable_action": "preserve_unassessed" if active else "no_rule",
                "unavailable_reason": "If evidence becomes unavailable, preserve an unassessed result.",
                "business_scope": plan["business_scope"],
                "baseline_use_ref": baseline["id"] if active else None,
            }],
        }

    def test_chronological_split_keeps_equal_timestamp_and_transaction_groups(self):
        data = rows(30)
        data[25]["transaction_key"] = data[3]["transaction_key"]
        data[28]["event_time"] = data[27]["event_time"]
        result = split_train(list(reversed(data)))
        self.assertFalse(result["issues"])
        ref, app = result["reference"], result["application"]
        self.assertTrue(max(r["event_time"] for r in ref) < min(r["event_time"] for r in app))
        self.assertFalse({r["transaction_key"] for r in ref} & {r["transaction_key"] for r in app})
        self.assertEqual(len(ref) + len(app), len(data))
        self.assertEqual(result["policy"]["name"], "train-internal-80-20-chronological-v1")

    def test_build_request_excludes_only_anomaly_reference_and_keeps_application_labels(self):
        data = rows(30)
        data[0]["label"] = "anomaly"
        data[24]["label"] = "anomaly"
        partition = split_train(data)
        result = build_request(
            data,
            definition(),
            {"partition": partition, "split_policy": "train-internal-80-20-chronological-v1"},
        )
        self.assertFalse(result["blocked"])
        self.assertIsInstance(result["input"], dict)
        self.assertEqual(result["input"]["requests"], ["baseline"])
        baseline = result["record"]["baseline"]
        self.assertEqual(baseline["valid_reference_n"], 23)
        self.assertTrue(baseline["unlabeled_reference_included_without_normality_claim"])
        self.assertEqual(baseline["current_finite_numeric_n"], 6)
        self.assertLess(len(str(result["input"])), 12000)
        self.assertNotIn("obs-000", str(result["input"]))
        self.assertNotIn("txn-000", str(result["input"]))

    def test_validator_rejects_threshold_and_boolean_coercion(self):
        data = rows(30)
        partition = split_train(data)
        request = build_request(data, definition(), {"partition": partition})
        source = request["record"]
        d_prediction = self.prediction(request)
        d = d_prediction["decisions"][0]
        prediction = d_prediction
        self.assertEqual(validate_prediction(request, prediction), [])
        suggestion = convert_suggestion(request, prediction)
        self.assertEqual(suggestion["condition"]["field"], "F.amount")
        self.assertEqual(suggestion["condition"]["value"],
                         format(Decimal(str(request["input"]["baseline"]["value"])), "f"))
        d["condition"]["value"] = True
        self.assertTrue(validate_prediction(request, prediction))

    def test_present_current_observations_cannot_be_described_as_absent(self):
        request = build_request(rows(), definition())
        prediction = self.prediction(request)
        prediction["decisions"][0]["unavailable_reason"] = "no usable current observations"
        self.assertIn("unavailable_reason_requires_future_conditional_for_available_observations",
                      validate_prediction(request, prediction))

    def test_needed_states_and_actions_for_unknown_unit_and_small_reference(self):
        unknown = definition()
        unknown["unit"] = "unknown"
        request = build_request(rows(), unknown, {"partition": split_train(rows())})
        prediction = self.prediction(request, "request_evidence")
        decision = prediction["decisions"][0]
        decision["needed"] = ["need_verified_unit"]
        self.assertEqual(validate_prediction(request, prediction), [])
        decision["unavailable_action"] = "preserve_unassessed"
        self.assertTrue(validate_prediction(request, prediction))

        small = rows(20)
        request = build_request(small, definition(), {"partition": split_train(small)})
        prediction = self.prediction(request, "request_evidence")
        prediction["decisions"][0]["needed"] = ["need_reference_minimum_n"]
        self.assertEqual(validate_prediction(request, prediction), [])

    def test_deferred_and_no_applicable_decisions_are_non_rules(self):
        data = rows()
        # A zero-current-evidence state must be justified by its input, not invented by the response.
        empty_current = rows()
        for row in empty_current[24:]:
            row["features"]["amount"] = None
        deferred_request = build_request(empty_current, definition(), {"partition": split_train(empty_current)})
        prediction = self.prediction(deferred_request, "deferred")
        prediction["decisions"][0]["needed"] = ["need_valid_observations"]
        self.assertEqual(validate_prediction(deferred_request, prediction), [])
        prediction["decisions"][0]["unavailable_action"] = "no_rule"
        self.assertTrue(validate_prediction(deferred_request, prediction))

        boolean_definition = definition()
        boolean_definition["dtype"] = "boolean"
        boolean_request = build_request(data, boolean_definition, {"partition": split_train(data)})
        no_rule = self.prediction(boolean_request, "no_applicable")
        self.assertEqual(validate_prediction(boolean_request, no_rule), [])
        self.assertIsNone(convert_suggestion(boolean_request, no_rule))

    def test_fabricated_refs_and_mutated_threshold_are_rejected(self):
        data = rows()
        request = build_request(data, definition(), {"partition": split_train(data)})
        prediction = self.prediction(request)
        prediction["decisions"][0]["precondition_refs"].append("fabricated")
        self.assertTrue(validate_prediction(request, prediction))
        prediction = self.prediction(request)
        prediction["decisions"][0]["required_evaluation_basis"].pop()
        self.assertTrue(validate_prediction(request, prediction))
        prediction = self.prediction(request)
        prediction["decisions"][0]["condition"]["value"] += 1
        self.assertTrue(validate_prediction(request, prediction))

    def test_input_is_bounded_and_lineage_changes_with_source_ids(self):
        data = rows(400)
        first = build_request(data, definition(), {"partition": split_train(data)})
        changed = rows(400)
        for row in changed:
            row["observation_id"] = "changed-" + row["observation_id"]
            row["transaction_key"] = "changed-" + row["transaction_key"]
        second = build_request(changed, definition(), {"partition": split_train(changed)})
        self.assertLess(len(str(first["input"])), 12000)
        self.assertLess(len(str(second["input"])), 12000)
        self.assertNotEqual(first["record"]["application_context"]["source_sha"],
                            second["record"]["application_context"]["source_sha"])
        self.assertNotIn("obs-000", str(first["input"]))

    def test_service_uses_json_threshold_not_exact_decimal_substitute(self):
        data = rows()
        for i, row in enumerate(data):
            row["features"]["amount"] = Decimal("1.00000000000000001") + Decimal(i) * Decimal("0.00000000000000001")
        request = build_request(data, definition(), {"partition": split_train(data)})
        prediction = self.prediction(request)
        suggestion = convert_suggestion(request, prediction)
        json_boundary = format(Decimal(str(request["input"]["baseline"]["value"])), "f")
        self.assertEqual(suggestion["condition"]["value"], json_boundary)
        self.assertNotEqual(json_boundary, request["input"]["baseline"]["value_exact"])

        tiny = rows()
        for row in tiny:
            row["features"]["amount"] = Decimal("1e-50")
        tiny_request = build_request(tiny, definition(), {"partition": split_train(tiny)})
        self.assertEqual(tiny_request["record"]["baseline"]["value"], None)
        self.assertTrue(tiny_request["blocked"])
        ask = self.prediction(tiny_request, "request_evidence")
        ask["decisions"][0]["needed"] = ["need_compatible_reference"]
        self.assertEqual(validate_prediction(tiny_request, ask), [])
        self.assertIsNone(convert_suggestion(tiny_request, ask))

    def test_no_safe_time_or_transaction_boundary_blocks_proposal(self):
        for relation in ("timestamp", "transaction"):
            data = rows()
            if relation == "timestamp":
                for row in data:
                    row["event_time"] = data[0]["event_time"]
            else:
                for row in data:
                    row["transaction_key"] = "one-transaction"
            partition = split_train(data)
            self.assertTrue(partition["issues"])
            request = build_request(data, definition(), {"partition": partition})
            self.assertTrue(request["blocked"])
            proposal = self.prediction(request, "proposed")
            self.assertTrue(validate_prediction(request, proposal))
            ask = self.prediction(request, "request_evidence")
            ask["decisions"][0]["needed"] = ["need_compatible_reference", "need_reference"]
            self.assertEqual(set(validate_prediction(request, ask)), set())

    def test_source_reference_and_measurement_changes_invalidate_old_prediction(self):
        data = rows()
        original = build_request(data, definition(), {"partition": split_train(data)})
        old_prediction = self.prediction(original)
        changed = rows()
        changed[0]["features"]["amount"] += 1000
        changed[-1]["features"]["amount"] += 100
        updated = build_request(changed, definition(), {"partition": split_train(changed)})
        for key in ("source_sha",):
            self.assertNotEqual(original["record"]["application_context"][key],
                                updated["record"]["application_context"][key])
        self.assertNotEqual(original["record"]["baseline"]["id"], updated["record"]["baseline"]["id"])
        self.assertNotEqual(original["record"]["measurement_id"], updated["record"]["measurement_id"])
        self.assertTrue(validate_prediction(updated, old_prediction))


if __name__ == "__main__":
    unittest.main()
