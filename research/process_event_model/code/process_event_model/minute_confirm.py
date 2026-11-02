"""Frozen-candidate confirmation on exactly the previous minute-level truth.

Main metrics are final causal alerts, NOT raw Process-row F1. Original rows and
IDs stay intact. Query timestamps inspect existing alarm state, not new logs.
"""
import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .data import ROOT,digest,write_json
from .minute_refinement import ACTIVE_TASKS,ALGORITHMS,MinuteReplay,EventPolicy
from .models import metrics
from .native_history import NativeHistory


def features(task):
    base=ROOT/'reports/process_event_model/current/event_models.joblib'
    package=joblib.load(base)
    signature=hashlib.sha256(joblib.hash(package['reference']).encode()).hexdigest()[:12]
    signature+='-predictive-'+digest(ROOT/'code/process_event_model/predictive.py')[:12]
    source=ROOT/'reports/process_event_model/confirmation_features'/signature/f'{task}_400.joblib'
    code_sha=digest(ROOT/'code/process_event_model/native_history.py')[:12]
    target=ROOT/'reports/process_event_model/minute_confirmation_features'/code_sha/f'{task}_400.joblib'
    if target.exists():return joblib.load(target)
    pair,cases=joblib.load(source)
    data={}
    for cohort,f in pair.items():
        f=f.loc[f.target].copy().reset_index(drop=True)
        data[cohort]=NativeHistory().transform(f)
        assert len(data[cohort])==len(f)
        np.testing.assert_array_equal(data[cohort].event_row_id,f.event_row_id)
    target.parent.mkdir(parents=True,exist_ok=True)
    joblib.dump((data,cases),target,compress=3)
    return data,cases


def confirm(source):
    source=Path(source)
    package=joblib.load(source/'event_models.joblib')
    if package['processor']!='native_row_context':
        raise ValueError('This evaluator expects the audited native execution processor')
    sha=digest(source/'event_models.joblib')
    selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    policy={a:selection['models'][a]['policy'] for a in ALGORITHMS}
    write_json(source/'incident_policy.json',dict(
        model_sha256=sha,runtime_sha256=digest(ROOT/'code/process_event_model/event_policy_runtime.py'),
        models={a:dict(params=p) for a,p in policy.items()},
        no_silence_onset=True,no_synthetic_Process_rows=True,
        evaluation_unit='final causal minute alert; original Process row input'))
    report=dict(model_sha256=sha,unit='same legacy minute truth',development_cases_per_task=400,
        confirmation_cases_per_task=4600,external_blind_test=False,benchmark_unchanged=True,
        no_point_adjustment=True,no_missing_Process_rows_generated=True,scope=list(ACTIVE_TASKS),tasks={})
    pooled={a:dict(y=[],p=[]) for a in ALGORITHMS}
    for task in ACTIVE_TASKS:
        pair,cases=features(task)
        windows=pd.read_csv(ROOT/'data/evaluation'/task/'window_labels.csv',encoding='utf-8-sig')
        windows=windows.loc[windows.scope.eq('TARGET')&windows.case_id.isin(cases.case_id)].copy()
        windows['_end']=(pd.to_datetime(windows.window_end,format='ISO8601').dt.tz_localize('Asia/Seoul')
            .dt.tz_convert('UTC').astype('datetime64[ns, UTC]').astype('int64')/1e9)
        frame=[];queries=[];yy=[]
        for cohort in ('observed','normal'):
            f=pair[cohort].copy()
            v=package['reference'].transform(f)
            f['row_residual'],f['row_upper_tail'],f['reference_level'],f['reference_support']=v.T
            f['_uid']=task+':'+cohort+':'+f.case_id.astype(str)
            f['_time']=f.availableTime.astype('datetime64[ns, UTC]').astype('int64')/1e9
            frame.append(f)
            w=windows.copy();w['_uid']=task+':'+cohort+':'+w.case_id.astype(str);queries.append(w)
            yy.append(w.window_is_anomaly.to_numpy(bool) if cohort=='observed' else np.zeros(len(w),bool))
        f=pd.concat(frame,ignore_index=True);w=pd.concat(queries,ignore_index=True);y=np.concatenate(yy)
        replay=MinuteReplay(f,w)
        old=np.load(ROOT/'reports/shared_model_v7/release'/f'{task}_scores.npz')
        keep=old['case_index']>=400
        assert len(y)==int(keep.sum()) and y.sum()==old['y'][keep].sum()
        legacy={a:metrics(old['y'][keep],old['scores'][keep,j]>1.) for j,a in enumerate(ALGORITHMS)}
        now={};row={};deltas={}
        for a,model in package['models'].items():
            score,alarm=model.predict(f)
            p=EventPolicy(**policy[a])
            families=model.state(f)[0] if p.traffic_model_family else None
            prediction=replay.project(score,p,families=families)
            now[a]=metrics(y,prediction);row[a]=metrics(f.label.to_numpy(bool),alarm)
            pooled[a]['y'].append(y);pooled[a]['p'].append(prediction)
            deltas[a]={k:100*(now[a][k]-legacy[a][k]) for k in ('accuracy','precision','recall','f1','false_positive_rate')}
            np.savez_compressed(source/f'{task}_{a}_minute_predictions.npz',truth=y,prediction=prediction)
        report['tasks'][task]=dict(cases=len(cases),legacy=legacy,current=now,
            delta_percentage_points=deltas,Process_row_metrics_secondary_only=row)
        write_json(source/'minute_confirmation_progress.json',report)
        print('SAME-MINUTE CONFIRM',task,{a:dict(f1=round(m['f1'],4),fpr=round(m['false_positive_rate'],4),delta_pp=round(deltas[a]['f1'],2)) for a,m in now.items()},flush=True)
    report['pooled']={a:metrics(np.concatenate(p['y']),np.concatenate(p['p'])) for a,p in pooled.items()}
    report['all_scenario_fpr_le_6_percent']=all(m['false_positive_rate']<=.06 for t in report['tasks'].values() for m in t['current'].values())
    report['worst_scenario_f1_loss_percentage_points']=max(-d['f1'] for t in report['tasks'].values() for d in t['delta_percentage_points'].values())
    assert digest(source/'event_models.joblib')==sha,'Model changed while confirming'
    write_json(source/'minute_confirmation.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    confirm(parser.parse_args().source)
