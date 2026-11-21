from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code'))
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v5.protocol import TASKS,ALGORITHMS

OUT=ROOT/'reports/shared_model_v7'
BASE=ROOT/'reports/shared_model_v6/release'

def atomic_dump(value,path):
    import os,tempfile,joblib
    path=Path(path);path.parent.mkdir(exist_ok=True,parents=True)
    with tempfile.NamedTemporaryFile(prefix=path.name+'.',suffix='.tmp',dir=path.parent,delete=False)as stream:temporary=Path(stream.name)
    joblib.dump(value,temporary,compress=3)
    os.replace(temporary,path)

def freeze():
    OUT.mkdir(exist_ok=True)
    payload={'objective':'Each model/task Recall >=55%, per-task Accuracy/Precision/F1 non-regression, FPR <=6%, and real distinct model strengths',
        'recall_scope':'each of the five tasks, for each of IF/K-means/LOF',
        'other_metrics_scope':'CONFIRMED: each model AND each task Accuracy/Precision/F1 non-regression; latest user relaxed FPR only to <=6%',
        'hard_FPR_nonincrease_each_task_and_pooled':False,
        'FPR_scope_user_confirmed_20261019':'each model/task FPR <= its unchanged v6 baseline, not merely below 5%',
        'latest_FPR_relaxation_20261019':'너무 어렵다면 오탐률 6% 이하 정도면 될 거 같아',
        'maximum_FPR':.06,'maximum_FPR_inclusive':True,
        'strict_each_task_metrics':True,
        'minimum_recall':.55,
        'nonregression_metrics':['accuracy','precision','recall','f1'],
        'active_tasks':list(TASKS),'algorithms':list(ALGORITHMS),
        'baseline_summary':read(BASE/'summary.json'),
        'baseline_summary_sha256':sha256(BASE/'summary.json'),
        'baseline_models_sha256':sha256(BASE/'models.joblib'),
        'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'task_manifests':{t:read(ROOT/'data/evaluation'/t/'manifest.json')for t in TASKS},
        'ADBench_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz'),
        'no_labels_rows_severity_or_task_changes':True,'no_point_adjustment':True,
        'normal_only_estimator_fit':True,'one_shared_estimator_each':True,
        'no_adapter_experts_or_cross_algorithm_ensemble':True,
        'adaptive_research_benchmark_reused_not_external_blind_test':True,
        'roles_must_be_observed_not_forced':True,
        'requested_role_preferences':{'if':'individual_delay','kmeans':'group_delay','lof':'gradual_degradation'},
        'role_preference_scope':'prefer F1 only after all task-wise constraints; never weaken another model to manufacture a lead'}
    path=OUT/'protocol.json'
    if path.exists():
        prior=read(path)
        for k in ('baseline_summary_sha256','baseline_models_sha256','benchmark_manifest_sha256','ADBench_npz_sha256'):assert prior[k]==payload[k],k
        if prior.get('hard_FPR_nonincrease_each_task_and_pooled',True):
            prior.setdefault('superseded_acceptance_conditions',[]).append({
                'reason':'explicit user relaxation on 2026-10-19, no model/data/metric changes',
                'FPR_nonincrease':True,'FPR_strictly_below':prior.get('FPR_strictly_below',.05)})
        keys=('objective','other_metrics_scope','strict_each_task_metrics','requested_role_preferences','role_preference_scope',
              'hard_FPR_nonincrease_each_task_and_pooled','FPR_scope_user_confirmed_20261019',
              'latest_FPR_relaxation_20261019','maximum_FPR','maximum_FPR_inclusive')
        prior.update({k:payload[k]for k in keys})
        prior.pop('FPR_strictly_below',None)
        prior.pop('other_metrics_scope_pending_user_confirmation',None)
        if(OUT/'point_full_diagnostic.json').exists():prior['full_point_feature_diagnostic_labels_also_inspected']=True
        write(path,prior)
        return prior
    write(path,payload)
    return payload

if __name__=='__main__':freeze()
