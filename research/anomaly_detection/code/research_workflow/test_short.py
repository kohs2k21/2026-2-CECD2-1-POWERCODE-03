"""Entirely invented fixtures; never reads original or benchmark datasets."""
from dataclasses import asdict
import tempfile
from pathlib import Path
import unittest
import joblib
import numpy as np
import pandas as pd
from .short import inputs, train, evaluate, load_trusted


def demo(start="2026-01-01", minutes=60, name="demo", adapter="DEMO.ADAPTER"):
    starts = pd.date_range(start, periods=minutes, freq="min")
    counts = (np.arange(minutes) % 5 != 3).astype(int)
    n = pd.DataFrame({"case_id": name, "adapterType": adapter, "processId": "DEMO.PROCESS",
                      "window_start": starts, "window_end": starts + pd.Timedelta(minutes=1), "execution_arrivals": counts})
    take = counts > 0
    ends = starts[take] + pd.Timedelta(seconds=2)
    c = pd.DataFrame({"case_id": name, "adapterType": adapter, "processId": "DEMO.PROCESS", "totalCount": 1,
                      "duration": 90 + 15 * np.sin(np.arange(take.sum()) / 3), "end_seconds": ends.as_unit("ns").asi8 / 1e9})
    return n, c


class InputAndMetricTests(unittest.TestCase):
    def test_native_quantiles_are_recomputed_not_trusted(self):
        n, c = demo()
        n["duration_p50_ms"] = 999999
        a, _ = inputs(n, c)
        self.assertAlmostEqual(a.duration_p50_ms.iloc[0], 90.)
        self.assertTrue(np.isnan(a.duration_p50_ms.iloc[3]))

    def test_gap_duplicate_and_mixed_stream_rejected(self):
        n, c = demo()
        for bad in (n.drop(index=2), pd.concat([n, n.iloc[[0]]])):
            with self.assertRaises(ValueError):
                inputs(bad, c)
        n.loc[1, "adapterType"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "One Adapter"):
            inputs(n, c)

    def test_fractional_arrivals_rejected(self):
        n, c = demo()
        n["execution_arrivals"] = n.execution_arrivals.astype(float)
        n.loc[0, "execution_arrivals"] = .5
        with self.assertRaisesRegex(ValueError, "integer"):
            inputs(n, c)

    def test_metrics_exact_key_join_and_no_label_adjustment(self):
        n, _ = demo(minutes=4)
        p = n[["case_id", "window_start"]].assign(anomaly=[0, 1, 0, 1])
        labels = n[["case_id", "window_start"]].assign(is_anomaly=[0, 0, 1, 1])
        m = evaluate(p, labels.iloc[::-1])
        for k in ("accuracy", "precision", "recall", "f1", "false_positive_rate"):
            self.assertEqual(m[k], .5)
        self.assertFalse(m["point_adjustment"])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            evaluate(p, labels.iloc[:-1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            evaluate(p, pd.concat([labels, labels.iloc[[0]]]))

    def test_nonbinary_labels_rejected(self):
        n, _ = demo(minutes=4)
        p = n[["case_id", "window_start"]].assign(anomaly=False)
        labels = n[["case_id", "window_start"]].assign(is_anomaly="False")
        with self.assertRaisesRegex(ValueError, "boolean"):
            evaluate(p, labels)


class NewDataFitTests(unittest.TestCase):
    def test_three_algorithms_refit_predict_and_roundtrip_without_research_cache(self):
        from shared_model_v6.models import Setting
        n, c = demo(minutes=75, name="fit")
        v, vc = demo("2026-01-02", minutes=45, name="cal")
        q, qc = demo("2026-01-03", minutes=20, name="query", adapter="UNSEEN")
        for algorithm in ("if", "kmeans", "lof"):
            with self.subTest(algorithm=algorithm):
                setting = asdict(Setting(algorithm, trees=8, samples=32, clusters=3, neighbors=8, cap=128))
                model = train(n, c, v, vc, algorithm=algorithm, setting=setting)
                self.assertTrue(model.provenance["all_feature_references_refit"])
                self.assertFalse(model.provenance["published_v7_metrics_apply"])
                result = model.predict(q, qc)
                self.assertTrue(np.isfinite(result.score).all())
                np.testing.assert_array_equal(result.anomaly, result.score > result.threshold)
                with tempfile.TemporaryDirectory(prefix="issue80-test-") as d:
                    path = Path(d) / "new.joblib"
                    joblib.dump(model, path)
                    pd.testing.assert_frame_equal(result, load_trusted(path).predict(q, qc))

    def test_overlap_and_chronological_leakage_rejected(self):
        from shared_model_v6.models import Setting
        n, c = demo(minutes=60, name="same")
        with self.assertRaisesRegex(ValueError, "disjoint"):
            train(n, c, n, c, algorithm="if", setting=asdict(Setting("if")))
        v, vc = demo(minutes=40, name="other")
        with self.assertRaisesRegex(ValueError, "chronologically"):
            train(n, c, v, vc, algorithm="if", setting=asdict(Setting("if")))


if __name__ == "__main__":
    unittest.main()
