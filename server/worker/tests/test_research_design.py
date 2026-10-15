from __future__ import annotations
import copy
import unittest
from derived_expression import evaluate, validate
from research_design import compile_features, feature_request, rule_request
from feature_runtime import calculate_features


def column(name):
    return {"op": "column", "table": "EC_PROCESS_MONITORING", "column": name}


class DerivedTests(unittest.TestCase):
    def test_candidate_unit_version_and_source_binding(self):
        request = feature_request([{"error_count": "2", "total_count": "10"}] * 30)
        ast = {"op": "divide", "args": [column("ERROR_COUNT"), column("TOTAL_COUNT")]}
        candidate = {"id": "ratio", "purpose": "Count ratio", "meaning": "Count ratio",
                     "sources": {"columns": [["EC_PROCESS_MONITORING", "ERROR_COUNT"],
                                             ["EC_PROCESS_MONITORING", "TOTAL_COUNT"]], "relationships": []},
                     "expected_type": "number", "expected_unit": "ratio", "availability": "offline_captured_scope",
                     "required_evidence": [], "plan": {"expression": ast,
                     "grain": {"table": "EC_PROCESS_MONITORING", "keys": ["OBSERVATION_ID"]},
                     "population": {"table": "EC_PROCESS_MONITORING", "filter": None}, "group_by": [],
                     "schema_version": request["schema_version"], "source_version": request["source_version"],
                     "missing_policy": {"input": "propagate", "empty_group": None, "denominator_zero": "missing",
                                        "child_missing": "propagate", "unknown_predicate": "defer"}}}
        receipt = {"output": {"candidates": [candidate], "stop_reason": "test"}, "adapterSha": "test"}
        result = compile_features(request, receipt)
        self.assertEqual(len(result["definitions"]), 1)
        changed = copy.deepcopy(receipt)
        changed["output"]["candidates"][0]["expected_unit"] = "ms"
        result = compile_features(request, changed)
        self.assertEqual(result["definitions"], [])
        self.assertEqual(result["reviews"][0]["reason"], "feature_output_unit_mismatch")
        changed = copy.deepcopy(receipt)
        changed["output"]["candidates"][0]["plan"]["source_version"] = "old"
        self.assertEqual(compile_features(request, changed)["reviews"][0]["reason"], "feature_source_version_mismatch")

    def test_signed_time_and_zero_denominator(self):
        expression = {"op": "datetime_diff", "args": [column("END_TIME"), column("START_TIME")], "unit": "ms"}
        self.assertEqual(validate(expression), "number")
        self.assertEqual(evaluate(expression, {"start_time_raw": "2026-01-01T00:00:01",
                                              "end_time_raw": "2026-01-01T00:00:00"}), -1000)
        self.assertIsNone(evaluate({"op": "divide", "args": [column("ERROR_COUNT"), column("TOTAL_COUNT")]},
                                  {"error_count": "1", "total_count": "0"}))
        with self.assertRaisesRegex(ValueError, "unsupported_derived_operation"):
            validate({"op": "child_sum", "relationship_id": "unknown"})

    def test_grounded_reference_and_unknown_unit(self):
        rows = [{"features": {"error_count": str(i)}} for i in range(30)]
        request = rule_request(rows, {"name": "error_count", "expression": {"op": "field", "field": "P.error_count"}}, "set")
        self.assertEqual(request["baseline"]["state"], "verified")
        self.assertEqual(request["baseline"]["n"], 30)
        definition = {"name": "ratio", "expression": {"op": "ratio",
                      "left": {"op": "field", "field": "P.error_count"},
                      "right": {"op": "field", "field": "P.total_count"}}}
        request = rule_request([{"features": {"ratio": "0.1"}}] * 30, definition, "set")
        self.assertEqual(request["baseline"]["state"], "unavailable")

    def test_boolean_missingness_preserves_numeric_output(self):
        definition = {"name": "both_missing", "expression": {"op": "derived", "ast": {"op": "and", "args": [
            {"op": "is_missing", "args": [column("START_TIME")]},
            {"op": "is_missing", "args": [column("END_TIME")]}]}, "outputType": "boolean",
            "unit": "boolean", "provenance": {}}}
        values, missing = calculate_features([definition], {})
        self.assertEqual(values, {"both_missing": "1"})
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
