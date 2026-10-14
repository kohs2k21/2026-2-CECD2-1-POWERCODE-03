import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from model_improvement.features import ConditionalBaseline, FEATURES, count_bin, build_features
from model_improvement.models import Encoder, Family, Settings
from model_improvement.refine import with_legacy, VIEWS
from model_improvement.inference import ProcessMonitor


class DummyHistory:
    def lookup(self, dates, widths):
        widths = np.asarray(widths)
        return np.column_stack([widths*2, widths*3, np.full(len(dates), 100)])


def baseline():
    rows = pd.DataFrame({"adapterType": ["A"]*40, "processId": ["X"]*40,
                         "totalCount": [1]*40, "duration_ms": np.linspace(90, 110, 40)})
    return ConditionalBaseline.fit(rows)


def inputs():
    timestamps = pd.date_range("2026-01-01", periods=8, freq="min")
    native = pd.DataFrame({"case_id": ["a"]*4+["b"]*4, "adapterType": "A", "processId": "X",
                           "window_start": timestamps, "window_end": timestamps+pd.Timedelta(minutes=1),
                           "execution_arrivals": [2, 0, 0, 2, 0, 2, 0, 0]})
    ends = timestamps.astype("datetime64[ns]").astype("int64").to_numpy()/1e9+30
    rows = pd.DataFrame({"case_id": native.case_id, "adapterType": "A", "processId": "X",
                         "totalCount": 1., "duration": np.arange(8)*10.+100., "end_seconds": ends})
    return native, rows


class FeatureTests(unittest.TestCase):
    def test_count_bins_and_invalid(self):
        self.assertEqual(count_bin([0, 1, 3, 7, 15]).tolist(), [0, 1, 2, 3, 4])
        with self.assertRaises(ValueError): count_bin([-1])
        with self.assertRaises(ValueError): count_bin([np.nan])

    def test_training_condition_and_unknown_fallback(self):
        b = baseline()
        rows = pd.DataFrame({"adapterType": ["A", "NEW"], "processId": ["X", "NEW"],
                             "totalCount": [1, 1], "duration": [100., 1000.]})
        residual, levels = b.transform(rows)
        self.assertEqual(levels.tolist(), [0, 4])
        self.assertLess(abs(residual[0]), .1)
        self.assertGreater(residual[1], residual[0])

    def test_future_completion_cannot_change_past_features(self):
        native, rows = inputs()
        original = build_features(native, rows, DummyHistory(), baseline())
        changed = rows.copy()
        changed.loc[3, "duration"] *= 1000
        changed.loc[3, "totalCount"] = 500
        modified = build_features(native, changed, DummyHistory(), baseline())
        np.testing.assert_allclose(original.loc[:2, list(FEATURES)], modified.loc[:2, list(FEATURES)], equal_nan=True)

    def test_episode_reset_no_cross_case_state(self):
        native, rows = inputs()
        together = build_features(native, rows, DummyHistory(), baseline())
        separate = build_features(native.iloc[4:], rows.iloc[4:], DummyHistory(), baseline())
        np.testing.assert_allclose(together.iloc[4:][list(FEATURES)], separate[list(FEATURES)], equal_nan=True)
        self.assertAlmostEqual(separate.zero_run.iloc[0], np.log(2))

    def test_no_labels_ids_or_status_in_vector(self):
        forbidden = {"status", "window_is_anomaly", "case_id", "processId", "adapterType", "transactionId", "severity", "normal_execution_arrivals"}
        self.assertFalse(forbidden.intersection(FEATURES))

    def test_completion_boundary_exclusive(self):
        native, rows = inputs()
        rows.loc[0, "end_seconds"] += 30  # exactly decision end, not visible yet
        frame = build_features(native, rows, DummyHistory(), baseline())
        self.assertEqual(frame.missing_duration_1m.iloc[0], 1)
        self.assertTrue(pd.isna(frame.residual_max_1m.iloc[0]))

    def test_raw_quantiles_not_average_minute_quantiles(self):
        native, rows = inputs()
        rows.loc[0, "duration"] = 1000
        frame = build_features(native, rows, DummyHistory(), baseline())
        expected = np.quantile(rows.duration.iloc[:3], .95)
        self.assertAlmostEqual(frame.log_p95_duration_5m.iloc[2], np.log1p(expected))

    def test_legacy_view_preserves_raw_rolling_counts(self):
        native, rows = inputs()
        modern = build_features(native, rows, DummyHistory(), baseline())
        refs = {"process": {("A", "X"): 100.}, "adapter": {"A": 100.}, "global": 100.}
        legacy = with_legacy(modern, refs, DummyHistory())
        np.testing.assert_allclose(np.expm1(legacy.log_arrivals), [2, 2, 2, 4, 0, 2, 2, 2])
        np.testing.assert_allclose(legacy.arrival_deviation, modern.arrival_dev_5m)
        self.assertAlmostEqual(legacy.p95_duration_relative.iloc[0], 0.)

    def test_all_candidate_views_ignore_labels(self):
        forbidden = {"status", "window_is_anomaly", "case_id", "processId", "adapterType", "transactionId", "severity"}
        for columns in VIEWS.values():
            self.assertEqual(len(columns), len(set(columns)))
            self.assertFalse(forbidden.intersection(columns))

    def test_backend_requires_contiguous_grid_and_same_group(self):
        native, rows = inputs()
        ProcessMonitor.validate(native, rows)
        wrong = rows.copy()
        wrong.loc[0, "processId"] = "OTHER"
        with self.assertRaises(ValueError): ProcessMonitor.validate(native, wrong)
        missing_minute = native.drop(index=1)
        with self.assertRaises(ValueError): ProcessMonitor.validate(missing_minute, rows)


class ModelTests(unittest.TestCase):
    def test_encoder_training_only_and_floor(self):
        train = pd.DataFrame({"a": [1., 1., 1.], "b": [0., 1., 2.]})
        encoder = Encoder.fit(train, ("a", "b"))
        transformed = encoder.transform(pd.DataFrame({"a": [2.], "b": [3.]}))
        np.testing.assert_allclose(transformed, [[5., 1.25]])
        np.testing.assert_allclose(encoder.median, [1., 1.])

    def test_unknown_adapter_exact_global_fallback(self):
        rng = np.random.default_rng(42)
        partitions = {n: pd.DataFrame({"x": rng.normal(size=600), "y": rng.normal(size=600), "adapterType": "A"})
                      for n in ("train", "calibration", "threshold")}
        support = {"A": {n: 30 for n in partitions}}
        for algo in ("if", "kmeans"):
            family = Family(("x", "y"), Settings(algo, trees=16, k=3, radius_normalization=True)).fit(partitions, support)
            frame = partitions["train"].iloc[:10].copy()
            frame["adapterType"] = "NEW"
            margin, _, coverage = family.score(frame)
            np.testing.assert_allclose(margin[:, 0], margin[:, 1])
            np.testing.assert_allclose(margin[:, 0], margin[:, 2])
            self.assertFalse(coverage.any())

    def test_strict_threshold_and_shared_support(self):
        family = Family(("x",), Settings("if"))
        family.thresholds = {"GLOBAL": np.array([1., 1., 1.])}
        family.global_weight = .5
        margin = family.from_components(np.array([[1., 1.], [1.1, 1.1]]), ["UNKNOWN", "UNKNOWN"])
        self.assertFalse((margin[0] > 0).any())
        self.assertTrue((margin[1] > 0).all())

    def test_settings_validation(self):
        with self.assertRaises(ValueError): Settings("unknown")
        with self.assertRaises(ValueError): Settings("if", quantile=1.)

    def test_cluster_explanation_sums_to_distance_and_is_not_label(self):
        rng = np.random.default_rng(52)
        partitions = {n: pd.DataFrame({"x": rng.normal(size=600), "y": rng.normal(size=600), "adapterType": "A"})
                      for n in ("train", "calibration", "threshold")}
        family = Family(("x", "y"), Settings("kmeans", k=3, radius_normalization=True)).fit(
            partitions, {"A": {n: 30 for n in partitions}})
        detector = family.experts["A"]
        labels, distances, contribution = detector.explain_cluster(partitions["threshold"].iloc[:10])
        np.testing.assert_allclose(contribution.sum(axis=1), 1., atol=1e-6)
        self.assertTrue((detector.radii > 0).all())
        raw = detector.raw(partitions["threshold"].iloc[:10])
        np.testing.assert_allclose(raw, distances/detector.radii[labels])


if __name__ == "__main__": unittest.main()
