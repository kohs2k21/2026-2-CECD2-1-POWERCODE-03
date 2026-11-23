"""CPU contract tests for deterministic XAI rendering and severity boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest
from jsonschema import ValidationError


XAI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(XAI_ROOT))

from xai_runtime.artifacts import InputError  # noqa: E402
from xai_runtime.engine import rejection, render, severity, validate_packet  # noqa: E402


EXAMPLES = json.loads((XAI_ROOT / "design/1.1.0/examples.json").read_text())


def example(number):
    return copy.deepcopy(EXAMPLES[number - 1]["input"])


def plan(packet, items=None):
    return {
        "schema_version": "xai-plan/1.0.0",
        "packet_id": packet["packet_id"],
        "items": items if items is not None else [
            {"template_id": f["kind"], "evidence_id": f["id"]}
            for f in packet["facts"] if f["required"]
        ],
        "next_check_codes": [],
    }


class RuntimeTests(unittest.TestCase):
    def test_input_rejection_has_typed_reason_and_keeps_detector_decision(self):
        for reason, expected in (
            ('source_binding_error', 'source_binding_error'),
            ('severity_configuration_error:missing_rule:example', 'severity_configuration_error'),
            ('saved_component_witness_mismatch', 'decision_mismatch'),
            ('changed_artifact:example', 'source_integrity_error'),
        ):
            with self.subTest(reason=reason):
                result = rejection(None, reason)
                self.assertEqual(result['error_code'], expected)
                self.assertEqual(result['status'], 'input_rejected')
                self.assertTrue(result['detector_decision_unchanged'])

    def test_invalid_plans_fall_back_to_fixed_packet_output(self):
        packet = example(1)
        expected = render(packet)
        required = [f for f in packet["facts"] if f["required"]]
        invalid_plans = [
            plan(packet, [{"template_id": required[0]["kind"], "evidence_id": required[0]["id"]}]),
            plan(packet, [
                {"template_id": f["kind"], "evidence_id": f["id"]} for f in required
            ] + [{"template_id": "observation", "evidence_id": "UNKNOWN"}]),
            plan(packet, [
                {"template_id": "logical_rule", "evidence_id": f["id"]} if f["id"] == "E1"
                else {"template_id": f["kind"], "evidence_id": f["id"]}
                for f in required
            ]),
        ]
        cross_packet = plan(packet)
        cross_packet["packet_id"] = "ANOTHER-PACKET"
        invalid_plans.append(cross_packet)

        injected = plan(packet)
        injected["severity"] = "critical"
        injected["cause"] = "confirmed failure"
        injected["number"] = 999999
        injected["items"][0].update(
            text="Invented numeric evidence", observed="999999", cause="confirmed failure",
            severity="critical",
        )
        invalid_plans.append(injected)

        for supplied in invalid_plans:
            with self.subTest(plan=supplied):
                actual = render(packet, supplied_plan=supplied)
                self.assertEqual(actual["status"], "template_fallback")
                self.assertEqual(actual["sentences"], expected["sentences"])
                self.assertEqual(actual["decision"], packet["decision"])
                self.assertEqual(actual["severity"], packet["severity"])
                self.assertEqual(actual["cause_status"], expected["cause_status"])
                self.assertNotIn("Invented numeric evidence", json.dumps(actual, ensure_ascii=False))
                self.assertNotIn("confirmed failure", json.dumps(actual, ensure_ascii=False))

    def test_corrupt_source_and_numeric_evidence_are_rejected(self):
        packet = example(1)
        packet["source_refs"][0]["transaction_ref"] = "OTHER-TRANSACTION"
        with self.assertRaisesRegex(InputError, "cross_transaction_source"):
            validate_packet(packet)

        packet = example(1)
        packet["facts"][0]["payload"]["observed"] = "1"
        with self.assertRaisesRegex(InputError, "numeric_decision_mismatch"):
            validate_packet(packet)

        packet = example(1)
        packet["facts"][1]["payload"]["score"] = "NaN"
        with self.assertRaises(ValidationError):
            validate_packet(packet)

    def test_unavailable_state_is_preserved(self):
        packet = example(3)
        result = render(packet)
        self.assertEqual(result["decision"], packet["decision"])
        self.assertEqual(result["decision"]["state"], "unavailable")
        self.assertEqual(result["severity"]["status"], "not_applicable")
        self.assertIn("평가 불가", result["sentences"][0]["text"])

    def test_physical_row_identity_is_enough_without_business_process_or_message_ids(self):
        packet = example(1)
        source = packet["source_refs"][0]
        source["process_ref"] = None
        source["message_ref"] = None
        result = render(packet)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sentences"][0]["source_refs"], [source["source_id"]])

    def test_explicit_rule_policy_supports_three_levels_without_operational_approval(self):
        packet = example(1)
        facts = [f for f in packet["facts"] if f["state"] == "warning"]
        rule_id = packet["facts"][0]["payload"]["rule_id"]
        for level, label in (("info", "참고"), ("warning", "주의"), ("critical", "위험")):
            with self.subTest(level=level):
                policy = {
                    "contract": "xai-rule-severity/1.0.0",
                    "version": "TEST-1",
                    "approved_for_operations": False,
                    "rules": {rule_id: {"level": level}},
                }
                result, diagnostics = severity(facts, "warning", policy)
                self.assertEqual(result["status"], "rated")
                self.assertEqual(result["level"], level)
                self.assertEqual(result["label"], label)
                self.assertFalse(diagnostics["approved_for_operations"])
                self.assertEqual(diagnostics["policy_status"], "draft")

    def test_missing_or_incomplete_severity_policy_does_not_invent_risk(self):
        packet = example(1)
        facts = [f for f in packet["facts"] if f["state"] == "warning"]
        result, diagnostics = severity(facts, "warning")
        self.assertEqual(result["status"], "unrated")
        self.assertIsNone(result["level"])
        self.assertEqual(diagnostics["policy_status"], "not_configured")
        self.assertEqual(diagnostics["reason"], "rule_policy_not_configured")
        ml_only = [f for f in facts if f['kind'] == 'ml_score']
        _, ml_diagnostics = severity(ml_only, 'warning')
        self.assertEqual(ml_diagnostics['reason'], 'only_unrated_methods')
        self.assertEqual(set(ml_diagnostics['unrated_method_reasons'].values()), {'ml_severity_undefined'})

        incomplete = {
            "contract": "xai-rule-severity/1.0.0",
            "version": "TEST-INCOMPLETE",
            "approved_for_operations": False,
            "rules": {},
        }
        with self.assertRaisesRegex(InputError, "missing_rule"):
            severity(facts, "warning", incomplete)

    def test_high_ml_score_and_duplicate_warnings_do_not_raise_rule_severity(self):
        packet = example(1)
        rule_fact = copy.deepcopy(packet["facts"][0])
        ml_fact = copy.deepcopy(packet["facts"][1])
        ml_fact["payload"]["score"] = "999999999"
        policy = {
            "contract": "xai-rule-severity/1.0.0",
            "version": "TEST-LOW",
            "approved_for_operations": False,
            "rules": {rule_fact["payload"]["rule_id"]: {"level": "warning"}},
        }
        result, _ = severity([rule_fact, copy.deepcopy(rule_fact), ml_fact], "warning", policy)
        self.assertEqual(result["level"], "warning")
        self.assertEqual(result["determined_by"], [rule_fact["payload"]["method_id"]])
        self.assertEqual(result["unrated_methods"], [ml_fact["payload"]["method_id"]])
        self.assertTrue(result["known_level_is_lower_bound"])


if __name__ == "__main__":
    unittest.main()
