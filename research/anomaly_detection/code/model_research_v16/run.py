"""Final pre-specified comparison after diagnosing v15 confirmation instability."""
import copy
from dataclasses import asdict
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import read, write
from model_research_v13.run import summarize
from model_research_v14.run import cache
from model_research_v15.data import prepare
from model_research_v15.run import baselines, acceptable, QUANTILES, SHRINKAGE
from model_research_v15.models import Settings
from model_research_v16.models import SharedTemporalFamily, CaseAwareCalibration

OUTPUT = ROOT/"reports/model_research_v16"


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v16 frozen")
    data = prepare(); baseline = baselines(); selected, choices, rows = {}, {}, []
    for algorithm in ("if", "kmeans", "lof"):
        original = baseline[algorithm]
        reference = summarize(original, cache(original, data["search"]))
        best_model, best_row = copy.deepcopy(original), {"algorithm": algorithm, "candidate": "baseline", **reference}
        best_key = np.mean([r["f1"] for r in reference["pooled"]])
        settings = Settings(algorithm, samples=2048 if algorithm == "if" else 512,
                            tree_count=384, k=6, neighbors=30)
        shared = SharedTemporalFamily(copy.deepcopy(original), settings).fit(data["partitions"], data["source_support"])
        for name, family in (("baseline", original), ("shared_temporal_stable", shared)):
            for mode in ("phase_eligibility", "phase"):
                model = CaseAwareCalibration(family, mode, minimum=80)
                cal = data["normal_reference"]
                model.fit_reference(model.components(cal)[0], cal.adapterType.to_numpy(), cal.phase.to_numpy(), cal.case_id.to_numpy())
                cached = cache(model, data["search"])
                qs, alphas = [.99]*3, [0.]*3
                for route in range(3):
                    route_best = None
                    for q in QUANTILES:
                        for alpha in SHRINKAGE:
                            qq, aa = qs.copy(), alphas.copy(); qq[route], aa[route] = q, alpha
                            model.set_point(qq, aa); stats = summarize(model, cached)
                            if acceptable(stats, reference, route):
                                r = stats["pooled"][route]
                                key = (r["f1"], stats["macro"][route]["f1"], -r["normal_false_positive_rate"])
                                if route_best is None or key > route_best[0]: route_best = (key, q, alpha)
                    if route_best: qs[route], alphas[route] = route_best[1:]
                model.set_point(qs, alphas); stats = summarize(model, cached)
                eligible = all(acceptable(stats, reference, r) for r in range(3))
                key = np.mean([r["f1"] for r in stats["pooled"]])
                row = {"algorithm": algorithm, "candidate": name, "settings": asdict(settings) if name != "baseline" else None,
                    "mode": mode, "quantiles": qs, "shrinkage": alphas, "relative_constraints_met": eligible, **stats}
                rows.append(row)
                if eligible and key > best_key: best_key, best_row, best_model = key, row, copy.deepcopy(model)
                print(f"v16 {algorithm}/{name}/{mode}: F1={key:.2%}, constraints={eligible}, alpha={alphas}", flush=True)
        selected[algorithm], choices[algorithm] = best_model, best_row
    joblib.dump(selected, OUTPUT/"families.joblib", compress=3)
    write(OUTPUT/"selection.json", {"model_sha256": sha256(OUTPUT/"families.joblib"),
          "development_sha256": read(ROOT/"reports/model_research_v15/preparation.json")["data_sha256"],
          "normal_calibration_and_all_search_cases_disjoint": True, "normal_only_raw_model_fit": True,
          "v15_confirmation_diagnostic_used_for_architecture": True,
          "fresh_v16_confirmation_used_for_selection": False, "final_labels_used_for_selection": False,
          "benchmark_difficulty_used_for_selection": False, "families": choices,
          "case_shrinkage": "effective correction multiplier = selected alpha * min(independent normal cases / 30, 1)",
          "numeric_resolution": "temporal normalized inputs rounded to .001 before fit AND score",
          "code_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
          "limitation": "Adaptive architecture diagnosis; final synthetic benchmark has been inspected previously, not external blind validation"})
    write(OUTPUT/"search_results.json", rows)
    print("v16 choices frozen before new confirmation or final evaluation", flush=True)


if __name__ == "__main__": tune()
