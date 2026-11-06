"""Rebuild actual five-minute observations from raw Process records, not hourly interpolation.

This audit creates numerical intermediates only. Occupied bins do not certify
collector health. No gaps are filled, no source rows or old benchmarks modified.
"""
import argparse
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

from analysis.log_analysis_common import PERIODS, discover_source, _read_source, canonical_column, parse_kst
from ..common import ROOT, REPORT, read_json, write_json, sha256

OUT = REPORT / "raw_windows"
KEYS = ["source_period", "timestamp", "adapterType", "processId"]
VALUES = ["received_rows", "normal_rows", "log_duration_sum", "log_duration_sq_sum", "log_work_sum"]


def support_table(frame):
    rows = []
    for (period, adapter, process), g in frame.loc[frame.normal_rows.gt(0)].groupby(
            ["source_period", "adapterType", "processId"], observed=True):
        g = g.sort_values("timestamp")
        times = pd.DatetimeIndex(g.timestamp)
        runs = np.r_[True, np.diff(times.asi8) != pd.Timedelta(minutes=5).value].cumsum()
        sizes = pd.Series(runs).value_counts().to_numpy()
        contiguous = np.diff(times.asi8) == pd.Timedelta(minutes=5).value
        values = np.column_stack((np.log1p(g.normal_rows), g.log_duration_sum / g.normal_rows))
        rho = []
        for ch in range(2):
            left, right = values[:-1, ch][contiguous], values[1:, ch][contiguous]
            rho.append(float(np.corrcoef(left, right)[0, 1]) if len(left) >= 20 and np.std(left) > 1e-8 and np.std(right) > 1e-8 else None)
        rows.append({"source_period": str(period), "adapterType": str(adapter), "processId": str(process),
            "normal_rows": int(g.normal_rows.sum()), "occupied_five_minute_bins": int(len(g)),
            "longest_occupied_run_bins": int(sizes.max()), "longest_occupied_run_hours": float(sizes.max() / 12),
            "occupied_days": int(times.normalize().nunique()), "adjacent_bin_pairs": int(contiguous.sum()),
            "adjacent_count_log_correlation": rho[0], "adjacent_duration_log_correlation": rho[1],
            "normal_rows_per_occupied_bin_median": float(g.normal_rows.median()),
            "normal_rows_per_occupied_bin_p10": float(g.normal_rows.quantile(.1)),
            "start": str(times[0]), "end": str(times[-1]),
            "disjoint_fully_occupied_windows": {str(h): int((sizes // (12 * h)).sum()) for h in (1, 4, 8, 12, 24)}})
    return sorted(rows, key=lambda r: r["normal_rows"], reverse=True)


def run(out=OUT):
    out = Path(out)
    if out.exists():
        raise FileExistsError("Preserve existing audit; use a new explicit output directory")
    original_audit = read_json(REPORT / "source_audit.json")
    source_map = {r["period"]: r for r in original_audit["sources"]}
    parts, sources = [], []
    for period in PERIODS:
        path, info = discover_source(ROOT / "data" / period, "process")
        digest = sha256(path)
        if digest != source_map[period]["sha256"]:
            raise ValueError("Raw source changed since original audit; review rather than overwrite")
        raw_count, good_count, status = 0, 0, Counter()
        for raw in _read_source(path, info, "process", 200_000):
            f = raw.rename(columns=canonical_column)
            raw_count += len(f)
            status.update(f.status.fillna("MISSING").value_counts().to_dict())
            start, end = parse_kst(f.startTime), parse_kst(f.endTime)
            duration = (end - start).dt.total_seconds() * 1000
            total = pd.to_numeric(f.totalCount, errors="coerce")
            success = pd.to_numeric(f.successCount, errors="coerce")
            error = pd.to_numeric(f.errorCount, errors="coerce")
            retry = pd.to_numeric(f.retryCount, errors="coerce")
            normal = (f.status.eq("S") & duration.ge(0) & total.ge(0) & total.mod(1).eq(0)
                      & success.eq(total) & error.eq(0) & retry.ge(0) & retry.mod(1).eq(0))
            good_count += int(normal.sum())
            log_duration = np.log1p(duration.where(normal, 0))
            small = pd.DataFrame({"timestamp": start.dt.floor("5min"),
                "adapterType": f.adapterType.fillna("MISSING"), "processId": f.processId.fillna("MISSING"),
                "received_rows": 1, "normal_rows": normal.astype(int),
                "log_duration_sum": log_duration, "log_duration_sq_sum": log_duration ** 2,
                "log_work_sum": np.log1p(total.where(normal, 0))})
            small = small.dropna(subset=["timestamp"])
            small = small.groupby(["timestamp", "adapterType", "processId"], observed=True)[VALUES].sum().reset_index()
            small["source_period"] = period
            parts.append(small)
        if raw_count != source_map[period]["raw_rows"] or dict(status) != source_map[period]["status_counts"]:
            raise AssertionError("Raw source row/status counts do not match preserved audit")
        sources.append({"period": period, "path": str(path.relative_to(ROOT)), "sha256": digest,
                        "raw_rows": raw_count, "normal_candidates": good_count})
        print("Raw five-minute source", sources[-1], flush=True)
    fine = pd.concat(parts, ignore_index=True).groupby(KEYS, observed=True)[VALUES].sum().reset_index()
    support = support_table(fine)
    # Independent reconciliation against previously frozen hourly observations.
    hourly = pd.read_csv(REPORT / "observed_hourly.csv.gz", parse_dates=["hour"])
    hourly["hour"] = hourly.hour.astype("datetime64[ns]")
    compare = fine.assign(hour=fine.timestamp.dt.floor("h")).groupby(
        ["source_period", "hour", "adapterType", "processId"], observed=True)[VALUES].sum().sort_index()
    old = hourly.set_index(["source_period", "hour", "adapterType", "processId"]).sort_index()
    # CSV re-import may use a different pandas StringDtype NA sentinel; compare
    # the complete identifier/time tuples exactly rather than storage metadata.
    np.testing.assert_array_equal(compare.index.to_numpy(), old.index.to_numpy())
    for new, prior in (("received_rows", "all_rows"), ("normal_rows", "normal_rows"),
                       ("log_duration_sum", "log_duration_sum"), ("log_work_sum", "log_work_sum")):
        if new in ("received_rows", "normal_rows"):
            np.testing.assert_array_equal(compare[new], old[prior])
        else:
            np.testing.assert_allclose(compare[new], old[prior], rtol=1e-12, atol=1e-8)
    source_fit = read_json(ROOT / "data/evaluation_long_term/source_distribution.json")
    before_cutoff = fine.source_period.eq(source_fit["source_period"]) & fine.timestamp.lt(pd.Timestamp(source_fit["cutoff_exclusive"]))
    fit_support = support_table(fine.loc[before_cutoff])
    out.mkdir(parents=True)
    payload = {"timestamp_ns": fine.timestamp.to_numpy("datetime64[ns]").astype(np.int64)}
    for name in ("source_period", "adapterType", "processId"):
        payload[name] = fine[name].astype(str).to_numpy(dtype=str)
    for name in VALUES:
        payload[name] = fine[name].to_numpy()
    np.savez_compressed(out / "observed_five_minute.npz", **payload)
    result = {"sources": sources, "group_bin_rows": len(fine),
        "five_minute_reaggregates_match_preserved_hourly_audit": True,
        "raw_period_files_modified": False, "old_benchmark_modified": False,
        "within_hour_values_observed_not_interpolated": True,
        "normal_candidates_are_not_verified_normal": True,
        "collection_health_unknown": True, "zero_filling_performed": False,
        "positive_bins_do_not_prove_complete_collection": True,
        "fully_occupied_window_counts_are_support_not_collector_certification": True,
        "normal_generator_fit_source_period": source_fit["source_period"],
        "normal_generator_fit_cutoff_exclusive": source_fit["cutoff_exclusive"],
        "fit_support": fit_support, "support": support,
        "intermediate_sha256": sha256(out / "observed_five_minute.npz"),
        "audit_code_sha256": sha256(__file__)}
    write_json(out / "source_audit.json", result)
    print("Fine-resolution support", {"group_bin_rows": len(fine), "series": len(support),
        "fit_series": len(fit_support), "fit_max_run_hours": max(r["longest_occupied_run_hours"] for r in fit_support),
        "reconciled": True}, flush=True)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=OUT)
    run(p.parse_args().output)
