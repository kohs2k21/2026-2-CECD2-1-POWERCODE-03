"""Threshold covariates must be extracted exclusively from normal references."""
import numpy as np
from .conditional_calibration import reference_covariates
from .test_followup import point, sample


def test_reference_features_ignore_all_monitored_rows(point):
    x,o,a=sample();ref,before=reference_covariates(point,x,o,a)
    changed=x.copy();changed[672:]+=100
    ref2,after=reference_covariates(point,changed,o,a)
    np.testing.assert_array_equal(before,after)
    np.testing.assert_array_equal(ref.center,ref2.center)
    assert before.shape==(22,)


def test_reference_covariates_do_not_require_monitoring_input(point):
    x,o,a=sample()
    _,full=reference_covariates(point,x,o,a)
    _,reference_only=reference_covariates(point,x[:672],o[:672],a[:672])
    np.testing.assert_array_equal(full,reference_only)


def test_reference_features_ignore_monitoring_missingness(point):
    x,o,a=sample();_,before=reference_covariates(point,x,o,a)
    o[672:]=False;a[672:]=False;x[672:]=np.nan
    _,after=reference_covariates(point,x,o,a)
    np.testing.assert_array_equal(before,after)
