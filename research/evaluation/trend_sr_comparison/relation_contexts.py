"""Separate AR/IID transfer controls without changing the accepted main source.

The original duration circular shift is retained as cross_alignment_shift.
serial_only jointly permutes observed tuples within each KST calendar hour.
The IID context receives the original AR-trained reference/cut at replay time;
this is transfer stress, not an IID-specific fit or a pure AR causal effect.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from . import dataset, scenarios
from .generation import _csv, _read_npz, _statistics, _write_npz, write_json

VERSION = "trend-sr-relation-contexts-v1"
CONTEXTS = {"AR": .35, "IID": 0.}
ROLES = ("no_intervention", "intervention", "marginal_only", "serial_only", "cross_alignment_shift")
FIELDS = ("count", "duration_ms", "total_count", "error_count")
CONTRACT = {
    "version": VERSION, "contexts": CONTEXTS, "roles": list(ROLES),
    "context_cases_per_seed": 8, "context_cases_total": 40,
    "primary_control_arms_total": 160, "cross_alignment_arms_total": 40, "all_arms_total": 200,
    "main_and_error_denominators_changed": False,
    "serial_only": "joint tuple permutation within target KST hour; simultaneous joint/marginal/hour multiset exact, autocorrelation measured",
    "cross_alignment_shift": "legacy serial_only alias; duration circular shift61, own ACF approximately preserved, cross alignment changed",
    "marginal_only": "exact normal source for rank-preserving R01/R02; identical control is not an independent sample",
    "IID_interpretation": "independent latent/noise coefficient0, AR-frozen fit/cut transfer stress; not IID-specific calibration or pure AR causality",
    "tolerances": {"weak_abs_pearson_max": .3, "reversal_pearson_max": -.4, "joint_correlation_absolute": 1e-12},
    "ACF": "measured diagnostic; no post-hoc permutation reselection or guaranteed finite-sample ACF effect",
}


def legacy_controls(seed, split="selection", *, authorize_final=False):
    """Read the accepted legacy snapshots with the corrected operational name."""
    controls = dataset.relation_controls(seed, split, authorize_final=authorize_final)
    shifted = controls.pop("serial_only")
    shifted.manifest = {**shifted.manifest, "legacy_role": "serial_only",
                        "corrected_role": "cross_alignment_shift",
                        "role_interpretation": CONTRACT["cross_alignment_shift"]}
    controls["cross_alignment_shift"] = shifted
    return controls


@lru_cache(maxsize=6)
def _normal_context(seed, split, context):
    if context not in CONTEXTS:
        raise ValueError("Context must be AR or IID")
    original = dataset._normal(seed, split)
    if context == "AR":
        return original
    frame = original.copy(deep=True)
    for group in scenarios.GROUPS:
        ids = np.flatnonzero(frame.group.eq(group).to_numpy())
        starts = pd.DatetimeIndex(frame.iloc[ids].bin_start)
        active, expected = dataset._calendar(starts, split, group)
        rng = dataset._rng(seed, split, f"{VERSION}/IID/normal/{group}")
        latent = dataset._ar(rng, len(ids), coefficient=0.)
        noise = dataset._ar(rng, len(ids), coefficient=0.)
        intensity = expected * np.exp(.24 * latent - .5 * .24 ** 2)
        count = rng.poisson(intensity).astype(float)
        count[~active] = 0
        z = (count - expected) / np.maximum(.27 * expected, 1.)
        duration = np.maximum(20., 230 + 45 * (.8 * z + .6 * noise))
        tails = rng.random(len(ids)) < .002
        duration[tails] *= rng.lognormal(mean=.7, sigma=.3, size=tails.sum())
        duration[count == 0] = np.nan
        errors = rng.binomial(count.astype(int), .01).astype(float)
        for column, values in (("count", count), ("total_count", count), ("duration_ms", duration), ("error_count", errors)):
            frame.iloc[ids, frame.columns.get_loc(column)] = values
    return frame


def case_specs(seed, split="selection"):
    return [{**case, "context": context, "context_case_id": f"RC-{context}-{case['case_id']}"}
            for context in CONTEXTS for case in scenarios.schedule(seed, split)
            if case["subtype"] in ("R01", "R02")]


def _intervene(frame, case, seed, split, context):
    start, end = case["intervals"][0]
    ids = np.flatnonzero(dataset._mask(frame, case["group"], start, end))
    count = frame.iloc[ids]["count"].to_numpy()
    duration = frame.iloc[ids].duration_ms.to_numpy(copy=True)
    valid = np.isfinite(duration) & (count > 0)
    x = count[valid]
    z = (x - x.mean()) / x.std()
    rng = dataset._rng(seed, split, f"{VERSION}/{context}/{case['context_case_id']}/intervention")
    noise = dataset._ar(rng, len(x), coefficient=CONTEXTS[context])
    noise -= noise @ z / (z @ z) * z
    noise = (noise - noise.mean()) / noise.std()
    rho = scenarios.registry()[case["subtype"]]["parameters"]["rho_after"]
    target = rho * z + np.sqrt(1 - rho * rho) * noise
    mapped = np.empty(len(x))
    mapped[np.argsort(target)] = np.sort(duration[valid])
    duration[valid] = mapped
    frame.iloc[ids, frame.columns.get_loc("duration_ms")] = duration


def _joint_permutation(frame, case, seed, split, context):
    start, end = case["intervals"][0]
    ids = np.flatnonzero(dataset._mask(frame, case["group"], start, end))
    local_hour = pd.DatetimeIndex(frame.iloc[ids].bin_start).tz_convert("Asia/Seoul").floor("h")
    columns = [frame.columns.get_loc(column) for column in FIELDS]
    for hour in local_hour.unique():
        selected = ids[local_hour == hour]
        rng = dataset._rng(seed, split, f"{VERSION}/{context}/{case['context_case_id']}/serial/{hour.isoformat()}")
        shuffled = rng.permutation(selected)
        frame.iloc[selected, columns] = frame.iloc[shuffled, columns].to_numpy(copy=True)


def generate_context_case(seed, split, context, case_id, role, *, authorize_final=False):
    dataset._gate(seed, split, authorize_final)
    if role not in ROLES:
        raise ValueError("Unregistered relation control role")
    case = next((c for c in case_specs(seed, split) if c["context"] == context and c["context_case_id"] == case_id), None)
    if case is None:
        raise ValueError("Unknown context case")
    base = _normal_context(seed, split, context)
    frame = base.copy(deep=True)
    if role == "intervention":
        _intervene(frame, case, seed, split, context)
    elif role == "serial_only":
        _joint_permutation(frame, case, seed, split, context)
    elif role == "cross_alignment_shift":
        start, end = case["intervals"][0]
        ids = np.flatnonzero(dataset._mask(frame, case["group"], start, end))
        frame.iloc[ids, frame.columns.get_loc("duration_ms")] = np.roll(frame.iloc[ids].duration_ms.to_numpy(), 61)
    definition = {**case, "case_id": case_id, "variant_id": f"{VERSION}/{context}/{role}"}
    events = [dataset._event(definition, case["intervals"][0], seed)] if role == "intervention" else []
    item = dataset._finish(frame, events, seed, split, "relation_context", [definition], {
        "context_contract": CONTRACT, "context": context, "context_coefficient": CONTEXTS[context],
        "case_id": case_id, "role": role, "normal_source_hash": dataset.frame_hash(base),
        "main_normal_source_hash": dataset.frame_hash(dataset._normal(seed, split)),
        "context_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_lineage": {"base_namespace": "original-normal" if context == "AR" else f"{VERSION}/IID/normal/group",
                           "paired_source": "same context normal source, same seed/split", "no_detector_or_truth_in_source": True},
        "legacy_role_alias": "serial_only -> cross_alignment_shift" if role == "cross_alignment_shift" else None,
        "normalcy_limit": "statistical counterfactual control; business normalcy and pure causal separation are not guaranteed"})
    item.manifest["analysis_target"] = dataset._event(definition, case["intervals"][0], seed)
    item.manifest["latent_diagnostics"] = latent_diagnostics(seed, split, context)
    return item


@lru_cache(maxsize=12)
def latent_diagnostics(seed, split, context):
    result = {}
    for group in scenarios.GROUPS:
        count = len(dataset._normal(seed, split).query("group == @group"))
        namespace = "normal/" + group if context == "AR" else f"{VERSION}/IID/normal/{group}"
        rng = dataset._rng(seed, split, namespace)
        latent = dataset._ar(rng, count, coefficient=CONTEXTS[context])
        noise = dataset._ar(rng, count, coefficient=CONTEXTS[context])
        result[group] = dict(coefficient=CONTEXTS[context], samples=count,
                             latent_acf1=float(pd.Series(latent).autocorr()), noise_acf1=float(pd.Series(noise).autocorr()),
                             latent_sha256=hashlib.sha256(latent.tobytes()).hexdigest(),
                             noise_sha256=hashlib.sha256(noise.tobytes()).hexdigest())
    return result


def check_context_case(item):
    manifest = item.manifest
    base = _normal_context(manifest["seed"], manifest["split"], manifest["context"])
    case = manifest["definitions"][0]
    start, end = case["intervals"][0]
    mask = dataset._mask(item.frame, case["group"], start, end)
    before, after = base.loc[mask], item.frame.loc[mask]
    old, new = _statistics(before), _statistics(after)
    tests = {"outside_target_exact": item.frame.loc[~mask].equals(base.loc[~mask]),
             "metadata_exact": item.frame[["bin_start", "group", "source_complete", "activity_expected", "category_supported", "available_at"]].equals(base[["bin_start", "group", "source_complete", "activity_expected", "category_supported", "available_at"]]),
             "count_total": bool(item.frame["count"].equals(item.frame.total_count)),
             "error_subset": bool((item.frame.error_count <= item.frame.total_count).all()),
             "zero_no_duration": bool(item.frame.loc[item.frame["count"].eq(0), "duration_ms"].isna().all()),
             "source_hash": dataset.frame_hash(item.frame) == manifest["source_hash"],
             "duration_marginal_exact": bool(np.array_equal(np.sort(before.duration_ms), np.sort(after.duration_ms), equal_nan=True))}
    role = manifest["role"]
    if role in ("no_intervention", "marginal_only"):
        tests["exact_identity_control"] = item.frame.equals(base)
    elif role == "intervention":
        r = new["pearson"]
        tests["target_correlation"] = r is not None and (abs(r) <= .3 if case["subtype"] == "R01" else r <= -.4)
    elif role == "serial_only":
        tests["pearson_exact"] = abs(old["pearson"] - new["pearson"]) <= 1e-12
        tests["spearman_exact"] = abs(old["spearman"] - new["spearman"]) <= 1e-12
        hours = before.bin_start.dt.tz_convert("Asia/Seoul").dt.floor("h")
        for hour in hours.unique():
            positions = np.flatnonzero(hours.eq(hour).to_numpy())
            left = np.sort(pd.util.hash_pandas_object(before.iloc[positions][list(FIELDS)], index=False).to_numpy())
            right = np.sort(pd.util.hash_pandas_object(after.iloc[positions][list(FIELDS)], index=False).to_numpy())
            tests["hour_joint_multiset_" + hour.isoformat()] = bool(np.array_equal(left, right))
        tests["actual_serial_reordering"] = not before[list(FIELDS)].equals(after[list(FIELDS)])
    elif role == "cross_alignment_shift":
        tests["legacy_shift_exact"] = np.array_equal(after.duration_ms, np.roll(before.duration_ms.to_numpy(), 61), equal_nan=True)
    if manifest["context"] == "AR":
        tests["accepted_AR_base_exact"] = dataset.frame_hash(base) == manifest["main_normal_source_hash"]
    return dict(version=VERSION, case_id=manifest["case_id"], context=manifest["context"], role=role,
                intended=1, generated=1, assessed=0, generation_valid=bool(all(tests.values())), tests=tests,
                before=old, after=new,
                count_acf1_before=float(before["count"].autocorr()), count_acf1_after=float(after["count"].autocorr()),
                duration_acf1_change=new["duration_acf1"] - old["duration_acf1"],
                interpretation=CONTRACT["IID_interpretation"] if manifest["context"] == "IID" else "accepted original AR normal source")


def export_context_case(item, directory, shared_directory):
    directory, shared = Path(directory), Path(shared_directory)
    directory.mkdir(parents=True, exist_ok=True)
    shared.mkdir(parents=True, exist_ok=True)
    base = _normal_context(item.manifest["seed"], item.manifest["split"], item.manifest["context"])
    base_path = shared / f"normal-{item.manifest['seed']}-{item.manifest['context']}-{item.manifest['normal_source_hash'][:16]}.npz"
    if not base_path.exists():
        _write_npz(base_path, base)
    changed = (item.frame != base) & ~(item.frame.isna() & base.isna())
    positions = np.flatnonzero(changed.any(axis=1).to_numpy())
    _write_npz(directory / "source-patch.npz", item.frame.iloc[positions], row_positions=positions)
    item.manifest["context_normal_file"] = os.path.relpath(base_path, directory).replace("\\", "/")
    write_json(directory / "definition.json", item.manifest)
    report = check_context_case(item)
    write_json(directory / "generation-check.json", report)
    _csv(directory / "truth-events.csv", item.events)
    _csv(directory / "analysis-target.csv", pd.DataFrame([item.manifest["analysis_target"]]))
    case = item.manifest["definitions"][0]
    start, end = case["intervals"][0]
    mask = dataset._mask(item.frame, case["group"], start-pd.Timedelta(minutes=30), end+pd.Timedelta(minutes=60))
    _csv(directory / "source-window.csv", item.frame.loc[mask])
    _csv(directory / "quality.csv", item.quality.loc[mask])
    _csv(directory / "paired-control.csv", base.loc[mask])
    _plot(item, base, mask, directory, report)
    restore_context_source(directory)
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*") if p.is_file() and p.name != "hashes.json"}
    hashes[item.manifest["context_normal_file"]] = hashlib.sha256(base_path.read_bytes()).hexdigest()
    write_json(directory / "hashes.json", hashes)
    return report


def restore_context_source(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "definition.json").read_text(encoding="utf-8"))
    frame, _ = _read_npz(directory / manifest["context_normal_file"])
    patch, positions = _read_npz(directory / "source-patch.npz")
    for column in frame:
        frame.iloc[positions, frame.columns.get_loc(column)] = patch[column].to_numpy()
    if dataset.frame_hash(frame) != manifest["source_hash"]:
        raise ValueError("Relation context source restoration hash mismatch")
    return frame


def _plot(item, base, mask, directory, report):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(3, 1, figsize=(7, 6))
    for axis, column in zip(axes[:2], ("count", "duration_ms")):
        axis.plot(base.loc[mask].bin_start, base.loc[mask, column], linewidth=.7, label="context normal")
        axis.plot(item.frame.loc[mask].bin_start, item.frame.loc[mask, column], linewidth=.7, label=item.manifest["role"])
        axis.set_ylabel(column)
    event = item.manifest["analysis_target"]
    active = dataset._mask(item.frame, event["group"], event["start"], event["end"])
    axes[2].scatter(base.loc[active, "count"], base.loc[active, "duration_ms"], s=4, label="context normal")
    axes[2].scatter(item.frame.loc[active, "count"], item.frame.loc[active, "duration_ms"], s=4, alpha=.5, label=item.manifest["role"])
    axes[2].set_xlabel("count; top time axes UTC")
    axes[2].set_ylabel("duration_ms")
    axes[0].set_title(f"{item.manifest['context']} {event['subtype']} {event['group']}: {item.manifest['role']}", fontsize=9)
    axes[0].legend(fontsize=6)
    figure.tight_layout()
    figure.savefig(directory / "before-after.png", dpi=100)
    plt.close(figure)
