"""Verify real Adapter use, bounded encodings, leakage boundaries and serving."""
import pickle
import unittest

import numpy as np

from adapter_feature_ablation.features import ADAPTER, AdapterReferences
from adapter_feature_ablation.test_ablation import frame
from shared_model_improvement.models import Setting, StableEncoder, StableDetector


class StableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = frame(160)
        cls.input = AdapterReferences.fit(raw).transform(raw)

    def test_only_one_adapter_feature_removed(self):
        for drop, count in ((True, 9), (False, 10)):
            encoder = StableEncoder.fit(self.input, Setting("if", drop_workload=drop))
            added = set(encoder.columns) & set(ADAPTER)
            self.assertEqual(len(added), count)
            if drop: self.assertEqual(set(ADAPTER)-added, {"adapter_workload_z"})

    def test_no_direct_identifiers_or_labels(self):
        encoder = StableEncoder.fit(self.input, Setting("if"))
        self.assertFalse(set(encoder.columns) & {"case_id", "adapterType", "processId", "window_is_anomaly"})

    def test_compact_views_preserve_adapter_block_and_traffic(self):
        from adapter_feature_ablation.features import BASE
        for view in ("full", "balanced", "compact"):
            encoder = StableEncoder.fit(self.input, Setting("if", view=view))
            self.assertEqual(len(set(encoder.columns) & set(ADAPTER)), 9)
            self.assertTrue(set(BASE[:6]).issubset(encoder.columns))

    def test_every_adapter_weight_positive(self):
        encoder = StableEncoder.fit(self.input, Setting("kmeans", adapter_weight=.25))
        for j, column in enumerate(encoder.columns):
            if column in ADAPTER: self.assertGreaterEqual(encoder.weights[j], .25)

    def test_huge_normal_context_cannot_dominate_unboundedly(self):
        for mode in ("asinh", "clip", "quantile"):
            encoder = StableEncoder.fit(self.input, Setting("lof", transform=mode, drop_workload=False))
            enormous = self.input.copy(); enormous["adapter_workload_z"] = 1e20
            transformed = encoder.transform(enormous)
            self.assertTrue(np.isfinite(transformed).all())
            self.assertLessEqual(np.abs(transformed).max(), 8.001)

    def test_transform_does_not_refit(self):
        encoder = StableEncoder.fit(self.input, Setting("if"))
        saved = pickle.dumps(encoder)
        edited = self.input.copy(); edited["adapter_count_p95_z"] = 10000
        encoder.transform(edited)
        self.assertEqual(saved, pickle.dumps(encoder))

    def test_hybrid_preserves_original_base_scaling(self):
        from adapter_feature_ablation.features import BASE
        from model_research_v5.models import Encoder
        hybrid = StableEncoder.fit(self.input, Setting("lof", transform="hybrid"))
        old = Encoder.fit(self.input, BASE)
        np.testing.assert_array_equal(hybrid.transform(self.input)[:, :len(BASE)], old.transform(self.input))

    def test_hybrid_bounds_adapter_features_only(self):
        encoder = StableEncoder.fit(self.input, Setting("lof", transform="hybrid", adapter_weight=.25))
        enormous = self.input.copy()
        for column in ADAPTER: enormous[column] = 1e20
        ids = [j for j, c in enumerate(encoder.columns) if c in ADAPTER]
        self.assertLessEqual(np.abs(encoder.transform(enormous)[:, ids]).max(), 2.001)

    def test_test_labels_and_order_do_not_change_numeric_transform(self):
        encoder = StableEncoder.fit(self.input, Setting("if"))
        changed = self.input.copy(); changed["window_is_anomaly"] = True
        np.testing.assert_array_equal(encoder.transform(self.input), encoder.transform(changed))
        np.testing.assert_array_equal(encoder.transform(self.input)[::-1], encoder.transform(self.input.iloc[::-1]))

    def test_single_estimator_and_serialization(self):
        for algorithm in ("if", "kmeans", "lof"):
            detector = StableDetector(Setting(algorithm, trees=30, reference_cap=150)).fit(self.input)
            detector.threshold = float(np.quantile(detector.score(self.input), .95))
            restored = pickle.loads(pickle.dumps(detector))
            np.testing.assert_array_equal(detector.predict(self.input)[1], restored.predict(self.input)[1])
            self.assertFalse(hasattr(detector, "experts"))
            self.assertEqual(detector.record()["estimator_count"], 1)


if __name__ == "__main__": unittest.main()
