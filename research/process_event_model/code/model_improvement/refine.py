"""Second, separately versioned experiment: retain v2 and select feature views.

v3 rejected wholesale feature replacement: IF lost sensitivity and clustering
expert FPR drifted. v4 searches old, relational, temporal and compact views on
NEW cases. v3 confirmation/test scores do not enter candidate selection.
"""
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd

from model_evaluation.data import SCENARIOS, sha256, verify_benchmark
from model_evaluation.metrics import Counts, metric_record
from model_evaluation.models import METHODS, WINDOW_FEATURES, SixModels
from model_evaluation.tune_windows import write_json, selected_references
from model_evaluation.windowing import PriorHistory
from model_evaluation.evaluate import event_results
from model_improvement.data import BENCHMARK, SOURCE_CACHE, SEARCH, OUTPUT as V3, validation_inputs
from model_improvement.features import FEATURES, COMPACT, build_features
from model_improvement.models import Family, Settings, Suite
from model_improvement.run import load_preparation, validation_six, trusted_load

OUTPUT = ROOT/"reports/model_improvement_v4"
VIEWS = {
    "legacy": WINDOW_FEATURES,
    "relational": WINDOW_FEATURES+("residual_max_1m", "residual_p95_5m", "residual_change", "log_mean_count_5m"),
    "temporal": WINDOW_FEATURES+("arrival_dev_1m", "arrival_dev_15m", "zero_run", "arrival_ewma", "residual_change", "log_max_duration_1m"),
    "compact": COMPACT,
}
ALL_FEATURES = tuple(dict.fromkeys((*FEATURES, *WINDOW_FEATURES)))
METRICS = ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")


def with_legacy(frame, baselines, history):
    """Recover the same v2 5-minute view from raw-derived multi-scale statistics."""
    frame = frame.copy()
    dates = pd.to_datetime(frame.window_start, format="ISO8601")
    widths = np.minimum(frame.groupby("case_id", sort=False).cumcount().to_numpy()+1, 5)
    prior = history.lookup(dates, widths)
    counts = np.rint(np.expm1(frame.arrival_logratio_5m+np.log1p(prior[:, 0])))
    if (counts < 0).any():
        raise ValueError("Recovered negative counts")
    reference = np.array([baselines["process"].get((a, p), baselines["adapter"].get(a, baselines["global"]))
                          for a, p in zip(frame.adapterType, frame.processId)])
    frame["log_arrivals"] = frame["log_arrivals_5m"] = np.log1p(counts)
    frame["arrival_deviation"] = frame["arrival_deviation_5m"] = frame.arrival_dev_5m
    frame["log_p50_duration"] = frame.log_p50_duration_5m
    frame["log_p95_duration"] = frame.log_p95_duration_5m
    frame["p95_duration_relative"] = frame.log_p95_duration_5m-np.log1p(reference)
    frame["missing_duration"] = frame.log_p95_duration_5m.isna().astype(float)
    return frame


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"preparation.json").exists():
        return load()
    manifest, bundle = verify_benchmark(BENCHMARK, SOURCE_CACHE)
    old = load_preparation()
    _, _, state, _ = selected_references(SEARCH, BENCHMARK, SOURCE_CACHE)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    partitions = {n: with_legacy(p, state["baselines"], history) for n, p in old["partitions"].items()}
    for name, frame in partitions.items():
        for column in WINDOW_FEATURES:
            np.testing.assert_allclose(frame[column], old["legacy_partitions"][name][column], atol=1e-7, rtol=1e-7, equal_nan=True)
    bank = validation_inputs(bundle, state, count=240, seed=2_370_042)
    features = {name: {cohort: with_legacy(build_features(data[cohort], data[cohort+"_rows"], history, old["baseline"]),
                                         state["baselines"], history) for cohort in ("observed", "normal")}
                for name, data in bank.items()}
    old_ids = set().union(*(set(d["observed"].case_id) for d in old["bank"].values()))
    if any(old_ids & set(d["observed"].case_id) for d in features.values()):
        raise ValueError("Refined validation overlaps v3")
    payload = {"partitions": partitions, "bank": features, "baseline": old["baseline"], "support": old["support"],
               "legacy_baselines": state["baselines"], "legacy_config": old["legacy_config"]}
    joblib.dump(payload, OUTPUT/"development_inputs.joblib", compress=3)
    write_json(OUTPUT/"preparation.json", {
        "development_inputs_sha256": sha256(OUTPUT/"development_inputs.joblib"),
        "benchmark_manifest_sha256": sha256(BENCHMARK/"manifest.json"),
        "code_sha256": {n: sha256(Path(__file__).parent/n) for n in ("features.py", "data.py", "refine.py")},
        "search_cases": 720, "confirmation_cases": 720, "validation_seed": 2_370_042,
        "validation_cases_per_scenario": 240, "fresh_vs_v3": True,
        "previous_experiment": "v3 retained separately, not promoted; motivated retaining legacy features",
        "normal_reference_counts": {n: len(p) for n, p in partitions.items()},
        "normal_reference_source_counts": state["source_reference_counts"],
        "independent_source_holdout": len(state["reserved_template_ids"]),
        "normal_reference_episodes": state["metadata"],
        "fpr_budget": "EACH strategy pooled normal FPR<=5%; different from v3's 2.5% scenario-macro cap. Both FPR and F1 reported, NOT a directly equivalent selection experiment."})
    return payload


def load():
    record = json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"))
    if sha256(BENCHMARK/"manifest.json") != record["benchmark_manifest_sha256"]:
        raise ValueError("Different benchmark")
    for name, digest in record["code_sha256"].items():
        if sha256(Path(__file__).parent/name) != digest:
            raise ValueError(f"Stale refined inputs: {name}")
    return trusted_load(OUTPUT/"development_inputs.joblib", record["development_inputs_sha256"])


def summarize(family, components):
    rows, pooled = [], np.zeros((3, 4), np.int64)
    for name, cohorts in components.items():
        counts = np.zeros((3, 4), np.int64)
        for score, adapters, y in cohorts:
            prediction = family.from_components(score, adapters) > 0
            for i, p in enumerate(prediction.T):
                counts[i] += [np.sum(~y & ~p), np.sum(~y & p), np.sum(y & ~p), np.sum(y & p)]
        pooled += counts
        rows.append({"scenario": name, "metrics": [metric_record(*c) for c in counts]})
    macro = [{"strategy": s, **{k: float(np.mean([r["metrics"][i][k] for r in rows])) for k in METRICS}}
             for i, s in enumerate(("global", "adapter", "ensemble"))]
    return {"scenarios": rows, "macro": macro, "pooled": [metric_record(*c) for c in pooled],
            "objective": float(np.mean([r["f1"] for r in macro])),
            "worst_strategy_pooled_fpr": max(metric_record(*c)["normal_false_positive_rate"] for c in pooled)}


def tune():
    content = load()
    if (OUTPUT/"selection.json").exists():
        raise FileExistsError("v4 selection already frozen")
    all_results, chosen = [], {}
    for algorithm in ("if", "kmeans"):
        settings_list = ([Settings("if", samples=n) for n in (256, 512, 1024)] if algorithm == "if" else
                         [Settings("kmeans", k=k, radius_normalization=r) for k, r in ((6, False), (6, True), (12, True))])
        best, model, fallback, fallback_model = None, None, None, None
        for view, columns in VIEWS.items():
            for settings in settings_list:
                print(f"v4 fit {algorithm} {view} samples={settings.samples} k={settings.k} radius={settings.radius_normalization}", flush=True)
                family = Family(columns, settings).fit(content["partitions"], content["support"])
                components = {}
                for name, data in content["bank"].items():
                    components[name] = []
                    for cohort in ("observed", "normal"):
                        frame = data[cohort].loc[data[cohort].validation_split.eq("search")]
                        y = frame.window_is_anomaly.to_numpy(dtype=bool) if cohort == "observed" else np.zeros(len(frame), bool)
                        components[name].append((family.components(frame)[0], frame.adapterType.to_numpy(), y))
                for q in (.975, .99, .995, .999):
                    for weight in (.25, .5, .75):
                        family.set_operating_point(q, weight)
                        metrics = summarize(family, components)
                        record = {"algorithm": algorithm, "view": view, "settings": asdict(settings), "quantile": q,
                                  "global_weight": weight, **metrics, "within_fpr_budget": metrics["worst_strategy_pooled_fpr"] <= .05}
                        all_results.append(record)
                        key = (record["objective"], -record["worst_strategy_pooled_fpr"], -len(columns), -settings.samples, -settings.k)
                        low_fpr = (-record["worst_strategy_pooled_fpr"], record["objective"])
                        if fallback is None or low_fpr > fallback[0]:
                            fallback, fallback_model = (low_fpr, record), copy.deepcopy(family)
                        if record["within_fpr_budget"] and (best is None or key > best[0]):
                            best, model = (key, record), copy.deepcopy(family)
        if best is None:
            best, model = fallback, fallback_model
        chosen[algorithm] = model
        write_json(OUTPUT/f"chosen_{algorithm}.json", best[1])
        print(f"Selected {algorithm}: {best[1]['view']}, search mean route macro F1={best[1]['objective']:.2%}", flush=True)
    suite = Suite(chosen, content["baseline"])
    suite.legacy_baselines = content["legacy_baselines"]
    # Baseline is genuinely refit with unchanged v2 settings on identical refs.
    baseline = SixModels("window", content["legacy_config"]).fit(content["partitions"], content["support"])
    defaults = Suite({a: Family(VIEWS["relational"], Settings(a)).fit(content["partitions"], content["support"])
                      for a in ("if", "kmeans")}, content["baseline"])
    validation = {name: {split: validation_six(model, content["bank"], split) for split in ("search", "confirmation")}
                  for name, model in (("baseline_v2", baseline), ("features_default", defaults), ("features_tuned", suite))}
    normal_margins = np.vstack([suite.score(d["normal"].loc[d["normal"].validation_split.eq("confirmation")])[0]
                               for d in content["bank"].values()])
    cutoffs = np.quantile(normal_margins, .99, axis=0)
    write_json(OUTPUT/"matched_fpr_reference.json", {"methods": list(METHODS), "margin_cutoffs": cutoffs.tolist(),
               "normal_reference": "v4 fresh confirmation normal controls only", "target_normal_fpr": .01,
               "normal_windows": len(normal_margins), "confirmation_realized_fpr": (normal_margins > cutoffs).mean(axis=0).tolist(),
               "purpose": "Equal-budget diagnosis, not production threshold or reselection"})
    joblib.dump(suite, OUTPUT/"models_v4.joblib", compress=3)
    write_json(OUTPUT/"search_results.json", all_results)
    write_json(OUTPUT/"validation_results.json", validation)
    write_json(OUTPUT/"selection.json", {
        "families": {a: json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in chosen},
        "model_file": "models_v4.joblib", "model_sha256": sha256(OUTPUT/"models_v4.joblib"),
        "training_code_sha256": {n: sha256(Path(__file__).parent/n) for n in ("features.py", "data.py", "models.py", "refine.py")},
        "benchmark_manifest_sha256": sha256(BENCHMARK/"manifest.json"),
        "search_rule": "Equal-scenario macro F1 averaged over three strategies, with EACH strategy pooled normal FPR<=5%. Ties lower FPR/fewer features/smaller model. No eligible candidate: lowest FPR, flagged.",
        "confirmation_used_for_selection": False, "final_test_used_for_selection": False, "hard_used_for_selection": False,
        "candidate_models_per_algorithm": 12, "operating_points_per_model": 12,
        "new_validation_seed": 2_370_042, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "previous_experiment_used": "v3 results motivated feature retention and a revised FPR objective; v4 confirmation uses new episodes but shares held-out source pool/generator."})


def frozen():
    selection = json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"))
    for name, digest in selection["training_code_sha256"].items():
        if sha256(Path(__file__).parent/name) != digest:
            raise ValueError(f"Frozen v4 code changed: {name}")
    return trusted_load(OUTPUT/"models_v4.joblib", selection["model_sha256"]), selection


def evaluate():
    suite, selection = frozen()
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    v3_summary = json.loads((V3/"summary.json").read_text(encoding="utf-8"))
    summaries = []
    for previous in v3_summary["datasets"]:
        name = previous["dataset"]
        destination = OUTPUT/name
        destination.mkdir(exist_ok=True)
        if (destination/"metrics.json").exists():
            existing = json.loads((destination/"metrics.json").read_text(encoding="utf-8"))
            if existing["model_sha256"] != selection["model_sha256"]:
                raise ValueError("Different frozen v4 model")
            for c, digest in existing["arrays_sha256"].items():
                if sha256(destination/f"{c}.npz") != digest:
                    raise ValueError("Changed v4 evaluation arrays")
            summaries.append(existing)
            continue
        arrays, counts, supported = {}, Counts(), Counts()
        cases = pd.read_csv(BENCHMARK/name/"cases.csv", encoding="utf-8-sig")
        events = pd.read_csv(BENCHMARK/name/"events.csv", encoding="utf-8-sig")
        for cohort, digest in previous["arrays_sha256"].items():
            if sha256(V3/name/f"{cohort}.npz") != digest:
                raise ValueError("Raw-derived v3 feature cache changed")
            with np.load(V3/name/f"{cohort}.npz", allow_pickle=False) as file:
                a = {k: file[k] for k in file.files if k not in ("margin", "prediction", "covered")}
            # No v3 test score/decision enters model fitting or v4 inference.
            frame = pd.DataFrame(a["features"], columns=FEATURES)
            frame["case_id"], frame["adapterType"], frame["processId"] = a["case_index"], a["adapter"], a["process"]
            frame["window_start"] = pd.to_datetime(a["window_end_seconds"]-60, unit="s")
            frame = with_legacy(frame, suite.legacy_baselines, history)
            margins, predictions, coverage = [], [], []
            for start in range(0, len(frame), 50_000):
                margin, prediction, cover = suite.score(frame.iloc[start:start+50_000])
                margins.append(margin.astype(np.float32)); predictions.append(prediction); coverage.append(cover)
            a["margin"], a["prediction"], a["covered"] = np.vstack(margins), np.vstack(predictions), np.concatenate(coverage)
            a["features"] = frame[list(ALL_FEATURES)].to_numpy(dtype=np.float32)
            counts.update(a["truth"], a["prediction"])
            supported.update(a["truth"], a["prediction"], a["covered"])
            np.savez_compressed(destination/f"{cohort}.npz", **a)
            arrays[cohort] = a
        result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(), "supported_only_metrics": supported.records(),
                  "coverage": float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                  "events": event_results(arrays["observed"], arrays["normal"], events, cases), "model_sha256": selection["model_sha256"],
                  "arrays_sha256": {c: sha256(destination/f"{c}.npz") for c in arrays}}
        write_json(destination/"metrics.json", result)
        summaries.append(result)
        print(f"v4 {name}: IF ensemble {result['metrics'][2]['f1']:.2%}; clustering ensemble {result['metrics'][5]['f1']:.2%}", flush=True)
    pooled = Counts()
    for d in summaries:
        pooled.values += Counts.from_records(d["metrics"]).values
    macro = [{"method": m, **{k: float(np.mean([d["metrics"][i][k] for d in summaries if d["dataset"] != "mixed_hard"])) for k in METRICS}}
             for i, m in enumerate(METHODS)]
    write_json(OUTPUT/"summary.json", {"datasets": summaries, "pooled": pooled.records(), "basic_macro": macro,
               "thresholds": suite.threshold_records(), "support": suite.families["if"].support, "feature_columns": list(ALL_FEATURES),
               "model_sha256": selection["model_sha256"], "protocol": {
                   "input": "Process-only stream with causal windows and synthetic prior traffic history",
                   "decision_stride_minutes": 1, "lookbacks_minutes": [1, 5, 15], "point_adjustment": False,
                   "normal_reference_counts": {n: len(p) for n, p in load()["partitions"].items()},
                   "limitations": ["Adaptive v4 design after v3; new confirmation episodes share source pool/generator.",
                                   "Original benchmark already examined; not a fresh blind external test.",
                                   "Not full source-data training; compact normal archive and independent replays.",
                                   "Traffic prior is synthetic, not a learned real operational baseline.",
                                   "No automatic v1 production replacement. Hard rules unchanged.",
                                   "S does not certify incident-free real source workflows."]}})
    verify_benchmark(BENCHMARK, SOURCE_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "tune", "evaluate", "all"), default="all")
    args = parser.parse_args()
    if args.stage in ("prepare", "all"): prepare()
    if args.stage in ("tune", "all"): tune()
    if args.stage in ("evaluate", "all"): evaluate()
