"""Frozen selection, unchanged full test, exact per-algorithm FP budgets."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v4.run_rank import OUT
from shared_model_v4.rank_context import RANK

def freeze():
    path=OUT/'models.joblib'
    if path.exists():assert sha256(path)==read(OUT/'selection.json')['model_sha256'];return
    models={a:joblib.load(OUT/(a+'_selected.joblib'))for a in ALGORITHMS}
    old=joblib.load(ROOT/'reports/shared_model_v3_release/models.joblib')
    joblib.dump({**old,'models':models},path,compress=3)
    write(OUT/'selection.json',{'model_sha256':sha256(path),'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'selected_on':'separate search and confirmation episodes; test labels not used in selection',
        'models':{a:models[a].record()for a in ALGORITHMS},'protocol':read(ROOT/'reports/shared_model_v4/protocol.json'),
        'new_test_rows_fit':0,'thresholds_frozen_before_full_test':True})

def run():
    freeze();selection=read(OUT/'selection.json');pkg=joblib.load(OUT/'models.joblib');models=pkg['models']
    source=ROOT/'reports/shared_model_v3/feature_cache'/selection['benchmark_manifest_sha256'][:16]
    base=ROOT/'reports/shared_model_v2/feature_cache'/selection['benchmark_manifest_sha256'][:16]
    assert selection['benchmark_manifest_sha256']==sha256(ROOT/'data/evaluation/manifest.json')
    results=[];ys=[];scores=[];names=read(ROOT/'data/evaluation/manifest.json')['datasets']
    for name in names:
        dest=OUT/(name+'_scores.npz')
        if dest.exists():
            saved=np.load(dest);y=saved['y'];s=saved['scores']
        else:
            yy=[];ss=[];ids=[];adapters=[]
            for ei,e in enumerate(read(base/name/'index.json')):
                path=source/name/e['file'];assert sha256(path)==read(path.with_suffix('.json'))['sha256']
                payload=joblib.load(path);f=payload['frame'];m=payload['metadata']
                # Context settings are identical in this frozen search. Compute
                # once per full independent case, not once per algorithm.
                contexts={models[a].base.context.prefix for a in ALGORITHMS}
                if len(contexts)==1:f=models['if'].base.derive(f)
                yy.append(np.asarray(m['truth'],bool));ss.append(np.column_stack([models[a].score(f)for a in ALGORITHMS]))
                ids.append(m['case_index']);adapters.append(f.adapterType.to_numpy(str))
                print(name,ei+1,flush=True)
            y=np.concatenate(yy);s=np.concatenate(ss)
            np.savez_compressed(dest,y=y,scores=s,case_index=np.concatenate(ids),adapter=np.concatenate(adapters))
        rows=[{'method':a,**_stats(y,s[:,i],models[a].threshold)}for i,a in enumerate(ALGORITHMS)]
        results.append({'dataset':name,'metrics':rows});ys.append(y);scores.append(s);write(OUT/'evaluation_progress.json',{'datasets':results})
        print(name,[(r['method'],round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,2))for r in rows],flush=True)
    y=np.concatenate(ys);s=np.concatenate(scores);protocol=read(ROOT/'reports/shared_model_v4/protocol.json');caps=protocol['log_false_positive_caps']
    old={r['method']:r for r in protocol['log_baseline']};rows=[]
    for i,a in enumerate(ALGORITHMS):
        r={'method':a,**_stats(y,s[:,i],models[a].threshold)}
        r.update({'F1_target_met':r['f1']>=.6,'FP_cap':caps[a],'FP_cap_met':r['fp']<=caps[a],
            'F1_gain_pp':100*(r['f1']-old[a]['f1']),'recall_gain_pp':100*(r['recall']-old[a]['recall'])});rows.append(r)
    write(OUT/'summary.json',{'pooled':rows,'datasets':results,'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),
        'benchmark_modified':False,'point_adjustment':False,'single_estimator_each':True,'adapter_experts':False},'selection_sha256':sha256(OUT/'selection.json')})
    print('FULL TEST',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':run()
