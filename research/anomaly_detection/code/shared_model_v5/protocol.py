"""User-authorized five-task scope and per-model, per-task acceptance gates."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256

TASKS=('individual_delay','group_delay','time_burst','input_stop','gradual_degradation')
RETIRED=('column_relation','mixed_hard')
ALGORITHMS=('if','kmeans','lof')
OUT=ROOT/'reports/shared_model_v5'

def gates(rows):
    if len(rows)!=len(TASKS):raise ValueError('Exactly five task rows required')
    f1=np.array([r['f1']for r in rows]);fpr=np.array([r['normal_false_positive_rate']for r in rows])
    return {'minimum_task_F1':float(f1.min()),'median_task_F1':float(np.median(f1)),
        'maximum_task_normal_FPR':float(fpr.max()),'minimum_F1_met':bool(f1.min()>=.3),
        'median_F1_met':bool(np.median(f1)>=.6),'all_task_FPR_below_5pct':bool((fpr<.05).all()),
        'all_met':bool(f1.min()>=.3 and np.median(f1)>=.6 and (fpr<.05).all())}

def freeze():
    OUT.mkdir(exist_ok=True)
    manifest=read(ROOT/'data/evaluation/manifest.json')
    current={'active_tasks':list(TASKS),'retired_tasks':list(RETIRED),'algorithms':list(ALGORITHMS),
        'minimum_each_task_F1':.3,'minimum_median_task_F1':.6,'maximum_normal_FPR_exclusive':.05,
        'normal_FPR_required_for_each_task_and_pooled':True,
        'median_is_across_five_task_F1_for_each_algorithm_not_across_models':True,
        'removed_tasks_not_counted_in_training_selection_evaluation_or_current_reports':True,
        'remaining_test_rows_labels_or_difficulty_changed':False,'point_adjustment':False,
        'ADBench_not_changed':True,'ADBench_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz'),
        'parent_benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'parent_feature_cache_hash_prefix':'4f5469a098b82372',
        'source_five_task_file_manifests':{name:read(ROOT/'data/evaluation'/name/'manifest.json')for name in TASKS},
        'adaptive_reused_research_benchmark_not_external_blind':True}
    path=OUT/'protocol.json'
    if path.exists():
        old=read(path)
        for k in ('active_tasks','retired_tasks','minimum_each_task_F1','minimum_median_task_F1','maximum_normal_FPR_exclusive'):
            assert old[k]==current[k]
        return old
    write(path,current)
    baseline=[]
    for release in ('shared_model_v3_release','shared_model_v4_final'):
        data=read(ROOT/'reports'/release/'summary.json');by_task={d['dataset']:d for d in data['datasets']}
        for j,a in enumerate(ALGORITHMS):
            rows=[by_task[k]['metrics'][j]for k in TASKS];baseline.append({'release':release,'method':a,**gates(rows),
                'tasks':{k:by_task[k]['metrics'][j]for k in TASKS}})
    write(OUT/'baseline_five_tasks.json',baseline)
    for b in baseline:print(b['release'],b['method'],[(k,round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,2))for k,r in b['tasks'].items()],gates(list(b['tasks'].values())),flush=True)
    return current

if __name__=='__main__':freeze()
