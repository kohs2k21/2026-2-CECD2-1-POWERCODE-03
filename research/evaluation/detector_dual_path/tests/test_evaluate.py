import unittest
from types import SimpleNamespace

from research.evaluation.detector_dual_path.evaluate import assert_independent_sources, choose_candidate, confusion, event_summary


class EvaluationTests(unittest.TestCase):
    def test_unavailable_not_false_normal(self):
        result = confusion([{"truth": True, "prediction": None, "status": "timed_out"},
                            {"truth": True, "prediction": True}])
        self.assertEqual(result["tp"], 1)
        self.assertEqual(result["fn"], 0)
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(result["coverage"], .5)

    def test_split_identity_rejected(self):
        episodes = [SimpleNamespace(split=split, source_template_id=split)
                    for split in ("train", "calibration", "threshold", "validation", "test")]
        self.assertEqual(len(assert_independent_sources(episodes)), 5)
        episodes[-1].source_template_id = "train"
        with self.assertRaises(ValueError):
            assert_independent_sources(episodes)

    def test_common_event_and_normal_time_denominator(self):
        episodes = [SimpleNamespace(case_id="a", control=False, start=0, end=3600,
                                    anomaly_start=100, anomaly_end=200, scenario="input_stop"),
                    SimpleNamespace(case_id="n", control=True, start=0, end=3600)]
        windows = [{"case_id": "a", "clock": 110, "prediction": True, "truth": True},
                   {"case_id": "n", "clock": 100, "prediction": True, "truth": False},
                   {"case_id": "n", "clock": 105, "prediction": True, "truth": False}]
        realtime = event_summary(episodes, [], windows, "realtime", normal_monitor_offset_seconds=0)
        dual = event_summary(episodes, [], windows, "dual", normal_monitor_offset_seconds=0)
        self.assertEqual(realtime["event_count"], dual["event_count"])
        self.assertEqual(realtime["confusion"]["unavailable"], 2)
        self.assertEqual(dual["trend_additional_hit"], 1)
        self.assertEqual(dual["normal_monitor_hours"], 1)
        self.assertEqual(dual["normal_raw_warnings_per_hour"], 2)
        self.assertEqual(dual["normal_suppressed_warnings_per_hour"], 1)
        self.assertEqual(dual["first_delay_p95_seconds"], 10)

    def test_validation_selection_has_frozen_priority(self):
        def candidate(hits, warnings, duration):
            return {"algorithm": "if", "lookback_seconds": duration, "stride_seconds": 60,
                    "paths": {"dual": {"trend_additional_hit": hits,
                                        "normal_suppressed_warnings_per_hour": warnings,
                                        "first_delay_p95_seconds": 10}}, "compute_p95_ms": 1}
        best = candidate(2, 1, 300)
        winner, _ = choose_candidate([candidate(1, 0, 60), best, candidate(2, 2, 900)])
        self.assertIs(winner, best)


if __name__ == "__main__":
    unittest.main()
