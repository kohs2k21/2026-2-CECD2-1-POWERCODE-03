from unittest.mock import patch
import io
import os
import subprocess
import sys
import joblib
import numpy as np
import pytest
from scipy.special import ndtr
from ..strict_refinement.test_model import sample
from ..strict_refinement.model import SharedARPoint
from ..strict_intervals.model import StrictIntervalBank,FEATURE_NAMES
from .model import AlignedPoint,AlignedBank,HORIZONS,score_frame


@pytest.fixture(scope='module')
def bank():
    grid=np.linspace(-12,12,257);rho=np.array([0.,.95])
    curves=np.asarray([np.stack([v,v]) for v in (grid,-grid,abs(grid),grid,-grid,abs(grid))])
    config=dict(weights=[1.]*6,temperature=.1,threshold=2.,scale_alpha=[0.,.25],
                quantiles=[.01,.05,.25,.5,.75,.95,.99],alignment_strength=[0.,.25])
    point=AlignedPoint(grid,rho,curves,config,np.broadcast_to(ndtr(grid),(2,2,len(grid))),[.05,.05])
    weights=[0. if n.startswith('std_lower') else 1. for n in FEATURE_NAMES]
    ordinary=StrictIntervalBank({h:dict(weights=weights,density='knn',quantile=.95,threshold=2.) for h in HORIZONS},96)
    return AlignedBank(ordinary,point)


@pytest.mark.parametrize('h',HORIZONS)
def test_all_modes_use_selected_batch_only(bank,h):
    f=sample();ref=bank.prepare_reference(f.iloc[:96]);x=f.iloc[96:][['log_requests','log_duration_ms','log_items_per_process']].to_numpy(float)
    mask=np.ones(48,bool);before,_=bank.score_arrays(ref,x,mask,mask,h)
    changed=x.copy();changed[:24]+=10.;changed[24+h:]+=10.
    after,_=bank.score_arrays(ref,changed,mask,mask,h)
    np.testing.assert_array_equal(before[24:24+h],after[24:24+h])
    standalone=bank.predict_batch(ref,f.iloc[120:120+h],h)
    np.testing.assert_allclose(before[24:24+h],standalone.score,atol=1e-11,rtol=0)
    assert not standalone.monitoring_state_carried.any()


@pytest.mark.parametrize('h',HORIZONS[1:])
def test_other_four_models_exactly_unchanged(bank,h):
    f=sample(h)
    np.testing.assert_array_equal(score_frame(bank,f,h).score,score_frame(bank.ordinary,f,h).score)


def test_zero_alignment_is_identity_with_original_scale(bank):
    p=bank.point;config={**p.config,'scale_alpha':[1.,1.],'alignment_strength':[0.,0.]}
    identity=AlignedPoint(p.grid,p.buckets,p.curves,config,p.normal_cdf,p.normal_scale_prior)
    plain=SharedARPoint(p.grid,p.buckets,p.curves,config)
    x=sample(1)[['log_requests','log_duration_ms','log_items_per_process']].to_numpy(float);m=np.ones(96,bool)
    a=identity.prepare_reference(x[:96],m,m);b=plain.prepare_reference(x[:96],m,m)
    np.testing.assert_array_equal(identity.score_arrays(a,x[96:],m[:1],m[:1])[0],plain.score_arrays(b,x[96:],m[:1],m[:1])[0])


def test_reference_and_query_not_modified_and_inference_has_no_io(bank):
    f=sample(1);before=f.copy(deep=True);ref=bank.prepare_reference(f.iloc[:96]);q=ref.point.source_quantiles.copy()
    with patch('builtins.open',side_effect=AssertionError('Inference file I/O')):
        out=bank.predict_batch(ref,f.iloc[96:],1)
    np.testing.assert_array_equal(ref.point.source_quantiles,q)
    assert f.equals(before) and out.assessable.all()


def test_serialization_round_trip(bank):
    f=sample(1);buf=io.BytesIO();joblib.dump(bank,buf);buf.seek(0);loaded=joblib.load(buf)
    np.testing.assert_array_equal(score_frame(bank,f,1).score,score_frame(loaded,f,1).score)


def test_missing_collection_and_zero_arrivals(bank):
    f=sample(1);f.loc[96:,['collection_observed','latency_available']]=False
    f.loc[96:,['log_requests','log_duration_ms','log_items_per_process']]=np.nan
    out=score_frame(bank,f,1)
    assert out.score.isna().all() and not out.assessable.any() and not out.is_anomaly.any()
    f=sample(1);f.loc[96:,'log_requests']=0.;f.loc[96:,'latency_available']=False
    f.loc[96:,['log_duration_ms','log_items_per_process']]=np.nan
    out=score_frame(bank,f,1)
    assert out.assessable.all() and not out.reason.str.endswith(':duration').any()


def test_cli_round_trip_and_existing_output_guard(bank,tmp_path):
    from ..common import ROOT
    model=tmp_path/'model.joblib';input_path=tmp_path/'input.csv';output=tmp_path/'result.csv'
    joblib.dump(bank,model);sample(1).to_csv(input_path,index=False)
    args=[sys.executable,'-m','long_term_anomaly.strict_alignment','--model',str(model),
          '--input',str(input_path),'--output',str(output),'--analysis-hours','1']
    env={**os.environ,'PYTHONPATH':str(ROOT/'code'),'PYTHONIOENCODING':'utf-8'}
    result=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    before=output.read_bytes();second=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
    assert second.returncode!=0 and output.read_bytes()==before
