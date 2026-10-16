"""Controlled minute aggregates. This is not a customer-log generator or v12 replay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_protocol() -> dict:
    return json.loads(Path(__file__).with_name("protocol.json").read_text(encoding="utf-8"))


def _normal_stream(seed: int, split: str, protocol: dict) -> pd.DataFrame:
    split_id = ("train", "calibration", "test").index(split)
    rng = np.random.default_rng(np.random.SeedSequence([seed, split_id]))
    size = int(protocol["split_days"][split]) * 1440 + protocol["warmup_minutes"]
    endpoints = pd.date_range(protocol["split_starts_utc"][split], periods=size, freq="min")
    hour = endpoints.hour.to_numpy() + endpoints.minute.to_numpy() / 60.0
    weekday = endpoints.dayofweek.to_numpy() < 5
    frames = []
    for group_id, group in enumerate(protocol["groups"]):
        active = np.ones(size, dtype=bool) if group == "continuous" else weekday & (hour >= 7) & (hour < 19)
        # The recurring legal peak is normal in every split, never an event label.
        daily = 1.0 + 0.30 * np.sin((hour - 8) * np.pi / 12)
        legal_peak = np.where((hour >= 10) & (hour < 12), 1.35, 1.0)
        expected_count = (24.0 + group_id * 14) * daily * legal_peak * np.where(weekday, 1.0, 0.75)
        counts = rng.poisson(expected_count) * active
        center = (110.0 + group_id * 45) * (1 + 0.08 * np.sin(hour * np.pi / 12))
        durations = center * np.exp(rng.normal(0.0, 0.07, size))
        durations[counts == 0] = np.nan
        frames.append(pd.DataFrame({
            "endpoint": endpoints, "minute_index": np.arange(size), "group": group,
            "adapter": f"adapter_{group_id}", "status": "S", "count": counts.astype(float),
            "duration_ms": durations, "failure_count": np.zeros(size),
            "coverage_known": True, "active": active, "normal_confirmed": True,
            "split": split,
        }))
    return pd.concat(frames, ignore_index=True)


def build_dataset(seed: int, protocol: dict | None = None) -> dict:
    """Return independent train/calibration/test frames and a separate event ledger.

    Each split includes its own unscored 12-hour prefix in addition to split_days.
    duration_ms is a simulated minute-level latency statistic, not a raw quantile.
    """
    p = load_protocol() if protocol is None else protocol
    if seed not in p["seeds"]:
        raise ValueError("Seed must be declared in the frozen protocol")
    if p["base_bin_minutes"] != 1 or p["warmup_minutes"] < 60 * max(p["window_hours"]):
        raise ValueError("Need one-minute bins and a common maximum-window warmup")
    result = {split: _normal_stream(seed, split, p) for split in ("train", "calibration", "test")}
    test = result["test"]
    events = []
    seed_index = p["seeds"].index(seed)
    # Test scored period starts Friday. Mon/Tue/Wed/Thu events avoid scheduled closure.
    for group_id, group in enumerate(p["groups"]):
        for offset, event_type in enumerate(p["event_types"]):
            strength_index = (seed_index + 2 * group_id + offset) % len(p["seeds"])
            duration_index = (seed_index + group_id + 2 * offset) % len(p["seeds"])
            strength_key, duration_key = str(p["seeds"][strength_index]), str(p["seeds"][duration_index])
            duration = int(p["duration_by_seed_minutes"][duration_key])
            count_multiplier = float(p["count_multiplier_by_seed"][strength_key])
            duration_multiplier = float(p["duration_multiplier_by_seed"][strength_key])
            start = p["warmup_minutes"] + (3 + offset) * 1440 + 9 * 60
            rows = (test.group == group) & test.minute_index.between(start, start + duration - 1)
            ids = np.flatnonzero(rows.to_numpy())
            if len(ids) != duration or not test.loc[ids, "active"].all():
                raise ValueError("Injected event must fit an active, fully observed interval")
            start_time = test.loc[ids[0], "endpoint"]
            end_time = test.loc[ids[-1], "endpoint"] + pd.Timedelta(minutes=1)
            if event_type == "burst":
                test.loc[ids, "count"] = np.ceil(test.loc[ids, "count"] * count_multiplier)
            elif event_type == "inflow_stop":
                test.loc[ids, "count"] = 0.0
                test.loc[ids, "duration_ms"] = np.nan
            elif event_type == "level_shift":
                test.loc[ids, "duration_ms"] *= duration_multiplier
            elif event_type == "gradual_increase":
                factors = np.linspace(1.0, duration_multiplier, duration)
                test.loc[ids, "duration_ms"] *= factors
            else:
                raise ValueError(f"Unsupported event type: {event_type}")
            test.loc[ids, "normal_confirmed"] = False
            detectable = start_time
            if event_type == "gradual_increase":
                crossing = np.flatnonzero(factors >= 1 + p["gradual_detectable_relative_increase"])
                if len(crossing) < p["gradual_detectable_minimum_bins"]:
                    raise ValueError("Ramp never reaches the independently fixed detectable onset")
                detectable = test.loc[ids[crossing[0]], "endpoint"]
            events.append({
                "event_id": f"seed{seed}-{group}-{event_type}", "group": group,
                "scenario": event_type, "start": start_time, "detectable_start": detectable,
                "end": end_time, "recovery": end_time, "duration_minutes": duration,
                "count_multiplier": count_multiplier, "duration_multiplier": duration_multiplier,
                "seed": seed, "status_preserved": "S",
            })
    # Source-unknown is deliberately distinct from zero, normal and an outage event.
    unknown_start = p["warmup_minutes"] + 2 * 1440 + 13 * 60
    unknown = test.minute_index.between(unknown_start, unknown_start + p["unknown_control_minutes"] - 1)
    test.loc[unknown, ["count", "duration_ms", "failure_count"]] = np.nan
    test.loc[unknown, ["coverage_known", "normal_confirmed"]] = False
    result["events"] = pd.DataFrame(events)
    digest = hashlib.sha256()
    for split in ("train", "calibration", "test"):
        digest.update(pd.util.hash_pandas_object(result[split], index=False).values.tobytes())
    result["manifest"] = {
        "protocol_version": p["version"], "seed": seed, "sha256": digest.hexdigest(),
        "data_nature": p["data_nature"], "uses_provided_source_rows": False,
        "normal_status_assumption": "All simulated source rows have status S; injected anomalies also preserve S",
        "independent_splits": True, "events": len(events), "warmup_minutes_per_split": p["warmup_minutes"],
        "aggregation_validation": "not tested: input is controlled minute aggregates",
    }
    return result
