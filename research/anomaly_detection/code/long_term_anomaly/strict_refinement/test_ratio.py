"""Numerical correctness and monitoring-row isolation for density ratios."""
import numpy as np
import pytest
from scipy.stats import norm
from scipy.special import logsumexp
from .ratio import prepare,ratio_features,mix_score,log_cdf_difference
from .reference_calibration import leave_one_out_features


def normal_reference(n=192):
    z=norm.ppf((np.arange(n)+.5)/n)
    return np.column_stack([z,z[::-1],np.full(n,3.)])


@pytest.mark.parametrize('low,high',[(-2.,1.),(19.,20.),(-20.,-19.),(-np.inf,0.),(0.,np.inf),(-np.inf,np.inf)])
def test_log_cdf_difference_stable_and_correct(low,high):
    expected=(norm.sf(low)-norm.sf(high) if low>0 else norm.cdf(high)-norm.cdf(low))
    actual=float(log_cdf_difference(high,low))
    assert np.isfinite(actual)
    assert np.isclose(actual,np.log(expected),rtol=0,atol=1e-10)


def test_shift_direction_is_retained_not_absolute_distance():
    r=normal_reference();x=np.array([[2.,0.,3.],[-2.,0.,3.]])
    f=ratio_features(r,x,np.ones(2,bool),np.ones(2,bool),.25)
    assert f[0,0]>f[0,1] and f[1,1]>f[1,0]
    np.testing.assert_allclose(f[0,0],f[1,1],rtol=0,atol=1e-10)


def test_rows_do_not_use_earlier_or_later_monitoring_values():
    r=normal_reference();x=np.array([[.1,.2,3.],[.5,1.,3.],[0.,.1,3.]])
    flags=np.ones(3,bool);first=ratio_features(r,x,flags,flags,.25)
    changed=x.copy();changed[[0,2],:2]=100.
    second=ratio_features(r,changed,flags,flags,.25)
    np.testing.assert_array_equal(first[1],second[1])
    alone=ratio_features(r,x[1:2],np.ones(1,bool),np.ones(1,bool),.25)
    np.testing.assert_array_equal(first[1],alone[0])


@pytest.mark.parametrize('mode',['maximum','mixture'])
def test_absent_duration_cannot_change_request_only_score(mode):
    x=np.array([[.1,1.,.5,0.,0.,0.]])
    before=mix_score(x,np.ones(6),np.zeros(1,bool),mode)
    x[:,3:]=100.
    after=mix_score(x,np.ones(6),np.zeros(1,bool),mode)
    np.testing.assert_array_equal(before,after)


def test_all_missing_explicitly_abstains():
    f=ratio_features(normal_reference(),np.full((1,3),np.nan),np.zeros(1,bool),np.zeros(1,bool))
    assert np.isnan(f).all()
    assert np.isnan(mix_score(f,np.ones(6),np.zeros(1,bool))).all()


def test_normal_reference_not_modified():
    rng=np.random.default_rng(1);t=np.arange(96)
    context=np.column_stack([5.+.3*np.sin(t/24*2*np.pi),6.+.2*np.cos(t/24*2*np.pi),np.full(96,8.)])
    context+=rng.normal(0,.05,context.shape);copy=context.copy()
    profile,r,slope,mid=prepare(context,np.ones(96,bool),np.ones(96,bool),.5,.5)
    assert profile.shape==(24,3) and r.shape==(96,3)
    assert slope.shape==(3,) and mid==47.5
    np.testing.assert_array_equal(context,copy)


def test_leave_one_out_density_excludes_queried_normal_sample():
    r=normal_reference(96);flags=np.ones(96,bool);bw=.25
    actual=leave_one_out_features(r,flags,flags,bw)
    i=40;z=r[:,0]/max(float(np.std(r[:,0])),.03);other=np.delete(z,i);delta=z[i]-other
    p0=np.mean(norm.pdf(delta/bw)/bw)
    up=np.mean((norm.cdf((delta-.25)/bw)-norm.cdf((delta-4.)/bw))/3.75)
    assert np.isclose(actual[i,0],np.log(up/p0),rtol=0,atol=1e-10)


@pytest.mark.parametrize('bad',[np.zeros(6),np.array([1.,1.,1.,1.,1.,np.nan]),np.array([-1.,1.,1.,1.,1.,1.]),np.ones(5)])
def test_invalid_weights_rejected(bad):
    with pytest.raises(ValueError):mix_score(np.ones((1,6)),bad,np.ones(1,bool))


def test_long_normal_reference_gap_rejected():
    x=np.ones((96,3));flags=np.ones(96,bool);flags[24:32]=False
    with pytest.raises(ValueError,match='Long missing'):prepare(x,flags,flags)
