"""Validate development feature isolation and quantile sensitivity helpers."""
import numpy as np
import pytest
from .model import SharedARPoint
from .point_diagnostic import features_for
from .quantile_warp import linear_warp
from .flexible_hypotheses import tables, RANGES, SIGMAS


@pytest.fixture(scope='module')
def point():
    grid=np.linspace(-12,12,257);buckets=np.array([0.,.95])
    curves=np.asarray([np.stack([v,v]) for v in (grid,-grid,abs(grid),grid,-grid,abs(grid))])
    return SharedARPoint(grid,buckets,curves,dict(weights=[1.]*6,threshold=2.,temperature=.1))


def sample():
    rng=np.random.default_rng(77);t=np.arange(680)
    x=np.column_stack([5.+.1*np.sin(2*np.pi*t/24),6.+.1*np.cos(2*np.pi*t/24),np.full(680,8.)])
    x+=rng.normal(0,.05,x.shape)
    return x,np.ones(680,bool),np.ones(680,bool)


def test_single_hour_features_do_not_read_other_monitoring_rows(point):
    x,o,a=sample();before=features_for(point,x,o,a)
    changed=x.copy();changed[672:675]+=5.;changed[676:]+=10.
    after=features_for(point,changed,o,a)
    np.testing.assert_array_equal(before[3],after[3])
    assert not np.array_equal(before[0],after[0])


def test_single_hour_features_match_standalone_query(point):
    x,o,a=sample();batch=features_for(point,x,o,a)
    # Select a row with the same hour-of-day at the first monitoring offset.
    np.testing.assert_array_equal(batch[0],features_for(point,x[:673],o[:673],a[:673])[0])
    assert batch.shape==(8,32)


def test_missing_collection_stays_missing_in_diagnostic_features(point):
    x,o,a=sample();o[674]=False;a[674]=False;x[674]=np.nan
    assert np.isnan(features_for(point,x,o,a)[2]).all()


def test_missing_duration_disabled_without_disabling_request(point):
    x,o,a=sample();a[674]=False;x[674,1:]=np.nan
    row=features_for(point,x,o,a)[2]
    assert np.isfinite(row[[0,2,3,4,8]]).all()
    assert np.isnan(row[[1,5,6,7,9]]).all()


@pytest.mark.parametrize('quantiles',[
    np.array([-2.,0.,2.]),np.array([-2.,-.8,0.,.8,2.])])
def test_warp_identity_and_out_of_range_values_not_clipped(quantiles):
    x=np.linspace(-20,20,101)
    np.testing.assert_allclose(linear_warp(x,quantiles,quantiles),x,atol=1e-13)


def test_warp_monotonicity_with_extrapolation():
    x=np.linspace(-100,100,1001)
    values=linear_warp(x,[-2.,0.,2.],[-3.,-.5,1.])
    assert (np.diff(values)>0).all()
    assert values[0]<-3 and values[-1]>1


def test_warp_duplicate_quantiles_and_flat_reference():
    x=np.array([-2.,0.,3.])
    np.testing.assert_array_equal(linear_warp(x,[0.,0.,0.],[-1.,0.,1.]),x)
    assert np.isfinite(linear_warp(x,[-1.,0.,0.,1.],[-2.,-.5,.5,2.])).all()


def test_fine_alternatives_gaussian_shift_direction_and_finiteness():
    grid=np.linspace(-12,12,1025);p=np.exp(-.5*grid**2)/np.sqrt(2*np.pi)
    density=np.broadcast_to(p,(2,2,len(grid)))
    result=tables(grid,density)
    n=len(RANGES);i=RANGES.index((1.,3.))
    assert result.shape==(2*n+len(SIGMAS)+1,2,2,len(grid))
    assert np.isfinite(result).all()
    positive=np.argmin(abs(grid-2));negative=np.argmin(abs(grid+2))
    assert result[i,0,0,positive]>result[n+i,0,0,positive]
    assert result[n+i,0,0,negative]>result[i,0,0,negative]
