"""Calibration, fallback, causality and novelty-reference invariants."""
import unittest
import numpy as np
import pandas as pd
from model_research_v14.models import CalibratedFamily
from model_research_v13.features import FixedNormalAnchor, DRIFT
from model_research_v13.models import DensityRaw, Settings


class FakeBase:
    experts = {"KNOWN": object()}
    support = []

    def score(self, frame):
        global_score = frame.value.to_numpy(float)
        local = np.where(frame.adapterType.eq("KNOWN"), global_score * .4, global_score)
        m = np.column_stack([global_score, local, (global_score + local) / 2])
        return m, m > 0, frame.adapterType.eq("KNOWN").to_numpy()


def frame(n=300):
    return pd.DataFrame({"case_id": "case", "window_start": pd.date_range("2026-01-01", periods=n, freq="min").astype(str),
                         "adapterType": np.where(np.arange(n)%2, "KNOWN", "NEW"), "processId": "process",
                         "value": np.linspace(-1, 2, n), "phase": np.where(np.arange(n)<10, "startup", "steady"),
                         "traffic_assessed": True, "latency_assessed": True,
                         "log_mean_count_5m": 1., "residual_mean_1m": np.sin(np.arange(n)*.1)})


class ResearchTests(unittest.TestCase):
    def test_unsupported_exact_global_with_different_quantiles(self):
        data = frame(); family = CalibratedFamily(FakeBase())
        raw = family.components(data)[0]
        family.fit_reference(raw, data.adapterType.to_numpy(), data.phase.to_numpy())
        family.set_quantiles([.9, .99, .95])
        m, p, coverage = family.score(data)
        np.testing.assert_array_equal(m[~coverage, 0], m[~coverage, 1])
        np.testing.assert_array_equal(m[~coverage, 0], m[~coverage, 2])
        np.testing.assert_array_equal(p, m > 0)

    def test_labels_and_future_not_used_by_calibrated_prediction(self):
        data = frame(); family = CalibratedFamily(FakeBase(), "phase")
        family.fit_reference(family.components(data)[0], data.adapterType.to_numpy(), data.phase.to_numpy())
        family.set_quantiles([.975]*3)
        altered = data.assign(window_is_anomaly=True, scenario_types="FAKE", normal_execution_arrivals=99999)
        np.testing.assert_array_equal(family.score(data)[0], family.score(altered)[0])
        np.testing.assert_array_equal(family.score(data.iloc[:100])[0], family.score(data)[0][:100])

    def test_fixed_anchor_prefix_causality_and_missing(self):
        train = frame(300); anchor = FixedNormalAnchor.fit(train)
        test = frame(80); test.loc[30:35, "latency_assessed"] = False
        complete = anchor.augment(test)
        prefix = anchor.augment(test.iloc[:40])
        np.testing.assert_allclose(complete[list(DRIFT)].iloc[:40], prefix[list(DRIFT)])
        self.assertTrue(np.isfinite(complete[list(DRIFT)].to_numpy()).all())
        with self.assertRaises(ValueError): anchor.augment(test.drop(index=20))

    def test_lof_novelty_dedup_and_bounded_reference(self):
        rng = np.random.default_rng(2); unique = rng.normal(size=(80, 4)).astype(np.float32)
        model = DensityRaw(Settings("lof", neighbors=20, reference_cap=60)).fit(np.repeat(unique, 3, axis=0), unique[:0])
        self.assertTrue(model.model.novelty)
        self.assertEqual(model.reference_size, 60)
        self.assertTrue(np.isfinite(model.score(rng.normal(size=(15, 4)))).all())


if __name__ == "__main__": unittest.main()
