"""Hand-derived scoring examples and immutable legacy cross-checks."""
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison import metrics


def timeline(values, group="g"):
    n = len(values)
    return pd.DataFrame({"group": group,
        "decision_tick": pd.date_range("2025-05-12", periods=n, freq="min", tz="UTC"),
        "prediction": np.asarray(values, float), "score_supported": True,
        "source_eligible": True, "common_eligible": True, "in_evaluation": True})


def event(frame, first, last, name="event"):
    start = frame["decision_tick"].iloc[0]
    return {"event_id": name, "group": frame["group"].iloc[0],
            "start": start + pd.Timedelta(minutes=first),
            "end": start + pd.Timedelta(minutes=last)}


class IndependentMetrics(unittest.TestCase):
    def test_ternary_or_does_not_turn_missing_branch_into_normal(self):
        frame = timeline([0] * 5)
        frame["a"] = [2, 0, np.nan, 2, 1]
        frame["b"] = [np.nan, np.nan, np.nan, 0, 1]
        frame.loc[3, "source_eligible"] = False
        result = metrics.apply_thresholds(frame, {"a": 1, "b": 1})
        np.testing.assert_equal(result["prediction"].to_numpy(), [1, np.nan, np.nan, np.nan, 0])
        np.testing.assert_array_equal(result["score_supported"], [False, False, False, True, True])

    def test_episode_merge_uses_known_gap_and_unknown_always_breaks(self):
        frame = timeline([1] + [0] * 10 + [1, np.nan, 1] + [0] * 11 + [1])
        result = metrics.evaluate(frame)
        self.assertEqual(len(result["episodes"]), 3)
        self.assertEqual([r["positive_minutes"] for r in result["episodes"]], [2, 1, 1])
        self.assertEqual(result["summary"][0]["positive_normal_minutes"], 4)
        self.assertEqual(result["summary"][0]["normal_prediction_available_minutes"], len(frame) - 1)

    def test_preexisting_and_late_alarms_are_not_event_hits(self):
        frame = timeline([0] * 70)
        frame.loc[0:6, "prediction"] = 1
        frame.loc[20:24, "prediction"] = 1
        truth = pd.DataFrame([event(frame, 2, 5, "preexisting"), event(frame, 15, 20, "late")])
        result = metrics.evaluate(frame, truth)
        self.assertEqual([r["hit"] for r in result["events"]], [False, False])
        self.assertEqual(result["summary"][0]["positive_normal_minutes"], 9)
        self.assertEqual(result["summary"][0]["false_alarm_episodes"], 2)
        self.assertTrue(all(r["recovery_quiet1_delay_minutes"] is None for r in result["events"]))

    def test_overlapping_labels_cannot_double_credit_one_onset(self):
        frame = timeline([0] * 50)
        frame.loc[10:29, "prediction"] = 1
        result = metrics.evaluate(frame, pd.DataFrame([event(frame, 8, 25, "A"), event(frame, 9, 30, "B")]))
        self.assertEqual(sum(r["hit"] for r in result["events"]), 1)
        self.assertEqual(result["summary"][0]["positive_normal_minutes"], 0)
        self.assertEqual(result["summary"][0]["normal_planned_minutes"], 28)

    def test_group_clocks_cannot_share_episode_credit(self):
        first, second = timeline([0] * 30, "A"), timeline([0] * 30, "B")
        first.loc[10:19, "prediction"] = 1
        truth = pd.DataFrame([event(first, 10, 20, "A"), event(second, 10, 20, "B")])
        result = metrics.evaluate(pd.concat([first, second]), truth)
        self.assertEqual([r["hit"] for r in result["events"]], [True, False])

    def test_recovery_requires_hit_and_resets_sustained_quiet_on_unknown(self):
        frame = timeline([0] * 70)
        frame.loc[5:9, "prediction"] = 1
        frame.loc[14, "prediction"] = np.nan
        result = metrics.evaluate(frame, pd.DataFrame([event(frame, 5, 10)]))["events"][0]
        self.assertEqual(result["recovery_quiet1_delay_minutes"], 0)
        self.assertEqual(result["recovery_quiet10_delay_minutes"], 5)
        self.assertEqual(result["recovery_quiet10_confirmed_after_minutes"], 15)
        self.assertEqual(result["recovery_quiet30_delay_minutes"], 5)
        self.assertEqual(result["recovery_unknown_minutes"], 1)

    def test_preclear_and_fully_unknown_recovery_are_censored_not_zero(self):
        frame = timeline([0] * 30)
        frame.loc[5:7, "prediction"] = 1
        row = metrics.evaluate(frame, pd.DataFrame([event(frame, 5, 10)]))["events"][0]
        self.assertTrue(row["recovery_precleared"])
        self.assertIsNone(row["recovery_quiet1_delay_minutes"])
        self.assertEqual(row["recovery_quiet1_censor_reason"], "precleared_before_end")
        frame.loc[5:9, "prediction"] = 1
        frame.loc[10:, "prediction"] = np.nan
        row = metrics.evaluate(frame, pd.DataFrame([event(frame, 5, 10)]))["events"][0]
        self.assertIsNone(row["recovery_quiet1_delay_minutes"])
        self.assertEqual(row["recovery_unknown_minutes"], 20)

    def test_bad_predictions_and_duplicate_minutes_are_rejected(self):
        for value in (2, np.inf, -np.inf):
            frame = timeline([0, value, 0])
            with self.assertRaises(ValueError):
                metrics.evaluate(frame)
        frame = timeline([0, 1, 0])
        frame.loc[1, "decision_tick"] = frame.loc[0, "decision_tick"]
        with self.assertRaises(ValueError):
            metrics.evaluate(frame)

    def test_calibration_checks_all_candidates_and_keeps_strict_cut(self):
        frame = timeline([0] * 100)
        frame["score"] = np.arange(100, dtype=float)
        result = metrics.calibrate({1: frame, 5: frame.copy()}, quantiles=(.9, .95, 1), budgets=(100, .05))
        self.assertEqual(len(result["candidate_checks"]), 3)
        self.assertEqual(result["quantile"], .95)
        self.assertEqual(result["thresholds"], {"score": 95.})
        self.assertEqual([r["passed"] for r in result["candidate_checks"]], [False, True, True])
        self.assertFalse(result["test_used"])

    def test_calibration_rejects_missing_group_and_changed_source_exposure(self):
        first, second = timeline([0] * 50, "A"), timeline([0] * 50, "B")
        first["score"], second["score"] = 0., 0.
        complete = pd.concat([first, second], ignore_index=True)
        with self.assertRaises(ValueError):
            metrics.calibrate({1: complete, 5: first})
        changed = complete.copy()
        changed.loc[0, "source_eligible"] = False
        with self.assertRaises(ValueError):
            metrics.calibrate({1: complete, 5: changed})

    def test_normal_episode_and_occupancy_match_immutable_legacy_on_random_traces(self):
        from research.evaluation.long_term_trend import evaluation as legacy
        for seed in range(12):
            rng = np.random.default_rng(seed)
            values = rng.choice([0., 1., np.nan], 160, p=[.76, .20, .04])
            frame = timeline(values)
            truth = pd.DataFrame([event(frame, 20, 40, "first"), event(frame, 90, 105, "second")])
            result = metrics.evaluate(frame, truth)
            intervals = frame.copy()
            intervals["prediction_available"] = np.isfinite(values)
            intervals["positive"] = values == 1
            intervals["source_known"] = True
            intervals["eligible"] = True
            intervals["interval_end"] = intervals["decision_tick"] + pd.Timedelta(minutes=1)
            episodes = legacy.alarm_episodes(intervals, 10)
            expected = legacy.normal_statistics(intervals, episodes, truth)[0]
            actual = result["summary"][0]
            self.assertEqual(len(result["episodes"]), len(episodes))
            for name in ("false_alarm_episodes", "positive_normal_minutes", "normal_monitored_hours", "positive_time_fraction"):
                self.assertAlmostEqual(actual[name], expected[name], places=13)

    def test_macro_equal_weights_subtypes_and_families_without_pulse_inflation(self):
        frame = timeline([0] * 120)
        frame.loc[5:9, "prediction"] = 1
        frame.loc[30:34, "prediction"] = 1
        rows = [event(frame, 5, 10, "b1"), event(frame, 30, 35, "b3-first"),
                event(frame, 60, 65, "b3-second"), event(frame, 90, 95, "z1")]
        for row, scenario, case in zip(rows, ("SC-B01", "SC-B03", "SC-B03", "SC-Z01"),
                                       ("one", "pulses", "pulses", "zero")):
            row.update(scenario_id=scenario, case_id=case)
        result = metrics.evaluate(frame, pd.DataFrame(rows))
        summary = result["summary"][0]
        self.assertEqual(summary["macro_subtype_recall"], .5)
        self.assertEqual(summary["macro_family_recall"], .375)
        self.assertEqual(summary["family_recalls"], {"B": .75, "Z": 0.})
        pulses = next(r for r in result["cases"] if r["case_id"] == "pulses")
        self.assertTrue(pulses["any_pulse_hit"])
        self.assertFalse(pulses["all_pulses_hit"])
        self.assertEqual((pulses["active_events"], pulses["hits"], pulses["pulse_recall"]), (2, 1, .5))
        self.assertEqual((pulses["active_minutes"], pulses["positive_active_minutes"]), (10, 5))

    def test_independent_normal_denominators_keep_partial_or_support_visible(self):
        frame = timeline([1, 0, np.nan, 1, 0, np.nan])
        frame["source_eligible"] = [1, 1, 1, 1, 0, 0]
        frame["common_eligible"] = [1, 1, 0, 1, 1, 0]
        frame["score_supported"] = [0, 1, 1, 1, 1, 1]
        result = metrics.evaluate(frame)["summary"][0]
        self.assertEqual((result["normal_planned_minutes"], result["normal_source_minutes"],
                          result["normal_common_minutes"], result["normal_all_branch_supported_minutes"],
                          result["normal_prediction_available_minutes"]), (6, 4, 3, 2, 3))
        self.assertEqual(result["positive_normal_minutes"], 2)
        self.assertEqual(result["positive_fraction_source"], .5)
        self.assertAlmostEqual(result["positive_fraction_common"], 2/3)
        self.assertEqual(result["positive_fraction_all_branch_supported"], .5)


if __name__ == "__main__":
    unittest.main()
