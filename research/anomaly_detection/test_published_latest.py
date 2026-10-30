"""No private logs, frozen development caches, or benchmark labels required."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))
from inspect_latest import verified_entries, long_demo
from research_workflow.short import inputs
from research_workflow.test_short import demo


class PublishedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = verified_entries()
        cls.models = {e["algorithm"]: joblib.load(ROOT / e["path"]) for e in cls.entries}

    def test_four_latest_hashes_and_modules_are_bundled(self):
        self.assertEqual(set(self.models), {"if", "kmeans", "lof", "long_term"})
        for name, module in list(sys.modules.items()):
            if name.startswith(("shared_model", "model_research", "model_improvement", "research_workflow")):
                path = getattr(module, "__file__", None)
                if path:
                    self.assertTrue(Path(path).resolve().is_relative_to(ROOT / "code"), (name, path))

    def test_short_known_and_unknown_adapter_and_roundtrip(self):
        for algorithm in ("if", "kmeans", "lof"):
            model = self.models[algorithm]
            for adapter in ("DB.READER", "DEMO.UNSEEN"):
                with self.subTest(algorithm=algorithm, adapter=adapter):
                    n, c = inputs(*demo(minutes=12, adapter=adapter))
                    result = model.predict(n, c)
                    self.assertTrue(np.isfinite(result.score).all())
                    np.testing.assert_array_equal(result.anomaly, result.score > 1.)
                    with tempfile.TemporaryDirectory(prefix="issue80-roundtrip-") as d:
                        p = Path(d) / "roundtrip.joblib"
                        joblib.dump(model, p, compress=3)
                        pd.testing.assert_frame_equal(result, joblib.load(p).predict(n, c))

    def test_short_ignores_future_completion(self):
        n, c = inputs(*demo(minutes=12))
        future = c.iloc[[-1]].copy()
        future["end_seconds"] += 3600
        future["duration"] = 1000000.
        for algorithm in ("if", "kmeans", "lof"):
            m = self.models[algorithm]
            pd.testing.assert_frame_equal(m.predict(n, c), m.predict(n, pd.concat([c, future], ignore_index=True)))

    def test_predictions_do_not_open_archived_data_or_cache(self):
        n, c = inputs(*demo(minutes=12))
        def forbidden(*args, **kwargs):
            raise AssertionError("Prediction attempted a dataset/cache read")
        original_read = Path.read_text
        workspace = ROOT.parents[2]
        def guarded_read(path, *args, **kwargs):
            # sklearn/narwhals may inspect installed-package entry_points.txt;
            # that is library metadata, not an archived research input.
            target = path.resolve()
            if any(target.is_relative_to(p) for p in (ROOT, workspace/'data', workspace/'reports')):
                forbidden()
            return original_read(path, *args, **kwargs)
        with patch('pandas.read_csv', side_effect=forbidden), patch('numpy.load', side_effect=forbidden), \
             patch('joblib.load', side_effect=forbidden), patch('pathlib.Path.read_text', new=guarded_read):
            for algorithm in ('if', 'kmeans', 'lof'):
                self.assertTrue(np.isfinite(self.models[algorithm].predict(n, c).score).all())
            from long_term_anomaly.raw_windows import ProcessWindowService
            long_demo(ProcessWindowService(self.models['long_term']))

    def test_long_raw_process_api_all_horizons(self):
        from long_term_anomaly.raw_windows import ProcessWindowService
        result = long_demo(ProcessWindowService(self.models["long_term"]))
        self.assertEqual(result["horizons"], [1, 4, 8, 12, 24])


if __name__ == "__main__":
    unittest.main()
