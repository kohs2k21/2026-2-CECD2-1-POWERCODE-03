import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from model_research_v5.features import CONTEXT, ALL_FEATURES, context_features, segment_native
from model_research_v5.models import Family, Settings, Encoder, RawModel
from model_research_v5.channels import TailReference, ChannelFamily, ChannelRaw


def inputs():
    dates = pd.date_range("2026-01-01", periods=18, freq="min")
    values = np.tile([100., 100., 100., 120., 100., 100., 110., 100., 100.], 2)
    frame = pd.DataFrame({"case_id": ["A"]*9+["B"]*9, "adapterType": "DB.READER", "processId": "P",
                          "window_start": dates, "window_end": dates+pd.Timedelta(minutes=1),
                          "execution_arrivals": 1, "log_max_duration_1m": np.log1p(values),
                          "log_p50_duration_5m": np.log1p(values), "log_p95_duration_5m": np.log1p(values),
                          "arrival_dev_1m": 0., "arrival_dev_5m": 0., "arrival_dev_15m": 0., "zero_run": 0.,
                          "missing_duration_1m": 0., "p95_duration_relative": 0., "residual_p95_5m": 0.,
                          "log_mean_count_5m": 1.})
    return frame


class FeatureTests(unittest.TestCase):
    def test_future_changes_cannot_change_past(self):
        original = inputs()
        changed = original.copy()
        changed.loc[5, ["log_max_duration_1m", "log_p50_duration_5m", "log_p95_duration_5m"]] = 50.
        a, b = context_features(original), context_features(changed)
        np.testing.assert_array_equal(a.loc[:4, list(CONTEXT)], b.loc[:4, list(CONTEXT)])

    def test_current_value_not_its_own_reference(self):
        frame = inputs()
        data = context_features(frame)
        expected = np.log1p(120)-np.log1p(100)
        self.assertAlmostEqual(data.max_delay_change.iloc[3], expected)
        self.assertEqual(data.history_support.iloc[0], 0.)
        self.assertEqual(data.max_delay_change.iloc[0], 0.)

    def test_streams_are_isolated(self):
        frame = inputs()
        together = context_features(frame).iloc[9:][list(CONTEXT)].to_numpy()
        separate = context_features(frame.iloc[9:])[list(CONTEXT)].to_numpy()
        np.testing.assert_array_equal(together, separate)

    def test_noncontiguous_grid_rejected(self):
        with self.assertRaises(ValueError): context_features(inputs().drop(index=2))

    def test_no_labels_ids_or_future_episode_length_in_features(self):
        self.assertFalse(set(ALL_FEATURES) & {"case_id", "status", "adapterType", "processId", "window_is_anomaly", "episode_end", "severity"})

    def test_phase_does_not_look_at_total_episode_length(self):
        frame = inputs()
        a = context_features(frame.iloc[:8])
        b = context_features(frame.iloc[:5])
        np.testing.assert_array_equal(a.phase.iloc[:5], b.phase)
        self.assertEqual(a.phase.iloc[5], "steady")

    def test_reset_assignment_uses_completion_time_and_keeps_counts(self):
        frame = inputs().iloc[:9].copy()
        starts = frame.window_start.astype("datetime64[ns]").astype("int64")/1e9
        rows = pd.DataFrame({"case_id": "A", "end_seconds": starts+30})
        native, rich = segment_native(frame, rows, 3)
        self.assertEqual(native.case_id.nunique(), 3)
        self.assertEqual(set(rich.case_id), set(native.case_id))
        np.testing.assert_array_equal(native.execution_arrivals, frame.execution_arrivals)


class ModelTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        self.parts = {name: pd.DataFrame({"x": rng.normal(size=600), "y": rng.normal(size=600),
                                          "adapterType": "A", "phase": np.tile(["startup", "steady"], 300)})
                      for name in ("train", "calibration", "threshold")}
        self.support = {"A": {n: 30 for n in self.parts}}

    def test_phase_calibration_and_exact_new_adapter_fallback(self):
        for algorithm in ("if", "kmeans"):
            family = Family(("x", "y"), Settings(algorithm)).fit(self.parts, self.support)
            family.set_operating_point(.995, .25, True)
            test = self.parts["threshold"].iloc[:20].copy()
            test["adapterType"] = "NEW"
            margin, prediction, coverage = family.score(test)
            np.testing.assert_array_equal(margin[:, 0], margin[:, 1])
            np.testing.assert_array_equal(margin[:, 0], margin[:, 2])
            self.assertFalse(coverage.any())
            np.testing.assert_array_equal(prediction, margin > 0)

    def test_rotation_matrices_orthogonal_and_reproducible(self):
        x = self.parts["train"][["x", "y"]].to_numpy(dtype=np.float32)
        cal = self.parts["calibration"][["x", "y"]].to_numpy(dtype=np.float32)
        a = RawModel(Settings("if", rotations=4)).fit(x, cal)
        b = RawModel(Settings("if", rotations=4)).fit(x, cal)
        self.assertEqual(sum(len(m.estimators_) for m in a.models), 128)
        for r in a.rotations: np.testing.assert_allclose(r.T@r, np.eye(2), atol=1e-6)
        np.testing.assert_array_equal(a.score(cal[:10]), b.score(cal[:10]))

    def test_cluster_shapes_positive_and_finite(self):
        family = Family(("x", "y"), Settings("kmeans", shape_distance=True)).fit(self.parts, self.support)
        raw = family.global_model.raw_model
        self.assertTrue((raw.cluster_scale > 0).all())
        self.assertTrue((raw.cluster_radii > 0).all())
        self.assertTrue(np.isfinite(family.score(self.parts["threshold"])[0]).all())

    def test_novelty_lof_is_not_refit_by_test_batch(self):
        family = Family(("x", "y"), Settings("lof", neighbors=30)).fit(self.parts, self.support)
        test = self.parts["threshold"].iloc[:25]
        a = family.score(test)[0]
        b = np.vstack([family.score(test.iloc[:5])[0], family.score(test.iloc[5:])[0]])
        np.testing.assert_allclose(a, b)

    def test_encoder_cannot_learn_test_distribution(self):
        encoder = Encoder.fit(self.parts["train"], ("x", "y"))
        median = encoder.median.copy()
        test = self.parts["threshold"].copy()
        test["x"] += 1000
        encoder.transform(test)
        np.testing.assert_array_equal(encoder.median, median)

    def test_invalid_settings(self):
        with self.assertRaises(ValueError): Settings("unknown")
        with self.assertRaises(ValueError): Settings("if", rotations=3)

    def test_empirical_tail_ties_conservative_and_monotone(self):
        values = np.array([0.]*50+[1.]*50)
        phases = np.array(["startup"]*100)
        reference = TailReference.fit(values, phases)
        scores = reference.score(np.array([0., 1., 2.]), np.array(["startup"]*3))
        self.assertEqual(scores[0], 0.)
        self.assertTrue(np.all(np.diff(scores) > 0))
        self.assertTrue(np.isfinite(scores).all())

    def test_latency_channel_unknown_is_not_assumed_healthy(self):
        frame = context_features(inputs())
        mask = ChannelRaw.eligible(frame)
        self.assertFalse(mask[0])
        self.assertFalse(mask[1])
        self.assertTrue(mask[2])

    def test_channels_max_is_calibrated_and_new_adapter_fallback_exact(self):
        rng = np.random.default_rng(73)
        parts = {}
        for name in ("train", "calibration", "threshold"):
            frame = pd.DataFrame(rng.normal(size=(600, len(CONTEXT))), columns=CONTEXT)
            frame["history_support"], frame["missing_duration_1m"] = 1., 0.
            frame["adapterType"] = "A"
            frame["phase"] = np.tile(["startup", "steady"], 300)
            parts[name] = frame
        family = ChannelFamily(CONTEXT, Settings("kmeans")).fit(parts, self.support)
        test = parts["threshold"].iloc[:20].copy()
        detector = family.global_model
        evidence, eligible = detector.explain(test)
        np.testing.assert_allclose(detector.raw(test), evidence.max(axis=1))
        self.assertTrue(eligible.all())
        test["adapterType"] = "NEW"
        margins, predictions, covered = family.score(test)
        np.testing.assert_array_equal(margins[:, 0], margins[:, 2])
        np.testing.assert_array_equal(margins[:, 0], margins[:, 1])
        self.assertFalse(covered.any())
        np.testing.assert_array_equal(predictions, margins > 0)
        test["missing_duration_1m"] = 1.
        evidence, eligible = detector.explain(test)
        self.assertFalse(eligible.any())
        np.testing.assert_array_equal(evidence[:, 1], np.zeros(len(test)))

    def test_central_normal_catalog_uses_only_given_templates(self):
        from model_research_v5.run_matched import CentralNormalCatalog
        templates = [{"rows": [{"adapterType": "A", "processId": "P", "totalCount": 1,
                                  "duration_ms": d}]} for d in range(100)]
        catalog = CentralNormalCatalog(templates)
        key = ("A", "P", 1)
        low, high = np.quantile(np.arange(100), [.05, .75])
        rng = np.random.default_rng(1)
        for _ in range(50):
            ti, ri = catalog.pick(key, rng)
            self.assertTrue(low <= templates[ti]["rows"][ri]["duration_ms"] <= high)
        self.assertEqual(len(catalog.templates), 100)


if __name__ == "__main__": unittest.main()
