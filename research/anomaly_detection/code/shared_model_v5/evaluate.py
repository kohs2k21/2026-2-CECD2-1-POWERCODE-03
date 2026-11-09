"""Frozen attempt: restore balanced IF, retain KM, evaluate balanced LOF selection.

This is an adaptively reused research benchmark, NOT an external blind test.
No estimator is fitted on test rows. Reused scores retain identity checks.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import _stats
from shared_model_v5.protocol import TASKS,ALGORITHMS,OUT,freeze,gates

def run(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);protocol=freeze()
    if not(out/'selection.json').exists():
        pkg=joblib.load(ROOT/'reports/shared_model_v4_final/models.joblib')
        pkg['models']['if']=joblib.load(ROOT/'reports/shared_model_v3_release/models.joblib')['models']['if']
        pkg['models']['lof']=joblib.load(OUT/'existing_search/lof_selected.joblib')
        joblib.dump(pkg,out/'models.joblib',compress=3)
        write(out/'selection.json',{'model_sha256':sha256(out/'models.joblib'),
            'models':{a:m.record()for a,m in pkg['models'].items()},
            'sources':{'if':'shared_model_v3_release: restore previous nonzero balanced result',
                'kmeans':'shared_model_v4_final: retained already passing model',
                'lof':read(OUT/'existing_search/lof_selection.json')},
            'thresholds_frozen_before_this_attempt_full_evaluation':True,'test_rows_used_for_fit':0,
            'adaptive_research_benchmark':True,'external_blind_test':False,
            'source_cache_manifest_prefix':protocol['parent_feature_cache_hash_prefix']})
    selection=read(out/'selection.json');assert sha256(out/'models.joblib')==selection['model_sha256']
    pkg=joblib.load(out/'models.joblib');models=pkg['models'];prefix=selection['source_cache_manifest_prefix']
    cache=ROOT/'reports/shared_model_v3/feature_cache'/prefix
    parent=ROOT/'reports/shared_model_v2/feature_cache'/prefix
    all_y=[];all_s=[];datasets=[]
    for name in TASKS:
        path=out/(name+'_scores.npz')
        old=np.load(ROOT/'reports/shared_model_v3_release'/(name+'_scores.npz'))
        current=np.load(ROOT/'reports/shared_model_v4_final'/(name+'_scores.npz'))
        for key in ('y','case_index','adapter'):np.testing.assert_array_equal(old[key],current[key])
        if not path.exists():
            yy=[];ss=[];ids=[];adapters=[]
            for i,e in enumerate(read(parent/name/'index.json')):
                target=cache/name/e['file'];assert sha256(target)==read(target.with_suffix('.json'))['sha256']
                payload=joblib.load(target);f=payload['frame'];meta=payload['metadata']
                yy.append(np.asarray(meta['truth'],bool));ss.append(models['lof'].score(f))
                ids.append(meta['case_index']);adapters.append(f.adapterType.to_numpy(str))
                print('EVAL',name,i+1,flush=True)
            y=np.concatenate(yy);case=np.concatenate(ids);adapter=np.concatenate(adapters)
            for key,arr in [('y',y),('case_index',case),('adapter',adapter)]:np.testing.assert_array_equal(old[key],arr)
            s=np.column_stack([old['scores'][:,0],current['scores'][:,1],np.concatenate(ss)])
            np.savez_compressed(path,y=y,scores=s,case_index=case,adapter=adapter)
        z=np.load(path);y=z['y'];s=z['scores']
        rows=[{'method':a,**_stats(y,s[:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':rows});all_y.append(y);all_s.append(s)
        write(out/'evaluation_progress.json',{'datasets':datasets})
        print(name,[(r['method'],round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,3))for r in rows],flush=True)
    y=np.concatenate(all_y);s=np.concatenate(all_s)
    pooled=[{'method':a,**_stats(y,s[:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)]
    acceptance={a:{**gates([d['metrics'][i]for d in datasets]),'pooled_FPR_below_5pct':pooled[i]['normal_false_positive_rate']<.05}for i,a in enumerate(ALGORITHMS)}
    passed=all(g['all_met']and g['pooled_FPR_below_5pct']for g in acceptance.values())
    write(out/'summary.json',{'pooled':pooled,'datasets':datasets,'acceptance':acceptance,'all_targets_met':passed,
        'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),
            'active_tasks':list(TASKS),'retained_test_data_modified':False,'point_adjustment':False,
            'single_estimator_each':True,'adapter_experts':False,'external_blind_test':False}})
    print('ACCEPTANCE',acceptance,'ALL',passed,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT/'attempt01');run(p.parse_args().output)
