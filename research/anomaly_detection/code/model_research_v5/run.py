"""v5 normal-only fit, fresh labelled search, untouched confirmation and test.

Prior v4 test results motivated this experiment, so this is adaptive research,
NOT a new blind external benchmark. No final labels enter candidate selection.
"""
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd

from model_evaluation.data import SCENARIOS, sha256, verify_benchmark
from model_evaluation.metrics import Counts, metric_record
from model_evaluation.evaluate import event_results
from model_evaluation.tune_windows import selected_references, write_json
from model_evaluation.windowing import PriorHistory
from model_improvement.data import BENCHMARK, SOURCE_CACHE, SEARCH, reference_rows, validation_inputs
from model_improvement.features import build_features
from model_improvement.refine import OUTPUT as V4, ALL_FEATURES as OLD_FEATURES, load as load_v4, frozen as frozen_v4, with_legacy
from model_improvement.run import trusted_load
from model_research_v5.features import context_features, segment_native, VIEWS, ALL_FEATURES
from model_research_v5.models import METHODS, Family, Settings, Suite

OUTPUT = ROOT/"reports/model_research_v5"
SEED = 7_170_042
RESETS = (6, 15, 90)
METRIC_NAMES = ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")


def input_fingerprints():
    files = [BENCHMARK/"manifest.json", SOURCE_CACHE, V4/"selection.json"]
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha256(p) for p in files}


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"preparation.json").exists(): return load()
    manifest, bundle = verify_benchmark(BENCHMARK, SOURCE_CACHE)
    old = load_v4()
    _, _, state, _ = selected_references(SEARCH, BENCHMARK, SOURCE_CACHE)
    _, rich, support = reference_rows(bundle, state)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    partitions, replay_stats = {}, []
    for name, native in state["native"].items():
        variants = []
        for width in RESETS:
            reset, raw = segment_native(native, rich[name], width)
            frame = build_features(reset, raw, history, old["baseline"])
            frame = with_legacy(frame, old["legacy_baselines"], history)
            variants.append(context_features(frame))
            replay_stats.append({"partition": name, "reset_minutes": width, "windows": len(frame),
                                 "reset_streams": int(frame.case_id.nunique()),
                                 "original_streams": int(native.case_id.nunique())})
        partitions[name] = pd.concat(variants, ignore_index=True)
        print(f"v5 normal {name}: {len(partitions[name]):,} augmented decision views", flush=True)
    native_bank = validation_inputs(bundle, state, count=240, seed=SEED)
    bank, legacy_bank = {}, {}
    prior_ids = set().union(*(set(data["observed"].case_id) for data in old["bank"].values()))
    for name, data in native_bank.items():
        bank[name], legacy_bank[name] = {}, {}
        for cohort in ("observed", "normal"):
            frame = with_legacy(build_features(data[cohort], data[cohort+"_rows"], history, old["baseline"]),
                                old["legacy_baselines"], history)
            if set(frame.case_id) & prior_ids: raise ValueError("Fresh cases overlap v4")
            legacy_bank[name][cohort] = frame
            bank[name][cohort] = context_features(frame)
    original = {n: context_features(p) for n, p in old["partitions"].items()}
    payload = {"partitions": partitions, "original_partitions": original, "bank": bank,
               "legacy_bank": legacy_bank, "source_support": support}
    joblib.dump(payload, OUTPUT/"development_inputs.joblib", compress=3)
    write_json(OUTPUT/"preparation.json", {
        "inputs": input_fingerprints(), "data_sha256": sha256(OUTPUT/"development_inputs.joblib"),
        "code_sha256": {n: sha256(Path(__file__).parent/n) for n in ("features.py", "models.py", "run.py")},
        "validation_seed": SEED, "search_cases": 720, "confirmation_cases": 720,
        "validation_cases_per_scenario": 240, "normal_augmented_windows": {n: len(p) for n, p in partitions.items()},
        "original_reference_windows": {n: len(p) for n, p in original.items()},
        "independent_source_reference_rows": state["source_reference_counts"],
        "held_out_source_templates": len(state["reserved_template_ids"]), "replay_reset_views": replay_stats,
        "augmentation_not_new_independent_data": True,
        "limitations": ["Fresh episodes share the generator and held-out source pool used in previous experiments.",
                        "Reset augmentation repeats normal observations; does not triple independent source support.",
                        "Validation is a source-template hash holdout, not chronological future validation.",
                        "Synthetic traffic prior; genuine operating-history validation remains necessary."]})
    return payload


def load():
    record = json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"))
    if record["inputs"] != input_fingerprints(): raise ValueError("Changed v5 inputs")
    for name, digest in record["code_sha256"].items():
        if sha256(Path(__file__).parent/name) != digest: raise ValueError(f"Stale preparation: {name}")
    return trusted_load(OUTPUT/"development_inputs.joblib", record["data_sha256"])


def components(family, bank, split):
    result = {}
    for name, data in bank.items():
        result[name] = []
        for cohort in ("observed", "normal"):
            frame = data[cohort].loc[data[cohort].validation_split.eq(split)]
            truth = frame.window_is_anomaly.to_numpy(dtype=bool) if cohort == "observed" else np.zeros(len(frame), bool)
            result[name].append((family.components(frame)[0], frame.adapterType.to_numpy(), frame.phase.to_numpy(), truth))
    return result


def summarize(family, cached):
    scenarios, pooled = [], np.zeros((3, 4), dtype=np.int64)
    for name, cohorts in cached.items():
        counts = np.zeros((3, 4), dtype=np.int64)
        for raw, adapters, phases, y in cohorts:
            pred = family.from_components(raw, adapters, phases) > 0
            for i, p in enumerate(pred.T):
                counts[i] += [np.sum(~y & ~p), np.sum(~y & p), np.sum(y & ~p), np.sum(y & p)]
        scenarios.append({"scenario": name, "metrics": [metric_record(*c) for c in counts]})
        pooled += counts
    macro = [{"strategy": strategy, **{k: float(np.mean([d["metrics"][i][k] for d in scenarios])) for k in METRIC_NAMES}}
             for i, strategy in enumerate(("global", "adapter", "ensemble"))]
    return {"scenarios": scenarios, "pooled": [metric_record(*c) for c in pooled], "macro": macro,
            "objective": float(np.mean([r["f1"] for r in macro])),
            "worst_scenario_strategy_fpr": max(m["normal_false_positive_rate"] for d in scenarios for m in d["metrics"]),
            "worst_strategy_pooled_fpr": max(metric_record(*c)["normal_false_positive_rate"] for c in pooled)}


def six_metrics(suite, bank, split):
    scenarios, pooled = [], Counts()
    for name, data in bank.items():
        counts = Counts()
        for cohort in ("observed", "normal"):
            frame = data[cohort].loc[data[cohort].validation_split.eq(split)]
            truth = frame.window_is_anomaly.to_numpy(dtype=bool) if cohort == "observed" else np.zeros(len(frame), bool)
            counts.update(truth, suite.score(frame)[1])
        scenarios.append({"scenario": name, "metrics": counts.records()})
        pooled.values += counts.values
    return {"scenarios": scenarios, "pooled": pooled.records(),
            "macro": [{"method": method, **{k: float(np.mean([s["metrics"][i][k] for s in scenarios])) for k in METRIC_NAMES}}
                      for i, method in enumerate(METHODS)]}


def candidates(algorithm):
    if algorithm == "if":
        return [Settings("if", samples=s, rotations=r) for s, r in ((256, 1), (1024, 1), (512, 4), (1024, 4))]
    return [Settings("kmeans", k=k, shape_distance=d) for k, d in ((6, False), (6, True), (12, False), (12, True))]


def tune():
    data = load()
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v5 already frozen")
    all_results, selected = [], {}
    for algorithm in ("if", "kmeans"):
        best = fallback = model = fallback_model = None
        for view, columns in VIEWS.items():
            for settings in candidates(algorithm):
                print(f"v5 fit {algorithm} {view} {asdict(settings)}", flush=True)
                family = Family(columns, settings).fit(data["partitions"], data["source_support"])
                cached = components(family, data["bank"], "search")
                for aware in (False, True):
                    for quantile in (.975, .99, .995, .999):
                        for weight in (.25, .5, .75):
                            family.set_operating_point(quantile, weight, aware)
                            result = summarize(family, cached)
                            record = {"algorithm": algorithm, "view": view, "settings": asdict(settings),
                                      "quantile": quantile, "global_weight": weight, "phase_aware": aware,
                                      "within_budget": result["worst_scenario_strategy_fpr"] <= .05, **result}
                            all_results.append(record)
                            key = (record["objective"], -record["worst_strategy_pooled_fpr"], -len(columns), -settings.rotations, -settings.k)
                            low_fpr = (-record["worst_scenario_strategy_fpr"], record["objective"])
                            if fallback is None or low_fpr > fallback[0]:
                                fallback, fallback_model = (low_fpr, record), copy.deepcopy(family)
                            if record["within_budget"] and (best is None or key > best[0]):
                                best, model = (key, record), copy.deepcopy(family)
                print(f"  best budget-compliant macro F1: {best[1]['objective']:.2%}" if best else "  no budget-compliant candidate yet", flush=True)
        if best is None: best, model = fallback, fallback_model
        selected[algorithm] = model
        write_json(OUTPUT/f"chosen_{algorithm}.json", best[1])
    suite = Suite(selected)
    old_model, _ = frozen_v4()
    # Ablations use predetermined defaults, not test-selected alternatives.
    feature_only = Suite({a: Family(VIEWS["self_relative"], Settings(a)).fit(data["original_partitions"], data["source_support"])
                          for a in ("if", "kmeans")})
    for family in feature_only.families.values(): family.set_operating_point(.99, .5, False)
    reset_only = Suite({a: Family(VIEWS["self_relative"], Settings(a)).fit(data["partitions"], data["source_support"])
                        for a in ("if", "kmeans")})
    for family in reset_only.families.values(): family.set_operating_point(.99, .5, False)
    validation = {name: {split: six_metrics(model, bank, split) for split in ("search", "confirmation")}
                  for name, model, bank in (("baseline_v4", old_model, data["legacy_bank"]),
                                           ("causal_features_only", feature_only, data["bank"]),
                                           ("normal_reset_augmentation", reset_only, data["bank"]),
                                           ("tuned_v5", suite, data["bank"]))}
    joblib.dump(suite, OUTPUT/"models_v5.joblib", compress=3)
    write_json(OUTPUT/"search_results.json", all_results)
    write_json(OUTPUT/"validation_results.json", validation)
    normal_margin = np.vstack([suite.score(d["normal"].loc[d["normal"].validation_split.eq("confirmation")])[0] for d in data["bank"].values()])
    write_json(OUTPUT/"matched_fpr_reference.json", {
        "target": .01, "normal_windows": len(normal_margin), "margin_cutoffs": np.quantile(normal_margin, .99, axis=0).tolist(),
        "note": "Confirmation normal controls ONLY. Diagnostic operating point, not selection/deployment."})
    write_json(OUTPUT/"selection.json", {
        "families": {a: json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in selected},
        "model_sha256": sha256(OUTPUT/"models_v5.joblib"), "inputs": input_fingerprints(),
        "training_code_sha256": {n: sha256(Path(__file__).parent/n) for n in ("features.py", "models.py", "run.py")},
        "normal_only_model_fit": True, "labelled_validation_used_for_selection": True,
        "confirmation_used_for_selection": False, "final_used_for_selection": False, "hard_used_for_selection": False,
        "objective": "Mean equal-scenario F1 averaged over three strategies. EACH scenario/strategy normal FPR<=5%; no feasible candidate: lowest worst FPR, flagged.",
        "candidate_fits_per_algorithm": 8, "operating_points_per_fit": 24,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "limitation": "Adaptive research after previously inspected v4 tests; new validation episodes share sources/generator."})
    print("v5 frozen; no reselection after confirmation", flush=True)


def frozen():
    record = json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"))
    if record["inputs"] != input_fingerprints(): raise ValueError("Frozen v5 input changed")
    for name, digest in record["training_code_sha256"].items():
        if sha256(Path(__file__).parent/name) != digest: raise ValueError(f"Frozen v5 code changed: {name}")
    return trusted_load(OUTPUT/"models_v5.joblib", record["model_sha256"]), record


def frame_from_arrays(arrays):
    frame = pd.DataFrame(arrays["features"], columns=OLD_FEATURES, dtype=float)
    frame["case_id"], frame["adapterType"], frame["processId"] = arrays["case_index"], arrays["adapter"], arrays["process"]
    frame["window_start"] = pd.to_datetime(arrays["window_end_seconds"]-60, unit="s")
    return context_features(frame)


def evaluate():
    suite, selection = frozen()
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    prior = json.loads((V4/"summary.json").read_text(encoding="utf-8"))
    summaries, pooled, supported = [], Counts(), Counts()
    for dataset in prior["datasets"]:
        name = dataset["dataset"]
        folder = OUTPUT/name
        folder.mkdir(exist_ok=True)
        if (folder/"metrics.json").exists():
            result = json.loads((folder/"metrics.json").read_text(encoding="utf-8"))
            if result["model_sha256"] != selection["model_sha256"]: raise ValueError("Different prior evaluation")
            for c, digest in result["arrays_sha256"].items():
                if sha256(folder/f"{c}.npz") != digest: raise ValueError("Changed previous evaluation")
        else:
            arrays, counts, support_counts = {}, Counts(), Counts()
            for cohort, digest in dataset["arrays_sha256"].items():
                if sha256(V4/name/f"{cohort}.npz") != digest: raise ValueError("Changed raw-derived v4 feature cache")
                with np.load(V4/name/f"{cohort}.npz", allow_pickle=False) as file:
                    raw = {k: file[k] for k in file.files if k not in ("margin", "prediction", "covered")}
                frame = frame_from_arrays(raw)
                margins, predictions, coverage = [], [], []
                for start in range(0, len(frame), 50_000):
                    m, p, c = suite.score(frame.iloc[start:start+50_000])
                    margins.append(m.astype(np.float32)); predictions.append(p); coverage.append(c)
                raw["margin"], raw["prediction"], raw["covered"] = np.vstack(margins), np.vstack(predictions), np.concatenate(coverage)
                raw["features"] = frame[list(ALL_FEATURES)].to_numpy(dtype=np.float32)
                raw["phase"] = frame.phase.to_numpy(dtype=str)
                counts.update(raw["truth"], raw["prediction"])
                support_counts.update(raw["truth"], raw["prediction"], raw["covered"])
                np.savez_compressed(folder/f"{cohort}.npz", **raw)
                arrays[cohort] = raw
            cases = pd.read_csv(BENCHMARK/name/"cases.csv", encoding="utf-8-sig")
            events = pd.read_csv(BENCHMARK/name/"events.csv", encoding="utf-8-sig")
            result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(),
                      "supported_only_metrics": support_counts.records(),
                      "events": event_results(arrays["observed"], arrays["normal"], events, cases),
                      "coverage": float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                      "model_sha256": selection["model_sha256"], "arrays_sha256": {c: sha256(folder/f"{c}.npz") for c in arrays}}
            write_json(folder/"metrics.json", result)
        pooled.values += Counts.from_records(result["metrics"]).values
        supported.values += Counts.from_records(result["supported_only_metrics"]).values
        summaries.append(result)
        print(f"v5 {name}: IF ensemble F1={result['metrics'][2]['f1']:.2%}; KMeans ensemble F1={result['metrics'][5]['f1']:.2%}", flush=True)
    write_json(OUTPUT/"summary.json", {
        "pooled": pooled.records(), "supported_only": supported.records(), "datasets": summaries,
        "basic_macro": [{"method": method, **{k: float(np.mean([s["metrics"][i][k] for s in summaries if s["dataset"] != "mixed_hard"])) for k in METRIC_NAMES}}
                        for i, method in enumerate(METHODS)],
        "thresholds": suite.threshold_records(), "support": suite.families["if"].support,
        "model_sha256": selection["model_sha256"], "completed_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": {"input": "Process-only causal minute grid + prior synthetic history", "point_adjustment": False,
                     "original_data_labels_unchanged": True, "previous_test_scores_not_features": True,
                     "normal_only_fit": True, "fresh_search_confirmation_seed": SEED,
                     "limitations": ["Previously inspected final benchmark, not blind external validation.",
                                     "Adaptive v5 design and shared generator/source-template pool.",
                                     "Reset augmentation is repeated data, not independent normal source growth.",
                                     "Anomalies may contaminate subsequent self-relative history.",
                                     "New-stream early minutes have little context; no assumed clean warm-up.",
                                     "Not full 3.7M-row training; original compact source archive/replays.",
                                     "Synthetic traffic prior; no automatic production model replacement."]}})
    verify_benchmark(BENCHMARK, SOURCE_CACHE)


def third_candidate():
    """Small LOF proof-of-concept on validation ONLY, not a deployed third model."""
    data = load()
    if (OUTPUT/"third_candidate.json").exists(): raise FileExistsError("LOF candidate already evaluated")
    chosen = best_model = None
    results = []
    for neighbors in (30, 75):
        family = Family(VIEWS["self_relative"], Settings("lof", neighbors=neighbors)).fit(data["partitions"], data["source_support"])
        cache = components(family, data["bank"], "search")
        for q in (.99, .995, .999):
            family.set_operating_point(q, .5, True)
            result = summarize(family, cache)
            record = {"neighbors": neighbors, "quantile": q, **result, "within_budget": result["worst_scenario_strategy_fpr"] <= .05}
            results.append(record)
            key = (record["within_budget"], record["objective"] if record["within_budget"] else -record["worst_scenario_strategy_fpr"], record["objective"])
            if chosen is None or key > chosen[0]: chosen, best_model = (key, record), copy.deepcopy(family)
    confirmation = summarize(best_model, components(best_model, data["bank"], "confirmation"))
    # Diagnostic inference latency, without model refit or retrospective relabeling.
    sample = next(iter(data["bank"].values()))["observed"].iloc[:500]
    best_model.score(sample)
    begin = time.perf_counter(); best_model.score(sample); elapsed = time.perf_counter()-begin
    joblib.dump(best_model, OUTPUT/"lof_validation_candidate.joblib", compress=3)
    write_json(OUTPUT/"third_candidate.json", {"algorithm": "LOF", "selected_on_search": chosen[1], "search_candidates": results,
        "confirmation": confirmation, "confirmation_used_for_selection": False, "final_test_evaluated": False,
        "features": list(VIEWS["self_relative"]), "novelty": True,
        "model_sha256": sha256(OUTPUT/"lof_validation_candidate.joblib"),
        "latency": {"rows": len(sample), "seconds": elapsed, "milliseconds_per_row_batch_average": elapsed/len(sample)*1000},
        "status": "Validation-only candidate. NOT promoted or blended into the six primary strategies.",
        "limitations": ["Neighbor storage/search grows with normal reference size; benchmark latency is not service P99.",
                        "Duplicated replay vectors/constant dimensions can cause unstable local density ratios.",
                        "No claimed final-test or production performance; confirmation shares source pool/generator."]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "tune", "evaluate", "third", "all"), default="all")
    args = parser.parse_args()
    if args.stage in ("prepare", "all"): prepare()
    if args.stage in ("tune", "all"): tune()
    if args.stage in ("evaluate", "all"): evaluate()
    if args.stage == "third": third_candidate()
