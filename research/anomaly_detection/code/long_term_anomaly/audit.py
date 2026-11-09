"""Stream actual exports, deduplicate exact rows and audit temporal support.

Missing collection is not zero traffic. An occupied hour proves at least some
collection, not that the collector was healthy for every process in that hour.
"""
import argparse
from collections import Counter
import numpy as np
import pandas as pd
from analysis.log_analysis_common import (
    PERIODS, SCHEMAS, discover_source, _read_source, canonical_column, parse_kst,
)
from .common import ROOT, REPORT, sha256, write_json


def audit():
    REPORT.mkdir(parents=True, exist_ok=True)
    frames, audits, seen = [], [], set()
    for period in PERIODS:
        path, info = discover_source(ROOT / "data" / period, "process")
        raw = duplicates = invalid_start = invalid_duration = 0
        status = Counter()
        for chunk in _read_source(path, info, "process", 200_000):
            chunk = chunk.rename(columns=canonical_column).reindex(columns=SCHEMAS["process"])
            raw += len(chunk)
            hashes = pd.util.hash_pandas_object(chunk, index=False).to_numpy()
            keep = []
            for h in hashes:
                value = int(h)
                keep.append(value not in seen)
                seen.add(value)
            duplicates += len(keep) - sum(keep)
            chunk = chunk.loc[keep].copy()
            status.update(chunk.status.fillna("MISSING").value_counts().to_dict())
            start, end = parse_kst(chunk.startTime), parse_kst(chunk.endTime)
            dur = (end - start).dt.total_seconds() * 1000
            total = pd.to_numeric(chunk.totalCount, errors="coerce")
            success = pd.to_numeric(chunk.successCount, errors="coerce")
            error = pd.to_numeric(chunk.errorCount, errors="coerce")
            retry = pd.to_numeric(chunk.retryCount, errors="coerce")
            invalid_start += int(start.isna().sum())
            invalid_duration += int((dur.isna() | dur.lt(0)).sum())
            eligible = (chunk.status.eq("S") & dur.ge(0) & total.ge(0)
                        & error.eq(0) & success.eq(total) & retry.ge(0)
                        & (total.mod(1).eq(0)) & retry.mod(1).eq(0))
            f = pd.DataFrame({
                "hour": start.dt.floor("h"),
                "adapterType": chunk.adapterType.fillna("MISSING"),
                "processId": chunk.processId.fillna("MISSING"),
                "all_rows": 1,
                "normal_rows": eligible.astype(int),
                "duration_sum": dur.where(eligible, 0),
                "log_duration_sum": np.log1p(dur.where(eligible, 0)),
                "log_duration_sq_sum": np.log1p(dur.where(eligible, 0)) ** 2,
                "log_work_sum": np.log1p(total.where(eligible, 0)),
            })
            f = f.dropna(subset=["hour"])
            g = f.groupby(["hour", "adapterType", "processId"], observed=True).sum().reset_index()
            g["source_period"] = period
            frames.append(g)
        audits.append({"period": period, "source": str(path.relative_to(ROOT)),
                       "format": info["format"], "sha256": sha256(path),
                       "raw_rows": raw, "exact_duplicates_removed": duplicates,
                       "invalid_start": invalid_start, "invalid_duration": invalid_duration,
                       "status_counts": dict(status)})
        print("Source audit", audits[-1], flush=True)
    f = pd.concat(frames, ignore_index=True)
    keys = ["source_period", "hour", "adapterType", "processId"]
    f = f.groupby(keys, observed=True).sum().reset_index()
    n = f.normal_rows.replace(0, np.nan)
    f["log_duration"] = f.log_duration_sum / n
    f["log_work"] = f.log_work_sum / n
    f["duration_ms"] = f.duration_sum / n
    f["log_duration_sd"] = np.sqrt((f.log_duration_sq_sum / n - f.log_duration ** 2).clip(lower=0))
    f.to_csv(REPORT / "observed_hourly.csv.gz", index=False, compression="gzip")
    coverage, support = [], []
    for period, part in f.groupby("source_period", observed=True):
        h = part.hour.drop_duplicates().sort_values()
        full_hours = int((h.max() - h.min()).total_seconds() / 3600) + 1
        run = h.diff().ne(pd.Timedelta(hours=1)).cumsum()
        coverage.append({"period": period, "start": str(h.min()), "end": str(h.max()),
                         "occupied_hours": len(h), "span_hours": full_hours,
                         "occupied_ratio": len(h) / full_hours,
                         "occupied_days": h.dt.normalize().nunique(),
                         "longest_all_log_hour_run": int(h.groupby(run).size().max())})
        for (adapter, process), group in part.loc[part.normal_rows.gt(0)].groupby(
                ["adapterType", "processId"], observed=True):
            times = group.hour.sort_values()
            span = int((times.max() - times.min()).total_seconds() / 3600) + 1
            runs = times.diff().ne(pd.Timedelta(hours=1)).cumsum()
            support.append({"period": period, "adapterType": adapter, "processId": process,
                            "normal_rows": int(group.normal_rows.sum()),
                            "occupied_hours": len(times), "span_hours": span,
                            "occupied_ratio": len(times) / span,
                            "days": times.dt.normalize().nunique(),
                            "longest_hour_run": int(times.groupby(runs).size().max()),
                            "start": str(times.min()), "end": str(times.max())})
    pd.DataFrame(support).sort_values("occupied_hours", ascending=False).to_csv(
        REPORT / "series_support.csv", index=False, encoding="utf-8-sig")
    write_json(REPORT / "source_audit.json", {
        "sources": audits, "coverage": coverage,
        "deduplication": "global exact-row 64-bit pandas hashes; theoretical hash collision risk",
        "normal_candidate": "S; duration >=0; nonnegative integer count/retry; total=success, error=0",
        "normal_candidate_is_not_ground_truth": True,
        "collection_health_unknown": True,
        "no_fill_across_source_gaps": True,
        "hourly_sha256": sha256(REPORT / "observed_hourly.csv.gz"),
    })
    print("Coverage", coverage, flush=True)
    print(pd.DataFrame(support).sort_values("occupied_hours", ascending=False).head(15).to_string(index=False), flush=True)
    return f


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    audit()
