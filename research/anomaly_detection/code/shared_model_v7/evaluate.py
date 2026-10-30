"""Freeze explicit candidates, replay ALL cases, never auto-publish a release.

Full results are adaptive research results. An attempted result can fail any
gate; existing v6 artifacts/latest pointers are never changed by this module.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,os,tempfile
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import _stats
from shared_model_v4.rank_context import Context
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.acceptance import check,policy
from shared_model_v7.role_priority import analyze


def atomic_npz(path,**arrays):
    with tempfile.NamedTemporaryFile(prefix=path.stem+'.',suffix='.npz',dir=path.parent,delete=False)as stream:name=Path(stream.name)
    np.savez_compressed(name,**arrays);os.replace(name,path)


def run(output,sources):
    protocol=freeze();output=Path(output).resolve()
    if not output.is_relative_to(OUT.resolve()):raise ValueError('v7 attempts must stay under reports/shared_model_v7')
    if output.name=='release':raise ValueError('Full evaluation is an attempt; audited publication is a separate action')
    output.mkdir(parents=True,exist_ok=True);source_records={a:{'path':str(Path(sources[a]).resolve()),'sha256':sha256(sources[a])}for a in ALGORITHMS}
    selection=output/'selection.json'
    if not selection.exists():
        models={a:joblib.load(sources[a])for a in ALGORITHMS}
        for a,m in models.items():
            if m.algorithm!=a or not np.isfinite(m.threshold):raise ValueError('Wrong estimator or nonfinite threshold: '+a)
        pkg=joblib.load(BASE/'models.joblib');pkg['models']=models;pkg.pop('alternative_IF',None)
        atomic_dump(pkg,output/'models.joblib')
        write(selection,{'sources':source_records,'models_sha256':sha256(output/'models.joblib'),
            'models':{a:m.record()for a,m in models.items()},'thresholds_frozen_before_full_replay':True,
            'normal_only_estimator_fit':True,'research_probe_labels_used_for_selection':True,'external_blind_test':False,
            'strict_each_task_nonregression':True,'baseline_summary_sha256':protocol['baseline_summary_sha256'],
            'code_sha256':{str(p.relative_to(ROOT)):sha256(p)for p in(ROOT/'code/shared_model_v7').glob('*.py')}})
    receipt=read(selection)
    if receipt['sources']!=source_records:raise ValueError('Output is frozen for different candidates; use another attempt folder')
    assert sha256(output/'models.joblib')==receipt['models_sha256'];pkg=joblib.load(output/'models.joblib');models=pkg['models']
    history=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['rank_history'];context=Context(history,6)
    datasets=[];truth=[];scores=[]
    for t in TASKS:
        target=output/(t+'_scores.npz');prior=np.load(BASE/(t+'_scores.npz'))
        if not target.exists():
            yy=[];ss=[];ids=[];adapters=[]
            index=read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372'/t/'index.json')
            for number,e in enumerate(index):
                path=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372'/t/e['file']
                assert sha256(path)==read(path.with_suffix('.json'))['sha256'];pack=joblib.load(path)
                f=pack['frame'];f=pd.concat([f,context.transform(f)],axis=1);m=pack['metadata']
                value=np.column_stack([models[a].score(f)for a in ALGORITHMS])
                if value.shape!=(len(f),3)or not np.isfinite(value).all():raise ValueError('Invalid score rows: '+t)
                yy.append(np.asarray(m['truth'],bool));ss.append(value);ids.append(np.asarray(m['case_index']));adapters.append(f.adapterType.to_numpy(str))
                print('V7 FULL',t,number+1,'/',len(index),flush=True)
            arrays={'y':np.concatenate(yy),'scores':np.concatenate(ss),'case_index':np.concatenate(ids),'adapter':np.concatenate(adapters)}
            for key in('y','case_index','adapter'):np.testing.assert_array_equal(arrays[key],prior[key])
            atomic_npz(target,**arrays)
        value=np.load(target)
        for key in('y','case_index','adapter'):np.testing.assert_array_equal(value[key],prior[key])
        rows=[{'method':a,**_stats(value['y'],value['scores'][:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':t,'metrics':rows});truth.append(value['y']);scores.append(value['scores'])
        write(output/'evaluation_progress.json',{'completed_datasets':datasets,'scope':'full fixed benchmark','publication':False})
        print('V7 TASK',t,[(a,round(r['precision']*100,3),round(r['recall']*100,3),round(r['f1']*100,3))for a,r in zip(ALGORITHMS,rows)],flush=True)
    y=np.concatenate(truth);s=np.concatenate(scores)
    summary={'datasets':datasets,'pooled':[{'method':a,**_stats(y,s[:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)],
        'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),'unchanged_rows_labels_severity':True,
            'point_adjustment':False,'external_blind_test':False,'all_5000_paired_cases_each_task':True,'published_release':False}}
    acceptance=check(summary,read(BASE/'summary.json'),**policy(protocol));summary['acceptance']=acceptance
    summary['requested_roles']=analyze(summary);summary['metric_targets_met']=acceptance['all_targets_met']
    summary['all_targets_met']=acceptance['all_targets_met']and summary['requested_roles']['all_desired_models_observed_best']
    write(output/'summary.json',summary)
    print('V7 FULL STRICT ACCEPTANCE',acceptance['all_targets_met'],'(not automatically published)',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    for a in ALGORITHMS:parser.add_argument('--'+a+'-model',type=Path,required=True)
    args=parser.parse_args();run(args.output,{a:getattr(args,a+'_model')for a in ALGORITHMS})
