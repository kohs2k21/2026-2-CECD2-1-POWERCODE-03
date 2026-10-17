"""Detector-independent generation checks and compact, auditable exports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from . import scenarios as sc
from .dataset import (PROTOCOL, Dataset, _calendar, _mask, _normal, connected_incidents,
                      frame_hash, generate, overlap_segments)


def _json(value):
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, default=_json, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _finite(value):
    return float(value) if np.isfinite(value) else None


def _statistics(frame):
    count = frame["count"].to_numpy()
    duration = frame.duration_ms.to_numpy()
    valid = np.isfinite(count) & np.isfinite(duration)
    x, y = count[valid], duration[valid]
    correlation = np.corrcoef(x, y)[0, 1] if len(x) >= 3 and x.std() and y.std() else np.nan
    spearman = pd.Series(x).corr(pd.Series(y), method="spearman") if len(x) >= 3 and x.std() and y.std() else np.nan
    acf = pd.Series(y).autocorr() if len(y) >= 3 and y.std() else np.nan
    return dict(n_pairs=len(x), count_mean=_finite(np.nanmean(count)), duration_mean=_finite(np.nanmean(duration)) if np.isfinite(duration).any() else None,
                duration_cv=_finite(np.std(y) / np.mean(y)) if len(y) else None,
                duration_q95=_finite(np.quantile(y, .95)) if len(y) else None,
                pearson=_finite(correlation), spearman=_finite(spearman), duration_acf1=_finite(acf))


def check_dataset(item: Dataset):
    """Keep generation failure separate from detector support or missed detections."""
    frame = item.frame
    seed, split = item.manifest["seed"], item.manifest["split"]
    base = _normal(seed, split)
    tests = {}
    tests["dense_two_groups"] = all(len(g) == sc.DAYS[split] * 1440 + (1442 if split in ("selection", "final") else 0)
                                    and pd.DatetimeIndex(g.index).to_series().diff().dropna().eq(pd.Timedelta(minutes=1)).all()
                                    for _, g in frame.groupby("group"))
    observed = frame.source_complete.to_numpy()
    count, total, error = (frame[c].to_numpy() for c in ("count", "total_count", "error_count"))
    tests["count_total_contract"] = bool(np.array_equal(count[observed], total[observed], equal_nan=True))
    tests["integer_nonnegative_counts"] = bool(all(np.all(np.isfinite(v[observed]) & (v[observed] >= 0) & (v[observed] == np.rint(v[observed]))) for v in (count, total, error)))
    tests["error_subset"] = bool(np.all(error[observed] <= total[observed]))
    tests["zero_has_no_duration"] = bool(frame.loc[frame["count"].eq(0), "duration_ms"].isna().all())
    tests["missing_is_not_zero"] = bool(frame.loc[~frame.source_complete, ["count", "total_count", "duration_ms", "error_count"]].isna().all().all())
    tests["available_not_before_complete"] = bool((frame.available_at >= frame.index).all())
    tests["no_ground_truth_in_source"] = not bool(set(PROTOCOL["truth_forbidden_in_features"]) & set(frame.columns))
    tests["snapshot_matches"] = frame_hash(frame) == item.manifest["source_hash"]
    tests["positive_events"] = bool((item.events.end > item.events.start).all())
    diagnostics, cases = [], []
    changed_union = np.zeros(len(frame), bool)
    for case in item.manifest["definitions"]:
        local_tests = {}
        subtype = case["subtype"]
        intervals = case["intervals"]
        expected_duration = sum((end - start).total_seconds() / 60 for start, end in intervals)
        mask = np.zeros(len(frame), bool)
        for start, end in intervals:
            mask |= _mask(frame, case["group"], start, end)
        changed_union |= mask
        before, after = base.loc[mask], frame.loc[mask]
        local_tests["active_rows_exact"] = int(mask.sum()) == expected_duration
        report = dict(case_id=case["case_id"], subtype=subtype, group=case["group"], before=_statistics(before), after=_statistics(after))
        if subtype in sc.registry():
            definition = sc.registry()[subtype]
            local_tests["source_quality_preserved"] = before[["source_complete", "activity_expected", "category_supported", "available_at"]].equals(after[["source_complete", "activity_expected", "category_supported", "available_at"]]) or item.manifest["suite"] == "quality_cross"
            if subtype[0] in "BZG":
                # Count->total/error coherence and zero-duration support are checked globally.
                factors = []
                for start, end in intervals:
                    length = int((end - start).total_seconds() / 60)
                    if subtype == "G01":
                        factors.extend(np.linspace(1, 2, length))
                    elif subtype == "G02":
                        factors.extend(np.geomspace(1, 4, length))
                    elif subtype == "G03":
                        factors.extend(np.repeat([1.5, 2., 2.5], 60))
                    else:
                        factors.extend([definition["parameters"]["factor"]] * length)
                # The mask is one group's chronological source order.
                target = np.rint(before["count"].to_numpy() * factors)
                local_tests["count_formula"] = bool(np.array_equal(target, after["count"].to_numpy(), equal_nan=True))
                report["expected_factor_first_last"] = [float(factors[0]), float(factors[-1])]
            elif subtype in ("L01", "L02"):
                local_tests["duration_formula"] = bool(np.allclose(after.duration_ms, before.duration_ms * definition["parameters"]["factor"], equal_nan=True))
            elif subtype == "L03":
                old, new = before.duration_ms.mean(), after.duration_ms.mean()
                local_tests["mean_preserved"] = bool(abs(new - old) / old <= PROTOCOL["generation_tolerances"]["mean_preservation_relative"])
                local_tests["tail_cv_increased"] = report["after"]["duration_cv"] >= report["before"]["duration_cv"] + PROTOCOL["generation_tolerances"]["tail_cv_increase_min"]
            elif subtype in ("R01", "R02"):
                local_tests["exact_duration_marginal_preserved"] = bool(np.array_equal(np.sort(before.duration_ms), np.sort(after.duration_ms), equal_nan=True))
                r = report["after"]["pearson"]
                local_tests["target_relationship_realized"] = r is not None and (abs(r) <= .3 if subtype == "R01" else r <= -.4)
                report["serial_change_is_not_causal_proof"] = True
            elif subtype == "R03":
                pairs = before["count"].notna() & before.duration_ms.notna()
                x, y0, y1 = before.loc[pairs, "count"], before.loc[pairs, "duration_ms"], after.loc[pairs, "duration_ms"]
                slopes = [np.polyfit(x, y, 1)[0] for y in (y0, y1)]
                report["conditional_slope_before_after"] = [float(s) for s in slopes]
                local_tests["conditional_slope_increased"] = bool(slopes[1] > slopes[0])
                _, expected = _calendar(pd.DatetimeIndex(before.bin_start), split, case["group"])
                target = np.maximum(1., before.duration_ms.to_numpy() + 45 * .8 * (before["count"].to_numpy() - expected) / (.27 * expected))
                local_tests["conditional_slope_formula"] = bool(np.allclose(target, after.duration_ms, equal_nan=True))
            elif subtype[0] == "E":
                n, k = after.total_count.sum(), after.error_count.sum()
                p = definition["parameters"]["probability"]
                tolerance = max(.03, 4 * np.sqrt(p * (1 - p) / n)) if n else None
                report.update(injected_probability=p, realized_probability=float(k / n) if n else None, total_exposure=int(n), low_support=n < 30)
                local_tests["error_rate_realized_or_low_support"] = bool(n < 30 or abs(k / n - p) <= tolerance)
        if item.manifest["suite"] == "composite":
            # Other selected components can intentionally change the same support.
            local_tests.pop("duration_formula", None)
        if item.manifest["suite"] in ("relation_marginal_only", "relation_serial_only"):
            local_tests.pop("target_relationship_realized", None)
        diagnostics.append(report)
        cases.append(dict(case_id=case["case_id"], subtype=subtype, tests=local_tests, generation_valid=bool(all(local_tests.values()))))
    if item.manifest["suite"] not in ("relation_marginal_only", "relation_serial_only"):
        tests["outside_case_intervals_unchanged"] = frame.loc[~changed_union].equals(base.loc[~changed_union])
    # Validate washout separately for each source timeline and group.
    if item.manifest["suite"] in ("main", "error"):
        for group in sc.GROUPS:
            definitions = sorted([c for c in item.manifest["definitions"] if c["group"] == group], key=lambda c: c["intervals"][0][0])
            tests["24h_washout_" + group] = all((b["intervals"][0][0] - a["intervals"][-1][1]).total_seconds() >= 1440 * 60 for a, b in zip(definitions, definitions[1:]))
    valid = bool(all(tests.values()) and all(c["generation_valid"] for c in cases))
    return dict(protocol_version=PROTOCOL["version"], source_hash=item.manifest["source_hash"], suite=item.manifest["suite"],
                generation_valid=valid, tests=tests, cases=cases, diagnostics=diagnostics,
                intended=item.manifest["intended_cases"], generated=item.manifest["generated_cases"],
                generation_valid_cases=(int(valid) if item.manifest["suite"] == "composite" else len({c["case_id"] for c in cases if c["generation_valid"]})), assessed=0,
                failure_policy=PROTOCOL["generation_failure_policy"])


def _csv(path, frame):
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object and any(isinstance(v, (list, tuple, dict)) for v in frame[column]):
            frame[column] = frame[column].map(lambda v: json.dumps(v, default=_json, ensure_ascii=False) if isinstance(v, (list, tuple, dict)) else v)
    frame.to_csv(path, index=True, compression={"method": "gzip", "compresslevel": 1} if str(path).endswith(".gz") else None)


def write_artifacts(item, directory, *, plots=True, shared_directory=None):
    """Normal source once per seed/split; suite source is a sparse row patch."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    base = _normal(item.manifest["seed"], item.manifest["split"])
    report = check_dataset(item)
    shared = Path(shared_directory) if shared_directory else directory.parent
    shared.mkdir(parents=True, exist_ok=True)
    base_path = shared / f"normal-{item.manifest['split']}-{item.manifest['seed']}-{item.manifest['normal_source_hash'][:12]}.npz"
    if not base_path.exists():
        _write_npz(base_path, base)
    changed = (item.frame != base) & ~(item.frame.isna() & base.isna())
    ids = np.flatnonzero(changed.any(axis=1).to_numpy())
    _write_npz(directory / "source-patch.npz", item.frame.iloc[ids], row_positions=ids)
    item.manifest["normal_source_file"] = os.path.relpath(base_path, directory).replace("\\", "/")
    write_json(directory / "definition.json", item.manifest)
    _csv(directory / "truth-events.csv", item.events)
    shared_quality = shared / f"quality-{frame_hash(item.quality)[:20]}.csv.gz"
    if not shared_quality.exists():
        _csv(shared_quality, item.quality)
    quality_path = directory / "quality.csv.gz"
    if not quality_path.exists():
        quality_path.hardlink_to(shared_quality)
    elif hashlib.sha256(quality_path.read_bytes()).digest() != hashlib.sha256(shared_quality.read_bytes()).digest():
        quality_path.unlink()
        quality_path.hardlink_to(shared_quality)
    _csv(directory / "paired-control.csv", base.iloc[ids])
    write_json(directory / "generation-check.json", report)
    if item.manifest["suite"] == "composite":
        _csv(directory / "truth-incidents.csv", connected_incidents(item))
        _csv(directory / "overlap-segments.csv", overlap_segments(item))
    if item.manifest["suite"] == "identifiability":
        alternative = item.events.copy()
        alternative["business_truth"] = "incident_abnormal"
        normal_alternative = alternative.copy()
        normal_alternative["business_truth"] = "announced_peak_normal"
        _csv(directory / "alternative-truths.csv", pd.concat([alternative, normal_alternative]))
    if item.manifest["suite"] in ("quality", "quality_cross"):
        _csv(directory / "raw-quality-ledger.csv", quality_raw_ledger(item))
    _case_artifacts(item, base, directory, report)
    if plots and len(item.events):
        _plots(item, base, directory)
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*") if p.is_file() and p.name != "hashes.json"}
    hashes[item.manifest["normal_source_file"]] = hashlib.sha256(base_path.read_bytes()).hexdigest()
    write_json(directory / "hashes.json", hashes)
    return report


def _write_npz(path, frame, **extra):
    arrays = dict(endpoint_ns=frame.index.as_unit("ns").asi8, group=frame.group.astype(str).to_numpy(dtype="U16"))
    for column in frame.columns:
        if column == "group":
            continue
        arrays[column] = frame[column].dt.as_unit("ns").array.asi8 if isinstance(frame[column].dtype, pd.DatetimeTZDtype) else frame[column].to_numpy()
    np.savez_compressed(path, **arrays, **extra)


def _read_npz(path):
    with np.load(path, allow_pickle=False) as arrays:
        index = pd.to_datetime(arrays["endpoint_ns"], unit="ns", utc=True)
        frame = pd.DataFrame(index=index)
        frame.index.name = "endpoint"
        for column in PROTOCOL["source_columns"]:
            values = arrays[column]
            frame[column] = pd.to_datetime(values, unit="ns", utc=True) if column in ("bin_start", "available_at") else values
        positions = arrays["row_positions"].copy() if "row_positions" in arrays else None
    return frame, positions


def restore_source(directory):
    """Reconstruct an exported immutable base+patch and verify the exact source hash."""
    directory = Path(directory)
    manifest = json.loads((directory / "definition.json").read_text(encoding="utf-8"))
    frame, _ = _read_npz(directory / manifest["normal_source_file"])
    patch, positions = _read_npz(directory / "source-patch.npz")
    for column in frame.columns:
        frame.iloc[positions, frame.columns.get_loc(column)] = patch[column].to_numpy()
    if frame_hash(frame) != manifest["source_hash"]:
        raise ValueError("Restored source hash does not match generation snapshot")
    return frame


def _case_artifacts(item, base, directory, report):
    grouped = {}
    for definition in item.manifest["definitions"]:
        grouped.setdefault(definition["case_id"], []).append(definition)
    for case_id, definitions in grouped.items():
        case_dir = directory / "cases" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        mask = np.zeros(len(item.frame), bool)
        for definition in definitions:
            start, end = definition["intervals"][0][0], definition["intervals"][-1][1]
            mask |= _mask(item.frame, definition["group"], start - pd.Timedelta(minutes=30), end + pd.Timedelta(minutes=60))
        write_json(case_dir / "definition.json", {"components": definitions, "source_hash": item.manifest["source_hash"],
                                                  "shared_full_source": "../../source-patch.npz", "control_base": item.manifest["normal_source_hash"],
                                                  "csv_scope": "local before/after window; complete evaluation source is shared base plus row patch"})
        _csv(case_dir / "source.csv", item.frame.loc[mask])
        _csv(case_dir / "truth-events.csv", item.events[item.events.case_id.eq(case_id)])
        _csv(case_dir / "quality.csv", item.quality.loc[mask])
        _csv(case_dir / "paired-control.csv", base.loc[mask])
        write_json(case_dir / "generation-check.json", {"cases": [c for c in report["cases"] if c["case_id"] == case_id],
                                                        "diagnostics": [c for c in report["diagnostics"] if c["case_id"] == case_id]})


def quality_raw_ledger(item):
    rows = []
    for case in item.manifest["definitions"]:
        if case["subtype"] != "Q03":
            continue
        start, end = case["intervals"][0]
        target = item.frame.loc[_mask(item.frame, case["group"], start, end)]
        for endpoint, row in target.iterrows():
            key = f"{row.group}/{endpoint.isoformat()}"
            for record, version, published, value in (("original", 1, endpoint, row["count"] + 1),
                                                       ("duplicate", 1, endpoint, row["count"] + 1),
                                                       ("revision", 2, endpoint + pd.Timedelta(minutes=10), row["count"])):
                rows.append(dict(source_key=key, record=record, version=version, available_at=published, count=value,
                                 canonical_count=row["count"], canonical_available_at=row.available_at))
    return pd.DataFrame(rows)


def _plots(item, base, directory):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # One compact panel per case, with all pulses and their normal gaps visible.
    for case_id, events in item.events.groupby("case_id"):
        start, end = events.start.min(), events.end.max()
        panels = []
        for group, group_events in events.groupby("group"):
            fields = ["count", "duration_ms"]
            if group_events.subtype.str.startswith("E").any():
                fields.append("error_rate")
            panels.extend((group, field, group_events) for field in fields)
        figure, axes = plt.subplots(len(panels), 1, figsize=(8, max(3.7, 1.65 * len(panels))), sharex=True, squeeze=False)
        axes = axes[:, 0]
        for axis, (group, field, group_events) in zip(axes, panels):
            ids = _mask(item.frame, group, start - pd.Timedelta(minutes=30), end + pd.Timedelta(minutes=60))
            x = item.frame.loc[ids].bin_start
            before, after = base.loc[ids], item.frame.loc[ids]
            old = before.error_count / before.total_count.replace(0, np.nan) if field == "error_rate" else before[field]
            new = after.error_count / after.total_count.replace(0, np.nan) if field == "error_rate" else after[field]
            axis.plot(x, old, label="same-source normal", linewidth=.8)
            axis.plot(x, new, label="intervention", linewidth=.8)
            for event in group_events.itertuples():
                axis.axvspan(event.start, event.end, alpha=.12, color="red")
            axis.set_ylabel(group + "\n" + field, fontsize=7)
        axes[0].legend(fontsize=6)
        axes[0].set_title(case_id, fontsize=8)
        axes[-1].set_xlabel("UTC")
        figure.autofmt_xdate()
        figure.tight_layout()
        figure.savefig(directory / f"{case_id}.png", dpi=100)
        case_dir = directory / "cases" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        figure.savefig(case_dir / "before-after.png", dpi=100)
        plt.close(figure)
