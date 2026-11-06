"""Never reuse v15/v16 diagnostic cases as fresh confirmation."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
from model_evaluation.data import sha256
from model_improvement.data import validation_inputs
from model_research_v13.data import trusted_cache, baseline_monitors, read, write
from model_research_v13.features import matched_precision
from model_research_v13.run import summarize
from model_research_v14.run import cache
from model_research_v15.data import prepare
from model_research_v15.run import baselines
from model_research_v17.features import augment
from model_research_v17.run import OUTPUT


def confirm():
    selection = read(OUTPUT/"selection.json")
    if sha256(OUTPUT/"families.joblib") != selection["model_sha256"]: raise ValueError("Frozen model changed")
    if (OUTPUT/"validation_results.json").exists(): raise FileExistsError("Confirmation already recorded")
    data = prepare(); bundle = read(ROOT/"reports/evaluation_v2_work/inputs.json")
    record = read(ROOT/"reports/window_search_v2/selection.json")
    state = trusted_cache(ROOT/"reports/window_search_v2/training_inputs.joblib", record["training_inputs_sha256"])
    native = validation_inputs(bundle, state, count=240, seed=19270042)
    processor = baseline_monitors()["if"]; bank = {}
    forbidden = set(data["normal_reference"].case_id).union(*(set(f.case_id) for b in (data["search"], data["confirmation"]) for v in b.values() for f in v.values()))
    for scenario, inputs in native.items():
        bank[scenario] = {}
        for cohort in ("observed", "normal"):
            if forbidden & set(inputs[cohort].case_id): raise ValueError("Case leakage into fresh confirmation")
            frame = data["anchor"].augment(matched_precision(processor.features(inputs[cohort], inputs[cohort+"_rows"])))
            bank[scenario][cohort] = augment(frame)
        print(f"v17 fresh confirmation features: {scenario}", flush=True)
    models = joblib.load(OUTPUT/"families.joblib"); old = baselines()
    results = {a: {"baseline": summarize(old[a], cache(old[a], bank)), "candidate": summarize(models[a], cache(models[a], bank))} for a in models}
    write(OUTPUT/"validation_results.json", {"seed": 19270042, "cases_per_scenario": 240,
        "case_ids_disjoint_all_development_and_prior_confirmation": True, "models_reselected": False, "results": results})
    joblib.dump(bank, OUTPUT/"confirmation_inputs.joblib", compress=3)
    print("Fresh confirmation recorded without reselection", flush=True)


if __name__ == "__main__": confirm()
