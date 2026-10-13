"""Independent review regressions for causal replay and delivery recovery."""
import json
import unittest

from research.evaluation.detector_dual_path.replay import FrozenHistory, Observation, ReplayState, iso
from research.evaluation.detector_dual_path.scenarios import normal_history_observations


class IndependentReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events = normal_history_observations(seed=731)
        cutoff = max(event.observed_at for event in events) + 1
        cls.history = FrozenHistory.fit(events, cutoff, widths=(60, 300, 900))
        cls.start = cutoff + 1000
        cls.baselines = {"process": {}, "adapter": {}, "global": 1000}

    def event(self, event_offset=0, observed_offset=1, revision=0, execution="execution", **fields):
        return Observation("synthetic", "TARGET", execution, "P00", 0, revision,
                           self.start + event_offset, self.start + observed_offset,
                           {"adapterType": "HTTP", "startTime": iso(self.start + event_offset),
                            "endTime": None, "status": "P", "totalCount": 2, **fields})

    def frame(self, state, offset, width=60):
        return state.window_frame(self.start + offset, width, self.history, self.baselines)

    def test_duplicate_redelivery_after_timer_and_restart_is_idempotent(self):
        state = ReplayState(self.start)
        event = self.event()
        state.ingest(event)
        state.advance(self.start + 10)
        self.assertFalse(state.ingest(event))
        self.assertEqual(state.clock, self.start + 10)
        restored = ReplayState.restore(json.loads(json.dumps(state.snapshot())))
        self.assertFalse(restored.ingest(event))
        self.assertEqual(restored.audit["duplicates"], 2)
        self.assertEqual(restored.clock, self.start + 10)
        self.assertEqual(len(restored.latest), 1)
        self.assertEqual(len(restored.arrivals), 1)

    def test_timer_states_without_new_event_callbacks(self):
        state = ReplayState(self.start)
        self.assertEqual(self.frame(state, 10).window_state.iloc[0], "warmup")
        self.assertEqual(self.frame(state, 60).window_state.iloc[0], "warmup")
        self.assertEqual(self.frame(state, 300).window_state.iloc[0], "no_arrivals")
        state.ingest(self.event(305, 306))
        self.assertEqual(self.frame(state, 330).window_state.iloc[0], "no_completions")
        self.assertEqual(state.pending(), [])
        self.assertEqual(len(state.advance(self.start + 335)), 1)
        state.ingest(self.event(305, 336, revision=1, endTime=iso(self.start + 334), status="S"))
        self.assertEqual(self.frame(state, 340).window_state.iloc[0], "ready")
        self.assertEqual(state.pending(), [])

    def test_collector_failure_and_closed_business_are_not_input_stop(self):
        state = ReplayState(self.start)
        state.set_visibility(self.start + 1, collector_visible=False)
        self.assertEqual(self.frame(state, 60).window_state.iloc[0], "collector_unknown")
        state.set_visibility(self.start + 61, collector_visible=True, business_open=False)
        self.assertEqual(self.frame(state, 121).window_state.iloc[0], "business_closed")
        state.set_visibility(self.start + 122, collector_visible=True, business_open=True)
        self.assertEqual(self.frame(state, 182).window_state.iloc[0], "warmup")
        self.assertEqual(self.frame(state, 300).window_state.iloc[0], "no_arrivals")

    def test_future_final_values_do_not_reappear_without_observation(self):
        state = ReplayState(self.start)
        state.ingest(self.event(endTime=iso(self.start + 4), status="S", successCount=2, errorCount=0))
        state.advance(self.start + 5)
        frame = state.process_frame()
        self.assertTrue(frame.duration_ms.isna().all())
        self.assertTrue(frame.successCount.isna().all())
        self.assertTrue(frame.errorCount.isna().all())
        self.assertEqual(frame.status.iloc[0], "P")

    def test_window_half_open_boundaries_and_result_copy(self):
        state = ReplayState(self.start)
        state.ingest(self.event(0, 2, endTime=iso(self.start + 1), status="S"))
        state.ingest(self.event(60, 60, execution="right-boundary"))
        frame = self.frame(state, 60)
        self.assertEqual(frame.execution_arrivals.iloc[0], 1)
        self.assertEqual(frame.completed_rows.iloc[0], 1)
        result = {"decision": "partial", "details": ["a"]}
        key = state.window_key(self.start + 60, 60, "v1")
        self.assertTrue(state.commit_result(key, result))
        result["details"].append("b")
        self.assertEqual(state.results[key]["details"], ["a"])
        restored = ReplayState.restore(json.loads(json.dumps(state.snapshot())))
        self.assertEqual(restored.results, state.results)
        with self.assertRaises(ValueError):
            restored.commit_result(key, {"decision": "normal"})


if __name__ == "__main__":
    unittest.main()
