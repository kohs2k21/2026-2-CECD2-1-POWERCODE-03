"""Locked-candidate stress replay with paired clocks and one-to-one union credit.

This is a separate, conditional-exposure experiment. Each arm restarts from
the same 1442-minute normal prefix; it is not the state or normal-budget test
of the full 42-day replay. A one-day recovery horizon can remain censored.
"""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from . import dataset, metrics, relation_contexts, scenarios
from .generation import _csv, _write_npz, check_dataset, write_json

CLOCK_CONTRACT = {
    "version": "trend-sr-stress-clock-v1",
    "source_start": "anchor - 1472 minutes",
    "scored_start": "anchor - 30 minutes",
    "scored_end": "last potential component end + 1440 minutes",
    "normal_prefix_minutes": 1442,
    "groups": "both groups, identical endpoints in every paired arm",
    "state": "independent replay, identical prefix initialization; no cross-arm mutable state",
    "interpretation": "conditional stress exposure; not 42-day state or normal-budget equivalence",
    "recovery": "right-censored at horizon; W1440+watermark+cadence can extend beyond horizon",
    "compound_intended_cases": 80,
    "compound_intended_arms": 360,
    "invalid_requests_per_seed": 3,
    "quality_cross_cases_per_seed": 2,
    "relation_context_contract": relation_contexts.CONTRACT,
}


def crop(item, anchor, last_end):
    """Same clock for all arms, including arms lacking their last component."""
    source_begin = pd.Timestamp(anchor) - pd.Timedelta(minutes=1472)
    scored_begin = pd.Timestamp(anchor) - pd.Timedelta(minutes=30)
    scored_end = pd.Timestamp(last_end) + pd.Timedelta(minutes=1440)
    mask = item.frame.bin_start.ge(source_begin) & item.frame.bin_start.lt(scored_end)
    frame, quality = item.frame.loc[mask].copy(), item.quality.loc[mask].copy()
    expected = int((scored_end - source_begin).total_seconds() / 60)
    if any(len(frame[frame.group.eq(group)]) != expected for group in scenarios.GROUPS):
        raise ValueError("Stress clock extends outside the source snapshot")
    events = item.events[(item.events.end > scored_begin) & (item.events.start < scored_end)].copy()
    manifest = {**item.manifest, "parent_source_hash": item.manifest["source_hash"],
                "source_hash": dataset.frame_hash(frame), "evaluation_start": scored_begin.isoformat(),
                "evaluation_end": scored_end.isoformat(), "stress_source_start": source_begin.isoformat(),
                "clock_contract": CLOCK_CONTRACT, "source_rows": len(frame)}
    events.loc[:, "snapshot_hash"] = manifest["source_hash"]
    frame.attrs.update(evaluation_start=manifest["evaluation_start"], evaluation_end=manifest["evaluation_end"])
    return dataset.Dataset(frame, events, quality, manifest)


def _ticks(frame):
    result = frame.copy(deep=False)
    required = ("group", "decision_tick", "prediction", "source_eligible", "common_eligible", "score_supported")
    if any(column not in result for column in required):
        raise ValueError("Scorer did not provide the locked minute-prediction contract")
    result["decision_tick"] = pd.to_datetime(result.decision_tick, utc=True)
    if result.duplicated(["group", "decision_tick"]).any():
        raise ValueError("Duplicate stress prediction clock")
    return result


def _union_truth(item):
    incidents = dataset.connected_incidents(item)
    incidents["event_id"] = incidents.incident_id
    incidents["case_id"] = item.manifest.get("case_id", item.manifest["suite"])
    incidents["scenario_id"] = item.manifest.get("recipe", item.manifest["suite"])
    return incidents


def _segment_table(events, begin, end):
    """Potential component boundaries are shared, even in the zero arm."""
    begin, end = pd.Timestamp(begin), pd.Timestamp(end)
    if len(events) and ((events.start < begin).any() or (events.end > end).any()):
        raise ValueError("Potential components extend outside the shared scored clock")
    rows = []
    labels_by_event = {str(event.event_id): str(event.component_id) if getattr(event, "component_id", "") else str(event.event_id)
                       for event in events.itertuples()}
    bits = {label: 1 << i for i, label in enumerate(sorted(set(labels_by_event.values())))}
    for group in scenarios.GROUPS:
        local = events[events.group.eq(group)]
        boundaries = sorted({pd.Timestamp(begin), pd.Timestamp(end), *local.start.tolist(), *local.end.tolist()})
        for start, stop in zip(boundaries, boundaries[1:]):
            active = local[(local.start <= start) & (local.end > start)]
            ids = sorted({labels_by_event[str(event)] for event in active.event_id})
            rows.append(dict(group=group, start=start, end=stop,
                             potential_component_ids=ids,
                             potential_bitmask=sum(bits[label] for label in ids),
                             minutes=(stop - start).total_seconds() / 60))
    return pd.DataFrame(rows)


def _eligible(frame):
    return (frame.source_eligible.to_numpy(bool) & frame.common_eligible.to_numpy(bool)
            & frame.prediction.notna().to_numpy())


def assess(item, predictions, thresholds=None, potential_events=None):
    """Evaluate union incidents; component evidence never receives onset credit."""
    predictions = _ticks(predictions)
    thresholds = thresholds or {}
    union = _union_truth(item)
    evaluated = metrics.evaluate(predictions, union)
    potential = item.events if potential_events is None else potential_events
    segments = _segment_table(potential, item.manifest["evaluation_start"], item.manifest["evaluation_end"])
    components, segment_rows = [], []
    families = {"B": "count", "Z": "count", "G": "count", "L": "latency", "R": "relation", "E": "error"}
    for event in item.events.itertuples():
        local = predictions[predictions.group.eq(event.group) & predictions.decision_tick.ge(event.start) & predictions.decision_tick.lt(event.end)]
        ready = _eligible(local)
        family = families.get(event.subtype[:1])
        supported = family in local and family in thresholds
        branch_ready = ready & np.isfinite(local[family].to_numpy(float)) if supported else np.zeros(len(local), bool)
        positive = branch_ready & (local[family].to_numpy(float) > thresholds[family]) if supported else np.zeros(len(local), bool)
        components.append(dict(component_event_id=event.event_id, case_id=event.case_id, group=event.group,
                               subtype=event.subtype, start=event.start, end=event.end,
                               component_minutes=int((event.end - event.start).total_seconds() / 60),
                               prediction_available_minutes=int(ready.sum()),
                               binary_positive_minutes=int((ready & local.prediction.eq(1).to_numpy()).sum()),
                               evidence_family=family, evidence_supported=supported,
                               evidence_available_minutes=int(branch_ready.sum()) if supported else None,
                               evidence_positive_minutes=int(positive.sum()) if supported else None,
                               onset_hit_credit=None, interpretation="branch evidence, not causal attribution or a second onset hit"))
    for segment in segments.to_dict("records"):
        local = predictions[predictions.group.eq(segment["group"]) & predictions.decision_tick.ge(segment["start"]) & predictions.decision_tick.lt(segment["end"])]
        ready = _eligible(local)
        realized = item.events[(item.events.group == segment["group"]) & (item.events.start <= segment["start"]) & (item.events.end > segment["start"])]
        realized_ids = sorted({str(event.component_id) if getattr(event, "component_id", "") else str(event.event_id) for event in realized.itertuples()})
        segment_rows.append({**segment, "realized_component_ids": realized_ids,
                             "prediction_available_minutes": int(ready.sum()),
                             "positive_minutes": int((ready & local.prediction.eq(1).to_numpy()).sum()),
                             "unknown_minutes": int((~ready).sum())})
    return {"evaluation": evaluated, "components": pd.DataFrame(components), "segments": pd.DataFrame(segment_rows),
            "union_truth": union}


def paired_differences(predictions, potential_events, begin, end):
    """Within-candidate, same-time/support contrasts; never sum model scores."""
    segments = _segment_table(potential_events, begin, end)
    rows = []
    for left, right in combinations(predictions, 2):
        a, b = _ticks(predictions[left]), _ticks(predictions[right])
        keys = ["group", "decision_tick"]
        a, b = a.sort_values(keys), b.sort_values(keys)
        if not a[keys].reset_index(drop=True).equals(b[keys].reset_index(drop=True)):
            raise ValueError("Paired arms have different clocks")
        for segment in segments.to_dict("records"):
            mask = a.group.eq(segment["group"]) & a.decision_tick.ge(segment["start"]) & a.decision_tick.lt(segment["end"])
            x, y = a.loc[mask], b.loc[mask]
            common = _eligible(x) & _eligible(y)
            row = {**segment, "left_arm": left, "right_arm": right,
                   "common_source_and_ready_minutes": int((x.source_eligible.to_numpy(bool) & y.source_eligible.to_numpy(bool) & x.common_eligible.to_numpy(bool) & y.common_eligible.to_numpy(bool)).sum()),
                   "common_prediction_minutes": int(common.sum()),
                   "right_minus_left_positive_minutes": int((y.prediction.to_numpy()[common] == 1).sum() - (x.prediction.to_numpy()[common] == 1).sum())}
            for family in ("count", "latency", "relation", "error"):
                if family in x and family in y:
                    first, second = x[family].to_numpy(float), y[family].to_numpy(float)
                    valid = common & np.isfinite(first) & np.isfinite(second)
                    row[family + "_paired_support_minutes"] = int(valid.sum())
                    row[family + "_right_minus_left_mean_score"] = float(np.mean(second[valid] - first[valid])) if valid.any() else None
            rows.append(row)
    return pd.DataFrame(rows)


def invalid_request_checks():
    rows = []
    for name, requested in (("MI01", ("Z01", "L01")), ("MI02", ("Z01", "E01")), ("MI03", ("B02", "Z01"))):
        try:
            dataset.validate_joint_request(requested)
            rows.append(dict(scenario_id=name, requested=list(requested), rejected=False, reason="unexpectedly accepted"))
        except ValueError as error:
            rows.append(dict(scenario_id=name, requested=list(requested), rejected=True, reason=str(error)))
    return rows


def _prediction_npz(path, frame):
    arrays = {}
    for column in frame:
        values = frame[column]
        if isinstance(values.dtype, pd.DatetimeTZDtype):
            arrays[column + "_ns"] = values.dt.as_unit("ns").array.asi8
        elif values.dtype == object or isinstance(values.dtype, pd.StringDtype):
            arrays[column] = np.asarray(values.astype(str).tolist(), dtype=str)
        else:
            arrays[column] = values.to_numpy()
    np.savez_compressed(path, **arrays)


def _save_response(directory, item, predictions, thresholds, potential):
    directory.mkdir(parents=True, exist_ok=True)
    response = assess(item, predictions, thresholds, potential)
    _prediction_npz(directory / "minute-predictions.npz", predictions)
    _write_npz(directory / "source-window.npz", item.frame)
    write_json(directory / "source-manifest.json", item.manifest)
    write_json(directory / "evaluation.json", response["evaluation"])
    _csv(directory / "truth-union-incidents.csv", response["union_truth"])
    _csv(directory / "truth-components.csv", item.events)
    _csv(directory / "component-evidence.csv", response["components"])
    _csv(directory / "overlap-segments.csv", response["segments"])
    return response


def _quality_arms(seed, split, authorize_final):
    both = dataset.generate(seed, split, "quality_cross", authorize_final=authorize_final)
    normal = dataset.generate(seed, split, "normal", authorize_final=authorize_final)
    service_frame = normal.frame.copy(deep=True)
    for event in both.events.itertuples():
        definition = dict(subtype=event.subtype, case_id=event.case_id, group=event.group, intervals=[(event.start, event.end)])
        dataset._apply(service_frame, definition, seed, split, "quality_cross")
    service = dataset.Dataset(service_frame, both.events.copy(), normal.quality.copy(),
                              {**both.manifest, "source_hash": dataset.frame_hash(service_frame), "control_role": "service_only"})
    quality_frame = both.frame.copy(deep=True)
    measured = quality_frame.source_complete.to_numpy()
    for column in ("count", "duration_ms", "total_count", "error_count"):
        quality_frame.loc[measured, column] = normal.frame.loc[measured, column]
    quality = dataset.Dataset(quality_frame, both.events.iloc[:0].copy(), both.quality.copy(),
                              {**both.manifest, "source_hash": dataset.frame_hash(quality_frame), "control_role": "quality_only"})
    return {"0": normal, "S": service, "Q": quality, "SQ": both}


def run_stress(development, output, *, split="selection", gate_path=None):
    from . import runner
    development, output = Path(development), Path(output)
    if output.resolve().is_relative_to(runner.REPOSITORY_ROOT.resolve()):
        raise ValueError("Stress artifacts must be written outside the Git repository")
    lock_path = development / "selection-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock["source_hashes"] != runner.source_digest():
        raise ValueError("Locked detector sources changed")
    for path, digest in lock["artifact_hashes"].items():
        if runner.file_hash(path) != digest:
            raise ValueError("Locked fit/cut artifact changed: " + path)
    if split == "final":
        if gate_path is None:
            raise ValueError("Final stress is sealed until root GO")
        gate = runner._gate(gate_path, "final")
        if gate.get("selection_lock_sha256") != runner.file_hash(lock_path):
            raise ValueError("Final stress gate does not match the frozen selection lock")
        seeds = scenarios.FINAL_SEEDS
    elif split == "selection":
        seeds = scenarios.DEV_SEEDS
    else:
        raise ValueError("Stress split must be selection or final")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "clock-contract.json", CLOCK_CONTRACT)
    started, records, replays = time.perf_counter(), [], []
    entries = [lock["baseline"], *lock["selected"]]
    for seed in seeds:
        reference_seed = lock["final_reference_seed_map"][str(seed)] if split == "final" else seed
        bundles = [runner.load_selected_candidate(development, entry, reference_seed) for entry in entries]
        for case in dataset.build_compound_cases(seed, split):
            potential_item = dataset.materialize_compound(case, case.arms[-1], authorize_final=split == "final")
            last_end = max(component.end for component in case.components)
            potential = potential_item.events
            for number, bundle in enumerate(bundles):
                predictions = {}
                for arm in case.arms:
                    root = output / str(seed) / f"candidate-{number}" / "composite" / case.case_id / arm
                    root.mkdir(parents=True, exist_ok=True)
                    ledger = dict(seed=seed, candidate=number, suite="composite", case_id=case.case_id, recipe=case.recipe, arm=arm)
                    try:
                        full = dataset.materialize_compound(case, arm, authorize_final=split == "final")
                        check = check_dataset(full)
                        write_json(root / "generation-check.json", check)
                        item = crop(full, case.anchor, last_end)
                        if not check["generation_valid"]:
                            _write_npz(root / "source-window.npz", item.frame)
                            write_json(root / "source-manifest.json", item.manifest)
                            replays.append({**ledger, "disposition": "generation_failed", "assessed": False})
                            continue
                        prediction = runner.score_candidate_bundle(bundle, item)
                        response = _save_response(root, item, prediction, bundle["calibration"].get("thresholds"), potential)
                        predictions[arm] = prediction
                        replays.append({**ledger, "disposition": "assessed", "assessed": True})
                        for summary in response["evaluation"]["summary"]:
                            records.append({**ledger, **summary})
                    except Exception as error:
                        failure = {**ledger, "disposition": "execution_failed", "assessed": False,
                                   "reason": f"{type(error).__name__}: {error}"}
                        write_json(root / "failure.json", failure)
                        replays.append(failure)
                differences = paired_differences(predictions, potential, case.anchor - pd.Timedelta(minutes=30), last_end + pd.Timedelta(minutes=1440)) if len(predictions) >= 2 else pd.DataFrame()
                _csv(root.parent / "paired-differences.csv", differences)
        _run_auxiliary(seed, split, bundles, runner, output, records)
        write_json(output / f"invalid-requests-{seed}.json", invalid_request_checks())
        write_json(output / "progress.json", dict(last_completed_seed=seed, result_rows=len(records), wall_seconds=time.perf_counter()-started))
    _csv(output / "stress-summary.csv", pd.DataFrame(records))
    _csv(output / "replay-coverage.csv", pd.DataFrame(replays))
    assessed = sum(row["assessed"] for row in replays)
    write_json(output / "stress-manifest.json", dict(status="completed" if assessed == len(replays) else "completed_with_failures", split=split, seeds=list(seeds),
               candidates=len(entries), intended_compound_cases=80, intended_compound_arms=360,
               intended_arm_candidate_replays=360 * len(entries), generated_arm_candidate_replays=len(replays),
               assessed_arm_candidate_replays=assessed, new_fits=0, new_calibrations=0,
               candidate_reselection=False, conditional_stress_exposure=True, clock_contract=CLOCK_CONTRACT,
               selection_lock_sha256=runner.file_hash(lock_path), stress_source_sha256=runner.file_hash(__file__),
               wall_seconds=time.perf_counter()-started, independent_sample_unit="seed/source and80cases; not360independent observations"))
    return records


def _run_auxiliary(seed, split, bundles, runner, output, records):
    quality = _quality_arms(seed, split, split == "final")
    quality_potential = quality["SQ"].events
    anchor, last_end = quality_potential.start.min(), quality_potential.end.max()
    context_cases = relation_contexts.case_specs(seed, split)
    context_checks = {}
    for case in context_cases:
        for role in relation_contexts.ROLES:
            source = relation_contexts.generate_context_case(seed, split, case["context"], case["context_case_id"], role, authorize_final=split == "final")
            report = relation_contexts.export_context_case(source,
                output / str(seed) / "relation-context-sources" / case["context_case_id"] / role,
                output / "relation-context-shared")
            context_checks[(case["context_case_id"], role)] = report
    identifiable = dataset.generate(seed, split, "identifiability", authorize_final=split == "final")
    for number, bundle in enumerate(bundles):
        predictions = {}
        root = output / str(seed) / f"candidate-{number}"
        for arm, source in quality.items():
            item = crop(source, anchor, last_end)
            prediction = runner.score_candidate_bundle(bundle, item)
            response = _save_response(root / "quality-cross" / arm, item, prediction, bundle["calibration"].get("thresholds"), quality_potential)
            predictions[arm] = prediction
            records.extend(dict(seed=seed, candidate=number, suite="quality-cross", arm=arm, **s) for s in response["evaluation"]["summary"])
        _csv(root / "quality-cross" / "paired-differences.csv", paired_differences(predictions, quality_potential, item.manifest["evaluation_start"], item.manifest["evaluation_end"]))
        for case in context_cases:
            case_id = case["context_case_id"]
            predictions = {}
            potential = relation_contexts.generate_context_case(seed, split, case["context"], case_id, "intervention", authorize_final=split == "final").events
            for role in relation_contexts.ROLES:
                report = context_checks[(case_id, role)]
                if not report["generation_valid"]:
                    records.append(dict(seed=seed, candidate=number, suite="relation-contexts", case_id=case_id,
                                        context=case["context"], arm=role, disposition="generation_failed", assessed=False))
                    continue
                source = relation_contexts.generate_context_case(seed, split, case["context"], case_id, role, authorize_final=split == "final")
                item = crop(source, potential.start.min(), potential.end.max())
                prediction = runner.score_candidate_bundle(bundle, item)
                response = _save_response(root / "relation-contexts" / case_id / role, item, prediction, bundle["calibration"].get("thresholds"), potential)
                predictions[role] = prediction
                records.extend(dict(seed=seed, candidate=number, suite="relation-contexts", case_id=case_id,
                                    context=case["context"], arm=role, disposition="assessed", assessed=True, **s)
                               for s in response["evaluation"]["summary"])
            differences = paired_differences(predictions, potential, potential.start.min()-pd.Timedelta(minutes=30), potential.end.max()+pd.Timedelta(minutes=1440)) if len(predictions) >= 2 else pd.DataFrame()
            pair_path = root / "relation-contexts" / case_id / "paired-differences.csv"
            pair_path.parent.mkdir(parents=True, exist_ok=True)
            _csv(pair_path, differences)
        target = identifiable.events
        item = crop(identifiable, target.start.min(), target.end.max())
        prediction = runner.score_candidate_bundle(bundle, item)
        path = root / "identifiability" / "paired-business-labels"
        _save_response(path, item, prediction, bundle["calibration"].get("thresholds"), target)
        write_json(path / "alternative-normal-truth.json", metrics.evaluate(prediction, item.events.iloc[:0]))
        write_json(path / "interpretation.json", dict(identical_observations=True, identical_predictions=True,
                   intended_cases=2, source_hash=item.manifest["source_hash"], truth_alternatives=["announced_normal", "abnormal_incident"],
                   discriminatory_accuracy=None, reason="Business labels differ without an observable difference; no simultaneous-correctness requirement"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--development", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=("selection", "final"), default="selection")
    parser.add_argument("--gate")
    args = parser.parse_args()
    run_stress(args.development, args.output, split=args.split, gate_path=args.gate)


if __name__ == "__main__":
    main()
