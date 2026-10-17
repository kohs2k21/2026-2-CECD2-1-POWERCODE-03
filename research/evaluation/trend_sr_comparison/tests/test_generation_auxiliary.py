"""Quality, recurrence, relation controls, and unopened-final invariants."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison.tests.generation_oracle import active_rows
from research.evaluation.trend_sr_comparison.tests.test_generated_data import ordered


class AuxiliaryContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from research.evaluation.trend_sr_comparison import dataset, generation
        cls.dataset, cls.generation = dataset, generation
        cls.normal = dataset.generate(9501, "selection", "normal")
        cls.quality = dataset.generate(9501, "selection", "quality")
        cls.recurrence = dataset.generate(9501, "selection", "recurrence")

    def test_recurrence_is_observable_on_business_days_including_all_t04_gaps(self):
        item = self.recurrence
        self.assertEqual(item.manifest["intended_cases"], 12)
        self.assertEqual(len(item.events), 18)
        base = ordered(self.normal.frame)
        active = active_rows(base, item.events)
        self.assertTrue(base.loc[active, "activity_expected"].all())
        self.assertTrue(base.loc[active, "duration_ms"].notna().all())
        for _, truth in item.events.groupby("case_id"):
            first = truth["start"].min().tz_convert("Asia/Seoul")
            self.assertLess(first.weekday(), 5)
            self.assertEqual(first.hour, 10)

    def test_true_normal_recurrence_gaps_and_ongoing_incident_gaps_have_different_truth(self):
        frame, base = ordered(self.recurrence.frame), ordered(self.normal.frame)
        for case in self.recurrence.manifest["definitions"]:
            intervals = case["intervals"]
            left, right = intervals[0][1], intervals[1][0]
            gap = (frame["group"].eq(case["group"]) & frame["bin_start"].ge(left) & frame["bin_start"].lt(right))
            pd.testing.assert_frame_equal(frame.loc[gap], base.loc[gap])
            truth = self.recurrence.events[self.recurrence.events["case_id"].eq(case["case_id"])]
            inside = active_rows(frame, truth)
            if case["scenario_id"] == "SC-T04":
                self.assertEqual(len(truth), 1)
                self.assertTrue(inside[gap].all())
            else:
                self.assertEqual(len(truth), 2)
                self.assertFalse(inside[gap].any())

    def test_quality_changes_do_not_create_service_truth_or_zero_fill_missing_observations(self):
        self.assertEqual(self.quality.manifest["intended_cases"], 8)
        self.assertEqual(len(self.quality.events), 0)
        frame, base = ordered(self.quality.frame), ordered(self.normal.frame)
        missing = ~frame["source_complete"]
        self.assertTrue(missing.any())
        self.assertTrue(frame.loc[missing, ["count", "duration_ms", "error_count", "total_count"]].isna().all().all())
        pd.testing.assert_series_equal(frame["activity_expected"], base["activity_expected"])
        normal_support = frame["source_complete"] & frame["category_supported"]
        self.assertTrue((pd.to_datetime(frame.loc[normal_support, "available_at"]) >=
                         pd.to_datetime(base.loc[normal_support, "available_at"])).all())

    def test_actual_revision_ledger_has_duplicates_and_canonical_latest_version(self):
        ledger = self.generation.quality_raw_ledger(self.quality)
        self.assertEqual(len(ledger), 2 * 60 * 3)
        for key, records in ledger.groupby("source_key"):
            self.assertEqual(len(records), 3, key)
            self.assertEqual(set(records["record"]), {"original", "duplicate", "revision"})
            old = records[records["record"].eq("original")].iloc[0]
            duplicate = records[records["record"].eq("duplicate")].iloc[0]
            last = records.sort_values(["version", "available_at"]).iloc[-1]
            self.assertEqual(old["count"], duplicate["count"])
            self.assertEqual(old["version"], duplicate["version"])
            self.assertEqual(last["count"], last["canonical_count"])
            self.assertEqual(last["available_at"], last["canonical_available_at"])
            self.assertEqual(last["available_at"] - old["available_at"], pd.Timedelta(minutes=10))

    def test_relation_control_intervenes_only_on_relation_windows_and_preserves_marginals(self):
        controls = self.dataset.relation_controls(9501)
        baseline = ordered(controls["no_intervention"].frame)
        actual = ordered(controls["intervention"].frame)
        events = controls["intervention"].events
        self.assertEqual(set(events["scenario_id"]), {"SC-R01", "SC-R02"})
        active = active_rows(actual, events)
        pd.testing.assert_frame_equal(actual.loc[~active], baseline.loc[~active])
        pd.testing.assert_frame_equal(actual.drop(columns="duration_ms"), baseline.drop(columns="duration_ms"))
        for _, truth in events.groupby("case_id"):
            mask = active_rows(actual, truth)
            np.testing.assert_array_equal(np.sort(actual.loc[mask, "duration_ms"]),
                                          np.sort(baseline.loc[mask, "duration_ms"]))
        pd.testing.assert_frame_equal(ordered(controls["marginal_only"].frame), baseline)

    def test_final_is_rejected_before_source_materialization(self):
        with patch.object(self.dataset, "_normal") as generate:
            for seed, split in ((4101, "final"), (4101, "selection"), (9501, "final")):
                with self.assertRaises(ValueError):
                    self.dataset.generate(seed, split)
            case = self.dataset.build_compound_cases(4101, "final")[0]
            with self.assertRaises(ValueError):
                self.dataset.materialize_compound(case, "AB")
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
