"""Freeze the development-selected native model, then evaluate the untouched test.

Policy fixed before test: use the sparse candidate at 4.5% development-normal
budget (1.5 percentage point margin to the 6% limit). Preserve 4/8/12/24h
conditional-release providers unchanged. No old test or source is rewritten.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import joblib

from ..common import REPORT, read_json, write_json, sha256
from . import run, refine
from .bank import load_bank
from .benchmark import DATA

OUT = REPORT / "five_minute_release"
SOURCE = REPORT / "five_minute_conditional"


def runtime_hash():
    package = Path(__file__).parent
    paths = [package / p for p in ("model.py", "conditional.py", "bank.py", "run.py", "release.py")]
    paths += [package.parent / "raw_windows" / p for p in ("aggregation.py", "service.py")]
    paths += [package.parents[1] / "process_anomaly_pipeline/data.py", package.parent / "inference.py"]
    return hashlib.sha256("".join(sha256(p) for p in paths).encode()).hexdigest()


def configure():
    refine.configure()
    run.OUT = OUT
    run.runtime_hash = runtime_hash


def freeze():
    if OUT.exists():
        raise FileExistsError("Preserve release; do not overwrite after test")
    base = load_bank(SOURCE / "models.joblib")
    study = read_json(SOURCE / "development_sparse_feature_study.json")
    selected = study["results"]["0.045"]
    if not selected["development"]["goal_pass"] or study["test_used"]:
        raise ValueError("Development gate not satisfied before test")
    bank = copy.deepcopy(base)
    bank.settings[1] = {k: selected[k] for k in ("weights", "temperature", "threshold", "normal_tail_budget")}
    bank.provenance = {**bank.provenance, "runtime_sha256": runtime_hash(),
        "development_feature_selection": True, "test_used_for_selection": False,
        "development_normal_budget_one_hour": .045,
        "one_hour_reference_calibration_and_sparse_evidence": True,
        "other_four_lengths_unchanged_from_native_conditional_provider": True,
        "sparse_study_sha256": sha256(SOURCE / "development_sparse_feature_study.json"),
        "sparse_study_source_sha256": sha256(Path(__file__).with_name("feature_study.py"))}
    OUT.mkdir(parents=True)
    joblib.dump(bank, OUT / "models.joblib", compress=3)
    base_selection = read_json(SOURCE / "selected_configuration.json")
    base_selection["horizons"]["1"] = {"selected": selected, "candidate_source": "development_sparse_feature_study.json"}
    base_selection["provenance"] = bank.provenance
    write_json(OUT / "selected_configuration.json", base_selection)
    write_json(OUT / "model_manifest.json", {"models_sha256": sha256(OUT / "models.joblib"),
        "selection_sha256": sha256(OUT / "selected_configuration.json"),
        "native_benchmark_manifest_sha256": sha256(DATA / "manifest.json"), **bank.provenance})
    print("Frozen native release before test", {"development_one_hour": selected["development"]["median"],
        "model_bytes": (OUT / "models.joblib").stat().st_size}, flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("freeze", "evaluate"))
    args = p.parse_args()
    if args.command == "freeze": freeze()
    else:
        configure()
        run.evaluate()
