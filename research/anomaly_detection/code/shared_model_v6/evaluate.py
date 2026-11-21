"""Freeze candidate artifact hashes, then evaluate ALL unchanged five-task rows."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v6.protocol import OUT,BASE,freeze,TASKS,ALGORITHMS
from shared_model_v4.rank_context import Context,RANK
from shared_model_improvement.run import _stats
from shared_model_v5.protocol import gates

def acceptance(datasets,pooled,baseline):
    result={}
    for i,a in enumerate(ALGORITHMS):
        r=pooled[i];b=baseline['pooled'][i];g=gates([d['metrics'][i]for d in datasets])
        maintained={k:r[k]>=b[k]-1e-12 for k in ('accuracy','precision','f1')}
        task_before={d['dataset']:d['metrics'][i]for d in baseline['datasets']}
        changes={d['dataset']:{k:100*(d['metrics'][i][k]-task_before[d['dataset']][k])for k in ('accuracy','precision','recall','f1','normal_false_positive_rate')}for d in datasets}
        result[a]={**g,'Recall_at_least_60pct':r['recall']>=.6,'maintained_pooled_metrics':maintained,
            'pooled_FPR_at_most_5pct':r['normal_false_positive_rate']<=.05,
            'gains_pp':{k:100*(r[k]-b[k])for k in ('accuracy','precision','recall','f1','normal_false_positive_rate')},
            'per_task_changes_pp':changes,'all_task_F1_nonregression':all(v['f1']>=-1e-9 for v in changes.values())}
        result[a]['all_targets_met']=g['all_met']and all(maintained.values())and r['recall']>=.6 and r['normal_false_positive_rate']<=.05
    return result

def run(output):
    freeze();output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if not(output/'selection.json').exists():
        sources={'if':OUT/'probe/if_selected.joblib','lof':OUT/'calibration_probe/lof_selected.joblib','kmeans':OUT/'calibration_probe/kmeans_selected.joblib'}
        models={a:joblib.load(p)for a,p in sources.items()};pkg=joblib.load(BASE/'models.joblib');pkg['models']=models
        # Secondary IF is diagnostic only, never a hidden ensemble.
        alternative=joblib.load(OUT/'calibration_probe/if_selected.joblib');pkg['alternative_IF']=alternative
        joblib.dump(pkg,output/'models.joblib',compress=3)
        write(output/'selection.json',{'model_sha256':sha256(output/'models.joblib'),
            'source_files':{a:{'path':str(p.relative_to(ROOT)),'sha256':sha256(p)}for a,p in sources.items()},
            'models':{a:m.record()for a,m in models.items()},'secondary_IF':alternative.record(),
            'thresholds_frozen_before_this_full_evaluation':True,'probe_research_labels_used_for_selection':True,
            'estimator_fit_test_rows':0,'external_blind_test':False,'protocol_sha256':sha256(OUT/'protocol.json'),
            'code_sha256':{p.name:sha256(p)for p in(ROOT/'code/shared_model_v6').glob('*.py')}})
    sel=read(output/'selection.json');assert sha256(output/'models.joblib')==sel['model_sha256'];pkg=joblib.load(output/'models.joblib');models=pkg['models']
    dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');context=Context(dev['rank_history'],6)
    datasets=[];ys=[];scores=[];alternatives=[]
    for t in TASKS:
        target=output/(t+'_scores.npz');old=np.load(BASE/(t+'_scores.npz'))
        if not target.exists():
            yy=[];ss=[];ii=[];aa=[];alts=[]
            for ei,e in enumerate(read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372'/t/'index.json')):
                p=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372'/t/e['file'];assert sha256(p)==read(p.with_suffix('.json'))['sha256']
                payload=joblib.load(p);f=payload['frame'];f=pd.concat([f,context.transform(f)],axis=1);m=payload['metadata']
                yy.append(np.asarray(m['truth'],bool));ii.append(m['case_index']);aa.append(f.adapterType.to_numpy(str))
                ss.append(np.column_stack([models[a].score(f)for a in ALGORITHMS]));alts.append(pkg['alternative_IF'].score(f))
                print('FULL',t,ei+1,flush=True)
            y=np.concatenate(yy);s=np.concatenate(ss);ids=np.concatenate(ii);adapters=np.concatenate(aa)
            for key,v in [('y',y),('case_index',ids),('adapter',adapters)]:np.testing.assert_array_equal(old[key],v)
            np.savez_compressed(target,y=y,scores=s,case_index=ids,adapter=adapters,alternative_IF=np.concatenate(alts))
        z=np.load(target);y=z['y'];s=z['scores'];rows=[{'method':a,**_stats(y,s[:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':t,'metrics':rows});ys.append(y);scores.append(s);alternatives.append(z['alternative_IF'])
        write(output/'evaluation_progress.json',{'datasets':datasets})
        print('TASK',t,[(a,*[round(r[k]*100,2)for k in ('precision','recall','f1','normal_false_positive_rate')])for a,r in zip(ALGORITHMS,rows)],flush=True)
    y=np.concatenate(ys);s=np.concatenate(scores);pooled=[{'method':a,**_stats(y,s[:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)]
    acc=acceptance(datasets,pooled,read(BASE/'summary.json'));alt_rows=[]
    for t,ay in zip(TASKS,alternatives):
        z=np.load(output/(t+'_scores.npz'));alt_rows.append({'task':t,**_stats(z['y'],ay,pkg['alternative_IF'].threshold)})
    write(output/'summary.json',{'pooled':pooled,'datasets':datasets,'acceptance':acc,'all_targets_met':all(v['all_targets_met']for v in acc.values()),
        'alternative_IF':{'pooled':_stats(y,np.concatenate(alternatives),pkg['alternative_IF'].threshold),'tasks':alt_rows},
        'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),'active_tasks':list(TASKS),
            'unchanged_data_labels_severity':True,'point_adjustment':False,'single_estimator_each':True,'external_blind_test':False}})
    print('FULL ACCEPTANCE',acc,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT/'attempt01');run(p.parse_args().output)
