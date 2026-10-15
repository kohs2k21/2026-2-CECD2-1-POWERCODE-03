"""Independent causality and fitting checks; no customer benchmark claims."""
import unittest

from research.evaluation.detector_dual_path.evaluate import _episode_features, _references, event_summary
from research.evaluation.detector_dual_path.replay import FrozenHistory, ReplayState
from research.evaluation.detector_dual_path.scenarios import build_suite, normal_history_observations


class IndependentExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.episodes = build_suite(45, 1)
        observations = normal_history_observations(45)
        cls.history = FrozenHistory.fit_observations(
            observations, max(item.observed_at for item in observations) + 1)
        cls.baselines = {"process": {}, "adapter": {}, "global": 100.0}

    def test_process_observed_clock_independent_of_window_stride(self):
        episode = next(e for e in self.episodes if e.split == "validation" and not e.control)
        records = []
        for stride in (60, 15, 5):
            _, rows, _, _ = _episode_features(episode, 300, stride, self.history, self.baselines)
            records.append(rows)
        self.assertEqual(records[0], records[1])
        self.assertEqual(records[0], records[2])
        self.assertTrue(any(record["clock"] % 60 != 0 for record in records[0]))

    def test_one_minute_candidate_requires_five_minute_feature_history(self):
        episode = next(e for e in self.episodes if e.control)
        replay = ReplayState(episode.start, source=episode.source, scope=episode.case_id)
        replay.observe_until(episode.events, episode.start + 60)
        frame = replay.window_frame(episode.start + 60, 60, self.history, self.baselines)
        self.assertEqual(frame.iloc[0].window_state, "warmup")

    def test_only_normal_reference_sources_enter_all_fitted_roles(self):
        rows, windows, _ = _references(self.episodes, self.history, 300, 60)
        for role in ("train", "calibration", "threshold"):
            allowed = {e.source_template_id for e in self.episodes if e.split == role and e.control}
            self.assertEqual(set(rows[role].source_template_id), allowed)
            self.assertEqual(set(windows[role].source_template_id), allowed)
            self.assertTrue(rows[role].status.eq("S").all())
            self.assertFalse(windows[role].window_state.eq("warmup").any())

    def test_comparisons_share_ready_monitoring_interval(self):
        episode = next(e for e in self.episodes if e.split == "validation" and e.control)
        records = [{"case_id": episode.case_id, "clock": episode.start + clock,
                    "prediction": True, "status": "complete", "truth": False}
                   for clock in (60, 300, 900, 1000, 1500)]
        result = event_summary([episode], records, [], "realtime")
        self.assertAlmostEqual(result["normal_monitor_hours"], 600 / 3600)
        self.assertEqual(result["normal_raw_warnings"], 2)
        self.assertAlmostEqual(result["normal_raw_warnings_per_hour"], 12)


if __name__ == "__main__":
    unittest.main()
