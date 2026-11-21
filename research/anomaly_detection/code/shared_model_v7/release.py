"""Publish a LOCAL research handoff only after independent complete gates.

No Git/Notion/deployment actions. No overwrite of any earlier release.
This is an adaptively selected benchmark result, never an external blind test.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,importlib.metadata,shutil
import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from model_research_v13.data import read,write
from model_evaluation.data import sha256,verify_benchmark
from shared_model_target.inference import Monitor
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.acceptance import check,policy
from shared_model_v7.role_priority import analyze
from shared_model_v7.evaluate import atomic_npz


def estimators(value):
    """Count unique learned FAULT estimators, not aliases or lookup trees."""
    pending=[value];seen=set();found=[]
    while pending:
        x=pending.pop()
        if id(x)in seen:continue
        seen.add(id(x))
        if isinstance(x,(IsolationForest,MiniBatchKMeans,LocalOutlierFactor)):
            found.append(x);continue
        if isinstance(x,dict):pending.extend(x.values())
        elif isinstance(x,(tuple,list)):pending.extend(x)
        elif type(x).__module__.startswith(('shared_model','model_research','adapter_feature')):
            pending.extend(vars(x).values())
    return found


def run(source):
    protocol=freeze();source=Path(source).resolve();dest=OUT/'release'
    if not source.is_relative_to(OUT.resolve())or source==dest.resolve():raise ValueError('Audited v7 attempt required')
    if dest.exists():raise FileExistsError('Existing research release preserved')
    audit=read(source/'audit.json');summary=read(source/'audited_summary.json')
    assert audit['independent_metrics_recomputed']and audit['all_targets_met']and summary['all_targets_met']
    assert all(all(r[k]for k in ('raw_Process_predict','prefix_causal','unknown_adapter_finite',
                                'save_load_identical','no_runtime_data_reads'))for r in audit['raw_serving_smoke_checks'])
    assert check(summary,protocol['baseline_summary'],**policy(protocol))['all_targets_met']
    assert analyze(summary)['all_desired_models_observed_best']
    assert sha256(BASE/'models.joblib')==protocol['baseline_models_sha256']
    assert sha256(BASE/'summary.json')==protocol['baseline_summary_sha256']
    assert sha256(ROOT/'data/adbench_1000/dataset.npz')==protocol['ADBench_npz_sha256']
    assert sha256(ROOT/'data/evaluation/manifest.json')==protocol['benchmark_manifest_sha256']
    for t in TASKS:assert read(ROOT/'data/evaluation'/t/'manifest.json')==protocol['task_manifests'][t]
    verify_benchmark(ROOT/'data/evaluation',ROOT/'reports/evaluation_v2_work/inputs.json')
    print('All benchmark file hashes verified; original data unchanged',flush=True)
    models={};records={}
    expected={'if':IsolationForest,'kmeans':MiniBatchKMeans,'lof':LocalOutlierFactor}
    for a in ALGORITHMS:
        receipt=read(source/(a+'_selection.json'));path=source/(a+'_model.joblib')
        assert sha256(path)==receipt['model_sha256']
        m=joblib.load(path);cores=estimators(m)
        assert len(cores)==1 and isinstance(cores[0],expected[a])
        assert m.record()['estimator_count']==1 and not m.record()['adapter_experts']
        assert float(m.threshold)==1.
        models[a]=m
        records[a]={'model_source':str(path.relative_to(ROOT)),'source_sha256':sha256(path),
            'actual_unique_fault_estimators':len(cores),'actual_estimator_features':int(cores[0].n_features_in_),
            'record':m.record()}
    base=joblib.load(BASE/'models.joblib');base['models']=models;base.pop('alternative_IF',None)
    dest.mkdir();atomic_dump(base,dest/'models.joblib');(dest/'handoff').mkdir()
    for t in TASKS:
        values=[np.load(source/(a+'_'+t+'_scores.npz'))for a in ALGORITHMS]
        for z in values[1:]:
            for k in ('y','case_index','adapter'):np.testing.assert_array_equal(z[k],values[0][k])
        atomic_npz(dest/(t+'_scores.npz'),scores=np.column_stack([z['scores']for z in values]),
                   **{k:values[0][k]for k in ('y','case_index','adapter')})
    handoff=[]
    for a,m in models.items():
        path=dest/'handoff'/(a+'_shared.joblib');atomic_dump(Monitor(m,base['adapter_reference'],base['feature_context']),path)
        handoff.append({'algorithm':a,'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path),
                        'threshold':m.threshold,**records[a]})
    summary['protocol']={'decisions':sum(d['metrics'][0]['n']for d in summary['datasets']),
        'normal':sum(d['metrics'][0]['normal']for d in summary['datasets']),
        'anomaly':sum(d['metrics'][0]['anomaly']for d in summary['datasets']),
        'all_5000_paired_cases_each_task':True,'normal_only_estimator_fit':True,
        'normal_only_fit_does_not_guarantee_clean_source':True,'benchmark_labels_used_for_calibration_selection':True,
        'unchanged_rows_labels_severity':True,'no_point_adjustment':True,'no_future_or_truth_routing':True,
        'single_estimator_each':True,'adapter_experts':False,'external_blind_test':False}
    write(dest/'summary.json',summary)
    write(dest/'selection.json',{'source_attempt':str(source.relative_to(ROOT)),
        'source_audit_sha256':sha256(source/'audit.json'),'model_sha256':sha256(dest/'models.joblib'),
        'baseline_summary_sha256':protocol['baseline_summary_sha256'],'models':records,
        'acceptance_policy':policy(protocol),'policy_relaxation_explicitly_requested_by_user':True,
        'no_test_rows_fitted_to_fault_estimator':True,'development_labels_used_for_selection':True,'external_blind_test':False})
    write(dest/'handoff/manifest.json',{'models':handoff,'versions':{n:importlib.metadata.version(n)for n in ('numpy','pandas','scipy','scikit-learn','joblib')},
        'requires_code_tree':True,'production_deployed':False,'stateful_worker_implemented':False,
        'input':'chronological contiguous minute grid + completed Process rows + retained past prefix',
        'worker_must_supply_empty_minutes_and_state':True,'Transaction_input_required':False,
        'not_bare_IF_KMeans_LOF':'normal reference/sequence evidence and development-calibrated thresholds are part of each pipeline',
        'unknown_adapter':'same estimator frozen original score/threshold; no expert fallback model',
        'runtime_reads_benchmark_or_original_CSV':False,'external_blind_test':False})
    for name in ('audit.json','assembly.json'):
        if(source/name).exists():shutil.copy2(source/name,dest/('source_'+name))
    write(dest/'audit.json',{**audit,'published_local_research_handoff':True,
        'all_benchmark_file_hashes_verified':True,'ADBench_and_v6_unchanged':True,
        'one_unique_fault_estimator_each_verified':True,'model_sha256':sha256(dest/'models.joblib'),
        'summary_sha256':sha256(dest/'summary.json'),'production_deployed':False})
    print('Local v7 research handoff prepared; notebook/final audit still required',dest,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);run(p.parse_args().attempt)
