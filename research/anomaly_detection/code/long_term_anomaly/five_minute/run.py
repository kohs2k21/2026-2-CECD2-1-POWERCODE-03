"""Normal-only fitting, development selection, sealed original-hour test scoring."""
import argparse
from pathlib import Path
from itertools import product
import hashlib
import joblib
import numpy as np

from ..common import ROOT, REPORT, read_json, write_json, sha256
from ..intervals.model import HORIZONS
from .benchmark import load_split, DATA
from .model import FineWindowModel
from .bank import FineBank, load_bank, combine_evidence

OUT = REPORT / "five_minute"
MODEL_SOURCE = Path(__file__).with_name("model.py")


def runtime_hash():
    paths = [MODEL_SOURCE, Path(__file__).with_name("bank.py")]
    return hashlib.sha256("".join(sha256(p) for p in paths).encode()).hexdigest()


def metadata(split, ids):
    path = DATA / f"{split}_metadata.json"
    manifest = read_json(DATA / "manifest.json")
    if sha256(path) != manifest["files"][path.name]:
        raise ValueError("Frozen split metadata changed")
    records = read_json(path)
    if [r["case_id"] for r in records] != list(ids):
        raise ValueError("Metadata/input case ordering differs")
    return records


def extract(split):
    d = load_split(split, False)
    fingerprint = hashlib.sha256((runtime_hash() + sha256(DATA / f"{split}_inputs.npz")).encode()).hexdigest()[:16]
    path = OUT / "cache" / f"features_{split}_{fingerprint}.npz"
    if path.exists():
        with np.load(path, allow_pickle=False) as z:
            return {h: z[f"h{h}"] for h in HORIZONS}, d
    template = FineWindowModel()
    rows = {h: [] for h in HORIZONS}
    for i, (x, o, a) in enumerate(zip(d["X"], d["collection_observed"], d["latency_available"])):
        ref = template.prepare_reference(x[:8064], o[:8064], a[:8064])
        for h in HORIZONS:
            f = template.sequence_features(ref, x[8064:], o[8064:], a[8064:], analysis_hours=h)
            if np.asarray(f).shape != (336 // h, len(template.feature_names)):
                raise AssertionError("Feature shape differs from selected-window protocol")
            rows[h].append(f)
        if (i + 1) % 32 == 0:
            print("Native features", split, i + 1, "/", len(d["X"]), flush=True)
    rows = {h: np.asarray(f, float) for h, f in rows.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **{f"h{h}": f for h, f in rows.items()})
    return rows, d


def hour_scores(window_scores, d, h):
    scores = np.repeat(np.asarray(window_scores).reshape(len(d["X"]), 336 // h), h, axis=1)
    observed = d["collection_observed"][:, 8064:].reshape(-1, 336, 12).all(axis=2)
    return np.where(observed, scores, np.nan)


def metric(y, score, threshold):
    good = np.isfinite(score)
    t, p = y[good], score[good] > threshold
    tp, fp = int((t & p).sum()), int((~t & p).sum())
    fn, tn = int((t & ~p).sum()), int((~t & ~p).sum())
    precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"accuracy": (tp + tn) / max(tp + fp + fn + tn, 1), "precision": precision,
        "recall": recall, "f1": 2 * tp / max(2 * tp + fp + fn, 1),
        "false_positive_rate": fp / max(fp + tn, 1), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "assessed_hours": int(good.sum()), "unassessable_hours": int((~good).sum())}


def metrics(y, score, threshold, groups):
    tasks = {str(g): metric(y[groups == g], score[groups == g], threshold) for g in np.unique(groups)}
    med = {k: float(np.median([r[k] for r in tasks.values()])) for k in ("accuracy", "precision", "recall", "f1")}
    pooled = metric(y, score, threshold)
    max_fpr = max(r["false_positive_rate"] for r in tasks.values())
    return {"pooled": pooled, "scenarios": tasks, "median": med, "maximum_scenario_fpr": max_fpr,
            "goal_pass": min(med.values()) >= .65 and max_fpr <= .06 and pooled["false_positive_rate"] <= .06}


def candidate_weights(names):
    names = [str(n).lower() for n in names]
    # A small declared family grid, selected only with development labels. No
    # scenario or label is available inside these weights at inference time.
    seen = set()
    for variance, trend, lower, work in product((.25, 1., 2.), (.25, 1.), (.5, 1., 2.), (.25, 1.)):
        weights = []
        for name in names:
            value = 1.
            if any(s in name for s in ("variance", "variability", "energy", "std", "volatility")):
                value *= variance
            if any(s in name for s in ("trend", "slope", "early", "late")):
                value *= trend
            if any(s in name for s in ("lower", "down", "negative")):
                value *= lower
            if name.startswith("work:"):
                value *= work
            weights.append(value)
        key = tuple(weights)
        if key not in seen:
            seen.add(key)
            yield weights


def fit():
    if (OUT / "models.joblib").exists() or (OUT / "selected_configuration.json").exists():
        raise FileExistsError("Preserve frozen selection; do not refit against test results")
    OUT.mkdir(parents=True, exist_ok=True)
    train, _ = extract("train")
    calibration, cd = extract("calibration")
    development, dd = extract("development")
    y = load_split("development", True)["y"]
    groups = np.asarray([r["scenario"] for r in metadata("development", dd["case_id"])])
    models, settings, selected = {}, {}, {}
    for h in HORIZONS:
        model = FineWindowModel()
        model.fit(train[h].reshape(-1, train[h].shape[-1]))
        ce = model.normal_evidence(calibration[h].reshape(-1, calibration[h].shape[-1]))
        de = model.normal_evidence(development[h].reshape(-1, development[h].shape[-1]))
        best = None
        candidates = []
        for weights, temperature in product(candidate_weights(model.feature_names), (0., .5, 1.)):
            ds = hour_scores(combine_evidence(de, weights, temperature), dd, h)
            cs = hour_scores(combine_evidence(ce, weights, temperature), cd, h)
            # Four percent dev-normal budget leaves a two percentage-point
            # margin to the fixed six-percent held-out requirement.
            threshold = max(float(np.nanquantile(cs, .96, method="higher")),
                *(float(np.quantile(ds[(groups == g)[:, None] & ~y & np.isfinite(ds)], .96, method="higher"))
                  for g in np.unique(groups)))
            result = metrics(y, ds, threshold, groups)
            row = {"weights": weights, "temperature": temperature, "threshold": threshold,
                   "normal_tail_budget": .04, "development": result}
            candidates.append(row)
            rank = (min(result["median"].values()), result["median"]["f1"])
            if best is None or rank > best[0]:
                best = rank, row
        cfg = {k: best[1][k] for k in ("weights", "temperature", "threshold", "normal_tail_budget")}
        models[h], settings[h], selected[str(h)] = model, cfg, {"selected": best[1], "candidates": candidates}
        print("Selected native", h, "hours", best[1]["development"]["median"],
              "max FPR", best[1]["development"]["maximum_scenario_fpr"], flush=True)
    provenance = {"normal_only_density_fit": True, "development_labels_used_for_hyperparameters": True,
        "test_used_for_selection": False, "native_benchmark_manifest_sha256": sha256(DATA / "manifest.json"),
        "runtime_sha256": runtime_hash(), "old_hourly_test_modified": False}
    bank = FineBank(models, settings, provenance)
    write_json(OUT / "selected_configuration.json", {"provenance": provenance, "horizons": selected})
    joblib.dump(bank, OUT / "models.joblib", compress=3)
    write_json(OUT / "model_manifest.json", {"models_sha256": sha256(OUT / "models.joblib"),
        "selection_sha256": sha256(OUT / "selected_configuration.json"), **provenance})


def evaluate():
    if (OUT / "results.json").exists() or (OUT / "test_predictions.npz").exists():
        raise FileExistsError("Preserve existing test result; no test-driven selection")
    manifest = read_json(OUT / "model_manifest.json")
    if manifest["models_sha256"] != sha256(OUT / "models.joblib") or manifest["runtime_sha256"] != runtime_hash():
        raise ValueError("Frozen model/runtime changed")
    bank = load_bank(OUT / "models.joblib")
    features, d = extract("test")
    scores = {}
    for h in HORIZONS:
        f = features[h]
        e = bank.models[h].normal_evidence(f.reshape(-1, f.shape[-1]))
        cfg = bank.settings[h]
        scores[h] = hour_scores(combine_evidence(e, cfg["weights"], cfg["temperature"]), d, h)
    p = OUT / "test_predictions.npz"
    np.savez_compressed(p, case_id=d["case_id"], **{f"score_h{h}": s for h, s in scores.items()})
    write_json(OUT / "test_prediction_seal.json", {"predictions_sha256": sha256(p),
        "model_sha256": sha256(OUT / "models.joblib"), "runtime_sha256": runtime_hash(),
        "test_inputs_sha256": sha256(DATA / "test_inputs.npz"), "predictions_frozen_before_opening_test_truth": True})
    y = load_split("test", True)["y"]
    groups = np.asarray([r["scenario"] for r in metadata("test", d["case_id"])])
    results = {str(h): metrics(y, scores[h], bank.settings[h]["threshold"], groups) for h in HORIZONS}
    output = {"results": results, "all_horizons_goal_pass": all(r["goal_pass"] for r in results.values()),
        "evaluation_unit": "original one-hour positions, no expanded labels or point adjustment",
        "native_five_minute_population_not_the_old_hourly_test": True,
        "independent_real_fault_validation": False, "old_hourly_results_preserved": True,
        "model_bytes": (OUT / "models.joblib").stat().st_size}
    write_json(OUT / "results.json", output)
    for h, result in results.items():
        print("Held-out native", h, result["median"], "max FPR", result["maximum_scenario_fpr"], "pass", result["goal_pass"], flush=True)
    return output


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("fit", "evaluate"))
    args = p.parse_args()
    fit() if args.command == "fit" else evaluate()
