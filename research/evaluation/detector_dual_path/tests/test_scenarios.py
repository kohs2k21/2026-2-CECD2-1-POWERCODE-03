import unittest
from research.evaluation.detector_dual_path.scenarios import SCENARIOS, SPLITS, build_suite


class ScenarioTests(unittest.TestCase):
    def test_fixed_seed_and_five_independent_partitions(self):
        episodes = build_suite(seed=45)
        self.assertEqual(episodes, build_suite(seed=45))
        self.assertEqual(len(episodes), 2 * len(SCENARIOS) * len(SPLITS))
        cohorts = {split: {e.source_template_id for e in episodes if e.split == split} for split in SPLITS}
        for first in SPLITS:
            for second in SPLITS:
                if first != second:
                    self.assertFalse(cohorts[first] & cohorts[second])

    def test_labels_separate_and_stop_has_no_observed_arrivals(self):
        for episode in build_suite():
            self.assertTrue(episode.timestamp_resolution_seconds == 1)
            self.assertTrue(all("label" not in event.fields for event in episode.events))
            if episode.scenario == "input_stop" and not episode.control:
                self.assertFalse(any(episode.anomaly_start <= event.event_at < episode.anomaly_end
                                     for event in episode.events))

    def test_paired_normal_recovery_remains_identical(self):
        suite = build_suite()
        injected = next(e for e in suite if e.split == "test" and e.scenario == "input_stop" and not e.control)
        normal = next(e for e in suite if e.source_template_id == injected.source_template_id and e.control)
        abnormal_recovery = [(e.event_at, e.observed_at, e.fields) for e in injected.events if e.event_at >= injected.anomaly_end]
        normal_recovery = [(e.event_at, e.observed_at, e.fields) for e in normal.events if e.event_at >= injected.anomaly_end]
        self.assertEqual(abnormal_recovery, normal_recovery)


if __name__ == "__main__":
    unittest.main()
