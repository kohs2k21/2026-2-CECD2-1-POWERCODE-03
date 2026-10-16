"""Frozen empirical-input tails versus added magnitude and bounded persistence.

This retrains a v12-style tail/context recipe on aggregate simulations. It does
not reproduce the original rich-completion features or frozen v12 weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


BASE_NAMES = [
    "count_upper_tail_1m", "count_lower_tail_1m", "count_tail_mean_5m",
    "count_tail_mean_15m", "count_zero_run", "count_tail_ewma",
    "count_tail_change", "latency_upper_tail_1m", "latency_lower_tail_1m",
    "latency_upper_mean_5m", "latency_lower_mean_5m", "latency_upper_max_5m",
    "latency_upper_change", "latency_upper_ewma", "latency_upper_anchor",
    "count_tail_mean_W", "latency_tail_mean_W", "duration_observed",
    "hour_sin", "hour_cos", "weekend", "adapter_0", "adapter_1",
]
MAGNITUDE_NAMES = ["count_above_q95_ratio", "count_below_q05_ratio", "latency_above_q95_ratio",
                   "count_excess_max_W", "latency_excess_max_W"]
PERSISTENCE_NAMES = ["count_above_q95_run_minutes", "count_below_q05_run_minutes",
                     "latency_above_q95_run_minutes", "count_exceed_fraction_W",
                     "latency_exceed_fraction_W"]


def _rolling(values, width, method="mean"):
    series = pd.Series(values, dtype=float).rolling(int(width), min_periods=1)
    return getattr(series, method)().to_numpy()


def _run(mask):
    indices = np.arange(len(mask))
    return np.where(mask, indices - np.maximum.accumulate(np.where(~mask, indices, -1)), 0).astype(float)


def _bounded_ewma(values, width, alpha=0.2):
    decay = 1.0 - alpha
    infinite = pd.Series(values).ewm(alpha=alpha, adjust=True).mean().to_numpy()
    n = np.arange(1, len(values) + 1)
    numerator = infinite * (1 - decay ** n)
    previous = np.r_[np.zeros(width), numerator[:-width]] if width < len(values) else np.zeros(len(values))
    return (numerator - decay ** width * previous) / (1 - decay ** np.minimum(n, width))


def _validate_stream(stream):
    required = {"endpoint", "minute_index", "group", "adapter", "status", "count", "duration_ms", "coverage_known"}
    if not required.issubset(stream.columns):
        raise ValueError(f"Missing input columns: {sorted(required - set(stream.columns))}")
    if stream.duplicated(["group", "endpoint"]).any():
        raise ValueError("Duplicate group-endpoints are not independent observations")
    for _, group in stream.groupby("group", sort=False):
        endpoints = pd.to_datetime(group.endpoint, utc=True)
        if len(group) > 1 and not (endpoints.diff().iloc[1:] == pd.Timedelta(minutes=1)).all():
            raise ValueError("Do not concatenate gaps or splits into fictitious continuous time")
    count = stream["count"].to_numpy(float)
    duration = stream.duration_ms.to_numpy(float)
    known = stream.coverage_known.to_numpy(bool)
    if (count[np.isfinite(count)] < 0).any() or (duration[np.isfinite(duration)] < 0).any():
        raise ValueError("Negative measurements are not valid")
    if np.any(known & ~np.isfinite(count)):
        raise ValueError("Known coverage requires observed count, including confirmed zero")


def _common_masks(group, warmup):
    known = group.coverage_known.to_numpy(bool) & np.isfinite(group["count"].to_numpy(float))
    valid_count = pd.Series(known.astype(int)).rolling(warmup, min_periods=warmup).sum().to_numpy()
    s_count = pd.Series((known & group.status.eq("S").to_numpy()).astype(int)).rolling(warmup, min_periods=warmup).sum().to_numpy()
    after_prefix = group.minute_index.to_numpy() >= warmup
    return after_prefix & (valid_count == warmup), after_prefix & (s_count == warmup)


def fit_reference(train: pd.DataFrame, protocol: dict | None = None) -> dict:
    """Fit only declared train rows on the common maximum-window normal-S mask."""
    _validate_stream(train)
    if "split" not in train or set(train.split) != {"train"}:
        raise ValueError("Reference fit accepts only the declared train split")
    p = {"warmup_minutes": 720, "reference_minimum": 20, "reference_quantiles": [0.05, 0.95]} if protocol is None else protocol
    tables, pooled, selected = {}, {}, 0
    for group_name, frame in train.groupby("group", sort=False):
        _, fit_mask = _common_masks(frame, p["warmup_minutes"])
        chosen = frame.loc[fit_mask]
        if len(chosen) < p["reference_minimum"]:
            raise ValueError(f"Normal common cohort unavailable: {group_name}")
        selected += len(chosen)
        for metric in ("count", "duration_ms"):
            values = chosen[metric].to_numpy(float)
            values = values[np.isfinite(values)]
            if len(values) < p["reference_minimum"]:
                raise ValueError(f"Insufficient normal reference for {group_name}/{metric}")
            pooled[(group_name, metric)] = np.sort(values)
        times = pd.to_datetime(chosen.endpoint, utc=True)
        keyed = chosen.assign(_hour=times.dt.hour, _weekend=times.dt.dayofweek.ge(5))
        for (hour, weekend), part in keyed.groupby(["_hour", "_weekend"], sort=False):
            for metric in ("count", "duration_ms"):
                values = part[metric].to_numpy(float)
                values = values[np.isfinite(values)]
                if len(values) >= p["reference_minimum"]:
                    tables[(group_name, int(hour), bool(weekend), metric)] = np.sort(values)
    return {"tables": tables, "pooled": pooled, "warmup_minutes": p["warmup_minutes"],
            "quantiles": p["reference_quantiles"], "reference_selected_rows": selected,
            "adapters": sorted(train.adapter.unique().tolist()), "fit_split": "train"}


def _transform_metric(frame, reference, metric):
    times = pd.to_datetime(frame.endpoint, utc=True)
    upper, lower, high, low, filled = (np.empty(len(frame)) for _ in range(5))
    raw = frame[metric].to_numpy(float)
    group_name = frame.group.iloc[0]
    keys = pd.DataFrame({"hour": times.dt.hour.to_numpy(), "weekend": times.dt.dayofweek.ge(5).to_numpy()})
    for (hour, weekend), ids in keys.groupby(["hour", "weekend"], sort=False).indices.items():
        key = (group_name, int(hour), bool(weekend), metric)
        ref = reference["tables"].get(key, reference["pooled"].get((group_name, metric)))
        if ref is None:
            raise ValueError(f"Unseen group has no approved reference: {group_name}")
        values = np.where(np.isfinite(raw[ids]), raw[ids], np.median(ref))
        n = len(ref)
        upper[ids] = -np.log((n - np.searchsorted(ref, values, side="left") + 1) / (n + 1))
        lower[ids] = -np.log((np.searchsorted(ref, values, side="right") + 1) / (n + 1))
        low[ids], high[ids] = np.quantile(ref, reference["quantiles"])
        filled[ids] = values
    return upper, lower, high, low, filled


def build_features(stream: pd.DataFrame, reference: dict, window_hours: int, cadence_minutes: int = 5) -> dict:
    """One row per group/base-bin; readiness is common across all W and variants.

    X for unready rows is NaN. Retain metadata for coverage while scoring only
    ready rows. Labels/status/active never enter X. All minute bins feed bounded
    W features, but only endpoints on the fixed decision grid are returned.
    """
    if window_hours not in range(1, 13) or cadence_minutes < 1:
        raise ValueError("Supported W is 1..12 hours; cadence must be positive")
    _validate_stream(stream)
    width = window_hours * 60
    all_x = {key: [] for key in ("base", "enhanced", "magnitude", "persistence")}
    metadata = []
    names = {"base": BASE_NAMES, "enhanced": BASE_NAMES + MAGNITUDE_NAMES + PERSISTENCE_NAMES,
             "magnitude": BASE_NAMES + MAGNITUDE_NAMES, "persistence": BASE_NAMES + PERSISTENCE_NAMES}
    for _, original in stream.groupby("group", sort=False):
        frame = original.reset_index(drop=True)
        ready, fit_eligible = _common_masks(frame, reference["warmup_minutes"])
        cu, cl, cq95, cq05, counts = _transform_metric(frame, reference, "count")
        du, dl, dq95, _, durations = _transform_metric(frame, reference, "duration_ms")
        observed = np.isfinite(frame.duration_ms.to_numpy(float))
        # Missing duration is an explicit observed flag plus a frozen median fill.
        du, dl = np.where(observed, du, 0.0), np.where(observed, dl, 0.0)
        ct = np.maximum(cu, cl)
        cm5, cm15, dm5 = _rolling(ct, 5), _rolling(ct, 15), _rolling(du, 5)
        past_count = np.r_[0.0, _rolling(ct, min(15, width))[:-1]]
        past_duration = np.r_[0.0, _rolling(dm5, min(15, width - 5))[:-1]]
        anchor = pd.Series(du).rolling(5, min_periods=1).mean().shift(width - 5).fillna(0).to_numpy()
        times = pd.to_datetime(frame.endpoint, utc=True)
        hour = times.dt.hour.to_numpy() + times.dt.minute.to_numpy() / 60
        adapters = reference["adapters"]
        if len(adapters) != 2 or not frame.adapter.isin(adapters).all():
            raise ValueError("This frozen protocol requires its two known adapters")
        base = np.column_stack([
            cu, cl, cm5, cm15, np.log1p(np.minimum(_run(counts == 0), width)),
            _bounded_ewma(ct, width), ct - past_count, du, dl, dm5, _rolling(dl, 5),
            _rolling(du, 5, "max"), dm5 - past_duration, _bounded_ewma(du, width), dm5 - anchor,
            _rolling(ct, width), _rolling(du, width), observed.astype(float),
            np.sin(hour * np.pi / 12), np.cos(hour * np.pi / 12), times.dt.dayofweek.ge(5).to_numpy(float),
            frame.adapter.eq(adapters[0]).to_numpy(float), frame.adapter.eq(adapters[1]).to_numpy(float),
        ])
        count_hi = np.maximum(counts / np.maximum(cq95, 1.0) - 1.0, 0.0)
        count_lo = np.maximum((cq05 - counts) / np.maximum(cq05, 1.0), 0.0)
        duration_hi = np.where(observed, np.maximum(durations / np.maximum(dq95, 1.0) - 1.0, 0.0), 0.0)
        magnitude = np.column_stack([count_hi, count_lo, duration_hi,
                                     _rolling(np.maximum(count_hi, count_lo), width, "max"),
                                     _rolling(duration_hi, width, "max")])
        count_exceeds, duration_exceeds = (count_hi > 0) | (count_lo > 0), duration_hi > 0
        persistence = np.column_stack([
            np.minimum(_run(count_hi > 0), width), np.minimum(_run(count_lo > 0), width),
            np.minimum(_run(duration_exceeds), width), _rolling(count_exceeds, width), _rolling(duration_exceeds, width),
        ])
        variants = {"base": base, "enhanced": np.column_stack([base, magnitude, persistence]),
                    "magnitude": np.column_stack([base, magnitude]), "persistence": np.column_stack([base, persistence])}
        times_on_grid = times.dt.floor(f"{cadence_minutes}min").eq(times).to_numpy()
        for key, values in variants.items():
            values[~ready] = np.nan
            if not np.isfinite(values[ready]).all():
                raise ValueError(f"Non-finite ready features: {key}")
            all_x[key].append(values[times_on_grid].astype(np.float32))
        meta_cols = [c for c in ("endpoint", "minute_index", "group", "adapter", "status", "split", "coverage_known", "active", "normal_confirmed") if c in frame]
        meta = frame[meta_cols].copy()
        meta["ready"], meta["fit_eligible"] = ready, fit_eligible
        meta["readiness"] = ready
        meta["train_mask"] = fit_eligible & frame["split"].eq("train").to_numpy() if "split" in frame else False
        meta["emitted_at"] = pd.to_datetime(frame.endpoint, utc=True).dt.ceil(f"{cadence_minutes}min")
        meta["window_start"] = pd.to_datetime(frame.endpoint, utc=True) - pd.Timedelta(minutes=width)
        split_label = frame["split"].astype(str) if "split" in frame else pd.Series("unknown", index=frame.index)
        meta["row_id"] = split_label + "|" + frame.group.astype(str) + "|" + times.astype(str)
        meta["window_hours"] = window_hours
        meta["not_ready_reason"] = np.where(ready, "", np.where(frame.minute_index < reference["warmup_minutes"], "common_warmup", "unknown_in_common_context"))
        metadata.append(meta.loc[times_on_grid].reset_index(drop=True))
    return {"X": {key: np.vstack(chunks) for key, chunks in all_x.items()},
            "metadata": pd.concat(metadata, ignore_index=True), "feature_names": names}


def self_check():
    """Small reproducible contract/causality check; no anomaly-model fitting."""
    try:
        from .dataset import build_dataset
    except ImportError:
        from dataset import build_dataset
    data = build_dataset(11)
    ref = fit_reference(data["train"])
    first = build_features(data["train"], ref, 1)
    last = build_features(data["train"], ref, 12)
    assert first["metadata"].row_id.equals(last["metadata"].row_id)
    assert first["metadata"].train_mask.equals(last["metadata"].train_mask)
    assert first["metadata"].train_mask.sum() == 4032
    assert np.array_equal(first["X"]["base"], first["X"]["enhanced"][:, :len(BASE_NAMES)], equal_nan=True)
    test = build_features(data["test"], ref, 4)
    changed = data["test"].copy()
    changed["status"], changed["active"], changed["normal_confirmed"] = "F", False, False
    changed.loc[changed.minute_index >= 4000, "count"] *= 100
    later = build_features(changed, ref, 4)
    prefix = test["metadata"].minute_index < 4000
    assert np.array_equal(test["X"]["enhanced"][prefix], later["X"]["enhanced"][prefix], equal_nan=True)
    assert np.isnan(test["X"]["enhanced"][~test["metadata"].ready]).all()
    assert data["test"].loc[~data["test"].coverage_known, "count"].isna().all()
    assert data["train"].endpoint.max() < data["calibration"].endpoint.min()
    assert data["calibration"].endpoint.max() < data["test"].endpoint.min()
    return {"training_rows": 4032, "test_ready_rows": int(test["metadata"].ready.sum()), "status": "passed"}


if __name__ == "__main__":
    print(self_check())
