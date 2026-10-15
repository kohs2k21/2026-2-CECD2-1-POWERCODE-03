"""Independent tests of the research-only finalization/failure fixture."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import unittest

from research.evaluation.detector_dual_path.decisions import DecisionGate, Result, infer_with_deadline


class DecisionTests(unittest.TestCase):
    def test_rule_only_does_not_wait_for_ml(self):
        gate = DecisionGate("rule_only", 10)
        gate.accept("rule", Result("available", False), 1)
        self.assertEqual(gate.finalize(1), Result("available", False))
        with self.assertRaises(ValueError):
            gate.accept("ml", Result("available", True), 1)

    def test_ml_only_preserves_available_hit(self):
        gate = DecisionGate("ml_only", 10)
        gate.accept("ml", Result("available", True), 1)
        self.assertEqual(gate.finalize(1), Result("available", True))

    def test_parallel_waits_for_selected_paths_before_deadline(self):
        gate = DecisionGate("parallel", 10)
        gate.accept("rule", Result("available", False), 1)
        self.assertIsNone(gate.finalize(2))
        gate.accept("ml", Result("available", True), 3)
        self.assertEqual(gate.finalize(3), Result("available", True))

    def test_parallel_missing_model_explicitly_falls_back_to_rule(self):
        gate = DecisionGate("parallel", 10, ml_ready=False)
        self.assertEqual(gate.selected, ("rule",))
        self.assertTrue(gate.fallback_rule_only)
        gate.accept("rule", Result("available", False), 1)
        self.assertEqual(gate.finalize(1), Result("available", False))

    def test_ml_only_unready_is_unavailable_not_normal(self):
        gate = DecisionGate("ml_only", 10, ml_ready=False)
        result = gate.finalize(1)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.hit)
        self.assertEqual(gate.results["ml"].reason, "model_not_ready")

    def test_partial_negative_at_deadline_is_unavailable(self):
        gate = DecisionGate("parallel", 10)
        gate.accept("rule", Result("available", False), 1)
        result = gate.finalize(10)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.hit)
        self.assertEqual(gate.results["ml"].status, "timeout")

    def test_partial_known_hit_is_preserved_with_missing_coverage(self):
        gate = DecisionGate("parallel", 10)
        gate.accept("rule", Result("available", True), 1)
        self.assertEqual(gate.finalize(10), Result("available", True))
        self.assertEqual(gate.results["ml"].status, "timeout")

    def test_late_ml_never_overwrites_final_negative_or_partial(self):
        for mode, first in (("parallel", "rule"), ("ml_only", "ml")):
            with self.subTest(mode=mode):
                gate = DecisionGate(mode, 10)
                if mode == "parallel":
                    gate.accept(first, Result("available", False), 1)
                final = gate.finalize(10)
                self.assertFalse(gate.accept("ml", Result("available", True), 11))
                self.assertEqual(gate.finalize(11), final)

    def test_completed_normal_is_not_rewritten_by_second_result(self):
        gate = DecisionGate("parallel", 10)
        gate.accept("rule", Result("available", False), 1)
        gate.accept("ml", Result("available", False), 2)
        final = gate.finalize(2)
        self.assertFalse(gate.accept("ml", Result("available", True), 3))
        self.assertEqual(gate.finalize(3), final)

    def test_json_restart_preserves_final_and_rule_fallback(self):
        for ready in (True, False):
            with self.subTest(ml_ready=ready):
                gate = DecisionGate("parallel", 10, ml_ready=ready)
                gate.accept("rule", Result("available", False), 1)
                final = gate.finalize(10)
                restored = DecisionGate.restore(json.loads(json.dumps(gate.snapshot())))
                self.assertEqual(restored.finalize(20), final)
                self.assertEqual(restored.selected, gate.selected)
                self.assertEqual(restored.fallback_rule_only, gate.fallback_rule_only)
                self.assertFalse(restored.accept("rule", Result("available", True), 20))

    def test_real_blocking_task_returns_before_release_on_timeout(self):
        started, release = threading.Event(), threading.Event()
        executor = ThreadPoolExecutor(max_workers=1)
        def block():
            started.set()
            release.wait(2)
            return True
        try:
            before = time.perf_counter()
            result = infer_with_deadline(executor, block, .03)
            elapsed = time.perf_counter() - before
            self.assertTrue(started.is_set())
            self.assertFalse(release.is_set())
            self.assertEqual(result.status, "timeout")
            self.assertIsNone(result.hit)
            self.assertLess(elapsed, .5)
        finally:
            release.set()
            executor.shutdown(wait=True)

    def test_worker_exception_is_failed_not_normal(self):
        def fail():
            raise ValueError("synthetic scorer failure")
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = infer_with_deadline(executor, fail, 1)
        self.assertEqual(result, Result("failed", reason="ValueError"))

    def test_worker_timeout_exception_is_failed_not_deadline(self):
        def fail():
            raise TimeoutError("scorer failed before deadline")
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = infer_with_deadline(executor, fail, 1)
        self.assertEqual(result, Result("failed", reason="TimeoutError"))

    def test_finalize_before_observed_result_cannot_use_future(self):
        gate = DecisionGate("rule_only", 10)
        gate.accept("rule", Result("available", True), 8)
        with self.assertRaises(ValueError):
            gate.finalize(7)

    def test_json_restart_preserves_observation_clock_before_finalization(self):
        gate = DecisionGate("parallel", 10)
        gate.accept("rule", Result("available", False), 8)
        restored = DecisionGate.restore(json.loads(json.dumps(gate.snapshot())))
        with self.assertRaises(ValueError):
            restored.finalize(7)
        self.assertIsNone(restored.finalize(8))
        restored.accept("ml", Result("available", True), 9)
        self.assertEqual(restored.finalize(9), Result("available", True))

    def test_unavailable_result_cannot_carry_a_fake_normal_boolean(self):
        for status in ("unavailable", "timeout", "failed"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                Result(status, False)


if __name__ == "__main__":
    unittest.main()
