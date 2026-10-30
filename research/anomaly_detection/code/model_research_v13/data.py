"""Verified historical normal cache + fresh reserved-template validation.

The old v8 manifest is intentionally NOT rewritten to pretend that its normal
cache was fit on a later benchmark. Its exact saved hash is checked and the
provenance mismatch is recorded; no final controls enter training/calibration.
"""
import json
from pathlib import Path
import sys
import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from model_evaluation.data import sha256, source_references
from model_evaluation.windowing import window_reference_split
from model_improvement.data import validation_inputs
from model_research_v8.inference import load
from model_research_v13.features import FixedNormalAnchor, matched_precision

OUTPUT = ROOT / "reports/model_research_v13"
SEED = 16_170_042


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def trusted_cache(path, expected):
    if sha256(path) != expected:
        raise ValueError(f"Cache hash mismatch: {path.name}")
    return joblib.load(path)


def baseline_monitors():
    folder = ROOT / "reports/model_research_v12/handoff"
    manifest = read(folder / "manifest.json")
    return {r["algorithm"]: load(folder / r["file"], r["sha256"]) for r in manifest["models"]}


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    output = OUTPUT / "development_inputs.joblib"
    if output.exists():
        provenance = read(OUTPUT / "preparation.json")
        return trusted_cache(output, provenance["data_sha256"])
    old = ROOT / "reports/model_research_v8"
    previous = read(old / "preparation.json")
    source_path = ROOT / "reports/evaluation_v2_work/inputs.json"
    if sha256(source_path) != previous["inputs"]["reports/evaluation_v2_work/inputs.json"]:
        raise ValueError("Original normal archive changed")
    data = trusted_cache(old / "development_inputs.joblib", previous["data_sha256"])
    search = ROOT / "reports/window_search_v2"
    record = read(search / "selection.json")
    state = trusted_cache(search / "training_inputs.joblib", record["training_inputs_sha256"])
    train = matched_precision(data["partitions"]["train"])
    anchor = FixedNormalAnchor.fit(train)
    parts = {name: anchor.augment(matched_precision(frame)) for name, frame in data["partitions"].items()}
    bundle = read(source_path)
    original, _, _, _ = source_references(bundle)
    refs, sets, support, _ = window_reference_split(original, set(state["reserved_template_ids"]))
    assert not set.union(*sets.values()).intersection(state["reserved_template_ids"])
    native = validation_inputs(bundle, state, count=180, seed=SEED)
    monitors = baseline_monitors()
    bank = {}
    forbidden = set().union(*(set(d["observed"].case_id) for d in data["bank"].values()))
    for scenario, inputs in native.items():
        bank[scenario] = {}
        for cohort in ("observed", "normal"):
            source = inputs[cohort]
            if set(source.case_id).intersection(forbidden):
                raise ValueError("Fresh v13 validation overlaps prior v8 cases")
            frame = monitors["if"].features(source, inputs[cohort + "_rows"])
            bank[scenario][cohort] = anchor.augment(matched_precision(frame))
            print(f"v13 validation features: {scenario}/{cohort}, {len(frame):,} decisions", flush=True)
    result = {"partitions": parts, "bank": bank, "anchor": anchor, "source_support": support,
              "reference": data["reference"], "baseline": data["baseline"],
              "legacy_baselines": data["legacy_baselines"], "history": monitors["if"].history}
    joblib.dump(result, output, compress=3)
    write(OUTPUT / "preparation.json", {
        "data_sha256": sha256(output), "normal_cache_sha256": previous["data_sha256"],
        "normal_cache_original_manifest_sha256": previous["inputs"]["data/evaluation/manifest.json"],
        "current_benchmark_manifest_sha256": sha256(ROOT / "data/evaluation/manifest.json"),
        "source_archive_sha256": sha256(source_path), "validation_seed": SEED,
        "fresh_cases_per_scenario": 180, "search_cases_per_scenario": 90,
        "confirmation_cases_per_scenario": 90, "normal_windows": {n: len(p) for n, p in parts.items()},
        "train_only_anchor": True, "final_controls_used_in_fit": False,
        "source_template_holdout_preserved": True,
        "original_final_benchmark_already_inspected": True,
        "limitation": "Adaptive research; fresh cases share generator/held-out source pool. No blind production generalization claim."})
    return result
