"""Select fusion on case-disjoint development, then freeze before new confirm."""
import copy
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
from model_research_v15.run import baselines
from model_research_v18.models import RoutedFusion

OUTPUT = ROOT/"reports/model_research_v18"


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v18 frozen")
    data = prepare()
    # Both are frozen independent-development candidates, not selected on final.
    global_families = joblib.load(ROOT/"reports/model_research_v15/families.joblib")
    local_families = joblib.load(ROOT/"reports/model_research_v17/families.joblib")
    baseline = baselines(); selected, choices, results = {}, {}, []
    for algorithm in ("if", "kmeans", "lof"):
        model = RoutedFusion(copy.deepcopy(global_families[algorithm]), copy.deepcopy(local_families[algorithm]))
        model.fit_reference(data["normal_reference"])
        cached = cache(model, data["search"])
        reference = summarize(baseline[algorithm], cache(baseline[algorithm], data["search"]))
        best_key = best_row = best_model = None
        fallback_key = fallback_row = fallback_model = None
        for mode in ("mean", "max"):
            for weight in (.25, .5, .75, .9):
                for q in (.95, .975, .99, .995):
                    for alpha in (0., .5, 1.):
                        model.set_point(weight, mode, q, alpha)
                        stats = summarize(model, cached)
                        candidate, old = stats["pooled"][2], reference["pooled"][2]
                        eligible = (candidate["normal_false_positive_rate"] <= old["normal_false_positive_rate"]+.005
                                    and candidate["precision"] >= old["precision"]-.01
                                    and candidate["recall"] >= old["recall"]-.005
                                    and candidate["accuracy"] >= old["accuracy"]-.001)
                        row = {"algorithm": algorithm, "combination": mode, "global_weight": weight,
                               "quantile": q, "alpha": alpha, "ensemble_relative_constraints_met": eligible, **stats}
                        results.append(row)
                        key = (candidate["f1"], -candidate["normal_false_positive_rate"])
                        if eligible and (best_key is None or key > best_key): best_key, best_row, best_model = key, row, copy.deepcopy(model)
                        # A failed-budget research comparator is not hidden.
                        fallback = (candidate["precision"], candidate["f1"])
                        if fallback_key is None or fallback > fallback_key: fallback_key, fallback_row, fallback_model = fallback, row, copy.deepcopy(model)
        if best_model is None: best_row, best_model = fallback_row, fallback_model
        selected[algorithm], choices[algorithm] = best_model, best_row
        print(f"v18 {algorithm}: ensemble F1={best_row['pooled'][2]['f1']:.2%}, precision={best_row['pooled'][2]['precision']:.2%}, constraints={best_row['ensemble_relative_constraints_met']}", flush=True)
    joblib.dump(selected, OUTPUT/"families.joblib", compress=3)
    write(OUTPUT/"selection.json", {"model_sha256": sha256(OUTPUT/"families.joblib"),
          "normal_calibration_and_search_cases_disjoint": True, "normal_only_model_fit": True,
          "fresh_confirmation_used_for_selection": False, "final_labels_used_for_selection": False,
          "previous_diagnostics_used_for_architecture": True, "global_source_version": "v15",
          "adapter_source_version": "v17", "families": choices,
          "actual_ensemble": "Selected global output + selected Adapter output, not a three-algorithm OR",
          "limitation": "Adaptive research with previously inspected synthetic benchmark, not blind operating validation"})
    write(OUTPUT/"search_results.json", results)


if __name__ == "__main__": tune()
