"""Second, conservative development-only calibration policy.

The first ADBench-specific attempt exceeded old FP budgets. Do not invert its
test ROC. Instead, screen existing development settings by an exact one-sided
95% binomial upper bound for normal FPR, plus F1 at the declared 20% anomaly
prevalence. This is adaptive research, not a new blind test.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from scipy.stats import beta
from shared_model_v4.adbench_training import OUT,FLAGS
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats

def upper_bound(fp,n,confidence=.95):return float(beta.ppf(confidence,fp+1,n-fp))if fp<n else 1.
def standardized_f1(r):
    tp=.2*r['recall'];fp=.8*r['normal_false_positive_rate'];fn=.2*(1-r['recall'])
    return 2*tp/max(2*tp+fp+fn,1e-30)

def run():
    dest=OUT/'conservative';dest.mkdir(exist_ok=True)
    if (dest/'selection.json').exists():raise FileExistsError('Conservative policy already frozen')
    caps=read(ROOT/'reports/shared_model_v4/protocol.json')['adbench_false_positive_caps'];selected={}
    for a in ALGORITHMS:
        records=read(OUT/(a+'_search.json'))
        eligible=[r for r in records if upper_bound(r['fp'],r['normal'])<=caps[a]/800]
        r=max(eligible,key=lambda r:(standardized_f1(r),-r['normal_false_positive_rate']))
        base=joblib.load(OUT/(a+'_candidate_'+str(r['candidate'])+'.joblib'));base.threshold=r['threshold']
        joblib.dump(base,dest/(a+'_specific.joblib'),compress=3)
        selected[a]={**r,'development_FPR_upper_95':upper_bound(r['fp'],r['normal']),'prevalence_standardized_F1':standardized_f1(r),'model_sha256':sha256(dest/(a+'_specific.joblib'))}
    write(dest/'selection.json',{'selected':selected,'policy':'95% one-sided exact binomial UCB <= previous FPR budget; F1 standardized to 20% prevalence',
        'test_ROC_or_test_score_quantile_used':False,'previous_failed_attempt_disclosed':True,'adaptive_not_blind':True})
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['case_id']=z['sample_id'];f['adapterType']='ADBENCH_GLOBAL_CONTEXT';rows=[];scores={}
    for a in ALGORITHMS:
        model=joblib.load(dest/(a+'_specific.joblib'));s=model.score(f);scores[a]=s;r=_stats(z['y'],s,model.threshold)
        rows.append({'method':a,**r,'threshold':model.threshold,'F1_target_met':r['f1']>=.6,'FP_cap':caps[a],'FP_cap_met':r['fp']<=caps[a],
            'by_mode':{m:_stats(z['y'][z['generation_mode']==m],s[z['generation_mode']==m],model.threshold)for m in ('global','local','cluster','dependency')}})
    np.savez_compressed(dest/'scores.npz',y=z['y'],sample_id=z['sample_id'],generation_mode=z['generation_mode'],**scores)
    write(dest/'results.json',{'results':rows,'ADBench_specific_retrained':True,'same_service_model_transfer':False,
        'test_points_fit':0,'normal_only_training':True,'fixed_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz')})
    print('Conservative ADBench-specific',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':run()
