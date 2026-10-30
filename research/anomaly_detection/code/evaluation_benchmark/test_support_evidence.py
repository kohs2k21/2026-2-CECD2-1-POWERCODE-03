"""Math, independence-unit and reproducibility checks; no files are written."""
import unittest
import numpy as np
from support_evidence import (tail_seen_probability, minimum_tail_sample,
                              quantile_interval_ranks, source_groups, sensitivity)


class SupportEvidenceTests(unittest.TestCase):
    def test_thirty_not_95pct_tail_coverage(self):
        self.assertAlmostEqual(tail_seen_probability(30), .7853612360570628)

    def test_59_is_exact_minimum_for_stated_objective(self):
        self.assertEqual(minimum_tail_sample(), 59)
        self.assertLess(tail_seen_probability(58), .95)
        self.assertGreaterEqual(tail_seen_probability(59), .95)

    def test_one_sided_result_is_not_two_sided_CI(self):
        self.assertIsNone(quantile_interval_ranks(30)["upper_rank"])
        self.assertIsNone(quantile_interval_ranks(59)["upper_rank"])
        self.assertFalse(quantile_interval_ranks(71)["finite_both_ends"])
        self.assertTrue(quantile_interval_ranks(72)["finite_both_ends"])

    def test_invalid_inputs(self):
        for n in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                tail_seen_probability(n)

    def test_repeated_workflow_not_independent(self):
        row = {"adapterType": "A", "processId": "P", "totalCount": 1, "duration_ms": 5}
        bundle = {"templates": [{"source_period": "X", "source_transactionId": "T", "rows": [row, row]}]}
        with self.assertRaises(ValueError):
            source_groups(bundle)

    def test_subsampling_is_reproducible(self):
        g = {("A", "P", 1): {("X", str(i)): float(x) for i, x in enumerate(np.arange(1, 241))}}
        a = sensitivity(g, repeats=5)
        self.assertEqual(a, sensitivity(g, repeats=5))
        self.assertEqual(len(a["aggregate"]), 5)
        self.assertFalse(a["IID_or_production_coverage_established"])


if __name__ == "__main__":
    unittest.main()
