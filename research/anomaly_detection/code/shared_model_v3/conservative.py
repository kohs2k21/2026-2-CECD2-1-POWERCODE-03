"""A NEW frozen operating policy: fixed normal-calibration P99.5 for K-means.

Previous candidates exceeded the FP budget. We do not invert the benchmark ROC
or choose a cutoff achieving its FP count. The next policy is fixed at P99.5 of
the separate normal-calibration scores, BEFORE its predictions are evaluated.
This remains adaptive research, not an independent test.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from sklearn.metrics import average_precision_score,roc_auc_score

BASE=ROOT/'reports/shared_model_v2_release';SOURCE=ROOT/'reports/shared_model_v3_calibration'
OUT=ROOT/'reports/shared_model_v3_conservative'

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    pkg=joblib.load(SOURCE/'models.joblib');baseline=joblib.load(BASE/'models.joblib')
    dev=joblib.load(ROOT/'reports/shared_model_v3_sensitive/development.joblib');normal=dev['normal_calibration']
    chosen={a:{**read(BASE/'selection.json')['selected'][a],'retained_parent':True}for a in('if','lof')}
    for a in('if','lof'):pkg['models'][a]=baseline['models'][a]
    m=pkg['models']['kmeans'];m.threshold=float(np.quantile(m.score(normal),.995,method='higher'))
    parent_record=read(SOURCE/'selection.json')['selected']['kmeans']
    chosen['kmeans']={**{k:parent_record[k]for k in('algorithm','source','mode','alpha','kind')},'q':.995,'threshold':m.threshold,
        'development_after_policy':{k:_stats(dev[k]['y'],m.score(dev[k]['frame']),m.threshold)for k in('search','confirmation')},
        'policy':'fixed separate-normal P99.5 safety margin; no benchmark ROC inversion'}
    joblib.dump(pkg,OUT/'models.joblib',compress=3)
    write(OUT/'selection.json',{'selected':chosen,'model_sha256':sha256(OUT/'models.joblib'),
        'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),'parent_sha256':sha256(BASE/'models.joblib'),
        'prior_candidate_false_alarms_informed_conservative_design':True,'normal_only_numeric_cutoff':True,
        'threshold_frozen_before_candidate_evaluation':True,'single_estimator_each':True,'adapter_experts':False,'benchmark_modified':False})
    # Scoring is identical to frozen SOURCE for KM and BASE for IF/LOF.
    # Only the newly frozen normal-only cutoff is applied, no extra feature work.
    all_y=[];all_s=[];datasets=[]
    for d in read(BASE/'summary.json')['datasets']:
        name=d['dataset'];old=np.load(BASE/(name+'_scores.npz'));new=np.load(SOURCE/(name+'_scores.npz'))
        for k in('y','case_index','adapter'):np.testing.assert_array_equal(old[k],new[k])
        y=old['y'];s=old['scores'].copy();s[:,1]=new['scores'][:,1]
        np.savez_compressed(OUT/(name+'_scores.npz'),y=y,scores=s,case_index=old['case_index'],adapter=old['adapter'])
        rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
            'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':rows});all_y.append(y);all_s.append(s)
    y,s=np.concatenate(all_y),np.concatenate(all_s)
    rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
        'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
    old=read(BASE/'summary.json')['pooled']
    changes=[{'method':a,'F1_gain_pp':100*(r['f1']-b['f1']),'recall_gain_pp':100*(r['recall']-b['recall']),
        'FPR_gain_pp':100*(r['normal_false_positive_rate']-b['normal_false_positive_rate']),
        'FPR_no_increase':r['fp']<=b['fp'],'target_plus_10pp_met':r['f1']>=b['f1']+.1 and r['recall']>=b['recall']+.1 and r['fp']<=b['fp']}for a,r,b in zip(ALGORITHMS,rows,old)]
    write(OUT/'summary.json',{'pooled':rows,'datasets':datasets,'changes':changes,'protocol':read(BASE/'summary.json')['protocol']})
    print('Fixed normal P99.5',m.threshold,rows[1],changes[1],flush=True)

if __name__=='__main__':run()
