"""Final refinement: correct temporal units and retain strong old channels."""
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
from model_research_v16.models import CaseAwareCalibration
from model_research_v17.models import CountTemporalFamily
from model_research_v17.features import augment

OUTPUT = ROOT/"reports/model_research_v17"


def development():
    original = prepare(); data = dict(original)
    data["partitions"] = {n: augment(f) for n, f in original["partitions"].items()}
    data["normal_reference"] = augment(original["normal_reference"])
    data["search"] = {n: {c: augment(f) for c, f in v.items()} for n, v in original["search"].items()}
    return data


def tune():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v17 frozen")
    data = development(); old = baselines(); models, choices, rows = {}, {}, []
    for algorithm in ("if", "kmeans", "lof"):
        base = old[algorithm]; baseline_stats = summarize(base, cache(base, data["search"]))
        best_model, best_row = copy.deepcopy(base), {"algorithm": algorithm, "candidate": "baseline", **baseline_stats}
        best_key = np.mean([r["f1"] for r in baseline_stats["pooled"]])
        settings = Settings(algorithm, samples=2048 if algorithm == "if" else 512,
                            tree_count=384, k=6, neighbors=30)
        family = CountTemporalFamily(copy.deepcopy(base), settings).fit(data["partitions"], data["source_support"])
        for name, candidate in (("baseline", base), ("count_temporal", family)):
            for normal_q in ((.99, .995) if name != "baseline" else (.99,)):
                if name != "baseline": candidate.set_operating_point(normal_q, .5, True, .5, "max")
                for mode in ("phase_eligibility", "phase"):
                    model = CaseAwareCalibration(candidate, mode, minimum=80)
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
                                if acceptable(stats, baseline_stats, route):
                                    r = stats["pooled"][route]
                                    key = (r["f1"], stats["macro"][route]["f1"], -r["normal_false_positive_rate"])
                                    if route_best is None or key > route_best[0]: route_best = (key, q, alpha)
                        if route_best: qs[route], alphas[route] = route_best[1:]
                    model.set_point(qs, alphas); stats = summarize(model, cached)
                    eligible = all(acceptable(stats, baseline_stats, r) for r in range(3))
                    key = np.mean([r["f1"] for r in stats["pooled"]])
                    row = {"algorithm": algorithm, "candidate": name, "settings": asdict(settings) if name != "baseline" else None,
                        "mode": mode, "quantiles": qs, "shrinkage": alphas, "temporal_normal_quantile": normal_q,
                        "relative_constraints_met": eligible, **stats}
                    rows.append(row)
                    if eligible and key > best_key: best_key, best_row, best_model = key, row, copy.deepcopy(model)
                    print(f"v17 {algorithm}/{name}/q{normal_q}/{mode}: F1={key:.2%}, constraints={eligible}, alpha={alphas}", flush=True)
        models[algorithm], choices[algorithm] = best_model, best_row
    joblib.dump(models, OUTPUT/"families.joblib", compress=3)
    write(OUTPUT/"selection.json", {"model_sha256": sha256(OUTPUT/"families.joblib"),
        "development_sha256": read(ROOT/"reports/model_research_v15/preparation.json")["data_sha256"],
        "normal_calibration_and_all_search_cases_disjoint": True, "normal_only_raw_model_fit": True,
        "earlier_confirmation_diagnostic_used_for_architecture": True, "fresh_confirmation_used_for_selection": False,
        "final_labels_used_for_selection": False, "benchmark_difficulty_used_for_selection": False,
        "families": choices, "feature_semantics": "Observed count-conditioned residual temporal change; no clean-prefix assumption",
        "code_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
        "limitation": "Adaptive research: prior diagnostics/benchmarks inspected. Fresh confirmation remains untouched until choices frozen."})
    write(OUTPUT/"search_results.json", rows)
    print("v17 frozen before fresh confirmation and final evaluation", flush=True)


if __name__ == "__main__": tune()
