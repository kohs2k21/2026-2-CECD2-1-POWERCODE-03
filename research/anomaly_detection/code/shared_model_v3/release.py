"""Adoption gate: same benchmark, no extra FP, both F1 and Recall improve.

This is explicitly an adaptively reused research benchmark, not a blind test.
No candidate's threshold is altered after reading its benchmark results.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS

BASE=ROOT/'reports/shared_model_v2_release'

def release(out,sources):
    out=Path(out).resolve();sources=[Path(p).resolve()for p in sources];out.mkdir(exist_ok=True)
    if(out/'selection.json').exists():raise FileExistsError('Release already frozen')
    pkg=joblib.load(BASE/'models.joblib');baseline=read(BASE/'summary.json');selected={};pick={};records=[]
    for j,a in enumerate(ALGORITHMS):
        b=baseline['pooled'][j];best=(0.,b['f1'],b['recall']);winner=BASE
        for source in sources:
            sel=read(source/'selection.json');assert sha256(source/'models.joblib')==sel['model_sha256']
            r=read(source/'summary.json')['pooled'][j]
            eligible=r['fp']<=b['fp']and r['f1']>b['f1']and r['recall']>b['recall']
            key=(min(r['f1']-b['f1'],r['recall']-b['recall']),r['f1'],r['recall'])
            records.append({'method':a,'source':str(source.relative_to(ROOT)),'eligible':eligible,
                'F1_gain_pp':100*(r['f1']-b['f1']),'recall_gain_pp':100*(r['recall']-b['recall']),
                'FPR_gain_pp':100*(r['normal_false_positive_rate']-b['normal_false_positive_rate']),**r})
            if eligible and key>best:best=key;winner=source
        pick[a]=winner;sourcepkg=joblib.load(winner/'models.joblib');pkg['models'][a]=sourcepkg['models'][a]
        selected[a]={**read(winner/'selection.json')['selected'][a],
            'source':str(winner.relative_to(ROOT)),'source_model_sha256':sha256(winner/'models.joblib'),
            'retained_previous':winner==BASE,'threshold':pkg['models'][a].threshold}
    # Retain only the feature stages actually required at serving time.
    from shared_model_v3.features import SequenceReference
    from shared_model_v3.adaptive import AdaptiveReference
    from shared_model_v3.sensitive import SensitiveReference
    columns={c for m in pkg['models'].values()for c in m.columns}
    if any(c.startswith(('seq_','small_','rank_','state_','evidence_'))for c in columns):
        pkg['adapter_reference']=SequenceReference(pkg['adapter_reference'])
    if any(c.startswith(('rank_','state_','evidence_'))for c in columns):pkg['adapter_reference']=AdaptiveReference(pkg['adapter_reference'])
    if any(c.startswith('small_')for c in columns):pkg['adapter_reference']=SensitiveReference(pkg['adapter_reference'])
    joblib.dump(pkg,out/'models.joblib',compress=3)
    write(out/'selection.json',{'selected':selected,'model_sha256':sha256(out/'models.joblib'),
        'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),'parent_sha256':sha256(BASE/'models.joblib'),
        'benchmark_modified':False,'thresholds_changed_after_benchmark':False,
        'adoption_gate_uses_previously_viewed_research_benchmark':True,'external_blind_test':False,
        'single_estimator_each':True,'adapter_experts':False})
    write(out/'adoption.json',{'attempts':records,'chosen':{a:str(p.relative_to(ROOT))for a,p in pick.items()},
        'criterion':'No increase in FP; strict increase in F1 AND Recall; else retain old model'})
    all_y=[];all_s=[];datasets=[]
    from shared_model_improvement.run import _stats
    from sklearn.metrics import average_precision_score,roc_auc_score
    for d in baseline['datasets']:
        name=d['dataset'];old=np.load(BASE/(name+'_scores.npz'));columns=[]
        for j,a in enumerate(ALGORITHMS):
            z=np.load(pick[a]/(name+'_scores.npz'))
            for key in('y','case_index','adapter'):np.testing.assert_array_equal(old[key],z[key])
            columns.append(z['scores'][:,j])
        y=old['y'];s=np.column_stack(columns)
        np.savez_compressed(out/(name+'_scores.npz'),y=y,scores=s,case_index=old['case_index'],adapter=old['adapter'])
        metrics=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
            'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':metrics});all_y.append(y);all_s.append(s)
    y,s=np.concatenate(all_y),np.concatenate(all_s)
    rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
        'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
    changes=[{'method':a,'F1_gain_pp':100*(r['f1']-b['f1']),'recall_gain_pp':100*(r['recall']-b['recall']),
        'FPR_gain_pp':100*(r['normal_false_positive_rate']-b['normal_false_positive_rate']),
        'FPR_no_increase':r['fp']<=b['fp'],'target_plus_10pp_met':r['f1']>=b['f1']+.1 and r['recall']>=b['recall']+.1 and r['fp']<=b['fp']}for a,r,b in zip(ALGORITHMS,rows,baseline['pooled'])]
    write(out/'summary.json',{'pooled':rows,'datasets':datasets,'changes':changes,'protocol':baseline['protocol']})
    print('Adopted',selected,'changes',changes,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--sources',type=Path,nargs='+',required=True)
    a=p.parse_args();release(a.output,a.sources)
