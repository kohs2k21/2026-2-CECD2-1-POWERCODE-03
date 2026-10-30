"""Development-only asymmetric normal tails; no other monitoring batch input.

The old model takes abs(residual), incorrectly imposing symmetric tails on a
strongly left-skewed normal distribution. This experiment estimates normal
upper and lower tails separately. No anomaly truth enters feature generation.
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from ..common import DATA, REPORT, ROOT, read_json, write_json, sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split, summarize
from ..models import Setting, forecast_context, fill_short_gaps

OUT = REPORT / "strict_intervals"
NAMES = ("upper_requests", "lower_requests", "upper_duration", "lower_duration",
         "density_requests", "density_duration")


def initialize():
    OUT.mkdir(exist_ok=True)
    (OUT / "cache").mkdir(exist_ok=True)
    path = OUT / "preservation.json"
    if path.exists():
        for name, h in read_json(path)["files"].items():
            assert sha256(ROOT / name) == h, name
        return
    files = [p for folder in (ROOT / "code/long_term_anomaly/intervals", REPORT / "intervals")
             for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts and "cache" not in p.parts]
    previous = read_json(REPORT / "intervals/preservation.json")["files"]
    previous.update({p.relative_to(ROOT).as_posix(): sha256(p) for p in files})
    previous.update({f"data/evaluation_long_term/{name}": h
                     for name, h in read_json(DATA / "manifest.json")["files"].items()})
    write_json(path, {"files": previous})
    write_json(OUT / "protocol.json", read_json(Path(__file__).with_name("protocol.json")))


def reference_distributions(context, observed, available, method="stl"):
    context = np.asarray(context, float).copy()
    context[~observed] = np.nan
    context[~available, 1:] = np.nan
    context = fill_short_gaps(context)
    if method == "stl":
        forecast, scale, info = forecast_context(context, 24, Setting(workload_conditioning=False))
        fit = forecast[np.arange(len(context)) % 24]
    else:
        hours = np.arange(len(context)) % 24
        # Robust phase-conditional location; reference only, no batch refit.
        forecast = np.asarray([np.median(context[hours == h], axis=0) for h in range(24)])
        fit = forecast[hours]
    return forecast, context - fit


def point_features(reference_r, monitor_r, observed, available, quantile=.95, k=8):
    columns = []
    for j in range(2):
        ref = np.sort(reference_r[:, j])
        x = monitor_r[:, j]
        lo, med, hi = np.quantile(ref, [1 - quantile, .5, quantile])
        floor = max(float(np.std(ref)) * .02, 1e-6)
        columns += [(x - med) / max(hi - med, floor), (med - x) / max(med - lo, floor)]
    # Normal density is unrelated to the label; nearest normal-neighbour gaps
    # can also expose values inside a broad but sparsely occupied tail.
    for j in range(2):
        ref = np.sort(reference_r[:, j])
        distances = abs(monitor_r[:, j, None] - ref[None, :])
        kth = np.partition(distances, k - 1, axis=1)[:, k - 1]
        ref_dist = abs(ref[:, None] - ref[None, :])
        scale = max(float(np.median(np.partition(ref_dist, k, axis=1)[:, k])),
                    float(np.std(ref)) * .01, 1e-6)
        columns.append(kth / scale)
    f = np.column_stack(columns)
    f[np.ix_(~available, [2, 3, 5])] = 0.0
    f[~observed] = np.nan
    return f


def worker(payload):
    x, observed, available, method, q = payload
    forecast, ref = reference_distributions(x[:672], observed[:672], available[:672], method)
    r = x[672:] - forecast[np.arange(len(x) - 672) % 24]
    return point_features(ref, r, observed[672:], available[672:], q)


def bank(split, method, quantile):
    d = load_split(split, False)
    signature = hashlib.sha256((sha256(Path(__file__)) + sha256(DATA / f"{split}_inputs.npz")).encode()).hexdigest()[:16]
    path = OUT / "cache" / f"points_{split}_{method}_{quantile}_{signature}.npz"
    if path.exists():
        with np.load(path) as f:
            return f["features"], d
    args = ((x, o, a, method, quantile) for x, o, a in zip(d["X"], d["collection_observed"], d["latency_available"]))
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        features = np.asarray(list(pool.map(worker, args, chunksize=8)))
    np.savez_compressed(path, features=features)
    print("Reference features", split, method, quantile, features.shape, flush=True)
    return features, d


def threshold_for(score, y, meta, cap=.05):
    return max(float(np.quantile(score[meta.scenario.eq(task).to_numpy()[:, None] & ~y & np.isfinite(score)],
                                 1 - cap, method="higher")) for task in meta.scenario.unique())


def explore():
    initialize()
    y = load_split("development", True)["y"]
    rows, records = [], []
    for method in ("stl", "phase_median"):
        for q in (.9, .95, .99):
            features, d = bank("development", method, q)
            meta = metadata_for_split("development", d["case_id"])
            for density in (False, True):
                for lower_weight in (.25, .5, 1., 2.):
                    weights = np.asarray([1., lower_weight, 1., lower_weight, .0, .0])
                    if density:
                        weights = np.asarray([0., 0., 0., 0., 1., 1.])
                    score = np.nanmax(features * weights, axis=2)
                    score[~np.isfinite(features).any(axis=2)] = np.nan
                    threshold = threshold_for(score, y, meta)
                    result = summarize(y, score, threshold, d["case_id"], "development")
                    row = {"method": method, "quantile": q, "density": density,
                           "lower_weight": lower_weight, "threshold": threshold,
                           **result["median"], "max_fpr": result["maximum_scenario_fpr"]}
                    rows.append(row); records.append({"setting": row, "development": result})
                    if density: break
            print("Point candidates", method, q, sorted(rows[-5:], key=lambda r:r['f1'])[-1], flush=True)
    pd.DataFrame(rows).to_csv(OUT / "point_development_search.csv", index=False)
    write_json(OUT / "point_development_search.json", records)
    print(pd.DataFrame(rows).sort_values("f1", ascending=False).head(12).to_string(index=False), flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    explore()
