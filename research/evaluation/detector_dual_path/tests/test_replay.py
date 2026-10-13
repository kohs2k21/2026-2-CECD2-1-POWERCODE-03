"""Replay invariants. Execution belongs to an independent tester."""
import json
import unittest

from research.evaluation.detector_dual_path.replay import FrozenHistory, Observation, ReplayState, iso
from research.evaluation.detector_dual_path.scenarios import normal_history_observations


class ReplayTests(unittest.TestCase):
    def event(self, start=100, observed=101, revision=0, retry=0, **fields):
        return Observation("synthetic", "TARGET", "exec", "P00", retry, revision,
                           start, observed, {"adapterType": "HTTP", "startTime": iso(start),
                                             "endTime": None, "status": "P", "totalCount": 2,
                                             **fields})

    def test_no_future_completion_and_revision_identity(self):
        state = ReplayState(100)
        state.ingest(self.event(endTime=iso(110), status="S"))
        self.assertTrue(state.process_frame().duration_ms.isna().all())
        self.assertEqual(state.process_frame().status.iloc[0], "P")
        state.advance(110)
        self.assertTrue(state.process_frame().duration_ms.isna().all())
        terminal = self.event(observed=111, revision=1, endTime=iso(110), status="S")
        state.ingest(terminal)
        self.assertEqual(state.process_frame().duration_ms.iloc[0], 10000)

    def test_retry_not_duplicate_and_old_revision_no_overwrite(self):
        state = ReplayState(100)
        initial = self.event()
        self.assertTrue(state.ingest(initial))
        self.assertFalse(state.ingest(initial))
        state.ingest(self.event(observed=102, retry=1))
        self.assertEqual(len(state.arrivals), 2)
        state.ingest(self.event(observed=110, revision=2))
        self.assertFalse(state.ingest(self.event(observed=111, revision=1)))
        self.assertEqual(state.latest[initial.identity].revision, 2)

    def test_conflicting_revision_is_rejected(self):
        state = ReplayState(100)
        state.ingest(self.event())
        with self.assertRaises(ValueError):
            state.ingest(self.event(totalCount=9))

    def test_restart_restores_timer_and_final_result(self):
        state = ReplayState(100, sla_seconds=10)
        event = self.event()
        state.ingest(event)
        state.advance(111)
        state.commit_result(event.revision_key, {"decision": "warning"})
        restored = ReplayState.restore(json.loads(json.dumps(state.snapshot())))
        self.assertEqual(state.pending(), restored.pending())
        self.assertFalse(restored.commit_result(event.revision_key, {"decision": "warning"}))
        with self.assertRaises(ValueError):
            restored.commit_result(event.revision_key, {"decision": "normal"})
        self.assertNotEqual(event.revision_key, restored.window_key(111, 10, "v1"))

    def test_clock_and_labels_rejected(self):
        state = ReplayState(100)
        with self.assertRaises(ValueError):
            state.advance(99)
        with self.assertRaises(ValueError):
            self.event(label=True)
        with self.assertRaises(ValueError):
            self.event(observed=99)

    def test_window_boundary_known_completions_and_controls(self):
        events = normal_history_observations()
        cutoff = max(e.observed_at for e in events) + 1
        history = FrozenHistory.fit(events, cutoff, widths=(60, 300))
        clock = cutoff + 1000
        state = ReplayState(clock - 300)
        state.ingest(self.event(start=clock - 60, observed=clock - 57,
                                endTime=iso(clock - 58), status="S"))
        second = self.event(start=clock - 40, observed=clock - 38, revision=1,
                            endTime=iso(clock - 39), status="S")
        from dataclasses import replace
        state.ingest(replace(second, execution_id="exec2"))
        frame = state.window_frame(clock, 60, history, {"process": {}, "adapter": {}, "global": 1000})
        self.assertEqual(frame.execution_arrivals.iloc[0], 2)
        self.assertEqual(frame.completed_rows.iloc[0], 2)
        self.assertAlmostEqual(frame.duration_p95_ms.iloc[0], 1950)
        state.set_visibility(clock, False, True)
        frame = state.window_frame(clock + 60, 60, history, {"process": {}, "adapter": {}, "global": 1000})
        self.assertEqual(frame.window_state.iloc[0], "collector_unknown")

    def test_history_cutoff_and_width_fail_closed(self):
        events = normal_history_observations()
        with self.assertRaises(ValueError):
            FrozenHistory.fit(events, events[0].observed_at)
        history = FrozenHistory.fit(events, max(e.observed_at for e in events) + 1, widths=(60,))
        with self.assertRaises(ValueError):
            history.lookup(history.fitted_until + 1, 300)

    def test_late_completion_does_not_rewrite_final_window(self):
        events = normal_history_observations()
        cutoff = max(e.observed_at for e in events) + 1
        history = FrozenHistory.fit(events, cutoff, widths=(60, 300))
        clock = cutoff + 1000
        state = ReplayState(clock - 300)
        initial = self.event(start=clock - 50, observed=clock - 49)
        state.ingest(initial)
        before = state.window_frame(clock, 60, history, {"process": {}, "adapter": {}, "global": 1000})
        key = state.window_key(clock, 60, "v1")
        state.commit_result(key, {"decision": "partial", "completed": 0})
        state.ingest(self.event(start=clock - 50, observed=clock + 1, revision=1,
                                endTime=iso(clock - 10), status="S"))
        after = state.window_frame(clock + 2, 60, history, {"process": {}, "adapter": {}, "global": 1000})
        self.assertEqual(before.completed_rows.iloc[0], 0)
        self.assertEqual(after.completed_rows.iloc[0], 1)
        self.assertEqual(state.results[key], {"decision": "partial", "completed": 0})

    def test_closed_empty_window_not_ready_and_pending_timeout(self):
        events = normal_history_observations()
        cutoff = max(e.observed_at for e in events) + 1
        history = FrozenHistory.fit(events, cutoff, widths=(60, 300))
        clock = cutoff + 1000
        state = ReplayState(clock - 300, sla_seconds=10)
        state.set_visibility(clock - 60, collector_visible=True, business_open=False)
        frame = state.window_frame(clock, 60, history, {"process": {}, "adapter": {}, "global": 1000})
        self.assertEqual(frame.window_state.iloc[0], "business_closed")
        self.assertEqual(frame.execution_arrivals.iloc[0], 0)
        state.ingest(self.event(start=clock, observed=clock + 1))
        self.assertEqual(state.advance(clock + 10), [self.event(start=clock, observed=clock + 1).revision_key])


if __name__ == "__main__":
    unittest.main()
