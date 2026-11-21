"""Normal-training anchors and causal accumulated deviation features.

The accumulation is a feature inspired by CUSUM, not a new hard rule or an
exact reproduction of a calibrated CUSUM control chart. No clean-prefix,
future observation, scenario or evaluation label is assumed.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from model_improvement.refine import ALL_FEATURES as OLD_FEATURES
from model_research_v8.features import FEATURES as TAIL_FEATURES

DRIFT = ("fixed_shift_5m", "fixed_shift_15m", "fixed_positive_cusum",
         "fixed_positive_ewma", "positive_persistence_15m")
TRAFFIC = ("arrival_dev_1m", "arrival_dev_5m", "arrival_dev_15m", "zero_run",
           "arrival_ewma", "arrival_change")
LATENCY = ("duration_upper_max_1m", "duration_lower_max_1m", "duration_upper_mean_5m",
           "duration_lower_mean_5m", "duration_upper_max_5m")


def matched_precision(frame):
    frame = frame.copy()
    columns = [c for c in dict.fromkeys((*OLD_FEATURES, *TAIL_FEATURES)) if c in frame]
    frame[columns] = frame[columns].astype(np.float32).astype(float)
    return frame


def bins(frame):
    # Routing only. The log mean uses completed observed rows, not a label.
    values = pd.to_numeric(frame.log_mean_count_5m, errors="coerce").fillna(0).to_numpy(float)
    return np.floor(np.maximum(values, 0) / np.log(2)).astype(int)


@dataclass
class FixedNormalAnchor:
    tables: list
    pooled: tuple
    minimum: int = 30

    @classmethod
    def fit(cls, train, minimum=30):
        frame = train.loc[train.latency_assessed].copy()
        frame["_count_bin"] = bins(frame)
        frame["_r"] = pd.to_numeric(frame.residual_mean_1m)
        frame = frame.loc[np.isfinite(frame._r)]
        if not len(frame):
            raise ValueError("No normal completed observations for fixed anchor")

        def stats(values):
            median = float(np.median(values))
            mad = float(np.median(np.abs(values - median)))
            return median, max(1.4826 * mad, .25), len(values)

        levels = (("adapterType", "processId", "_count_bin"),
                  ("adapterType", "_count_bin"), ("adapterType", "processId"), ("adapterType",))
        tables = []
        for keys in levels:
            table = {}
            for key, group in frame.groupby(list(keys), observed=True):
                if len(group) >= minimum:
                    table[key if isinstance(key, tuple) else (key,)] = stats(group._r.to_numpy())
            tables.append((keys, table))
        return cls(tables, stats(frame._r.to_numpy()), minimum)

    def augment(self, original):
        frame = original.copy().reset_index(drop=True)
        dates = pd.to_datetime(frame.window_start, format="ISO8601")
        gaps = dates.groupby(frame.case_id, sort=False).diff().dropna()
        if not gaps.eq(pd.Timedelta(minutes=1)).all():
            raise ValueError("Need chronological contiguous per-stream minute grid")
        count_bins = bins(frame)
        shifts = np.zeros(len(frame))
        levels = np.full(len(frame), -1, dtype=int)
        observed = frame.latency_assessed.to_numpy(bool)
        for i, (adapter, process, count, value) in enumerate(zip(
                frame.adapterType, frame.processId, count_bins, frame.residual_mean_1m)):
            if not observed[i] or not np.isfinite(value):
                continue
            lookup = {"adapterType": adapter, "processId": process, "_count_bin": count}
            reference, level = self.pooled, len(self.tables)
            for j, (keys, table) in enumerate(self.tables):
                found = table.get(tuple(lookup[k] for k in keys))
                if found is not None:
                    reference, level = found, j
                    break
            shifts[i] = (value - reference[0]) / reference[1]
            levels[i] = level
        frame["fixed_shift_1m"], frame["fixed_anchor_level"] = shifts, levels
        sample = pd.Series(np.where(observed, shifts, np.nan), index=frame.index)
        for width in (5, 15):
            value = sample.groupby(frame.case_id, sort=False).rolling(width, min_periods=1).mean()
            frame[f"fixed_shift_{width}m"] = value.reset_index(level=0, drop=True).reindex(frame.index).fillna(0)
        positive = pd.Series(np.where(observed, shifts > .25, np.nan), index=frame.index)
        value = positive.groupby(frame.case_id, sort=False).rolling(15, min_periods=1).mean()
        frame["positive_persistence_15m"] = value.reset_index(level=0, drop=True).reindex(frame.index).fillna(0)
        cumulative, ewma = np.zeros(len(frame)), np.zeros(len(frame))
        for ids in frame.groupby("case_id", sort=False).indices.values():
            running = smooth = 0.
            for i in ids:
                if observed[i]:
                    running = max(0., .98 * running + shifts[i] - .1)
                    smooth = .2 * max(shifts[i], 0.) + .8 * smooth
                else:
                    # Missing completion is not an invented healthy duration.
                    running *= .98
                    smooth *= .8
                cumulative[i], ewma[i] = np.log1p(running), np.log1p(smooth)
        frame["fixed_positive_cusum"], frame["fixed_positive_ewma"] = cumulative, ewma
        if not np.isfinite(frame[list(DRIFT)].to_numpy()).all():
            raise ValueError("Non-finite drift features")
        return frame
