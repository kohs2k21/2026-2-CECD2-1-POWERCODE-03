"""Observed-completion temporal features; no clean-prefix/future assumption.

Missing completion minutes are skipped, not healthy zero durations. Prefixes
may be anomalous and then these self-relative features can miss onset anomalies;
the original source-conditioned channel is retained to address that case.
"""
import numpy as np
import pandas as pd

TEMPORAL = ("prefix_duration_z", "recent_duration_lift", "duration_trend_z",
            "positive_change_fraction", "duration_level_ewma")


def augment(original):
    frame = original.copy().reset_index(drop=True)
    dates = pd.to_datetime(frame.window_start, format="ISO8601")
    gaps = dates.groupby(frame.case_id, sort=False).diff().dropna()
    if not gaps.eq(pd.Timedelta(minutes=1)).all(): raise ValueError("Contiguous chronological minute grid required")
    out = np.zeros((len(frame), len(TEMPORAL)))
    eligible = np.zeros(len(frame), bool)
    values = pd.to_numeric(frame.log_max_duration_1m).to_numpy(float)
    observed = frame.latency_assessed.to_numpy(bool) & np.isfinite(values)
    for ids in frame.groupby("case_id", sort=False).indices.values():
        prefix, history = [], []
        smooth = 0.
        for age, i in enumerate(ids):
            if not observed[i]: continue
            current = values[i]
            # Reference is already observed before the current completion.
            if len(prefix) >= 4:
                ref = float(np.median(prefix))
                scale = max(1.4826*float(np.median(np.abs(np.asarray(prefix)-ref))), .03)
                lift = (current-ref)/scale
                recent = [(t, v) for t, v in history if age-t <= 15]
                if len(recent) >= 4:
                    old = np.asarray([v for _, v in recent])
                    center = float(np.median(old))
                    noise = max(1.4826*float(np.median(np.abs(old-center))), .03)
                    delta = (current-center)/noise
                    points = recent+[(age, current)]
                    times = np.asarray([t for t, _ in points], float)
                    sample = np.asarray([v for _, v in points], float)
                    centered = times-times.mean()
                    slope = float(np.dot(centered, sample-sample.mean())/max(np.dot(centered, centered), 1.))
                    # A 10-minute slope in units of observed normal fluctuation.
                    trend = slope*10/noise
                    increments = np.diff(sample)
                    fraction = float(np.mean(increments > 1e-5))
                    eligible[i] = True
                else: delta = trend = fraction = 0.
                smooth = .2*lift+.8*smooth
                out[i] = np.clip([lift, delta, trend, fraction, smooth], -100, 100)
            if len(prefix) < 8: prefix.append(current)
            history.append((age, current))
    for j, column in enumerate(TEMPORAL): frame[column] = out[:, j]
    frame["temporal_assessed"] = eligible
    return frame
