import numpy as np
import pandas as pd

from .minute_refinement import EventPolicy,traffic_lifetime
from .rate_conditioned import RateTrafficDetector


def test_reference_bins_use_only_past_numeric_exposure_and_dispersion():
    f=pd.DataFrame(dict(predictive_support_1m=[0.,3.,6.,12.,24.],
        predictive_exposure_1m=[6.]*5,predictive_dispersion_1m=[1.,2.,4.,8.,1.]))
    rate,dispersion=RateTrafficDetector.codes(f)
    np.testing.assert_array_equal(rate,[0,1,2,3,4])
    np.testing.assert_array_equal(dispersion,[0,1,2,3,0])
    changed=f.assign(label=1,scenario_types='secret',case_id='unknown',status='F')
    for x,y in zip(RateTrafficDetector.codes(f),RateTrafficDetector.codes(changed)):
        np.testing.assert_array_equal(x,y)


def test_optional_rate_timeout_does_not_change_default_policy():
    score=np.array([1.1,2.,10.]);count=np.array([1.,10.,100.])
    np.testing.assert_array_equal(traffic_lifetime(180.,score,count,EventPolicy()),[180.]*3)
    np.testing.assert_allclose(traffic_lifetime(180.,score,count,EventPolicy(traffic_gap_events=2.)),[120.,12.,1.2])
