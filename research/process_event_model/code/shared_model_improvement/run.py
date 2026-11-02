"""Development-only selection followed by frozen existing-benchmark scoring."""
import argparse
from dataclasses import replace, asdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from adapter_feature_ablation.run import load_development, counts, MAIN_SCENARIOS
from shared_model_improvement.models import Setting, StableDetector
from model_research_v13.data import read, write
from model_research_v15.features import augment
from model_evaluation.data import sha256
from model_evaluation.metrics import metric_record

OUTPUT = ROOT/"reports/shared_model_improvement"
OLD = ROOT/"reports/adapter_feature_ablation"
ALGORITHMS = ("if", "kmeans", "lof")
QUANTILES = (.90, .925, .95, .96, .97, .98, .99, .995)


def _stats(y, scores, threshold, scenarios=None):
    result = metric_record(*counts(y, scores > threshold))
    if scenarios is not None:
        result["macro_f1"] = float(np.mean([metric_record(*counts(y[scenarios == s],
            scores[scenarios == s] > threshold))["f1"] for s in np.unique(scenarios)]))
    return result


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT/"development.joblib"
    if path.exists():
        record = read(OUTPUT/"preparation.json")
        if sha256(path) != record["sha256"]: raise ValueError("Changed development features")
        return joblib.load(path)
    context = load_development()
    frozen = read(OLD/"frozen.json")
    if sha256(OLD/"shared_models.joblib") != frozen["model_sha256"]: raise ValueError("Old model changed")
    old = joblib.load(OLD/"shared_models.joblib")
    reference = old["adapter_reference"]
    result = {"train": reference.transform(context["partitions"]["train"]),
              "normal_calibration": reference.transform(context["normal_reference"]),
              "adapter_reference": reference, "feature_context": old["feature_context"]}
    for split in ("search", "confirmation"):
        frames, ys, scenarios = [], [], []
        for scenario, cohorts in context[split].items():
            for cohort, frame in cohorts.items():
                frames.append(reference.transform(frame))
                ys.append(frame.window_is_anomaly.to_numpy(bool) if cohort == "observed" else np.zeros(len(frame), bool))
                scenarios.extend([scenario]*len(frame))
        result[split] = {"frame": pd.concat(frames, ignore_index=True),
                         "y": np.concatenate(ys), "scenario": np.asarray(scenarios)}
    groups = [set(result["train"].case_id), set(result["normal_calibration"].case_id),
              set(result["search"]["frame"].case_id), set(result["confirmation"]["frame"].case_id)]
    for i in range(len(groups)):
        for j in range(i):
            if groups[i] & groups[j]: raise ValueError("Cases overlap across partitions")
    joblib.dump(result, path, compress=3)
    write(OUTPUT/"preparation.json", {"sha256": sha256(path), "source": frozen["development_sha256"],
        "old_model_sha256": frozen["model_sha256"], "benchmark_manifest_sha256": frozen["benchmark_manifest_sha256"],
        "normal_train_windows": len(result["train"]), "normal_calibration_windows": len(result["normal_calibration"]),
        "search_windows": len(result["search"]["y"]), "confirmation_windows": len(result["confirmation"]["y"]),
        "all_four_case_sets_disjoint": True, "source_template_split_unchanged": True})
    print("Prepared normal fit/calibration + disjoint search/confirmation", flush=True)
    return result


def candidates(algorithm):
    if algorithm == "if":
        for transform in ("asinh", "clip", "quantile"):
            for samples, maximum in ((256, 1.), (512, 1.), (2048, 1.), (512, .75)):
                yield Setting(algorithm, transform=transform, samples=samples, max_features=maximum)
        yield Setting(algorithm, transform="asinh", drop_workload=False)
    elif algorithm == "kmeans":
        for transform in ("asinh", "clip", "quantile"):
            for weight in (.25, .5):
                for clusters in (8, 16, 32):
                    yield Setting(algorithm, transform=transform, adapter_weight=weight, clusters=clusters)
        yield Setting(algorithm, transform="asinh", drop_workload=False, adapter_weight=.25)
    else:
        for transform in ("asinh", "clip", "quantile"):
            for weight in (.25, .5):
                for neighbors in (20, 50, 100):
                    yield Setting(algorithm, transform=transform, adapter_weight=weight, neighbors=neighbors)
        yield Setting(algorithm, transform="asinh", drop_workload=False, adapter_weight=.25)
        for weight in (.1, .25, .5):
            for cap in (2048, 4096):
                for neighbors in (20, 35, 75):
                    yield Setting(algorithm, transform="hybrid", adapter_weight=weight,
                                  reference_cap=cap, neighbors=neighbors)
        for metric in ("euclidean", "manhattan"):
            for neighbors in (35, 75):
                yield Setting(algorithm, transform="hybrid", adapter_weight=.25,
                              neighbors=neighbors, metric=metric, deduplicate=True)


def tune():
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("Already frozen; do not retune selected models")
    data = prepare()
    reference = joblib.load(OLD/"shared_models.joblib")
    search = data["search"]
    selected, records, models, baseline_records = {}, [], {}, {}
    for algorithm in ALGORITHMS:
        baseline = reference["models"][algorithm+"_baseline"]
        base_scores = baseline.score(search["frame"])
        base = _stats(search["y"], base_scores, baseline.thresholds["0.03"], search["scenario"])
        print(f"{algorithm} baseline search: F1={base['f1']:.2%}, precision={base['precision']:.2%}, FPR={base['normal_false_positive_rate']:.2%}", flush=True)
        baseline_records[algorithm] = base
        best, best_key, best_record = None, None, None
        for number, setting in enumerate(candidates(algorithm)):
            model = StableDetector(setting).fit(data["train"])
            normal_scores = model.score(data["normal_calibration"])
            scores = model.score(search["frame"])
            for q in QUANTILES:
                threshold = float(np.quantile(normal_scores, q, method="higher"))
                stats = _stats(search["y"], scores, threshold, search["scenario"])
                eligible = (stats["normal_false_positive_rate"] <= max(base["normal_false_positive_rate"]+.005, .03)
                    and stats["precision"] >= base["precision"]-.02)
                record = {"algorithm": algorithm, "candidate": number, "settings": asdict(setting),
                    "normal_quantile": q, "threshold": threshold, "eligible": eligible, **stats}
                records.append(record)
                key = (stats["f1"], stats["macro_f1"], -stats["normal_false_positive_rate"])
                if eligible and (best_key is None or key > best_key):
                    best_key, best_record = key, record
                    best = model
                    best.threshold = threshold
            maximum = max((r for r in records if r['algorithm'] == algorithm and r['candidate'] == number), key=lambda r:r['f1'])
            print(f"{algorithm} candidate {number:02d}: {setting.transform}, weight={setting.adapter_weight}, "
                  f"candidate F1={maximum['f1']:.2%}, P={maximum['precision']:.2%}, FP={maximum['normal_false_positive_rate']:.2%}, "
                  +(f"best eligible={best_key[0]:.2%}" if best_key else "no eligible yet"), flush=True)
            write(OUTPUT/"search_results.json", records)
            # A pointer to a model must keep the threshold selected for that model,
            # not the last quantile tested in a later iteration.
        if best is None:
            write(OUTPUT/"failure.json", {"algorithm": algorithm, "reason": "No candidate met the development precision/FPR gate", "baseline": base})
            raise RuntimeError(f"No acceptable {algorithm} candidate; preserve diagnostics")
        selected[algorithm], models[algorithm] = best_record, best
        write(OUTPUT/"search_progress.json", {"finished_algorithm": algorithm, "selected": selected,
                                               "candidate_records": len(records)})
    joblib.dump({"models": models, "adapter_reference": data["adapter_reference"],
                 "feature_context": data["feature_context"]}, OUTPUT/"models.joblib", compress=3)
    write(OUTPUT/"search_results.json", records)
    write(OUTPUT/"selection.json", {"model_sha256": sha256(OUTPUT/"models.joblib"),
        "baseline": baseline_records, "selected": selected, "primary_targets": read(OLD/"summary.json")["pooled"],
        "benchmark_manifest_sha256": sha256(ROOT/"data/evaluation/manifest.json"),
        "old_experiment_summary_sha256": sha256(OLD/"summary.json"),
        "old_models_sha256": sha256(OLD/"shared_models.joblib"),
        "v18_models_sha256": sha256(ROOT/"reports/model_research_v18/families.joblib"),
        "selection_on_development_only": True, "confirmation_used_for_selection": False,
        "previous_final_results_informed_architecture": True,
        "test_labels_used_for_current_parameter_selection": False,
        "adapter_experts": False, "single_estimator_per_algorithm": True,
        "minimum_adapter_features": 9, "benchmark_modified": False})
    print("Three single detectors selected on development and frozen", flush=True)


def load_models(folder=None):
    folder = OUTPUT if folder is None else Path(folder)
    selection = read(folder/"selection.json")
    if sha256(folder/"models.joblib") != selection["model_sha256"]: raise ValueError("Frozen model changed")
    return selection, joblib.load(folder/"models.joblib")


def confirm():
    selection, fitted = load_models()
    data = prepare()["confirmation"]
    old = joblib.load(OLD/"shared_models.joblib")["models"]
    records = []
    for a in ALGORITHMS:
        for method, model, threshold in (("baseline", old[a+"_baseline"], old[a+"_baseline"].thresholds["0.03"]),
                ("improved", fitted["models"][a], fitted["models"][a].threshold)):
            scores = model.score(data["frame"])
            records.append({"algorithm": a, "method": method,
                            **_stats(data["y"], scores, threshold, data["scenario"])})
    write(OUTPUT/"confirmation.json", {"used_to_reselect": False, "metrics": records})
    print(records, flush=True)


def evaluate():
    selection, fitted = load_models()
    benchmark = ROOT/"data/evaluation"
    digest = sha256(benchmark/"manifest.json")
    if digest != selection["benchmark_manifest_sha256"]: raise ValueError("Benchmark changed")
    cache = ROOT/"reports/model_research_v18/feature_cache"/digest[:16]
    datasets, all_scores, all_y = [], [], []
    samples = []
    for name in read(benchmark/"manifest.json")["datasets"]:
        ys, scores, adapters, case_indices = [], [], [], []
        for index, record in enumerate(read(cache/name/"index.json")):
            path = cache/name/record["file"]
            if sha256(path) != record["sha256"]: raise ValueError("Feature cache changed")
            payload = joblib.load(path)
            frame = fitted["adapter_reference"].transform(augment(payload["frame"]))
            values = np.column_stack([fitted["models"][a].score(frame) for a in ALGORITHMS])
            truth = np.asarray(payload["metadata"]["truth"], bool)
            ys.append(truth); scores.append(values); adapters.append(frame.adapterType.to_numpy())
            case_indices.append(payload["metadata"]["case_index"])
            ids = np.random.default_rng(4200+index).choice(len(frame), min(len(frame), 100), replace=False)
            sample = frame.iloc[ids].copy()
            sample["actual_anomaly"], sample["dataset"] = truth[ids], name
            for i, a in enumerate(ALGORITHMS): sample[a+"_score"] = values[ids, i]
            samples.append(sample)
        y, score = np.concatenate(ys), np.concatenate(scores)
        adapter = np.concatenate(adapters)
        records = []
        for i, a in enumerate(ALGORITHMS):
            t = fitted["models"][a].threshold
            records.append({"method": a, **_stats(y, score[:, i], t),
                "average_precision": float(average_precision_score(y, score[:, i])),
                "roc_auc": float(roc_auc_score(y, score[:, i])), "threshold": t})
        np.savez_compressed(OUTPUT/(name+"_scores.npz"), y=y, scores=score, adapter=adapter.astype(str),
                            case_index=np.concatenate(case_indices))
        datasets.append({"dataset": name, "metrics": records})
        all_y.append(y); all_scores.append(score)
        write(OUTPUT/"evaluation_progress.json", {"finished": [d["dataset"] for d in datasets], "metrics": records})
        print(name, [(r["method"], round(r["f1"]*100, 2)) for r in records], flush=True)
    y, scores = np.concatenate(all_y), np.concatenate(all_scores)
    pooled = [{"method": a, **_stats(y, scores[:, i], fitted["models"][a].threshold),
        "average_precision": float(average_precision_score(y, scores[:, i])),
        "roc_auc": float(roc_auc_score(y, scores[:, i]))} for i, a in enumerate(ALGORITHMS)]
    main = []
    for i, a in enumerate(ALGORITHMS):
        chunks = [np.load(OUTPUT/(s+"_scores.npz")) for s in MAIN_SCENARIOS]
        yy = np.concatenate([v["y"] for v in chunks]); ss = np.concatenate([v["scores"][:, i] for v in chunks])
        main.append({"method": a, **_stats(yy, ss, fitted["models"][a].threshold)})
    old = read(OLD/"summary.json")
    comparisons = []
    for r in pooled:
        baseline = next(b for b in old["pooled"] if b["method"] == r["method"]+"_baseline")
        failed = next(b for b in old["pooled"] if b["method"] == r["method"]+"_adapter_features")
        comparisons.append({"algorithm": r["method"], "before_adapter_f1": baseline["f1"],
            "initial_adapter_f1": failed["f1"], "improved_f1": r["f1"],
            "target_met": r["f1"] >= baseline["f1"],
            "adapter_feature_count": sum(c.startswith("adapter_") for c in fitted["models"][r["method"]].columns)})
    summary = {"pooled": pooled, "main_five_pooled": main, "datasets": datasets,
        "comparisons": comparisons, "protocol": {"decision_unit": "Process-stream minute", "point_adjustment": False,
        "decisions": len(y), "normal": int((~y).sum()), "anomaly": int(y.sum()), "benchmark_modified": False,
        "model_sha256": selection["model_sha256"], "benchmark_manifest_sha256": digest,
        "prior_test_inspection_informed_architecture": True, "not_external_blind_evaluation": True,
        "hyperparameter_selection_on_search_only": True, "adapter_experts": False}}
    write(OUTPUT/"summary.json", summary)
    joblib.dump(pd.concat(samples, ignore_index=True), OUTPUT/"analysis_samples.joblib", compress=3)
    print(comparisons, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("prepare", "tune", "confirm", "evaluate"), required=True)
    stage = parser.parse_args().stage
    {"prepare": prepare, "tune": tune, "confirm": confirm, "evaluate": evaluate}[stage]()
