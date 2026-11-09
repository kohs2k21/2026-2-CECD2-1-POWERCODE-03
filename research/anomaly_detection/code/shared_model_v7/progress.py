"""Read completed candidate summaries without presenting them as a release."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v7.protocol import OUT,BASE,freeze

def run():
    protocol=freeze();stages={}
    for folder in sorted(OUT.iterdir()):
        if not folder.is_dir():continue
        rows=[]
        for p in folder.glob('*_selection.json'):
            try:
                data=read(p);r=data.get('chosen',data.get('best'))
                if not r:continue
                rows.append({key:r.get(key)for key in('algorithm','candidate','source','minimum_task_recall','maximum_task_FPR','maintains_pooled',
                    'maintains_each_task_accuracy_precision_F1','probe_targets_met','pooled')})
            except(ValueError,OSError):continue
        if rows:stages[folder.name]=rows
    checks={
        'v6_models_unchanged':sha256(BASE/'models.joblib')==protocol['baseline_models_sha256'],
        'v6_summary_unchanged':sha256(BASE/'summary.json')==protocol['baseline_summary_sha256'],
        'benchmark_manifest_unchanged':sha256(ROOT/'data/evaluation/manifest.json')==protocol['benchmark_manifest_sha256'],
        'ADBench_unchanged':sha256(ROOT/'data/adbench_1000/dataset.npz')==protocol['ADBench_npz_sha256']}
    assert all(checks.values())
    attempts={}
    for folder in sorted(OUT.glob('attempt*')):
        if not folder.is_dir():continue
        one={a:read(folder/(a+'_summary.json'))for a in ('if','kmeans','lof')if(folder/(a+'_summary.json')).exists()}
        attempts[folder.name]={'completed_individual_algorithms':list(one),
            'algorithm_metric_targets_met':{a:x['all_metric_targets_met']for a,x in one.items()},
            'full_three_model_summary_exists':(folder/'summary.json').exists(),
            'independent_audit_exists':(folder/'audit.json').exists(),'published':False}
    released=(OUT/'release/audit.json').exists()and(OUT/'release/report.json').exists()
    released=released and read(OUT/'release/audit.json').get('all_targets_met',False)and read(OUT/'release/report.json').get('executed',False)
    write(OUT/'progress.json',{'state':'local_research_release_completed'if released else'research_in_progress_NOT_a_completed_release','confirmed_scope':'each task R>=55 and each task Accuracy/Precision/F1 >=v6; latest explicit user FPR cap <=6% each task and pooled',
        'unchanged_baselines_and_data':checks,'stages':stages,'full_evaluation_of_adopted_model_done':bool(released),
        'v6_release_replaced':False,'new_local_v7_release_available':bool(released),'production_deployed':False,'no_final_metrics_claimed':not released,'full_attempts':attempts,
        'warning':'Some earlier probe flags precede strict per-task scope; never use flags as final evidence. Final acceptance must re-evaluate all 5000 cases/task.'})
    print('PROGRESS saved:',len(stages),'experiment families; baseline/data hashes intact; local release available =',bool(released),flush=True)

if __name__=='__main__':run()
