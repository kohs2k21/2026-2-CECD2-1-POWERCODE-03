"""Preserve Process rows and attach strictly-past completion context.

Naive source times mean Asia/Seoul. Internally all clocks are UTC nanoseconds.
Without availableTime, delivery at transaction completion is an explicit proxy,
not a claim that original exports contain collector reception timestamps.
"""
from bisect import bisect_left
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np
import pandas as pd


POINT = ("log_duration", "log_total_count", "duration_residual", "duration_tail",
         "duration_per_count", "reference_missing")
CONTEXT = ("recent_duration_shift", "prefix_duration_shift", "recent_residual_mean",
           "recent_residual_max", "delay_slope", "delay_cusum", "delay_persistence",
           "observed_log_count_1m", "observed_log_count_5m", "observed_log_count_15m",
           "recent_burst", "prefix_burst", "gap_log_seconds", "gap_relative",
           "recent_gap_shift", "context_support", "hour_sin", "hour_cos",
           "weekday_sin", "weekday_cos")
FEATURES = POINT + CONTEXT


def timestamps(values):
    s = pd.Series(values)
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        return s.dt.tz_convert("UTC")
    if pd.api.types.is_datetime64_dtype(s):
        return s.dt.tz_localize("Asia/Seoul").dt.tz_convert("UTC")
    aware = s.astype("string").str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")
    out.loc[aware] = pd.to_datetime(s.loc[aware], format="mixed", errors="raise", utc=True)
    out.loc[~aware] = (pd.to_datetime(s.loc[~aware], format="mixed", errors="raise")
                       .dt.tz_localize("Asia/Seoul").dt.tz_convert("UTC"))
    return out


def normalize(records):
    f = pd.DataFrame(records).copy().reset_index(drop=True)
    required = ("transactionId", "processId", "adapterType", "startTime", "endTime", "totalCount")
    missing = set(required) - set(f)
    if missing:
        raise ValueError(f"Missing Process fields: {sorted(missing)}")
    for col in ("startTime", "endTime"):
        f[col] = timestamps(f[col])
    for col in ("totalCount", "successCount", "errorCount", "retryCount"):
        if col in f:
            f[col] = pd.to_numeric(f[col], errors="raise").astype(float)
    if "source_period" not in f:
        f["source_period"] = "runtime"
    if "case_id" not in f:
        f["case_id"] = f.source_period.astype(str)
    f["duration_ms"] = (f.endTime-f.startTime).dt.total_seconds()*1000
    if f[list(required)].isna().any().any() or f.case_id.isna().any():
        raise ValueError("Required identity/time/count is missing; apply input rules first")
    if (f.duration_ms < 0).any() or (f.totalCount < 0).any() or (f.totalCount % 1 != 0).any():
        raise ValueError("Invalid duration/count; apply input rules first")
    if "availableTime" in f:
        f["availableTime"] = timestamps(f.availableTime)
    else:
        # A transaction-complete payload can contain several Process rows.
        f["availableTime"] = f.groupby(["case_id", "transactionId"], observed=True).endTime.transform("max")
    if f.availableTime.isna().any() or (f.availableTime < f.endTime).any():
        raise ValueError("A completed row cannot be available before its endTime")
    if "event_row_id" not in f:
        f["event_row_id"] = np.arange(len(f))
    if f.event_row_id.duplicated().any():
        raise ValueError("event_row_id must uniquely identify each original row")
    return f


@dataclass
class NormalReference:
    tables: list
    global_sample: np.ndarray
    min_support: int = 30

    @classmethod
    def fit(cls, f, min_support=30):
        f = f.copy()
        f["_count_bin"] = np.floor(np.log2(f.totalCount.to_numpy(float)+1)).astype(int)
        f["_duration"] = np.log1p(f.duration_ms.to_numpy(float))
        levels = [("adapterType", "processId", "_count_bin"), ("adapterType", "processId"),
                  ("adapterType", "_count_bin"), ("adapterType",)]
        tables = []
        for cols in levels:
            table = {}
            for key, g in f.groupby(list(cols), observed=True, sort=False):
                if len(g) >= min_support:
                    key = key if isinstance(key, tuple) else (key,)
                    # Full empirical samples; no evaluation values enter tables.
                    table[key] = np.sort(g._duration.to_numpy(float))
            tables.append((cols, table))
        return cls(tables, np.sort(f._duration.to_numpy(float)), min_support)

    def transform(self, f):
        out = np.zeros((len(f), 4))
        logs = np.log1p(f.duration_ms.to_numpy(float))
        bins = np.floor(np.log2(f.totalCount.to_numpy(float)+1)).astype(int)
        # Cache group statistics, not anomaly scores.
        stats = {}
        for i, (a, p, b, v) in enumerate(zip(f.adapterType, f.processId, bins, logs)):
            key = (a, p, b)
            if key not in stats:
                lookup = dict(adapterType=a, processId=p, _count_bin=b)
                sample, level = self.global_sample, len(self.tables)
                for j, (cols, table) in enumerate(self.tables):
                    candidate = table.get(tuple(lookup[c] for c in cols))
                    if candidate is not None:
                        sample, level = candidate, j
                        break
                q25, median, q75 = np.quantile(sample, [.25, .5, .75])
                stats[key] = (sample, level, median, max(q75-q25, .15))
            sample, level, median, width = stats[key]
            rank = np.searchsorted(sample, v, side="left")
            upper = -np.log((len(sample)-rank+.5)/(len(sample)+1))
            out[i] = ((v-median)/width, upper, level, len(sample))
        return out


class EventFeatures:
    """State survives calls; every emitted row maps to an original Process row.

    Equal availableTime rows are scored against the same prior state. Batch and
    streaming equivalence requires a complete availability-timestamp batch.
    No artificial zero rows, source dates, traffic counts or durations are made.
    """
    def __init__(self, reference):
        self.reference = reference
        self.states = {}

    def transform(self, records):
        f = normalize(records)
        r = self.reference.transform(f)
        values = np.zeros((len(f), len(FEATURES)))
        ld = np.log1p(f.duration_ms.to_numpy(float))
        lc = np.log1p(f.totalCount.to_numpy(float))
        values[:, :6] = np.column_stack((ld, lc, r[:, 0], r[:, 1], ld-lc, r[:, 2] == 4))
        now = f.availableTime.astype("datetime64[ns, UTC]").astype("int64").to_numpy()/1e9
        local = f.availableTime.dt.tz_convert("Asia/Seoul")
        row_names = f.event_row_id.astype(str).to_numpy()
        hours = local.dt.hour.to_numpy()
        weekdays = local.dt.dayofweek.to_numpy()
        group_keys = list(zip(f.case_id.astype(str), f.adapterType.astype(str), f.processId.astype(str)))
        grouped = defaultdict(list)
        for i, key in enumerate(group_keys):
            grouped[key].append(i)
        for key, indices in grouped.items():
            ids = sorted(indices, key=lambda i: (now[i], row_names[i]))
            state = self.states.setdefault(key, dict(history=deque(maxlen=32), arrivals=deque(),
                              prefix=[], gaps=deque(maxlen=32), last=None, cusum=0., last_time=None))
            cursor = 0
            while cursor < len(ids):
                end = cursor+1
                t = now[ids[cursor]]
                while end < len(ids) and now[ids[end]] == t:
                    end += 1
                batch = ids[cursor:end]
                if state["last_time"] is not None and t <= state["last_time"]:
                    raise ValueError("Time moved backwards or a timestamp batch was split; reset state/replay history")
                while state["arrivals"] and state["arrivals"][0] < t-900:
                    state["arrivals"].popleft()
                times = np.array(state["arrivals"], float)
                counts = [len(times)-np.searchsorted(times, t-w, side="left") for w in (60, 300, 900)]
                hist = np.array(state["history"], float)
                prefix = np.array(state["prefix"], float)
                gaps = np.array(state["gaps"], float)
                support = len(hist)
                gap = max(t-state["last"], 0.) if state["last"] is not None else 0.
                prior_gap = max(float(np.median(gaps)), .001) if len(gaps) else 60.
                median = float(np.median(hist[:, 1])) if support else 0.
                spread = max(float(np.quantile(hist[:, 1], .75)-np.quantile(hist[:, 1], .25)), .15) if support >= 4 else .15
                pm = float(np.median(prefix[:, 1])) if len(prefix) else 0.
                ps = max(float(np.median(np.abs(prefix[:, 1]-pm))*1.4826), .15) if len(prefix) else .15
                # Incomplete early context is explicitly marked, not certified normal.
                for i in batch:
                    shift = max((ld[i]-median)/spread, 0.) if support >= 3 else 0.
                    pshift = max((ld[i]-pm)/ps, 0.) if len(prefix) >= 3 else 0.
                    slope = 0.
                    if support >= 3:
                        recent = hist[-10:]
                        x = (recent[:, 0]-t)/60
                        y = recent[:, 1]
                        slope = max(float(np.dot(x-x.mean(), y-y.mean())/max(np.dot(x-x.mean(), x-x.mean()), 1.))*5/spread, 0.)
                    residual_mean = max(float(hist[-10:, 2].mean()), 0.) if support else 0.
                    residual_max = max(float(hist[-10:, 2].max()), 0.) if support else 0.
                    persistence = float(np.mean(hist[-10:, 2] > 1.)) if support else 0.
                    cusum = max(0., .85*state["cusum"]+min(pshift, 20.)-.5) if len(prefix) >= 3 else 0.
                    # Current row is included in counts; no simultaneous other row leaks.
                    recent_rate = 60/prior_gap if len(gaps) >= 3 else None
                    recent_burst = max(np.log((counts[1]+1.5)/(recent_rate*5+.5)), 0.) if recent_rate is not None else 0.
                    if len(prefix) >= 5:
                        exposure = max(prefix[-1, 0]-prefix[0, 0], 1.)
                        prefix_rate = (len(prefix)-1)/exposure
                        prefix_burst = max(np.log((counts[1]+1.5)/(prefix_rate*300+.5)), 0.)
                    else:
                        prefix_burst = 0.
                    vals = (shift, pshift, residual_mean, residual_max, slope, cusum,
                            persistence, *np.log1p(np.array(counts)+1), recent_burst, prefix_burst,
                            np.log1p(gap), np.log1p(gap/prior_gap) if support >= 3 else 0.,
                            np.log1p(gap/max(float(np.mean(gaps[-8:])), .001)) if len(gaps) >= 3 else 0.,
                            np.log1p(support), np.sin(2*np.pi*hours[i]/24),
                            np.cos(2*np.pi*hours[i]/24),
                            np.sin(2*np.pi*weekdays[i]/7), np.cos(2*np.pi*weekdays[i]/7))
                    values[i, 6:] = vals
                # Atomic update after all tied rows are scored.
                for i in batch:
                    entry = (t, ld[i], r[i, 0])
                    state["history"].append(entry)
                    if len(state["prefix"]) < 12:
                        state["prefix"].append(entry)
                    state["arrivals"].append(t)
                if gap > 0 and state["last"] is not None:
                    state["gaps"].append(gap)
                state["cusum"] = max(values[i, FEATURES.index("delay_cusum")] for i in batch)
                state["last"] = state["last_time"] = t
                cursor = end
        for j, c in enumerate(FEATURES):
            f[c] = np.nan_to_num(np.clip(values[:, j], -100, 100), nan=0.)
        f["reference_level"], f["reference_support"] = r[:, 2], r[:, 3]
        return f
