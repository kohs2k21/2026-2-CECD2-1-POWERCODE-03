"""Observed-time replay, isolated process revisions and causal lookback windows.

Times in this module are Unix seconds, not millisecond source timestamps. Labels
belong to Episode/evaluation and never enter a model feature frame. Historical
feature mathematics remain in the original research modules.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

RESEARCH_CODE = Path(__file__).resolve().parents[2] / "anomaly_detection" / "code"
if str(RESEARCH_CODE) not in sys.path:
    sys.path.insert(0, str(RESEARCH_CODE))

from process_anomaly_pipeline.data import prepare
from model_evaluation.data import window_features


def iso(seconds: float) -> str:
    return pd.Timestamp(seconds, unit="s", tz="UTC").isoformat()


@lru_cache(maxsize=32768)
def _string_seconds(value: str) -> float:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("Replay timestamps require an explicit timezone")
    return stamp.timestamp()


def seconds(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    elif isinstance(value, str):
        result = _string_seconds(value)
    else:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            raise ValueError("Replay timestamps require an explicit timezone")
        result = stamp.timestamp()
    if not math.isfinite(result):
        raise ValueError("Replay time must be finite")
    return result


@dataclass(frozen=True)
class Observation:
    source: str
    scope: str
    execution_id: str
    process_id: str
    retry: int
    revision: int
    event_at: float
    observed_at: float
    fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for value in (self.source, self.scope, self.execution_id, self.process_id):
            if not isinstance(value, str) or not value:
                raise ValueError("Nonempty source/scope/execution/process IDs required")
        for value in (self.retry, self.revision):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Retry/revision must be nonnegative integers")
        object.__setattr__(self, "event_at", seconds(self.event_at))
        object.__setattr__(self, "observed_at", seconds(self.observed_at))
        if self.event_at > self.observed_at:
            raise ValueError("A source event cannot be observed before it exists")
        if {"label", "is_anomaly", "row_is_anomaly", "case_id", "scenario", "scenario_types"} & self.fields.keys():
            raise ValueError("Evaluation labels are not model input")
        object.__setattr__(self, "fields", deepcopy(self.fields))

    @property
    def identity(self):
        return (self.source, self.scope, self.execution_id, self.process_id, self.retry)

    @property
    def revision_key(self):
        return ("PROCESS", *self.identity, self.revision)

    @classmethod
    def from_dict(cls, values):
        return cls(**values)


@dataclass
class FrozenHistory:
    """Normal-only historical arrival quantiles fitted before evaluation.

    Cohorts without sufficient hour/day support use the explicit global prior.
    A missing width is an error, never an evaluation-derived estimate.
    """
    values: dict[tuple[int, bool, int], tuple[float, float, int]]
    global_values: dict[int, tuple[float, float, int]]
    fitted_until: float
    fitted_source: str = "synthetic_normal_reference"

    @classmethod
    def fit_observations(cls, observations, cutoff, widths=(60, 300, 900), stride_seconds=60):
        cutoff = seconds(cutoff)
        if stride_seconds <= 0 or any(w <= 0 for w in widths):
            raise ValueError("Positive history width/stride required")
        # History must be supplied as verified normal references by the caller.
        arrivals = {}
        for event in observations:
            if event.observed_at >= cutoff:
                raise ValueError("History contains a post-cutoff observation")
            key = (event.source, event.scope, event.execution_id, event.retry)
            arrivals[key] = min(arrivals.get(key, event.event_at), event.event_at)
        if not arrivals:
            raise ValueError("Normal history is empty")
        times = np.sort(np.asarray(list(arrivals.values()), dtype=float))
        values, global_values = {}, {}
        for width in widths:
            dates = np.arange(times[0] + width, cutoff, stride_seconds)
            if not len(dates):
                raise ValueError("Insufficient normal history for requested width")
            counts = np.searchsorted(times, dates, side="left") - np.searchsorted(times, dates - width, side="left")
            global_values[int(width)] = (float(np.median(counts)), float(np.quantile(counts, .95)), len(counts))
            stamps = pd.to_datetime(dates, unit="s", utc=True).tz_convert("Asia/Seoul")
            cohorts = defaultdict(list)
            for stamp, count in zip(stamps, counts):
                cohorts[(bool(stamp.dayofweek >= 5), int(stamp.hour))].append(float(count))
            for (weekend, hour), sample in cohorts.items():
                values[int(width), weekend, hour] = (float(np.median(sample)), float(np.quantile(sample, .95)), len(sample))
        return cls(values, global_values, cutoff)

    fit = fit_observations

    def lookup(self, clock, width):
        clock = seconds(clock)
        if clock < self.fitted_until:
            raise ValueError("Evaluation clock precedes frozen normal-history cutoff")
        stamp = pd.Timestamp(clock, unit="s", tz="UTC").tz_convert("Asia/Seoul")
        if int(width) not in self.global_values:
            raise ValueError("Normal history does not cover this window width")
        return self.values.get((int(width), bool(stamp.dayofweek >= 5), int(stamp.hour)), self.global_values[int(width)])


class ReplayState:
    """One scope's observed revisions and an immutable mock result store."""
    def __init__(self, start, source="synthetic", scope="TARGET", sla_seconds=30):
        self.start = seconds(start)
        self.clock = self.start
        self.source, self.scope = source, scope
        if not math.isfinite(sla_seconds) or sla_seconds <= 0:
            raise ValueError("Positive PROCESS SLA required")
        self.sla_seconds = float(sla_seconds)
        self.latest: dict[tuple, Observation] = {}
        self.seen: dict[tuple, dict] = {}
        self.arrivals: dict[tuple, float] = {}
        self.results: dict[tuple, Any] = {}
        self.controls = [(self.start, True, True)]
        self.audit = {"duplicates": 0, "stale_revisions": 0, "future_end_hidden": 0}

    def advance(self, clock):
        clock = seconds(clock)
        if clock < self.clock:
            raise ValueError("Observed replay clock cannot move backwards")
        self.clock = clock
        return self.pending()

    def set_visibility(self, clock, collector_visible=True, business_open=True):
        self.advance(clock)
        self.controls.append((self.clock, bool(collector_visible), bool(business_open)))

    def ingest(self, observation):
        event = Observation.from_dict(observation) if isinstance(observation, dict) else observation
        if (event.source, event.scope) != (self.source, self.scope):
            raise ValueError("Observation belongs to another source/scope")
        encoded = asdict(event)
        if event.revision_key in self.seen:
            if self.seen[event.revision_key] != encoded:
                raise ValueError("Conflicting content for the same immutable revision")
            self.audit["duplicates"] += 1
            return False
        self.advance(event.observed_at)
        old = self.latest.get(event.identity)
        if old is not None and event.revision < old.revision:
            self.seen[event.revision_key] = encoded
            self.audit["stale_revisions"] += 1
            return False
        if old is not None and event.event_at != old.event_at:
            raise ValueError("An execution revision cannot change its source start time")
        self.seen[event.revision_key] = encoded
        fields = deepcopy(event.fields)
        end = fields.get("endTime")
        if end is not None and pd.notna(end) and seconds(end) > event.observed_at:
            fields.update(endTime=None, status="P", successCount=None, errorCount=None)
            self.audit["future_end_hidden"] += 1
        event = Observation(event.source, event.scope, event.execution_id, event.process_id,
                            event.retry, event.revision, event.event_at, event.observed_at, fields)
        self.latest[event.identity] = event
        key = (event.source, event.scope, event.execution_id, event.retry)
        self.arrivals[key] = min(self.arrivals.get(key, event.event_at), event.event_at)
        return True

    def observe_until(self, events, clock):
        for event in sorted(events, key=lambda e: (e.observed_at, e.revision)):
            if self.clock <= event.observed_at <= clock:
                self.ingest(event)
        self.advance(clock)
        return self

    def _record(self, event):
        values = deepcopy(event.fields)
        # Even a fixture exposing final source values prematurely cannot leak the
        # future completion duration into the real-time model.
        end = values.get("endTime")
        if end is not None and pd.notna(end) and seconds(end) > self.clock:
            values["endTime"] = None
            values["status"] = "P"
        values.update(transactionId=event.execution_id, processId=event.process_id,
                      retryCount=event.retry, source_period=event.source,
                      observed_at=event.observed_at, event_at=event.event_at,
                      revision=event.revision, scope=event.scope)
        if "startTime" not in values:
            values["startTime"] = iso(event.event_at)
        return values

    def process_frame(self):
        return prepare([self._record(event) for event in self.latest.values()])

    def process_record(self, event):
        """Current accepted revision only; caller may batch known snapshots later."""
        current = self.latest.get(event.identity)
        if current is None or current.revision_key != event.revision_key:
            raise ValueError("PROCESS snapshot is not the current observed revision")
        return self._record(current)

    def pending(self):
        result = []
        for event in self.latest.values():
            end = event.fields.get("endTime")
            unfinished = end is None or pd.isna(end) or seconds(end) > self.clock
            if unfinished and self.clock - event.event_at >= self.sla_seconds:
                result.append(event.revision_key)
        return result

    def window_frame(self, clock, lookback_seconds, history, baselines):
        self.advance(clock)
        if lookback_seconds <= 0 or int(lookback_seconds) != lookback_seconds:
            raise ValueError("Positive integer window width required")
        lo = clock - lookback_seconds
        arrivals = sum(lo <= at < clock for at in self.arrivals.values())
        arrivals5 = sum(clock - 300 <= at < clock for at in self.arrivals.values())
        completed, adapter, process = [], None, None
        for event in self.latest.values():
            row = self._record(event)
            adapter = adapter or row.get("adapterType", "UNKNOWN")
            process = process or event.process_id
            end, start = row.get("endTime"), row.get("startTime")
            if end is None or pd.isna(end) or start is None or pd.isna(start):
                continue
            end_at, start_at = seconds(end), seconds(start)
            if lo <= end_at < clock and end_at >= start_at:
                completed.append((end_at - start_at) * 1000)
        median, p95, support = history.lookup(clock, lookback_seconds)
        median5, p955, _ = history.lookup(clock, 300)
        controls = [entry for entry in self.controls if lo <= entry[0] < clock]
        before = [entry for entry in self.controls if entry[0] <= lo]
        if before:
            controls.insert(0, before[-1])
        visible = all(entry[1] for entry in controls)
        opened = all(entry[2] for entry in controls)
        state = ("collector_unknown" if not visible else "business_closed" if not opened else
                 "warmup" if clock - self.start < max(lookback_seconds, 300) else
                 "no_arrivals" if not arrivals else "no_completions" if not completed else "ready")
        raw = {"case_id": self.scope, "scope": self.scope, "adapterType": adapter or "UNKNOWN",
               "processId": process or "UNKNOWN", "window_start": iso(lo), "window_end": iso(clock),
               "execution_arrivals": arrivals, "execution_arrivals_5m": arrivals5,
               "duration_p50_ms": float(np.quantile(completed, .5)) if completed else np.nan,
               "duration_p95_ms": float(np.quantile(completed, .95)) if completed else np.nan,
               "history_median_arrivals": median, "history_p95_arrivals": p95,
               "history_median_arrivals_5m": median5, "history_p95_arrivals_5m": p955,
               "history_support_windows": support, "completed_rows": len(completed),
               "window_state": state, "collector_visible": visible, "business_open": opened,
               "clock": clock, "source": self.source}
        return window_features(pd.DataFrame([raw]), baselines)

    def window_key(self, clock, lookback_seconds, config_version):
        return ("TIME_WINDOW", self.source, self.scope, clock - lookback_seconds, clock, config_version)

    def commit_result(self, key, result):
        key = tuple(key)
        if key in self.results:
            if self.results[key] != result:
                raise ValueError("Finalized result cannot be overwritten")
            return False
        self.results[key] = deepcopy(result)
        return True

    def snapshot(self):
        return {"version": 1, "start": self.start, "clock": self.clock, "source": self.source,
                "scope": self.scope, "sla_seconds": self.sla_seconds,
                "latest": [asdict(event) for event in self.latest.values()],
                "seen": list(self.seen.values()), "arrivals": [[list(k), v] for k, v in self.arrivals.items()],
                "results": [[list(k), v] for k, v in self.results.items()],
                "controls": deepcopy(self.controls), "audit": dict(self.audit)}

    @classmethod
    def restore(cls, snapshot):
        if snapshot.get("version") != 1:
            raise ValueError("Unsupported replay snapshot version")
        instance = cls(snapshot["start"], snapshot["source"], snapshot["scope"], snapshot["sla_seconds"])
        instance.clock = snapshot["clock"]
        instance.latest = {event.identity: event for event in map(Observation.from_dict, snapshot["latest"])}
        instance.seen = {event.revision_key: asdict(event) for event in map(Observation.from_dict, snapshot["seen"])}
        instance.arrivals = {tuple(k): v for k, v in snapshot["arrivals"]}
        instance.results = {tuple(k): deepcopy(v) for k, v in snapshot["results"]}
        instance.controls = [tuple(entry) for entry in snapshot["controls"]]
        instance.audit = dict(snapshot["audit"])
        return instance
