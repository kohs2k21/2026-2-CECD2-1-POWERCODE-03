"""Freeze and replay one candidate on every fixed benchmark decision.

Independent models may finish at different times; this avoids recomputing the
other two algorithms while checking a new core/calibration candidate. This
module NEVER promotes a release and cannot label a partial result complete.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import _stats
from shared_model_v4.rank_context import Context
from shared_model_v7.evaluate import atomic_npz
from shared_model_v7.protocol import OUT,BASE,TASKS,freeze,atomic_dump


def run(algorithm,source,output):
    protocol=freeze();dest=Path(output).resolve()
    if not dest.is_relative_to(OUT.resolve())or dest.name=='release':raise ValueError('Research attempt only')
    dest.mkdir(parents=True,exist_ok=True);receipt=dest/(algorithm+'_selection.json');snapshot=dest/(algorithm+'_model.joblib')
    if not receipt.exists():
        before=sha256(source);model=joblib.load(source)
        if sha256(source)!=before:raise ValueError('Source changed while freezing')
        if model.algorithm!=algorithm or not np.isfinite(model.threshold):raise ValueError('Invalid model')
        atomic_dump(model,snapshot)
        write(receipt,{'source':str(Path(source).resolve()),'source_sha256':before,'model_sha256':sha256(snapshot),
                       'record':model.record(),'threshold_frozen_before_full_replay':True,'external_blind_test':False,
                       'code_sha256':{str(p.relative_to(ROOT)):sha256(p)for p in(ROOT/'code/shared_model_v7').glob('*.py')}})
    r=read(receipt);assert sha256(snapshot)==r['model_sha256'];model=joblib.load(snapshot)
    history=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['rank_history'];context=Context(history,6)
    datasets=[];truth=[];scores=[]
    for t in TASKS:
        target=dest/(algorithm+'_'+t+'_scores.npz');prior=np.load(BASE/(t+'_scores.npz'))
        if not target.exists():
            yy=[];ss=[];ids=[];adapters=[]
            index=read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372'/t/'index.json')
            for number,e in enumerate(index):
                path=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372'/t/e['file']
                assert sha256(path)==read(path.with_suffix('.json'))['sha256'];pack=joblib.load(path)
                f=pack['frame'];f=pd.concat([f,context.transform(f)],axis=1);m=pack['metadata'];score=model.score(f)
                if score.shape!=(len(f),)or not np.isfinite(score).all():raise ValueError('Invalid score vector')
                yy.append(np.asarray(m['truth'],bool));ss.append(score);ids.append(np.asarray(m['case_index']));adapters.append(f.adapterType.to_numpy(str))
                print('ONE FULL',algorithm,t,number+1,'/',len(index),flush=True)
            arrays={'y':np.concatenate(yy),'scores':np.concatenate(ss),'case_index':np.concatenate(ids),'adapter':np.concatenate(adapters)}
            for key in ('y','case_index','adapter'):np.testing.assert_array_equal(arrays[key],prior[key])
            atomic_npz(target,**arrays)
        value=np.load(target)
        for key in ('y','case_index','adapter'):np.testing.assert_array_equal(value[key],prior[key])
        metric={'method':algorithm,**_stats(value['y'],value['scores'],model.threshold)}
        old=next(r for d in protocol['baseline_summary']['datasets']if d['dataset']==t for r in d['metrics']if r['method']==algorithm)
        checks={k:metric[k]>=old[k]-1e-12 for k in ('accuracy','precision','f1')}
        checks.update({'R55':metric['recall']>=.55,'FPR_nonincrease':metric['normal_false_positive_rate']<=old['normal_false_positive_rate']+1e-12})
        datasets.append({'dataset':t,'metrics':[metric],'checks':checks,'all_met':all(checks.values())})
        truth.append(value['y']);scores.append(value['scores'])
        write(dest/(algorithm+'_progress.json'),{'datasets':datasets,'publication':False})
        print('ONE TASK',algorithm,t,{k:round(metric[k]*100,4)for k in ('precision','recall','f1','normal_false_positive_rate')},checks,flush=True)
    pooled=_stats(np.concatenate(truth),np.concatenate(scores),model.threshold)
    old=next(r for r in protocol['baseline_summary']['pooled']if r['method']==algorithm)
    pooled_met=all(pooled[k]>=old[k]-1e-12 for k in ('accuracy','precision','recall','f1'))and pooled['normal_false_positive_rate']<=old['normal_false_positive_rate']+1e-12
    write(dest/(algorithm+'_summary.json'),{'algorithm':algorithm,'datasets':datasets,'pooled':pooled,
        'all_metric_targets_met':pooled_met and all(d['all_met']for d in datasets),'all_5000_paired_cases_each_task':True,
        'model_sha256':r['model_sha256'],'published':False,'external_blind_test':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithm',choices=['if','kmeans','lof'],required=True)
    p.add_argument('--model',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();run(args.algorithm,args.model,args.output)
