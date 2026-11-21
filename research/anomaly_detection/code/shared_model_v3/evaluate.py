"""Unchanged minute-level benchmark, no oracle adjustment or threshold tuning."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,time
import joblib
import numpy as np
from sklearn.metrics import average_precision_score,roc_auc_score
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v3.features import augment as sequence
from shared_model_v3.adaptive import augment as adaptive
from shared_model_v3.sensitive import augment as sensitive

def evaluate(out,readonly_cache=False,wait_cache=False):
    out=Path(out);sel=read(out/'selection.json');assert sha256(out/'models.joblib')==sel['model_sha256']
    assert sha256(ROOT/'data/evaluation/manifest.json')==sel['benchmark_manifest_sha256']
    pkg=joblib.load(out/'models.joblib');parent=ROOT/'reports/shared_model_v2/feature_cache'/sel['benchmark_manifest_sha256'][:16]
    cache=ROOT/'reports/shared_model_v3/feature_cache'/sel['benchmark_manifest_sha256'][:16]
    cache.mkdir(parents=True,exist_ok=True)
    protocol={c:sha256(ROOT/'code/shared_model_v3'/c)for c in ('features.py','adaptive.py','sensitive.py')}
    if(cache/'protocol.json').exists():assert read(cache/'protocol.json')==protocol
    else:write(cache/'protocol.json',protocol)
    needs_extra=any(any(c.startswith(('seq_','small_','rank_','state_','evidence_'))for c in model.columns)for model in pkg['models'].values())
    all_y=[];all_s=[];datasets=[]
    for name in read(ROOT/'data/evaluation/manifest.json')['datasets']:
        dest=cache/name;dest.mkdir(exist_ok=True);ys=[];ss=[];ids=[];adapters=[];entries=[]
        for e in read(parent/name/'index.json'):
            path=parent/name/e['file'];assert sha256(path)==e['sha256'];target=dest/e['file']
            if needs_extra and readonly_cache and wait_cache:
                deadline=time.monotonic()+1800
                while not(target.exists()and target.with_suffix('.json').exists()):
                    if time.monotonic()>deadline:raise TimeoutError('Feature-cache writer did not finish '+str(target))
                    time.sleep(2)
            if not needs_extra:payload=joblib.load(path)
            elif target.exists():
                assert sha256(target)==read(target.with_suffix('.json'))['sha256'];payload=joblib.load(target)
            else:
                if readonly_cache:raise FileNotFoundError('Finish feature cache generation first: '+str(target))
                payload=joblib.load(path);payload['frame']=sensitive(adaptive(sequence(payload['frame'])))
                joblib.dump(payload,target,compress=3);write(target.with_suffix('.json'),{'sha256':sha256(target),'source_sha256':e['sha256']})
            f=payload['frame'];m=payload['metadata'];y=np.asarray(m['truth'],bool)
            s=np.column_stack([pkg['models'][a].score(f)for a in ALGORITHMS])
            ys.append(y);ss.append(s);ids.append(m['case_index']);adapters.append(f.adapterType.to_numpy(str))
            if needs_extra:entries.append({**e,'sha256':sha256(target)})
        if needs_extra and not readonly_cache:write(dest/'index.json',entries)
        y,s=np.concatenate(ys),np.concatenate(ss)
        np.savez_compressed(out/(name+'_scores.npz'),y=y,scores=s,case_index=np.concatenate(ids),adapter=np.concatenate(adapters))
        rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
            'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':rows});all_y.append(y);all_s.append(s)
        write(out/'evaluation_progress.json',{'datasets':datasets})
        print(name,[(r['method'],round(r['f1']*100,2),round(r['recall']*100,2),round(r['normal_false_positive_rate']*100,2))for r in rows],flush=True)
    y,s=np.concatenate(all_y),np.concatenate(all_s);old=read(ROOT/'reports/shared_model_v2_release/summary.json')['pooled']
    rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
        'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
    changes=[{'method':a,'F1_gain_pp':100*(r['f1']-b['f1']),'recall_gain_pp':100*(r['recall']-b['recall']),
        'FPR_gain_pp':100*(r['normal_false_positive_rate']-b['normal_false_positive_rate']),
        'FPR_no_increase':r['fp']<=b['fp'],'target_plus_10pp_met':r['f1']>=b['f1']+.1 and r['recall']>=b['recall']+.1 and r['fp']<=b['fp']}for a,r,b in zip(ALGORITHMS,rows,old)]
    write(out/'summary.json',{'pooled':rows,'datasets':datasets,'changes':changes,
        'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),
            'benchmark_modified':False,'point_adjustment':False,'single_estimator_each':True,'adapter_experts':False}})
    print('FINAL',rows,'CHANGES',changes,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--readonly-cache',action='store_true');p.add_argument('--wait-cache',action='store_true')
    args=p.parse_args();evaluate(args.output,args.readonly_cache,args.wait_cache)
