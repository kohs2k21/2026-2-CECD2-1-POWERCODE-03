"""Direct statistical oracles for R01--R20, separate from generation truth."""
import copy
import unittest

import numpy as np
import pandas as pd
from scipy import stats

from research.evaluation.trend_sr_comparison import features as ft, methods, references
from research.evaluation.trend_sr_comparison.tests.test_independent_features import source


def normal_reference():
    def table(values):
        q = np.quantile(values, [.05, .1, .25, .5, .75, .9, .95])
        return dict(status="ok", values=values, n=len(values), mean=values.mean(), std=values.std(ddof=1),
                    median=q[3], mad=np.median(np.abs(values-q[3])), q05=q[0], q10=q[1],
                    q25=q[2], q75=q[4], q90=q[5], q95=q[6], iqr=q[4]-q[2], range90=q[5]-q[1])
    tables = {name: {"pooled": table(values)} for name, values in {
        "count": np.arange(80., 121.), "duration_ms": np.arange(160., 241., 2.),
        "error_rate": np.linspace(.01, .05, 41)}.items()}
    return {"g": references.NormalReference("g", 1, "B0", tables,
        {name: {0: "pooled"} for name in tables}, {},
        {"status": "ok", "intercept": .1, "beta": .5, "scale": {"std": 2.}})}


def observed(seed, n=240):
    frame = source(n)
    rng = np.random.default_rng(seed)
    frame["count"] = rng.integers(50, 151, n).astype(float)
    frame["total_count"] = frame["count"]
    frame["duration_ms"] = 200 + .8 * (frame["count"] - 100) + rng.normal(0, 25, n)
    frame["error_count"] = rng.binomial(frame["total_count"].to_numpy(int), .03).astype(float)
    return frame


class IndependentMethodFormulae(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.train, cls.test = observed(91501, n=1800), observed(91502)
        cls.training = ft.aggregate(cls.train, start=cls.train.index[0], end=cls.train.index[-1]+pd.Timedelta(minutes=1))

    def test_r01_through_r20_match_independent_formula_at_supported_tick(self):
        width = 30
        for number in range(1, 21):
            method = f"R{number:02}"
            for variant in methods.variants(method):
                frame = self.test.copy(deep=True)
                if method == "R04":
                    # Keep the discrete-count oracle away from one-ULP mean ties.
                    frame.loc[frame.index[-width:], "count"] += 50
                    frame["total_count"] = frame["count"]
                if method == "R10":
                    frame.loc[frame.index[-5:], ["count", "total_count", "error_count"]] = 0.
                    frame.loc[frame.index[-5:], "duration_ms"] = np.nan
                aggregates = ft.aggregate(frame, start=frame.index[0], end=frame.index[-1]+pd.Timedelta(minutes=1))
                ref = methods.fit_method(self.training, methods.Configuration(method, width, baseline="B0", variant=variant),
                                         normal=normal_reference())
                trace = methods.score_method(ref, aggregates)["g"]
                a, fit = aggregates["g"], ref.groups["g"]
                nr = ref.normal["g"]
                residuals = {}
                for name in ("count", "duration_ms"):
                    t = nr.metrics[name]["pooled"]
                    residuals[name] = (a.values[name]-t["mean"])/t["std"]
                expected = {}
                if number <= 9 or number in (19, 20):
                    for family, metric in (("count", "count"), ("latency", "duration_ms")):
                        z = residuals[metric]
                        current = z[-width:]
                        x, s = current.mean(), fit["statistics"][metric]
                        if number == 1: value = abs(x-s["mean"])/s["std"]
                        elif number == 2: value = abs(x-s["median"])/(1.4826*s["mad"])
                        elif number == 3:
                            k = variant["k"]
                            value = max(0., x-s["q75"]-k*s["iqr"], s["q25"]-k*s["iqr"]-x)/s["iqr"]
                        elif number == 4:
                            values = s["values"]; n = len(values)
                            tail = min(((values <= x).sum()+1)/(n+1), ((values >= x).sum()+1)/(n+1))
                            value = -np.log(min(1., 2*tail))
                        elif number == 5:
                            t = nr.metrics[metric]["pooled"]; mean = a.values[metric][-width:].mean()
                            value = max(0., mean/t["q95"]-1, (t["q05"]-mean)/max(t["q05"], 1.))
                        elif number == 6:
                            alpha, state = 1-2**(-1/width), 0.
                            for item in z: state = alpha*item+(1-alpha)*state
                            value = abs(state)
                        elif number == 7:
                            k = variant["k"]
                            value = max(0., *(sum(current[-j:]-k) for j in range(1,width+1)),
                                        *(sum(-current[-j:]-k) for j in range(1,width+1)))
                        elif number == 8: value = abs(np.median(current[:15])-np.median(current[15:]))
                        elif number == 9: value = abs(np.polyfit(np.arange(width), current, 1)[0])*width
                        elif number == 19: value = stats.ks_2samp(current, nr.metrics["_residuals"][metric]).statistic
                        else: value = stats.wasserstein_distance(current, nr.metrics["_residuals"][metric])
                        expected[family] = value
                elif number == 10:
                    expected["count"] = 5 * nr.metrics["count"]["pooled"]["mean"]
                elif number in (11, 12):
                    count, mu = a.values["count"][-width:].sum(), width * nr.metrics["count"]["pooled"]["mean"]
                    alpha = fit.get("alpha", 0.)
                    distribution, args = (stats.poisson, (mu,)) if number == 11 or alpha == 0 else (stats.nbinom, (1/alpha, 1/(1+alpha*mu)))
                    expected["count"] = -np.log(min(1., 2*min(distribution.cdf(count, *args), distribution.sf(count-1, *args))))
                elif number in (13, 14):
                    n, k = a.values["total_count"][-width:].sum(), a.values["error_count"][-width:].sum()
                    expected["error"] = -(stats.binom.logsf(k-1,n,fit["p"]) if number == 13 else
                                          stats.betabinom.logsf(k-1,n,*fit["alpha_beta"]))
                    eligible_train = self.train.iloc[1439:]
                    own_p = (eligible_train["error_count"].sum()+.5)/(eligible_train["total_count"].sum()+1)
                    self.assertAlmostEqual(fit["p"], own_p, places=15)
                elif number in (15, 16):
                    x, y = residuals["count"][-width:], residuals["duration_ms"][-width:]
                    r = stats.pearsonr(x,y).statistic if number == 15 else stats.spearmanr(x,y).statistic
                    s = fit["statistics"]["relation"]
                    expected["relation"] = abs(r-s["median"])/s["range90"]
                elif number == 17:
                    residual = residuals["duration_ms"][-width:]-.1-.5*residuals["count"][-width:]
                    expected["relation"] = np.mean(abs(residual))/2.
                else:
                    vector = np.array([residuals["count"][-width:].mean(), residuals["duration_ms"][-width:].mean(),
                        residuals["count"][-width:].std(ddof=1), residuals["duration_ms"][-width:].std(ddof=1)])
                    diff = vector-fit["mean"]
                    expected["relation"] = diff @ fit["inverse"] @ diff
                with self.subTest(method=method, variant=variant):
                    self.assertEqual(set(trace.values), set(expected))
                    for family, value in expected.items():
                        self.assertTrue(np.isfinite(trace.values[family][-1]), family)
                        self.assertAlmostEqual(trace.values[family][-1], value, delta=1e-9*max(1,abs(value)))

    def test_train_only_and_short_relation_window_support(self):
        with self.assertRaises(ValueError):
            methods.fit_method(self.training, methods.Configuration("R01",30), split="selection")
        for method in ("R15", "R16", "R17", "R18"):
            ref = methods.fit_method(self.training, methods.Configuration(method,5,baseline="B0"), normal=normal_reference())
            trace = methods.score_method(ref,self.training)["g"]
            self.assertTrue(np.isnan(trace.values["relation"]).all(), method)

    def test_bucket_weekend_uses_source_calendar_and_endpoint_interval(self):
        index = pd.DatetimeIndex(["2025-01-03T15:00Z", "2025-01-03T15:01Z"])
        np.testing.assert_array_equal(references.bucket_key(index,"B1"), [23,24])

    def test_fitted_train_scales_correlations_covariance_and_dispersion_have_independent_oracles(self):
        width=30
        a=self.training['g']; nr=normal_reference()['g']
        z={name:(a.values[name]-nr.metrics[name]['pooled']['mean'])/nr.metrics[name]['pooled']['std'] for name in ('count','duration_ms')}
        # Explicit original-source prefix gate1440; windows ending at1439 onward.
        ends=range(1439,len(a.index))
        windows={name:np.array([values[i-width+1:i+1] for i in ends]) for name,values in z.items()}
        def check_statistics(actual,values):
            q=np.quantile(values,[.05,.1,.25,.5,.75,.9,.95]); mid=q[3]
            oracle={'mean':np.mean(values),'std':np.std(values,ddof=1),'median':mid,'mad':np.median(abs(values-mid)),
                    'q05':q[0],'q10':q[1],'q25':q[2],'q75':q[4],'q90':q[5],'q95':q[6],'iqr':q[4]-q[2],'range90':q[5]-q[1]}
            self.assertEqual(actual['n'],len(values))
            for key,value in oracle.items(): self.assertAlmostEqual(actual[key],value,delta=1e-11*max(1,abs(value)),msg=key)
        for method in ('R01','R12','R14','R15','R16','R18'):
            ref=methods.fit_method(self.training,methods.Configuration(method,width,baseline='B0'),normal=normal_reference())
            fit=ref.groups['g']
            for name in windows: check_statistics(fit['statistics'][name],windows[name].mean(axis=1))
            if method in ('R15','R16'):
                f=stats.pearsonr if method=='R15' else stats.spearmanr
                values=np.array([f(x,y).statistic for x,y in zip(windows['count'],windows['duration_ms'])])
                check_statistics(fit['statistics']['relation'],values)
            elif method=='R18':
                v=np.column_stack([windows['count'].mean(axis=1),windows['duration_ms'].mean(axis=1),windows['count'].std(axis=1,ddof=1),windows['duration_ms'].std(axis=1,ddof=1)])
                covariance=np.cov(v,rowvar=False)
                np.testing.assert_allclose(fit['mean'],v.mean(axis=0),atol=1e-12)
                np.testing.assert_allclose(fit['inverse'],np.linalg.inv(covariance+.01*np.diag(np.diag(covariance))),rtol=1e-10)
            elif method=='R12':
                sums=np.array([self.train['count'].to_numpy()[i-width+1:i+1].sum() for i in ends]); mu=width*100.
                self.assertAlmostEqual(fit['alpha'],max(0.,np.mean(((sums-mu)**2-mu)/mu**2)))
            elif method=='R14':
                frame=self.train.iloc[1439:]; total=frame.total_count.to_numpy(); error=frame.error_count.to_numpy()
                p=(error.sum()+.5)/(total.sum()+1); invn=np.mean(1/total)
                rho=min(.999999,max(0.,(np.var(error/total,ddof=1)/(p*(1-p))-invn)/(1-invn)))
                concentration=1/rho-1 if rho>0 else 1e8
                np.testing.assert_allclose(fit['alpha_beta'],[p*concentration,(1-p)*concentration],rtol=1e-12)


if __name__ == "__main__":
    unittest.main()
