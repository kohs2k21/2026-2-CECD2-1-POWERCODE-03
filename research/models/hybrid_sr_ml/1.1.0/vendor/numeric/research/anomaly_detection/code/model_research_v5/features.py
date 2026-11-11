"""Causal, self-relative features: no 'first minutes are normal' assumption.

The reference at t is computed strictly from earlier decisions of that monitored
Adapter/Process stream. Incoming anomalies CAN contaminate later context. This
limitation is measured, not hidden by point-adjusting labels or using controls.
"""
import numpy as np
import pandas as pd

CONTEXT = (
    "arrival_dev_1m", "arrival_dev_5m", "arrival_dev_15m", "zero_run",
    "max_delay_change", "p95_delay_change", "duration_slope",
    "delay_tail_spread", "delay_ewma", "delay_cusum",
    "history_support", "context_age", "missing_duration_1m",
)
ANCHOR = CONTEXT+("p95_duration_relative", "residual_p95_5m", "log_mean_count_5m")
VIEWS = {"self_relative": CONTEXT, "with_source_anchor": ANCHOR}
ALL_FEATURES = tuple(dict.fromkeys((*CONTEXT, *ANCHOR)))


def rolling(frame, values, width, operation="mean"):
    grouped = pd.Series(values, index=frame.index).groupby(frame.case_id, sort=False)
    return getattr(grouped.rolling(width, min_periods=1), operation)().reset_index(
        level=0, drop=True).reindex(frame.index)


def context_features(frame):
    frame = frame.copy().reset_index(drop=True)
    # Caller already has a chronological, contiguous native minute grid.
    stamps = pd.to_datetime(frame.window_start, format="ISO8601")
    gaps = stamps.groupby(frame.case_id, sort=False).diff().dropna()
    if not gaps.eq(pd.Timedelta(minutes=1)).all():
        raise ValueError("Context must be chronological and contiguous within stream")
    ages = frame.groupby("case_id", sort=False).cumcount()+1
    frame["context_age"] = np.log1p(np.minimum(ages, 15))/np.log(16)
    frame["phase"] = np.where(ages <= 5, "startup", "steady")
    # Use ONLY earlier observations. No counterfactual, future event boundary,
    # label, total episode length, or source ID is consulted.
    current = pd.to_numeric(frame.log_max_duration_1m)
    previous = current.groupby(frame.case_id, sort=False).shift(1)
    prior = rolling(frame, previous, 5, "median")
    support = previous.notna().astype(float)
    frame["history_support"] = rolling(frame, support, 5, "sum").clip(upper=5)/5
    delta = current-prior
    frame["max_delay_change"] = delta.fillna(0.)
    p50 = pd.to_numeric(frame.log_p50_duration_5m)
    prior_p50 = p50.groupby(frame.case_id, sort=False).shift(1)
    prior_p50 = rolling(frame, prior_p50, 5, "median")
    frame["p95_delay_change"] = (frame.log_p95_duration_5m-prior_p50).fillna(0.)
    frame["delay_tail_spread"] = (frame.log_p95_duration_5m-p50).fillna(0.)
    # A short-vs-long lagged reference is not a regression on future data.
    lagged_mean = current.groupby(frame.case_id, sort=False).shift(1)
    reference = rolling(frame, lagged_mean, 15)
    short = rolling(frame, current, 3)
    frame["duration_slope"] = (short-reference).fillna(0.)
    frame["delay_ewma"] = frame.max_delay_change.groupby(frame.case_id, sort=False).transform(
        lambda s: s.ewm(alpha=.2, adjust=False).mean())
    # Leaky CUSUM-style FEATURE, not a Page/CUSUM control-chart implementation
    # or a calibrated standalone hard rule. Drift/serial dependence remain.
    cusum = np.zeros(len(frame), dtype=float)
    for ids in frame.groupby("case_id", sort=False).groups.values():
        running = 0.
        for index in ids:
            running = max(0., .9*running+frame.max_delay_change.iat[index]-.05)
            cusum[index] = np.log1p(running)
    frame["delay_cusum"] = cusum
    if not np.isfinite(frame[list(CONTEXT)].to_numpy()).all():
        raise ValueError("Non-finite self-relative feature")
    return frame


def segment_native(native, rows, minutes):
    """Normal replay reset augmentation. SAME source executions, not new data.

    Counts retain original minute meaning; duration quantiles MUST be rebuilt
    from completions AFTER each reset, rather than copy a previous 5m P95.
    """
    if minutes < 1:
        raise ValueError("Invalid reset interval")
    native = native.sort_values(["case_id", "window_start"], kind="stable").reset_index(drop=True).copy()
    start = pd.to_datetime(native.window_start, format="ISO8601").astype("datetime64[ns]").astype("int64")/1e9
    first = start.groupby(native.case_id, sort=False).min()
    block = ((start-native.case_id.map(first))//(minutes*60)).astype(int)
    native["case_id"] = native.case_id.astype(str)+"-reset"+str(minutes)+"-"+block.astype(str)
    rich = rows.copy()
    blocks = ((rich.end_seconds-rich.case_id.map(first))//(minutes*60)).astype(int)
    rich["case_id"] = rich.case_id.astype(str)+"-reset"+str(minutes)+"-"+blocks.astype(str)
    # No completed log outside the observed minute grid is pulled in.
    rich = rich.loc[rich.case_id.isin(native.case_id)].reset_index(drop=True)
    return native, rich
