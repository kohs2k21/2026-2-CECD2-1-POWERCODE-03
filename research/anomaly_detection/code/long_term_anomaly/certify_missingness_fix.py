"""Pre-label bugfix certificate: no refit, no threshold or benchmark changes.

Two simulated short gaps may touch. Split a long monitoring gap instead of
aborting an otherwise valid batch. Recompute ALL fit/cal/dev residuals and
compare to the already selected model's cache before opening final truth.
"""
import joblib
import numpy as np
from .common import REPORT, DATA, read_json, write_json, sha256
from .experiment import residual_bank
from pathlib import Path


def certify():
    if (REPORT / "test_results.json").exists() or (REPORT / "prediction_seal.json").exists():
        raise RuntimeError("Only permissible before final truth/score seal")
    selection = read_json(REPORT / "selection.json")
    old_source = selection["models_source_sha256"]
    old_model = selection["model_sha256"]
    current = sha256(Path(__file__).with_name("models.py"))
    if old_source == current:
        raise RuntimeError("No new code fix to certify")
    equality = {}
    for base in ("stl_window", "seasonal_naive"):
        for split in ("train", "calibration", "development"):
            signature = sha256(DATA / f"{split}_inputs.npz")[:12] + old_source[:12]
            old_cache = REPORT / "cache" / f"residual_{split}_{base}_{signature}.npz"
            with np.load(old_cache) as f:
                before = f["residual"]
            after, _ = residual_bank(split, base)
            np.testing.assert_array_equal(before, after)
            equality[f"{base}:{split}"] = True
    package = joblib.load(REPORT / "models.joblib")
    for name, model in package["models"].items():
        assert model.record() == selection["models"][name]["record"]
    package["models_source_sha256"] = current
    joblib.dump(package, REPORT / "models.joblib", compress=3)
    selection["models_source_sha256"] = current
    selection["model_sha256"] = sha256(REPORT / "models.joblib")
    selection["pre_evaluation_missingness_fix"] = {
        "reason": "adjacent simulated short collection gaps can form a >6h monitoring gap",
        "policy": "split monitoring statistics/PELT at long collection gaps; gap hours remain unassessable",
        "benchmark_or_labels_changed": False, "test_truth_opened": False,
        "refit_or_threshold_changes": False, "old_model_sha256": old_model,
        "old_models_source_sha256": old_source, "all_fit_calibration_development_residuals_exactly_equal": equality}
    write_json(REPORT / "selection.json", selection)
    print("Pre-evaluation bugfix certified, normal references and all thresholds unchanged", equality, flush=True)


if __name__ == "__main__":
    certify()
