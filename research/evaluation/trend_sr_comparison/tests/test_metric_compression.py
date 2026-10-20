"""Exact run-length calibration versus dense minute scoring, including unknowns."""
import unittest

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison import metrics
from research.evaluation.trend_sr_comparison.tests.test_independent_metrics import timeline


class CompressedMetricContracts(unittest.TestCase):
    def test_weighted_statistics_match_expanded_minutes_for_all_fields(self):
        for seed in range(40):
            rng = np.random.default_rng(92700+seed)
            n = 70
            weights = rng.integers(1, 65, n)
            source, common, planned, supported, inside = [rng.random(n) > p for p in (.1,.05,.1,.1,.8)]
            positive = (rng.random(n) > .7) & source & common
            available = source & common & (supported | positive)
            arguments = (positive,available,source,common,planned,supported,inside)
            dense = [np.repeat(value,weights) for value in arguments]
            for gap in (0,10,60):
                compressed = metrics._array_stats(*arguments,gap,weights=weights)
                expanded = metrics._array_stats(*dense,gap)
                self.assertEqual(compressed[0],expanded[0],(seed,gap))
                np.testing.assert_array_equal(compressed[-1],expanded[-1])

    def test_compression_preserves_every_candidate_group_cadence_guard(self):
        for seed in range(5):
            rng = np.random.default_rng(92800+seed)
            data = {}
            for cadence in (1,60):
                groups = []
                for group in ("A","B"):
                    frame = timeline([0]*600,group)
                    values = np.repeat(rng.integers(0,7,(30,2)).astype(float),20,axis=0)
                    values[np.repeat(rng.random((30,2))<.1,20,axis=0)] = np.nan
                    frame["a"],frame["b"] = values[:,0],values[:,1]
                    frame.loc[:29,"in_evaluation"] = False
                    # Exposure is identical across cadences; score snapshots differ.
                    frame.loc[120:124,"source_eligible"] = False
                    frame.loc[400:409,"common_eligible"] = False
                    groups.append(frame)
                data[cadence] = pd.concat(groups,ignore_index=True)
            result = metrics.calibrate(data,("a","b"),quantiles=(.9,.95,1),budgets=(100,.1))
            expected_first = None
            for check in result["candidate_checks"]:
                passing = True
                expected_guards = []
                for cadence, frame in data.items():
                    dense = metrics.evaluate(metrics.apply_thresholds(frame,check["thresholds"]))["summary"]
                    for row in dense:
                        okay = row["normal_prediction_available_minutes"] > 0 and row["false_alarm_episodes_per_hour"] <= 100 and row["positive_time_fraction"] <= .1
                        passing &= okay
                        expected_guards.append((cadence,row["group"],okay,row))
                self.assertEqual(check["passed"],passing)
                for actual, (cadence,group,okay,expected) in zip(check["guards"],expected_guards):
                    self.assertEqual((actual["cadence"],actual["group"],actual["passed"]),(cadence,group,okay))
                    for name in ("normal_planned_minutes","normal_source_minutes","normal_common_minutes",
                                 "normal_all_branch_supported_minutes","normal_prediction_available_minutes",
                                 "positive_normal_minutes","false_alarm_episodes","positive_time_fraction","unknown_minutes"):
                        self.assertEqual(actual[name],expected[name],name)
                if passing and expected_first is None: expected_first = check["quantile"]
            self.assertEqual(result["quantile"],expected_first)


if __name__ == "__main__":
    unittest.main()
