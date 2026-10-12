"""Evaluate six strategies without fitting anything to final benchmark labels.

Run from the workspace root:
  .analysis-venv/Scripts/python.exe code/model_evaluation/evaluate.py
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))

import joblib
import numpy as np
import pandas as pd
import sklearn

from model_evaluation.data import (KEYS, POINT_DATASETS, SCENARIOS, WINDOW_KEYS,
    labels_for_rows, normal_development, normal_windows, observed_windows,
    sha256, source_references, unix_seconds, verify_benchmark, window_features)
from model_evaluation.metrics import Counts
from model_evaluation.models import ComparisonConfig, METHODS, SixModels
from process_anomaly_pipeline.data import prepare
from model_evaluation.windowing import PriorHistory, aggregate_windows, observed_completions
from model_evaluation.tune_windows import selected_references


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def transaction_cases(labels, events):
    pairs = labels.reset_index(drop=True)[["transactionId","case_id"]].drop_duplicates()
    if pairs.transactionId.duplicated().any():
        raise ValueError("Ambiguous case mapping")
    mapping = pairs.set_index("transactionId").case_id.to_dict()
    for event in events.loc[events.scenario.eq("input_stop")].itertuples():
        mapping.update({tx:event.case_id for tx in json.loads(event.evidence_json)["missing_transaction_ids"]})
    return mapping


def row_scores(folder, output, suite, labels, cases, events, max_cases):
    selected = set(cases.case_id)
    mapping = transaction_cases(labels, events)
    case_index = {c:i for i,c in enumerate(cases.case_id)}
    total, supported = Counts(), Counts()
    fallback, assessed = 0, 0
    outputs = []
    for cohort, filename in (("observed","processes.csv"),("normal","normal_processes.csv")):
        parts = {key:[] for key in ("margin","prediction","truth","valid","covered","case_index")}
        scanned = 0
        for raw in pd.read_csv(folder/filename,dtype="string",encoding="utf-8-sig",chunksize=suite.config.chunk_rows):
            if cohort == "observed":
                keys = pd.MultiIndex.from_frame(raw[KEYS])
                meta = labels.reindex(keys).reset_index(drop=True)
                if meta.case_id.isna().any():
                    raise ValueError("Unlabelled observed Process; PK join is required")
                keep = meta.case_id.isin(selected).to_numpy()
                raw, meta = raw.loc[keep].reset_index(drop=True), meta.loc[keep].reset_index(drop=True)
                truth = meta.row_is_anomaly.to_numpy(dtype=bool)
                valid = truth | ~meta.collective_context_is_anomaly.to_numpy(dtype=bool)
                indices = meta.case_id.map(case_index).to_numpy(dtype=np.int32)
            else:
                ids = raw.transactionId.map(mapping)
                if ids.isna().any():
                    raise ValueError("Unmapped normal execution")
                keep = ids.isin(selected).to_numpy()
                indices = ids.loc[keep].map(case_index).to_numpy(dtype=np.int32)
                raw = raw.loc[keep].reset_index(drop=True)
                truth, valid = np.zeros(len(raw),bool), np.ones(len(raw),bool)
            if not len(raw):
                continue
            frame = prepare(raw)
            if not frame.status.eq("S").all():
                raise ValueError("Unexpected benchmark STATUS")
            margin, prediction, covered = suite.score(frame)
            total.update(truth,prediction,valid)
            supported.update(truth,prediction,valid & covered)
            assessed += int(valid.sum())
            fallback += int((valid & ~covered).sum())
            for key,value in {"margin":margin.astype(np.float32),"prediction":prediction,"truth":truth,
                              "valid":valid,"covered":covered,"case_index":indices}.items():
                parts[key].append(value)
            scanned += len(raw)
            if scanned // 250_000 != (scanned-len(raw)) // 250_000:
                print(f"  {folder.name} row {cohort}: {scanned:,} rows scored",flush=True)
        arrays = {k:np.concatenate(v) for k,v in parts.items()}
        destination = output/f"row_{cohort}.npz"
        np.savez_compressed(destination,**arrays)
        outputs.append(destination.name)
    return {"metrics":total.records(),"supported_only_metrics":supported.records(),
            "assessed":assessed,"fallback":fallback,"fallback_rate":fallback/assessed,
            "status":"assessed","files":outputs}


def window_score_frame(suite, frame, cases, *, normal=False):
    indices = {c:i for i,c in enumerate(cases.case_id)}
    margins, predictions, coverage = [], [], []
    for begin in range(0,len(frame),suite.config.chunk_rows):
        part = frame.iloc[begin:begin+suite.config.chunk_rows]
        margin, prediction, covered = suite.score(part)
        margins.append(margin.astype(np.float32)); predictions.append(prediction); coverage.append(covered)
    truth = np.zeros(len(frame),bool) if normal else frame.window_is_anomaly.to_numpy(dtype=bool)
    bits = np.zeros(len(frame),dtype=np.uint8)
    if not normal:
        types = frame.scenario_types.astype("string").fillna("")
        for i,name in enumerate(SCENARIOS):
            bits |= types.str.split(";").map(lambda v:name in v).to_numpy(dtype=np.uint8) << i
    return {"margin":np.concatenate(margins),"prediction":np.concatenate(predictions),
            "covered":np.concatenate(coverage),"truth":truth,"valid":np.ones(len(frame),bool),
            "case_index":frame.case_id.map(indices).to_numpy(dtype=np.int32),
            "window_end_seconds":unix_seconds(frame.window_end),
            "execution_arrivals":frame.execution_arrivals.to_numpy(dtype=np.float32),
            "native_execution_arrivals":frame.get('native_execution_arrivals',frame.execution_arrivals).to_numpy(dtype=np.float32),
            "lookback_minutes":frame.get('lookback_minutes',pd.Series(1,index=frame.index)).to_numpy(dtype=np.int16),
            "duration_p95_ms":frame.duration_p95_ms.to_numpy(dtype=np.float32),
            "scenario_bits":bits}


def event_results(observed, normal, events, cases):
    case_map = {c:i for i,c in enumerate(cases.case_id)}
    case_n = len(cases)
    normal_alert = np.zeros((case_n,len(METHODS)),bool)
    np.logical_or.at(normal_alert,normal["case_index"],normal["prediction"])
    records = []
    all_types = np.ones((case_n,len(METHODS)),bool)
    type_count = np.zeros(case_n,dtype=int)
    for bit,name in enumerate(SCENARIOS):
        relevant = events.loc[events.scenario.eq(name)].copy()
        if relevant.empty:
            continue
        if relevant.case_id.duplicated().any():
            raise ValueError("Expected one event per type per case")
        ids = relevant.case_id.map(case_map).to_numpy(dtype=int)
        starts = unix_seconds(relevant.startTime)
        select = (observed["scenario_bits"] & (1<<bit)) > 0
        first = np.full((case_n,len(METHODS)),np.inf)
        for method_i in range(len(METHODS)):
            fired = select & observed["prediction"][:,method_i]
            np.minimum.at(first[:,method_i],observed["case_index"][fired],observed["window_end_seconds"][fired])
        detected = np.isfinite(first[ids])
        all_types[ids] &= detected
        type_count[ids] += 1
        for method_i,method in enumerate(METHODS):
            delays = first[ids,method_i][detected[:,method_i]]-starts[detected[:,method_i]]
            records.append({"scenario":name,"method":method,"events":len(ids),
                            "detected":int(detected[:,method_i].sum()),
                            "event_recall":float(detected[:,method_i].mean()),
                            "median_delay_seconds":float(np.median(delays)) if len(delays) else None})
    return {"per_type":records,"normal_case_false_alarm_rate":{
                method:float(normal_alert[:,i].mean()) for i,method in enumerate(METHODS)},
            "all_six_recall":{method:float(all_types[type_count==6,i].mean()) if (type_count==6).any() else None
                              for i,method in enumerate(METHODS)}}


def window_scores(folder, output, suite, labels, cases, baselines, history=None):
    observed = observed_windows(folder)
    observed = observed.loc[observed.case_id.isin(set(cases.case_id))].reset_index(drop=True)
    if history is None:
        normal = normal_windows(folder,observed,labels,cases)
        observed, normal = window_features(observed,baselines), window_features(normal,baselines)
    else:
        normal, normal_rows = normal_windows(folder,observed,labels,cases,return_rows=True)
        observed_rows = observed_completions(folder,labels,cases)
        observed = aggregate_windows(observed,observed_rows,suite.config.window_minutes,history,baselines)
        normal = aggregate_windows(normal,normal_rows,suite.config.window_minutes,history,baselines)
    if not observed[WINDOW_KEYS].equals(normal[WINDOW_KEYS]):
        raise ValueError("Observed and normal episode grids do not align")
    observed_scores = window_score_frame(suite,observed,cases)
    normal_scores = window_score_frame(suite,normal,cases,normal=True)
    total, supported = Counts(), Counts()
    for cohort,arrays in (("observed",observed_scores),("normal",normal_scores)):
        total.update(arrays["truth"],arrays["prediction"])
        supported.update(arrays["truth"],arrays["prediction"],arrays["covered"])
        np.savez_compressed(output/f"window_{cohort}.npz",**arrays)
    events = pd.read_csv(folder/"events.csv",encoding="utf-8-sig")
    events = events.loc[events.case_id.isin(set(cases.case_id))]
    assessed = len(observed)+len(normal)
    fallback = int((~observed_scores["covered"]).sum()+(~normal_scores["covered"]).sum())
    return {"metrics":total.records(),"supported_only_metrics":supported.records(),
            "assessed":assessed,"fallback":fallback,"fallback_rate":fallback/assessed,"status":"assessed",
            "events":event_results(observed_scores,normal_scores,events,cases),
            "files":["window_observed.npz","window_normal.npz"]}


def summarize(output, manifest, protocol):
    summary = {"protocol":protocol,"datasets":[],"pooled":{},"supported_only":{},"macro_basic_scenarios":{}}
    for name in manifest["datasets"]:
        summary["datasets"].append(json.loads((output/name/"metrics.json").read_text(encoding="utf-8")))
    for unit in ("row","window"):
        total, supported = Counts(), Counts()
        assessed = [d for d in summary["datasets"] if d[unit]["status"]=="assessed"]
        for dataset in assessed:
            total.values += Counts.from_records(dataset[unit]["metrics"]).values
            supported.values += Counts.from_records(dataset[unit]["supported_only_metrics"]).values
        summary["pooled"][unit], summary["supported_only"][unit] = total.records(), supported.records()
        basic = [d for d in assessed if d["dataset"]!="mixed_hard"]
        summary["macro_basic_scenarios"][unit] = []
        for method in METHODS:
            rows = [next(r for r in d[unit]["metrics"] if r["method"]==method) for d in basic]
            record = {"method":method,"scenarios":len(rows)}
            for metric in ("accuracy","precision","recall","f1"):
                values = [r[metric] for r in rows if r[metric] is not None]
                record[metric] = float(np.mean(values)) if values else None
            summary["macro_basic_scenarios"][unit].append(record)
    summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(output/"summary.json",summary)
    return summary


def run(args):
    config = ComparisonConfig(seed=args.seed,replay_cases_per_adapter=args.replays_per_adapter)
    benchmark, cache, output = args.benchmark.resolve(), args.source_cache.resolve(), args.output.resolve()
    selected_state, selected_record, selected_partitions = None, None, None
    if not args.row_only and args.window_search is not None:
        config,selected_partitions,selected_state,selected_record = selected_references(args.window_search.resolve(),benchmark,cache,args.window_minutes)
        print(f'Frozen validation-selected lookback: {config.window_minutes} minutes, 1-minute decision stride',flush=True)
    history = PriorHistory(benchmark/'reference_history_windows.csv') if selected_state else None
    print("Verifying all 67 benchmark CSV hashes and the original policy artifact...",flush=True)
    manifest, bundle = verify_benchmark(benchmark,cache)
    identity = {"config":asdict(config),"benchmark_manifest_sha256":sha256(benchmark/"manifest.json"),
                "source_cache_sha256":sha256(cache),"max_cases":args.max_cases,"include_windows":not args.row_only,
                "implementation_sha256":{p.name:sha256(p) for p in Path(__file__).parent.glob("*.py")}}
    identity['window_selection_sha256'] = sha256(args.window_search/'selection.json') if selected_state else None
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise FileExistsError("Use a new output folder or --resume; previous results will not be overwritten")
    output.mkdir(parents=True,exist_ok=True)
    protocol_path, model_path = output/"protocol.json", output/"comparison_models.joblib"
    if args.resume and protocol_path.exists():
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        if protocol["identity"] != identity or sha256(model_path) != protocol["new_models_sha256"]:
            raise ValueError("Resume identity/model mismatch; use a new output folder")
        # Own artifact, checked against our saved hash. Never load untrusted joblib.
        state = joblib.load(model_path)
        row_suite, window_suite, baselines = state["row"], state["window"], state["baselines"]
    else:
        partitions, template_sets, source_support, baselines = source_references(bundle)
        print("Training comparable row IF and K-means on the same past normal source sample...",flush=True)
        row_suite = SixModels("row",config).fit(partitions)
        window_suite, window_reference = None, []
        if not args.row_only:
            if selected_state:
                window_partitions,window_reference = selected_partitions,selected_state['metadata']
                source_support,baselines = selected_state['source_support'],selected_state['baselines']
            else:
                window_partitions, window_reference = normal_development(bundle,template_sets,benchmark,baselines,config)
            window_suite = SixModels("window",config).fit(window_partitions,source_support)
        joblib.dump({"row":row_suite,"window":window_suite,"baselines":baselines},model_path,compress=3)
        protocol = {"identity":identity,"new_models_sha256":sha256(model_path),"methods":list(METHODS),
                    "versions":{"python":platform.python_version(),"sklearn":sklearn.__version__,"numpy":np.__version__,"pandas":pd.__version__},
                    "row_reference":[{"split":name,"rows":len(p),"workflows":int(p.source_template_id.nunique()),
                                      "first":p.startTime.min().isoformat(),"last":p.startTime.max().isoformat()} for name,p in partitions.items()],
                    "window_reference":window_reference,"support":row_suite.support+(window_suite.support if window_suite else []),
                    "window_selection":selected_record,
                    "window_minutes":config.window_minutes,"decision_stride_minutes":1,
                    "features":{"row":list(row_suite.global_models['if'].encoder.columns),
                                "window":list(window_suite.global_models['if'].encoder.columns) if window_suite else []},
                    "thresholds":row_suite.threshold_records()+(window_suite.threshold_records() if window_suite else []),
                    "limitations":["Semi-synthetic stress test; original normal patterns overlap with evaluation generation.",
                        "New comparable research models, NOT the previously saved serving IF.",
                        "Window references are independently seeded normal replays with disjoint source workflows; virtual dates overlap across reference/test episodes.",
                        "TARGET windows assume a predefined monitored business group; production must monitor all groups, not select them using incident labels.",
                        "Event-time offline evaluation; delayed completed logs require an arrival stream or watermark policy for live traffic features.",
                        "Row evaluation excludes collective-only labels and cannot classify a zero-row input-stop event.",
                        "Precision uses zero_division=0; no positive ground truth makes Recall/F1 unavailable.",
                        "Hard all-six recall is reported separately; one event hit is not full case success."]}
        write_json(protocol_path,protocol)
    for name in manifest["datasets"]:
        destination = output/name
        completed = destination/"metrics.json"
        if args.resume and completed.exists():
            previous = json.loads(completed.read_text(encoding="utf-8"))
            for file,digest in previous["output_hashes"].items():
                if sha256(destination/file) != digest:
                    raise ValueError("Changed completed prediction file")
            print(f"Verified completed {name}; skipping",flush=True)
            continue
        destination.mkdir(exist_ok=True)
        folder = benchmark/name
        cases = pd.read_csv(folder/"cases.csv",encoding="utf-8-sig").sort_values("case_id").reset_index(drop=True)
        if args.max_cases is not None:
            cases = cases.head(args.max_cases)
        labels = labels_for_rows(folder)
        events = pd.read_csv(folder/"events.csv",encoding="utf-8-sig")
        started = time.monotonic()
        print(f"Evaluating {name}: {len(cases):,} cases + matched normal controls",flush=True)
        result = {"dataset":name,"cases":len(cases),"normal_cases":len(cases),"case_ids":list(cases.case_id)}
        result["row"] = (row_scores(folder,destination,row_suite,labels,cases,events,args.max_cases)
                         if name in POINT_DATASETS else {"status":"not_applicable","reason":"Collective/time anomaly: no positive point labels; not a row-classification task"})
        result["window"] = (window_scores(folder,destination,window_suite,labels,cases,baselines,history)
                            if window_suite else {"status":"disabled"})
        result["elapsed_seconds"] = time.monotonic()-started
        result["output_hashes"] = {f:sha256(destination/f) for unit in ("row","window") for f in result[unit].get("files",[])}
        write_json(completed,result)
        print(f"PASS {name}: evaluated and saved ({result['elapsed_seconds']:.1f}s)",flush=True)
    summary = summarize(output,manifest,protocol)
    # Original policy and every benchmark CSV remain byte-identical after evaluation.
    verify_benchmark(benchmark,cache)
    print("ALL COMPLETE: seven datasets; original data/model hashes unchanged",flush=True)
    for record in summary["pooled"]["window" if window_suite else "row"]:
        print(record["method"],{k:record[k] for k in ("accuracy","precision","recall","f1")},flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark",type=Path,default=ROOT/"data/evaluation")
    parser.add_argument("--source-cache",type=Path,default=ROOT/"reports/evaluation_v2_work/inputs.json")
    parser.add_argument("--output",type=Path,default=ROOT/"reports/model_evaluation_v2")
    parser.add_argument('--window-search',type=Path,default=ROOT/'reports/window_search_v2')
    parser.add_argument('--window-minutes',type=int,choices=(1,2,3,5,10,15),default=None,help='Preplanned fixed-window baseline; default uses validation selection')
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--replays-per-adapter",type=int,default=8)
    parser.add_argument("--max-cases",type=int,default=None,help="Smoke test only; default evaluates all cases")
    parser.add_argument("--row-only",action="store_true")
    parser.add_argument("--resume",action="store_true")
    args = parser.parse_args()
    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be positive")
    run(args)
