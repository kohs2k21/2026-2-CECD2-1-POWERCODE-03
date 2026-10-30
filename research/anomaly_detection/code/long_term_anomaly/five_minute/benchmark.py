"""Separate native-resolution semi-synthetic benchmark, not interpolated hourly data.

Actual five-minute Process observations supply marginal distributions, clock
profiles and short-lag innovations. Multi-week continuity remains simulated.
Original six fault definitions, hour-level labels and split sizes are retained.
This is NOT a recovered version of the old hourly test nor official ADBench.
"""
from pathlib import Path
import argparse
import copy
import numpy as np
import pandas as pd
from scipy.signal import lfilter

from ..common import ROOT, REPORT, read_json, write_json, sha256

DATA = ROOT / "data/evaluation_long_term_five_minute"
AUDIT = REPORT / "raw_windows"
OLD = ROOT / "data/evaluation_long_term"
CHANNELS = ("log_requests", "log_duration_ms", "log_items_per_process")
STEPS = 12


def estimate_source():
    audit = read_json(AUDIT / "source_audit.json")
    path = AUDIT / "observed_five_minute.npz"
    if sha256(path) != audit["intermediate_sha256"]:
        raise ValueError("Native source intermediate changed")
    with np.load(path, allow_pickle=False) as z:
        f = pd.DataFrame({k: z[k] for k in z.files})
    f["timestamp"] = pd.to_datetime(f.timestamp_ns)
    f = f.loc[f.source_period.eq(audit["normal_generator_fit_source_period"])
              & f.timestamp.lt(pd.Timestamp(audit["normal_generator_fit_cutoff_exclusive"]))
              & f.normal_rows.gt(0)].copy()
    old_source = read_json(OLD / "source_distribution.json")
    keys = {(p["adapterType"], p["processId"]) for p in old_source["profiles"]}
    profiles, innovations = [], []
    for (adapter, process), g in f.groupby(["adapterType", "processId"], observed=True):
        if (adapter, process) not in keys:
            continue
        g = g.sort_values("timestamp")
        if len(g) < 48:
            continue
        x = np.column_stack((np.log1p(g.normal_rows), g.log_duration_sum / g.normal_rows,
                             g.log_work_sum / g.normal_rows))
        center = np.median(x, axis=0)
        scale = np.maximum(np.median(abs(x - center), axis=0) * 1.4826, [.12, .12, .08])
        z = (x - center) / scale
        phase = (g.timestamp.dt.hour * 12 + g.timestamp.dt.minute // 5).to_numpy()
        profile = np.zeros((288, 3))
        for p in np.unique(phase):
            a = z[phase == p]
            profile[p] = np.clip(np.median(a, axis=0), -1.5, 1.5) * len(a) / (len(a) + 8)
        r = z - profile[phase]
        r -= np.median(r, axis=0)
        r = np.clip(r, np.quantile(r, .01, axis=0), np.quantile(r, .99, axis=0))
        adjacent = np.diff(g.timestamp_ns) == pd.Timedelta(minutes=5).value
        rho = []
        for ch in range(3):
            a, b = r[:-1, ch][adjacent], r[1:, ch][adjacent]
            corr = np.corrcoef(a, b)[0, 1] if len(a) >= 20 and np.std(a) > 1e-8 and np.std(b) > 1e-8 else 0.
            rho.append(float(np.clip(np.nan_to_num(corr), 0, .85)))
        rho = np.asarray(rho)
        # Derive innovations from actual adjacent bins. Never bridge an export
        # gap and never reconstruct unknown within-hour observations.
        if adjacent.sum() >= 20:
            noise = (r[1:][adjacent] - rho * r[:-1][adjacent]) / np.sqrt(1 - rho ** 2)
        else:
            noise = r.copy()
        innovations.append(noise)
        profiles.append({"adapterType": str(adapter), "processId": str(process),
            "center": center.tolist(), "scale": scale.tolist(), "clock_profile": profile.tolist(),
            "rho": rho.tolist(), "fit_occupied_bins": int(len(g)), "adjacent_pairs": int(adjacent.sum())})
    if not profiles:
        raise ValueError("No native-resolution normal-candidate support")
    pool = np.concatenate(innovations)
    pool -= np.mean(pool, axis=0)
    pool /= np.maximum(np.std(pool, axis=0), .25)
    pool = np.clip(pool, -4, 4)
    pool /= np.maximum(np.std(pool, axis=0), .25)
    return profiles, pool, {"native_intermediate_sha256": sha256(path),
        "source_period": audit["normal_generator_fit_source_period"],
        "fit_cutoff_exclusive": audit["normal_generator_fit_cutoff_exclusive"],
        "raw_after_original_fit_cutoff_used": False, "actual_native_bins_not_hourly_interpolation": True,
        "generator_group_support": len(profiles), "original_generator_groups": len(keys),
        "normal_candidates_not_verified_normal": True, "collection_health_known_in_simulation_only": True,
        "continuous_long_history_is_simulated": True,
        "fine_scale_changes_normal_variance_and_not_directly_comparable_to_old_test": True}


def simulate(rng, profile, pool, cfg, phase):
    n = (cfg["context_hours"] + cfg["monitor_hours"]) * STEPS
    rho = np.asarray(profile["rho"])
    noise = pool[rng.integers(len(pool), size=n + 1000)]
    noise = np.column_stack([lfilter([np.sqrt(1 - rho[ch] ** 2)], [1, -rho[ch]], noise[:, ch])[-n:]
                             for ch in range(3)])
    t = np.arange(n)
    days = rng.normal(0, cfg["normal_daily_random_walk_sigma"], (n // 288 + 2, 3)).cumsum(axis=0)
    daily = np.column_stack([np.interp(t / 288, np.arange(len(days)), days[:, ch]) for ch in range(3)])
    slope = rng.uniform(-cfg["normal_six_week_slope_max_sigma"], cfg["normal_six_week_slope_max_sigma"], 3)
    short = rng.random((n, 3)) < cfg["normal_short_spike_probability"] / STEPS
    noise += short * rng.normal(0, cfg["normal_short_spike_sigma"], (n, 3))
    start = int(rng.integers(48 * STEPS, n - 120 * STEPS))
    duration = int(rng.integers(12, 49)) * STEPS
    noise[start:start + duration] += rng.uniform(*cfg["normal_temporary_shift_sigma"], 3) * rng.choice([-1, 1], 3)
    return noise + daily + t[:, None] / n * slope + np.asarray(profile["clock_profile"])[(t + phase) % 288]


def inject(z, rng, task, cfg):
    spec = cfg["scenario_definitions"][task]
    duration = int(rng.integers(spec["duration_hours"][0], spec["duration_hours"][1] + 1))
    start = int(rng.integers(24, cfg["monitor_hours"] - duration - 12))
    amount = float(rng.uniform(*spec["magnitude_sigma"]))
    a = (cfg["context_hours"] + start) * STEPS
    b = a + duration * STEPS
    t = np.arange(duration * STEPS) / STEPS
    if task == "gradual_degradation":
        change = amount * (t + 1 / STEPS) / duration
    elif task == "daily_pattern_change":
        change = amount * np.sin(2 * np.pi * t / 24 + rng.uniform(0, 2 * np.pi))
    elif task == "volatility_growth":
        change = rng.normal(0, np.sqrt(amount ** 2 - 1), len(t))
    else:
        change = amount * (-1 if task == "sustained_input_drop" else 1)
    z[a:b, spec["channel"]] += change
    return {"event_start_hour": start, "event_end_hour": start + duration,
            "magnitude_sigma_native_five_minute_scale": amount, "changed_channel": CHANNELS[spec["channel"]]}


def build():
    if DATA.exists():
        raise FileExistsError("Preserve native benchmark; do not overwrite to improve measured scores")
    cfg = copy.deepcopy(read_json(OLD / "frozen_protocol.json"))
    cfg.update(schema_version=2, seed=20261108, internal_bin_minutes=5,
        task="selected-window retrospective native-five-minute anomaly detection",
        historical_evaluation_preserved=True, official_ADBench=False)
    cfg["evaluation"]["median_minimum"] = .65
    profiles, pool, provenance = estimate_source()
    DATA.mkdir(parents=True)
    write_json(DATA / "frozen_protocol.json", cfg)
    write_json(DATA / "source_distribution.json", {**provenance, "profiles": profiles})
    np.savez_compressed(DATA / "source_innovations.npz", innovations=pool)
    tasks = list(cfg["scenario_definitions"])
    metadata, files = {}, {}
    for si, split in enumerate(("train", "calibration", "development", "test")):
        if split in ("train", "calibration"):
            count = cfg["train_normal_cases" if split == "train" else "calibration_normal_cases"]
            cases = [("normal", False, i) for i in range(count)]
        else:
            count = cfg["development_pairs_per_scenario" if split == "development" else "test_pairs_per_scenario"]
            cases = [(task, fault, i) for task in tasks for i in range(count) for fault in (False, True)]
        xs, obs, available, truths, ids, starts, meta = [], [], [], [], [], [], []
        for ci, (task, fault, pair) in enumerate(cases):
            seed = cfg["seed"] + si * 1_000_000 + (tasks.index(task) * 10000 if task != "normal" else 0) + pair
            rng = np.random.default_rng(seed)
            profile = profiles[int(rng.integers(len(profiles)))]
            phase = int(rng.integers(24)) * STEPS
            z = simulate(rng, profile, pool, cfg, phase)
            event = inject(z, np.random.default_rng(seed + 900_000_000), task, cfg) if fault else None
            x = np.maximum(np.asarray(profile["center"]) + z * profile["scale"], 0)
            requests = np.rint(np.expm1(np.minimum(x[:, 0], 20)))
            x[:, 0] = np.log1p(requests)
            a = requests > 0
            x[~a, 1:] = np.nan
            o = np.ones(len(x), bool)
            gap_rng = np.random.default_rng(seed + 700_000_000)
            for _ in range(2):
                begin = int(gap_rng.integers(24, len(x) // STEPS - 8)) * STEPS
                o[begin:begin + int(gap_rng.integers(2, 6)) * STEPS] = False
            a &= o
            x[~o] = np.nan
            y = np.zeros(cfg["monitor_hours"], bool)
            if event:
                y[event["event_start_hour"]:event["event_end_hour"]] = True
            identity = f"FIVE-{split}-{ci:04d}"
            xs.append(x.astype(np.float32));obs.append(o);available.append(a);truths.append(y);ids.append(identity)
            starts.append((pd.Timestamp("2031-01-01") + pd.Timedelta(minutes=phase * 5)).value)
            meta.append({"case_id": identity, "scenario": task, "injected": fault,
                         "adapterType": profile["adapterType"], "processId": profile["processId"], **(event or {})})
        p = DATA / f"{split}_inputs.npz"
        np.savez_compressed(p, X=np.asarray(xs), collection_observed=np.asarray(obs),
                            latency_available=np.asarray(available), case_id=np.asarray(ids),
                            start_ns=np.asarray(starts), channels=np.asarray(CHANNELS))
        truth = DATA / f"{split}_truth.npz"
        np.savez_compressed(truth, y=np.asarray(truths), case_id=np.asarray(ids))
        mp = DATA / f"{split}_metadata.json"
        write_json(mp, meta)
        files.update({p.name: sha256(p), truth.name: sha256(truth), mp.name: sha256(mp)})
        metadata[split] = len(ids)
        print("Native benchmark generated", split, len(ids), "cases", len(ids) * len(x), "fine bins", flush=True)
    for name in ("frozen_protocol.json", "source_distribution.json", "source_innovations.npz"):
        files[name] = sha256(DATA / name)
    write_json(DATA / "manifest.json", {"files": files, "generator_sha256": sha256(__file__),
        "source": provenance, "case_counts": metadata, "custom_semi_synthetic": True,
        "official_ADBench": False, "original_hourly_test_preserved": True,
        "normal_and_anomaly_pair_share_base_trajectory": True,
        "all_six_original_scenarios_retained": True, "no_scores_seen_before_freeze": True,
        "point_adjustment": False, "hourly_labels_include_weak_event_hours": True,
        "normal_and_fault_generation_shared_across_splits": True,
        "independent_real_fault_validation": False})


def load_split(split, with_truth=False):
    if split not in ("train", "calibration", "development", "test"):
        raise ValueError("Unknown benchmark split")
    manifest = read_json(DATA / "manifest.json")
    p = DATA / f"{split}_inputs.npz"
    if sha256(p) != manifest["files"][p.name]:
        raise ValueError("Frozen benchmark input changed")
    with np.load(p, allow_pickle=False) as z:
        result = {k: z[k] for k in z.files}
    if with_truth:
        p = DATA / f"{split}_truth.npz"
        if sha256(p) != manifest["files"][p.name]:
            raise ValueError("Frozen benchmark truth changed")
        with np.load(p, allow_pickle=False) as z:
            np.testing.assert_array_equal(result["case_id"], z["case_id"])
            result["y"] = z["y"]
    return result


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
