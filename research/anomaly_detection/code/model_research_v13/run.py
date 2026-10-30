"""Train/select on fresh validation, with explicit normal false-alarm budgets."""
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import joblib
import numpy as np
from model_evaluation.metrics import metric_record
from model_evaluation.data import sha256
from model_research_v13.data import OUTPUT, prepare, baseline_monitors, read, write
from model_research_v13.models import Settings, DensityFamily, PersistentFamily, PreservedFamily


def components(family, bank, split):
    result = {}
    for scenario, inputs in bank.items():
        result[scenario] = []
        for cohort in ("observed", "normal"):
            frame = inputs[cohort].loc[inputs[cohort].validation_split.eq(split)]
            raw = family.components(frame)[0]
            y = frame.window_is_anomaly.to_numpy(bool) if cohort == "observed" else np.zeros(len(frame), bool)
            result[scenario].append((raw, frame.adapterType.to_numpy(), frame.phase.to_numpy(), y, frame.case_id.to_numpy()))
    return result


def summarize(family, cached):
    pooled = np.zeros((3, 4), np.int64)
    scenarios = []
    case_fprs = []
    for name, cohorts in cached.items():
        counts = np.zeros((3, 4), np.int64)
        for ci, (raw, adapters, phases, y, ids) in enumerate(cohorts):
            pred = family.from_components(raw, adapters, phases) > 0
            for i, p in enumerate(pred.T):
                counts[i] += [np.sum(~y & ~p), np.sum(~y & p), np.sum(y & ~p), np.sum(y & p)]
            if ci == 1:
                unique, index = np.unique(ids, return_inverse=True)
                case_alert = np.zeros((len(unique), 3), bool)
                np.logical_or.at(case_alert, index, pred)
                case_fprs.append(case_alert.mean(axis=0))
        scenarios.append({"scenario": name, "metrics": [metric_record(*c) for c in counts]})
        pooled += counts
    records = [metric_record(*c) for c in pooled]
    macro = [{k: float(np.mean([s["metrics"][i][k] for s in scenarios]))
              for k in ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")} for i in range(3)]
    return {"pooled": records, "macro": macro, "scenarios": scenarios,
            "objective": float(np.mean([r["f1"] for r in macro])),
            "worst_pooled_fpr": max(r["normal_false_positive_rate"] for r in records),
            "worst_scenario_fpr": max(r["normal_false_positive_rate"] for s in scenarios for r in s["metrics"]),
            "normal_case_false_alarm_rate": np.mean(case_fprs, axis=0).tolist()}


def candidates(algorithm):
    if algorithm == "lof":
        return [Settings("lof", view=view, neighbors=k, deduplicate=dedup)
                for view, k, dedup in (("tails", 30, True), ("anchored", 30, True),
                                       ("anchored", 75, True), ("anchored", 30, False))] + [
                    Settings("lof", neighbors=50, metric="manhattan", deduplicate=True)]
    if algorithm == "if":
        return [Settings("if", view=view, samples=n) for view, n in
                (("drift", 256), ("drift", 1024), ("anchored", 512))]
    return [Settings("kmeans", view=view, k=k, shape_distance=shape)
            for view, k, shape in (("drift", 6, False), ("drift", 12, True), ("anchored", 6, True))]


def tune():
    data = prepare()
    if (OUTPUT / "selection.json").exists():
        raise FileExistsError("v13 selection already frozen; preserve it")
    baseline = baseline_monitors()
    selected, search = {}, []
    validations = {}
    for algorithm in ("if", "kmeans", "lof"):
        best = best_model = fallback = fallback_model = None
        if algorithm != "lof":
            preserved = PreservedFamily(copy.deepcopy(baseline[algorithm].family), algorithm)
            reference = summarize(preserved, components(preserved, data["bank"], "search"))
            within = reference["worst_pooled_fpr"] <= .025 and reference["worst_scenario_fpr"] <= .08
            row = {"algorithm": algorithm, "preserved_v12": True, "settings": None,
                   "within_budget": within, **reference}
            search.append(row)
            fallback, fallback_model = ((-reference["worst_scenario_fpr"], reference["objective"]), row), preserved
            if within:
                best, best_model = ((reference["objective"], -reference["worst_pooled_fpr"]), row), copy.deepcopy(preserved)
            print(f"v12 validation reference {algorithm}: macro F1={reference['objective']:.2%}, budget={within}", flush=True)
        for settings in candidates(algorithm):
            print(f"v13 fit {asdict(settings)}", flush=True)
            started = time.perf_counter()
            family = (DensityFamily((), settings) if algorithm == "lof" else
                      PersistentFamily(copy.deepcopy(baseline[algorithm].family), settings))
            family.fit(data["partitions"], data["source_support"])
            cached = components(family, data["bank"], "search")
            print(f"  fit + search scoring: {time.perf_counter()-started:.1f}s", flush=True)
            for q in (.975, .99, .995):
                for weight in (.25, .5, .75):
                    for beta in (.5, 1.):
                        family.set_operating_point(q, weight, True, beta, "mean" if algorithm == "lof" else "max")
                        result = summarize(family, cached)
                        within = result["worst_pooled_fpr"] <= .025 and result["worst_scenario_fpr"] <= .08
                        record = {"algorithm": algorithm, "settings": asdict(settings), "quantile": q,
                                  "global_weight": weight, "drift_or_latency_weight": beta,
                                  "within_budget": within, **result}
                        search.append(record)
                        key = (result["objective"], -result["worst_pooled_fpr"])
                        fallback_key = (-result["worst_scenario_fpr"], result["objective"])
                        if fallback is None or fallback_key > fallback[0]:
                            fallback, fallback_model = (fallback_key, record), copy.deepcopy(family)
                        if within and (best is None or key > best[0]):
                            best, best_model = (key, record), copy.deepcopy(family)
            print(f"  current best macro F1: {(best or fallback)[1]['objective']:.2%}", flush=True)
        if best is None:
            best, best_model = fallback, fallback_model
        selected[algorithm] = best_model
        validations[algorithm] = {"search": best[1],
                                  "confirmation": summarize(best_model, components(best_model, data["bank"], "confirmation"))}
        write(OUTPUT / f"chosen_{algorithm}.json", best[1])
        print(f"v13 selected {algorithm}: macro F1 {best[1]['objective']:.2%}, budget={best[1]['within_budget']}", flush=True)
    joblib.dump(selected, OUTPUT / "families.joblib", compress=3)
    write(OUTPUT / "search_results.json", search)
    write(OUTPUT / "validation_results.json", validations)
    write(OUTPUT / "selection.json", {
        "model_sha256": sha256(OUTPUT / "families.joblib"),
        "development_sha256": sha256(OUTPUT / "development_inputs.joblib"),
        "code_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
        "normal_only_model_fit": True, "confirmation_used_for_selection": False,
        "final_used_for_selection": False, "benchmark_difficulty_changed_for_selection": False,
        "objective": "Equal-scenario mean F1 over three routes, pooled normal FPR <=2.5%, each scenario FPR <=8%.",
        "families": {a: validations[a]["search"] for a in selected},
        "limitation": "Adaptive research on previously inspected benchmark; fresh reserved-source validation is not external blind testing."})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("prepare", "tune"), default="tune")
    args = parser.parse_args()
    prepare() if args.stage == "prepare" else tune()
