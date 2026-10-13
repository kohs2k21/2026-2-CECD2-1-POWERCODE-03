"""Disjoint sources, rich normal replay, fresh validation, no benchmark edit."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from evaluation_benchmark.generate import Case
from model_evaluation.data import source_references, history_lookup, sha256, verify_benchmark
from model_evaluation.windowing import window_reference_split, PriorHistory
from model_evaluation.tune_windows import selected_references, write_json
from model_improvement.data import BENCHMARK, SOURCE_CACHE, SEARCH, rich_rows, validation_inputs
from model_improvement.features import build_features
from model_improvement.refine import load as load_v4, with_legacy
from model_improvement.run import trusted_load
from model_research_v5.run_matched import CentralNormalCatalog, SEED as NORMAL_SEED, load as load_v7
from model_research_v5.features import context_features
from model_research_v8.features import ConditionalTails, add_duration_features

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT/"reports/model_research_v8"
SEED = 10_170_042
CODE_FILES = ("features.py", "models.py", "data.py", "run.py")


def fingerprints():
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha256(p) for p in
            (BENCHMARK/"manifest.json", SOURCE_CACHE, ROOT/"reports/model_research_v7/selection.json")}


def regenerate_normals(bundle, state):
    original, _, _, _ = source_references(bundle)
    refs, sets, support, _ = window_reference_split(original, set(state["reserved_template_ids"]))
    history = history_lookup(BENCHMARK)
    completion = {}
    for si, name in enumerate(("train", "calibration", "threshold")):
        catalog = CentralNormalCatalog([t for t in bundle["templates"] if t["template_id"] in sets[name]])
        raw = []
        for index in range(len(catalog.adapter_names)*24):
            regime = (index//len(catalog.adapter_names)) % 4
            case = Case(catalog, "individual_delay", index, NORMAL_SEED+100_000+si*10_000, history)
            if regime == 0: case.clone(case.key, 2); case.background((0, 4))
            elif regime == 1:
                case.clone(case.key, 2.2)
                other = [k for k in catalog.groups if k[0] == case.key[0] and k[2] != case.key[2]]
                case.clone(other[int(case.rng.integers(len(other)))] if other else case.key, 2.6)
                case.background((0, 4))
            else: case.baseline(36 if regime == 2 else 60)
            assert not case.events and not case.labels
            raw.append(rich_rows(case, case.observed))
        completion[name] = pd.concat(raw, ignore_index=True)
    return refs, completion, support


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"preparation.json").exists(): return load()
    _, bundle = verify_benchmark(BENCHMARK, SOURCE_CACHE)
    old, v7 = load_v4(), load_v7()
    _, _, state, _ = selected_references(SEARCH, BENCHMARK, SOURCE_CACHE)
    refs, rich, support = regenerate_normals(bundle, state)
    reference = ConditionalTails.fit(refs["train"], minimum=20)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    parts = {}
    for name, source in v7["partitions"].items():
        check = with_legacy(build_features(source, rich[name], history, old["baseline"]), old["legacy_baselines"], history)
        for c in ("residual_max_1m", "log_p95_duration_5m", "log_mean_count_5m"):
            np.testing.assert_allclose(source[c], check[c], rtol=1e-7, atol=1e-7, equal_nan=True)
        parts[name] = add_duration_features(source, rich[name], reference)
    native = validation_inputs(bundle, state, count=360, seed=SEED)
    bank, legacy = {}, {}
    forbidden = set().union(*(set(d["observed"].case_id) for d in v7["bank"].values()))
    for name, d in native.items():
        bank[name], legacy[name] = {}, {}
        for cohort in ("observed", "normal"):
            f = context_features(with_legacy(build_features(d[cohort], d[cohort+"_rows"], history, old["baseline"]), old["legacy_baselines"], history))
            if set(f.case_id) & forbidden: raise ValueError("Fresh validation overlap")
            legacy[name][cohort] = f
            bank[name][cohort] = add_duration_features(f, d[cohort+"_rows"], reference)
    payload = {"partitions": parts, "source_support": support, "bank": bank, "legacy_bank": legacy,
               "reference": reference, "baseline": old["baseline"], "legacy_baselines": old["legacy_baselines"]}
    import joblib
    joblib.dump(payload, OUTPUT/"development_inputs.joblib", compress=3)
    write_json(OUTPUT/"preparation.json", {"inputs": fingerprints(), "data_sha256": sha256(OUTPUT/"development_inputs.joblib"),
        "code_sha256": {n: sha256(Path(__file__).parent/n) for n in CODE_FILES},
        "normal_reference_windows": {n: len(p) for n,p in parts.items()}, "normal_source_rows": {n:len(p) for n,p in refs.items()},
        "normal_source_templates": {n:int(p.source_template_id.nunique()) for n,p in refs.items()},
        "fresh_validation_seed": SEED, "search_cases": 1080, "confirmation_cases": 1080,
        "conditional_reference_fit_only_on_normal_train": True, "normal_replay_identical_to_v7": True,
        "original_benchmark_not_blind": True})
    return payload


def load():
    r = json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"))
    if fingerprints() != r["inputs"]: raise ValueError("Changed v8 inputs")
    for n, digest in r["code_sha256"].items():
        if sha256(Path(__file__).parent/n) != digest: raise ValueError(f"Changed frozen v8 code {n}")
    return trusted_load(OUTPUT/"development_inputs.joblib", r["data_sha256"])
