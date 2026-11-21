from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v5.protocol import TASKS,ALGORITHMS

OUT=ROOT/'reports/shared_model_v6'
BASE=ROOT/'reports/shared_model_v5/release'

def freeze():
    OUT.mkdir(exist_ok=True)
    payload={'goal':'Maintain Accuracy, Precision and F1; Recall >=60% for each model; aim +5..10pp; normal FPR <=5%',
        'recall_scope_pending_user_reply':'each algorithm pooled; also inspect every task without hiding regressions',
        'active_tasks':list(TASKS),'algorithms':list(ALGORITHMS),'baseline_summary':read(BASE/'summary.json'),
        'baseline_summary_sha256':sha256(BASE/'summary.json'),'baseline_models_sha256':sha256(BASE/'models.joblib'),
        'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'unchanged_task_file_manifests':{t:read(ROOT/'data/evaluation'/t/'manifest.json')for t in TASKS},
        'ADBench_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz'),
        'no_data_labels_or_severity_changes':True,'no_point_adjustment':True,
        'one_shared_estimator_each':True,'no_adapter_experts_or_cross_model_ensemble':True,
        'model_fit_normal_candidates_only':True,'development_labels_select_parameters':True,
        'research_benchmark_reused_adaptively_not_external_blind':True,
        'minimum_maintained_pooled_metrics':['accuracy','precision','f1'],
        'per_task_zero_regression_rejected':True,'previous_five_task_minimum_F1_30pct_and_median_60pct_retained':True,
        'false_positive_cap_applies_to_each_task_and_pooled':.05}
    path=OUT/'protocol.json'
    if path.exists():
        old=read(path)
        for key in ('baseline_summary_sha256','baseline_models_sha256','benchmark_manifest_sha256','ADBench_npz_sha256'):assert old[key]==payload[key]
        return old
    write(path,payload);return payload

if __name__=='__main__':freeze()
