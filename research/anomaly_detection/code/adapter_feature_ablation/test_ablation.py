"""Reference leakage, causal transforms, cold start and one-estimator checks."""
import unittest

import numpy as np
import pandas as pd

from adapter_feature_ablation.features import ADAPTER, BASE, AdapterReferences
from adapter_feature_ablation.models import SharedDetector


def frame(size=120):
    rng = np.random.default_rng(12)
    result = pd.DataFrame({"case_id": "a", "adapterType": "DB.READER", "processId": "proc1",
        "window_start": pd.date_range("2026-01-01", periods=size, freq="min").astype(str),
        "log_mean_count_5m": np.log1p(10.),
        "log_max_duration_1m": rng.normal(5., .15, size),
        "log_p95_duration_5m": rng.normal(4.8, .1, size),
        "native_execution_arrivals": rng.integers(1, 5, size)})
    for column in BASE:
        if column not in result:
            result[column] = rng.normal(0, 1, size)
    return result


class ReferenceTests(unittest.TestCase):
    def test_no_identifiers_in_detector_columns(self):
        self.assertFalse(set((*BASE, *ADAPTER)) & {"case_id", "adapterType", "processId", "status", "actual_anomaly"})

    def test_unknown_adapter_falls_back(self):
        reference = AdapterReferences.fit(frame())
        new = frame(); new["adapterType"] = "UNSEEN"
        result = reference.transform(new)
        self.assertTrue(result.adapter_reference_level.eq(2).all())
        self.assertTrue(np.isfinite(result[list(ADAPTER)]).all().all())

    def test_unseen_process_reuses_adapter(self):
        reference = AdapterReferences.fit(frame())
        old = reference.transform(frame())
        new = frame(); new["processId"] = "brand-new-process"
        np.testing.assert_array_equal(old[list(ADAPTER)], reference.transform(new)[list(ADAPTER)])

    def test_prefix_invariant_to_future(self):
        original = frame()
        reference = AdapterReferences.fit(original)
        prefix = reference.transform(original.iloc[:60])
        edited = original.copy()
        edited.loc[60:, "log_p95_duration_5m"] = 100.
        extended = reference.transform(edited).iloc[:60]
        np.testing.assert_array_equal(prefix[list(ADAPTER)], extended[list(ADAPTER)])

    def test_reference_frozen_after_test_transform(self):
        import pickle
        reference = AdapterReferences.fit(frame())
        before = pickle.dumps(reference)
        reference.transform(frame())
        self.assertEqual(before, pickle.dumps(reference))

    def test_missing_completion_not_fabricated_zero(self):
        original = frame(); original.loc[1, "log_p95_duration_5m"] = np.nan
        result = AdapterReferences.fit(original).transform(original)
        self.assertTrue(np.isnan(result.adapter_count_p95_z.iloc[1]))

    def test_tail_increases_with_duration(self):
        train = frame(); reference = AdapterReferences.fit(train)
        test = frame(); test["log_p95_duration_5m"] = np.linspace(3, 8, len(test))
        result = reference.transform(test)
        self.assertTrue(np.all(np.diff(result.adapter_count_p95_upper_tail) >= 0))

    def test_real_one_estimator_and_serialization(self):
        import pickle
        original = frame()
        reference = AdapterReferences.fit(original)
        transformed = reference.transform(original)
        for algorithm in ("if", "kmeans", "lof"):
            with self.subTest(algorithm=algorithm):
                model = SharedDetector.fit(algorithm, BASE, transformed, transformed)
                self.assertFalse(hasattr(model, "experts"))
                self.assertEqual(set(model.thresholds), {"0.01", "0.03", "0.05"})
                np.testing.assert_array_equal(model.score(transformed), pickle.loads(pickle.dumps(model)).score(transformed))


if __name__ == "__main__":
    unittest.main()
