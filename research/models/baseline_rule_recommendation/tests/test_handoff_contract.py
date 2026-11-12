"""Research-only consumer contract: no worker/server import or model call."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from baseline_rule_recommendation import output_schema, validate_prediction, convert_suggestion

PACKAGE = Path(__file__).resolve().parents[1]


class HandoffContractTests(unittest.TestCase):
    def test_published_schema_is_the_effective_baseline_contract(self):
        schema = json.loads((PACKAGE / "contracts/baseline.schema.json").read_text())
        self.assertEqual(schema, output_schema())
        decisions = schema["properties"]["decisions"]
        self.assertEqual(decisions["minItems"], 1)
        self.assertEqual(decisions["maxItems"], 1)
        self.assertEqual(decisions["items"]["properties"]["request"]["enum"], ["baseline"])

    def test_saved_actual_outputs_validate_without_server_code(self):
        cases = json.loads((PACKAGE / "examples/actual_service_generation.json").read_text())["cases"]
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case["case"]):
                self.assertEqual(validate_prediction(case["input"], case["prediction"]), [])
                draft = convert_suggestion(case["input"], case["prediction"])
                if case["case"] == "proposed":
                    self.assertEqual(draft["condition"], case["suggestions"][0]["condition"])
                    self.assertEqual(draft["outcome"], case["suggestions"][0]["outcome"])
                else:
                    self.assertIsNone(draft)

    def test_original_identity_errors_remain_rejected(self):
        cases = json.loads((PACKAGE / "examples/actual_service_generation.json").read_text())["cases"]
        rejected = 0
        for case in cases:
            first = case["attempts"][0]
            if not first["validation"]["passed"]:
                errors = validate_prediction(case["input"], first["receipt"]["output"])
                self.assertIn("feature_definition_mismatch", errors)
                rejected += 1
        self.assertEqual(rejected, 2)


if __name__ == "__main__":
    unittest.main()
