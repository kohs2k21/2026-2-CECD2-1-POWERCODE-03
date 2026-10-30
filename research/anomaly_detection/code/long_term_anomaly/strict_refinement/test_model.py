"""Composite bank isolation, exact old-provider reuse and portable inference."""
from unittest.mock import patch
import io
import joblib
import numpy as np
import pandas as pd
import pytest
from ..benchmark import CHANNELS
from ..strict_intervals.model import StrictIntervalBank,FEATURE_NAMES,HORIZONS,score_frame
from .model import RefinedBank,SharedARPoint,evidence_score
from .local_shared import soft_score


def sample(monitor=48):
    t=np.arange(96+monitor);rng=np.random.default_rng(5)
    x=np.column_stack([4+.2*np.sin(2*np.pi*t/24),6+.2*np.cos(2*np.pi*t/24),np.full(len(t),8.)])
    x+=rng.normal(0,.05,x.shape);f=pd.DataFrame(x,columns=CHANNELS)
    f['timestamp']=pd.date_range('2030-01-01',periods=len(f),freq='h');f['case_id']='one_group'
    f['collection_observed']=True;f['latency_available']=True;return f


@pytest.fixture(scope='module')
def bank():
    weights=[0. if n.startswith('std_lower') else 1. for n in FEATURE_NAMES]
    ordinary=StrictIntervalBank({h:dict(weights=weights,density='knn',quantile=.95,threshold=2.) for h in HORIZONS},96)
    grid=np.linspace(-12,12,257);rho=np.array([0.,.95])
    curves=np.asarray([np.stack([g,g]) for g in (grid,-grid,abs(grid),grid,-grid,abs(grid))])
    point=SharedARPoint(grid,rho,curves,dict(weights=[1.]*6,threshold=2.,temperature=.1))
    return RefinedBank(ordinary,point)


@pytest.mark.parametrize('temperature',[0.,.065,1.,2.])
def test_deployment_score_matches_selection_score(temperature):
    f=np.random.default_rng(1).normal(size=(100,6));w=np.arange(1,7.)
    available=np.ones(100,bool);available[::10]=False
    np.testing.assert_array_equal(evidence_score(f,w,available,temperature),soft_score(f,w,available,temperature))


@pytest.mark.parametrize('h',HORIZONS)
def test_selected_horizon_and_batch_completion_time(bank,h):
    f=sample(h);out=score_frame(bank,f,h)
    assert len(out)==h and out.analysis_hours.eq(h).all() and out.assessable.all()
    assert out.available_at.eq(f.timestamp.iloc[-1]+pd.Timedelta(hours=1)).all()
    assert not out.monitoring_state_carried.any()


@pytest.mark.parametrize('h',HORIZONS)
def test_prior_and_subsequent_monitoring_never_change_current_batch(bank,h):
    f=sample();ref=bank.prepare_reference(f.iloc[:96]);x=f.iloc[96:][list(CHANNELS)].to_numpy(float);mask=np.ones(48,bool)
    before,_=bank.score_arrays(ref,x,mask,mask,h)
    modified=x.copy();modified[:24]+=5.;modified[24+h:]+=5.
    after,_=bank.score_arrays(ref,modified,mask,mask,h)
    np.testing.assert_array_equal(before[24:24+h],after[24:24+h])
    current=bank.predict_batch(ref,f.iloc[120:120+h],h)
    np.testing.assert_array_equal(before[24:24+h],current.score)


@pytest.mark.parametrize('h',HORIZONS[1:])
def test_all_other_length_providers_exactly_unchanged(bank,h):
    f=sample(h);new=score_frame(bank,f,h);old=score_frame(bank.ordinary,f,h)
    np.testing.assert_array_equal(new.score,old.score)
    np.testing.assert_array_equal(new.reason,old.reason)
    np.testing.assert_array_equal(new.threshold,old.threshold)


def test_zero_arrivals_not_a_duration_fault(bank):
    f=sample(1);f.loc[96:,'log_requests']=0.;f.loc[96:,['log_duration_ms','log_items_per_process']]=np.nan
    f.loc[96:,'latency_available']=False;out=score_frame(bank,f,1)
    assert out.assessable.all() and not out.reason.str.endswith(':duration').any()


def test_unobserved_hour_abstains(bank):
    f=sample(1);f.loc[96:,list(CHANNELS)]=np.nan;f.loc[96:,['collection_observed','latency_available']]=False
    out=score_frame(bank,f,1)
    assert not out.assessable.any() and not out.is_anomaly.any() and out.score.isna().all()


def test_reference_immutable_and_inference_no_file_io(bank):
    f=sample(1);ref=bank.prepare_reference(f.iloc[:96]);before=ref.point.profile.copy();center=ref.point.center.copy()
    with patch('builtins.open',side_effect=AssertionError('Inference performed I/O')):
        bank.predict_batch(ref,f.iloc[96:],1)
    np.testing.assert_array_equal(before,ref.point.profile);np.testing.assert_array_equal(center,ref.point.center)


def test_serialized_bank_portable(bank):
    buf=io.BytesIO();joblib.dump(bank,buf);buf.seek(0);restored=joblib.load(buf)
    f=sample(1);np.testing.assert_array_equal(score_frame(bank,f,1).score,score_frame(restored,f,1).score)


def test_group_clock_and_stale_reference_guards(bank):
    f=sample(1);ref=bank.prepare_reference(f.iloc[:96]);bad=f.iloc[96:].copy();bad['case_id']='another'
    with pytest.raises(ValueError,match='groups'):bank.predict_batch(ref,bad,1)
    bad=f.iloc[96:].copy();bad['timestamp']+=pd.Timedelta(hours=336)
    with pytest.raises(ValueError,match='14-day'):bank.predict_batch(ref,bad,1)
    bad=f.iloc[96:].copy();bad['timestamp']-=pd.Timedelta(hours=1)
    with pytest.raises(ValueError,match='follow'):bank.predict_batch(ref,bad,1)


def test_flat_reference_yields_finite_estimates(bank):
    f=sample(1);f.loc[:95,list(CHANNELS)]=[4.,6.,8.]
    out=score_frame(bank,f,1);assert out.assessable.all() and np.isfinite(out.score).all()


def test_cli_round_trip_and_output_preservation(bank,tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    source=tmp_path/'input.csv';target=tmp_path/'output.csv';model=tmp_path/'model.joblib'
    f=sample(1);f.to_csv(source,index=False);joblib.dump(bank,model)
    env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[2])
    cmd=[sys.executable,'-m','long_term_anomaly.strict_refinement','--model',str(model),'--input',str(source),
         '--output',str(target),'--analysis-hours','1']
    result=subprocess.run(cmd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stderr
    np.testing.assert_allclose(pd.read_csv(target).score,score_frame(bank,f,1).score,rtol=0,atol=1e-9)
    before=target.read_bytes();result=subprocess.run(cmd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode!=0 and target.read_bytes()==before
