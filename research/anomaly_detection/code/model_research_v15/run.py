"""Case-disjoint operating-point search; no final labels/confirmation tuning."""
import copy
from dataclasses import asdict
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import baseline_monitors, read, write
from model_research_v13.models import PreservedFamily
from model_research_v13.run import summarize
from model_research_v14.run import cache
from model_research_v15.data import prepare, OUTPUT
from model_research_v15.models import Settings, TemporalFamily, RegularizedCalibration

QUANTILES = (.925, .95, .975, .99, .995, .999)
SHRINKAGE = (0., .25, .5, 1.)


def baselines():
    original = baseline_monitors()
    previous = joblib.load(ROOT/"reports/model_research_v14/families.joblib")
    return {"if": PreservedFamily(copy.deepcopy(original["if"].family), "if"),
            "kmeans": PreservedFamily(copy.deepcopy(original["kmeans"].family), "kmeans"),
            "lof": copy.deepcopy(previous["lof"])}


def candidates(algorithm, base, data):
    yield "baseline", copy.deepcopy(base), None
    if algorithm == "if": settings = [Settings(algorithm, samples=512), Settings(algorithm, samples=2048, tree_count=384)]
    elif algorithm == "kmeans": settings = [Settings(algorithm, k=4), Settings(algorithm, k=12, whiten=True)]
    else: settings = [Settings(algorithm, neighbors=30), Settings(algorithm, neighbors=75, whiten=True)]
    for setting in settings:
        family = TemporalFamily(copy.deepcopy(base), setting).fit(data["partitions"], data["source_support"])
        family.set_operating_point(.975, .5, True, .5, "max")
        yield f"temporal_{asdict(setting)}", family, asdict(setting)


def acceptable(stats, baseline, route):
    new, old = stats["pooled"][route], baseline["pooled"][route]
    # Search constraints are relative to the proper same-case baseline, not a
    # cherry-picked absolute target. They are not a production false-alarm guarantee.
    return (new["normal_false_positive_rate"] <= old["normal_false_positive_rate"] + .005
            and new["precision"] >= old["precision"] - .01
            and new["recall"] >= old["recall"] - .005
            and new["accuracy"] >= old["accuracy"] - .001
            and stats["macro"][route]["f1"] >= baseline["macro"][route]["f1"] - .02)


def tune():
    data = prepare()
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v15 frozen; preserve results")
    baseline = baselines(); models, chosen, rows = {}, {}, []
    for algorithm in ("if", "kmeans", "lof"):
        reference_stats = summarize(baseline[algorithm], cache(baseline[algorithm], data["search"]))
        best = {"algorithm": algorithm, "candidate": "baseline", "recalibrated": False, **reference_stats}
        best_model = copy.deepcopy(baseline[algorithm])
        best_key = np.mean([r["f1"] for r in reference_stats["pooled"]])
        print(f"v15 {algorithm} independent search baseline F1={best_key:.2%}", flush=True)
        rows.append(best)
        for name, family, settings in candidates(algorithm, baseline[algorithm], data):
            for mode in ("phase_eligibility", "phase"):
                started = time.perf_counter()
                model = RegularizedCalibration(family, mode, minimum=80)
                cal = data["normal_reference"]
                model.fit_reference(model.components(cal)[0], cal.adapterType.to_numpy(), cal.phase.to_numpy())
                cached = cache(model, data["search"])
                selected_q, selected_alpha = [.99]*3, [0.]*3
                for route in range(3):
                    route_best = None
                    for q in QUANTILES:
                        for alpha in SHRINKAGE:
                            qs, shrink = selected_q.copy(), selected_alpha.copy()
                            qs[route], shrink[route] = q, alpha
                            model.set_point(qs, shrink)
                            stats = summarize(model, cached)
                            record = {"algorithm": algorithm, "candidate": name, "settings": settings,
                                "mode": mode, "quantiles": qs, "shrinkage": shrink, "route_search": route, **stats}
                            rows.append(record)
                            if acceptable(stats, reference_stats, route):
                                r = stats["pooled"][route]
                                key = (r["f1"], stats["macro"][route]["f1"], -r["normal_false_positive_rate"])
                                if route_best is None or key > route_best[0]: route_best = (key, q, alpha)
                    if route_best: selected_q[route], selected_alpha[route] = route_best[1:]
                model.set_point(selected_q, selected_alpha)
                stats = summarize(model, cached)
                eligible = all(acceptable(stats, reference_stats, route) for route in range(3))
                key = np.mean([r["f1"] for r in stats["pooled"]])
                record = {"algorithm": algorithm, "candidate": name, "settings": settings, "recalibrated": True,
                    "mode": mode, "quantiles": selected_q, "shrinkage": selected_alpha,
                    "relative_quality_constraints_met": eligible, **stats}
                rows.append(record)
                if eligible and key > best_key:
                    best_key, best, best_model = key, record, copy.deepcopy(model)
                print(f"v15 {algorithm}/{name.split('_')[0]}/{mode}: F1={key:.2%}, eligible={eligible}, alpha={selected_alpha}, {time.perf_counter()-started:.1f}s", flush=True)
        models[algorithm], chosen[algorithm] = best_model, best
        print(f"selected v15 {algorithm}: F1={best_key:.2%}, candidate={best['candidate']}", flush=True)
    joblib.dump(models, OUTPUT/"families.joblib", compress=3)
    write(OUTPUT/"selection.json", {"model_sha256": sha256(OUTPUT/"families.joblib"),
          "development_sha256": read(OUTPUT/"preparation.json")["data_sha256"],
          "normal_calibration_observed_and_normal_search_cases_disjoint": True,
          "normal_only_model_fit": True, "final_used_for_selection": False,
          "confirmation_used_for_selection": False, "benchmark_difficulty_changed_for_selection": False,
          "families": chosen, "selection_objective": "Mean three-route pooled F1 with relative precision/recall/accuracy/FPR constraints and macro-F1 floor",
          "code_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
          "limitation": "Adaptive synthetic research; fresh independent cases do not constitute external operating validation"})
    write(OUTPUT/"search_results.json", rows)
    confirmation = {a: {"baseline": summarize(baseline[a], cache(baseline[a], data["confirmation"])),
                         "candidate": summarize(models[a], cache(models[a], data["confirmation"]))} for a in models}
    write(OUTPUT/"validation_results.json", confirmation)


if __name__ == "__main__": tune()
