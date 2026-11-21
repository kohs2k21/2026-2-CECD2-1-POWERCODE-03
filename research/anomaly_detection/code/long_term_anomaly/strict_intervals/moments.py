"""Length-conditional normal-reference summaries; monitoring batches isolated.

Normal reference is allowed to contain many historic normal windows. Monitoring
summaries never see an earlier/later monitoring window or a scenario identifier.
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.special import logsumexp
from ..common import DATA, ROOT, REPORT, read_json, write_json, sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split, metrics
from .explore import initialize, reference_distributions

OUT = REPORT / "strict_intervals"
HORIZONS = (1, 4, 8, 12, 24)
STAT_NAMES = ("mean", "median", "std")
NAMES = tuple(f"{stat}_{direction}:{channel}" for stat in STAT_NAMES
              for channel in ("requests", "duration") for direction in ("upper", "lower", "density"))


def summaries(x):
    # [windows, hours, channels] -> [windows, stat, channels]
    return np.stack([np.nanmean(x, axis=1), np.nanmedian(x, axis=1), np.nanstd(x, axis=1)], axis=1)


def density_distance(reference, values, k=8):
    ref = np.sort(reference)
    k = min(k, len(ref) - 1)
    ref_distances = abs(ref[:, None] - ref[None, :])
    ref_radius = np.partition(ref_distances, k, axis=1)[:, k]
    distances = abs(values[:, None] - ref[None, :])
    radius = np.partition(distances, k - 1, axis=1)[:, k - 1]
    # A normal-tail radius, not its median: per-group density tails differ.
    scale = max(float(np.quantile(ref_radius, .95)), float(np.std(ref)) * .01, 1e-6)
    return radius / scale


def kde_distance(reference, values, bandwidth=.2):
    ref = np.asarray(reference)
    sd = max(float(np.std(ref)), 1e-6)
    bw = bandwidth * sd
    def log_density(a, leave_one_out=False):
        z = -.5 * ((a[:, None] - ref[None, :]) / bw) ** 2
        if leave_one_out: np.fill_diagonal(z, -np.inf)
        return logsumexp(z, axis=1) - np.log(len(ref) - int(leave_one_out))
    normal_nll = -log_density(ref, True)
    nll = -log_density(values)
    med, hi = np.quantile(normal_nll, [.5, .95])
    return (nll - med) / max(hi - med, .05)


def features_from_reference(reference, current, quantile=.95, density="knn"):
    n = len(current)
    out = np.empty((n, len(NAMES)))
    for s in range(3):
        for ch in range(2):
            ref, x = reference[:, s, ch], current[:, s, ch]
            ref = ref[np.isfinite(ref)]
            if len(ref) < 48:
                raise ValueError("Not enough normal reference windows")
            lo, med, hi = np.quantile(ref, [1 - quantile, .5, quantile])
            floor = max(float(np.std(ref)) * .025, 1e-6)
            start = (s * 2 + ch) * 3
            out[:, start] = (x - med) / max(hi - med, floor)
            out[:, start + 1] = (med - x) / max(med - lo, floor)
            out[:, start + 2] = (density_distance(ref, x) if density == "knn"
                                  else kde_distance(ref, x, float(density)))
    return out


def per_case(x, observed, available, method="phase_median", quantile=.95, density="knn"):
    forecast, context_r = reference_distributions(x[:672], observed[:672], available[:672], method)
    monitor = x[672:, :2] - forecast[np.arange(len(x) - 672) % 24, :2]
    context_r = context_r[:, :2]
    outputs = {}
    for h in HORIZONS:
        windows = sliding_window_view(context_r, h, axis=0).transpose(0, 2, 1)
        masks = sliding_window_view(observed[:672] & available[:672], h).all(axis=1)
        normal = summaries(windows[masks])
        batches = monitor.reshape(-1, h, 2).copy()
        obs = observed[672:].reshape(-1, h)
        lat = available[672:].reshape(-1, h)
        batches[~obs] = np.nan
        batches[:, :, 1][~lat] = np.nan
        with np.errstate(all="ignore"):
            current = summaries(batches)
        # No latency at zero requests is not latency=0. Missing latency cannot
        # become an alert solely from the absent duration channel.
        no_latency = ~lat.any(axis=1)
        current[no_latency, :, 1] = np.median(normal[:, :, 1], axis=0)
        f = features_from_reference(normal, current, quantile, density)
        no_observation = ~obs.any(axis=1)
        f[no_observation] = np.nan
        expanded = np.repeat(f, h, axis=0)
        expanded[~observed[672:]] = np.nan
        outputs[h] = expanded
    return outputs


def worker(payload):
    x, o, a, method, q, density = payload
    return per_case(x, o, a, method, q, density)


def bank(split, method="phase_median", quantile=.95, density="knn"):
    d = load_split(split)
    sig = hashlib.sha256((sha256(Path(__file__)) + sha256(Path(__file__).with_name("explore.py"))
                         + sha256(DATA / f"{split}_inputs.npz")).encode()).hexdigest()[:16]
    path = OUT / "cache" / f"moments_{split}_{method}_{quantile}_{density}_{sig}.npz"
    if path.exists():
        with np.load(path) as f: return {h:f[str(h)] for h in HORIZONS}, d
    payloads = ((x, o, a, method, quantile, density) for x, o, a in
                zip(d["X"], d["collection_observed"], d["latency_available"]))
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as p:
        values = list(p.map(worker, payloads, chunksize=4))
    values = {h:np.asarray([v[h] for v in values]) for h in HORIZONS}
    np.savez_compressed(path, **{str(h):v for h,v in values.items()})
    print("Moment features", split, method, quantile, density, flush=True)
    return values, d


def compact_metrics(y, score, threshold, groups):
    result = {"scenarios":{}}
    for task in np.unique(groups):
        mask = (groups == task)[:, None] & np.isfinite(score)
        truth, pred = y[mask], score[mask] > threshold
        tp = np.count_nonzero(truth & pred); fp = np.count_nonzero(~truth & pred)
        fn = np.count_nonzero(truth & ~pred); tn = np.count_nonzero(~truth & ~pred)
        p = tp / max(tp + fp, 1); r = tp / max(tp + fn, 1)
        result["scenarios"][str(task)] = dict(accuracy=(tp+tn)/max(tp+tn+fp+fn,1),
            precision=p, recall=r, f1=2*p*r/max(p+r,1e-15), false_positive_rate=fp/max(fp+tn,1))
    result["median"] = {k:float(np.median([v[k] for v in result["scenarios"].values()]))
                        for k in ("accuracy","precision","recall","f1")}
    result["maximum_scenario_fpr"] = max(v['false_positive_rate'] for v in result['scenarios'].values())
    return result


def explore():
    initialize()
    d = load_split("development", True)
    groups = metadata_for_split("development",d['case_id']).scenario.to_numpy()
    y = d["y"]
    records = []
    for density in ("knn", .1, .25, .5):
        features, _ = bank("development", density=density)
        for h in HORIZONS:
            f = features[h]
            for stat in ("mean", "median", "both"):
                for lower in (.25,.5,1.):
                    for energy in (0., .5, 1.):
                        for den in (0., 1.):
                            weights = []
                            for name in NAMES:
                                w = 1. if ((stat in ("mean","both") and name.startswith("mean"))
                                           or (stat in ("median","both") and name.startswith("median"))) else 0.
                                if name.startswith("std"): w = energy
                                if "lower:" in name: w *= lower
                                if "density:" in name: w *= den
                                weights.append(w)
                            valid = np.isfinite(f).any(axis=2)
                            score = np.max(np.nan_to_num(f,nan=-np.inf)*np.asarray(weights),axis=2)
                            score[~valid] = np.nan
                            threshold = max(np.quantile(score[(groups==g)[:,None] & ~y & valid], .95, method="higher")
                                            for g in np.unique(groups))
                            r = compact_metrics(y,score,threshold,groups)
                            records.append(dict(hours=h,density=density,stat=stat,lower_weight=lower,
                                energy_weight=energy,density_weight=den,threshold=float(threshold),
                                **r['median'],max_fpr=r['maximum_scenario_fpr']))
            subset=[r for r in records if r['hours']==h and r['density']==density]
            best=max(subset,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
            print("Best minimum median",best,flush=True)
        pd.DataFrame(records).to_csv(OUT / "moment_development_search.csv",index=False)
    write_json(OUT / "moment_development_search.json",records)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    explore()
