"""Causal stream cadence/latency context. No event labels, future, or case semantics."""
import numpy as np
import pandas as pd

NEW=("own_arrival_change","own_arrival_lift_5m","prefix_arrival_lift_5m",
     "gap_expected_prefix","gap_expected_recent","empty_expected_rate",
     "duration_tail_spread_local","delay_population_pressure","residual_level_5m",
     "recent_residual_lift","cadence_ready","traffic_assessed","latency_assessed","temporal_assessed")


def augment(original):
    frame=original.copy().reset_index(drop=True)
    if frame.duplicated(["case_id","window_start"]).any():
        raise ValueError("Observed and normal paired cohorts must be augmented separately")
    counts=pd.to_numeric(frame.native_execution_arrivals).to_numpy(float)
    dates=pd.to_datetime(frame.window_start,format="ISO8601")
    gaps=dates.groupby(frame.case_id,sort=False).diff().dropna()
    if not gaps.eq(pd.Timedelta(minutes=1)).all():raise ValueError("Contiguous chronological minute grid required")
    output=np.zeros((len(frame),7))
    for ids in frame.groupby("case_id",sort=False).indices.values():
        c=counts[ids];prefix=[];zero_run=0
        for age,i in enumerate(ids):
            current=c[age]
            zero_run=zero_run+1 if current==0 else 0
            if age>=3:
                recent=c[max(0,age-15):age]
                rate=float(recent.mean())
                initial=float(np.mean(prefix))
                width=min(age+1,5);observed=float(c[max(0,age-4):age+1].sum())
                output[i]=[np.log((current+1)/(rate+1)),
                    np.log((observed+1)/(rate*width+1)),
                    np.log((observed+1)/(initial*width+1)),
                    np.log1p(zero_run*initial),np.log1p(zero_run*rate),
                    float(current==0)*np.log1p(initial),1.]
            if len(prefix)<8:prefix.append(current)
    for j,column in enumerate((*NEW[:6],"cadence_ready")):frame[column]=output[:,j]
    frame["duration_tail_spread_local"]=(frame.log_max_duration_1m-frame.log_p50_duration_5m).clip(lower=0)
    completed=frame.completion_count_1m.groupby(frame.case_id,sort=False).transform(lambda s:s.rolling(5,min_periods=1).sum())
    frame["delay_population_pressure"]=np.arcsinh(frame.residual_mean_5m.clip(lower=0))*np.sqrt(np.log1p(completed))
    frame["residual_level_5m"]=np.arcsinh(frame.residual_mean_5m)
    past=frame.residual_mean_1m.groupby(frame.case_id,sort=False).shift(1)
    past=past.groupby(frame.case_id,sort=False).transform(lambda s:s.rolling(15,min_periods=3).median())
    frame["recent_residual_lift"]=np.arcsinh(frame.residual_mean_5m-past)
    return frame
