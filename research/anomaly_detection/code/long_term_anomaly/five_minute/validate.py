"""Independent counts, masks, provenance and public backend replay checks."""
import argparse
from contextlib import ExitStack
from unittest.mock import patch
import numpy as np
import pandas as pd

from ..common import ROOT, REPORT, read_json, write_json, sha256
from . import benchmark, confirmation, localized
from .bank import load_bank
from .serialization import load_portable


def independent_metrics(y, s, cutoff):
    available = np.isfinite(s)
    actual = y[available].astype(np.int8)
    predicted = (s[available] > cutoff).astype(np.int8)
    matrix = np.bincount(actual * 2 + predicted, minlength=4)
    tn, fp, fn, tp = map(int, matrix)
    total = int(matrix.sum())
    return dict(accuracy=(tp + tn) / max(total, 1), precision=tp / max(tp + fp, 1),
        recall=tp / max(tp + fn, 1), f1=2 * tp / max(2 * tp + fp + fn, 1),
        false_positive_rate=fp / max(tn + fp, 1), tp=tp, tn=tn, fp=fp, fn=fn,
        assessed_hours=total, unassessable_hours=int(s.size - total))


def make_frame(data, index, begin, length):
    f = pd.DataFrame(data["X"][index, begin:begin + length], columns=benchmark.CHANNELS)
    start = pd.Timestamp(int(data["start_ns"][index])) + pd.Timedelta(minutes=5 * begin)
    f["timestamp"] = pd.date_range(start, periods=length, freq="5min")
    f["case_id"] = str(data["case_id"][index])
    f["collection_observed"] = data["collection_observed"][index, begin:begin + length]
    f["latency_available"] = data["latency_available"][index, begin:begin + length]
    return f


def verify_population(bank, data, meta, result_path, prediction_path):
    result = read_json(result_path)
    if [r["case_id"] for r in meta] != list(data["case_id"]): raise AssertionError("Case order")
    groups = np.asarray([r["scenario"] for r in meta])
    tasks = set(read_json(benchmark.DATA / "frozen_protocol.json")["scenario_definitions"])
    assert set(groups) == tasks and len(tasks) == 6 and len(groups) == 576
    expected_y = np.zeros((len(groups), 336), bool)
    for i, row in enumerate(meta):
        if row["injected"]:
            expected_y[i, row["event_start_hour"]:row["event_end_hour"]] = True
    np.testing.assert_array_equal(data["y"], expected_y)
    observed = data["collection_observed"][:, 8064:].reshape(-1, 336, 12).all(axis=2)
    with np.load(prediction_path, allow_pickle=False) as z:
        np.testing.assert_array_equal(z["case_id"], data["case_id"])
        scores = {h: z[f"score_h{h}"] for h in (1, 4, 8, 12, 24)}
    checks, replays = 0, 0
    for h, scores_h in scores.items():
        np.testing.assert_array_equal(np.isfinite(scores_h), observed)
        cutoff = bank.settings[1]["threshold"] if h == 1 else 0.
        reported = result["results"][str(h)]
        rows = {}
        for g in [None, *sorted(tasks)]:
            subset = np.ones(len(groups), bool) if g is None else groups == g
            recalculated = independent_metrics(data["y"][subset], scores_h[subset], cutoff)
            stored = reported["pooled"] if g is None else reported["scenarios"][g]
            for name, value in recalculated.items():
                np.testing.assert_allclose(value, stored[name], rtol=0, atol=1e-12)
            if g is not None: rows[g] = recalculated
            checks += 1
        medians = {k: float(np.median([r[k] for r in rows.values()])) for k in ("accuracy", "precision", "recall", "f1")}
        for name, value in medians.items(): np.testing.assert_allclose(value, reported["median"][name], rtol=0, atol=1e-12)
        gate = min(medians.values()) >= .65 and max(r["false_positive_rate"] for r in rows.values()) <= .06 and reported["pooled"]["false_positive_rate"] <= .06
        assert gate == reported["goal_pass"]
        if h > 1:
            # A generic structural guarantee, independent of anomaly labels.
            assert not ((scores_h > 0) & ~(scores[1] > bank.settings[1]["threshold"])).any()
    # Reconstruct normal parameters and call the public, portable backend for
    # every case and every H (2,880 independently replayed selected windows).
    # No cache files, labels or CSV reads can occur inside model inference.
    with ExitStack() as stack:
        stack.enter_context(patch("builtins.open", side_effect=AssertionError("Inference file I/O")))
        stack.enter_context(patch("pathlib.Path.open", side_effect=AssertionError("Inference path I/O")))
        for i in range(len(groups)):
            reference = bank.prepare_reference(make_frame(data, i, 0, 8064))
            for h in (1, 4, 8, 12, 24):
                window = i % (336 // h)
                start_hour = window * h
                batch = make_frame(data, i, 8064 + start_hour * 12, h * 12)
                actual = bank.predict_batch(reference, batch, h)
                np.testing.assert_allclose(actual.score, scores[h][i, start_hour:start_hour + h], rtol=0, atol=1e-12, equal_nan=True)
                assert not actual.monitoring_state_carried.any()
                replays += 1
    assert result["all_horizons_goal_pass"] == all(r["goal_pass"] for r in result["results"].values())
    return {"independent_confusion_matrices": checks, "public_backend_replays": replays,
        "all_original_hour_positions_preserved": True, "label_expansion_or_point_adjustment": False,
        "all_horizons_goal_pass": result["all_horizons_goal_pass"]}


def run():
    out = localized.OUT
    model = load_portable()
    frozen = read_json(out / "model_manifest.json")
    assert frozen["runtime_sha256"] == localized.runtime_hash()
    port = read_json(out / "portable_model_manifest.json")
    assert port["numeric_state_identical"] and port["numerical_state_hash_before"] == port["numerical_state_hash_after"]
    # Validate complete benchmark manifests, not merely filename existence.
    for data_dir in (ROOT / "data/evaluation_long_term", benchmark.DATA, confirmation.DATA):
        manifest = read_json(data_dir / "manifest.json")
        for name, expected in manifest["files"].items(): assert sha256(data_dir / name) == expected
    native_manifest = read_json(benchmark.DATA / "manifest.json")
    assert native_manifest["generator_sha256"] == sha256(benchmark.__file__)
    cm = read_json(confirmation.DATA / "manifest.json")
    assert cm["confirmation_generator_sha256"] == sha256(confirmation.__file__)
    assert cm["frozen_model_sha256"] == frozen["models_sha256"]
    for src in read_json(REPORT / "source_audit.json")["sources"]:
        assert sha256(ROOT / src["source"]) == src["sha256"]
    aligned = REPORT / "strict_alignment"
    assert sha256(aligned / "models.joblib") == read_json(aligned / "calibration.json")["model_sha256"]
    summaries = {}
    for label, data, meta, result, prediction in (
        ("native_test_reused_for_architecture_diagnosis", benchmark.load_split("test", True),
         read_json(benchmark.DATA / "test_metadata.json"), "results.json", "test_predictions.npz"),
        ("fresh_frozen_model_confirmation", confirmation.load(True),
         read_json(confirmation.DATA / "metadata.json"), "confirmation_results.json", "confirmation_predictions.npz")):
        summaries[label] = verify_population(model, data, meta, out / result, out / prediction)
        print("Independent validation", label, summaries[label], flush=True)
    for name in ("test", "confirmation"):
        seal = read_json(out / f"{name}_prediction_seal.json")
        assert seal["predictions_sha256"] == sha256(out / f"{name}_predictions.npz")
        assert seal["model_sha256"] == sha256(out / "models.joblib")
        assert seal["runtime_sha256"] == localized.runtime_hash()
    value = {"populations": summaries, "original_period_sources_unchanged": True,
        "original_hourly_benchmark_and_model_preserved": True, "runtime_and_prediction_seals_valid": True,
        "portable_numerical_model_unchanged": True, "no_model_file_io_during_backend_replay": True,
        "independent_real_fault_validation": False,
        "goal_verified": all(v["all_horizons_goal_pass"] for v in summaries.values())}
    write_json(out / "validation.json", value)
    if not value["goal_verified"]: raise RuntimeError("Performance goal remains unmet")
    return value


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args();run()
