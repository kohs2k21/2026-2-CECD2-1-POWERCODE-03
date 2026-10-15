"""Execution contracts and forced faults; no training or performance trials."""
import copy
import json
import threading
import time
from types import SimpleNamespace
import unittest

import numpy as np

from research.evaluation.detector_dual_path.process_pair import (
    Jobs, Pair, Prepared, count_rule, make_trace, ml_score, predictions, summary)


ITEM = Prepared((3., 2., 0.), True, True, np.array([[1.]]))


class ProcessPairContracts(unittest.TestCase):
    def test_margin_difference_is_not_hidden_by_same_prediction(self):
        row = {"job_id": 1, "status": "complete", "prediction": False, "feature_sha256": "same",
               "rule": {"status": "complete", "prediction": False},
               "ml": {"status": "complete", "prediction": False, "margin": -1.}}
        changed = copy.deepcopy(row)
        changed["ml"]["margin"] = -2.
        self.assertNotEqual(predictions({"records": [row]}), predictions({"records": [changed]}))

    def pair(self, mode, **kwargs):
        pair = Pair(mode, None, preparer=lambda raw: ITEM, **kwargs)
        self.addCleanup(pair.close)
        return pair

    def test_rule_hit_still_invokes_ml_and_same_outputs(self):
        outcomes = []
        for mode in ("sequential", "concurrent"):
            called = []
            result = self.pair(mode, ml=lambda item: called.append(item) or
                               {"status": "complete", "prediction": False}).execute("immutable", time.perf_counter() + 1)
            self.assertEqual(len(called), 1)
            self.assertTrue(result["rule"]["prediction"])
            self.assertTrue(result["prediction"])
            outcomes.append((result["rule"]["prediction"], result["ml"]["prediction"], result["status"]))
        self.assertEqual(*outcomes)

    def test_direct_sequence_and_actual_concurrent_wait_both(self):
        result = self.pair("sequential", ml=lambda item: {"status": "complete", "prediction": False}).execute("x", time.perf_counter() + 1)
        self.assertLessEqual(result["rule"]["finished"], result["ml"]["started"])
        entered = {name: threading.Event() for name in ("rule", "ml")}
        release = threading.Event()
        def branch(name):
            def run(item):
                entered[name].set()
                if not entered["ml" if name == "rule" else "rule"].wait(1):
                    raise AssertionError("Branches did not overlap")
                release.wait(1)
                return {"status": "complete", "prediction": False}
            return run
        pair = self.pair("concurrent", rule=branch("rule"), ml=branch("ml"))
        output = []
        worker = threading.Thread(target=lambda: output.append(pair.execute("x", time.perf_counter() + 2)))
        worker.start()
        self.assertTrue(entered["rule"].wait(1) and entered["ml"].wait(1))
        self.assertFalse(output)
        release.set()
        worker.join(2)
        self.assertEqual(output[0]["status"], "complete")

    def test_rule_and_ml_errors_partial_not_normal(self):
        def broken(item):
            raise ValueError("forced")
        for rule, ml in ((broken, lambda item: {"status": "complete", "prediction": False}),
                         (lambda item: {"status": "complete", "prediction": False}, broken)):
            result = self.pair("sequential", rule=rule, ml=ml).execute("x", time.perf_counter() + 1)
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["prediction"])
        self.assertEqual(ml_score(ITEM, None)["status"], "model_missing")

    def test_missing_completion_and_nonfinite_margin_unavailable(self):
        incomplete = Prepared((2., float("nan"), float("nan")), False, False, None)
        self.assertIsNone(count_rule(incomplete)["prediction"])
        detector = SimpleNamespace(algorithm="if", threshold=1., normalizer=SimpleNamespace(transform=lambda x: x),
                                   model=SimpleNamespace(score_samples=lambda x: np.array([np.nan])))
        self.assertEqual(ml_score(incomplete, detector)["status"], "unavailable_missing_completion")
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            ml_score(ITEM, detector)

    def test_timeout_and_late_result_never_revises_final(self):
        release, done = threading.Event(), threading.Event()
        def late(item):
            release.wait(1)
            done.set()
            return {"status": "complete", "prediction": True}
        result = self.pair("concurrent", ml=late).execute("x", time.perf_counter() + .02)
        before = copy.deepcopy(result)
        self.assertEqual(result["ml"]["status"], "timeout")
        release.set()
        self.assertTrue(done.wait(1))
        self.assertEqual(result, before)

    def test_bounded_queue_timeout_and_all_scheduled_denominator(self):
        jobs, release, entered = Jobs(queue_limit=1), threading.Event(), threading.Event()
        def blocking():
            entered.set()
            release.wait(1)
            return {"status": "complete", "prediction": False}
        now = time.perf_counter()
        jobs.submit(0, now, blocking, now + 1)
        self.assertTrue(entered.wait(1))
        jobs.submit(1, now, blocking, now + 1)
        jobs.submit(2, now, blocking, now + 1)
        records = jobs.close(time.perf_counter() + .01)
        sealed = copy.deepcopy(records)
        release.set()
        self.assertEqual(records, sealed)
        self.assertEqual({r["status"] for r in records}, {"timeout_running", "pending_at_deadline", "drop_queue_overflow"})
        self.assertEqual(summary(records, 1)["all_scheduled_failed_or_over_3s_fraction"], 1.)

    def test_input_end_and_over_3s_retained(self):
        jobs = Jobs()
        now = time.perf_counter()
        jobs.submit(0, now - 4, lambda: {}, now - 1)
        records = jobs.close(now)
        records.append({"status": "complete", "local_latency_seconds": 4., "producer_lateness_seconds": 0.,
                        "queue_wait_seconds": 0., "compute_seconds": 0.})
        self.assertEqual(summary(records, 1)["all_scheduled_failed_or_over_3s_fraction"], 1.)

    def test_trace_repeatable_immutable_and_relation_injection(self):
        a, raw = make_trace(451, 10, 60, 60)
        b, other = make_trace(451, 10, 60, 60)
        self.assertEqual(raw, other)
        with self.assertRaises(ValueError):
            a.arrivals[0] = 0
        measured = a.arrivals >= a.start + 960
        self.assertEqual(int(np.sum(a.contract_invalid[measured])), 30)
        raw_row = json.loads(raw[-1])
        self.assertIn("transactionId", raw_row)
        self.assertTrue(np.array_equal(a.arrivals, b.arrivals))


if __name__ == "__main__":
    unittest.main()
