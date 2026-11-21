"""Verify same-hour metrics, legacy preservation and API/bank equivalence."""
from pathlib import Path
from unittest.mock import patch
import argparse
import numpy as np
import pandas as pd
from ..common import ROOT, REPORT, DATA, read_json, write_json, sha256
from ..benchmark import load_split, CHANNELS
from ..validate import count_metrics
from ..experiment import metadata_for_split
from .model import HORIZONS, load_bank, score_frame
from .run import OUT, code_hash


def validate():
    checks = {}
    for name, expected in read_json(OUT / "preservation.json")["files"].items():
        assert sha256(ROOT / name) == expected, name
    checks["legacy_model_code_notebook_results_preserved"] = True
    manifest = read_json(DATA / "manifest.json")
    for name, expected in manifest["files"].items():
        assert sha256(DATA / name) == expected, name
    checks["original_frozen_benchmark_unchanged"] = True
    for source in read_json(REPORT / "source_audit.json")["sources"]:
        assert sha256(ROOT / source["source"]) == source["sha256"]
    checks["all_three_raw_process_sources_preserved"] = True
    calibration, results, seal = (read_json(OUT / n) for n in
                                  ("calibration.json", "results.json", "prediction_seal.json"))
    assert code_hash() == calibration["code_sha256"] == seal["code_sha256"]
    assert sha256(OUT / "models.joblib") == calibration["model_sha256"] == results["model_sha256"]
    bank = load_bank(OUT / "models.joblib")
    data = load_split("test", with_truth=True)
    meta = metadata_for_split("test", data["case_id"])
    counts = set()
    replay = []
    # One fixed injected case per scenario, selected by file order, never by score.
    case_indices = [int(np.flatnonzero(meta.scenario.eq(task) & meta.injected)[0])
                    for task in sorted(meta.scenario.unique())]
    for h in HORIZONS:
        path = OUT / f"scores_{h}h.npz"
        assert sha256(path) == seal["scores_sha256"][str(h)]
        with np.load(path, allow_pickle=False) as f:
            score, threshold = f["score"], float(f["threshold"])
        assert threshold == bank.models[h].threshold
        r = results["results"][str(h)]
        groups = {"pooled": (np.ones(len(meta), bool), r["pooled"])}
        groups.update({task: (meta.scenario.eq(task).to_numpy(), value) for task, value in r["scenarios"].items()})
        for task, (keep, value) in groups.items():
            independent = count_metrics(data["y"][keep].ravel(), score[keep].ravel(), threshold)
            for k, v in independent.items():
                assert np.isclose(v, value[k], rtol=0, atol=1e-12), (h, task, k)
        counts.add(r["pooled"]["assessed_hours"])
        for k in ("accuracy", "precision", "recall", "f1"):
            assert np.isclose(np.median([v[k] for v in r["scenarios"].values()]), r["median"][k])
        for i in case_indices:
            frame = pd.DataFrame(data["X"][i], columns=CHANNELS)
            frame["timestamp"] = pd.date_range(pd.Timestamp(int(data["start_ns"][i])), periods=len(frame), freq="h")
            frame["case_id"] = str(data["case_id"][i])
            frame["collection_observed"] = data["collection_observed"][i]
            frame["latency_available"] = data["latency_available"][i]
            # Full 14-day replay under selected H, but each batch isolated.
            with patch("builtins.open", side_effect=AssertionError("Inference performed file I/O")):
                out = score_frame(bank, frame, h, split_batches=True)
            np.testing.assert_allclose(out.score, score[i], rtol=0, atol=1e-12, equal_nan=True)
            assert len(out) == 336
            replay.append(f"{h}h:{data['case_id'][i]}")
    assert len(counts) == 1
    checks["all_horizons_use_exactly_the_same_observed_hours"] = True
    checks["all_pooled_and_scenario_metrics_independently_recomputed"] = True
    checks["thirty_full_API_replays_match_saved_scores_without_file_io"] = True
    checks["horizon_specific_thresholds_match_saved_models"] = True
    checks["no_new_blind_test_claim"] = bool(results["no_independent_new_test_claimed"])
    write_json(OUT / "validation.json", {"passed": all(checks.values()), "checks": checks,
        "assessed_hours_per_horizon": next(iter(counts)), "api_replays": replay,
        "model_bytes": (OUT / "models.joblib").stat().st_size})
    print("Interval validation", checks, flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    validate()
