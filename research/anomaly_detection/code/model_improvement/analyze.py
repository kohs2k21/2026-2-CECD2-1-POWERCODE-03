"""Audit six strategies, complementarity and explanations without retuning."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import average_precision_score

from model_evaluation.data import SCENARIOS, sha256
from model_evaluation.metrics import Counts, metric_record
from model_evaluation.models import METHODS
from model_evaluation.tune_windows import write_json
from model_improvement.data import OUTPUT
from model_improvement.features import FEATURES
from model_improvement.run import freeze_load, load_preparation
from model_improvement.models import Family, Settings, Suite


def count_matrix(y, prediction):
    y = np.asarray(y, dtype=bool)
    return np.array([[np.sum(~y & ~p), np.sum(~y & p), np.sum(y & ~p), np.sum(y & p)]
                     for p in prediction.T], dtype=np.int64)


def role_overlap(y, prediction):
    a, b = prediction[:, 2], prediction[:, 5]
    records = []
    for truth, label in ((True, "anomaly"), (False, "normal")):
        masks = (a & b, a & ~b, ~a & b, ~a & ~b)
        total = int(np.sum(y == truth))
        for category, mask in zip(("both", "if_only", "kmeans_only", "neither"), masks):
            n = int(np.sum(mask & (y == truth)))
            records.append({"truth": label, "category": category, "n": n, "denominator": total, "rate": n/total if total else None})
    return records


def explanations(suite, arrays, scenario, columns=FEATURES):
    pred, y = arrays["prediction"], arrays["truth"]
    masks = {"if_only": y & pred[:, 2] & ~pred[:, 5], "kmeans_only": y & ~pred[:, 2] & pred[:, 5],
             "both": y & pred[:, 2] & pred[:, 5], "missed": y & ~pred[:, 2] & ~pred[:, 5],
             "normal_false_alarm": ~y & (pred[:, 2] | pred[:, 5])}
    result = []
    for category, mask in masks.items():
        indices = np.flatnonzero(mask)
        if not len(indices):
            continue
        # First qualifying decision, NOT cherry-picked maximum/easiest example.
        index = int(indices[0])
        frame = pd.DataFrame([arrays["features"][index]], columns=columns, dtype=float)
        frame["adapterType"] = arrays["adapter"][index]
        record = {"scenario": scenario, "category": category, "index": index,
                  "case_index": int(arrays["case_index"][index]), "adapter": str(arrays["adapter"][index]),
                  "expert_available": bool(arrays["covered"][index]),
                  "margins": {m: float(arrays["margin"][index, j]) for j, m in enumerate(METHODS)},
                  "feature_values": {c: float(v) if np.isfinite(v) else None for c, v in zip(columns, arrays["features"][index])}}
        details = []
        for algorithm in ("if", "kmeans"):
            family = suite.families[algorithm]
            detectors = {"GLOBAL": family.global_model}
            if record["adapter"] in family.experts:
                detectors[record["adapter"]] = family.experts[record["adapter"]]
            for route, detector in detectors.items():
                cols = detector.encoder.columns
                values = detector.encoder.transform(frame)[0]
                if algorithm == "kmeans":
                    labels, distance, contribution = detector.explain_cluster(frame)
                    order = np.argsort(-contribution[0])[:5]
                    details.append({"algorithm": algorithm, "route": route, "cluster": int(labels[0]),
                                    "distance": float(distance[0]), "radius": float(detector.radii[labels[0]]),
                                    "explanation": "Scaled squared distance contribution to nearest center; NOT causal root cause",
                                    "top_features": [{"feature": cols[j], "distance_share": float(contribution[0, j]),
                                                      "standardized_value": float(values[j]),
                                                      "cluster_center": float(detector.model.cluster_centers_[labels[0], j])} for j in order]})
                else:
                    original = float(detector.score(frame)[0])
                    variants = pd.concat([frame]*len(cols), ignore_index=True)
                    for j, col in enumerate(cols):
                        variants.loc[j, col] = detector.encoder.median[j]
                    reduction = original-detector.score(variants)
                    order = np.argsort(-reduction)[:5]
                    details.append({"algorithm": algorithm, "route": route, "score": original,
                                    "explanation": "Replace one feature by training median, measure score decrease. Correlated features interact; NOT SHAP or causal attribution",
                                    "top_features": [{"feature": cols[j], "score_drop": float(reduction[j]),
                                                      "standardized_value": float(values[j])} for j in order]})
        record["explanations"] = details
        result.append(record)
    return result


def bootstrap_difference(case_counts, left=5, right=2, seed=42, repetitions=400):
    """Paired, scenario-stratified case bootstrap; controls stay with injected case."""
    rng = np.random.default_rng(seed)
    differences = []
    for _ in range(repetitions):
        pooled = np.zeros((6, 4), dtype=np.int64)
        for counts in case_counts:
            selected = rng.integers(0, len(counts), len(counts))
            pooled += counts[selected].sum(axis=0)
        f1 = [metric_record(*c)["f1"] for c in pooled]
        differences.append(f1[left]-f1[right])
    lo, median, hi = np.quantile(differences, [.025, .5, .975])
    return {"left": METHODS[left], "right": METHODS[right], "metric": "pooled F1 difference (left-right)",
            "low_95": float(lo), "median": float(median), "high_95": float(hi),
            "repetitions": repetitions, "unit": "synthetic case with observed+normal pair; stratified by scenario",
            "limitation": "Case sampling uncertainty only; no training-seed, source-template or real-world domain uncertainty"}


def run(output=OUTPUT, loader=freeze_load, preploader=load_preparation, feature_columns=FEATURES):
    OUTPUT = output
    suite, selection = loader()
    summary = json.loads((OUTPUT/"summary.json").read_text(encoding="utf-8"))
    reference = json.loads((OUTPUT/"matched_fpr_reference.json").read_text(encoding="utf-8"))
    cutoffs = np.asarray(reference["margin_cutoffs"])
    content = preploader()
    # Algorithm-only diagnostic: same predeclared feature view and defaults.
    # Neither final labels nor previous final scores choose this diagnostic.
    if "log_arrivals" in content["partitions"]["train"]:
        from model_improvement.refine import VIEWS
        shared_columns = VIEWS["relational"]
    else:
        shared_columns = FEATURES
    common = Suite({a: Family(shared_columns, Settings(a)).fit(content["partitions"], content["support"])
                    for a in ("if", "kmeans")}, suite.baseline)
    common_normal = np.vstack([common.score(d["normal"].loc[d["normal"].validation_split.eq("confirmation")])[0]
                               for d in content["bank"].values()])
    common_cutoffs = np.quantile(common_normal, .99, axis=0)
    joblib.dump(common, OUTPUT/"common_view_diagnostic.joblib", compress=3)
    common_pooled, common_matched = Counts(), Counts()
    by_adapter, comparisons, examples, bootstrap_cases = {}, [], [], []
    pooled_matched = Counts()
    for dataset in summary["datasets"]:
        name = dataset["dataset"]
        arrays = {}
        for cohort, digest in dataset["arrays_sha256"].items():
            if sha256(OUTPUT/name/f"{cohort}.npz") != digest:
                raise ValueError("Evaluation arrays changed")
            with np.load(OUTPUT/name/f"{cohort}.npz", allow_pickle=False) as file:
                arrays[cohort] = {k: file[k] for k in file.files}
        y = np.concatenate([a["truth"] for a in arrays.values()])
        scores = np.vstack([a["margin"] for a in arrays.values()])
        prediction = np.vstack([a["prediction"] for a in arrays.values()])
        matched = scores > cutoffs
        matched_counts = Counts()
        matched_counts.update(y, matched)
        pooled_matched.values += matched_counts.values
        common_margin = []
        for a in arrays.values():
            frame = pd.DataFrame(a["features"], columns=feature_columns)
            frame["adapterType"] = a["adapter"]
            common_margin.extend(common.score(frame.iloc[start:start+50_000])[0] for start in range(0, len(frame), 50_000))
        common_margin = np.vstack(common_margin)
        common_counts, common_budget_counts = Counts(), Counts()
        common_counts.update(y, common_margin > 0)
        common_budget_counts.update(y, common_margin > common_cutoffs)
        common_pooled.values += common_counts.values
        common_matched.values += common_budget_counts.values
        comparisons.append({"dataset": name, "deployed_overlap": role_overlap(y, prediction),
                            "matched_budget_overlap": role_overlap(y, matched), "matched_budget_metrics": matched_counts.records(),
                            "common_view_metrics": common_counts.records(), "common_view_matched_metrics": common_budget_counts.records(),
                            "pr_auc": [{"method": m, "average_precision": float(average_precision_score(y, scores[:, i])),
                                        "random_ranking_baseline": float(y.mean())} for i, m in enumerate(METHODS)]})
        ids = np.concatenate([a["case_index"] for a in arrays.values()])
        case_counts = np.zeros((dataset["case_count"], 6, 4), dtype=np.int64)
        for j in range(6):
            p = prediction[:, j]
            for c, mask in enumerate((~y & ~p, ~y & p, y & ~p, y & p)):
                np.add.at(case_counts[:, j, c], ids[mask], 1)
        bootstrap_cases.append(case_counts)
        for cohort, a in arrays.items():
            for adapter in np.unique(a["adapter"]):
                mask = a["adapter"] == adapter
                value = by_adapter.setdefault(str(adapter), np.zeros((6, 4), dtype=np.int64))
                value += count_matrix(a["truth"][mask], a["prediction"][mask])
        examples.extend(explanations(suite, arrays["observed"], name, feature_columns))
        print(f"Role analysis: {name}", flush=True)
    adapters = [{"adapter": adapter, "expert_available": adapter in suite.families["if"].experts,
                 "metrics": [{"method": m, **metric_record(*counts[i])} for i, m in enumerate(METHODS)]}
                for adapter, counts in sorted(by_adapter.items())]
    centers = []
    family = suite.families["kmeans"]
    for route, detector in {"GLOBAL": family.global_model, **family.experts}.items():
        train = content["partitions"]["train"]
        cal = content["partitions"]["calibration"]
        if route != "GLOBAL":
            train, cal = train.loc[train.adapterType.eq(route)], cal.loc[cal.adapterType.eq(route)]
        train_labels, _, _ = detector.explain_cluster(train)
        cal_labels, _, _ = detector.explain_cluster(cal)
        for i, center in enumerate(detector.model.cluster_centers_):
            original = center*detector.encoder.scale+detector.encoder.median
            centers.append({"route": route, "cluster": i, "train_windows": int(np.sum(train_labels == i)),
                            "calibration_windows": int(np.sum(cal_labels == i)), "radius_p95": float(detector.radii[i]),
                            "radius_used_in_score": detector.settings.radius_normalization,
                            "features": {c: float(v) for c, v in zip(detector.encoder.columns, original)}})
    result = {"datasets": comparisons, "matched_budget_pooled": pooled_matched.records(),
              "matched_budget_reference": reference, "adapters": adapters, "examples": examples, "clusters": centers,
              "paired_bootstrap": [bootstrap_difference(bootstrap_cases, left, right)
                                   for left, right in ((5, 2), (2, 0), (5, 3), (1, 0), (4, 3))],
              "common_view_diagnostic": {"features": list(shared_columns), "settings": "Both families defaults; IF128/512, KMeans6 raw-distance; quantile.99/global weight.5",
                  "normal_reference": "Fresh confirmation normal controls only, target pooled FPR1%",
                  "margin_cutoffs": common_cutoffs.tolist(), "pooled": common_pooled.records(), "matched_pooled": common_matched.records(),
                  "model_sha256": sha256(OUTPUT/"common_view_diagnostic.joblib"),
                  "purpose": "Algorithm-only control for selected feature-view confounding; NOT a final-test-driven promoted model"},
              "model_sha256": selection["model_sha256"]}
    write_json(OUTPUT/"role_analysis.json", result)
    print("Role comparisons/explanations completed without any model or threshold changes.", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, choices=(3, 4), default=4)
    args = parser.parse_args()
    if args.version == 4:
        from model_improvement.refine import OUTPUT as refined_output, frozen, load, ALL_FEATURES
        run(refined_output, frozen, load, ALL_FEATURES)
    else:
        run()
