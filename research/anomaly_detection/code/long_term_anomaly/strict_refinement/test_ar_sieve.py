"""Normal-only AR fitting and row-isolation contracts for the sieve study."""
import numpy as np
import pytest
from scipy.signal import lfilter
from .ar_sieve import fit_ar, _case


@pytest.mark.parametrize('order',[1,2,4,8])
def test_normal_ar_fitted_coefficients_are_stationary(order):
    z=np.random.default_rng(19).normal(size=2000)
    coeff,e=fit_ar(z,np.ones(len(z),bool),order,.1)
    assert np.max(abs(np.roots(np.r_[1.,-coeff])))<.98
    assert abs(e.mean())<1e-12
    assert abs(e.std()-1)<1e-12


def test_ar2_recovers_known_normal_dynamics():
    rng=np.random.default_rng(21)
    z=lfilter([1.],[1.,-.45,-.2],rng.normal(size=12000))[1000:]
    coeff,_=fit_ar(z,np.ones(len(z),bool),2,0.)
    np.testing.assert_allclose(coeff,[.45,.2],atol=.025)


def test_masked_reference_values_do_not_enter_ar_fit():
    rng=np.random.default_rng(23);z=rng.normal(size=672);mask=np.ones(len(z),bool);mask[50:54]=False
    coeff,e=fit_ar(z,mask,4,.1)
    z[~mask]=1e20
    changed,after=fit_ar(z,mask,4,.1)
    np.testing.assert_array_equal(coeff,changed)
    np.testing.assert_array_equal(e,after)


def test_inadequate_observed_lag_windows_rejected():
    with pytest.raises(ValueError,match='Too few'):
        fit_ar(np.zeros(672),np.zeros(672,bool),4,.1)


def sample():
    rng=np.random.default_rng(25);x=rng.normal(size=(680,3))*.1+np.array([5.,6.,8.])
    o=np.ones(len(x),bool);a=o.copy();draws=[rng.normal(size=10240),rng.normal(size=10240)]
    return x,o,a,draws


def test_query_does_not_use_other_monitored_hours():
    x,o,a,draws=sample();prior=np.array([.1,.1])
    before=_case((x,o,a,2,.1,draws,prior))
    x[672:675]+=3.;x[676:]+=5.
    after=_case((x,o,a,2,.1,draws,prior))
    np.testing.assert_array_equal(before[:,:,3],after[:,:,3])
    assert before.shape==(2,3,8,6)


def test_missing_query_abstains_and_unavailable_duration_is_disabled():
    x,o,a,draws=sample();o[673]=False;a[673]=False;x[673]=np.nan
    a[674]=False;x[674,1:]=np.nan
    f=_case((x,o,a,2,.1,draws,np.array([.1,.1])))
    assert np.isnan(f[:,:,1]).all()
    np.testing.assert_array_equal(f[:,:,2,3:],0.)
    assert np.isfinite(f[:,:,2,:3]).all()
