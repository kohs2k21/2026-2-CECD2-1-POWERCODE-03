"""Causal incident state, separate from original execution-row classification.

An already-open contextual alarm can stay open between original observations.
This never opens an input-stop incident from a previously normal execution.
No true event boundary, anomaly label or future recovery is consulted.
"""
import numpy as np
import pandas as pd


DEFAULT_TTL_SECONDS=300  # same finite horizon as the past-five-minute context


def project_to_windows(rows,predicted,detector,windows,ttl_seconds=DEFAULT_TTL_SECONDS,
                       context_min=0.,fast_traffic=False):
    """Project alerts/state to a requested clock grid without creating Process rows.

    Point-only/cold alerts are instantaneous; warm delay/accumulation/traffic
    alarms persist until the next observation clears them or finite TTL expires.
    A minute is marked if an execution alert occurred in it OR an incident is
    still active at its end. Both outputs are causal, not oracle point adjustment.
    """
    if ttl_seconds<0:raise ValueError('TTL must be nonnegative')
    if hasattr(detector,'state'):
        family=detector.state(rows)[0]%4
    else:
        family=np.zeros(len(rows),int)
    warm=rows.history_support.to_numpy(float)>=np.log1p(3)
    contextual=np.isin(family,[1,2])
    if context_min>0:
        contextual=rows.local_upper.to_numpy(float)>=context_min
    traffic=family==3
    if fast_traffic and traffic.any():
        traffic &= rows.traffic_lift_1m.to_numpy(float)>=rows.traffic_lift_3m.to_numpy(float)
    can_hold=np.asarray(predicted,bool) & warm & (contextual|traffic)
    timestamps=rows.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
    end=(pd.to_datetime(windows.window_end,format='ISO8601')
         .dt.tz_localize('Asia/Seoul').dt.tz_convert('UTC')
         .astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9)
    start=end-60
    result=np.zeros(len(windows),bool)
    groups={c:np.asarray(ids) for c,ids in rows.groupby('case_id',sort=False).indices.items()}
    for c,window_ids in windows.groupby('case_id',sort=False).indices.items():
        ids=groups.get(c)
        if ids is None:continue
        ids=ids[np.argsort(timestamps[ids],kind='stable')]
        times=timestamps[ids]
        hit=np.asarray(predicted,bool)[ids]
        holds=can_hold[ids]
        # Timestamp batches are atomic: input ordering within a delivery cannot
        # decide whether an incident remains open.
        if len(times)>1 and (np.diff(times)==0).any():
            beginnings=np.r_[0,np.flatnonzero(np.diff(times)!=0)+1]
            hit=np.maximum.reduceat(hit,beginnings)
            holds=np.maximum.reduceat(holds,beginnings)
            times=times[beginnings]
        high=np.searchsorted(times,end[window_ids],side='left')
        low=np.searchsorted(times,start[window_ids],side='left')
        cumsum=np.r_[0,np.cumsum(hit)]
        fresh=cumsum[high]>cumsum[low]
        last=high-1
        valid=last>=0
        active=np.zeros(len(window_ids),bool)
        active[valid]=(holds[last[valid]] & ((end[window_ids][valid]-times[last[valid]])<=ttl_seconds))
        result[window_ids]=fresh|active
    return result
