"""Train-only hierarchical conditional tails and causal minute aggregates.

Empirical tails are feature transforms, NOT guaranteed p-values, COPOD/ECOD,
or additional hard rules. IDs select references; they are never tree features.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from model_evaluation.data import unix_seconds

TRAFFIC = ("arrival_dev_1m", "arrival_dev_5m", "arrival_dev_15m", "zero_run", "arrival_ewma", "arrival_change")
LATENCY = ("duration_upper_max_1m", "duration_lower_max_1m", "duration_upper_mean_5m", "duration_lower_mean_5m",
           "duration_upper_max_5m", "duration_upper_change", "duration_upper_ewma", "duration_upper_anchor")
FEATURES = TRAFFIC+LATENCY


@dataclass
class ConditionalTails:
    tables: list
    pooled: np.ndarray
    minimum: int

    @classmethod
    def fit(cls, source, minimum=20):
        frame = source.copy()
        frame["_count"] = pd.to_numeric(frame.totalCount)
        frame["_duration"] = pd.to_numeric(frame.duration_ms)
        if (frame._count < 0).any() or (frame._duration < 0).any(): raise ValueError("Invalid normal source")
        levels = (("adapterType", "processId", "_count"), ("adapterType", "_count"),
                  ("adapterType", "processId"), ("adapterType",))
        tables = []
        for keys in levels:
            table = {}
            for key, group in frame.groupby(list(keys), observed=True):
                if len(group) >= minimum:
                    table[key if isinstance(key, tuple) else (key,)] = np.sort(group._duration.to_numpy(float))
            tables.append((keys, table))
        return cls(tables, np.sort(frame._duration.to_numpy(float)), minimum)

    def transform(self, rows):
        result = np.zeros((len(rows), 3), float)
        # Grouping avoids a Python search for each individual completion.
        for (adapter, process, count), ids in rows.groupby(["adapterType", "processId", "totalCount"], sort=False, observed=True).indices.items():
            lookup = {"adapterType": adapter, "processId": process, "_count": float(count)}
            reference, level = self.pooled, len(self.tables)
            for i, (keys, table) in enumerate(self.tables):
                found = table.get(tuple(lookup[k] for k in keys))
                if found is not None: reference, level = found, i; break
            values = rows.iloc[ids].duration.to_numpy(float)
            if not np.isfinite(values).all() or (values < 0).any(): raise ValueError("Invalid duration")
            n = len(reference)
            upper = (n-np.searchsorted(reference, values, side="left")+1)/(n+1)
            lower = (np.searchsorted(reference, values, side="right")+1)/(n+1)
            result[ids, 0], result[ids, 1], result[ids, 2] = -np.log(upper), -np.log(lower), level
        return result


def add_duration_features(frame, completions, reference):
    """Completed at end<t only. Never use arrival-time future duration."""
    frame = frame.copy().reset_index(drop=True)
    seconds = unix_seconds(frame.window_start)+60
    sizes = frame.groupby("case_id", sort=False).cumcount().to_numpy()+1
    if len(completions):
        raw = completions.copy().reset_index(drop=True)
        transformed = reference.transform(raw)
        raw["_upper"], raw["_lower"], raw["_level"] = transformed.T
    else: raw = pd.DataFrame(columns=["case_id", "end_seconds", "_upper", "_lower", "_level"])
    raw_groups = {c: g.sort_values("end_seconds", kind="stable") for c, g in raw.groupby("case_id", sort=False)}
    columns = LATENCY[:5]
    output = {c: np.zeros(len(frame)) for c in columns}
    completed, fallback = np.zeros(len(frame), int), np.full(len(frame), -1, int)
    for case, ids in frame.groupby("case_id", sort=False).indices.items():
        group = raw_groups.get(case)
        if group is None: continue
        times, upper, lower = (group[c].to_numpy(float) for c in ("end_seconds", "_upper", "_lower"))
        hi = np.searchsorted(times, seconds[ids], side="left")
        for width in (1, 5):
            lo = np.searchsorted(times, seconds[ids]-np.minimum(sizes[ids], width)*60, side="left")
            for row, first, last in zip(ids, lo, hi):
                if last <= first: continue
                u, l = upper[first:last], lower[first:last]
                if width == 1:
                    output["duration_upper_max_1m"][row], output["duration_lower_max_1m"][row] = u.max(), l.max()
                    completed[row] = last-first
                    fallback[row] = int(group._level.iloc[first:last].max())
                else:
                    output["duration_upper_mean_5m"][row], output["duration_lower_mean_5m"][row] = u.mean(), l.mean()
                    output["duration_upper_max_5m"][row] = u.max()
    for column, values in output.items(): frame[column] = values
    frame["completion_count_1m"], frame["duration_reference_level"] = completed, fallback
    mean = frame.duration_upper_mean_5m
    past = mean.groupby(frame.case_id, sort=False).transform(lambda x: x.shift().rolling(15, min_periods=1).mean()).fillna(0)
    frame["duration_upper_change"] = mean-past
    frame["duration_upper_ewma"] = mean.groupby(frame.case_id, sort=False).transform(lambda x: x.ewm(alpha=.2, adjust=False).mean())
    # First five *observed* nonempty minute means, frozen only after they arrive.
    # No assertion that this prefix is clean; anomalies can contaminate it.
    anchor = np.zeros(len(frame))
    for ids in frame.groupby("case_id", sort=False).indices.values():
        prefix = []
        for row in ids:
            if len(prefix) >= 3 and completed[row]:
                anchor[row] = mean.iloc[row]-np.mean(prefix)
            if len(prefix) < 5 and completed[row]: prefix.append(mean.iloc[row])
    frame["duration_upper_anchor"] = anchor
    return add_cadence(frame)


def add_cadence(frame):
    frame = frame.copy()
    if "native_execution_arrivals" not in frame: raise ValueError("Need native minute arrivals, not rolling totals")
    counts = pd.to_numeric(frame.native_execution_arrivals)
    # At least three PRIOR nonempty observed minutes. No clean-prefix assumption,
    # no episode total-length/severity/template labels used for routing.
    previous = counts.gt(0).groupby(frame.case_id, sort=False).cumsum()-counts.gt(0)
    frame["traffic_assessed"] = previous.ge(3)
    frame["latency_assessed"] = frame.completion_count_1m.gt(0)
    return frame
