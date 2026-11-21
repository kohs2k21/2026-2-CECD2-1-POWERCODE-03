import unittest
import numpy as np
import pandas as pd
from model_research_v14.test_research import FakeBase, frame
from model_research_v15.models import RegularizedCalibration
from model_research_v15.features import augment, TEMPORAL
from model_research_v15.data import split_cases


class ResearchTests(unittest.TestCase):
    def test_whole_case_split_excludes_paired_observed_case(self):
        base = pd.DataFrame({"case_id": [str(i) for i in range(18)],
                             "validation_split": ["search"]*12+["confirmation"]*6})
        calibration, search, confirmation = split_cases({"example": {"observed": base.copy(), "normal": base.copy()}})
        ids = set(calibration.case_id)
        self.assertEqual(len(ids), 6)
        for cohort in ("observed", "normal"):
            self.assertFalse(ids & set(search["example"][cohort].case_id))
            self.assertFalse(ids & set(confirmation["example"][cohort].case_id))

    def test_temporal_prefix_causal_missing_is_not_zero_duration(self):
        data = frame(70)
        data["log_max_duration_1m"] = 5 + np.arange(len(data))*.01
        data.loc[20:25, "latency_assessed"] = False
        data.loc[20:25, "log_max_duration_1m"] = np.nan
        whole, prefix = augment(data), augment(data.iloc[:40])
        np.testing.assert_array_equal(whole[list(TEMPORAL)].iloc[:40], prefix[list(TEMPORAL)])
        self.assertFalse(whole.loc[20:25, "temporal_assessed"].any())
        self.assertTrue(np.isfinite(whole[list(TEMPORAL)]).all().all())
        self.assertGreater(whole.duration_trend_z.iloc[40], 0)

    def test_regularized_calibration_zero_preserves_base_and_fallback(self):
        data = frame(); model = RegularizedCalibration(FakeBase())
        raw = model.components(data)[0]
        model.fit_reference(raw, data.adapterType.to_numpy(), data.phase.to_numpy())
        model.set_point([.95, .99, .975], [0., 0., 0.])
        np.testing.assert_array_equal(model.score(data)[0], FakeBase().score(data)[0])
        model.set_point([.95, .99, .975], [.5, 1., .25])
        m, p, covered = model.score(data)
        np.testing.assert_array_equal(m[~covered, 0], m[~covered, 1])
        np.testing.assert_array_equal(m[~covered, 0], m[~covered, 2])
        np.testing.assert_array_equal(p, m > 0)


if __name__ == "__main__": unittest.main()
