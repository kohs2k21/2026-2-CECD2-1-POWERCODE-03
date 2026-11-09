"""Normal fit -> normal calibration -> development selection -> sealed test.

No test truths, scenario IDs, injection magnitudes or event boundaries enter
fit or prediction. Test input/score generation happens before truths are opened.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from dataclasses import asdict, replace
import hashlib
import os
from pathlib import Path
import time
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, accuracy_score, precision_score, recall_score, f1_score
from .benchmark import load_split
from .common import REPORT, DATA, read_json, write_json, sha256
from .models import Setting, LongTermDetector, prepare_case, components_from_residual
from .persistent import PersistentDetector

METRICS = ("accuracy", "precision", "recall", "f1")


def _residual_worker(payload):
    x, observed, latency, setting = payload
    return prepare_case(x, observed, latency, setting)[0]


def _component_worker(payload):
    residual, observed, latency, setting = payload
    return components_from_residual(residual, observed, latency, setting)[0]


def worker_pool(size):
    # Independent cases; no shared detector state, seeds or labels. Multiprocess
    # avoids the GIL in the Cython/statistical routines on Windows.
    if size < 16:
        return ThreadPoolExecutor(max_workers=2)
    workers = min(int(os.environ.get("LONG_TERM_WORKERS", "8")), max(1, (os.cpu_count() or 2) // 2))
    return ProcessPoolExecutor(max_workers=max(1, workers))


def metadata_for_split(split, case_ids):
    cfg = read_json(DATA / "frozen_protocol.json")
    sizes = {"train": cfg["train_normal_cases"], "calibration": cfg["calibration_normal_cases"],
             "development": 2 * len(cfg["scenario_definitions"]) * cfg["development_pairs_per_scenario"],
             "test": 2 * len(cfg["scenario_definitions"]) * cfg["test_pairs_per_scenario"]}
    offset = 0
    for name, size in sizes.items():
        if name == split:
            break
        offset += size
    # Do not load final-test metadata during development selection.
    meta = pd.read_csv(DATA / "case_metadata.csv", skiprows=range(1, offset + 1), nrows=sizes[split])
    meta = meta.set_index("case_id").loc[case_ids]
    assert meta.split.eq(split).all()
    return meta


def metrics(y, score, threshold):
    valid = np.isfinite(score)
    y, pred = y[valid].astype(bool), score[valid] > threshold
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[False, True]).ravel()
    return {"accuracy": float(accuracy_score(y, pred)),
            "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0)),
            "f1": float(f1_score(y, pred, zero_division=0)),
            "false_positive_rate": float(fp / (fp + tn)),
            "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
            "assessed_hours": len(y), "unassessable_hours": int((~valid).sum())}


def summarize(y, scores, threshold, case_ids, split):
    meta = metadata_for_split(split, case_ids)
    groups = meta.scenario.to_numpy(str)
    result = {"pooled": metrics(y.ravel(), scores.ravel(), threshold), "scenarios": {}}
    for task in sorted(set(groups)):
        keep = groups == task
        result["scenarios"][task] = metrics(y[keep].ravel(), scores[keep].ravel(), threshold)
    result["median"] = {key: float(np.median([s[key] for s in result["scenarios"].values()])) for key in METRICS}
    result["maximum_scenario_fpr"] = max(s["false_positive_rate"] for s in result["scenarios"].values())
    result["target_met"] = (min(result["median"].values()) >= .70
                            and result["maximum_scenario_fpr"] <= .06
                            and result["pooled"]["false_positive_rate"] <= .06)
    return result


def residual_bank(split, base):
    f = load_split(split, with_truth=False)
    cache = REPORT / "cache"
    cache.mkdir(exist_ok=True)
    # Input files and actual preprocessing source determine cache identity.
    signature = sha256(DATA / f"{split}_inputs.npz")[:12] + sha256(Path(__file__).with_name("models.py"))[:12]
    path = cache / f"residual_{split}_{base}_{signature}.npz"
    if path.exists():
        with np.load(path, allow_pickle=False) as z:
            return z["residual"], f
    config = read_json(DATA / "frozen_protocol.json")
    s = Setting(method=base, context_hours=config["context_hours"])
    began = time.perf_counter()
    payloads = ((f["X"][i], f["collection_observed"][i], f["latency_available"][i], s) for i in range(len(f["X"])))
    with worker_pool(len(f["X"])) as pool:
        result = []
        for i, r in enumerate(pool.map(_residual_worker, payloads, chunksize=4)):
            result.append(r)
            if (i + 1) % 48 == 0:
                print("Residual", split, base, i + 1, "/", len(f["X"]), flush=True)
    result = np.asarray(result)
    np.savez_compressed(path, residual=result)
    print("Residual ready", split, base, round(time.perf_counter() - began, 1), "seconds", flush=True)
    return result, f


def component_bank(residual, inputs, setting):
    c = setting.context_hours
    cache_path = None
    if setting.method == "stl_pelt":
        # PELT segmentation does not depend on the rolling-window width. Reuse
        # exactly the same computed segment features across window candidates.
        digest = hashlib.sha256(np.ascontiguousarray(residual).tobytes())
        digest.update(inputs["collection_observed"].tobytes())
        digest.update(inputs["latency_available"].tobytes())
        digest.update(sha256(Path(__file__).with_name("models.py")).encode())
        digest.update(str((setting.penalty_factor, setting.min_segment, setting.jump)).encode())
        cache_path = REPORT / "cache" / f"segments_{digest.hexdigest()[:24]}.npz"
        if cache_path.exists():
            local = component_bank(residual, inputs, replace(setting, method="stl_window"))
            with np.load(cache_path, allow_pickle=False) as saved:
                return np.concatenate([local, saved["segments"]], axis=2)
    payloads = ((residual[i], inputs["collection_observed"][i, c:],
                 inputs["latency_available"][i, c:], setting) for i in range(len(residual)))
    with worker_pool(len(residual)) as pool:
        result = np.asarray(list(pool.map(_component_worker, payloads, chunksize=4)))
    if cache_path is not None:
        np.savez_compressed(cache_path, segments=result[:, :, 6:])
    return result


def score_bank(model, components):
    return np.asarray([model.score_components(c)[0] for c in components])


def tune():
    REPORT.mkdir(exist_ok=True)
    if (REPORT / "test_results.json").exists():
        raise FileExistsError("Test has already been opened. Do not silently retune a reported final test.")
    cfg = read_json(DATA / "frozen_protocol.json")
    banks, inputs = {}, {}
    for base in ("stl_window", "seasonal_naive"):
        for split in ("train", "calibration", "development"):
            banks[base, split], inputs[split] = residual_bank(split, base)
    dev_truth = load_split("development", with_truth=True)["y"]
    all_results, winners = [], {}
    settings = [Setting(method="seasonal_naive", context_hours=cfg["context_hours"])]
    settings += [Setting(method="stl_window", context_hours=cfg["context_hours"], window=w,
                         energy_window=2 * w) for w in (12, 24, 36, 48)]
    settings += [Setting(method="stl_pelt", context_hours=cfg["context_hours"], window=w,
                         energy_window=2 * w, penalty_factor=p) for w in (12, 24, 48) for p in (.5, 1, 2)]
    for candidate, setting in enumerate(settings):
        began = time.perf_counter()
        base = "seasonal_naive" if setting.method == "seasonal_naive" else "stl_window"
        features = {split: component_bank(banks[base, split], inputs[split], setting)
                    for split in ("train", "calibration", "development")}
        variants = [(energy, local) for energy in (.6, 1., 1.5)
                    for local in ((0., .3, .6, 1.) if setting.method == "stl_pelt" else (1.,))]
        for energy_weight, local_weight in variants:
            model = (PersistentDetector(setting, energy_weight, local_weight) if setting.method == "stl_pelt"
                     else LongTermDetector(setting, energy_weight)).fit_reference(features["train"])
            cal_scores = score_bank(model, features["calibration"])
            dev_scores = score_bank(model, features["development"])
            thresholds = [(q, float(np.nanquantile(cal_scores, q))) for q in (.96, .97, .975, .98, .985, .99, .995)]
            # Refine the normal-only operating point using development normal
            # hours, including normal portions of injected cases. This corrects
            # coarse quantile steps without looking at any final-test scores.
            meta = metadata_for_split("development", inputs["development"]["case_id"])
            for cap in (.03, .04, .045, .05):
                threshold = float(np.nanquantile(cal_scores, .96))
                for task in sorted(meta.scenario.unique()):
                    mask = (meta.scenario.eq(task).to_numpy()[:, None] & ~dev_truth & np.isfinite(dev_scores))
                    threshold = max(threshold, float(np.quantile(dev_scores[mask], 1 - cap, method="higher")))
                thresholds.append((f"development_normal_cap_{cap}", threshold))
            for q, threshold in thresholds:
                r = summarize(dev_truth, dev_scores, threshold, inputs["development"]["case_id"], "development")
                # Development-only constrained selection. Reserve a percentage
                # point of FPR headroom; there is no mathematical test guarantee.
                feasible = (r["maximum_scenario_fpr"] <= .05
                            and r["pooled"]["false_positive_rate"] <= .05)
                ranking = (int(feasible), int(r["target_met"]),
                           min(r["median"]["recall"], r["median"]["f1"]),
                           r["median"]["f1"], -r["maximum_scenario_fpr"])
                row = {"candidate": candidate, "method": setting.method, "window": setting.window,
                       "energy_window": setting.energy_window, "penalty_factor": setting.penalty_factor,
                       "energy_weight": energy_weight, "calibration_quantile": q, "threshold": threshold,
                       "local_weight": local_weight,
                       "development_fpr_max": r["maximum_scenario_fpr"],
                       **{f"median_{k}": v for k, v in r["median"].items()}, "target_met": r["target_met"]}
                all_results.append(row)
                old = winners.get(setting.method)
                if old is None or ranking > old["ranking"]:
                    model.threshold = threshold
                    # Each threshold loop must snapshot the model; otherwise a
                    # later q can accidentally change the earlier winner.
                    import copy
                    winners[setting.method] = {"ranking": ranking, "model": copy.deepcopy(model),
                                               "development": r, "parameters": row}
        pd.DataFrame(all_results).to_csv(REPORT / "development_search.csv", index=False)
        print("Candidate", candidate, asdict(setting), "seconds", round(time.perf_counter() - began, 1),
              "current", {k: {m: round(v, 3) for m, v in w['development']['median'].items()} for k, w in winners.items()}, flush=True)
    # Baselines are comparators, not required to meet the release target. The
    # primary model is selected from the two long-horizon statistical methods.
    champion = max((k for k in winners if k != "seasonal_naive"), key=lambda k: winners[k]["ranking"])
    package = {"models": {k: v["model"] for k, v in winners.items()}, "primary": champion,
               "benchmark_manifest_sha256": sha256(DATA / "manifest.json"),
               "models_source_sha256": sha256(Path(__file__).with_name("models.py")),
               "persistent_source_sha256": sha256(Path(__file__).with_name("persistent.py"))}
    joblib.dump(package, REPORT / "models.joblib", compress=3)
    write_json(REPORT / "selection.json", {"primary": champion,
        "selected_using": "normal train, normal calibration, labeled development only",
        "test_opened": False, "manifest_sha256": sha256(DATA / "manifest.json"),
        "model_sha256": sha256(REPORT / "models.joblib"),
        "models_source_sha256": package["models_source_sha256"],
        "persistent_source_sha256": package["persistent_source_sha256"],
        "candidates": len(settings), "search_rows": len(all_results),
        "models": {k: {"parameters": w["parameters"], "development": w["development"],
                       "record": w["model"].record()} for k, w in winners.items()}})
    print("Selected", champion, winners[champion]["development"], flush=True)


def evaluate():
    target = REPORT / "test_results.json"
    if target.exists():
        print("Existing sealed test report (no refit/no threshold changes):", read_json(target), flush=True)
        return
    selection = read_json(REPORT / "selection.json")
    assert sha256(REPORT / "models.joblib") == selection["model_sha256"]
    assert sha256(DATA / "manifest.json") == selection["manifest_sha256"]
    assert sha256(Path(__file__).with_name("models.py")) == selection["models_source_sha256"]
    assert sha256(Path(__file__).with_name("persistent.py")) == selection["persistent_source_sha256"]
    package = joblib.load(REPORT / "models.joblib")
    predictions, records = {}, {}
    for method, model in package["models"].items():
        base = "seasonal_naive" if method == "seasonal_naive" else "stl_window"
        residual, inputs = residual_bank("test", base)
        components = component_bank(residual, inputs, model.setting)
        scores = score_bank(model, components)
        path = REPORT / f"test_scores_{method}.npz"
        reasons = np.asarray([model.score_components(c)[1] for c in components], dtype=str)
        np.savez_compressed(path, score=scores, reason=reasons,
                            case_id=inputs["case_id"], threshold=model.threshold)
        predictions[method] = (scores, inputs["case_id"])
        records[method] = {"scores_sha256": sha256(path), "threshold": model.threshold}
    # Predictions and their hashes are recorded before the evaluator opens any
    # test labels, including task metadata. No threshold can be chosen here.
    write_json(REPORT / "prediction_seal.json", {"selection_sha256": sha256(REPORT / "selection.json"),
        "manifest_sha256": sha256(DATA / "manifest.json"), "predictions": records,
        "test_truth_opened": False})
    truth = load_split("test", with_truth=True)["y"]
    results = {k: summarize(truth, scores, package["models"][k].threshold, ids, "test")
               for k, (scores, ids) in predictions.items()}
    write_json(target, {"primary": package["primary"], "results": results,
        "median_over": "six scenario-level pointwise metrics; context and collection gaps excluded",
        "point_adjustment": False, "model_sha256": selection["model_sha256"],
        "selection_sha256": sha256(REPORT / "selection.json"),
        "prediction_seal_sha256": sha256(REPORT / "prediction_seal.json"),
        "benchmark_manifest_sha256": selection["manifest_sha256"],
        "test_used_for_tuning": False,
        "known_limitations": ["semi-synthetic trajectories, not real labeled failures",
                              "same generator family across partitions; no external-domain guarantee",
                              "offline hindsight, not causal real-time event detection",
                              "normal context is known by design, not automatically certified from S"]})
    rows = [{"method": method, "scenario": task, **r} for method, result in results.items()
            for task, r in result["scenarios"].items()]
    pd.DataFrame(rows).to_csv(REPORT / "test_metrics.csv", index=False, encoding="utf-8-sig")
    for method, result in results.items():
        print("TEST", method, "median", result["median"], "maxFPR", result["maximum_scenario_fpr"],
              "target_met", result["target_met"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["tune", "evaluate"])
    args = p.parse_args()
    tune() if args.stage == "tune" else evaluate()
