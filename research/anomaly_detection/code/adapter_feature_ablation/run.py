"""Fit once on normal development, then evaluate the UNCHANGED benchmark."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from adapter_feature_ablation.features import BASE, ADAPTER, DESCRIPTIONS, AdapterReferences
from adapter_feature_ablation.models import SharedDetector
from model_evaluation.data import sha256, verify_benchmark
from model_evaluation.metrics import metric_record
from model_research_v13.data import read, write, trusted_cache
from model_research_v15.features import augment

OUTPUT = ROOT/"reports/adapter_feature_ablation"
BENCHMARK = ROOT/"data/evaluation"
ALGORITHMS = ("if", "kmeans", "lof")
VIEWS = ("baseline", "adapter_features")
METHODS = tuple(f"{algorithm}_{view}" for algorithm in ALGORITHMS for view in VIEWS)
MAIN_SCENARIOS = ("individual_delay", "group_delay", "time_burst", "input_stop", "gradual_degradation")


def load_development():
    path = ROOT/"reports/model_research_v15/development_inputs.joblib"
    expected = read(path.parent/"preparation.json")["data_sha256"]
    return trusted_cache(path, expected)


def counts(y, prediction):
    y, prediction = np.asarray(y, bool), np.asarray(prediction, bool)
    return np.array([np.sum(~y & ~prediction), np.sum(~y & prediction),
                     np.sum(y & ~prediction), np.sum(y & prediction)], np.int64)


def train():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"frozen.json").exists():
        raise FileExistsError("Frozen experiment exists. Use evaluate/report; do not silently retune.")
    context = load_development()
    original = context["partitions"]["train"]
    normal = context["normal_reference"]
    if set(original.case_id) & set(normal.case_id):
        raise ValueError("Fit/calibration case overlap")
    reference = AdapterReferences.fit(original)
    fitting, calibration = reference.transform(original), reference.transform(normal)
    records, models = [], {}
    for algorithm in ALGORITHMS:
        for view in VIEWS:
            columns = BASE if view == "baseline" else (*BASE, *ADAPTER)
            method = f"{algorithm}_{view}"
            model = SharedDetector.fit(algorithm, columns, fitting, calibration)
            models[method] = model
            records.append({"method": method, "columns": list(columns), "estimator_count": 1,
                "fit_rows": model.fit_rows, "fit_seconds": model.fit_seconds,
                "thresholds": model.thresholds,
                "calibration_false_positive_rate": {str(r): float(np.mean(model.score(calibration) > t))
                    for r, t in model.thresholds.items()}})
            print(f"fit {method}: {model.fit_rows:,} rows, {model.fit_seconds:.1f}s", flush=True)
    artifact = OUTPUT/"shared_models.joblib"
    # Inference still needs the existing Process->minute feature transformer.
    feature_context = {k: context[k] for k in ("anchor", "reference", "baseline", "legacy_baselines", "history")}
    joblib.dump({"models": models, "adapter_reference": reference,
                 "feature_context": feature_context}, artifact, compress=3)
    write(OUTPUT/"frozen.json", {"model_sha256": sha256(artifact), "seed": 42,
        "benchmark_manifest_sha256": sha256(BENCHMARK/"manifest.json"),
        "old_model_sha256": sha256(ROOT/"reports/model_research_v18/families.joblib"),
        "development_sha256": read(ROOT/"reports/model_research_v15/preparation.json")["data_sha256"],
        "normal_train_windows": len(original), "normal_calibration_windows": len(normal),
        "normal_train_cases": int(original.case_id.nunique()), "normal_calibration_cases": int(normal.case_id.nunique()),
        "primary_false_alarm_target": .03, "sensitivity_targets": [.01, .03, .05],
        "test_labels_used_for_selection": False, "hyperparameters_tuned_in_this_experiment": False,
        "source_partition_protocol": "unchanged v15 source-template holdout; S normal candidates + replay",
        "adapter_reference_fit": "only normal training minute aggregates; no final/background controls",
        "reference_level": "Adapter+mean-totalCount-bin -> Adapter -> global, >=30 finite normal windows",
        "single_detector_per_algorithm_per_view": True, "adapter_experts": False,
        "direct_categorical_features": False, "benchmark_modified": False,
        "base_already_contains_adapter_process_conditioned_features": True,
        "adapter_wide_concurrent_features_assessed": False,
        "models": records, "added_feature_descriptions": DESCRIPTIONS})


def evaluate():
    frozen = read(OUTPUT/"frozen.json")
    artifact = OUTPUT/"shared_models.joblib"
    if sha256(artifact) != frozen["model_sha256"]:
        raise ValueError("Changed frozen models")
    benchmark_hash = sha256(BENCHMARK/"manifest.json")
    if benchmark_hash != frozen["benchmark_manifest_sha256"]:
        raise ValueError("Changed benchmark")
    manifest, _ = verify_benchmark(BENCHMARK, ROOT/"reports/evaluation_v2_work/inputs.json")
    fitted = joblib.load(artifact)
    cache = ROOT/"reports/model_research_v18/feature_cache"/benchmark_hash[:16]
    if read(cache/"protocol.json")["benchmark_manifest_sha256"] != benchmark_hash:
        raise ValueError("Wrong cache provenance")
    totals = np.zeros((len(METHODS), 4), np.int64)
    main_totals = totals.copy()
    rates = {str(r): totals.copy() for r in (.01, .03, .05)}
    adapter_totals, datasets, sample_frames = {}, [], []
    all_y, all_scores = [], []
    unseen_counts = totals.copy()
    for name in manifest["datasets"]:
        case_info = pd.read_csv(BENCHMARK/name/"cases.csv", encoding="utf-8-sig", usecols=["case_id"])
        collected_y, collected_scores = [], []
        case_alerts = np.zeros((len(case_info), len(METHODS)), bool)
        events_detected = np.zeros_like(case_alerts)
        elapsed, fallbacks = 0., np.zeros(3, np.int64)
        example = {}
        for entry in read(cache/name/"index.json"):
            path = cache/name/entry["file"]
            if sha256(path) != entry["sha256"]:
                raise ValueError("Changed feature cache")
            data = joblib.load(path)
            frame = fitted["adapter_reference"].transform(augment(data["frame"]))
            meta, y = data["metadata"], np.asarray(data["metadata"]["truth"], bool)
            start = time.perf_counter()
            scores = np.column_stack([fitted["models"][m].score(frame) for m in METHODS])
            elapsed += time.perf_counter()-start
            thresholds = np.array([fitted["models"][m].thresholds["0.03"] for m in METHODS])
            pred = scores > thresholds
            for i in range(len(METHODS)):
                totals[i] += counts(y, pred[:, i])
                if name in MAIN_SCENARIOS:
                    main_totals[i] += counts(y, pred[:, i])
                if entry["cohort"] == "normal":
                    np.logical_or.at(case_alerts[:, i], meta["case_index"], pred[:, i])
                else:
                    np.logical_or.at(events_detected[:, i], meta["case_index"], pred[:, i] & y)
                unseen = frame.adapter_reference_level.eq(2).to_numpy()
                unseen_counts[i] += counts(y[unseen], pred[unseen, i])
            for adapter in frame.adapterType.unique():
                matrix = adapter_totals.setdefault(str(adapter), np.zeros_like(totals))
                mask = frame.adapterType.eq(adapter).to_numpy()
                for i in range(len(METHODS)):
                    matrix[i] += counts(y[mask], pred[mask, i])
            for rate in rates:
                cutoff = np.array([fitted["models"][m].thresholds[rate] for m in METHODS])
                prediction = scores > cutoff
                for i in range(len(METHODS)):
                    rates[rate][i] += counts(y, prediction[:, i])
            fallbacks += np.bincount(frame.adapter_reference_level, minlength=3)
            # Reservoir is descriptive only; never used to fit or select thresholds.
            ids = np.random.default_rng(42+len(sample_frames)).choice(len(frame), min(100, len(frame)), replace=False)
            sampled = frame.iloc[ids][["adapterType", "processId", "window_start", *BASE, *ADAPTER,
                                       "adapter_reference_level", "adapter_reference_support"]].copy()
            sampled["actual_anomaly"], sampled["dataset"] = y[ids], name
            for i, method in enumerate(METHODS):
                sampled[method+"_score"] = scores[ids, i]
            sample_frames.append(sampled)
            collected_y.append(y); collected_scores.append(scores)
            for i, method in enumerate(METHODS):
                for kind, mask in (("TP", y & pred[:, i]), ("FP", ~y & pred[:, i]), ("FN", y & ~pred[:, i])):
                    key = method+"_"+kind
                    if key not in example and mask.any():
                        j = int(np.flatnonzero(mask)[0])
                        example[key] = {"method": method, "outcome": kind, "adapterType": str(frame.adapterType.iloc[j]),
                            "processId": str(frame.processId.iloc[j]), "score": float(scores[j, i]),
                            "threshold": float(thresholds[i]), **{c: float(frame[c].iloc[j]) if np.isfinite(frame[c].iloc[j]) else None
                                for c in ("zero_run", "arrival_dev_1m", "duration_upper_max_1m", *ADAPTER)},
                            "explanation": "Observed feature clues, not causal attribution"}
        y, scores = np.concatenate(collected_y), np.concatenate(collected_scores)
        records = []
        for i, method in enumerate(METHODS):
            cutoff = fitted["models"][method].thresholds["0.03"]
            records.append({"method": method, **metric_record(*counts(y, scores[:, i] > cutoff)),
                "average_precision": float(average_precision_score(y, scores[:, i])),
                "roc_auc": float(roc_auc_score(y, scores[:, i])), "threshold": cutoff,
                "normal_case_false_alarm_rate": float(case_alerts[:, i].mean()),
                "anomaly_case_any_true_window_recall": float(events_detected[:, i].mean())})
        datasets.append({"dataset": name, "metrics": records, "score_seconds": elapsed,
                         "reference_level_windows": fallbacks.tolist(), "examples": list(example.values())})
        np.savez_compressed(OUTPUT/(name+"_scores.npz"), y=y, scores=scores)
        all_y.append(y); all_scores.append(scores)
        write(OUTPUT/"progress.json", {"completed": [d["dataset"] for d in datasets], "last_metrics": records})
        print(name, [(r["method"], round(r["f1"]*100, 2)) for r in records], flush=True)
    y, scores = np.concatenate(all_y), np.concatenate(all_scores)
    records = [{"method": m, **metric_record(*totals[i]),
                "average_precision": float(average_precision_score(y, scores[:, i])),
                "roc_auc": float(roc_auc_score(y, scores[:, i]))} for i, m in enumerate(METHODS)]
    make_records = lambda matrix: [{"method": m, **metric_record(*matrix[i])} for i, m in enumerate(METHODS)]
    summary = {"pooled": records, "main_five_pooled": make_records(main_totals),
        "sensitivity": {r: make_records(matrix) for r, matrix in rates.items()},
        "per_adapter": {a: make_records(v) for a, v in adapter_totals.items()},
        "global_adapter_reference_fallback": make_records(unseen_counts), "datasets": datasets,
        "protocol": {**{k:v for k,v in frozen.items() if k not in ("models", "added_feature_descriptions")},
            "decision_unit": "one Process-stream minute", "point_adjustment": False,
            "evaluation_decisions": len(y), "normal_decisions": int((~y).sum()), "anomaly_decisions": int(y.sum()),
            "all_existing_scenarios_included": True, "main_five_reported_separately": True,
            "external_blind_test": False, "replay_prior_is_synthetic": True,
            "caveat": "Controlled feature ablation within new single estimators; v18 nested global route is external reference, not architecture-matched control."}}
    if sha256(BENCHMARK/"manifest.json") != benchmark_hash:
        raise ValueError("Benchmark changed while evaluating")
    if sha256(ROOT/"reports/model_research_v18/families.joblib") != frozen["old_model_sha256"]:
        raise ValueError("Old models changed")
    write(OUTPUT/"summary.json", summary)
    joblib.dump(pd.concat(sample_frames, ignore_index=True), OUTPUT/"analysis_samples.joblib", compress=3)
    print("Evaluation complete; benchmark and v18 models unchanged", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("train", "evaluate", "report"), required=True)
    stage = parser.parse_args().stage
    if stage == "train": train()
    elif stage == "evaluate": evaluate()
    else:
        from adapter_feature_ablation.report import build
        build()
