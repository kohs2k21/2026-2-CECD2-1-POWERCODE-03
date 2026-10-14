"""Reproducible preparation, limited tuning, confirmation and frozen evaluation.

python code/model_improvement/run.py --stage prepare|tune|evaluate|all
Outputs are v3 only; datasets, v1 serving artifact and v2 results are untouched.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
import sklearn

from model_evaluation.data import (SCENARIOS, labels_for_rows, observed_windows, sha256,
                                   verify_benchmark)
from model_evaluation.evaluate import event_results
from model_evaluation.metrics import Counts, metric_record
from model_evaluation.models import METHODS, SixModels
from model_evaluation.tune_windows import selected_references, write_json
from model_evaluation.windowing import PriorHistory, aggregate_windows
from model_improvement.data import (BENCHMARK, SEARCH, SOURCE_CACHE, OUTPUT, reference_rows,
                                    validation_inputs, final_rows, control_grid)
from model_improvement.features import ConditionalBaseline, FEATURES, COMPACT, build_features
from model_improvement.models import Settings, Family, Suite

NAMES = ("global", "adapter", "ensemble")
METRICS = ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")
QUANTILES = (.99, .995, .999, .9995)
WEIGHTS = (.25, .5, .75)
FPR_BUDGET = .025


def trusted_load(path, expected):
    if sha256(path) != expected:
        raise ValueError(f"Artifact changed: {path.name}")
    # Only our just-created local files or previously hash-verified v2 inputs.
    return joblib.load(path)


def preparation():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if (OUTPUT/"preparation.json").exists():
        print("Preparation exists; verifying and reusing", flush=True)
        return load_preparation()
    manifest, bundle = verify_benchmark(BENCHMARK, SOURCE_CACHE)
    config, legacy_parts, state, selection = selected_references(SEARCH, BENCHMARK, SOURCE_CACHE)
    train, rich, support = reference_rows(bundle, state)
    baseline = ConditionalBaseline.fit(train)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    partitions = {n: build_features(state["native"][n], rich[n], history, baseline) for n in rich}
    bank = validation_inputs(bundle, state)
    features, legacy_bank = {}, {}
    for name, data in bank.items():
        features[name] = {cohort: build_features(data[cohort], data[cohort+"_rows"], history, baseline)
                          for cohort in ("observed", "normal")}
        legacy_bank[name] = {cohort: aggregate_windows(data[cohort], data[cohort+"_rows"], 5, history, state["baselines"])
                            for cohort in ("observed", "normal")}
    content = {"partitions": partitions, "bank": features, "baseline": baseline, "support": support,
               "legacy_partitions": legacy_parts, "legacy_bank": legacy_bank, "legacy_config": config}
    joblib.dump(content, OUTPUT/"development_inputs.joblib", compress=3)
    case_sets = {split: set().union(*(set(data["observed"].loc[data["observed"].validation_split.eq(split)].case_id)
                                     for data in features.values())) for split in ("search", "confirmation")}
    if case_sets["search"] & case_sets["confirmation"]:
        raise ValueError("Case split leaked")
    record = {"benchmark_manifest_sha256": sha256(BENCHMARK/"manifest.json"),
              "source_cache_sha256": sha256(SOURCE_CACHE),
              "development_inputs_sha256": sha256(OUTPUT/"development_inputs.joblib"),
              "feature_code_sha256": {n: sha256(Path(__file__).parent/n) for n in ("features.py", "data.py")},
              "case_counts": {n: len(v) for n, v in case_sets.items()},
              "normal_reference_counts": {n: len(p) for n, p in partitions.items()},
              "source_template_counts": state["source_reference_counts"],
              "normal_reference_episodes": state["metadata"],
              "validation_seed": 1_910_042, "validation_cases_per_scenario": 120,
              "validation_source_templates": len(state["reserved_template_ids"]),
              "validation_source_overlaps_window_reference": 0,
              "frozen_policy": manifest["frozen_policy"],
              "note": "Fresh episodes on held-out source templates; hash holdout, NOT chronological future holdout. Normal paired controls never fit models."}
    write_json(OUTPUT/"preparation.json", record)
    return content


def load_preparation():
    record = json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"))
    if record["benchmark_manifest_sha256"] != sha256(BENCHMARK/"manifest.json") or record["source_cache_sha256"] != sha256(SOURCE_CACHE):
        raise ValueError("Different preparation inputs")
    for name, digest in record["feature_code_sha256"].items():
        if sha256(Path(__file__).parent/name) != digest:
            raise ValueError(f"Prepared features stale: {name}")
    return trusted_load(OUTPUT/"development_inputs.joblib", record["development_inputs_sha256"])


def components_for(family, bank, split):
    result = {}
    for name, data in bank.items():
        result[name] = []
        for cohort in ("observed", "normal"):
            frame = data[cohort].loc[data[cohort].validation_split.eq(split)]
            components, coverage = family.components(frame)
            y = frame.window_is_anomaly.to_numpy(dtype=bool) if cohort == "observed" else np.zeros(len(frame), bool)
            result[name].append((components, frame.adapterType.to_numpy(), y, coverage))
    return result


def family_metrics(family, bank_components):
    scenarios = []
    for name, cohorts in bank_components.items():
        counts = np.zeros((3, 4), dtype=np.int64)
        for scores, adapters, y, _ in cohorts:
            prediction = family.from_components(scores, adapters) > 0
            for i in range(3):
                p = prediction[:, i]
                counts[i] += [np.sum(~y & ~p), np.sum(~y & p), np.sum(y & ~p), np.sum(y & p)]
        scenarios.append({"scenario": name, "metrics": [
            {"strategy": strategy, **metric_record(*counts[i])} for i, strategy in enumerate(NAMES)]})
    macro = [{"strategy": strategy, **{k: float(np.mean([d["metrics"][i][k] for d in scenarios]))
                                      for k in METRICS}} for i, strategy in enumerate(NAMES)]
    return {"scenarios": scenarios, "macro": macro,
            "objective": float(np.mean([r["f1"] for r in macro])),
            "worst_strategy_macro_fpr": max(r["normal_false_positive_rate"] for r in macro)}


def candidates(algorithm):
    if algorithm == "if":
        return [Settings(algorithm, trees=t, samples=s, max_features=f)
                for t, s, f in ((128, 512, 1.), (128, 256, 1.), (256, 512, 1.),
                                (128, 1024, 1.), (128, 512, .7), (256, 256, .7))]
    return [Settings(algorithm, k=k, radius_normalization=radius)
            for k, radius in ((6, False), (6, True), (12, True), (24, True), (3, True), (12, False))]


def validation_six(suite, bank, split):
    records = []
    for name, data in bank.items():
        counts = Counts()
        for cohort in ("observed", "normal"):
            frame = data[cohort].loc[data[cohort].validation_split.eq(split)]
            truth = frame.window_is_anomaly.to_numpy(dtype=bool) if cohort == "observed" else np.zeros(len(frame), bool)
            counts.update(truth, suite.score(frame)[1])
        records.append({"scenario": name, "metrics": counts.records()})
    macro = [{"method": m, **{k: float(np.mean([d["metrics"][i][k] for d in records])) for k in METRICS}}
             for i, m in enumerate(METHODS)]
    pooled = Counts()
    for d in records:
        pooled.values += Counts.from_records(d["metrics"]).values
    return {"scenarios": records, "macro": macro, "pooled": pooled.records()}


def tune():
    content = load_preparation()
    if (OUTPUT/"selection.json").exists():
        raise FileExistsError("Selection already frozen. Use another version for a new search.")
    chosen, all_records, feature_defaults = {}, [], {}
    for algorithm in ("if", "kmeans"):
        best, best_family = None, None
        fallback, fallback_family = None, None
        for feature_set, columns in (("compact", COMPACT), ("full", FEATURES)):
            for index, settings in enumerate(candidates(algorithm)):
                print(f"Fit {algorithm} {feature_set} candidate {index+1}/6", flush=True)
                family = Family(columns, settings).fit(content["partitions"], content["support"])
                components = components_for(family, content["bank"], "search")
                if feature_set == "full" and index == 0:
                    feature_defaults[algorithm] = family
                    joblib.dump(family, OUTPUT/f"feature_default_{algorithm}.joblib", compress=3)
                # Thresholds always come from independent NORMAL threshold refs.
                # Search labels choose operating point, never train trees/centers.
                for q in QUANTILES:
                    for weight in WEIGHTS:
                        family.set_operating_point(q, weight)
                        metrics = family_metrics(family, components)
                        record = {"algorithm": algorithm, "feature_set": feature_set, "settings": asdict(settings),
                                  "quantile": q, "global_weight": weight, **metrics,
                                  "within_fpr_budget": metrics["worst_strategy_macro_fpr"] <= FPR_BUDGET}
                        all_records.append(record)
                        key = (metrics["objective"], -metrics["worst_strategy_macro_fpr"], -len(columns), -settings.trees, -settings.k)
                        conservative_key = (-metrics["worst_strategy_macro_fpr"], metrics["objective"])
                        if fallback is None or conservative_key > fallback[0]:
                            fallback = conservative_key, record
                            # Deepcopy includes fitted estimator, no fit on search.
                            import copy
                            fallback_family = copy.deepcopy(family)
                        if record["within_fpr_budget"] and (best is None or key > best[0]):
                            import copy
                            best, best_family = (key, record), copy.deepcopy(family)
                print(f"  current best eligible: {best[1]['objective']:.2%}" if best else "  no budget-compliant candidate yet", flush=True)
        if best is None:
            best, best_family = fallback, fallback_family
        chosen[algorithm] = best_family
        write_json(OUTPUT/f"chosen_{algorithm}.json", best[1])
    suite = Suite(chosen, content["baseline"])
    default_suite = Suite({a: joblib.load(OUTPUT/f"feature_default_{a}.joblib") for a in ("if", "kmeans")}, content["baseline"])
    baseline_suite = SixModels("window", content["legacy_config"]).fit(content["legacy_partitions"], content["support"])
    # Confirmation is evaluated once AFTER both families are frozen; no reselection.
    validation = {}
    for name, model, bank in (("baseline_v2", baseline_suite, content["legacy_bank"]),
                              ("features_default", default_suite, content["bank"]),
                              ("features_tuned", suite, content["bank"])):
        validation[name] = {split: validation_six(model, bank, split) for split in ("search", "confirmation")}
    # Diagnostic equal-budget comparison, NEVER a new production operating point.
    # Normal confirmation controls only; no final-test score/label is inspected.
    normal_margins = np.vstack([suite.score(d["normal"].loc[d["normal"].validation_split.eq("confirmation")])[0]
                               for d in content["bank"].values()])
    diagnostic_cutoffs = np.quantile(normal_margins, .99, axis=0)
    write_json(OUTPUT/"matched_fpr_reference.json", {
        "methods": list(METHODS), "margin_cutoffs": diagnostic_cutoffs.tolist(),
        "normal_reference": "Fresh confirmation paired normal controls ONLY; pooled over six scenarios",
        "target_normal_fpr": .01, "normal_windows": len(normal_margins),
        "purpose": "Post-hoc equal-budget role comparison, NOT model selection or deployment threshold",
        "confirmation_realized_fpr": (normal_margins > diagnostic_cutoffs).mean(axis=0).tolist(),
    })
    joblib.dump(suite, OUTPUT/"models_v3.joblib", compress=3)
    write_json(OUTPUT/"search_results.json", all_records)
    write_json(OUTPUT/"validation_results.json", validation)
    write_json(OUTPUT/"selection.json", {
        "families": {a: json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in chosen},
        "search_rule": "Maximize equal-scenario macro F1 averaged over global/adapter/ensemble, subject to EACH strategy macro normal FPR<=2.5%. Ties: lower FPR, fewer features, smaller model. If none feasible: lowest FPR, explicitly flagged.",
        "fpr_budget": FPR_BUDGET, "confirmation_used_for_selection": False,
        "final_test_used_for_selection": False, "hard_used_for_selection": False,
        "candidates_per_algorithm": 12, "operating_points_per_candidate": len(QUANTILES)*len(WEIGHTS),
        "model_sha256": sha256(OUTPUT/"models_v3.joblib"),
        "training_code_sha256": {n: sha256(Path(__file__).parent/n) for n in ("features.py", "data.py", "models.py", "run.py")},
        "benchmark_manifest_sha256": sha256(BENCHMARK/"manifest.json"),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "dependencies": {"python": platform.python_version(), "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
    })
    print("Models and operating points frozen. Confirmation was not used for reselection.", flush=True)


def freeze_load():
    record = json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"))
    for name, digest in record["training_code_sha256"].items():
        if sha256(Path(__file__).parent/name) != digest:
            raise ValueError(f"Frozen training code changed: {name}")
    if sha256(BENCHMARK/"manifest.json") != record["benchmark_manifest_sha256"]:
        raise ValueError("Frozen benchmark manifest changed")
    return trusted_load(OUTPUT/"models_v3.joblib", record["model_sha256"]), record


def score_arrays(suite, frame, cases, normal=False):
    values, preds, covers = [], [], []
    for start in range(0, len(frame), 50_000):
        margin, pred, cover = suite.score(frame.iloc[start:start+50_000])
        values.append(margin.astype(np.float32)); preds.append(pred); covers.append(cover)
    indices = {c: i for i, c in enumerate(cases.case_id)}
    from model_evaluation.data import unix_seconds
    bits = np.zeros(len(frame), dtype=np.uint8)
    if not normal:
        for i, name in enumerate(SCENARIOS):
            bits |= frame.scenario_types.fillna("").str.split(";").map(lambda v: name in v).to_numpy(dtype=np.uint8) << i
    return {"margin": np.concatenate(values), "prediction": np.concatenate(preds), "covered": np.concatenate(covers),
            "truth": np.zeros(len(frame), bool) if normal else frame.window_is_anomaly.to_numpy(dtype=bool),
            "valid": np.ones(len(frame), bool), "case_index": frame.case_id.map(indices).to_numpy(dtype=np.int32),
            "window_end_seconds": unix_seconds(frame.window_end), "scenario_bits": bits,
            "features": frame[list(FEATURES)].to_numpy(dtype=np.float32),
            "adapter": frame.adapterType.to_numpy(dtype=str), "process": frame.processId.to_numpy(dtype=str)}


def evaluate():
    suite, selection = freeze_load()
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    summaries = []
    for name in (*SCENARIOS, "mixed_hard"):
        destination = OUTPUT/name
        destination.mkdir(exist_ok=True)
        if (destination/"metrics.json").exists():
            previous = json.loads((destination/"metrics.json").read_text(encoding="utf-8"))
            if previous["model_sha256"] != selection["model_sha256"]:
                raise ValueError("Previous evaluation used a different model")
            for cohort, digest in previous["arrays_sha256"].items():
                if sha256(destination/f"{cohort}.npz") != digest:
                    raise ValueError("Previous evaluation arrays changed")
            summaries.append(previous)
            print(f"Verified frozen run: reusing {name}", flush=True)
            continue
        print(f"Final evaluation {name}: reading original Process logs", flush=True)
        folder = BENCHMARK/name
        labels = labels_for_rows(folder)
        cases = pd.read_csv(folder/"cases.csv", encoding="utf-8-sig")
        events = pd.read_csv(folder/"events.csv", encoding="utf-8-sig")
        observed = observed_windows(folder)
        rows = final_rows(folder, labels, cases, events)
        normal = control_grid(observed, rows["normal"])
        # Audit only: normal oracle counts cannot become features.
        audit = pd.read_csv(folder/"window_labels.csv", encoding="utf-8-sig")
        audit = audit.loc[audit.scope.eq("TARGET")]
        if not np.array_equal(normal.execution_arrivals, audit.normal_execution_arrivals):
            raise ValueError("Reconstructed normal controls mismatch")
        arrays = {}
        counts, supported = Counts(), Counts()
        for cohort, native in (("observed", observed), ("normal", normal)):
            print(f"  causal features {cohort}: {len(native):,} decisions", flush=True)
            frame = build_features(native, rows[cohort], history, suite.baseline)
            arrays[cohort] = score_arrays(suite, frame, cases, normal=cohort == "normal")
            a = arrays[cohort]
            counts.update(a["truth"], a["prediction"])
            supported.update(a["truth"], a["prediction"], a["covered"])
            np.savez_compressed(destination/f"{cohort}.npz", **a)
        result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(),
                  "supported_only_metrics": supported.records(),
                  "coverage": float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                  "events": event_results(arrays["observed"], arrays["normal"], events, cases),
                  "model_sha256": selection["model_sha256"],
                  "arrays_sha256": {c: sha256(destination/f"{c}.npz") for c in arrays}}
        write_json(destination/"metrics.json", result)
        summaries.append(result)
        print(f"  done: IF ensemble F1 {result['metrics'][2]['f1']:.2%}, KMeans ensemble F1 {result['metrics'][5]['f1']:.2%}", flush=True)
    pooled, basic = Counts(), Counts()
    for d in summaries:
        pooled.values += Counts.from_records(d["metrics"]).values
        if d["dataset"] != "mixed_hard":
            basic.values += Counts.from_records(d["metrics"]).values
    macro = [{"method": m, **{k: float(np.mean([d["metrics"][i][k] for d in summaries if d["dataset"] != "mixed_hard"]))
                              for k in METRICS}} for i, m in enumerate(METHODS)]
    write_json(OUTPUT/"summary.json", {
        "datasets": summaries, "pooled": pooled.records(), "basic_pooled": basic.records(), "basic_macro": macro,
        "thresholds": suite.threshold_records(), "support": suite.families["if"].support,
        "protocol": {"input": "Process-only target group stream + prior synthetic normal traffic history; no Transaction features",
                     "decision_stride_minutes": 1, "lookbacks_minutes": [1, 5, 15],
                     "training": "Normal source references/replays only; not full 3.7M source rows",
                     "point_adjustment": False, "expert_support_identical": True,
                     "labels": "Original injection truth; no changes to CSVs or labels",
                     "selection": "Fresh held-out-template validation search; separate confirmation without reselection",
                     "limitations": ["Existing final benchmark previously examined, not a new blind external test.",
                                     "Synthetic traffic prior and case generators can favor engineered features.",
                                     "All S does not establish that real source executions are incident-free.",
                                     "Input stop needs a healthy collector, expected traffic and a scheduled empty-window check.",
                                     "Target selectors are predeclared monitored groups, not discovered from labels.",
                                     "Small confirmation sample and shared generator; operational validation required."]},
        "model_sha256": selection["model_sha256"], "finished_utc": datetime.now(timezone.utc).isoformat()})
    # Verify original datasets and frozen serving artifact again after evaluation.
    verify_benchmark(BENCHMARK, SOURCE_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "tune", "evaluate", "all"), default="all")
    args = parser.parse_args()
    if args.stage in ("prepare", "all"):
        preparation()
    if args.stage in ("tune", "all"):
        tune()
    if args.stage in ("evaluate", "all"):
        evaluate()
