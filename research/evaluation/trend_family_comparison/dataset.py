"""Fresh, gated synthetic splits using the pinned legacy source generator.

No main holdout is constructed by loading protocol/train/calibration. The
explicit main-blind flag belongs only in a caller that has checked its lock.
Counterfactual labels and source metadata remain separate from observable X.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import types

import numpy as np
import pandas as pd

from research.evaluation.long_term_trend import dataset as legacy

ROOT = Path(__file__).resolve().parents[3]
MAIN_SEEDS = [101, 137, 173]
FIXTURE_SEED = 9102
STARTS = {"train": "2026-05-04T00:00:00Z", "calibration": "2026-05-18T00:00:00Z",
          "blind": "2026-07-06T00:00:00Z"}


def protocol_hash(protocol):
    """Hash the actual passed protocol, including fixture overrides."""
    return hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _validate_protocol(protocol):
    kind = protocol.get("run_kind")
    if kind not in ("main", "fixture"):
        raise ValueError("Declare main or isolated fixture protocol")
    expected_seeds = MAIN_SEEDS if kind == "main" else [FIXTURE_SEED]
    expected_days = {"train": 7, "calibration": 7, "blind": 14} if kind == "main" else {
        "train": 2, "calibration": 2, "blind": 14}
    if protocol["seeds"] != expected_seeds or protocol["split_days"] != expected_days:
        raise ValueError("Use the frozen main seeds/days or fixture_protocol()")
    if protocol["scored_starts_utc"] != STARTS or protocol["allowed_splits"] != list(STARTS):
        raise ValueError("Unexpected split dates or names")
    if (protocol["window_hours"], protocol["max_window_minutes"],
        protocol["watermark_lag_minutes"], protocol["prefix_minutes"]) != ([1], 720, 2, 722):
        raise ValueError("Keep W1 and the original common 720-minute availability contract")


def load_protocol():
    protocol = json.loads(Path(__file__).with_name("protocol.json").read_text(encoding="utf-8"))
    _validate_protocol(protocol)
    return protocol


def fixture_protocol():
    """Only seed9102; two normal days are a functional check, not an evaluation."""
    protocol = copy.deepcopy(load_protocol())
    protocol.update(run_kind="fixture", seeds=[FIXTURE_SEED],
                    split_days={"train": 2, "calibration": 2, "blind": 14})
    _validate_protocol(protocol)
    return protocol


def verify_legacy_sources(protocol):
    for relative, expected in protocol["legacy_source_sha256"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Pinned legacy source changed: {relative}")


def _without_relation_reassignment(seed, name, protocol):
    # This statement consumes no RNG. Every other generator statement is intact.
    source = Path(legacy.__file__).read_text(encoding="utf-8")
    needle = 'frame.loc[order_ids, "duration_ms"] = np.sort(frame.loc[local, "duration_ms"].to_numpy())[::-1]'
    if source.count(needle) != 1:
        raise ValueError("Pinned relation assignment must match exactly once")
    module = types.ModuleType("trend_family_no_relation_reassignment")
    module.__file__ = legacy.__file__
    exec(compile(source.replace(needle, "pass"), "<pinned-same-rng-counterfactual>", "exec"), module.__dict__)
    return module.load_split(seed, name, protocol)


def _check_counterfactual(original, control):
    a, b = original["stream"], control["stream"]
    if not a.drop(columns="duration_ms").equals(b.drop(columns="duration_ms")):
        raise ValueError("Counterfactual changed another source field")
    if not original["events"].drop(columns="relation_marginal_after_sha256").equals(
            control["events"].drop(columns="relation_marginal_after_sha256")):
        raise ValueError("Counterfactual changed event schedule or labels")
    changed = np.zeros(len(a), bool)
    for event in original["events"].query("scenario == 'relation_change'").itertuples():
        mask = (a.group == event.group) & (a.bin_start >= event.start) & (a.bin_start < event.end)
        changed |= mask.to_numpy()
        for _, hour in a.loc[mask].groupby(a.loc[mask, "bin_start"].dt.floor("h")):
            if not np.array_equal(np.sort(hour.duration_ms), np.sort(b.loc[hour.index, "duration_ms"])):
                raise ValueError("Counterfactual changed hourly duration multiset")
    if not a.loc[~changed, "duration_ms"].equals(b.loc[~changed, "duration_ms"]):
        raise ValueError("Counterfactual changed duration outside relation targets")


def load_split(seed, name, protocol=None, *, relation_intervention=True, allow_main_blind=False):
    """Return legacy-shaped item plus original_events and control_windows.

    In the no-reassignment control, events excludes relation targets so that
    legacy normal_statistics includes their time in normal FP/occupancy. The
    original target labels remain in source metadata solely for provenance.
    """
    protocol = load_protocol() if protocol is None else protocol
    _validate_protocol(protocol)
    if name not in protocol["allowed_splits"] or seed not in protocol["seeds"]:
        raise ValueError("Use an explicitly declared seed and split")
    if name == "blind" and protocol["run_kind"] == "main" and not allow_main_blind:
        raise ValueError("Main blind blocked until root GO and a verified frozen lock")
    if not relation_intervention and name != "blind":
        raise ValueError("The no-reassignment control is a separate locked blind command")
    verify_legacy_sources(protocol)
    original = legacy.load_split(seed, name, protocol)
    item = original if relation_intervention else _without_relation_reassignment(seed, name, protocol)
    if not relation_intervention:
        _check_counterfactual(original, item)
    item["original_events"] = original["events"].copy()
    targets = original["events"].scenario.eq("relation_change")
    item["control_windows"] = original["events"].loc[targets].copy().reset_index(drop=True)
    item["control_windows"]["control_role"] = "relation_intervention_target" if relation_intervention else "no_relation_reassignment_control"
    if not relation_intervention:
        item["events"] = item["events"].loc[~targets].copy().reset_index(drop=True)
    item["manifest"].update(
        protocol_sha256=protocol_hash(protocol), run_kind=protocol["run_kind"],
        legacy_source_sha256=protocol["legacy_source_sha256"],
        relation_intervention=bool(relation_intervention),
        original_source_sha256=original["manifest"]["source_sha256"],
        intended_events=len(item["events"]), original_intended_events=len(item["original_events"]),
        relation_control_windows=len(item["control_windows"]),
        counterfactual_invariants_verified=not relation_intervention,
        normal_truth_authority="events; never filter CF by source normal_confirmed",
        real_source_used=False,
    )
    return item
