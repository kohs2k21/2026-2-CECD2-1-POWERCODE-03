"""Independent metrics/raw-Process smoke checks, including FAILED attempts.

An audit may confirm a reported failure. It must never convert successful
arithmetic/serving checks into a claim that metric/role targets were met.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,builtins,io
from unittest.mock import patch
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score,precision_score,recall_score,f1_score,confusion_matrix
from model_research_v13.data import read,write
from model_evaluation.data import sha256,ReplayCatalog
from model_improvement.data import rich_rows
from evaluation_benchmark.generate import Case
from shared_model_target.inference import Monitor
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.acceptance import check,policy
from shared_model_v7.role_priority import analyze


def run(output):
    protocol=freeze();dest=Path(output).resolve()
    if not dest.is_relative_to(OUT.resolve()):raise ValueError('Research only')
    datasets=[{'dataset':t,'metrics':[]}for t in TASKS];pooled=[];monitors=[]
    pkg=joblib.load(BASE/'models.joblib')
    bundle=read(ROOT/'reports/evaluation_v2_work/inputs.json')
    history=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['rank_history']
    case=Case(ReplayCatalog(bundle['templates']),'input_stop',0,48500042,history);case.baseline(20)
    native=pd.DataFrame(case.export()['windows.csv']);native=native.loc[native.scope.eq('TARGET')].reset_index(drop=True)
    raw=rich_rows(case,case.normal)
    real_open=builtins.open
    def guard(file,*args,**kwargs):
        name=str(file).replace('\\','/')
        if any(x in name for x in ('data/','reports/','training_inputs.joblib','development.joblib')):raise AssertionError('Runtime research-data read: '+name)
        return real_open(file,*args,**kwargs)
    functions={'accuracy':accuracy_score,'precision':precision_score,'recall':recall_score,'f1':f1_score}
    for a in ALGORITHMS:
        receipt=read(dest/(a+'_selection.json'));model=joblib.load(dest/(a+'_model.joblib'))
        assert sha256(dest/(a+'_model.joblib'))==receipt['model_sha256']
        reported=read(dest/(a+'_summary.json'));yy=[];pp=[]
        for i,t in enumerate(TASKS):
            z=np.load(dest/(a+'_'+t+'_scores.npz'));old=np.load(BASE/(t+'_scores.npz'))
            for key in ('y','case_index','adapter'):np.testing.assert_array_equal(z[key],old[key])
            prediction=z['scores']>model.threshold;y=z['y'];yy.append(y);pp.append(prediction)
            tn,fp,fn,tp=map(int,confusion_matrix(y,prediction,labels=[False,True]).ravel())
            r={'method':a,'tn':tn,'fp':fp,'fn':fn,'tp':tp,'n':len(y),'normal':tn+fp,'anomaly':fn+tp,
               'normal_false_positive_rate':fp/max(tn+fp,1)}
            r.update({k:float(fn_(y,prediction))for k,fn_ in functions.items()})
            for k,v in r.items():
                if k=='method':continue
                np.testing.assert_allclose(v,reported['datasets'][i]['metrics'][0][k],rtol=0,atol=1e-12)
            datasets[i]['metrics'].append(r)
        y=np.concatenate(yy);prediction=np.concatenate(pp)
        tn,fp,fn,tp=map(int,confusion_matrix(y,prediction,labels=[False,True]).ravel())
        r={'method':a,'tn':tn,'fp':fp,'fn':fn,'tp':tp,'normal_false_positive_rate':fp/max(tn+fp,1)}
        r.update({k:float(fn_(y,prediction))for k,fn_ in functions.items()});pooled.append(r)
        for k,v in r.items():
            if k!='method':np.testing.assert_allclose(v,reported['pooled'][k],rtol=0,atol=1e-12)
        monitor=Monitor(model,pkg['adapter_reference'],pkg['feature_context'])
        path=dest/(a+'_research_monitor.joblib');atomic_dump(monitor,path);loaded=joblib.load(path)
        with patch('builtins.open',guard),patch('io.open',guard):
            whole=loaded.predict(native,raw);prefix=loaded.predict(native.iloc[:10].copy(),raw)
            un=native.copy();un['adapterType']='UNKNOWN_V7';ur=raw.copy();ur['adapterType']='UNKNOWN_V7'
            unknown=loaded.predict(un,ur)
        np.testing.assert_allclose(whole.score.iloc[:10],prefix.score,rtol=1e-10,atol=1e-10)
        np.testing.assert_array_equal(whole.score,monitor.predict(native,raw).score)
        assert np.isfinite(unknown.score).all()
        monitors.append({'algorithm':a,'raw_Process_predict':True,'prefix_causal':True,'unknown_adapter_finite':True,
                         'save_load_identical':True,'no_runtime_data_reads':True,'research_only_not_promoted':True})
        print('ATTEMPT INDEPENDENT AUDIT',a,'arithmetic/raw serving passed; metric goals separately assessed',flush=True)
    summary={'datasets':datasets,'pooled':pooled,'external_blind_test':False,'publication':False}
    summary['acceptance']=check(summary,protocol['baseline_summary'],**policy(protocol));summary['requested_roles']=analyze(summary)
    summary['all_targets_met']=summary['acceptance']['all_targets_met']and summary['requested_roles']['all_desired_models_observed_best']
    write(dest/'audited_summary.json',summary)
    write(dest/'audit.json',{'independent_metrics_recomputed':True,'raw_serving_smoke_checks':monitors,
                            'all_targets_met':summary['all_targets_met'],'published':False,'external_blind_test':False})
    print('ARITHMETIC/SERVING AUDIT PASSED; GOAL MET =',summary['all_targets_met'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
