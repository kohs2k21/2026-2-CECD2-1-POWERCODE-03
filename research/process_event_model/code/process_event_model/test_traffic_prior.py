from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from .calendar_traffic import CalendarTrafficDetector,append_observed_count,fit_count_reference
from .empirical_traffic import EmpiricalTrafficDetector
from .features import normalize
from .native_history import NativeHistory
from .test_features import rows


@pytest.mark.parametrize('kind',['empirical','calendar'])
def test_count_wrappers_preserve_rate_bin_calibration_and_readonly_arrays(kind):
    model=SimpleNamespace(base=object(),settings=None,fit_rows=100,columns=(),model_weight=.15,
        family_weights=np.full(8,.15),cutoffs=np.ones(8))
    parent=SimpleNamespace(model=model,factors={},support={},traffic_factors={'A':2.},traffic_support={},
        rate_factors=np.array([1.,1.1,.8,1.3,1.]),dispersion_factors=np.ones(4))
    wrapped=EmpiricalTrafficDetector(parent,{'alpha':1.,'beta_minutes':1.}) if kind=='empirical' else CalendarTrafficDetector(parent,{})
    f=pd.DataFrame(dict(adapterType=['A','B'],predictive_support_1m=[6.,18.],
        predictive_exposure_1m=[6.,6.],predictive_dispersion_1m=[1.,1.]))
    np.testing.assert_allclose(wrapped.traffic_factor(f),[1.6,1.3])


def test_cached_count_feature_matches_native_causal_count():
    f=normalize(rows(15)).assign(cohort='normal',_uid='A')
    f.loc[1:3,'availableTime']=f.loc[3,'availableTime']
    expected=NativeHistory().transform(f).native_observed_minute_count
    np.testing.assert_array_equal(append_observed_count(f).native_observed_minute_count,expected)
    ns=f.copy();ns['availableTime']=ns.availableTime.astype('datetime64[ns, UTC]')
    np.testing.assert_array_equal(append_observed_count(f).native_observed_minute_count,
        append_observed_count(ns).native_observed_minute_count)


def test_reference_cannot_assume_sparse_original_logs_have_healthy_coverage():
    with pytest.raises(ValueError,match='Known continuous normal coverage'):
        fit_count_reference(pd.DataFrame())
