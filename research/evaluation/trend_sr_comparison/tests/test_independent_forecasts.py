"""Real 42-day fixture fits and independent chronology/forecast score checks."""
import copy
import json
import os
import pickle
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison import dataset, features as ft, forecasting, methods


class ForecastContracts(unittest.TestCase):
    def test_rolling_origin_imputation_cannot_read_future_train_values(self):
        index = pd.date_range("2025-01-01", periods=504, freq="h", tz="UTC")
        first = np.arange(504, dtype=float)+100
        first[0] = np.nan
        second = first.copy()
        second[168:] += 10000
        captures = []
        class Connection:
            def send(self, result): self.result = result
            def close(self): pass
        for values in (first, second):
            fits = []
            def fake_fit(y, index, method, variant):
                fits.append(np.asarray(y).copy())
                return {}
            connection = Connection()
            payload = {"index": index, "method": "R22", "variant": {}, "metrics": {"count": values}}
            with patch.object(forecasting, "_fit", side_effect=fake_fit), patch.object(
                    forecasting, "_predict", side_effect=lambda model, index, method: np.zeros(len(index))):
                forecasting._worker(connection, payload)
            self.assertEqual(connection.result["status"], "ok")
            self.assertEqual(len(fits[0]), 168)
            captures.append(fits[0])
        np.testing.assert_array_equal(captures[0], captures[1])

    def test_all_five_real_statistical_forecast_variants_are_supported_and_restore_exactly(self):
        train = dataset.generate(9501, "train", "normal")
        selected = dataset.generate(9501, "selection", "normal")
        start = pd.Timestamp(selected.manifest["evaluation_start"])
        finish = start + pd.Timedelta(days=2)
        frame = selected.frame.loc[selected.frame["bin_start"] < finish].copy()
        train_aggs = ft.aggregate(train.frame, start=pd.Timestamp(train.manifest["evaluation_start"]),
                                  end=pd.Timestamp(train.manifest["evaluation_end"]))
        score_aggs = ft.aggregate(frame, start=start, end=finish)
        records = []
        for method in ("R21", "R22", "R23"):
            for variant in methods.variants(method):
                bundle = forecasting.fit_forecasts(train_aggs, method, variant)
                restored = pickle.loads(pickle.dumps(bundle))
                for group, a in score_aggs.items():
                    with self.subTest(method=method, variant=variant, group=group):
                        original, cloned = bundle[group], restored[group]
                        self.assertEqual(original["status"], "ok", original.get("reason"))
                        for metric in ("count", "duration_ms"):
                            fitted = original["metrics"][metric]
                            self.assertEqual(fitted["status"], "ok", fitted.get("reason"))
                            self.assertGreater(fitted["scale"], 0.)
                            self.assertGreaterEqual(fitted["residual_count"], 30)
                            self.assertEqual(original["fit_scope"], "train_hourly_only")
                            for fold in fitted["rolling_origin"]:
                                self.assertGreaterEqual(fold["train_hours"], 168)
                            with patch.object(forecasting, "_fit", side_effect=AssertionError("score attempted refit")):
                                actual = forecasting.forecast_score(original, a, metric, 60)
                                repeat = forecasting.forecast_score(cloned, a, metric, 60)
                            np.testing.assert_array_equal(actual, repeat)
                            self.assertTrue(np.isfinite(actual).any())
                            origin = pd.Timestamp(original["train_end"])
                            hours = (a.index-pd.Timedelta(minutes=1)).floor("h")
                            future = pd.date_range(origin, hours.max()+pd.Timedelta(hours=1), freq="h", inclusive="left")
                            model = fitted["model"]
                            if method == "R21":
                                expected = np.asarray(model.forecast(len(future)), float)
                            elif method == "R22":
                                expected = model["level"] + model["template"][future.hour.to_numpy()]
                            else:
                                local = future.tz_convert("Asia/Seoul")
                                hour, day = local.hour.to_numpy(), local.dayofweek.to_numpy()
                                exog = np.column_stack([np.sin(2*np.pi*hour/24), np.cos(2*np.pi*hour/24),
                                                        *[(day == i).astype(float) for i in range(1,7)]])
                                expected = model["center"] + model["scale"] * np.asarray(
                                    model["result"].get_forecast(len(future), exog=exog).predicted_mean)
                            offsets = ((hours-origin).total_seconds()/3600).astype(int)
                            expected = np.maximum(expected,0)[offsets]
                            scale = fitted["scale"]
                            if metric == "count": expected, scale = expected/60, scale/60
                            residual = np.abs(a.values[metric]-expected)/scale
                            residual[~a.valid] = np.nan
                            own = pd.Series(residual).rolling(60,min_periods=60 if metric == "count" else 2).mean().to_numpy()
                            np.testing.assert_allclose(actual,own,rtol=1e-13,atol=1e-13,equal_nan=True)
                            records.append({"method": method,"variant":variant,"group":group,"metric":metric,
                                            "supported":int(np.isfinite(actual).sum()),"scale":fitted["scale"],
                                            "residual_count":fitted["residual_count"],"rolling_origin":fitted["rolling_origin"]})
        if os.environ.get("SR_REVIEW_ARTIFACT_ROOT"):
            Path(os.environ["SR_REVIEW_ARTIFACT_ROOT"],"forecast-supported-oracles.json").write_text(
                json.dumps(records,indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
