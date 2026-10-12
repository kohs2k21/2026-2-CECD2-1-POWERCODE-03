"""Independent geometry, threshold, invariance and plot checks for the experiment."""
import sys
from pathlib import Path
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from minibatch_kmeans_common import (
    KMeansConfig, fit_experiment, score_experiment, threshold_table,
    distance_contributions, histogram, sample_for_plot, cluster_profiles, plot_count_buckets,
    sample_projection_rows,
)


def synthetic(n, seed, offset):
    rng = np.random.default_rng(seed)
    mode = rng.integers(0, 2, n)
    return pd.DataFrame({
        "duration_ms": np.exp(rng.normal(4 + mode * 3, .25, n)),
        "totalCount": np.exp(rng.normal(2 + mode * 2, .15, n)),
        "retryCount": np.zeros(n),
        "adapterType": np.where(mode == 0, "DB.READER", "DB.WRITER"),
        "status": "S",
        "startTime": pd.date_range("2026-01-01", periods=n, freq="h") + pd.Timedelta(days=offset),
    })


class KMeansExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.train = synthetic(350, 7, 0)
        cls.calibration = synthetic(160, 8, 30)
        cls.threshold = synthetic(180, 9, 60)
        cls.test = synthetic(100, 10, 90)
        cls.config = KMeansConfig(selected_k=2, candidates=(2, 3), max_train_rows=250,
                                  silhouette_rows=80, batch_size=64, n_init=2,
                                  min_cluster_threshold_rows=30, score_batch_rows=23)
        cls.bundle = fit_experiment(cls.train, cls.calibration, cls.threshold, cls.config)

    def test_configuration_rejects_invalid_values(self):
        for kwargs in ({"selected_k": 0}, {"selected_k": True}, {"candidates": (2, 2, 6)},
                       {"quantile": 1}, {"min_cluster_threshold_rows": 0}, {"random_state": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                KMeansConfig(**kwargs)

    def test_threshold_is_independent_quantile_not_cluster_size(self):
        table, pooled = threshold_table([0, 0, 0, 1, 1], [1., 2., 3., 10., 20.], 3, .99, 3)
        self.assertAlmostEqual(table.loc[0, "distance_threshold"], np.quantile([1., 2., 3.], .99))
        self.assertEqual(table.loc[0, "threshold_basis"], "cluster_P99")
        self.assertEqual(table.loc[1, "threshold_basis"], "GLOBAL_P99_fallback")
        self.assertEqual(table.loc[2, "threshold_rows"], 0)
        self.assertEqual(table.loc[2, "distance_threshold"], pooled)
        self.assertTrue(pd.isna(table.loc[2, "reference_median_distance"]))

    def test_threshold_rejects_missing_or_infinite_reference(self):
        for labels, distances in (([], []), ([0], [np.nan]), ([0], [np.inf]), ([0], [-1.]), ([0, 1], [1.])):
            with self.subTest(distances=distances), self.assertRaises(ValueError):
                threshold_table(labels, distances, 2)

    def test_training_and_diagnostic_samples_belong_to_correct_partitions(self):
        self.assertEqual(len(self.bundle["train_sample"]), 250)
        self.assertTrue(self.bundle["train_sample"].index.isin(self.train.index).all())
        self.assertEqual(len(self.bundle["diagnostic_sample"]), 80)
        self.assertEqual(int(self.bundle["thresholds"].threshold_rows.sum()), len(self.threshold))

    def test_frozen_thresholds_match_reference_geometry(self):
        table = self.bundle["thresholds"].set_index("cluster")
        labels, distances = self.bundle["threshold_labels"], self.bundle["threshold_distances"]
        for c in range(2):
            values = distances[labels == c]
            if len(values) >= self.config.min_cluster_threshold_rows:
                self.assertAlmostEqual(table.loc[c, "distance_threshold"], float(np.quantile(values, .99)))

    def test_score_is_independent_of_request_batch(self):
        batch = score_experiment(self.bundle, self.test)
        single = pd.concat([score_experiment(self.bundle, self.test.iloc[[i]]) for i in range(len(self.test))])
        pd.testing.assert_frame_equal(batch, single, atol=1e-5, rtol=1e-5)
        reordered = score_experiment(self.bundle, self.test.sample(frac=1, random_state=1)).sort_index()
        pd.testing.assert_frame_equal(batch, reordered, atol=1e-5, rtol=1e-5)

    def test_decision_is_strict_distance_comparison(self):
        scored = score_experiment(self.bundle, self.test)
        np.testing.assert_array_equal(scored.model_candidate, scored.distance > scored.distance_threshold)
        np.testing.assert_array_equal(scored.model_candidate, scored.score_margin > 0)

    def test_explanations_sum_to_squared_distance(self):
        scored = score_experiment(self.bundle, self.test)
        components = distance_contributions(self.bundle, self.test)
        np.testing.assert_allclose(components.sum(axis=1), scored.distance ** 2, rtol=1e-5, atol=1e-5)
        self.assertTrue(components.ge(0).all().all())

    def test_empty_score_is_supported(self):
        self.assertEqual(len(score_experiment(self.bundle, self.test.iloc[:0])), 0)

    def test_plot_sample_does_not_replace_full_cohort(self):
        sampled = sample_for_plot(self.test, 15, 42)
        self.assertEqual(len(sampled), 15)
        self.assertEqual(len(self.test), 100)
        self.assertEqual(sampled.index.nunique(), 15)
        pd.testing.assert_frame_equal(sampled, sample_for_plot(self.test, 15, 42))

    def test_ordinary_histogram_has_linear_axes_and_real_threshold(self):
        fig, ax = plt.subplots()
        histogram(ax, pd.Series([1., 2., 3., 4., 100.]), "Test", "distance", cutoff=3.)
        self.assertEqual(ax.get_xscale(), "linear")
        self.assertEqual(ax.get_yscale(), "linear")
        self.assertEqual(ax.get_ylim()[0], 0)
        self.assertGreater(len(ax.patches), 0)
        self.assertEqual(list(ax.lines[0].get_xdata()), [3., 3.])
        self.assertIn("전수 5행", ax.texts[0].get_text())
        self.assertIn("밖 0행", ax.texts[0].get_text())
        self.assertEqual(sum(patch.get_height() for patch in ax.patches), 5)
        plt.close(fig)

    def test_large_histogram_discloses_zoomed_out_tail_counts(self):
        fig, ax = plt.subplots()
        histogram(ax, np.arange(2000), "Test", "distance")
        self.assertIn("전수 2,000행", ax.texts[0].get_text())
        self.assertIn("밖 20행", ax.texts[0].get_text())
        self.assertEqual(sum(patch.get_height() for patch in ax.patches), 1980)
        plt.close(fig)

    def test_profiles_reconcile_and_keep_empty_cluster_unavailable(self):
        scored = score_experiment(self.bundle, self.test)
        profiles = cluster_profiles(self.test, scored, 3)
        self.assertEqual(int(profiles.rows.sum()), len(self.test))
        self.assertEqual(int(profiles.candidate_rows.sum()), int(scored.model_candidate.sum()))
        self.assertTrue(pd.isna(profiles.loc[2, "candidate_pct"]))

    def test_count_buckets_preserve_rare_huge_values_and_invalid_values(self):
        fig, ax = plt.subplots()
        values = pd.Series([0., 1., 2., 10., 100., 1000., 10000., 10000000., -1., .5, np.nan])
        counts = plot_count_buckets(ax, values)
        self.assertEqual(int(counts.sum()), 10)
        self.assertEqual(counts.loc["1"], 1)
        self.assertEqual(counts.loc["10,000-99,999"], 1)
        self.assertEqual(counts.loc["100,000+"], 1)
        self.assertEqual(counts.loc["음수/소수/비유한값"], 2)
        self.assertEqual(ax.get_xscale(), "linear")
        self.assertEqual(ax.get_yscale(), "linear")
        plt.close(fig)

    def test_candidate_projection_includes_rare_candidates_without_duplicates(self):
        result = score_experiment(self.bundle, self.test)
        result["model_candidate"] = False
        result.loc[[0, 1], "model_candidate"] = True
        selected = sample_projection_rows(self.test, result, maximum=20, color_by="candidate")
        self.assertEqual(len(selected), 20)
        self.assertEqual(selected.index.nunique(), 20)
        self.assertTrue(set([0, 1]).issubset(selected.index))


if __name__ == "__main__":
    unittest.main()
