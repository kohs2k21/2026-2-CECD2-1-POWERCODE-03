"""Calibrate five batch lengths without altering the original dataset/release.

This reuses a previously reported test population. It is a controlled horizon
comparison, not a new independent blind evaluation. No test-driven thresholding.
"""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import argparse
import hashlib
import os
import time
import joblib
import numpy as np
import pandas as pd
from ..common import ROOT, REPORT, DATA, read_json, write_json, sha256
from ..benchmark import load_split
from ..experiment import summarize, metadata_for_split
from ..persistent import PersistentDetector
from .model import (HORIZONS, HOUR_NS, IntervalModelBank, prepare_reference_arrays,
                    raw_residual, sequence_components, setting_for)

OUT = REPORT / "intervals"
PROTOCOL = Path(__file__).with_name("protocol.json")


def code_hash():
    files = [Path(__file__), Path(__file__).with_name("model.py"),
             Path(__file__).parents[1] / "models.py", Path(__file__).parents[1] / "persistent.py"]
    return hashlib.sha256("".join(sha256(p) for p in files).encode()).hexdigest()


def initialize():
    OUT.mkdir(exist_ok=True)
    (OUT / "cache").mkdir(exist_ok=True)
    snapshot = OUT / "preservation.json"
    if snapshot.exists():
        for name, expected in read_json(snapshot)["files"].items():
            assert sha256(ROOT / name) == expected, f"Legacy file changed: {name}"
        assert sha256(PROTOCOL) == read_json(OUT / "protocol.json")["source_protocol_sha256"]
        return
    legacy = ["code/long_term_anomaly/models.py", "code/long_term_anomaly/persistent.py",
        "code/long_term_anomaly/inference.py", "code/long_term_anomaly/result.ipynb",
        "reports/long_term_anomaly/models.joblib", "reports/long_term_anomaly/test_results.json",
        "reports/long_term_anomaly/selection.json", "reports/long_term_anomaly/test_metrics.csv",
        "reports/long_term_anomaly/handoff/long_term_model.joblib",
        "reports/long_term_anomaly/handoff/metadata.json", "data/evaluation_long_term/manifest.json"]
    write_json(snapshot, {"files": {name: sha256(ROOT / name) for name in legacy}})
    write_json(OUT / "protocol.json", {"source_protocol_sha256": sha256(PROTOCOL),
        "benchmark_manifest_sha256": sha256(DATA / "manifest.json"),
        "plan": read_json(PROTOCOL), "fixed_before_interval_model_scores": True})


def pool(size):
    if size < 16:
        return ThreadPoolExecutor(max_workers=2)
    workers = min(8, max(1, (os.cpu_count() or 2) // 2))
    return ProcessPoolExecutor(max_workers=workers)


def _reference_worker(payload):
    x, observed, latency, start_ns, case_id, context = payload
    reference = prepare_reference_arrays(x[:context], observed[:context], latency[:context],
        setting_for(24, context), pd.Timestamp(int(start_ns) + (context - 1) * HOUR_NS), case_id)
    return raw_residual(reference, x[context:], observed[context:], latency[context:])


def raw_bank(split):
    data = load_split(split, with_truth=False)
    signature = hashlib.sha256((sha256(DATA / f"{split}_inputs.npz") + code_hash()).encode()).hexdigest()[:20]
    path = OUT / "cache" / f"raw_{split}_{signature}.npz"
    if path.exists():
        with np.load(path, allow_pickle=False) as f:
            return f["residual"], data
    context = read_json(PROTOCOL)["normal_reference_hours"]
    payloads = ((data["X"][i], data["collection_observed"][i], data["latency_available"][i],
                 data["start_ns"][i], data["case_id"][i], context) for i in range(len(data["X"])))
    began = time.perf_counter()
    with pool(len(data["X"])) as executor:
        result = []
        for i, r in enumerate(executor.map(_reference_worker, payloads, chunksize=4)):
            result.append(r)
            if (i + 1) % 96 == 0:
                print("Normal-reference forecast", split, i + 1, "/", len(data["X"]), flush=True)
    result = np.asarray(result)
    np.savez_compressed(path, residual=result)
    print("Reference ready", split, round(time.perf_counter() - began, 1), "seconds", flush=True)
    return result, data


def _component_worker(payload):
    residual, observed, latency, hours, setting = payload
    return sequence_components(residual, observed, latency, hours, setting)


def components_bank(split, hours, residual, data):
    context = read_json(PROTOCOL)["normal_reference_hours"]
    setting = setting_for(hours, context)
    signature = hashlib.sha256((sha256(DATA / f"{split}_inputs.npz") + code_hash()).encode()).hexdigest()[:20]
    path = OUT / "cache" / f"components_{split}_{hours}h_{signature}.npz"
    if path.exists():
        with np.load(path, allow_pickle=False) as f:
            return f["components"]
    payloads = ((residual[i], data["collection_observed"][i, context:],
        data["latency_available"][i, context:], hours, setting) for i in range(len(residual)))
    with pool(len(residual)) as executor:
        result = np.asarray(list(executor.map(_component_worker, payloads, chunksize=4)))
    np.savez_compressed(path, components=result)
    print("Components", split, f"{hours}h", result.shape, flush=True)
    return result


def scores(model, components):
    return np.asarray([model.score_components(f)[0] for f in components])


def fit():
    initialize()
    if (OUT / "results.json").exists():
        raise FileExistsError("Comparison already evaluated; do not silently retune its test results")
    cfg = read_json(PROTOCOL)
    inputs, raw = {}, {}
    for split in ("train", "calibration", "development"):
        raw[split], inputs[split] = raw_bank(split)
    y_dev = load_split("development", with_truth=True)["y"]
    meta = metadata_for_split("development", inputs["development"]["case_id"])
    models, calibration = {}, {}
    for hours in HORIZONS:
        features = {split: components_bank(split, hours, raw[split], inputs[split])
                    for split in inputs}
        model = PersistentDetector(setting_for(hours), energy_weight=1., local_weight=1.)
        model.fit_reference(features["train"])
        cal_score, dev_score = scores(model, features["calibration"]), scores(model, features["development"])
        floor = float(np.nanquantile(cal_score, cfg["normal_calibration_quantile_floor"]))
        normal_thresholds = {}
        for task in sorted(meta.scenario.unique()):
            normal = (meta.scenario.eq(task).to_numpy()[:, None] & ~y_dev & np.isfinite(dev_score))
            normal_thresholds[str(task)] = float(np.quantile(dev_score[normal],
                1 - cfg["development_normal_fpr_cap"], method="higher"))
        model.threshold = max(floor, *normal_thresholds.values())
        models[hours] = model
        calibration[str(hours)] = {"normal_calibration_floor": floor,
            "normal_development_thresholds": normal_thresholds, "record": model.record(),
            "development": summarize(y_dev, dev_score, model.threshold,
                                     inputs["development"]["case_id"], "development")}
        print("Calibrated", f"{hours}h", "threshold", round(model.threshold, 6),
              "dev median", calibration[str(hours)]["development"]["median"], flush=True)
    bank = IntervalModelBank(models, cfg["normal_reference_hours"])
    joblib.dump(bank, OUT / "models.joblib", compress=3)
    write_json(OUT / "calibration.json", {"code_sha256": code_hash(),
        "model_sha256": sha256(OUT / "models.joblib"), "protocol_sha256": sha256(OUT / "protocol.json"),
        "test_scores_used_for_calibration": False, "previous_test_population_reused": True,
        "algorithm_parameters_fixed_across_horizons_except_window_clipping": True,
        "threshold_selection_uses_only_normal_calibration_and_normal_development_hours": True,
        "models": calibration})


def evaluate():
    initialize()
    if (OUT / "results.json").exists():
        print("Existing comparison retained. No model/threshold changes.", flush=True)
        return
    c = read_json(OUT / "calibration.json")
    assert sha256(OUT / "models.joblib") == c["model_sha256"]
    assert code_hash() == c["code_sha256"]
    bank = joblib.load(OUT / "models.joblib")
    residual, data = raw_bank("test")
    predictions, seal = {}, {}
    for hours in HORIZONS:
        features = components_bank("test", hours, residual, data)
        model = bank.models[hours]
        values = scores(model, features)
        reasons = np.asarray([model.score_components(f)[1] for f in features], dtype=str)
        path = OUT / f"scores_{hours}h.npz"
        np.savez_compressed(path, score=values, reason=reasons, case_id=data["case_id"], threshold=model.threshold)
        predictions[hours] = values
        seal[str(hours)] = sha256(path)
    write_json(OUT / "prediction_seal.json", {"model_sha256": c["model_sha256"],
        "code_sha256": code_hash(), "scores_sha256": seal,
        "new_horizon_scores_saved_before_comparison_labels_loaded": True,
        "test_population_previously_reported": True, "fresh_blind_test_claimed": False})
    y = load_split("test", with_truth=True)["y"]
    results = {str(h): summarize(y, predictions[h], bank.models[h].threshold, data["case_id"], "test")
               for h in HORIZONS}
    counts = {r["pooled"]["assessed_hours"] for r in results.values()}
    assert len(counts) == 1, "Horizon comparison must score the same observed hours"
    write_json(OUT / "results.json", {"model_sha256": c["model_sha256"],
        "calibration_sha256": sha256(OUT / "calibration.json"), "results": results,
        "comparison_unit": "same observed hours across all horizons", "aggregation_changed": False,
        "normal_reference_hours": 672, "analysis_hours": list(HORIZONS),
        "labels_expanded_or_point_adjusted": False, "previous_test_set_reused": True,
        "no_independent_new_test_claimed": True})
    rows = [{"analysis_hours": int(h), "scenario": task, **value}
            for h, r in results.items() for task, value in r["scenarios"].items()]
    pd.DataFrame(rows).to_csv(OUT / "scenario_metrics.csv", index=False, encoding="utf-8-sig")
    summary = [{"analysis_hours": int(h), "threshold": bank.models[int(h)].threshold,
                **{f"median_{k}": v for k, v in r["median"].items()},
                **{f"pooled_{k}": r["pooled"][k] for k in ("accuracy", "precision", "recall", "f1", "false_positive_rate")},
                "maximum_scenario_fpr": r["maximum_scenario_fpr"], "assessed_hours": r["pooled"]["assessed_hours"],
                "median_goal_met": r["target_met"]} for h, r in results.items()]
    pd.DataFrame(summary).to_csv(OUT / "comparison.csv", index=False, encoding="utf-8-sig")
    print(pd.DataFrame(summary).to_string(index=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["fit", "evaluate"])
    args = p.parse_args()
    fit() if args.stage == "fit" else evaluate()
