import threading
import time
import unittest
from types import SimpleNamespace

from research.evaluation.detector_dual_path.performance import benchmark_pair, percentiles


class TinyPair:
    row = SimpleNamespace(model=SimpleNamespace(n_jobs=1))
    window = SimpleNamespace(model=SimpleNamespace(n_jobs=1))

    def __init__(self):
        self.thread_ids = set()

    def metadata(self):
        return {"fixture": True}

    def score_rows(self, frame):
        self.thread_ids.add(threading.get_ident())
        time.sleep(.001)

    def score_window(self, frame):
        self.thread_ids.add(threading.get_ident())
        time.sleep(.001)


class PerformanceTests(unittest.TestCase):
    def test_empty_metric_is_unknown(self):
        self.assertIsNone(percentiles([])["p95_ms"])

    def test_three_crossover_repeats_and_actual_workers(self):
        pair = TinyPair()
        result = benchmark_pair(pair, [None] * 4, [None] * 4, repeats=3, warmup=1, input_rate_hz=1000)
        self.assertEqual(len(result["trials"]), 6)
        self.assertEqual([trial["mode"] for trial in result["trials"]],
                         ["realtime", "dual", "dual", "realtime", "realtime", "dual"])
        self.assertGreater(len(pair.thread_ids), 1)
        self.assertGreater(result["summary"]["dual"]["peak_rss_bytes"]["median"], 0)
        self.assertFalse(result["end_to_end_esb_claim"])

    def test_invalid_short_repeat_rejected(self):
        with self.assertRaises(ValueError):
            benchmark_pair(TinyPair(), [None], [None], repeats=1)


if __name__ == "__main__":
    unittest.main()
