"""Minute-interval scoring and finite normal-only threshold calibration.

Truth is accepted only by evaluate(), never by calibration or the detector.
Publication/as-of handling belongs to features; rows here are dense minutes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

QUANTILES = (.90, .95, .975, .99, .9925, .995, .9975, .999,
             .9995, .99975, .9999, 1.0)
MINUTE_NS = 60_000_000_000


def _ticks(values):
    return pd.DatetimeIndex(pd.to_datetime(values, utc=True)).as_unit("ns").asi8


def _flag(frame, name, default=True):
    if name not in frame:
        return np.full(len(frame), default, bool)
    values = frame[name]
    if values.isna().any():
        raise ValueError(f"Null eligibility flag: {name}")
    return values.to_numpy(bool)


def apply_thresholds(frame, thresholds):
    """Three-valued OR; a missing branch cannot turn a negative into normal."""
    if not thresholds:
        raise ValueError("At least one frozen threshold is required")
    result = frame.copy()
    values = result[list(thresholds)].to_numpy(float)
    cuts = np.array(list(thresholds.values()), float)
    if not np.isfinite(cuts).all():
        raise ValueError("Non-finite threshold")
    finite = np.isfinite(values)
    all_ready = finite.all(axis=1)
    positive = ((values > cuts) & finite).any(axis=1)
    eligible = _flag(result, "source_eligible") & _flag(result, "common_eligible")
    result["score_supported"] = all_ready
    result["prediction"] = np.where(eligible & (all_ready | positive), positive, np.nan)
    return result


def _groups(frame):
    for group, part in frame.groupby("group", sort=True, observed=True):
        part = part.sort_values("decision_tick", kind="stable")
        tick = _ticks(part.decision_tick)
        if len(tick) > 1 and np.any(np.diff(tick) != MINUTE_NS):
            raise ValueError("Expected dense, unique one-minute rows per group")
        yield str(group), part, tick


def _episodes(positive, available, merge_gap):
    indices = np.flatnonzero(positive)
    if not len(indices):
        return np.empty(0, int), np.empty(0, int), np.empty(0, int)
    unavailable = np.r_[0, np.cumsum(~available)]
    unknown_gap = unavailable[indices[1:]] - unavailable[indices[:-1] + 1]
    split = np.r_[True, (np.diff(indices) - 1 > merge_gap) | (unknown_gap > 0)]
    boundaries = np.flatnonzero(split)
    last = np.r_[boundaries[1:] - 1, len(indices) - 1]
    return indices[boundaries], indices[last] + 1, np.diff(np.r_[boundaries, len(indices)])


def _normal_stats(part, tick, inside, merge_gap):
    values = part.prediction.to_numpy(float)
    if np.isinf(values).any():
        raise ValueError("prediction must be 0, 1 or NaN; infinity is malformed")
    if np.any(np.isfinite(values) & ~np.isin(values, [0., 1.])):
        raise ValueError("prediction must be 0, 1 or NaN")
    source = _flag(part, "source_eligible")
    common = _flag(part, "common_eligible")
    planned = _flag(part, "in_evaluation")
    supported = _flag(part, "score_supported")
    available = np.isfinite(values) & source & common
    positive = available & (values == 1.)
    return _array_stats(positive, available, source, common, planned, supported, inside, merge_gap)


def _array_stats(positive, available, source, common, planned, supported, inside, merge_gap):
    first, last, counts = _episodes(positive, available, merge_gap)
    normal = ~inside & planned
    source_normal = normal & source
    common_normal = source_normal & common
    strict_normal = common_normal & supported
    conditional_normal = normal & available
    false = int(np.sum(normal[first] & source[first] & common[first]))
    normal_positive = int(np.sum(normal & positive))
    div = lambda n, d: float(n / d) if d else None
    conditional_minutes = int(conditional_normal.sum())
    source_minutes = int(source_normal.sum())
    common_minutes = int(common_normal.sum())
    strict_minutes = int(strict_normal.sum())
    planned_minutes = int(normal.sum())
    stats = {
        "normal_planned_minutes": planned_minutes,
        "normal_source_minutes": source_minutes,
        "normal_common_minutes": common_minutes,
        "normal_all_branch_supported_minutes": strict_minutes,
        "normal_prediction_available_minutes": conditional_minutes,
        "normal_monitored_hours": conditional_minutes / 60.,
        "false_alarm_episodes": false,
        "false_alarm_episodes_per_hour": div(false * 60, conditional_minutes),
        "false_alarm_episodes_per_source_hour": div(false * 60, source_minutes),
        "positive_normal_minutes": normal_positive,
        "positive_time_fraction": div(normal_positive, conditional_minutes),
        "positive_fraction_source": div(normal_positive, source_minutes),
        "positive_fraction_common": div(normal_positive, common_minutes),
        "positive_fraction_all_branch_supported": div(int(np.sum(strict_normal & positive)), strict_minutes),
        "positive_fraction_planned": div(normal_positive, planned_minutes),
        "normal_availability": div(conditional_minutes, source_minutes),
        "unknown_minutes": int(np.sum(planned & ~available)),
    }
    return stats, available, positive, first, last, counts


def evaluate(frame, events=None, merge_gap=10):
    """Score actual new episode onsets, with one-to-one event credit.

    Compound union incidents must be passed as union events. Component evidence
    is a separate diagnostic; this function never gives a single onset two hits.
    """
    if merge_gap < 0:
        raise ValueError("Negative episode merge gap")
    event_records = [] if events is None else events.to_dict("records")
    summaries, scored, episodes, cases = [], [], [], []
    for group, part, tick in _groups(frame):
        group_events = sorted((e for e in event_records if str(e["group"]) == group),
                              key=lambda e: (pd.Timestamp(e["start"]), str(e["event_id"])))
        inside = np.zeros(len(part), bool)
        spans = []
        for event in group_events:
            left, right = _ticks([event["start"], event["end"]])
            if right <= left:
                raise ValueError("Nonpositive event interval")
            start, end = np.searchsorted(tick, [left, right])
            inside[start:end] = True
            spans.append((event, left, right, start, end))
        summary, ready, positive, first, last, counts = _normal_stats(part, tick, inside, merge_gap)
        summary["group"] = group
        assigned = set()
        group_scored = []
        for event, left, right, start, end in spans:
            candidates = [int(i) for i in np.flatnonzero((tick[first] >= left) & (tick[first] < right))
                          if int(i) not in assigned]
            matched = candidates[0] if candidates else None
            if matched is not None:
                assigned.add(matched)
            delay = float((tick[first[matched]] - left) / MINUTE_NS) if matched is not None else None
            minutes = max(1, int((right - left) // MINUTE_NS))
            available_count = int(ready[start:end].sum())
            positive_count = int(positive[start:end].sum())
            row = {k: v for k, v in event.items() if k not in ("start", "end")}
            row.update(start=pd.Timestamp(left, tz="UTC").isoformat(),
                       end=pd.Timestamp(right, tz="UTC").isoformat(),
                       hit=matched is not None, delay_minutes=delay,
                       hit_episode_id=f"{group}-{matched:06d}" if matched is not None else None,
                       event_minutes=minutes, prediction_available_minutes=available_count,
                       positive_active_minutes=positive_count,
                       active_coverage=positive_count / minutes,
                       availability=available_count / minutes,
                       loss360_minutes=min(delay, 360.) if delay is not None else 360.,
                       loss1440_minutes=min(delay, 1440.) if delay is not None else 1440.,
                       miss_reason=None if matched is not None else
                       "no_available_prediction" if not available_count else
                       "ongoing_episode_without_new_onset" if positive_count else "no_positive_onset")
            # Recovery is observable only after a hit and after truth end.
            later_starts = [s[1] for s in spans if s[1] >= right]
            horizon_ns = min(later_starts) if later_starts else (tick[-1] + MINUTE_NS)
            recovery_end = int(np.searchsorted(tick, horizon_ns))
            recovery_ready = ready[end:recovery_end]
            recovery_quiet = recovery_ready & ~positive[end:recovery_end]
            precleared = bool(matched is not None and end > start and ready[end - 1] and not positive[end - 1])
            for quiet_length in (1, 10, 30):
                name = f"recovery_quiet{quiet_length}"
                hits = np.empty(0, int)
                if matched is not None and not precleared and len(recovery_quiet) >= quiet_length:
                    sums = np.r_[0, np.cumsum(recovery_quiet)]
                    hits = np.flatnonzero(sums[quiet_length:] - sums[:-quiet_length] == quiet_length)
                row[name + "_delay_minutes"] = float(hits[0]) if len(hits) else None
                row[name + "_confirmed_after_minutes"] = float(hits[0] + quiet_length) if len(hits) else None
                row[name + "_censor_reason"] = (None if len(hits) else "baseline_miss" if matched is None else
                                                               "precleared_before_end" if precleared else
                                                               "unknown_or_no_sustained_quiet")
            row["recovery_precleared"] = precleared
            row["recovery_unknown_minutes"] = int((~recovery_ready).sum())
            row["recovery_horizon"] = pd.Timestamp(horizon_ns, tz="UTC").isoformat()
            group_scored.append(row)
        delays = [r["delay_minutes"] for r in group_scored if r["hit"]]
        subtype_rows = {}
        case_rows = {}
        for row in group_scored:
            subtype = str(row.get("scenario_id", row.get("scenario", "unspecified")))
            subtype_rows.setdefault(subtype, []).append(row)
            case_key = str(row.get("case_id", row["event_id"]))
            case_rows.setdefault(case_key, []).append(row)
        subtypes = {key: {"events": len(rows), "hits": sum(r["hit"] for r in rows),
                          "recall": float(np.mean([r["hit"] for r in rows]))}
                    for key, rows in subtype_rows.items()}
        families = {}
        for key, row in subtypes.items():
            family = key.removeprefix("SC-")[:1]
            families.setdefault(family, []).append(row["recall"])
        family_recalls = {key: float(np.mean(values)) for key, values in families.items()}
        for key, rows in case_rows.items():
            cases.append({"group": group, "case_id": key,
                          "scenario_id": rows[0].get("scenario_id", rows[0].get("scenario")),
                          "active_events": len(rows), "hits": sum(r["hit"] for r in rows),
                          "any_pulse_hit": any(r["hit"] for r in rows),
                          "all_pulses_hit": all(r["hit"] for r in rows),
                          "pulse_recall": float(np.mean([r["hit"] for r in rows])),
                          "first_hit_delay_minutes": next((r["delay_minutes"] +
                              (pd.Timestamp(r["start"]) - pd.Timestamp(rows[0]["start"])).total_seconds() / 60.
                              for r in rows if r["hit"]), None),
                          "positive_active_minutes": sum(r["positive_active_minutes"] for r in rows),
                          "active_minutes": sum(r["event_minutes"] for r in rows)})
        summary.update(events=len(group_scored), hits=len(delays),
                       event_recall=len(delays) / len(group_scored) if group_scored else None,
                       subtype_results=subtypes, family_recalls=family_recalls,
                       macro_subtype_recall=float(np.mean([v["recall"] for v in subtypes.values()])) if subtypes else None,
                       macro_family_recall=float(np.mean(list(family_recalls.values()))) if family_recalls else None,
                       delay_median_minutes=float(np.median(delays)) if delays else None,
                       delay_p95_minutes=float(np.quantile(delays, .95)) if delays else None,
                       mean_loss360_minutes=float(np.mean([r["loss360_minutes"] for r in group_scored])) if group_scored else None,
                       mean_loss1440_minutes=float(np.mean([r["loss1440_minutes"] for r in group_scored])) if group_scored else None)
        summaries.append(summary)
        scored.extend(group_scored)
        for index, (begin, end, count) in enumerate(zip(first, last, counts)):
            episodes.append({"episode_id": f"{group}-{index:06d}", "group": group,
                             "start": pd.Timestamp(tick[begin], tz="UTC").isoformat(),
                             "end": pd.Timestamp(tick[end - 1] + MINUTE_NS, tz="UTC").isoformat(),
                             "positive_minutes": int(count), "matched_event": index in assigned})
    return {"summary": summaries, "events": scored, "episodes": episodes, "cases": cases}


def calibrate(published, score_columns=("score",), quantiles=QUANTILES,
              budgets=(.05, .01), merge_gap=10):
    """Evaluate every fixed quantile candidate; episode rate is not monotonic.

    A multi-branch candidate uses the same quantile for each branch and checks
    the resulting OR as a whole, at every supplied cadence and every group.
    """
    if not published or not score_columns or not 0 < len(quantiles) <= 12:
        raise ValueError("Empty calibration input or more than twelve candidates")
    if any(not 0 <= q <= 1 for q in quantiles) or len(set(quantiles)) != len(quantiles):
        raise ValueError("Invalid/duplicate calibration quantiles")
    first = published[min(published)]
    eligible = _flag(first, "source_eligible") & _flag(first, "common_eligible") & _flag(first, "in_evaluation")
    pools = {}
    for column in score_columns:
        values = first[column].to_numpy(float)
        pools[column] = values[eligible & np.isfinite(values)]
    if any(not len(v) for v in pools.values()):
        return {"passed": False, "thresholds": None, "quantile": None,
                "candidate_checks": [], "reason": "no_finite_calibration_exposure"}
    # The full grid shares minute arrays across all twelve threshold trials.
    payloads = []
    group_contract = None
    for cadence, frame in sorted(published.items()):
        cadence_contract = {}
        for group, part, tick in _groups(frame):
            values = part[list(score_columns)].to_numpy(float)
            finite = np.isfinite(values)
            source = _flag(part, "source_eligible")
            common = _flag(part, "common_eligible")
            planned = _flag(part, "in_evaluation")
            cadence_contract[group] = (tick, source, common, planned)
            payloads.append((int(cadence), group, values, finite,
                             source, common, planned, np.zeros(len(part), bool)))
        if group_contract is None:
            group_contract = cadence_contract
        elif set(group_contract) != set(cadence_contract):
            raise ValueError("Calibration cadence is missing a required group")
        else:
            for group, actual in cadence_contract.items():
                if not all(np.array_equal(expected, value) for expected, value in zip(group_contract[group], actual)):
                    raise ValueError("Calibration cadences have different source/evaluation exposure")
    checks = []
    selected = None
    for q in sorted(quantiles):
        cuts = {column: float(np.quantile(values, q, method="higher")) for column, values in pools.items()}
        guard_rows = []
        okay = True
        cut_array = np.array([cuts[column] for column in score_columns])
        for cadence, group, values, finite, source, common, planned, inside in payloads:
            supported = finite.all(axis=1)
            positive = (finite & (values > cut_array)).any(axis=1) & source & common
            available = (supported | positive) & source & common
            stats = _array_stats(positive, available, source, common, planned,
                                 supported, inside, merge_gap)[0]
            passed = (stats["normal_prediction_available_minutes"] > 0 and
                      stats["false_alarm_episodes_per_hour"] <= budgets[0] + 1e-12 and
                      stats["positive_time_fraction"] <= budgets[1] + 1e-12)
            okay &= passed
            guard_rows.append({"cadence": cadence, "group": group, "passed": bool(passed), **stats})
        checks.append({"quantile": float(q), "thresholds": cuts, "passed": bool(okay), "guards": guard_rows})
        if okay and selected is None:
            selected = {"thresholds": cuts, "quantile": float(q), "guards": guard_rows}
    return {"passed": selected is not None,
            "thresholds": None if selected is None else selected["thresholds"],
            "quantile": None if selected is None else selected["quantile"],
            "guards": [] if selected is None else selected["guards"],
            "candidate_checks": checks,
            "reason": None if selected is not None else "normal_budget_failed",
            "strict_greater_than": True, "test_used": False,
            "candidate_policy": "first passing predeclared quantile; all candidates checked"}
