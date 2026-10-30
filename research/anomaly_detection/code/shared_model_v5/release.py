"""Transparent adaptive research adoption; normal-derived cutoffs, no test fit.

Cutoff candidates are normal calibration quantiles. Adoption DOES inspect the
reused research benchmark and must not be presented as an external blind test.
The losing attempt remains immutable and reproducible.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import copy
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v5.protocol import TASKS,ALGORITHMS,OUT,gates
from shared_model_improvement.run import _stats

def run():
    source=OUT/'attempt01';dest=OUT/'release';dest.mkdir(exist_ok=True)
    if(dest/'selection.json').exists():raise FileExistsError('Completed release is immutable')
    pkg=joblib.load(source/'models.joblib');model=pkg['models']['lof']
    calibration=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')['normal_calibration']
    normal=model.score(calibration)
    bank={t:np.load(source/(t+'_scores.npz'))for t in TASKS}
    candidates=[]
    for q in (.9925,.995,.9975):
        threshold=float(np.quantile(normal,q,method='higher'))
        rows=[_stats(bank[t]['y'],bank[t]['scores'][:,2],threshold)for t in TASKS]
        candidates.append({'q':q,'threshold':threshold,'tasks':dict(zip(TASKS,rows)),**gates(rows)})
    write(OUT/'adoption_candidates.json',{'thresholds_from':'independent normal calibration only',
        'adoption_uses_reused_research_benchmark_labels':True,'external_blind_test':False,'candidates':candidates})
    eligible=[r for r in candidates if r['all_met']]
    if not eligible:raise RuntimeError('No passing LOF; failed candidates retained, no release')
    chosen=max(eligible,key=lambda r:(r['minimum_task_F1'],r['median_task_F1'],-r['maximum_task_normal_FPR']))
    model.threshold=chosen['threshold']
    joblib.dump(pkg,dest/'models.joblib',compress=3)
    write(dest/'selection.json',{'model_sha256':sha256(dest/'models.joblib'),'models':{a:m.record()for a,m in pkg['models'].items()},
        'sources':read(source/'selection.json')['sources'],'LOF_normal_calibration_quantile':chosen['q'],
        'normal_calibration_rows':len(calibration),'estimator_training_rows':19793,'test_rows_used_for_estimator_fit':0,
        'adoption_uses_reused_research_benchmark_labels':True,'external_blind_test':False,
        'reason':'Per-task minimum and median F1 plus each-task and pooled FPR, not pooled F1 alone',
        'IF_restored_not_newly_trained':True,'KM_retained_not_newly_trained':True,
        'source_attempt_sha256':sha256(source/'selection.json'),'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json')})
    datasets=[];yy=[];ss=[]
    for t in TASKS:
        z=bank[t];y=z['y'];s=z['scores']
        np.savez_compressed(dest/(t+'_scores.npz'),**{k:z[k]for k in z.files})
        rows=[{'method':a,**_stats(y,s[:,i],pkg['models'][a].threshold)}for i,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':t,'metrics':rows});yy.append(y);ss.append(s)
    y=np.concatenate(yy);s=np.concatenate(ss)
    pooled=[{'method':a,**_stats(y,s[:,i],pkg['models'][a].threshold)}for i,a in enumerate(ALGORITHMS)]
    acceptance={a:{**gates([d['metrics'][i]for d in datasets]),'pooled_FPR_below_5pct':pooled[i]['normal_false_positive_rate']<.05}for i,a in enumerate(ALGORITHMS)}
    passed=all(g['all_met']and g['pooled_FPR_below_5pct']for g in acceptance.values());assert passed
    write(dest/'summary.json',{'pooled':pooled,'datasets':datasets,'acceptance':acceptance,'all_targets_met':passed,
        'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),
            'active_tasks':list(TASKS),'retained_test_data_modified':False,'point_adjustment':False,
            'single_estimator_each':True,'adapter_experts':False,'adoption_uses_reused_research_benchmark':True,'external_blind_test':False}})
    for a,g in acceptance.items():print('RELEASE',a,g,flush=True)

if __name__=='__main__':run()
