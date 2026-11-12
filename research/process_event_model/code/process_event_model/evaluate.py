"""Frozen held-case execution metrics and common minute-grid comparison.

No point adjustment, no carrying an alert into empty minutes, and no fabricated
Process observations. Input-stop has no positive execution rows: row F1 is null.
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .data import ROOT, TASKS, write_json, digest
from .models import metrics
from .run import OUT, features_for_benchmark, evaluated_rows
from .scope import evaluation_tasks, EXCLUDED_TASKS


ALGORITHMS = ("if", "kmeans", "lof")


def window_predictions(task, pair, cases, predictions):
    """Actual execution decisions mapped to the minute they become available.

    Only the same target Adapter+Process is mapped, matching legacy scope.
    Zero-completion windows receive no alarm. This is a diagnostic, not a claim
    that a Process classifier monitors absent executions.
    """
    labels = pd.read_csv(ROOT/"data/evaluation"/task/"window_labels.csv", encoding="utf-8-sig")
    labels = labels.loc[labels.scope.eq("TARGET") & labels.case_id.isin(cases.case_id)].copy()
    y = labels.window_is_anomaly.to_numpy(bool)
    unit_results = {}
    for a in ALGORITHMS:
        truth, new, old = [], [], []
        for cohort in ("observed", "normal"):
            f = pair[cohort]
            mask = f.target.to_numpy(bool)
            clock = f.availableTime.dt.tz_convert("Asia/Seoul").dt.tz_localize(None).dt.floor("min")
            hits = pd.DataFrame(dict(case_id=f.case_id.to_numpy()[mask], minute=clock.to_numpy()[mask],
                                    alarm=predictions[cohort][a][mask]))
            grouped = hits.groupby(["case_id", "minute"], observed=True).alarm.max()
            index = pd.MultiIndex.from_arrays([labels.case_id, pd.to_datetime(labels.window_start, format="ISO8601")])
            alarm = grouped.reindex(index, fill_value=False).to_numpy(bool)
            truth.append(y if cohort == "observed" else np.zeros(len(y), bool))
            new.append(alarm)
        unit_results[a] = metrics(np.concatenate(truth), np.concatenate(new))
    return unit_results


def observability(task,pair,cases):
    labels=pd.read_csv(ROOT/'data/evaluation'/task/'window_labels.csv',encoding='utf-8-sig')
    labels=labels.loc[labels.scope.eq('TARGET') & labels.case_id.isin(cases.case_id)]
    f=pair['observed'].loc[pair['observed'].target]
    clock=f.availableTime.dt.tz_convert('Asia/Seoul').dt.tz_localize(None).dt.floor('min')
    observed=pd.MultiIndex.from_arrays([f.case_id,clock]).unique()
    index=pd.MultiIndex.from_arrays([labels.case_id,pd.to_datetime(labels.window_start,format='ISO8601')])
    has_row=index.isin(observed)
    y=labels.window_is_anomaly.to_numpy(bool)
    reachable=int((has_row & y).sum());positives=int(y.sum())
    return dict(anomaly_minutes=positives,anomaly_minutes_with_original_target_completion=reachable,
                no_target_completion_anomaly_minutes=positives-reachable,
                row_only_minute_recall_upper_bound=reachable/max(positives,1),
                oracle_only_not_a_predictor=True,
                explanation='Even a perfect execution-row detector cannot issue a fresh decision in an empty minute without a separate clock/state observation')


def evaluate(development_cases=400, folder=None, limit=None, include_input_stop=False):
    output = Path(folder) if folder else OUT
    suffix = f"_{limit}" if limit else ""
    model_path = output/"event_models.joblib"
    frozen = digest(model_path)
    package = joblib.load(model_path)
    report = dict(observation_unit="original_completed_Process_row", model_sha256=frozen,
                  development_cases_per_task=development_cases,
                  final_cases_per_task=limit or 5000-development_cases, unchanged_benchmark=True,
                  external_blind_test=False, no_point_adjustment=True,
                  active_tasks=list(evaluation_tasks(include_input_stop)),
                  excluded_tasks={} if include_input_stop else EXCLUDED_TASKS,
                  models={a:dict(settings=vars(m.settings), columns=list(m.columns), threshold=m.threshold,
                                 fit_rows=m.fit_rows) for a,m in package["models"].items()},
                  tasks={}, pooled={})
    known_adapters = {key[0] for cols, table in package["reference"].tables
                      if cols == ("adapterType",) for key in table}
    pooled = {a: dict(y=[], prediction=[]) for a in ALGORITHMS}
    original_reference = package["reference"]
    reference_signature = __import__('hashlib').sha256(joblib.hash(original_reference).encode()).hexdigest()[:12]
    legacy_feature_folder=ROOT/'reports/process_event_model/confirmation_features'/reference_signature
    processor_kind=package.get('processor')
    if processor_kind=='predictive_row_context':
        processing_sha=digest(ROOT/'code/process_event_model/predictive.py')[:12]
        feature_folder=legacy_feature_folder.parent/(reference_signature+'-predictive-'+processing_sha)
        report['feature_processor']=dict(name=processor_kind,code_sha256=processing_sha)
    else:
        feature_folder=legacy_feature_folder
    feature_folder.mkdir(parents=True,exist_ok=True)
    for task in evaluation_tasks(include_input_stop):
        feature_path = feature_folder/f"{task}_{development_cases}{suffix}.joblib"
        if feature_path.exists():
            pair, cases = joblib.load(feature_path)
        else:
            old_cache=legacy_feature_folder/f"{task}_{development_cases}{suffix}.joblib"
            if processor_kind=='predictive_row_context' and old_cache.exists():
                from .predictive import PredictiveHistory
                previous,cases=joblib.load(old_cache)
                pair={c:PredictiveHistory().transform(f) for c,f in previous.items()}
                for c in pair:
                    assert len(pair[c])==len(previous[c])
                    np.testing.assert_array_equal(pair[c].event_row_id,previous[c].event_row_id)
            else:
                pair, cases = features_for_benchmark(package["reference"], task, limit=limit, offset=development_cases,
                                                    processor_kind=processor_kind)
            joblib.dump((pair, cases), feature_path, compress=3)
        predictions = {}
        task_report = dict(cases=len(cases), observed_rows=len(pair["observed"]), normal_rows=len(pair["normal"]),
                           metrics={}, input_stop_no_positive_Process_rows=task == "input_stop")
        for cohort in ("observed", "normal"):
            predictions[cohort] = {}
            for a, model in package["models"].items():
                score, alarm = model.predict(pair[cohort])
                predictions[cohort][a] = alarm
                np.savez_compressed(output/f"{task}_{cohort}_{a}_scores{suffix}.npz", score=score,
                    label=pair[cohort].label.to_numpy(bool), event_row_id=pair[cohort].event_row_id.to_numpy(),
                    case_id=pair[cohort].case_id.to_numpy(str),target=pair[cohort].target.to_numpy(bool))
        y = np.concatenate([pair[c].label.to_numpy(bool) for c in ("observed", "normal")])
        target = np.concatenate([pair[c].target.to_numpy(bool) for c in ("observed", "normal")])
        task_report["all_rows_secondary_metrics"] = {}
        for a in ALGORITHMS:
            alarm = np.concatenate([predictions[c][a] for c in ("observed", "normal")])
            task_report["all_rows_secondary_metrics"][a] = metrics(y,alarm)
            m = metrics(y[target], alarm[target])
            if task == "input_stop":
                m.update(precision=None, recall=None, f1=None,
                         explanation="No missing execution is a Process row; only alert fraction on observed rows is measurable")
            else:
                pooled[a]["y"].append(y[target])
                pooled[a]["prediction"].append(alarm[target])
            task_report["metrics"][a] = m
        task_report["adapter_support"] = {}
        for name, known in (("seen_adapter", True), ("new_adapter", False)):
            masks = [pair[c].adapterType.isin(known_adapters).to_numpy() == known
                     for c in ("observed", "normal")]
            mask = np.concatenate(masks)
            if task != "input_stop":
                task_report["adapter_support"][name] = {a:metrics(y[mask], np.concatenate(
                    [predictions[c][a] for c in ("observed", "normal")])[mask]) for a in ALGORITHMS}
        task_report["common_minute_grid_diagnostic"] = window_predictions(task, pair, cases, predictions)
        task_report['observability']=observability(task,pair,cases)
        legacy = np.load(ROOT/"reports/shared_model_v7/release"/(task+"_scores.npz"))
        # Historical case_index is verified against cache metadata in tests/audit.
        held = legacy["case_index"] >= development_cases
        if limit: held &= legacy["case_index"] < development_cases+limit
        task_report["legacy_held_case_minute_metrics"] = {a:metrics(legacy["y"][held],
            legacy["scores"][held, i] > 1.) for i,a in enumerate(ALGORITHMS)}
        for a in ALGORITHMS:
            assert task_report["common_minute_grid_diagnostic"][a]["n"] == task_report["legacy_held_case_minute_metrics"][a]["n"]
            assert task_report["common_minute_grid_diagnostic"][a]["positives"] == task_report["legacy_held_case_minute_metrics"][a]["positives"]
        report["tasks"][task] = task_report
        write_json(output/f"confirmation_progress{suffix}.json", report)
        print("CONFIRMATION", task, {a:{k:task_report["metrics"][a][k] for k in ("precision","recall","f1","false_positive_rate")}
                                     for a in ALGORITHMS}, flush=True)
    for a, p in pooled.items():
        report["pooled"][a] = metrics(np.concatenate(p["y"]), np.concatenate(p["prediction"]))
    assert digest(model_path) == frozen, "Models changed during confirmation"
    report["legacy_minute_results_not_directly_comparable_to_row_metrics"] = True
    source_features = ROOT/'reports/process_event_model/row_context_v2/original_features.joblib'
    if processor_kind=='predictive_row_context':
        source_features=ROOT/'reports/process_event_model/predictive_counts/original_features.joblib'
    if source_features.exists() and package['reference'].__class__.__name__ == 'ExactReference':
        original = joblib.load(source_features)
        report['original_normal_candidate_alert_rates'] = {n:{a:float(np.mean(m.predict(f)[1]))
            for a,m in package['models'].items()} for n,f in original['partitions'].items() if n != 'train'}
        report['original_normal_candidate_alert_rate_caution'] = 'S/count-consistent candidates are not labelled true negatives; this is NOT measured real-world FPR'
    report["legacy_full_benchmark_summary"] = json.loads((ROOT/"reports/shared_model_v7/release/summary.json").read_text(encoding="utf-8"))["pooled"]
    report["primary_scope"]="same target Adapter+Process as legacy; original execution rows"
    report["all_rows_secondary_scope"]="background roles may be affected but unlabelled in collective benchmarks"
    write_json(output/f"confirmation{suffix}.json", report)
    print("POOLED", report["pooled"], flush=True)
    return report


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--folder',type=Path,default=OUT)
    parser.add_argument('--development-cases',type=int,default=400)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--include-input-stop', action='store_true',
                        help='Archived diagnostic only; missing executions are not Process rows')
    args=parser.parse_args()
    evaluate(args.development_cases,args.folder,args.limit,args.include_input_stop)
