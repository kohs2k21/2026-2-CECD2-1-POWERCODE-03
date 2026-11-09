"""Independent post-evaluation verification; never tunes model or benchmark."""
import argparse
from pathlib import Path
import time
from unittest.mock import patch
import joblib
import numpy as np
import pandas as pd
from .common import ROOT, REPORT, DATA, read_json, write_json, sha256
from .benchmark import load_split, CHANNELS
from .inference import score_frame


def count_metrics(y, score, threshold):
    valid = np.isfinite(score)
    y, p = y[valid].astype(bool), score[valid] > threshold
    tn, fp, fn, tp = (int(x.sum()) for x in (~y & ~p, ~y & p, y & ~p, y & p))
    return {"tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "accuracy": (tn + tp) / (tn + fp + fn + tp),
            "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1),
            "f1": 2 * tp / max(2 * tp + fp + fn, 1),
            "false_positive_rate": fp / max(fp + tn, 1)}


def validate():
    results = read_json(REPORT / "test_results.json")
    selection = read_json(REPORT / "selection.json")
    manifest = read_json(DATA / "manifest.json")
    checks = {}
    for name, expected in manifest["files"].items():
        assert sha256(DATA / name) == expected, name
    checks["all_frozen_benchmark_files_unchanged"] = True
    assert sha256(Path(__file__).with_name("benchmark.py")) == manifest["generator_sha256"]
    assert sha256(Path(__file__).with_name("protocol.json")) == manifest["protocol_sha256"]
    checks["frozen_generator_and_protocol_unchanged"] = True
    for source in read_json(REPORT / "source_audit.json")["sources"]:
        assert sha256(ROOT / source["source"]) == source["sha256"]
    checks["all_three_raw_process_sources_unchanged"] = True
    assert sha256(REPORT / "models.joblib") == selection["model_sha256"] == results["model_sha256"]
    assert sha256(Path(__file__).with_name("models.py")) == selection["models_source_sha256"]
    assert sha256(Path(__file__).with_name("persistent.py")) == selection["persistent_source_sha256"]
    seal = read_json(REPORT / "prediction_seal.json")
    assert seal["test_truth_opened"] is False
    assert sha256(REPORT / "selection.json") == results["selection_sha256"] == seal["selection_sha256"]
    checks["models_thresholds_and_prediction_seal_consistent"] = True
    all_ids, context_hashes = set(), set()
    cfg = read_json(DATA / "frozen_protocol.json")
    import hashlib
    split_sizes = {}
    for split in ("train", "calibration", "development", "test"):
        d = load_split(split, with_truth=True)
        assert set(d) == {"X", "collection_observed", "latency_available", "case_id", "start_ns", "channels", "y"}
        assert list(d["channels"]) == list(CHANNELS)
        assert not (set(d["case_id"]) & all_ids)
        all_ids.update(d["case_id"])
        if split in ("train", "calibration"):
            assert not d["y"].any()
        else:
            assert (d["y"].any(axis=1).sum() * 2) == len(d["X"])
            for i in range(0, len(d["X"]), 2):
                np.testing.assert_array_equal(d["X"][i, :cfg["context_hours"]], d["X"][i + 1, :cfg["context_hours"]])
                np.testing.assert_array_equal(d["collection_observed"][i], d["collection_observed"][i + 1])
        h = {hashlib.sha256(np.ascontiguousarray(row[:cfg["context_hours"]]).tobytes()).hexdigest() for row in d["X"]}
        assert not h & context_hashes
        context_hashes |= h
        split_sizes[split] = {"cases": len(d["X"]), "hour_rows": int(np.prod(d["X"].shape[:2]))}
    checks["no_cross_split_case_or_reference_context_overlap"] = True
    checks["no_label_scenario_severity_columns_in_model_inputs"] = True
    package = joblib.load(REPORT / "models.joblib")
    d = load_split("test", with_truth=True)
    meta = pd.read_csv(DATA / "case_metadata.csv").set_index("case_id").loc[d["case_id"]]
    for method, model in package["models"].items():
        path = REPORT / f"test_scores_{method}.npz"
        assert sha256(path) == seal["predictions"][method]["scores_sha256"]
        with np.load(path) as z:
            score = z["score"]
            assert float(z["threshold"]) == model.threshold == selection["models"][method]["record"]["threshold"]
        groups = {"pooled": (np.ones(len(meta), bool), results["results"][method]["pooled"])}
        groups.update({task: (meta.scenario.eq(task).to_numpy(), r) for task, r in results["results"][method]["scenarios"].items()})
        for task, (keep, record) in groups.items():
            independent = count_metrics(d["y"][keep].ravel(), score[keep].ravel(), model.threshold)
            for key, value in independent.items():
                assert np.isclose(value, record[key], rtol=0, atol=1e-12), (method, task, key)
    checks["all_model_scenario_and_pooled_metrics_independently_recomputed"] = True
    model = package["models"][package["primary"]]
    with np.load(REPORT / f"test_scores_{package['primary']}.npz") as z:
        expected = z["score"]
    began = time.perf_counter()
    tested = []
    for task in sorted(meta.scenario.unique()):
        i = int(np.flatnonzero(meta.scenario.eq(task) & meta.injected)[0])
        f = pd.DataFrame(d["X"][i], columns=CHANNELS)
        f["timestamp"] = pd.date_range(pd.Timestamp(int(d["start_ns"][i])), periods=len(f), freq="h")
        f["case_id"] = "unknown_new_adapter_and_process"
        f["collection_observed"] = d["collection_observed"][i]
        f["latency_available"] = d["latency_available"][i]
        # The model API must not read original logs, labels or benchmark files.
        with patch("builtins.open", side_effect=AssertionError("Inference performed file I/O")):
            out = score_frame(model, f)
        np.testing.assert_allclose(out.score, expected[i], rtol=0, atol=1e-12, equal_nan=True)
        # Unknown labels/identifiers do not change the score.
        tested.append(str(d["case_id"][i]))
    checks["validated_api_reproduces_six_saved_cases_without_file_io"] = True
    checks["unknown_identifiers_need_no_lookup_or_routing"] = True
    selected = results["results"][package["primary"]]
    median = {k: float(np.median([v[k] for v in selected["scenarios"].values()]))
              for k in ("accuracy", "precision", "recall", "f1")}
    for key, value in median.items():
        assert np.isclose(value, selected["median"][key], atol=1e-12)
    target_met = min(median.values()) >= .70 and selected["maximum_scenario_fpr"] <= .06 and selected["pooled"]["false_positive_rate"] <= .06
    assert target_met == selected["target_met"]
    write_json(REPORT / "validation.json", {"integrity_passed": all(checks.values()), "checks": checks,
        "split_sizes": split_sizes, "api_cases_tested": tested,
        "six_api_cases_seconds": time.perf_counter() - began, "primary_goal_met": target_met,
        "model_bytes": (REPORT / "models.joblib").stat().st_size,
        "median": median, "maximum_scenario_fpr": selected["maximum_scenario_fpr"]})
    print("Independent validation", checks, "goal_met", target_met, flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    validate()
