"""Check figure denominators, shared bins, and unknown-versus-zero handling."""

import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from log_analysis_common import PERIODS, sample_by_period, setup_plotting
from log_analysis_visuals import (
    plot_categories, plot_continuous, plot_period_timeseries, plot_rule_rates, sampling_audit,
)


class VisualAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_plotting()

    def tearDown(self):
        plt.close("all")

    def test_balanced_sample_is_not_original_distribution(self):
        frame = pd.DataFrame({"source_period": [PERIODS[0]] * 100 + [PERIODS[1]] * 20 + [PERIODS[2]] * 5})
        sample = sample_by_period(frame, n=30)
        audit = sampling_audit(frame, sample)
        self.assertEqual(audit["그림 표본 행 수"].tolist(), [10, 10, 5])
        self.assertAlmostEqual(audit["전체 내 비중 (%)"].sum(), 100)
        self.assertAlmostEqual(audit["표본 내 비중 (%)"].sum(), 100)
        self.assertEqual(audit.loc[PERIODS[2], "기간별 추출률 (%)"], 100)
        self.assertNotEqual(audit.loc[PERIODS[0], "전체 내 비중 (%)"], audit.loc[PERIODS[0], "표본 내 비중 (%)"])

    def test_histograms_share_bins_and_use_full_quantiles(self):
        frame = pd.DataFrame({"source_period": [PERIODS[0]] * 5 + [PERIODS[1]] * 5,
                              "duration_ms": [1, 10, 100, 1000, np.nan, 3, 30, 300, 3000, 30000]})
        sample = frame.loc[[0, 1, 2, 3, 5, 6, 7, 8]]
        with patch("log_analysis_visuals.finish") as finished, patch("log_analysis_visuals.note"):
            plot_continuous(frame, sample, "duration_ms")
        axes = finished.call_args.args[0].axes
        bins = [[p.get_x() for p in ax.patches] for ax in axes]
        np.testing.assert_allclose(bins[0], bins[1])
        for ax in axes:
            self.assertAlmostEqual(sum(p.get_height() for p in ax.patches), 100)
        self.assertEqual(axes[1].lines[0].get_xdata()[0], 300)

    def test_rates_exclude_unavailable_rows_and_keep_unknown(self):
        frame = pd.DataFrame({"source_period": [PERIODS[0]] * 3 + [PERIODS[1]],
                              "candidate": [True, False, True, False],
                              "comparable": [True, True, False, False]})
        with patch("log_analysis_visuals.finish") as finished, patch("log_analysis_visuals.note") as notes:
            plot_rule_rates(frame, {"candidate": "후보"}, {"candidate": "comparable"})
        bars = finished.call_args.args[0].axes[0].patches
        self.assertEqual(bars[0].get_width(), 50)
        self.assertTrue(np.isnan(bars[1].get_width()))
        self.assertIn("비교 가능한 로그 없음", notes.call_args.args[0][-1])

    def test_sampled_sequence_population_is_named_explicitly(self):
        frame = pd.DataFrame({"source_period": [PERIODS[0]] * 3, "sequence": ["A → B", "A → B", "C"]})
        with patch("log_analysis_visuals.finish") as finished, patch("log_analysis_visuals.note"):
            plot_categories(frame, ["sequence"], population="시퀀스 생성 표본 거래")
        fig, title = finished.call_args.args
        self.assertIn("표본 거래", title)
        self.assertNotIn("전체 데이터", title)
        self.assertIn("표본 거래", fig.axes[0].get_xlabel())

    def test_short_daily_series_does_not_repeat_date_labels(self):
        frame = pd.DataFrame({"source_period": [PERIODS[2]] * 3,
                              "bin": pd.date_range("2026-09-30", periods=3), "rows": [100, 1, 10]})
        with patch("log_analysis_visuals.finish") as finished, patch("log_analysis_visuals.note"):
            plot_period_timeseries(frame, "rows", "일별 건수")
        fig = finished.call_args.args[0]
        fig.canvas.draw()
        self.assertIsInstance(fig.axes[0].xaxis.get_major_locator(), mdates.DayLocator)
        labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
        self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()
