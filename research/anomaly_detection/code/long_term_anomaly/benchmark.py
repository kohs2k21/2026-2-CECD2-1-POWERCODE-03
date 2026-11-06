"""Frozen, custom semi-synthetic long-series benchmark, NOT official ADBench.

The real exports supply centers, scales, within-hour workload/duration patterns
and short-lag correlations. Continuous six-week trajectories are simulations,
not observed production histories. Source test-period values are never used.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.signal import lfilter
from .common import ROOT, REPORT, DATA, read_json, write_json, sha256

PROTOCOL = Path(__file__).with_name("protocol.json")
CHANNELS = ("log_requests", "log_duration_ms", "log_items_per_process")


def source_distribution(config):
    audit = read_json(REPORT / "source_audit.json")
    hourly = REPORT / "observed_hourly.csv.gz"
    assert sha256(hourly) == audit["hourly_sha256"]
    f = pd.read_csv(hourly, parse_dates=["hour"])
    first = min(f.source_period.unique())
    f = f.loc[f.source_period.eq(first) & f.normal_rows.ge(5)].copy()
    lower, upper = f.hour.min(), f.hour.max()
    cutoff = lower + (upper - lower) * config["source_generator_fit_fraction"]
    f = f.loc[f.hour.lt(cutoff)].copy()
    f["log_requests"] = np.log1p(f.normal_rows)
    f["log_duration_ms"] = f.log_duration
    f["log_items_per_process"] = f.log_work
    profiles, residuals, rows = [], [], []
    for (adapter, process), g in f.groupby(["adapterType", "processId"], observed=True):
        g = g.sort_values("hour")
        if len(g) < 48:
            continue
        x = g[list(CHANNELS)].to_numpy(float)
        center = np.median(x, axis=0)
        scale = np.maximum(np.median(abs(x - center), axis=0) * 1.4826, [.12, .12, .08])
        z = (x - center) / scale
        profile = np.zeros((24, 3))
        for hour in range(24):
            a = z[g.hour.dt.hour.eq(hour)]
            if len(a):
                profile[hour] = np.clip(np.median(a, axis=0), -1.5, 1.5) * len(a) / (len(a) + 8)
        r = z - profile[g.hour.dt.hour.to_numpy()]
        r -= np.median(r, axis=0)
        # Robustify normal candidates, without claiming that all S logs are clean.
        r = np.clip(r, np.quantile(r, .01, axis=0), np.quantile(r, .99, axis=0))
        pairs = g.hour.diff().eq(pd.Timedelta(hours=1)).to_numpy()[1:]
        rho = []
        for channel in range(3):
            if pairs.sum() >= 20 and np.std(r[:-1, channel][pairs]) > 1e-6 and np.std(r[1:, channel][pairs]) > 1e-6:
                value = np.corrcoef(r[:-1, channel][pairs], r[1:, channel][pairs])[0, 1]
            else:
                value = 0.0
            rho.append(float(np.clip(np.nan_to_num(value), *config["normal_ar1_clip"])))
        residuals.append(r)
        profiles.append({"adapterType": str(adapter), "processId": str(process),
                         "center": center.tolist(), "scale": scale.tolist(),
                         "hour_profile": profile.tolist(), "rho": rho,
                         "fit_hours": len(g), "adjacent_hour_pairs": int(pairs.sum())})
        rows.extend(g.index.tolist())
    if not profiles:
        raise ValueError("Not enough source support for a semi-synthetic benchmark")
    pool = np.concatenate(residuals)
    # A shared innovation pool preserves empirical cross-channel associations;
    # marginal center/scale are normalized using source fit rows only.
    pool -= np.mean(pool, axis=0)
    sd = np.std(pool, axis=0)
    pool /= np.maximum(sd, .25)
    pool = np.clip(pool, -4, 4)
    pool /= np.maximum(np.std(pool, axis=0), .25)
    metadata = {"source_period": first, "fit_start": str(lower), "cutoff_exclusive": str(cutoff),
                "fit_hourly_group_rows": len(rows), "reference_groups": len(profiles),
                "hourly_source_sha256": sha256(hourly),
                "source_rows_after_cutoff_used": False,
                "profile_estimation": "observed hours only; shrunk median by hour of day; not proof of daily seasonality",
                "innovation_estimation": "P1-P99 winsorized group residuals; joint empirical bootstrap; clipped at 4 sigma",
                "continuity": "simulated AR(1), estimated only from observed adjacent-hour pairs",
                "source_weekly_or_monthly_seasonality_claimed": False}
    return profiles, pool, metadata


def simulate_normal(rng, profile, pool, config, length, phase):
    channels = len(CHANNELS)
    rho = np.asarray(profile["rho"])
    innovations = pool[rng.integers(len(pool), size=length + 100)]
    noise = np.column_stack([
        lfilter([np.sqrt(1 - rho[j] ** 2)], [1, -rho[j]], innovations[:, j])[-length:]
        for j in range(channels)])
    t = np.arange(length)
    day_noise = rng.normal(0, config["normal_daily_random_walk_sigma"], (length // 24 + 2, channels)).cumsum(axis=0)
    daily_drift = np.column_stack([np.interp(t / 24, np.arange(len(day_noise)), day_noise[:, j]) for j in range(channels)])
    slope = rng.uniform(-config["normal_six_week_slope_max_sigma"],
                        config["normal_six_week_slope_max_sigma"], channels)
    short = rng.random((length, channels)) < config["normal_short_spike_probability"]
    noise += short * rng.normal(0, config["normal_short_spike_sigma"], (length, channels))
    # Benign temporary deviations force the detector to distinguish persistence
    # from normal small fluctuations, rather than receiving stationary toy data.
    start = int(rng.integers(48, length - 120))
    duration = int(rng.integers(12, 49))
    noise[start:start + duration] += rng.uniform(*config["normal_temporary_shift_sigma"], channels) * rng.choice([-1, 1], channels)
    seasonal = np.asarray(profile["hour_profile"])[(t + phase) % 24]
    z = noise + daily_drift + (t[:, None] / length) * slope + seasonal
    return z


def inject(z, rng, task, cfg, context, horizon):
    spec = cfg["scenario_definitions"][task]
    lo, hi = spec["duration_hours"]
    duration = int(rng.integers(lo, hi + 1))
    start = context + int(rng.integers(24, horizon - duration - 12))
    end = start + duration
    amount = float(rng.uniform(*spec["magnitude_sigma"]))
    channel = spec["channel"]
    t = np.arange(duration)
    if task == "gradual_degradation":
        change = amount * (t + 1) / duration
        z[start:end, channel] += change
    elif task == "daily_pattern_change":
        # Whole altered-cycle interval is positive, including its weak/zero
        # crossings. No post-hoc exclusion of difficult event hours.
        z[start:end, channel] += amount * np.sin(2 * np.pi * t / 24 + rng.uniform(0, 2 * np.pi))
    elif task == "volatility_growth":
        z[start:end, channel] += rng.normal(0, np.sqrt(amount ** 2 - 1), duration)
    else:
        z[start:end, channel] += amount * (-1 if task == "sustained_input_drop" else 1)
    return {"event_start_hour": start - context, "event_end_hour": end - context,
            "magnitude_sigma": amount, "changed_channel": CHANNELS[channel]}


def build():
    if (DATA / "manifest.json").exists():
        raise FileExistsError("Benchmark is frozen. Do not overwrite it to change measured performance.")
    cfg = read_json(PROTOCOL)
    DATA.mkdir(parents=True, exist_ok=True)
    profiles, pool, provenance = source_distribution(cfg)
    write_json(DATA / "frozen_protocol.json", cfg)
    write_json(DATA / "source_distribution.json", {**provenance, "profiles": profiles})
    np.savez_compressed(DATA / "source_innovations.npz", innovations=pool)
    context, horizon = cfg["context_hours"], cfg["monitor_hours"]
    length = context + horizon
    tasks = list(cfg["scenario_definitions"])
    files, all_meta = {}, []
    for split_index, split in enumerate(("train", "calibration", "development", "test")):
        records = []
        if split in ("train", "calibration"):
            n = cfg["train_normal_cases" if split == "train" else "calibration_normal_cases"]
            records = [("normal", False, i) for i in range(n)]
        else:
            n = cfg["development_pairs_per_scenario" if split == "development" else "test_pairs_per_scenario"]
            records = [(task, anomaly, i) for task in tasks for i in range(n) for anomaly in (False, True)]
        arrays, masks, latency, truth, ids, starts, csv_parts = [], [], [], [], [], [], []
        for case_index, (task, anomaly, pair) in enumerate(records):
            # Paired cases share the normal trajectory. Whole pairs stay in a
            # single split; they never cross fit/calibration/development/test.
            pair_seed = cfg["seed"] + split_index * 1_000_000 + tasks.index(task) * 10000 + pair if task != "normal" else cfg["seed"] + split_index * 1_000_000 + pair
            rng = np.random.default_rng(pair_seed)
            profile = profiles[int(rng.integers(len(profiles)))]
            phase = int(rng.integers(24))
            z = simulate_normal(rng, profile, pool, cfg, length, phase)
            event = None
            injection_rng = np.random.default_rng(pair_seed + 900_000_000)
            if anomaly:
                event = inject(z, injection_rng, task, cfg, context, horizon)
            x = np.asarray(profile["center"]) + z * np.asarray(profile["scale"])
            x = np.maximum(x, 0)
            requests = np.maximum(np.rint(np.expm1(np.minimum(x[:, 0], 20))), 0)
            x[:, 0] = np.log1p(requests)
            available = requests > 0
            x[~available, 1:] = np.nan
            observed = np.ones(length, bool)
            # Known collector outages are missing, not successful zero requests.
            gap_rng = np.random.default_rng(pair_seed + 700_000_000)
            for _ in range(2):
                a = int(gap_rng.integers(24, length - 8))
                observed[a:a + int(gap_rng.integers(2, 6))] = False
            x[~observed] = np.nan
            available &= observed
            y = np.zeros(horizon, bool)
            if event:
                y[event["event_start_hour"]:event["event_end_hour"]] = True
            case_id = f"LT-{split}-{case_index:04d}"
            # Simulated clock, not a claim about the raw export's dates.
            start = pd.Timestamp("2030-01-01") + pd.Timedelta(hours=phase)
            arrays.append(x.astype(np.float32)); masks.append(observed); latency.append(available)
            truth.append(y); ids.append(case_id); starts.append(start.value)
            all_meta.append({"split": split, "case_id": case_id, "scenario": task,
                             "injected": anomaly, "pair_seed": pair_seed,
                             "adapterType": profile["adapterType"], "processId": profile["processId"],
                             **(event or {})})
            frame = pd.DataFrame(x, columns=CHANNELS)
            frame.insert(0, "timestamp", pd.date_range(start, periods=length, freq="h"))
            frame.insert(0, "case_id", case_id)
            frame["collection_observed"] = observed
            frame["latency_available"] = available
            csv_parts.append(frame)
        # Model input files contain no scenario, pair, severity, label or event bounds.
        p = DATA / f"{split}_inputs.npz"
        np.savez_compressed(p, X=np.asarray(arrays), collection_observed=np.asarray(masks),
                            latency_available=np.asarray(latency), case_id=np.asarray(ids),
                            start_ns=np.asarray(starts), channels=np.asarray(CHANNELS))
        pd.concat(csv_parts, ignore_index=True).to_csv(DATA / f"{split}_hourly.csv.gz", index=False,
                                                     compression="gzip", float_format="%.7g")
        truth_path = DATA / f"{split}_truth.npz"
        np.savez_compressed(truth_path, y=np.asarray(truth), case_id=np.asarray(ids))
        files[p.name] = sha256(p)
        files[truth_path.name] = sha256(truth_path)
        files[f"{split}_hourly.csv.gz"] = sha256(DATA / f"{split}_hourly.csv.gz")
        print("Generated", split, len(ids), "cases", len(ids) * length, "hour rows", flush=True)
    pd.DataFrame(all_meta).to_csv(DATA / "case_metadata.csv", index=False, encoding="utf-8-sig")
    for name in ("frozen_protocol.json", "source_distribution.json", "source_innovations.npz", "case_metadata.csv"):
        files[name] = sha256(DATA / name)
    write_json(DATA / "manifest.json", {"protocol_sha256": sha256(PROTOCOL),
        "generator_sha256": sha256(Path(__file__)), "source": provenance, "files": files,
        "custom_semi_synthetic": True, "official_ADBench": False,
        "generator_shared_across_splits": True, "independent_real_fault_validation": False,
        "pairs_never_cross_splits": True, "normal_context_known_by_design": True,
        "no_scores_seen_before_freeze": True, "raw_period_files_modified": False})


def load_split(split, with_truth=False):
    manifest = read_json(DATA / "manifest.json")
    p = DATA / f"{split}_inputs.npz"
    if sha256(p) != manifest["files"][p.name]:
        raise ValueError("Frozen input hash differs")
    with np.load(p, allow_pickle=False) as f:
        result = {k: f[k] for k in f.files}
    if with_truth:
        p = DATA / f"{split}_truth.npz"
        if sha256(p) != manifest["files"][p.name]:
            raise ValueError("Frozen truth hash differs")
        with np.load(p, allow_pickle=False) as f:
            assert np.array_equal(f["case_id"], result["case_id"])
            result["y"] = f["y"]
    return result


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
