import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from evaluation_benchmark.generate import Catalog, generate_case, make_history
from benchmark_difficulty.adjust import (PROFILE, semantic_bag, normal_records,
                                         scaled_duration, strengthen, original_fingerprint)


class AdjustmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle = json.loads((ROOT / "reports/evaluation_v2_work/inputs.json").read_text(encoding="utf-8"))
        cls.catalog = Catalog(bundle)
        _, cls.history = make_history(20261010)
        cls.profile = json.loads(PROFILE.read_text(encoding="utf-8"))

    def case(self, kind, index=0):
        return generate_case(self.catalog, kind, index, 20261010, self.history)

    def test_scaled_deviation_not_absolute_duration(self):
        self.assertEqual(scaled_duration(100, 200, 1.2, 1000), 220)
        self.assertEqual(scaled_duration(200, 100, 1.2, 1000), 80)

    def test_cap_and_nonnegative(self):
        self.assertEqual(scaled_duration(100, 200, 1.2, 210), 209)
        self.assertEqual(scaled_duration(100, 0, 1.2, 210), 1)

    def test_normal_and_ids_preserved_all_six(self):
        for kind in ("individual_delay", "group_delay", "column_relation", "time_burst", "input_stop", "gradual_degradation"):
            with self.subTest(kind=kind):
                case = self.case(kind)
                before, cid = semantic_bag(normal_records(case)), case.case_id
                case, audits = strengthen(case, self.profile)
                self.assertEqual(before, semantic_bag(normal_records(case)))
                self.assertEqual(cid, case.case_id)
                self.assertEqual(len(audits), 1)

    def test_hard_unchanged(self):
        case = self.case("mixed_hard")
        before = json.dumps(case.export(), sort_keys=True)
        case, audits = strengthen(case, self.profile)
        self.assertEqual(before, json.dumps(case.export(), sort_keys=True))
        self.assertEqual(audits, [])

    def test_burst_extra_only_observed(self):
        case = self.case("time_burst")
        old = len(case.observed); extra = case.events[0]["added_executions"]
        strengthen(case, self.profile)
        self.assertGreater(len(case.observed), old)
        self.assertGreater(case.events[0]["added_executions"], extra)

    def test_stop_duration_and_missing_ids(self):
        case = self.case("input_stop")
        strengthen(case, self.profile)
        event = case.events[0]; evidence = json.loads(event["evidence_json"])
        self.assertEqual(evidence["expected_executions"], event["removed_executions"])
        self.assertTrue(set(evidence["missing_transaction_ids"]).isdisjoint(case.observed))

    def test_seed_reproducibility(self):
        a, b = self.case("time_burst", 3), self.case("time_burst", 3)
        strengthen(a, self.profile); strengthen(b, self.profile)
        self.assertEqual(original_fingerprint(a), original_fingerprint(b))

    def test_matched_feature_precision(self):
        import numpy as np
        import pandas as pd
        from benchmark_difficulty.evaluate import match_saved_precision, FEATURES, OLD_FEATURES
        columns = list(dict.fromkeys((*OLD_FEATURES, *FEATURES)))
        original = pd.DataFrame({c: [1.123456789] for c in columns})
        original["case_id"] = ["not-a-feature"]
        result = match_saved_precision(original)
        np.testing.assert_array_equal(result[columns], np.float64(np.float32(1.123456789)))
        self.assertEqual(result.case_id.iloc[0], "not-a-feature")
        self.assertEqual(original[columns[0]].iloc[0], 1.123456789)


if __name__ == "__main__":
    unittest.main()
