"""Independent source/truth checks using a non-main, non-final fixture seed."""
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison.tests.generation_oracle import (
    ERROR, MAIN, PULSES, SINGLE_LENGTHS, active_rows,
)


def ordered(frame):
    return frame.sort_values(["group", "bin_start"]).reset_index(drop=True)


class GeneratedSourceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from research.evaluation.trend_sr_comparison import dataset
        cls.dataset = dataset
        cls.normal = dataset.generate(9501, "selection", suite="normal")
        cls.main = dataset.generate(9501, "selection", suite="main")
        cls.error = dataset.generate(9501, "selection", suite="error")

    def test_complete_fixture_source_is_physically_consistent(self):
        for item in (self.normal, self.main, self.error):
            with self.subTest(suite=item.manifest):
                frame = item.frame
                self.assertIsInstance(frame.index, pd.DatetimeIndex)
                self.assertEqual(str(frame.index.tz), "UTC")
                self.assertFalse(frame.reset_index(drop=True).duplicated(["group", "bin_start"]).any())
                starts = pd.DatetimeIndex(frame["bin_start"])
                np.testing.assert_array_equal(frame.index.as_unit("ns").asi8 - starts.as_unit("ns").asi8,
                                              np.full(len(frame), pd.Timedelta(minutes=1).value))
                self.assertTrue(frame["source_complete"].all())
                self.assertTrue((pd.to_datetime(frame["available_at"]).array >= frame.index.array).all())
                for name in ("count", "total_count", "error_count"):
                    values = frame[name].to_numpy()
                    self.assertTrue(np.isfinite(values).all())
                    self.assertTrue((values >= 0).all())
                    np.testing.assert_array_equal(values, np.floor(values))
                np.testing.assert_array_equal(frame["count"], frame["total_count"])
                self.assertTrue((frame["error_count"] <= frame["total_count"]).all())
                self.assertTrue(frame.loc[frame["count"].eq(0), "duration_ms"].isna().all())

    def test_main_and_error_coverage_keeps_case_and_active_event_denominators(self):
        for item, names, cases, events in ((self.main, MAIN, 30, 40),
                                           (self.error, ERROR, 6, 12)):
            truth = item.events
            self.assertEqual(set(truth["scenario_id"]), set(names))
            self.assertEqual(truth["case_id"].nunique(), cases)
            self.assertEqual(len(truth), events)
            self.assertEqual(truth["event_id"].nunique(), events)
            self.assertEqual(set(truth["source_seed"]), {9501})
            self.assertTrue((truth["end"] > truth["start"]).all())
            for group, cohort in truth.groupby("group"):
                with self.subTest(group=group):
                    self.assertEqual(set(cohort["scenario_id"]), set(names))
                    self.assertEqual(cohort["case_id"].nunique(), len(names))

    def test_pulse_lengths_gaps_and_single_event_lengths_are_exact(self):
        for item in (self.main, self.error):
            for case, truth in item.events.groupby("case_id"):
                truth = truth.sort_values("start")
                names = set(truth["scenario_id"])
                self.assertEqual(len(names), 1)
                name = next(iter(names))
                if name in PULSES:
                    length, count, gap = PULSES[name]
                    self.assertEqual(len(truth), count, case)
                    starts = pd.DatetimeIndex(truth["start"]).as_unit("ns")
                    np.testing.assert_array_equal(np.diff(starts.asi8),
                        np.full(count - 1, pd.Timedelta(minutes=length + gap).value))
                else:
                    length = SINGLE_LENGTHS[name]
                    self.assertEqual(len(truth), 1, case)
                self.assertTrue(((truth["end"] - truth["start"]) ==
                                 pd.Timedelta(minutes=length)).all(), case)

    def test_intervention_cannot_modify_normal_gaps_or_other_source_rows(self):
        baseline = ordered(self.normal.frame)
        for item in (self.main, self.error):
            frame = ordered(item.frame)
            self.assertEqual(len(frame), len(baseline))
            active = active_rows(frame, item.events)
            self.assertTrue(active.any())
            pd.testing.assert_frame_equal(frame.loc[~active].reset_index(drop=True),
                                          baseline.loc[~active].reset_index(drop=True))
            for name in ("bin_start", "group", "source_complete", "activity_expected",
                         "category_supported", "available_at"):
                pd.testing.assert_series_equal(frame[name], baseline[name])
        for name in ("count", "duration_ms", "total_count"):
            pd.testing.assert_series_equal(ordered(self.error.frame)[name], baseline[name])

    def test_zero_count_has_unknown_duration_without_becoming_source_failure(self):
        frame = ordered(self.main.frame)
        truth = self.main.events[self.main.events["scenario_id"].isin(["SC-Z01", "SC-Z03"])]
        affected = active_rows(frame, truth)
        self.assertTrue(frame.loc[affected, "count"].eq(0).all())
        self.assertTrue(frame.loc[affected, "duration_ms"].isna().all())
        self.assertTrue(frame.loc[affected, "source_complete"].all())
        self.assertTrue(frame.loc[affected, "activity_expected"].all())

    def test_duration_variance_case_preserves_mean_and_increases_variability(self):
        baseline = ordered(self.normal.frame)
        frame = ordered(self.main.frame)
        for case, truth in self.main.events[self.main.events["scenario_id"].eq("SC-L03")].groupby("case_id"):
            affected = active_rows(frame, truth)
            before = baseline.loc[affected, "duration_ms"].to_numpy()
            after = frame.loc[affected, "duration_ms"].to_numpy()
            before, after = before[np.isfinite(before)], after[np.isfinite(after)]
            self.assertEqual(len(before), 120, case)
            self.assertEqual(len(after), 120, case)
            self.assertLessEqual(abs(after.mean() - before.mean()) / before.mean(), 1e-6, case)
            self.assertGreater(after.std() / after.mean(), before.std() / before.mean(), case)
            self.assertGreater(after.max(), before.max(), case)

    def test_cases_have_full_day_washout_and_business_activity(self):
        for item in (self.main, self.error):
            for group, truth in item.events.groupby("group"):
                cases = truth.groupby("case_id").agg(start=("start", "min"), end=("end", "max")).sort_values("start")
                gaps = cases["start"].iloc[1:].reset_index(drop=True) - cases["end"].iloc[:-1].reset_index(drop=True)
                self.assertTrue((gaps >= pd.Timedelta(days=1)).all(), group)
                local_start = cases["start"].dt.tz_convert("Asia/Seoul")
                local_end = cases["end"].dt.tz_convert("Asia/Seoul")
                self.assertTrue((local_start.dt.weekday < 5).all(), group)
                self.assertTrue(local_start.dt.hour.eq(10).all(), group)
                self.assertTrue(local_end.dt.hour.lt(17).all(), group)

    def test_fixture_replay_is_exact_and_input_call_order_does_not_change_it(self):
        repeat = self.dataset.generate(9501, "selection", suite="main")
        pd.testing.assert_frame_equal(self.main.frame, repeat.frame)
        pd.testing.assert_frame_equal(self.main.events, repeat.events)
        pd.testing.assert_frame_equal(self.main.quality, repeat.quality)

    def test_all_eighteen_transforms_have_declared_physical_effects(self):
        baseline = ordered(self.normal.frame)
        seen = set()
        for item in (self.main, self.error):
            frame = ordered(item.frame)
            for _, truth in item.events.groupby("case_id"):
                name = truth["scenario_id"].iloc[0]
                seen.add(name)
                mask = active_rows(frame, truth)
                before, after = baseline.loc[mask], frame.loc[mask]
                subtype = name[3:]
                with self.subTest(scenario=name, group=truth["group"].iloc[0]):
                    if subtype[0] in "BZG":
                        factor = {"B01": 5., "B02": 2., "B03": 3., "Z01": 0.,
                                  "Z02": .2, "Z03": 0.}.get(subtype)
                        if subtype == "G01": factor = np.linspace(1., 2., 180)
                        if subtype == "G02": factor = np.geomspace(1., 4., 180)
                        if subtype == "G03": factor = np.repeat([1.5, 2., 2.5], 60)
                        np.testing.assert_array_equal(after["count"], np.rint(before["count"].to_numpy() * factor))
                    else:
                        for column in ("count", "total_count"):
                            pd.testing.assert_series_equal(after[column], before[column])
                    if subtype in ("L01", "L02"):
                        multiplier = 2. if subtype == "L01" else 5.
                        np.testing.assert_allclose(after["duration_ms"], before["duration_ms"] * multiplier, rtol=1e-14)
                    if subtype in ("R01", "R02"):
                        r = after["count"].corr(after["duration_ms"])
                        self.assertLessEqual(abs(r), .3) if subtype == "R01" else self.assertLessEqual(r, -.4)
                        np.testing.assert_array_equal(np.sort(after["duration_ms"]), np.sort(before["duration_ms"]))
                    if subtype == "R03":
                        old_slope = np.polyfit(before["count"], before["duration_ms"], 1)[0]
                        new_slope = np.polyfit(after["count"], after["duration_ms"], 1)[0]
                        self.assertGreater(new_slope, old_slope * 1.5)
                    if subtype[0] == "E":
                        probability = .5 if subtype == "E02" else .15
                        total = after["total_count"].sum()
                        rate = after["error_count"].sum() / total
                        tolerance = max(.03, 4 * np.sqrt(probability * (1-probability) / total))
                        self.assertLessEqual(abs(rate-probability), tolerance)
        self.assertEqual(seen, set(MAIN + ERROR))


if __name__ == "__main__":
    unittest.main()
