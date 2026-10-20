"""Reconstruct compound truth and all component subsets independently."""
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison.tests.generation_oracle import (
    COMPOSITES, active_rows, component_segments, connected_intervals, expected_arms,
)
from research.evaluation.trend_sr_comparison.tests.test_generated_data import ordered


class CompoundContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from research.evaluation.trend_sr_comparison import dataset, scenarios
        cls.dataset, cls.scenarios = dataset, scenarios
        cls.cases = scenarios.compound_cases(9501, "selection")
        cls.normal = dataset.generate(9501, "selection", "normal")

    def test_recipe_inventory_has_all_registered_arms_and_groups(self):
        self.assertEqual(len(self.cases), 16)
        self.assertEqual(sum(len(case.arms) for case in self.cases), 72)
        self.assertEqual({case.recipe for case in self.cases}, set(COMPOSITES))
        self.assertEqual(len({case.case_id for case in self.cases}), 16)
        for case in self.cases:
            with self.subTest(case=case.case_id):
                self.assertEqual(set(case.arms), expected_arms(len(case.components)))
                expected = COMPOSITES[case.recipe]
                observed = tuple((part.subtype, int((part.start - case.anchor).total_seconds() / 60),
                                  int((part.end - case.anchor).total_seconds() / 60))
                                 for part in case.components)
                self.assertEqual(observed, expected)
                self.assertEqual(len({c.group for c in case.components}), 2 if case.recipe == "MX06" else 1)

    def test_all_subset_sources_preserve_base_outside_selected_components(self):
        baseline = ordered(self.normal.frame)
        for case in self.cases:
            for arm in case.arms:
                with self.subTest(case=case.case_id, arm=arm):
                    item = self.dataset.materialize_compound(case, arm)
                    selected = {c.label for c in case.components if c.label in arm}
                    self.assertEqual(set(item.events["component_id"]), selected)
                    self.assertEqual(len(item.events), len(selected))
                    self.assertEqual(item.manifest["normal_source_hash"], self.normal.manifest["source_hash"])
                    frame = ordered(item.frame)
                    active = active_rows(frame, item.events)
                    pd.testing.assert_frame_equal(frame.loc[~active].reset_index(drop=True),
                                                  baseline.loc[~active].reset_index(drop=True))
                    for name in ("source_complete", "activity_expected", "category_supported", "available_at"):
                        pd.testing.assert_series_equal(frame[name], baseline[name])
                    self.assertTrue((frame["error_count"] <= frame["total_count"]).all())
                    np.testing.assert_array_equal(frame["count"], frame["total_count"])

    def test_overlap_segments_match_independent_boundary_partition_including_all_subsets(self):
        for case in self.cases:
            for arm in case.arms:
                item = self.dataset.materialize_compound(case, arm)
                segments = self.dataset.overlap_segments(item)
                begin = pd.Timestamp(item.manifest["evaluation_start"])
                end = pd.Timestamp(item.manifest["evaluation_end"])
                for group in self.scenarios.GROUPS:
                    components = [(c.label, c.start, c.end) for c in case.components
                                  if c.group == group and c.label in arm]
                    expected = component_segments(components, begin, end)
                    actual = segments[segments["group"].eq(group)].sort_values("start")
                    rows = [(row.start, row.end, frozenset(row.active_component_ids))
                            for row in actual.itertuples()]
                    with self.subTest(case=case.case_id, arm=arm, group=group):
                        self.assertEqual(rows, expected)
                        self.assertEqual(actual["minutes"].sum(), 42 * 1440)
                        truth_minutes = sum((b - a).total_seconds() / 60
                            for a, b in connected_intervals([(a, b) for _, a, b in components]))
                        self.assertEqual(actual.loc[actual["bitmask"].ne(0), "minutes"].sum(), truth_minutes)

    def test_union_connections_do_not_bridge_mx05_gap_or_merge_mx06_groups(self):
        for case in self.cases:
            if case.recipe not in ("MX05", "MX06"):
                continue
            item = self.dataset.materialize_compound(case, "AB")
            union = self.dataset.connected_incidents(item)
            self.assertEqual(len(union), 2, case.case_id)
            self.assertEqual(union["group"].nunique(), 2 if case.recipe == "MX06" else 1)
            self.assertEqual(sum((row.end - row.start).total_seconds() / 60
                                 for row in union.itertuples()), 180)

    def test_compatibility_has_153_unique_pairs_and_rejects_zero_exposure_and_conflicts(self):
        rows = self.scenarios.compatibility_table()
        self.assertEqual(len(rows), 153)
        self.assertEqual(len({frozenset((r["first"], r["second"])) for r in rows}), 153)
        for subtypes in (("Z01", "L01"), ("Z01", "E01"), ("B02", "Z01")):
            with self.assertRaises(ValueError):
                self.dataset.validate_joint_request(subtypes)
            self.dataset.validate_joint_request(subtypes, same_group=False)


if __name__ == "__main__":
    unittest.main()
