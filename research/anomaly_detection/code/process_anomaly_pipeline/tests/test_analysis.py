"""Optional analysis-dependency smoke tests; run with requirements-analysis.txt."""
import unittest
from types import SimpleNamespace
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from process_anomaly_pipeline import ProcessAnomalyPipeline
from process_anomaly_pipeline.data import prepare
from process_anomaly_pipeline.analysis import (
    set_style, rule_audit, relation_summary, outcome_summary, numeric_summary,
    experiment_split, plot_population, plot_input_features, plot_training_mix,
    plot_duration_distributions, plot_model_scores, plot_rule_audit, plot_status,
    plot_daily, plot_test_score_timeline, bucket_percent, DURATION_BINS, COUNT_BINS,
    adapter_model_support,
)
from .test_pipeline import sample_data, test_config, row


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        set_style()
        cls.p = prepare(sample_data())
        cls.pipeline = ProcessAnomalyPipeline(test_config()).fit(cls.p)
        cls.result = cls.pipeline.predict(cls.p)
        cls.split = experiment_split(cls.p, cls.pipeline)

    def tearDown(self):
        plt.close("all")

    def test_summaries_keep_denominators_and_unknowns(self):
        audit = rule_audit(self.result).set_index("규칙")
        self.assertEqual(audit.loc["process_count_mismatch", "미평가"], len(self.p))
        self.assertEqual(audit.loc["process_count_mismatch", "평가 가능"], 0)
        self.assertEqual(outcome_summary(self.result).전체_행.sum(), len(self.p))
        self.assertEqual(relation_summary(self.p, self.result)["평가 거래 시도"].sum(), 0)
        self.assertFalse(numeric_summary(self.p).empty)

    def test_every_plot_can_render(self):
        p, r, s, m = self.p, self.result, self.split, self.pipeline
        figures = [plot_population(p), plot_input_features(p), plot_training_mix(p, r, s, m)[0],
                   plot_duration_distributions(p, s, m), plot_model_scores(r, s, m, reference_only=True),
                   plot_model_scores(r, s, m), plot_rule_audit(r), plot_status(r),
                   plot_daily(r, p, s)[0], plot_test_score_timeline(p, r, s, m)]
        for fig in figures:
            fig.canvas.draw()
            self.assertTrue(fig.axes)
            for ax in fig.axes:
                self.assertEqual(ax.get_xscale(), "linear")
                self.assertEqual(ax.get_yscale(), "linear")
                self.assertFalse(ax.images)  # No heatmaps in the redesigned figures.
        plt.close("all")

    def test_unassessed_status_and_date_render_as_unknown_not_zero(self):
        p = prepare([row(), row(transactionId="n", status="N", startTime="2026-01-02", endTime=None)])
        r = self.pipeline.predict(p)
        fig = plot_status(r)
        fig.canvas.draw()
        fig, daily = plot_daily(r, p, experiment_split(self.p, self.pipeline))
        fig.canvas.draw()
        self.assertTrue(daily.loc[daily.day.eq("2026-01-02"), "IF 후보 %"].isna().all())

    def test_bucket_boundaries_use_full_valid_population(self):
        pct, n = bucket_percent([0, .01, .1, 300, -1, np.nan, np.inf], DURATION_BINS)
        self.assertEqual(n, 4)
        np.testing.assert_allclose(pct[[0, 1, 2, -1]], [25, 25, 25, 25])
        self.assertAlmostEqual(pct.sum(), 100)
        pct, n = bucket_percent([0, 1, 9, 10, 1_000_000, np.nan], COUNT_BINS)
        self.assertEqual(n, 5)
        np.testing.assert_allclose(pct[[0, 1, 2, -1]], [20, 40, 20, 20])

    def test_separate_filled_histograms_keep_the_actual_cutoff(self):
        fig = plot_model_scores(self.result, self.split, self.pipeline)
        routes = ["GLOBAL", *self.pipeline.adapter_models]
        self.assertEqual(len(fig.axes), 2 * len(routes))
        for i, route in enumerate(routes):
            left, right = fig.axes[2 * i:2 * i + 2]
            np.testing.assert_allclose(left.get_xlim(), right.get_xlim())
            np.testing.assert_allclose(left.get_ylim(), right.get_ylim())
            for ax in (left, right):
                if len(ax.patches) > 1:  # An empty GLOBAL fallback has only the shaded region.
                    self.assertTrue(any(p.get_fill() and p.get_facecolor()[3] == 1 for p in ax.patches))
                else:
                    self.assertIn("전체 0, 그림 0", ax.get_title())
                np.testing.assert_allclose(ax.lines[0].get_xdata(), self.pipeline.route_thresholds[route])
        fig = plot_duration_distributions(self.p, self.split, self.pipeline)
        for ax in fig.axes:
            self.assertTrue(all(p.get_fill() for p in ax.patches))
            self.assertAlmostEqual(sum(p.get_height() for p in ax.patches), 100)

    def test_support_table_distinguishes_training_from_validation_shortage(self):
        support = adapter_model_support(self.pipeline).set_index("Adapter")
        for name, model in self.pipeline.adapter_models.items():
            self.assertEqual(support.loc[name, "모델"], "생성")
            self.assertEqual(support.loc[name, "학습 후보"], model.support["train_rows"])
        fake = SimpleNamespace(adapter_models={}, training_summary={"skipped_adapters": [
            {"adapter": "FILE.READER", "train_rows": 24_151, "calibration_rows": 45,
             "threshold_rows": 45, "train_days": 14,
             "reasons": ["insufficient_calibration", "insufficient_threshold"]}]})
        support = adapter_model_support(fake).iloc[0]
        self.assertEqual(support["학습 후보"], 24_151)
        self.assertEqual(support["제외 이유"], "정규화 검증 부족, 판정선 검증 부족")

    def test_missing_dates_are_not_drawn_as_zero_day_bars(self):
        p = prepare([row(startTime=None, endTime=None)])
        r = self.pipeline.predict(p)
        fig, daily = plot_daily(r, p, self.split)
        fig.canvas.draw()
        self.assertTrue(daily.empty)
        self.assertFalse(fig.axes[0].patches)

    def test_shared_period_axes_do_not_clip_later_population_peaks(self):
        records = [row(source_period="a", transactionId=f"a-{h}", totalCount=h,
                       startTime=f"2026-01-01T{h:02d}:00:00", endTime=f"2026-01-01T{h:02d}:00:01")
                   for h in range(24)]
        records += [row(source_period="b", transactionId=f"b-{i}", totalCount=1,
                        startTime="2026-01-02T09:00:00", endTime="2026-01-02T09:00:01")
                    for i in range(24)]
        fig = plot_input_features(prepare(records))
        fig.canvas.draw()
        for ax in fig.axes:
            self.assertGreaterEqual(ax.get_ylim()[1], max(p.get_height() for p in ax.patches))
        self.assertGreaterEqual(fig.axes[5].get_ylim()[1], 100)  # Later period: all at 09:00.


if __name__ == "__main__":
    unittest.main()
