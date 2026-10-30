"""Require selected-window evidence AND localized hourly evidence.

Long batches may straddle fault boundaries. Their contextual score is useful,
but copying a batch alarm to every hour spreads alerts to adjacent normal
hours. Localization uses only twelve-bin chunks already inside the selected
batch, with the independently development-selected 1h gate. No labels or
neighboring monitoring batches enter inference; no label is expanded.
"""
import argparse
import copy
import hashlib
from pathlib import Path
import joblib
import numpy as np

from ..common import REPORT, read_json, write_json, sha256
from . import run, release
from .bank import FineBank, validated_frame, combine_evidence, load_bank
from .benchmark import load_split, DATA

OUT = REPORT / "five_minute_localized"


class LocalizedBank(FineBank):
    def predict_batch(self, reference, batch, analysis_hours=4):
        result = super().predict_batch(reference, batch, analysis_hours).copy()
        if analysis_hours == 1:
            result["model_type"] = "native_reference_calibrated_hour"
            return result
        x, o, a, times, _ = validated_frame(batch)
        offset = int((times[0].value - reference.end_time.value) // (5 * 60 * 10**9))
        point = self.models[1]
        features = point.sequence_features(reference.latent, x, o, a, 1, offset)
        evidence = point.normal_evidence(features)
        cfg = self.settings[1]
        local_score = combine_evidence(evidence, cfg["weights"], cfg["temperature"])
        context = result.score.to_numpy(float)
        threshold = float(result.threshold.iloc[0])
        # Positive margin iff BOTH independently calibrated gates are passed.
        margin = np.minimum(context - threshold, local_score - cfg["threshold"])
        result["context_score"] = context
        result["context_threshold"] = threshold
        result["local_hour_score"] = local_score
        result["local_hour_threshold"] = cfg["threshold"]
        result["score"] = margin
        result["threshold"] = 0.
        result["is_anomaly"] = np.isfinite(margin) & (margin > 0)
        result["assessable"] = np.isfinite(margin)
        result["model_type"] = "native_window_context_with_hour_localization"
        result["score_scope"] = "hour_localized_inside_selected_batch"
        result["reason"] = np.where(result.assessable,
            "context:" + result.reason.astype(str) + ";hour_normal_tail_gate", "collection_missing")
        return result

    def record(self):
        return {**super().record(), "long_window_hourly_localization": True,
            "hourly_alarm_requires_context_and_local_hour_evidence": True,
            "window_score_repeated_over_original_hourly_evaluation_positions": False,
            "local_gate_tuned_on_test": False,
            "architecture_revision_after_first_native_test_review": True,
            "original_native_test_is_no_longer_blind": True}


def runtime_hash():
    return hashlib.sha256((release.runtime_hash() + sha256(__file__)).encode()).hexdigest()


def configure():
    release.configure()
    run.OUT = OUT
    run.runtime_hash = runtime_hash


def make_scores(bank, features, data):
    scores, contexts = {}, {}
    for h in (1, 4, 8, 12, 24):
        f, cfg = features[h], bank.settings[h]
        e = bank.models[h].normal_evidence(f.reshape(-1, f.shape[-1]))
        contexts[h] = run.hour_scores(combine_evidence(e, cfg["weights"], cfg["temperature"]), data, h)
    for h, context in contexts.items():
        scores[h] = context if h == 1 else np.minimum(
            context - bank.settings[h]["threshold"], contexts[1] - bank.settings[1]["threshold"])
    return scores, contexts


def freeze():
    if OUT.exists(): raise FileExistsError("Preserve existing localization release")
    original = load_bank(release.OUT / "models.joblib")
    bank = LocalizedBank(copy.deepcopy(original.models), copy.deepcopy(original.settings),
        {**original.provenance, "runtime_sha256": runtime_hash(),
         "architecture_revision_after_first_native_test_review": True,
         "original_native_test_no_longer_blind": True, "no_test_fitted_numeric_parameters": True})
    # Development-only gate; retain exactly the prior learned densities,
    # weights and thresholds. There is no new test-dependent cutoff.
    release.configure()
    f, d = run.extract("development")
    scores, _ = make_scores(bank, f, d)
    y = load_split("development", True)["y"]
    groups = np.asarray([r["scenario"] for r in run.metadata("development", d["case_id"])])
    development = {str(h): run.metrics(y, s, bank.settings[1]["threshold"] if h == 1 else 0., groups)
                   for h, s in scores.items()}
    if not all(r["goal_pass"] for r in development.values()):
        raise RuntimeError(f"Development gate not met: {development}")
    OUT.mkdir(parents=True)
    joblib.dump(bank, OUT / "models.joblib", compress=3)
    write_json(OUT / "selected_configuration.json", {"development": development, "record": bank.record()})
    write_json(OUT / "model_manifest.json", {"models_sha256": sha256(OUT / "models.joblib"),
        "runtime_sha256": runtime_hash(), "selection_sha256": sha256(OUT / "selected_configuration.json"),
        "native_benchmark_manifest_sha256": sha256(DATA / "manifest.json"),
        "original_native_test_no_longer_blind": True, "new_numeric_thresholds_tuned_on_test": False})
    for h, r in development.items(): print("Localized development", h, r["median"], r["maximum_scenario_fpr"], flush=True)


def evaluate():
    if (OUT / "results.json").exists(): raise FileExistsError("Preserve localized evaluation")
    manifest = read_json(OUT / "model_manifest.json")
    if manifest["models_sha256"] != sha256(OUT / "models.joblib") or manifest["runtime_sha256"] != runtime_hash():
        raise ValueError("Frozen localization model/runtime changed")
    bank = load_bank(OUT / "models.joblib")
    release.configure()
    features, d = run.extract("test")
    scores, contexts = make_scores(bank, features, d)
    p = OUT / "test_predictions.npz"
    np.savez_compressed(p, case_id=d["case_id"], **{f"score_h{h}": s for h, s in scores.items()},
                        **{f"context_h{h}": s for h, s in contexts.items()})
    write_json(OUT / "test_prediction_seal.json", {"predictions_sha256": sha256(p),
        "model_sha256": sha256(OUT / "models.joblib"), "runtime_sha256": runtime_hash(),
        "predictions_saved_before_current_truth_read": True, "original_native_test_no_longer_blind": True})
    y = load_split("test", True)["y"]
    groups = np.asarray([r["scenario"] for r in run.metadata("test", d["case_id"])])
    results = {str(h): run.metrics(y, s, bank.settings[1]["threshold"] if h == 1 else 0., groups)
               for h, s in scores.items()}
    output = {"results": results, "all_horizons_goal_pass": all(r["goal_pass"] for r in results.values()),
        "point_adjustment": False, "hourly_labels_unchanged": True,
        "original_native_test_no_longer_blind": True, "independent_real_fault_validation": False,
        "old_hourly_results_preserved": True, "model_bytes": (OUT / "models.joblib").stat().st_size}
    write_json(OUT / "results.json", output)
    for h, r in results.items(): print("Localized test", h, r["median"], "max FPR", r["maximum_scenario_fpr"], "pass", r["goal_pass"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("freeze", "evaluate"))
    args = p.parse_args()
    freeze() if args.command == "freeze" else evaluate()
