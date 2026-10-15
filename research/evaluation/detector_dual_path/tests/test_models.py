import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.detector_dual_path.models import detector_margin, fit_pair, trusted_published_bundle
from model_evaluation.models import ComparisonConfig, Detector
from process_anomaly_pipeline.data import prepare


def rows(role, count=30):
    return prepare([{"transactionId": f"{role}-{i}", "processId": "P", "processHubId": "H",
                     "source_template_id": f"{role}-{i}", "startTime": f"2026-01-01T12:{i:02d}:00+09:00",
                     "endTime": f"2026-01-01T12:{i:02d}:00.{100+i:03d}+09:00", "retryCount": i % 3,
                     "totalCount": i % 8 + 1, "adapterType": "HTTP"} for i in range(count)])


class ModelTests(unittest.TestCase):
    def test_global_margin_equals_existing_core_for_both_algorithms(self):
        partitions = {role: rows(role) for role in ("train", "calibration", "threshold")}
        config = ComparisonConfig(n_estimators=8, max_samples=20, k=3, kmeans_n_init=2, kmeans_max_iter=10)
        for algorithm in ("if", "kmeans"):
            with self.subTest(algorithm=algorithm):
                detector = Detector.fit(algorithm, *(partitions[name] for name in partitions), "row", config)
                expected = detector.score(partitions["threshold"]) - detector.threshold
                np.testing.assert_allclose(detector_margin(detector, partitions["threshold"]), expected, atol=1e-12)

    def test_pickle_trust_and_inventory_gate_before_loading(self):
        with patch("research.evaluation.detector_dual_path.models.joblib.load") as load:
            with self.assertRaises(ValueError):
                trusted_published_bundle("reports/model_evaluation_v2/comparison_models.joblib")
            with self.assertRaises(ValueError):
                trusted_published_bundle("../outside.joblib", trusted=True)
            with self.assertRaises(ValueError):
                trusted_published_bundle("reports/model_evaluation_v2/comparison_models.joblib", trusted=True,
                                         expected_sklearn="impossible-version")
            load.assert_not_called()

    def test_references_must_be_disjoint(self):
        frame = rows("shared")
        refs = {role: frame for role in ("train", "calibration", "threshold")}
        with self.assertRaisesRegex(ValueError, "leaked"):
            fit_pair("if", refs, refs, 300)


if __name__ == "__main__":
    unittest.main()
