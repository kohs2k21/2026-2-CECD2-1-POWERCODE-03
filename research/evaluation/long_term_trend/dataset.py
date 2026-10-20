"""Issue54 independent aggregate simulation. No raw customer data or v12 replay.

Each row is a source minute bin [bin_start, endpoint). Zero completed executions
is count=0, duration=null, never a fabricated successful PROCESS. Event truth is
separate from observable source values; all source executions retain status S.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


SPLITS = ("train", "calibration", "selection", "blind", "cadence_holdout")


def load_protocol():
    return json.loads(Path(__file__).with_name("protocol.json").read_text(encoding="utf-8"))


def _registry(groups):
    aliases, originals, renamed = {}, {}, {}
    for i, group in enumerate(groups):
        originals[group] = (f"transport:{i}/original", f"interface:{group}.v1")
        renamed[group] = (f"renamed:任意値/{i}", f"alias:{group}/vNext")
        for pair in (originals[group], renamed[group]):
            aliases[json.dumps(list(pair), ensure_ascii=False)] = group
    return {"version": "controlled-registry-v1", "aliases": aliases,
            "originals": originals, "renamed": renamed, "unseen_policy": "not_ready_no_fallback"}


def _hash_values(values):
    return hashlib.sha256(np.asarray(values, dtype=np.float64).tobytes()).hexdigest()


def load_split(seed, name, protocol=None):
    """Explicit phase access; loading train never constructs selection/blind truth.

    Returns stream/events/observation_ledger/registry/manifest. Source values for
    late bins exist here, but features must enforce available_at <= decision_tick.
    """
    p = load_protocol() if protocol is None else protocol
    if name not in SPLITS or seed not in p["seeds"]:
        raise ValueError("Use a declared seed and explicit allowed split name")
    if p["base_bin_minutes"] != 1 or p["prefix_minutes"] < p["max_window_minutes"] + p["watermark_lag_minutes"]:
        raise ValueError("Insufficient independent context or unsupported source resolution")
    split_id, seed_id = SPLITS.index(name), p["seeds"].index(seed)
    rng = np.random.default_rng(np.random.SeedSequence([5401, seed, split_id]))
    registry = _registry(p["groups"])
    scored_start = pd.Timestamp(p["scored_starts_utc"][name])
    size = p["split_days"][name] * 1440 + p["prefix_minutes"]
    minute = np.arange(-p["prefix_minutes"], p["split_days"][name] * 1440)
    starts = pd.date_range(scored_start - pd.Timedelta(minutes=p["prefix_minutes"]), periods=size, freq="min")
    endpoints = starts + pd.Timedelta(minutes=1)
    hour = starts.hour.to_numpy() + starts.minute.to_numpy() / 60
    weekday = starts.dayofweek.to_numpy() < 5
    frames, events, observations = [], [], []
    for group_id, group in enumerate(p["groups"]):
        noise = rng.normal(size=size)
        latent = np.empty(size)
        latent[0] = noise[0]
        for i in range(1, size):
            latent[i] = .94 * latent[i - 1] + np.sqrt(1 - .94 ** 2) * noise[i]
        latent = np.clip(latent, -2.5, 2.5)
        active = np.ones(size, bool) if group == "continuous" else weekday & (hour >= 7) & (hour < 19)
        day_phase = 1 + .22 * np.sin((hour - 7) * np.pi / 12)
        legal_peak = np.where((hour >= 11) & (hour < 13), 1.28, 1.0)
        # Every split contains slow legal workload changes, not only flat normals.
        plateau = 1 + .13 * np.sin(minute / (1440 * 2) * np.pi)
        rate = (26 + 13 * group_id) * day_phase * legal_peak * plateau * np.exp(.16 * latent)
        rate *= np.where(weekday, 1.0, .8)
        count = (rng.poisson(rate) * active).astype(float)
        duration = (105 + 45 * group_id) * (1 + .07 * np.sin(hour * np.pi / 12)) * np.exp(.10 * latent + rng.normal(0, .045, size))
        duration[count == 0] = np.nan
        adapter, interface = registry["originals"][group]
        delay = rng.integers(0, p["watermark_lag_minutes"] + 1, size=size)
        frame = pd.DataFrame({"bin_start": starts, "endpoint": endpoints, "minute_index": minute,
                              "group": group, "scope": group, "adapter": adapter, "interface": interface,
                              "status": "S", "count": count, "completion_count": count.copy(),
                              "duration_ms": duration, "failure_count": 0.0, "source_complete": True,
                              "coverage_known": True, "active": active, "normal_confirmed": True,
                              "split": name, "seed": seed})
        frame["available_at"] = endpoints + pd.to_timedelta(delay, unit="min")
        frame["observed_at"] = frame["available_at"]

        def mark_unknown(ids, reason):
            frame.loc[ids, ["count", "completion_count", "duration_ms", "failure_count"]] = np.nan
            frame.loc[ids, ["source_complete", "coverage_known", "normal_confirmed"]] = False
            observations.append({"group": group, "statecode": reason,
                                 "start": frame.loc[ids[0], "bin_start"],
                                 "end": frame.loc[ids[-1], "endpoint"], "split": name, "seed": seed})

        if name not in ("train", "calibration"):
            # Two complete working weeks: ten independent event slots per group.
            for repetition in range(2):
                order = rng.permutation(p["event_types"])
                for slot, event_type in enumerate(order):
                    type_id = p["event_types"].index(event_type)
                    strength_id = (seed_id + 2 * group_id + type_id + repetition) % 3
                    duration_id = (seed_id + group_id + 2 * type_id + 2 * repetition) % 3
                    length = int(p["event_durations_minutes"][duration_id])
                    start_minute = (repetition * 7 + slot) * 1440 + p["event_onset_minutes_of_day"]
                    ids = np.flatnonzero((minute >= start_minute) & (minute < start_minute + length))
                    if len(ids) != length or not active[ids].all():
                        raise ValueError("Event interval must be fully inside a registered active interval")
                    cm, dm = p["count_multipliers"][strength_id], p["duration_multipliers"][strength_id]
                    marginal_before = marginal_after = None
                    if event_type == "burst":
                        frame.loc[ids, "count"] = np.ceil(frame.loc[ids, "count"] * cm)
                    elif event_type == "inflow_stop":
                        frame.loc[ids, "count"] = 0.0
                        frame.loc[ids, "duration_ms"] = np.nan
                    elif event_type == "level_shift":
                        frame.loc[ids, "duration_ms"] *= dm
                    elif event_type == "gradual_increase":
                        factors = np.linspace(1.0, dm, length)
                        frame.loc[ids, "duration_ms"] *= factors
                    elif event_type == "relation_change":
                        # Within-hour duration values are unchanged as a multiset;
                        # their assignment to ordinary counts reverses association.
                        before = frame.loc[ids, "duration_ms"].to_numpy(float).copy()
                        for hour_key in pd.DatetimeIndex(frame.loc[ids, "bin_start"]).floor("h").unique():
                            local = ids[pd.DatetimeIndex(frame.loc[ids, "bin_start"]).floor("h") == hour_key]
                            order_ids = local[np.argsort(frame.loc[local, "count"].to_numpy(), kind="stable")]
                            frame.loc[order_ids, "duration_ms"] = np.sort(frame.loc[local, "duration_ms"].to_numpy())[::-1]
                        after = frame.loc[ids, "duration_ms"].to_numpy(float)
                        marginal_before, marginal_after = _hash_values(np.sort(before)), _hash_values(np.sort(after))
                    else:
                        raise ValueError(event_type)
                    frame.loc[ids, "completion_count"] = frame.loc[ids, "count"]
                    frame.loc[ids, "normal_confirmed"] = False
                    start, end = frame.loc[ids[0], "bin_start"], frame.loc[ids[-1], "endpoint"]
                    confirmed = start
                    if event_type == "gradual_increase":
                        crossing = np.flatnonzero(factors >= 1 + p["confirmed_relative_increase"])
                        if len(crossing) < p["confirmed_minimum_bins"]:
                            raise ValueError("Ramp has no independently confirmed interval")
                        confirmed = frame.loc[ids[crossing[0]], "bin_start"]
                    cross = p["suite_crosses"].get(f"{group}:{event_type}:{repetition}", "known_complete")
                    if cross == "source_unknown":
                        mark_unknown(ids[:20], cross)
                    elif cross == "observation_late":
                        late_ids = ids[:60]
                        frame.loc[late_ids, "available_at"] = frame.loc[late_ids, "endpoint"] + pd.Timedelta(minutes=20)
                    elif cross == "category_unseen":
                        frame.loc[ids, "adapter"] = f"unmapped:{name}/{seed}"
                    events.append({"event_id": f"{name}-{seed}-{group}-{event_type}-{repetition}",
                                   "split": name, "seed": seed, "group": group, "scope": group,
                                   "scenario": event_type, "repetition": repetition, "start": start, "end": end,
                                   "confirmed_start": confirmed, "detectable_start": confirmed, "recovery": end,
                                   "first_bin_start": start, "first_bin_endpoint": start + pd.Timedelta(minutes=1),
                                   "duration_minutes": length, "count_multiplier": cm, "duration_multiplier": dm,
                                   "observability_kind": cross,
                                   "cohort": {"known_complete": "core", "source_unknown": "source_unknown",
                                              "observation_late": "source_late", "category_unseen": "new_category"}[cross],
                                   "status_preserved": "S",
                                   "relation_marginal_before_sha256": marginal_before,
                                   "relation_marginal_after_sha256": marginal_after,
                                   "label_contract": "generator intervention, not validated business incident"})
            # Normal quality/category controls never acquire service-event labels.
            normal_unknown = np.flatnonzero((minute >= 5 * 1440 + 13 * 60) & (minute < 5 * 1440 + 13 * 60 + 20))
            mark_unknown(normal_unknown, "source_unknown_normal_control")
            late_ids = np.flatnonzero((minute >= 6 * 1440 + 14 * 60) & (minute < 6 * 1440 + 15 * 60))
            frame.loc[late_ids, "available_at"] = frame.loc[late_ids, "endpoint"] + pd.Timedelta(minutes=20)
            alias_ids = np.flatnonzero((minute >= 6 * 1440 + 9 * 60) & (minute < 6 * 1440 + 11 * 60))
            frame.loc[alias_ids, "adapter"], frame.loc[alias_ids, "interface"] = registry["renamed"][group]
            unseen_ids = np.flatnonzero((minute >= 12 * 1440 + 10 * 60) & (minute < 12 * 1440 + 11 * 60))
            frame.loc[unseen_ids, "adapter"] = f"new-normal:{name}/{seed}"
            observations.extend([{"group": group, "statecode": state, "start": frame.loc[ix[0], "bin_start"],
                                  "end": frame.loc[ix[-1], "endpoint"], "split": name, "seed": seed}
                                 for state, ix in (("observation_late_normal_control", late_ids),
                                                   ("category_known_alias", alias_ids), ("category_unseen_normal_control", unseen_ids))])
        frame["observed_at"] = frame["available_at"]
        frame["lineage_id"] = [f"{name}|{seed}|{group}|{int(i)}" for i in minute]
        frames.append(frame)
    stream = pd.concat(frames, ignore_index=True)
    stream.attrs.update({"registry": registry, "protocol": p, "scored_start": scored_start.isoformat(),
                         "scored_end": (scored_start + pd.Timedelta(days=p["split_days"][name])).isoformat()})
    digest = hashlib.sha256(pd.util.hash_pandas_object(stream, index=False).to_numpy().tobytes()).hexdigest()
    event_columns = ["event_id", "split", "seed", "group", "scope", "scenario", "repetition", "start", "end",
                     "confirmed_start", "detectable_start", "recovery", "first_bin_start", "first_bin_endpoint",
                     "duration_minutes", "count_multiplier", "duration_multiplier", "observability_kind", "cohort", "status_preserved",
                     "relation_marginal_before_sha256", "relation_marginal_after_sha256", "label_contract"]
    return {"stream": stream, "events": pd.DataFrame(events, columns=event_columns),
            "observation_ledger": pd.DataFrame(observations), "registry": registry,
            "manifest": {"version": p["version"], "seed": seed, "split": name, "source_sha256": digest,
                         "scored_start": stream.attrs["scored_start"], "scored_end": stream.attrs["scored_end"],
                         "source_rows": len(stream), "intended_events": len(events), "real_source_used": False,
                         "normality_basis": "simulation contract, S is not real-world normal truth",
                         "prefix_minutes": p["prefix_minutes"], "groups": p["groups"], "suite": p["suite"]}}
