"""Five-minute aggregation without changing the external Process schema.

Only records whose startTime is inside [start, end) are counted. Duration is
retrospective and uses only completed records available by end. Collection
coverage is supplied separately by the collector, never inferred from absence
of Process records. Unknown coverage is not a successful zero-request interval.
"""
from dataclasses import dataclass
import json
from typing import Mapping

import numpy as np
import pandas as pd

from process_anomaly_pipeline.data import prepare, FINAL_STATUSES

BIN = pd.Timedelta(minutes=5)
GROUP = ("adapterType", "processId")
PK = ("transactionId", "processId", "retryCount")


def kst(value):
    """A naive input is KST, matching the existing Process normalizer."""
    value = pd.Timestamp(value)
    if pd.isna(value):
        raise ValueError("A finite timestamp is required")
    if value.tzinfo is not None:
        value = value.tz_convert("Asia/Seoul").tz_localize(None)
    return value.as_unit("ns")


def group_key(adapter, process):
    if pd.isna(process) or not str(process).strip():
        raise ValueError("processId is required for a monitoring group")
    return (None if pd.isna(adapter) else str(adapter), str(process))


def case_id(key):
    # JSON avoids delimiter collisions and separates SQL NULL from literal text.
    return json.dumps(list(key), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class CollectionCoverage:
    """Explicitly confirmed collector intervals [start, end), not log coverage.

    Instantiate only from collection telemetry or an explicitly certified
    complete export. A CSV's first/last timestamps are not such certification.
    """
    intervals: tuple

    def __post_init__(self):
        normalized = sorted((kst(a), kst(b)) for a, b in self.intervals)
        merged = []
        for a, b in normalized:
            if a >= b:
                raise ValueError("Coverage intervals must have positive length")
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        object.__setattr__(self, "intervals", tuple(merged))

    def mask(self, times, width=BIN):
        result = np.zeros(len(times), bool)
        for a, b in self.intervals:
            result |= (times >= a) & (times + width <= b)
        return result


@dataclass(frozen=True)
class ProcessWindow:
    start: pd.Timestamp
    end: pd.Timestamp
    bins: pd.DataFrame
    diagnostics: dict


def aggregate_processes(records, *, start, end, coverage=None, groups=None):
    """Aggregate original records into exactly 12 bins per selected hour.

    coverage: CollectionCoverage shared by these groups, or a mapping from
    (adapterType, processId) to CollectionCoverage. None means unknown.
    groups: reviewed reference keys, needed to represent a completely silent
    group. An ID never previously registered cannot be inferred from no rows.
    No labels, scenario IDs or benchmark metadata are accepted as predictors.
    """
    start, end = kst(start), kst(end)
    if start >= end or (end - start) % pd.Timedelta(hours=1):
        raise ValueError("Supply a positive whole-hour selected interval")
    if coverage is not None and not isinstance(coverage, (CollectionCoverage, Mapping)):
        raise TypeError("Coverage must be CollectionCoverage, a group mapping, or None")
    raw = pd.DataFrame([records] if isinstance(records, dict) else records).copy()
    # Do not trust a caller's mutable prepared-data marker to bypass parsing.
    raw.attrs.clear()
    f = prepare(raw)
    if f.startTime.isna().any():
        raise ValueError("Missing/invalid startTime cannot be assigned to a selected window")
    selected = f.startTime.ge(start) & f.startTime.lt(end)
    f = f.loc[selected].copy()
    for c in (*PK, "processHubId"):
        if f[c].isna().any():
            raise ValueError(f"Selected Process records require {c}")
    if (f._invalid_retryCount | f.retryCount.lt(0) | f.retryCount.mod(1).ne(0)).any():
        raise ValueError("Selected retryCount must be a nonnegative integer")
    if f.duplicated(list(PK)).any():
        raise ValueError("Duplicate SQL Process primary key; reconcile before aggregation")
    actual_keys = {group_key(a, p) for a, p in zip(f.adapterType, f.processId)}
    if groups is None:
        keys = actual_keys
    else:
        keys = {group_key(*key) for key in groups}
        # Unexpected groups must be surfaced rather than pooled into another ID.
        if actual_keys - keys:
            raise ValueError(f"Unregistered monitoring groups: {sorted(map(str, actual_keys - keys))}")
    if not keys:
        raise ValueError("No monitoring groups; supply registered groups for an empty input")
    times = pd.date_range(start, end, freq="5min", inclusive="left")
    f["_bin"] = ((f.startTime - start) / BIN).astype(int)
    f["_key"] = [group_key(a, p) for a, p in zip(f.adapterType, f.processId)]
    rows = []
    for key in sorted(keys, key=case_id):
        part = f.loc[f._key.map(lambda v: v == key)].copy()
        cov = coverage.get(key) if isinstance(coverage, Mapping) else coverage
        if cov is not None and not isinstance(cov, CollectionCoverage):
            raise TypeError("Each group's coverage must be CollectionCoverage or None")
        observed = np.zeros(len(times), bool) if cov is None else cov.mask(times)
        frame = pd.DataFrame({"timestamp": times, "case_id": case_id(key),
                              "adapterType": key[0], "processId": key[1],
                              "collection_observed": observed})
        received = np.bincount(part._bin, minlength=len(times))
        terminal = part.status.isin(FINAL_STATUSES)
        finished = terminal & part.endTime.le(end) & part.duration_ms.ge(0)
        valid_work = (part.totalCount.ge(0) & part.totalCount.mod(1).eq(0)
                      & ~part._invalid_totalCount)
        work = finished & valid_work
        invalid_duration = terminal & (part.endTime.isna() | part.duration_ms.lt(0))
        pending = ~terminal | (terminal & part.endTime.gt(end))
        def count(mask):
            return np.bincount(part.loc[mask, "_bin"], minlength=len(times))
        def log_sum(mask, column):
            return np.bincount(part.loc[mask, "_bin"], weights=np.log1p(part.loc[mask, column]),
                               minlength=len(times))
        nd, nw = count(finished), count(work)
        ds, ws = log_sum(finished, "duration_ms"), log_sum(work, "totalCount")
        frame["received_rows"] = received
        frame["request_count"] = np.where(observed, received, np.nan)
        frame["duration_samples"] = nd
        frame["work_samples"] = nw
        frame["duration_log_sum"] = ds
        frame["work_log_sum"] = ws
        frame["pending_rows"] = count(pending)
        frame["invalid_duration_rows"] = count(invalid_duration)
        frame["invalid_work_rows"] = count(finished & ~valid_work)
        frame["failure_rows"] = count(part.status.eq("F"))
        frame["log_requests"] = np.where(observed, np.log1p(received), np.nan)
        frame["log_duration_ms"] = np.divide(ds, nd, out=np.full(len(times), np.nan), where=nd > 0)
        frame["log_items_per_process"] = np.divide(ws, nw, out=np.full(len(times), np.nan), where=nw > 0)
        frame["latency_available"] = observed & (nd > 0) & (nw > 0) & (count(invalid_duration) == 0) & (count(finished & ~valid_work) == 0)
        frame.loc[~frame.latency_available, ["log_duration_ms", "log_items_per_process"]] = np.nan
        rows.append(frame)
    return ProcessWindow(start, end, pd.concat(rows, ignore_index=True), {
        "selected_rows": int(len(f)), "outside_rows_ignored": int((~selected).sum()),
        "bin_minutes": 5, "external_process_schema_changed": False,
        "outside_window_monitoring_used": False,
        "duration_available_as_of": end.isoformat(),
        "group_columns": list(GROUP), "different_hubs_pooled_within_adapter_process": True,
        "collection_coverage_inferred_from_logs": False,
    })


def hourly_frame(window):
    """Recover compatible hourly sufficient statistics, not mean-of-bin means.

    This bridge preserves the old bank's input contract. It does not claim
    that old hourly thresholds have been validated on new raw-log populations.
    """
    parts = []
    for _, bins in window.bins.groupby("case_id", sort=False):
        b = bins.copy()
        b["_hour"] = np.arange(len(b)) // 12
        for _, g in b.groupby("_hour", sort=True):
            observed = bool(g.collection_observed.all())
            nd, nw = int(g.duration_samples.sum()), int(g.work_samples.sum())
            available = (observed and nd > 0 and nw > 0 and not bool(g.invalid_duration_rows.sum())
                         and not bool(g.invalid_work_rows.sum()))
            parts.append({"case_id": g.case_id.iloc[0], "timestamp": g.timestamp.iloc[0],
                "collection_observed": observed, "latency_available": available,
                "log_requests": float(np.log1p(g.received_rows.sum())) if observed else np.nan,
                "log_duration_ms": float(g.duration_log_sum.sum() / nd) if available else np.nan,
                "log_items_per_process": float(g.work_log_sum.sum() / nw) if available else np.nan})
    return pd.DataFrame(parts)


def window_features(window):
    """Uncalibrated internal summaries for the next fine-resolution model.

    All trends are computed from this selected interval only. These diagnostic
    features are not silently applied to the frozen hourly model's threshold.
    """
    rows = []
    for key, g in window.bins.groupby("case_id", sort=False):
        row = {"case_id": key, "analysis_hours": (window.end - window.start).total_seconds() / 3600,
               "covered_bin_fraction": float(g.collection_observed.mean()),
               "received_rows": int(g.received_rows.sum()),
               "duration_samples": int(g.duration_samples.sum())}
        for name, mask in (("log_requests", g.collection_observed), ("log_duration_ms", g.latency_available)):
            y = g[name].to_numpy(float)
            good = mask.to_numpy(bool) & np.isfinite(y)
            t = np.arange(len(g), dtype=float) / 12
            row[f"{name}_mean"] = float(y[good].mean()) if good.any() else np.nan
            row[f"{name}_std"] = float(y[good].std()) if good.any() else np.nan
            row[f"{name}_slope_per_hour"] = float(np.polyfit(t[good], y[good], 1)[0]) if good.sum() >= 3 else np.nan
            half = len(g) // 2
            a, b = y[:half][good[:half]], y[half:][good[half:]]
            row[f"{name}_late_minus_early"] = float(b.mean() - a.mean()) if len(a) and len(b) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)
