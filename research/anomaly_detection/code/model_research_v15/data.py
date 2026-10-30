"""Fresh case-disjoint development; original source partitions preserved."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from evaluation_benchmark.generate import Case
from model_evaluation.data import ReplayCatalog, source_references, history_lookup, sha256
from model_evaluation.windowing import window_reference_split
from model_improvement.data import validation_inputs, rich_rows
from model_research_v13.data import prepare as previous, trusted_cache, baseline_monitors, read, write
from model_research_v13.features import matched_precision
from model_research_v15.features import augment

OUTPUT = ROOT/"reports/model_research_v15"
SEED = 16270042


def split_cases(bank):
    calibration, search, confirmation = [], {}, {}
    rng = np.random.default_rng(SEED+1)
    for scenario, cohorts in bank.items():
        normal = cohorts["normal"]
        ids = np.asarray(sorted(normal.loc[normal.validation_split.eq("search"), "case_id"].unique()))
        cal_ids = set(rng.permutation(ids)[:len(ids)//2])
        search[scenario], confirmation[scenario] = {}, {}
        for cohort, frame in cohorts.items():
            candidate = frame.loc[frame.validation_split.eq("search")]
            if cohort == "normal": calibration.append(candidate.loc[candidate.case_id.isin(cal_ids)])
            search[scenario][cohort] = candidate.loc[~candidate.case_id.isin(cal_ids)].copy()
            confirmation[scenario][cohort] = frame.loc[frame.validation_split.eq("confirmation")].copy()
        search_ids = set().union(*(set(f.case_id) for f in search[scenario].values()))
        confirmed_ids = set().union(*(set(f.case_id) for f in confirmation[scenario].values()))
        assert not cal_ids & (search_ids | confirmed_ids)
        assert not search_ids & confirmed_ids
    return pd.concat(calibration, ignore_index=True), search, confirmation


def prepare():
    OUTPUT.mkdir(exist_ok=True); path = OUTPUT/"development_inputs.joblib"
    if path.exists(): return trusted_cache(path, read(OUTPUT/"preparation.json")["data_sha256"])
    old = previous(); source = read(ROOT/"reports/evaluation_v2_work/inputs.json")
    record = read(ROOT/"reports/window_search_v2/selection.json")
    state = trusted_cache(ROOT/"reports/window_search_v2/training_inputs.joblib", record["training_inputs_sha256"])
    original, _, _, _ = source_references(source)
    _, sets, support, _ = window_reference_split(original, set(state["reserved_template_ids"]))
    processor = baseline_monitors()["if"]
    history = history_lookup(ROOT/"data/evaluation")
    parts, provenance = {}, {}
    for si, name in enumerate(("train", "calibration", "threshold")):
        catalog = ReplayCatalog([t for t in source["templates"] if t["template_id"] in sets[name]])
        streams = 24 if name == "train" else 12
        frames = []
        for i in range(len(catalog.adapter_names)*streams):
            case = Case(catalog, "input_stop", i, SEED+100000+si*10000, history)
            # Original normal samples vary within the SAME Adapter/Process/count.
            # Not a noise injection or a new independent source transaction.
            if i % 2: case.fixed_ref = None
            case.baseline((6, 18, 60)[i % 3])
            native = pd.DataFrame(case.export()["windows.csv"])
            native = native.loc[native.scope.eq("TARGET")].reset_index(drop=True)
            frame = old["anchor"].augment(matched_precision(processor.features(native, rich_rows(case, case.normal))))
            frames.append(augment(frame))
        added = pd.concat(frames, ignore_index=True)
        parts[name] = pd.concat([augment(old["partitions"][name]), added], ignore_index=True)
        provenance[name] = {"windows": len(parts[name]), "added_streams": int(added.case_id.nunique()),
                            "source_templates": len(sets[name]), "original_source_partition": True}
        print(f"v15 normal {name}: {len(parts[name]):,} windows", flush=True)
    native = validation_inputs(source, state, count=360, seed=SEED)
    forbidden = set().union(*(set(v[c].case_id) for v in old["bank"].values() for c in ("observed", "normal")))
    bank = {}
    for scenario, inputs in native.items():
        bank[scenario] = {}
        for cohort in ("observed", "normal"):
            if forbidden & set(inputs[cohort].case_id): raise ValueError("Previous validation case collision")
            f = old["anchor"].augment(matched_precision(processor.features(inputs[cohort], inputs[cohort+"_rows"])))
            bank[scenario][cohort] = augment(f)
        print(f"v15 fresh features: {scenario}", flush=True)
    calibration, search, confirmation = split_cases(bank)
    result = {**{k: old[k] for k in ("anchor", "reference", "baseline", "legacy_baselines", "history")},
              "partitions": parts, "source_support": support, "normal_reference": calibration,
              "search": search, "confirmation": confirmation}
    joblib.dump(result, path, compress=3)
    write(OUTPUT/"preparation.json", {"data_sha256": sha256(path), "validation_seed": SEED,
          "cases_per_scenario": 360, "calibration_cases_per_scenario": 90, "search_cases_per_scenario": 90,
          "confirmation_cases_per_scenario": 180, "calibration_observed_and_normal_search_cases_disjoint": True,
          "model_fit_original_source_partition_holdout": True, "final_controls_used_in_fit": False,
          "normal_replay": provenance, "original_benchmark_already_inspected": True,
          "benchmark_before_change_sha256": sha256(ROOT/"data/evaluation/manifest.json"),
          "limitation": "Fresh cases, same generator/source pool; adaptive synthetic research, not external blind operating testing"})
    return result


if __name__ == "__main__": prepare()
