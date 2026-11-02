"""Row-driven incident confirmation; no new observations during silence.

Original execution scores never change. Optional weak-context filtering affects
confirmed incident notifications, not the underlying Process anomaly candidates.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Policy:
    ttl_seconds: float = 300.
    context_min: float = 1.
    traffic_min: float = 0.
    normal_confirmations: int = 1
    fast_traffic: bool = True
    filter_weak_context: bool = False

    def validate(self):
        if self.ttl_seconds < 0 or self.context_min < 0 or self.traffic_min < 0:
            raise ValueError('Policy durations/effect sizes must be nonnegative')
        if self.normal_confirmations < 1:
            raise ValueError('At least one actual normal execution is required to clear')


@dataclass
class State:
    active: bool = False
    expires: float = -np.inf
    normals: int = 0


def transition(state, now, onset, any_alarm, normal_rows, policy):
    """One atomic delivery timestamp. No clock call can open a new incident."""
    if state.active and now > state.expires:
        state.active = False
        state.normals = 0
    if onset and policy.ttl_seconds > 0:
        state.active = True
        state.expires = now + policy.ttl_seconds
        state.normals = 0
    elif state.active:
        if policy.normal_confirmations == 1:
            # Exact compatibility with the preceding latest-observation policy.
            state.active = False
            state.normals = 0
        elif not any_alarm:
            state.normals += normal_rows
            if state.normals >= policy.normal_confirmations:
                state.active = False
                state.normals = 0
        else:
            state.normals = 0
    return state.expires if state.active else -np.inf


def evidence(rows, predicted, detector, policy):
    policy.validate()
    alarm = np.asarray(predicted, bool)
    family = detector.state(rows)[0] % 4
    warm = rows.history_support.to_numpy(float) >= np.log1p(3)
    latency = np.isin(family, [1, 2])
    if policy.context_min > 0:
        latency = rows.local_upper.to_numpy(float) >= policy.context_min
    traffic = family == 3
    if policy.traffic_min > 0:
        traffic = rows.traffic_lift_1m.to_numpy(float) >= policy.traffic_min
    if policy.fast_traffic:
        traffic &= rows.traffic_lift_1m.to_numpy(float) >= rows.traffic_lift_3m.to_numpy(float)
    onset = alarm & warm & (latency | traffic)
    if policy.filter_weak_context:
        # Cold/static-point candidates are immediate. Warm contextual candidates
        # must pass the same observable onset gate used by the runtime tracker.
        fresh = alarm & (~warm | (family == 0) | onset)
    else:
        fresh = alarm
    return alarm, onset, fresh


def project(rows, predicted, detector, windows, policy):
    """Read original events and return confirmed alerts on a requested grid.

    Grid timestamps are evaluation queries, not fabricated Process input rows.
    Ground truth, event endpoints and task names are absent from this function.
    """
    alarm, onset, fresh = evidence(rows, predicted, detector, policy)
    times = rows.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
    ends = (pd.to_datetime(windows.window_end, format='ISO8601').dt.tz_localize('Asia/Seoul')
            .dt.tz_convert('UTC').astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9)
    result = np.zeros(len(windows), bool)
    groups = rows.groupby('case_id', sort=False).indices
    for case, wi in windows.groupby('case_id', sort=False).indices.items():
        ri = groups.get(case)
        if ri is None:
            continue
        ri = np.asarray(ri)
        ri = ri[np.argsort(times[ri], kind='stable')]
        clock = times[ri]
        starts = np.r_[0, np.flatnonzero(np.diff(clock) != 0)+1]
        stops = np.r_[starts[1:], len(ri)]
        clock = clock[starts]
        batch_fresh = np.maximum.reduceat(fresh[ri], starts)
        batch_alarm = np.maximum.reduceat(alarm[ri], starts)
        batch_onset = np.maximum.reduceat(onset[ri], starts)
        expiry = np.full(len(starts), -np.inf)
        state = State()
        for j, (lo, hi) in enumerate(zip(starts, stops)):
            expiry[j] = transition(state, clock[j], bool(batch_onset[j]), bool(batch_alarm[j]), hi-lo, policy)
        upper = np.searchsorted(clock, ends[wi], side='left')
        lower = np.searchsorted(clock, ends[wi]-60, side='left')
        count = np.r_[0, np.cumsum(batch_fresh)]
        hits = count[upper] > count[lower]
        last = upper-1
        known = last >= 0
        active = np.zeros(len(wi), bool)
        active[known] = expiry[last[known]] >= ends[wi][known]
        result[wi] = hits | active
    return result
