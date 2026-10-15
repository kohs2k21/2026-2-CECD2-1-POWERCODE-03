"""운영 변수셋으로 학습한 IF의 한 건 추론과 호환성 경계를 확인한다."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import model_adapters


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
