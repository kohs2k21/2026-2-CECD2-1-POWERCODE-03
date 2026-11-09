"""Second fixed normal-calibration policy; no inversion of full-test ROC.

First rank attempt exceeded IF/LOF FP budgets. Their new cutoffs are normal
calibration P99.5, fixed before this second evaluation. K-means is unchanged.
Existing scores are reused because only thresholds, not estimators, change.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import copy,argparse
import joblib
import numpy as np
from shared_model_v4.run_rank import OUT as SOURCE,prepare
from shared_model_improvement.run import ALGORITHMS,_stats
from model_research_v13.data import read,write
from model_evaluation.data import sha256

OUT=ROOT/'reports/shared_model_v4_release'

def run(if_quantile=.995,output=None):
    global OUT
    if output is not None:OUT=Path(output).resolve()
    OUT.mkdir(exist_ok=True)
    if (OUT/'selection.json').exists():raise FileExistsError('Release policy frozen')
    d=prepare();pkg=joblib.load(SOURCE/'models.joblib');selected={}
    for a in ALGORITHMS:
        m=pkg['models'][a]
        q=if_quantile if a=='if'else .995
        if a!='kmeans':m.threshold=float(np.quantile(m.score(d['normal_calibration']),q,method='higher'))
        selected[a]={**m.record(),'threshold_policy':f'normal calibration P{q*100:g}'if a!='kmeans'else'unchanged development-selected normal calibration cutoff'}
    joblib.dump(pkg,OUT/'models.joblib',compress=3);protocol=read(ROOT/'reports/shared_model_v4/protocol.json')
    write(OUT/'selection.json',{'model_sha256':sha256(OUT/'models.joblib'),'benchmark_manifest_sha256':protocol['benchmark_manifest_sha256'],
        'models':selected,'protocol':protocol,'normal_threshold_policy_fixed_before_second_evaluation':True,
        'test_quantile_or_ROC_threshold_inversion':False,'prior_failed_FP_attempt':'reports/shared_model_v4_rank',
        'adaptive_research_not_blind':True,'threshold_policy_informed_by_prior_aggregate_evaluation_failures':True,
        'changes':f'IF normal-calibration P{if_quantile*100:g}, LOF P99.5; K-means unchanged'})
    old=read(SOURCE/'summary.json');ys=[];scores=[];datasets=[]
    for dataset in old['datasets']:
        name=dataset['dataset'];z=np.load(SOURCE/(name+'_scores.npz'));y=z['y'];s=z['scores']
        np.savez_compressed(OUT/(name+'_scores.npz'),**{k:z[k]for k in z.files})
        rows=[{'method':a,**_stats(y,s[:,i],pkg['models'][a].threshold)}for i,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':rows});ys.append(y);scores.append(s)
    y=np.concatenate(ys);s=np.concatenate(scores);baseline={r['method']:r for r in protocol['log_baseline']};rows=[]
    for i,a in enumerate(ALGORITHMS):
        r={'method':a,**_stats(y,s[:,i],pkg['models'][a].threshold)};cap=protocol['log_false_positive_caps'][a]
        r.update({'F1_target_met':r['f1']>=.6,'FP_cap':cap,'FP_cap_met':r['fp']<=cap,
            'F1_gain_pp':100*(r['f1']-baseline[a]['f1']),'recall_gain_pp':100*(r['recall']-baseline[a]['recall'])});rows.append(r)
    write(OUT/'summary.json',{'pooled':rows,'datasets':datasets,'protocol':old['protocol'],'selection_sha256':sha256(OUT/'selection.json')})
    print('CONSERVATIVE FULL LOG',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--if-quantile',type=float,default=.995);p.add_argument('--output',type=Path)
    a=p.parse_args();run(a.if_quantile,a.output)
