"""Independent numerical oracles and adverse publication/availability traces."""
import unittest

import numpy as np
import pandas as pd
from scipy import stats

from research.evaluation.trend_sr_comparison import features as ft, methods


def source(n=4500):
    starts = pd.date_range("2025-01-01", periods=n, freq="min", tz="UTC")
    return pd.DataFrame({"bin_start": starts, "group": "g", "count": 100.,
        "duration_ms": 200., "error_count": 1., "total_count": 100.,
        "source_complete": True, "activity_expected": True,
        "category_supported": True, "available_at": starts + pd.Timedelta(minutes=1)},
        index=starts + pd.Timedelta(minutes=1))


class IndependentNumerics(unittest.TestCase):
    def test_ks_and_wasserstein_match_scipy_with_ties_and_missing_values(self):
        rng = np.random.default_rng(91031)
        reference = np.array([-4., -1., -1., 0., 2., 2., 9.])
        values = rng.integers(-5, 9, 37).astype(float)
        values[[4, 5, 17]] = np.nan
        for width in (2, 5, 8):
            actual_ks, actual_w1 = ft.distribution_distances(values, width, reference, minimum=2, batch=3)
            for i in range(len(values)):
                window = values[max(0, i - width + 1):i + 1]
                window = window[np.isfinite(window)]
                if i < width - 1 or len(window) < 2:
                    self.assertTrue(np.isnan(actual_ks[i]))
                    self.assertTrue(np.isnan(actual_w1[i]))
                else:
                    self.assertAlmostEqual(actual_ks[i], stats.ks_2samp(window, reference).statistic, places=13)
                    self.assertAlmostEqual(actual_w1[i], stats.wasserstein_distance(window, reference), places=13)

    def test_spearman_ranks_each_window_with_pairwise_missing_and_ties(self):
        x = np.array([1, 1, 3, 4, np.nan, 2, 2, 5, 6, 7, 7, 8.])
        y = np.array([3, 2, 1, 7, 8, 3, np.nan, 1, 9, 2, 2, 1.])
        out = ft.spearman(x, y, 5, minimum=3, batch=2)
        for i in range(4, len(x)):
            a, b = x[i-4:i+1], y[i-4:i+1]
            good = np.isfinite(a) & np.isfinite(b)
            expected = stats.spearmanr(a[good], b[good]).statistic
            self.assertAlmostEqual(out[i], expected, places=13)

    def test_finite_cusum_is_exact_suffix_max_and_rewarms_after_unknown(self):
        values = np.array([2., -1., 3., 2., np.nan, 4., -2., 3., 2., -1., 5.])
        for width in (1, 3, 5):
            for k in (.25, .5, 1.):
                expected = np.full(len(values), np.nan)
                run = []
                for i, value in enumerate(values):
                    if not np.isfinite(value):
                        run = []
                    else:
                        run.append(value - k)
                        if len(run) >= width:
                            expected[i] = max(0., *(sum(run[-j:]) for j in range(1, width+1)))
                np.testing.assert_allclose(ft.finite_cusum(values, width, k), expected, atol=1e-13, rtol=0, equal_nan=True)

    def test_ewma_uses_once_per_bin_fixed_zero_initial_state_and_unknown_reset(self):
        values = np.array([3., 2., -1., np.nan, 2., 2., 3.])
        for half in (1, 5, 30):
            alpha, state = 1 - 2 ** (-1 / half), 0.
            expected = []
            for value in values:
                if np.isnan(value):
                    state = 0.
                    expected.append(np.nan)
                else:
                    state = alpha * value + (1-alpha) * state
                    expected.append(state)
            np.testing.assert_allclose(ft.ewma(values, half), expected, atol=1e-14, rtol=0, equal_nan=True)

    def test_extreme_discrete_tails_stay_finite_supported_extremes(self):
        for dist, args in ((stats.poisson, (100.,)), (stats.nbinom, (10., .5)),
                           (stats.binom, (10000, .01)), (stats.betabinom, (10000, 1., 100.))):
            value = 10000 if dist in (stats.binom, stats.betabinom) else 1e6
            with self.subTest(distribution=dist.name):
                score = methods._tail(dist, np.array([value, np.nan]), *args, two_sided=False)
                self.assertTrue(np.isfinite(score[0]))
                self.assertGreater(score[0], 10.)
                self.assertTrue(np.isnan(score[1]))


class IndependentPublication(unittest.TestCase):
    def test_truth_and_latent_columns_never_reach_observable_or_legacy_inputs(self):
        clean, poison = source(), source()
        poison["expected_count"] = np.arange(len(poison)) * 1e9
        poison["normal_confirmed"] = False
        poison["event_id"] = "injected-truth"
        poison["incident_end"] = pd.Timestamp("2099-01-01", tz="UTC")
        first, second = ft.aggregate(clean), ft.aggregate(poison)
        pd.testing.assert_frame_equal(first["g"].source, second["g"].source)
        for metric in ft.METRICS:
            np.testing.assert_equal(first["g"].values[metric], second["g"].values[metric])
        pd.testing.assert_frame_equal(methods._legacy_stream(first, "selection"),
                                      methods._legacy_stream(second, "selection"))

    def test_late_historical_bin_is_hidden_even_when_current_bin_arrives_on_time(self):
        base = source()
        altered = base.copy(deep=True)
        hidden = 2100
        arrival = base.index[hidden] + pd.Timedelta(minutes=15)
        base.iloc[hidden, base.columns.get_loc("available_at")] = arrival
        altered.iloc[hidden, altered.columns.get_loc("available_at")] = arrival
        altered.iloc[hidden, altered.columns.get_loc("count")] = 900.
        pubs = []
        for frame in (base, altered):
            aggregate = ft.aggregate(frame, 1, start=base.index[2000], end=base.index[-1])["g"]
            raw = ft.rolling(aggregate.values["count"], 30)
            pubs.append(ft.publish_score(aggregate, raw, 1))
        before = pubs[0]["decision_tick"].lt(arrival)
        np.testing.assert_equal(pubs[0].loc[before, "score"].to_numpy(), pubs[1].loc[before, "score"].to_numpy())
        withheld = pubs[0]["decision_tick"].ge(base.index[hidden] + pd.Timedelta(minutes=2)) & before
        self.assertTrue(pubs[0].loc[withheld, "score"].isna().all())

    def test_unknown_mid_hold_cannot_revive_a_previous_native_value(self):
        frame = source()
        anchor = pd.Timestamp("2025-01-02T10:00Z")
        missing = frame.index == anchor + pd.Timedelta(minutes=3)
        frame.loc[missing, "source_complete"] = False
        aggregate = ft.aggregate(frame, 15, start=anchor, end=anchor+pd.Timedelta(hours=2))["g"]
        result = ft.publish_score(aggregate, np.ones(len(aggregate.index)), 60)
        initial = result.loc[result["decision_tick"].eq(anchor), "score"].iloc[0]
        self.assertEqual(initial, 1.)
        gap = result["decision_tick"].ge(anchor+pd.Timedelta(minutes=5)) & result["decision_tick"].lt(anchor+pd.Timedelta(minutes=17))
        self.assertTrue(result.loc[gap, "score"].isna().all())

    def test_sequential_method_cannot_consume_unreleased_bin_after_common_window_expires(self):
        train = source()
        train["count"] = np.rint(100 + 15 * np.sin(np.arange(len(train))/7))
        train["total_count"] = train["count"]
        train_aggs = ft.aggregate(train, 1, start=train.index[0], end=train.index[-1])
        frames = [train.copy(deep=True), train.copy(deep=True)]
        hidden = 1700
        arrival = frames[0].index[hidden] + pd.Timedelta(minutes=2000)
        for frame in frames:
            frame.iloc[hidden, frame.columns.get_loc("available_at")] = arrival
        frames[1].iloc[hidden, frames[1].columns.get_loc("count")] = 900.
        for method, variant in (("R06", {}), ("R07", {"unbounded": True})):
            ref = methods.fit_method(train_aggs, methods.Configuration(method, 1440, baseline="B0", variant=variant))
            outputs = []
            for frame in frames:
                aggs = ft.aggregate(frame, 1, start=frame.index[1600], end=frame.index[-1])
                trace = methods.score_method(ref, aggs)["g"]
                outputs.append(ft.publish_score(aggs["g"], trace.values["count"], 1))
            before_arrival = outputs[0]["decision_tick"].lt(arrival)
            with self.subTest(method=method):
                np.testing.assert_equal(outputs[0].loc[before_arrival, "score"].to_numpy(),
                                        outputs[1].loc[before_arrival, "score"].to_numpy())

    def test_common_native_ticks_have_same_value_and_duplicate_queries_add_no_information(self):
        frame = source()
        a = ft.aggregate(frame, 15, start=frame.index[2000], end=frame.index[-1])["g"]
        raw = np.arange(len(a.index), dtype=float)
        one = ft.publish_score(a, raw, 1)
        sixty = ft.publish_score(a, raw, 60)
        common = sixty["on_grid"]
        np.testing.assert_equal(one.loc[common, "score"].to_numpy(), sixty.loc[common, "score"].to_numpy())
        observed = one.dropna(subset=["score"])
        for _, rows in observed.groupby("last_observation_end"):
            self.assertEqual(rows["score"].nunique(), 1)


if __name__ == "__main__":
    unittest.main()
