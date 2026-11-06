"""v14: normal-only recalibration, preserve baseline when a candidate loses."""
import argparse
import copy
from dataclasses import asdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.data import sha256
from model_research_v13.data import prepare, baseline_monitors, read, write
from model_research_v13.models import Settings, DensityFamily, PersistentFamily, PreservedFamily
from model_research_v13.run import summarize
from model_research_v14.models import CalibratedFamily
from model_research_v14.data import expanded_partitions
from model_research_v8.models import ChannelFamily, ResearchSettings

OUTPUT = ROOT / "reports/model_research_v14"
QUANTILES = (.90, .925, .95, .975, .99, .995, .999, .9995)


def partition_normals(bank):
    references, search_bank = [], {}
    rng = np.random.default_rng(1617043)
    for scenario, cohorts in bank.items():
        search_bank[scenario] = {}
        for cohort, frame in cohorts.items():
            frame = frame.loc[frame.validation_split.eq("search")].copy()
            if cohort == "normal":
                ids = np.array(sorted(frame.case_id.unique()))
                calibration_ids = set(rng.permutation(ids)[:len(ids)//2])
                references.append(frame.loc[frame.case_id.isin(calibration_ids)])
                frame = frame.loc[~frame.case_id.isin(calibration_ids)]
            search_bank[scenario][cohort] = frame
    return pd.concat(references, ignore_index=True), search_bank


def cache(family, bank, split=None):
    result = {}
    for scenario, cohorts in bank.items():
        result[scenario] = []
        for cohort in ("observed", "normal"):
            frame = cohorts[cohort]
            if split: frame = frame.loc[frame.validation_split.eq(split)]
            raw = family.components(frame)[0]
            truth = frame.window_is_anomaly.to_numpy(bool) if cohort == "observed" else np.zeros(len(frame), bool)
            result[scenario].append((raw, frame.adapterType.to_numpy(), frame.phase.to_numpy(), truth, frame.case_id.to_numpy()))
    return result


def candidate_models(algorithm, data, baseline, diverse):
    if algorithm != "lof":
        yield "preserved_v12", PreservedFamily(copy.deepcopy(baseline[algorithm].family), algorithm), None
        settings = Settings(algorithm, view="drift", samples=256, k=6, shape_distance=False)
        family = PersistentFamily(copy.deepcopy(baseline[algorithm].family), settings)
        family.fit(data["partitions"], data["source_support"])
        family.set_operating_point(.975, .5, True, .5, "max")
        yield "persistent", family, asdict(settings)
        choices = ((512, 1, 6, "radius"), (1024, 4, 6, "radius")) if algorithm == "if" else ((512, 1, 6, "radius"), (512, 1, 12, "diagonal"))
        for samples, rotations, k, covariance in choices:
            settings = ResearchSettings(algorithm, samples=samples, rotations=rotations, k=k, covariance=covariance)
            family = ChannelFamily((), settings).fit(diverse["partitions"], diverse["source_support"])
            family.set_operating_point(.99, .5, True, 1., "mean")
            yield f"diverse_s{samples}_r{rotations}_k{k}_{covariance}", family, asdict(settings)
    else:
        for view, neighbors in (("tails", 30), ("tails", 75), ("anchored", 75)):
            settings = Settings("lof", view=view, neighbors=neighbors, reference_cap=2048)
            family = DensityFamily((), settings).fit(data["partitions"], data["source_support"])
            family.set_operating_point(.975, .5, True, 1., "mean")
            yield f"{view}_k{neighbors}", family, asdict(settings)
        settings = Settings("lof", view="tails", neighbors=50, reference_cap=4096)
        family = DensityFamily((), settings).fit(diverse["partitions"], diverse["source_support"])
        family.set_operating_point(.99, .5, True, 1., "mean")
        yield "diverse_tails_k50", family, asdict(settings)


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT / "selection.json").exists(): raise FileExistsError("v14 selection already frozen")
    data = prepare()
    diverse = expanded_partitions()
    reference, bank = partition_normals(data["bank"])
    baseline = baseline_monitors()
    selected, results, choices, validation = {}, [], {}, {}
    for algorithm in ("if", "kmeans", "lof"):
        best_model = best_row = None
        best_key = None
        baseline_stats = None
        if algorithm != "lof":
            preserved = PreservedFamily(copy.deepcopy(baseline[algorithm].family), algorithm)
            baseline_stats = summarize(preserved, cache(preserved, bank))
            best_model = preserved
            best_row = {"algorithm": algorithm, "candidate": "preserved_v12", "recalibrated": False, **baseline_stats}
            best_key = (baseline_stats["objective"], -baseline_stats["worst_pooled_fpr"])
            results.append(best_row)
        budget = [max(.025, r["normal_false_positive_rate"]) for r in baseline_stats["pooled"]] if baseline_stats else [.025]*3
        rejected_lof = None
        for name, family, settings in candidate_models(algorithm, data, baseline, diverse):
            start = time.perf_counter()
            for mode in ("phase", "phase_eligibility"):
                calibrated = CalibratedFamily(family, mode)
                raw = calibrated.components(reference)[0]
                calibrated.fit_reference(raw, reference.adapterType.to_numpy(), reference.phase.to_numpy())
                cached = cache(calibrated, bank)
                chosen_q = [0.9995]*3
                # Each route has its own normal quantile. Global chosen first,
                # since all unsupported Adapter routes fall back to that cut.
                for route in range(3):
                    route_best = None
                    for q in QUANTILES:
                        trial = chosen_q.copy(); trial[route] = q
                        calibrated.set_quantiles(trial)
                        stats = summarize(calibrated, cached)
                        row = {"algorithm": algorithm, "candidate": name, "settings": settings,
                               "recalibrated": True, "mode": mode, "quantiles": trial, "route_search": route, **stats}
                        results.append(row)
                        record = stats["pooled"][route]
                        worst = max(s["metrics"][route]["normal_false_positive_rate"] for s in stats["scenarios"])
                        if record["normal_false_positive_rate"] <= budget[route] and worst <= .08:
                            key = (stats["macro"][route]["f1"], -record["normal_false_positive_rate"])
                            if route_best is None or key > route_best[0]: route_best = (key, q)
                    if route_best: chosen_q[route] = route_best[1]
                calibrated.set_quantiles(chosen_q)
                stats = summarize(calibrated, cached)
                within = all(r["normal_false_positive_rate"] <= limit for r, limit in zip(stats["pooled"], budget)) and stats["worst_scenario_fpr"] <= .08
                row = {"algorithm": algorithm, "candidate": name, "settings": settings,
                       "recalibrated": True, "mode": mode, "quantiles": chosen_q,
                       "within_budget": within, **stats}
                results.append(row)
                key = (stats["objective"], -stats["worst_pooled_fpr"])
                # Never sacrifice baseline F1 merely to satisfy a new budget.
                if within and (best_key is None or key > best_key):
                    best_key, best_row, best_model = key, row, copy.deepcopy(calibrated)
                if algorithm == "lof" and (rejected_lof is None or key > rejected_lof[0]):
                    rejected_lof = (key, row, copy.deepcopy(calibrated))
                print(f"v14 {algorithm}/{name}/{mode}: macroF1={stats['objective']:.2%}, FPR={stats['worst_pooled_fpr']:.2%}, q={chosen_q}, eligible={within}", flush=True)
            print(f"  calibration + search {time.perf_counter()-start:.1f}s", flush=True)
        if best_model is None:
            # LOF must still be evaluated, but a budget-failing research
            # comparator is explicitly not a recommended production promotion.
            best_key, best_row, best_model = rejected_lof
            best_row["production_promotion_recommended"] = False
            best_row["warning"] = "No candidate met development false-alarm budget; comparison only"
        selected[algorithm], choices[algorithm] = best_model, best_row
        # Inspect confirmation only AFTER all choices are frozen below.
        print(f"selected v14 {algorithm}: {best_row['candidate']}, macroF1={best_row['objective']:.2%}", flush=True)
    joblib.dump(selected, OUTPUT / "families.joblib", compress=3)
    write(OUTPUT / "search_results.json", results)
    write(OUTPUT / "selection.json", {"model_sha256": sha256(OUTPUT / "families.joblib"),
          "development_sha256": read(ROOT / "reports/model_research_v13/preparation.json")["data_sha256"],
          "code_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
          "normal_calibration_cases": int(reference.case_id.nunique()),
          "normal_calibration_disjoint_search_cases": False,
          "normal_calibration_disjoint_normal_search_cases": True,
          "paired_observed_search_case_overlap": True, "normal_only_model_fit": True,
          "confirmation_used_for_selection": False, "final_used_for_selection": False,
          "benchmark_difficulty_changed_for_selection": False,
          "objective": "Equal-scenario macro F1; baseline-preserving selection; per-route development FPR budget and <=8% per scenario.",
          "families": choices,
          "limitation": "Adaptive research; normal calibration shares paired observed search case IDs. Confirmation/final cases unused for calibration; not external blind evaluation."})
    for algorithm, family in selected.items():
        validation[algorithm] = {"search": choices[algorithm],
                                 "confirmation": summarize(family, cache(family, data["bank"], "confirmation"))}
    write(OUTPUT / "validation_results.json", validation)


if __name__ == "__main__":
    tune()
