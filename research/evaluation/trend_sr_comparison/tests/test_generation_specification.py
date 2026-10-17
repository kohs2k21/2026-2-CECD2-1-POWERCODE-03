"""Independent plan oracle checks; these do not certify generated data."""
import unittest

from research.evaluation.trend_sr_comparison.tests.generation_oracle import (
    COMPOSITES, ERROR, MAIN, PULSES, SINGLE_LENGTHS, component_segments,
    connected_intervals, expected_arms,
)


class IndependentSpecification(unittest.TestCase):
    def test_declared_case_and_active_event_counts(self):
        self.assertEqual(len(MAIN), 15)
        self.assertEqual(len(ERROR), 3)
        self.assertEqual(len(set(MAIN + ERROR)), 18)
        self.assertEqual(set(SINGLE_LENGTHS) | set(PULSES), set(MAIN + ERROR))
        counts = lambda names: sum(PULSES[name][1] if name in PULSES else 1
                                   for name in names)
        self.assertEqual((len(MAIN) * 10, counts(MAIN) * 10), (150, 200))
        self.assertEqual((len(ERROR) * 10, counts(ERROR) * 10), (30, 60))

    def test_composite_subsets_and_pair_coverage(self):
        self.assertEqual(len(COMPOSITES) * 10, 80)
        self.assertEqual(sum(len(expected_arms(len(parts))) * 10
                             for parts in COMPOSITES.values()), 360)
        self.assertEqual(len(MAIN + ERROR) * (len(MAIN + ERROR) - 1) // 2, 153)
        self.assertEqual(expected_arms(3), {"0", "A", "B", "C", "AB", "AC", "BC", "ABC"})

    def test_schedule_rotations_are_permutations(self):
        for seed_index in range(5):
            for group_index in range(2):
                order = [(4 * slot + 3 * seed_index + group_index) % 15
                         for slot in range(15)]
                self.assertEqual(sorted(order), list(range(15)))

    def test_interval_partition_preserves_c_only_and_gap(self):
        segments = component_segments([("A", 0, 5), ("B", 2, 6), ("C", 7, 9)], 0, 10)
        self.assertEqual(sum(right - left for left, right, _ in segments), 10)
        self.assertEqual(sum(right - left for left, right, active in segments if active), 8)
        self.assertIn((7, 9, frozenset({"C"})), segments)
        self.assertIn((6, 7, frozenset()), segments)
        self.assertEqual(connected_intervals([(0, 5), (2, 6), (7, 9)]), [(0, 6), (7, 9)])


if __name__ == "__main__":
    unittest.main()
