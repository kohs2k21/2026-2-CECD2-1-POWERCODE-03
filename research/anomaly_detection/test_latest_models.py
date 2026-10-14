"""Synthetic, data-free regression checks of the two trusted v12 models."""
import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))
from inspect_models import demo_inputs
from model_research_v8.inference import load, ProcessResearchMonitor


class LatestModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = json.loads((ROOT / "reports/model_research_v12/handoff/manifest.json").read_text(encoding="utf-8"))
        cls.models = {r["algorithm"]: load(ROOT / "reports/model_research_v12/handoff" / r["file"], r["sha256"])
                      for r in manifest["models"]}

    def test_portable_types_and_three_routes(self):
        for algorithm, model in self.models.items():
            with self.subTest(algorithm=algorithm):
                self.assertIsInstance(model, ProcessResearchMonitor)
                result = model.predict(*demo_inputs())
                for name in ("global", "adapter", "ensemble"):
                    self.assertTrue(np.isfinite(result[name + "_margin"]).all())
                    np.testing.assert_array_equal(result[name + "_anomaly"], result[name + "_margin"] > 0)

    def test_unseen_adapter_exact_global_fallback(self):
        for model in self.models.values():
            result = model.predict(*demo_inputs("DEMO.NEW.ADAPTER"))
            self.assertFalse(result.expert_available.any())
            for name in ("adapter", "ensemble"):
                np.testing.assert_array_equal(result[name + "_margin"], result.global_margin)

    def test_cadence_gate_uses_three_prior_nonempty_minutes(self):
        for model in self.models.values():
            result = model.predict(*demo_inputs())
            self.assertFalse(result.traffic_assessed.iloc[:3].any())
            self.assertTrue(result.traffic_assessed.iloc[3:].all())
            self.assertFalse(result.latency_assessed.iloc[5])

    def test_mixed_adapter_stream_rejected(self):
        native, completions = demo_inputs()
        native.loc[1, "adapterType"] = "DEMO.OTHER"
        for model in self.models.values():
            with self.assertRaisesRegex(ValueError, "One Adapter\\+Process"):
                model.predict(native, completions)

    def test_noncontiguous_minute_grid_rejected(self):
        native, completions = demo_inputs()
        for model in self.models.values():
            with self.assertRaisesRegex(ValueError, "chronological and contiguous"):
                model.predict(native.drop(index=2), completions)

    def test_future_completion_does_not_change_prior_decisions(self):
        native, completions = demo_inputs()
        future = completions.iloc[[-1]].copy()
        future["end_seconds"] += 3600
        future["duration"] = 1234567.0
        augmented = pd.concat([completions, future], ignore_index=True)
        for model in self.models.values():
            pd.testing.assert_frame_equal(model.predict(native, completions), model.predict(native, augmented))


if __name__ == "__main__":
    unittest.main()
