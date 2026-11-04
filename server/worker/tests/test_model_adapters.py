"""운영 변수셋으로 학습한 IF의 한 건 추론과 호환성 경계를 확인한다."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_adapters
from methods.registry import IFOREST_DEFAULTS


def _regression_rows(count=700):
    rng = np.random.default_rng(2026)
    rows = []
    for index in range(count):
        rows.append({"features": {"error_count": str(int(rng.integers(0, 4))),
                                  "total_count": str(40 + int(rng.integers(0, 30))),
                                  "retry_count": str(index % 7)}, "missing": []})
    return rows


def _legacy_train(rows, names):
    """파라미터화 이전 train_iforest(origin/dev a64d5e9, model_adapters.py:111-127)의 문자 그대로 복제.

    고정값 128 / min(512, n) / "auto" / random_state 42, 분할 셔플 seed 42, P99. 회귀 동일성의 기준이다.
    """
    values = np.asarray([[float(row["features"][name]) for name in names] for row in rows], dtype=np.float64)
    shuffled = values[np.random.default_rng(42).permutation(len(values))]
    threshold_count = max(100, math.ceil(len(shuffled) * .2))
    fit_rows, threshold_rows = shuffled[:-threshold_count], shuffled[-threshold_count:]
    scaler = RobustScaler().fit(fit_rows)
    fit_values = scaler.transform(fit_rows)
    forest = IsolationForest(n_estimators=128, max_samples=min(512, len(fit_values)), contamination="auto",
                             max_features=1.0, bootstrap=False, random_state=42, n_jobs=2).fit(fit_values)
    boundary = float(np.quantile(-forest.score_samples(scaler.transform(threshold_rows)), .99))
    return forest, scaler, boundary


class ModelAdapterParameterTests(unittest.TestCase):
    NAMES = ["error_count", "total_count", "retry_count"]
    PROBES = [{"error_count": "1", "total_count": "55", "retry_count": "2"},
              {"error_count": "3", "total_count": "69", "retry_count": "6"},
              {"error_count": "100", "total_count": "1000", "retry_count": "0"}]

    def _trees(self, forest):
        return [(est.tree_.feature.copy(), est.tree_.threshold.copy(), est.tree_.children_left.copy()) for est in forest.estimators_]

    def test_default_call_reproduces_legacy_fixed_values_exactly(self):
        rows = _regression_rows()
        legacy_forest, legacy_scaler, legacy_boundary = _legacy_train(rows, self.NAMES)
        with tempfile.TemporaryDirectory() as directory:
            result = model_adapters.train_iforest(rows, self.NAMES, directory)
            explicit = model_adapters.train_iforest(rows, self.NAMES, str(Path(directory) / "explicit"),
                                                    parameters=dict(IFOREST_DEFAULTS))
            self.assertEqual(result["parameters"], IFOREST_DEFAULTS)
            self.assertEqual(result["thresholdSpec"]["value"], str(legacy_boundary))
            self.assertEqual(explicit["thresholdSpec"]["value"], str(legacy_boundary))
            forest = joblib.load(result["modelPath"])["forest"]
            scaler = joblib.load(result["fitPath"])["scaler"]
            self.assertEqual((forest.n_estimators, forest.max_samples, forest.contamination, forest.random_state),
                             (128, 512, "auto", 42))
            np.testing.assert_array_equal(scaler.center_, legacy_scaler.center_)
            np.testing.assert_array_equal(scaler.scale_, legacy_scaler.scale_)
            for (feature, threshold, left), (lf, lt, ll) in zip(self._trees(forest), self._trees(legacy_forest), strict=True):
                np.testing.assert_array_equal(feature, lf)
                np.testing.assert_array_equal(threshold, lt)
                np.testing.assert_array_equal(left, ll)
            reference = {"modelPath": result["modelPath"], "fitPath": result["fitPath"], "featureNames": self.NAMES}
            for probe in self.PROBES:
                vector = [[float(probe[name]) for name in self.NAMES]]
                legacy_score = float(-legacy_forest.score_samples(legacy_scaler.transform(vector))[0])
                self.assertEqual(model_adapters.score_iforest(reference, probe), legacy_score)

    def test_explicit_parameters_change_the_model_and_are_recorded(self):
        rows = _regression_rows()
        with tempfile.TemporaryDirectory() as directory:
            result = model_adapters.train_iforest(rows, self.NAMES, directory,
                                                  parameters={"n_estimators": 64, "max_samples": 256, "random_state": 7})
            self.assertEqual(result["parameters"], {**IFOREST_DEFAULTS, "n_estimators": 64, "max_samples": 256, "random_state": 7})
            forest = joblib.load(result["modelPath"])["forest"]
            self.assertEqual((forest.n_estimators, forest.max_samples, forest.random_state), (64, 256, 7))
            self.assertEqual(len(forest.estimators_), 64)
            with self.assertRaisesRegex(ValueError, "unknown_parameter:bootstrap"):
                model_adapters.train_iforest(rows, self.NAMES, directory, parameters={"bootstrap": True})
            with self.assertRaisesRegex(ValueError, "parameter_const_mismatch:contamination"):
                model_adapters.train_iforest(rows, self.NAMES, directory, parameters={"contamination": 0.05})


class ModelAdapterTests(unittest.TestCase):
    def test_train_prepare_and_score_one_observation(self):
        rows = []
        for index in range(650):
            rows.append({"features": {"error_count": str(index % 3),
                                      "total_count": str(50 + index % 13)}, "missing": []})
        with tempfile.TemporaryDirectory() as directory:
            result = model_adapters.train_iforest(
                rows, ["error_count", "total_count"], directory)
            reference = {"modelPath": result["modelPath"], "fitPath": result["fitPath"],
                         "featureNames": ["error_count", "total_count"]}
            model_adapters.prepare_iforest(reference)
            normal = model_adapters.score_iforest(
                reference, {"error_count": "1", "total_count": "55"})
            outlier = model_adapters.score_iforest(
                reference, {"error_count": "100", "total_count": "1000"})
            self.assertGreater(outlier, normal)
            self.assertTrue(0 < float(result["thresholdSpec"]["value"]) < 1)
            self.assertEqual(result["thresholdSpec"]["direction"], "higher_is_anomaly")
            with self.assertRaisesRegex(ValueError, "feature_schema_mismatch"):
                model_adapters.score_iforest(reference, {"error_count": "1"})
            with self.assertRaisesRegex(ValueError, "model_feature_schema_mismatch"):
                model_adapters.prepare_iforest({**reference, "featureNames": ["total_count", "error_count"]})

    def test_training_rejects_too_few_complete_rows(self):
        rows = [{"features": {"error_count": "1"}, "missing": []}] * 100
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "insufficient_complete_training_rows"):
                model_adapters.train_iforest(rows, ["error_count"], directory)

    def test_training_excludes_known_anomalies(self):
        rows = [{"features": {"error_count": str(index % 5)}, "missing": [],
                 "label": "anomaly" if index < 100 else "normal"} for index in range(650)]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "insufficient_complete_training_rows"):
                model_adapters.train_iforest(rows, ["error_count"], directory)

    def test_recommendation_uses_only_well_observed_allowed_fields(self):
        rows = [{"error_count": "0", "total_count": "10", "BODY": "secret"}] * 20
        with patch.dict("os.environ", {"FEATURE_DESIGN_PROVIDER": "numeric"}):
            definitions = model_adapters.recommend_features(rows)
        self.assertEqual([item["name"] for item in definitions], ["error_count", "total_count"])
        self.assertNotIn("BODY", repr(definitions))


if __name__ == "__main__":
    unittest.main()
