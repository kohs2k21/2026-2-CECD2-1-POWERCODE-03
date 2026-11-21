"""Contracts, two-sided monitoring isolation, missingness, and replay fidelity."""
from unittest.mock import patch
import io
import joblib
import numpy as np
import pandas as pd
import pytest
from ..benchmark import CHANNELS
from ..intervals.model import checked_hours
from .model import StrictIntervalBank,Distribution,HORIZONS,FEATURE_NAMES,score_frame,summaries
from .moments import features_from_reference


def sample(monitor=48,seed=1):
    rng=np.random.default_rng(seed);t=np.arange(96+monitor)
    x=np.column_stack([4+.2*np.sin(2*np.pi*t/24),6+.2*np.cos(2*np.pi*t/24),np.full(len(t),8.)])
    x+=rng.normal(0,.05,x.shape)
    f=pd.DataFrame(x,columns=CHANNELS)
    f['timestamp']=pd.date_range('2030-01-01',periods=len(f),freq='h');f['case_id']='one_group'
    f['collection_observed']=True;f['latency_available']=True
    return f


@pytest.fixture(scope='module')
def bank():
    w=np.asarray([0. if '_lower:' in name and name.startswith('std') else 1. for name in FEATURE_NAMES])
    settings={h:dict(weights=w.tolist(),density='knn',quantile=.95,threshold=2.) for h in HORIZONS}
    return StrictIntervalBank(settings,context_hours=96)


@pytest.mark.parametrize('h',HORIZONS)
def test_exact_selected_batch_and_availability(bank,h):
    f=sample(h);ref=bank.prepare_reference(f.iloc[:96]);out=bank.predict_batch(ref,f.iloc[96:],h)
    assert len(out)==h and out.analysis_hours.eq(h).all()
    assert out.assessable.all() and not out.monitoring_state_carried.any()
    assert out.available_at.eq(f.timestamp.iloc[-1]+pd.Timedelta(hours=1)).all()
    assert out.score.nunique()==1 and out.score_scope.eq('selected_batch').all()


@pytest.mark.parametrize('h',HORIZONS)
def test_neither_past_nor_future_monitoring_batches_enter_current_score(bank,h):
    f=sample();x=f.iloc[96:][list(CHANNELS)].to_numpy(float);o=np.ones(48,bool)
    ref=bank.prepare_reference(f.iloc[:96])
    first,_=bank.score_arrays(ref,x,o,o,h)
    changed=x.copy();changed[:24]+=5.;changed[24+h:]+=10.
    second,_=bank.score_arrays(ref,changed,o,o,h)
    np.testing.assert_array_equal(first[24:24+h],second[24:24+h])
    solo=bank.predict_batch(ref,f.iloc[120:120+h],h)
    np.testing.assert_array_equal(first[24:24+h],solo.score)


@pytest.mark.parametrize('h',HORIZONS)
def test_array_and_full_dataframe_replay_identical(bank,h):
    f=sample();ref=bank.prepare_reference(f.iloc[:96]);o=np.ones(48,bool)
    a,r=bank.score_arrays(ref,f.iloc[96:][list(CHANNELS)].to_numpy(float),o,o,h)
    b=score_frame(bank,f,h,True)
    np.testing.assert_array_equal(a,b.score);np.testing.assert_array_equal(r,b.reason)


@pytest.mark.parametrize('h',HORIZONS)
def test_all_missing_batch_explicitly_abstains(bank,h):
    f=sample(h);f.loc[96:,list(CHANNELS)]=np.nan
    f.loc[96:,['collection_observed','latency_available']]=False
    out=score_frame(bank,f,h)
    assert not out.assessable.any() and not out.is_anomaly.any() and out.score.isna().all()


@pytest.mark.parametrize('h',HORIZONS)
def test_zero_arrivals_do_not_invent_duration_alarm(bank,h):
    f=sample(h);f.loc[96:,'log_requests']=0.
    f.loc[96:,['log_duration_ms','log_items_per_process']]=np.nan;f.loc[96:,'latency_available']=False
    out=score_frame(bank,f,h)
    assert out.assessable.all()
    assert not out.loc[out.score>0,'reason'].str.endswith(':duration').any()


@pytest.mark.parametrize('density',['knn',.1,.25,.5])
def test_prepared_distribution_matches_independent_experimental_formula(density):
    rng=np.random.default_rng(11)
    ref=rng.normal(size=(96,3,2));x=rng.normal(size=(7,3,2))
    expected=features_from_reference(ref,x,.95,density)
    actual=np.column_stack([Distribution.fit(ref[:,s,j],.95,density).transform_many(x[:,s,j])
                            for s in range(3) for j in range(2)])
    np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-11)


def test_missing_observed_hours_are_not_counted_as_normal(bank):
    f=sample(24);f.loc[104:111,list(CHANNELS)]=np.nan
    f.loc[104:111,['collection_observed','latency_available']]=False
    out=score_frame(bank,f,24)
    assert out.assessable.sum()==16 and out.score.iloc[8:16].isna().all()


def test_normal_reference_never_updated_and_inference_no_file_io(bank):
    f=sample(24);ref=bank.prepare_reference(f.iloc[:96]);before=ref.next_day_forecast.copy()
    distributions=[d.values.copy() for v in ref.distributions.values() for d in v]
    f.loc[96:,'log_duration_ms']+=4.
    with patch('builtins.open',side_effect=AssertionError('Inference opened a file')):
        bank.predict_batch(ref,f.iloc[96:],24)
    np.testing.assert_array_equal(before,ref.next_day_forecast)
    for a,d in zip(distributions,(d for v in ref.distributions.values() for d in v)):
        np.testing.assert_array_equal(a,d.values)


def test_joblib_round_trip(bank):
    f=sample(4);buf=io.BytesIO();joblib.dump(bank,buf);buf.seek(0);restored=joblib.load(buf)
    np.testing.assert_array_equal(score_frame(bank,f,4).score,score_frame(restored,f,4).score)


def test_long_unobserved_normal_reference_gap_is_rejected(bank):
    f=sample(4);f.loc[24:32,list(CHANNELS)]=np.nan
    f.loc[24:32,['collection_observed','latency_available']]=False
    with pytest.raises(ValueError,match='Long missing'):bank.prepare_reference(f.iloc[:96])


@pytest.mark.parametrize('bad',[True,False,0,2,3,48,'4',4.0,None])
def test_unsupported_hours_rejected(bad):
    with pytest.raises(ValueError):checked_hours(bad)


def test_reference_group_time_and_length_guards(bank):
    f=sample(4);ref=bank.prepare_reference(f.iloc[:96]);batch=f.iloc[96:].copy()
    with pytest.raises(ValueError):bank.predict_batch(ref,batch,8)
    batch['case_id']='different'
    with pytest.raises(ValueError,match='groups'):bank.predict_batch(ref,batch,4)
    batch=f.iloc[96:].copy();batch['timestamp']+=pd.Timedelta(hours=336)
    with pytest.raises(ValueError,match='14-day'):bank.predict_batch(ref,batch,4)
    batch=f.iloc[96:].copy();batch['timestamp']-=pd.Timedelta(hours=1)
    with pytest.raises(ValueError,match='follow'):bank.predict_batch(ref,batch,4)


def test_invalid_and_duplicate_input_rejected(bank):
    f=sample(4);f.loc[97,'timestamp']=f.loc[96,'timestamp']
    with pytest.raises(ValueError,match='ordered'):score_frame(bank,f,4)
    f=sample(4);f.loc[97,'collection_observed']=False
    with pytest.raises(ValueError,match='Latency'):score_frame(bank,f,4)


def test_batch_remainder_never_silently_dropped(bank):
    with pytest.raises(ValueError):score_frame(bank,sample(5),4,True)


def test_summary_missingness_and_standard_deviation():
    x=np.asarray([[[1.,np.nan],[3.,np.nan]],[[np.nan,np.nan],[np.nan,np.nan]]])
    s=summaries(x)
    np.testing.assert_array_equal(s[0,:,0],[2.,2.,1.]);assert np.isnan(s[0,:,1]).all() and np.isnan(s[1]).all()


def test_canonical_cli_round_trip_and_no_output_overwrite(bank,tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    f=sample(4);model_path=tmp_path/'model.joblib';input_path=tmp_path/'input.csv';output_path=tmp_path/'output.csv'
    joblib.dump(bank,model_path);f.to_csv(input_path,index=False)
    env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[2])
    command=[sys.executable,'-m','long_term_anomaly.strict_intervals','--model',str(model_path),
             '--input',str(input_path),'--output',str(output_path),'--analysis-hours','4']
    first=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert first.returncode==0,first.stderr
    out=pd.read_csv(output_path)
    np.testing.assert_allclose(out.score,score_frame(bank,f,4).score,rtol=0,atol=1e-10)
    before=output_path.read_bytes();second=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert second.returncode!=0 and output_path.read_bytes()==before
