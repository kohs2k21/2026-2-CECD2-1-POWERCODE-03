"""Causal, calendar-balanced synthetic inputs; labels never enter features.

v3 remains the PROCESS reference. cb006179 estimators are refitted on explicitly
different v4 window features, not presented as the portable v12 predictor.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .models import ComparisonConfig, Detector, detector_margin, frame_digest, sha256_file
from process_anomaly_pipeline.data import prepare
from process_anomaly_pipeline.features import ScoreNormalizer

ROLES = ("train", "calibration", "threshold", "validation", "test")
SCENARIOS = ("input_stop", "arrival_burst", "individual_count", "group_delay",
             "gradual_degradation", "mixed_hard", "count_contract")
CONTROLS = ("low_frequency", "business_closed", "collector_unknown",
            "publication_unknown", "unfinished")
FEATURES = ("log_arrivals", "arrival_relative", "log_elapsed", "elapsed_relative",
            "log_duration_p50", "log_duration_p95", "duration_relative",
            "log_count_p95", "count_relative", "missing_duration", "missing_elapsed",
            "support", "business_open", "inflow_expected", "collector_visible",
            "publication_fresh", "hour_sin", "hour_cos", "weekday_sin", "weekday_cos")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def read_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if (len(config["seeds"]) != 5 or len(set(config["seeds"])) != 5 or
            config["reference_days"] < 14 or config["reference_days"] % 7 or
            config["hours"] != list(range(24)) or config["ready_seconds"] < 900 or
            config["normal_seconds"] * config["reference_days"] * 24 < 20 * 3600 or
            config["window_seconds"] != 300 or config["strides"] != [60, 5] or
            config["calendar_features"] is not True):
        raise ValueError("Primary protocol requires five seeds, balanced independent roles, >=20h/seed, calendar and 300s/60s+5s")
    if (config["load_repeats"] < 3 or config["load_warmup_seconds"] < 60 or
            config["load_measurement_seconds"] < 600 or config["queue_limit"] != 1000 or
            config["load_rates"] != [10, 100]):
        raise ValueError("Load protocol was reduced")
    return config


@dataclass
class Block:
    block_id: str
    source_id: str
    role: str
    scenario: str
    control: bool
    start: float
    end: float
    arrivals: np.ndarray
    durations: np.ndarray
    counts: np.ndarray
    completions: np.ndarray
    contract_invalid: np.ndarray
    guard_intervals: tuple = ()
    anomaly_start: float | None = None
    anomaly_end: float | None = None

    def guard(self, clocks):
        """Independent schedule/heartbeat/publication state, never truth routing."""
        clocks = np.asarray(clocks)
        values = {name: np.ones(len(clocks), bool) for name in
                  ("business_open", "inflow_expected", "collector_visible", "publication_fresh")}
        for begin, end, kind in self.guard_intervals:
            # Guards apply to the full 15-minute readiness horizon after recovery.
            values[kind][(clocks >= begin) & (clocks < end + 900)] = False
        return values


def _block(seed, role, index, start, config, scenario="normal", control=True):
    source_id = f"{seed}:{role}:{scenario}:{index}"
    rng = np.random.default_rng(np.random.SeedSequence([seed, ROLES.index(role), index,
                    (SCENARIOS + CONTROLS + ("normal",)).index(scenario)]))
    ready = config["ready_seconds"]
    watch = (config["normal_seconds"] if scenario == "normal" else
             config["event_seconds"] + config["recovery_seconds"] + 61)
    end = start + ready + watch
    stamp = pd.Timestamp(start, unit="s", tz="UTC").tz_convert("Asia/Seoul")
    mean_gap = 5.5 + 2 * (1 + np.sin(2 * np.pi * stamp.hour / 24)) + .4 * stamp.dayofweek
    offsets = np.cumsum(rng.uniform(.6, 1.4, int((end - start) / (mean_gap * .5)) + 20) * mean_gap)
    arrivals = start + offsets[offsets < end - start]
    durations = rng.lognormal(np.log(.2 + .03 * stamp.dayofweek), .25, len(arrivals))
    counts = rng.integers(1, 9, len(arrivals)).astype(float)
    invalid = np.zeros(len(arrivals), bool)
    active = (arrivals >= start + ready) & (arrivals < start + ready + config["event_seconds"])
    guards = ()
    if not control:
        if scenario == "input_stop":
            keep = ~active
            arrivals, durations, counts, invalid = (a[keep] for a in (arrivals, durations, counts, invalid))
        elif scenario in ("arrival_burst", "mixed_hard"):
            # Replication adds arrivals only; duration/count marginal values are preserved.
            original = np.flatnonzero(active)
            extra = np.repeat(original, 5)
            arrivals = np.r_[arrivals, arrivals[extra] + np.tile(np.arange(1, 6) * .05, len(original))]
            durations = np.r_[durations, durations[extra]]
            counts = np.r_[counts, counts[extra]]
            invalid = np.r_[invalid, invalid[extra]]
            order = np.argsort(arrivals, kind="stable")
            arrivals, durations, counts, invalid = (a[order] for a in (arrivals, durations, counts, invalid))
        active = (arrivals >= start + ready) & (arrivals < start + ready + config["event_seconds"])
        if scenario == "individual_count": counts[active] *= 8
        if scenario in ("group_delay", "mixed_hard"): durations[active] += 8
        if scenario == "gradual_degradation": durations[active] += (arrivals[active] - start - ready) / 25
        if scenario == "count_contract": invalid[active] = True
    if scenario in CONTROLS:
        begin, finish = start + ready, start + ready + config["event_seconds"]
        if scenario == "low_frequency":
            keep = ~((arrivals >= begin) & (arrivals < finish)) | (np.arange(len(arrivals)) % 8 == 0)
            arrivals, durations, counts, invalid = (a[keep] for a in (arrivals, durations, counts, invalid))
            guards = ((begin, finish, "inflow_expected"),)
        elif scenario == "unfinished": durations[(arrivals >= begin) & (arrivals < finish)] = np.inf
        else:
            kind = {"business_closed": "business_open", "collector_unknown": "collector_visible",
                    "publication_unknown": "publication_fresh"}[scenario]
            guards = ((begin, finish, kind),)
    completions = arrivals + durations + .02
    arrivals = arrivals + .02  # First observed PROCESS start, not event-time arrivals.
    return Block(source_id + (":normal" if control else ":injected"), source_id, role,
                 scenario, control, start, end, arrivals, durations, counts, completions,
                 invalid, guards, None if control else start + ready,
                 None if control else start + ready + config["event_seconds"])


def build_role(seed, role, config, *, include_scenarios=False):
    """Each role owns disjoint dates/blocks; paired sources stay inside a role."""
    base = pd.Timestamp("2026-01-05T00:00:00+09:00").timestamp() + ROLES.index(role) * 28 * 86400
    blocks = [_block(seed, role, day * 24 + hour, base + day * 86400 + hour * 3600 + 600, config)
              for day in range(config["reference_days"]) for hour in config["hours"]]
    if include_scenarios:
        base += 14 * 86400
        for scenario in SCENARIOS:
            for day in range(7):
                for hour_index, hour in enumerate((2, 10, 18)):
                    index = day * 3 + hour_index
                    start = base + day * 86400 + hour * 3600 + 600
                    blocks.extend(_block(seed, role, index, start, config, scenario, control)
                                  for control in (True, False))
        for kind in CONTROLS:
            for day in range(7):
                blocks.append(_block(seed, role, day, base + day * 86400 + 6 * 3600 + 600,
                                     config, kind, True))
    return blocks


def row_frame(block, ready_seconds=900):
    ready = block.start + ready_seconds
    ids = np.flatnonzero((block.arrivals >= ready) & (block.arrivals < block.end))
    # One start revision and one completion revision per accepted execution.
    completion = ids[np.isfinite(block.completions[ids]) & (block.completions[ids] < block.end)]
    idx = np.r_[ids, completion]
    terminal = np.r_[np.zeros(len(ids), bool), np.ones(len(completion), bool)]
    clocks = np.r_[block.arrivals[ids], block.completions[completion]]
    durations = np.where(terminal, block.durations[idx], np.nan)
    begin = pd.to_datetime(block.arrivals[idx] - .02, unit="s", utc=True)
    end = pd.to_datetime(np.where(terminal, block.completions[idx] - .02, np.nan), unit="s", utc=True)
    frame = prepare(pd.DataFrame({"transactionId": [f"{block.block_id}:{i}" for i in idx],
        "processId": "SYNTHETIC_P00", "processHubId": "SYNTHETIC_HUB", "adapterType": "HTTP",
        "startTime": begin, "endTime": end, "totalCount": block.counts[idx], "retryCount": 0,
        "successCount": np.where(terminal, block.counts[idx] - block.contract_invalid[idx], np.nan),
        "errorCount": np.where(terminal, 0., np.nan), "status": np.where(terminal, "S", "P")}))
    frame["clock"], frame["block_id"] = clocks, block.block_id
    frame["logical_rule"] = (frame.successCount.notna() & frame.errorCount.notna() &
        frame.totalCount.notna() & (frame.successCount + frame.errorCount != frame.totalCount))
    guard = block.guard(clocks)
    frame["eligible"] = np.logical_and.reduce(list(guard.values()))
    frame["source_id"] = block.source_id
    return frame.sort_values("clock", kind="stable").reset_index(drop=True)


@dataclass
class History:
    values: dict
    support: list

    @classmethod
    def fit(cls, blocks, config):
        if any(b.role != "train" or b.scenario != "normal" or not b.control for b in blocks):
            raise ValueError("History requires independent train normal-only blocks")
        samples = {}
        for block in blocks:
            stamp = pd.Timestamp(block.start, unit="s", tz="UTC").tz_convert("Asia/Seoul")
            key = (stamp.dayofweek, stamp.hour)
            samples.setdefault(key, []).append(block)
        values, support = {}, []
        for key, cohort in samples.items():
            gaps = np.concatenate([np.diff(b.arrivals) for b in cohort])
            dates = {pd.Timestamp(b.start, unit="s", tz="UTC").tz_convert("Asia/Seoul").date() for b in cohort}
            valid = len(gaps) >= config["minimum_gaps"] and len(dates) >= config["minimum_dates"]
            arrivals = np.concatenate([np.searchsorted(b.arrivals,
                np.arange(b.start + 900, b.end, 60), side="left") - np.searchsorted(b.arrivals,
                np.arange(b.start + 900, b.end, 60) - 300, side="left") for b in cohort])
            duration = np.concatenate([b.durations for b in cohort])
            counts = np.concatenate([b.counts for b in cohort])
            values[key] = {"gap": float(np.quantile(gaps, .99)),
                "arrivals": max(float(np.quantile(arrivals, .99)), 1.),
                "duration": float(np.quantile(duration, .99)),
                "count": float(np.quantile(counts, .99)), "supported": valid}
            support.append({"weekday": key[0], "hour": key[1], "dates": len(dates),
                "gaps": len(gaps), "new_arrivals": sum(len(b.arrivals) for b in cohort),
                "eligible_seconds": len(cohort) * config["normal_seconds"], "supported": valid,
                "fallback": False})
        if len(support) != 168 or not all(s["supported"] for s in support):
            raise ValueError("Unsupported 7-day/24-hour normal history; no pooled fallback")
        return cls(values, support)

    def lookup(self, clock):
        stamp = pd.Timestamp(clock, unit="s", tz="UTC").tz_convert("Asia/Seoul")
        return self.values.get((stamp.dayofweek, stamp.hour))


def window_frame(block, history, stride, width=300, ready_seconds=900, *, clocks=None):
    clocks = np.arange(block.start + ready_seconds, block.end, stride) if clocks is None else np.asarray(clocks)
    output = []
    guards = block.guard(clocks)
    for index, clock in enumerate(clocks):
        reference = history.lookup(clock)
        supported = reference is not None and reference["supported"]
        hi = np.searchsorted(block.arrivals, clock, side="left")
        lo = np.searchsorted(block.arrivals, clock - width, side="left")
        count = hi - lo
        elapsed = clock - block.arrivals[hi - 1] if hi else np.nan
        completed = (block.completions >= clock - width) & (block.completions < clock)
        durations = block.durations[completed]
        p50, p95 = np.quantile(durations, [.5, .95]) if len(durations) else (np.nan, np.nan)
        cp95 = float(np.quantile(block.counts[lo:hi], .95)) if hi > lo else np.nan
        stamp = pd.Timestamp(clock, unit="s", tz="UTC").tz_convert("Asia/Seoul")
        r = reference or dict(gap=np.nan, arrivals=np.nan, duration=np.nan, count=np.nan)
        row = {"log_arrivals": np.log1p(count), "arrival_relative": count / r["arrivals"],
            "log_elapsed": np.log1p(elapsed), "elapsed_relative": elapsed / r["gap"],
            "log_duration_p50": np.log1p(p50 * 1000), "log_duration_p95": np.log1p(p95 * 1000),
            "duration_relative": p95 / r["duration"], "log_count_p95": np.log1p(cp95),
            "count_relative": cp95 / r["count"], "missing_duration": float(not len(durations)),
            "missing_elapsed": float(not hi), "support": float(supported),
            "hour_sin": np.sin(2 * np.pi * stamp.hour / 24), "hour_cos": np.cos(2 * np.pi * stamp.hour / 24),
            "weekday_sin": np.sin(2 * np.pi * stamp.dayofweek / 7), "weekday_cos": np.cos(2 * np.pi * stamp.dayofweek / 7)}
        row.update({k: float(v[index]) for k, v in guards.items()})
        row.update(clock=clock, block_id=block.block_id, source_id=block.source_id,
            eligible=supported and hi > 0 and all(v[index] for v in guards.values()),
            elapsed_seconds=elapsed, arrivals=count, duration_p95_seconds=p95,
            rule_score=float(np.nanmax([elapsed / r["gap"], count / r["arrivals"],
                                       p95 / r["duration"], cp95 / r["count"]])))
        output.append(row)
    return pd.DataFrame(output)


@dataclass
class TrendModel:
    algorithm: str
    encoder: object
    raw_model: object
    normalizer: object
    threshold: float
    source_hashes: dict

    def score(self, frame):
        values = self.encoder.transform(frame)
        with threadpool_limits(limits=1):
            if self.algorithm == "if":
                # Source RawModel.score internally raises BLAS to 2; evaluate the
                # identical negative-score mean with the required single thread.
                raw = np.mean([-m.score_samples(values @ r) for m, r in
                               zip(self.raw_model.models, self.raw_model.rotations)], axis=0)
            else:
                raw = self.raw_model.score(values)
        scores = self.normalizer.transform(raw)
        if not np.isfinite(scores).all(): raise ValueError("Non-finite trend score")
        return scores


def fit_models(rows, windows, config, seed, base):
    settings = ComparisonConfig(seed=seed, quantile=1 - 1e-9,
        n_estimators=config["process_n_estimators"], max_samples=config["process_max_samples"])
    with threadpool_limits(limits=1):
        process = Detector.fit(config["process_algorithm"], *(rows[n] for n in ROLES[:3]), "row", settings)
    process.model.n_jobs = 1
    # Smallest strict-above-normal maximum, without an arbitrary extra spread.
    # Every group/budget keeps this same validation-independent threshold.
    process.threshold = float(np.nextafter(np.max(process.score(rows["threshold"])), np.inf))
    snapshot = (base / config["source_snapshot"]).resolve()
    if not snapshot.is_relative_to((base / "runs" / "source_snapshot").resolve()):
        raise ValueError("Source snapshot outside fixed export directory")
    files = ("model_research_v5/models.py", "model_research_v8/models.py",
             "model_research_v8/features.py", "process_anomaly_pipeline/features.py")
    source_hashes = {f: sha256_file(snapshot / "code" / f) for f in files}
    sys.path.insert(0, str(snapshot / "code"))
    module = import_module("model_research_v8.models")
    columns = FEATURES if config["calendar_features"] else FEATURES[:-4]
    trends = {}
    for algorithm in config["trend_algorithms"]:
        encoder = module.Encoder.fit(windows["train"], columns)
        settings = module.ResearchSettings(algorithm=algorithm, samples=config["trend_samples"],
                    k=config["trend_k"], seed=seed, rotations=1, covariance="radius")
        raw = module.RawModel(settings) if algorithm == "if" else module.ClusterDistance(settings)
        raw.fit(encoder.transform(windows["train"]), encoder.transform(windows["calibration"]))
        if algorithm == "if":
            for estimator in raw.models: estimator.n_jobs = 1
        cal = (-np.mean([m.score_samples(encoder.transform(windows["calibration"]) @ r)
                for m, r in zip(raw.models, raw.rotations)], axis=0)
                if algorithm == "if" else raw.score(encoder.transform(windows["calibration"])))
        model = TrendModel(algorithm, encoder, raw, ScoreNormalizer.fit(cal), 0., source_hashes)
        model.threshold = float(np.max(model.score(windows["threshold"])))
        trends[algorithm] = model
    return process, trends


def truth_at(block, clocks, width=300):
    clocks = np.asarray(clocks)
    if block.control:
        return np.zeros(len(clocks)), np.full(len(clocks), "clean", dtype=object)
    s, e = block.anomaly_start, block.anomaly_end
    q = np.maximum(0., np.minimum(clocks, e) - np.maximum(clocks - width, s)) / width
    state = np.where((clocks >= s) & (clocks < e) & (q > 0), "active",
                     np.where((clocks >= e) & (q > 0), "recovery", "clean")).astype(object)
    state[(clocks == s) & (q == 0)] = "antecedent"
    return q, state


def suppress(clocks, seconds=60):
    result, previous = [], -np.inf
    for clock in sorted(set(map(float, clocks))):
        if clock - previous >= seconds:
            result.append(clock)
            previous = clock
    return result


def score_block(block, rows, windows, process, trends):
    return {"rows": rows, "windows": windows,
            "row_prediction": (detector_margin(process, rows) > 0) | rows.logical_rule.to_numpy(bool),
            "trend_scores": {a: m.score(windows) for a, m in trends.items()}}


def group_metrics(blocks, cache, group, algorithm, rule_limit, ml_limit, suppression=60):
    events, details, all_raw, all_final, normal_seconds, unavailable, opportunities = [], [], 0, 0, 0., 0, 0
    for block in blocks:
        data = cache[block.block_id]
        r, w = data["rows"], data["windows"]
        eligible_r, eligible_w = r.eligible.to_numpy(bool), w.eligible.to_numpy(bool)
        row_clocks = r.clock.to_numpy()[data["row_prediction"] & eligible_r]
        trend_pred = np.zeros(len(w), bool)
        if group in ("B", "D"): trend_pred |= w.rule_score.to_numpy() > rule_limit
        if group in ("C", "D"): trend_pred |= data["trend_scores"][algorithm] > ml_limit
        window_clocks = w.clock.to_numpy()[trend_pred & eligible_w]
        raw = sorted(np.r_[row_clocks, window_clocks])
        final = suppress(raw, suppression)
        clocks = w.clock.to_numpy()
        stride = clocks[1] - clocks[0] if len(clocks) > 1 else 60
        seconds = float(np.sum(eligible_w) * stride)
        unavailable += int(np.sum(~eligible_w)); opportunities += len(w)
        if block.scenario == "normal":
            normal_seconds += seconds
            all_raw += len(raw); all_final += len(final)
        q, phase = truth_at(block, final)
        details.append({"block_id": block.block_id, "scenario": block.scenario, "control": block.control,
            "raw_alert_clocks": list(map(float, raw)), "final_alert_clocks": final,
            "overlap_q": q.tolist(), "alert_phase": phase.tolist(), "eligible_seconds": seconds,
            "window_opportunities": len(w), "unavailable": int(np.sum(~eligible_w)),
            "recovery_alerts": int(np.sum(phase == "recovery")),
            "antecedent_alerts": int(np.sum(phase == "antecedent")),
            "clean_alerts": int(np.sum(phase == "clean"))})
        if not block.control:
            # count-contract is a logical-rule audit, not an ML capability test.
            hits = [t for t in final if block.anomaly_start < t < block.anomaly_end]
            hit = bool(hits)
            delay = hits[0] - block.anomaly_start if hit else block.anomaly_end - block.anomaly_start
            cleared = [float(t) for i, t in enumerate(clocks) if t >= block.anomaly_end + 300 and
                eligible_w[i] and not trend_pred[i] and not np.any((row_clocks > t - stride) & (row_clocks <= t))]
            recovery_delay = cleared[0] - block.anomaly_end if cleared else None
            events.append({"block_id": block.block_id, "scenario": block.scenario, "hit": hit,
                "first_delay_seconds": delay if hit else None, "penalized_delay_seconds": delay,
                "recovery_delay_seconds": recovery_delay, "recovery_right_censored": not bool(cleared),
                "recovery_observed_until_seconds": float(clocks[-1] - block.anomaly_end) if len(clocks) else None,
                "recovery_criterion": "first eligible q0 timer with no raw trend alert and no realtime alert in prior stride; q0 is intervention-arrival overlap, not guaranteed completion-clean",
                "logical_contract_audit": block.scenario == "count_contract"})
    primary = [e for e in events if not e["logical_contract_audit"]]
    return {"group": group, "algorithm": algorithm, "events": events, "blocks": details,
            "normal_hours": normal_seconds / 3600, "normal_alerts": all_final,
            "normal_raw_alerts": all_raw, "normal_alerts_per_hour": all_final / (normal_seconds / 3600),
            "event_recall": float(np.mean([e["hit"] for e in primary])) if primary else None,
            "penalized_delay_seconds": float(np.mean([e["penalized_delay_seconds"] for e in primary])) if primary else None,
            "recovery_delay_seconds_mean_observed": float(np.mean([e["recovery_delay_seconds"] for e in primary
                if e["recovery_delay_seconds"] is not None])) if any(e["recovery_delay_seconds"] is not None for e in primary) else None,
            "recovery_right_censored_events": sum(e["recovery_right_censored"] for e in primary),
            "window_opportunities": opportunities, "unavailable": unavailable,
            "unavailable_fraction": unavailable / opportunities if opportunities else None}
