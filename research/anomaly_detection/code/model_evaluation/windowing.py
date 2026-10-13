"""Causal lookback windows on a common one-minute decision/label grid.

Long windows do not change ground truth or evaluation denominators. Completion
quantiles are recomputed from raw durations; minute quantiles are never averaged.
"""
import hashlib
import json

import numpy as np
import pandas as pd

from .data import KEYS, unix_seconds, window_features

CANDIDATES = (1, 2, 3, 5, 10, 15)


def reserved_validation_ids(bundle):
    return {t['template_id'] for t in bundle['templates']
            if int(hashlib.sha256(('window-validation-v2:'+t['template_id']).encode()).hexdigest()[:16],16)%4 == 0}


def window_reference_split(partitions, reserved):
    filtered = {n:p.loc[~p.source_template_id.isin(reserved)].copy() for n,p in partitions.items()}
    sets = {n:set(p.source_template_id) for n,p in filtered.items()}
    assert not set.union(*sets.values()) & reserved
    support = {}
    for n,p in filtered.items():
        for a,g in p.groupby('adapterType',observed=True):
            support.setdefault(str(a),{})[n] = int(g.source_template_id.nunique())
    train = filtered['train']
    baselines = {'process':train.groupby(['adapterType','processId'],observed=True).duration_ms.median().to_dict(),
                 'adapter':train.groupby('adapterType',observed=True).duration_ms.median().to_dict(),
                 'global':float(train.duration_ms.median())}
    return filtered, sets, support, baselines


class PriorHistory:
    """True rolling count quantiles from the prior synthetic minute history."""
    def __init__(self, path):
        frame = pd.read_csv(path,encoding='utf-8-sig')
        dates = pd.to_datetime(frame.window_start,format='ISO8601')
        frame = frame.assign(_date=dates).sort_values('_date').reset_index(drop=True)
        self.values = {}
        for width in range(1,max(CANDIDATES)+1):
            counts = frame.execution_arrivals.rolling(width,min_periods=width).sum()
            for (weekend,hour),ids in frame.groupby([frame._date.dt.dayofweek.ge(5),frame._date.dt.hour]).groups.items():
                sample = counts.loc[ids].dropna()
                self.values[width,bool(weekend),int(hour)] = (float(sample.median()),float(sample.quantile(.95)),len(sample))

    def lookup(self, dates, widths):
        return np.array([self.values[int(w),bool(d.dayofweek>=5),int(d.hour)]
                         for d,w in zip(dates,widths)],dtype=float)


def aggregate_windows(native, completions, minutes, history, baselines):
    if minutes not in CANDIDATES:
        raise ValueError('Unsupported window candidate')
    frame = native.sort_values(['case_id','window_start'],kind='stable').reset_index(drop=True).copy()
    dates = pd.to_datetime(frame.window_start,format='ISO8601')
    sizes = frame.groupby('case_id',sort=False).cumcount().to_numpy()+1
    # Cold-start windows use only the available history, with matching prior width.
    widths = np.minimum(sizes,minutes)
    frame['native_execution_arrivals'] = frame.execution_arrivals
    for width,column in ((minutes,'execution_arrivals'),(5,'execution_arrivals_5m')):
        frame[column] = frame.native_execution_arrivals.groupby(frame.case_id,sort=False).rolling(width,min_periods=1).sum().reset_index(level=0,drop=True).reindex(frame.index)
    prior = history.lookup(dates,widths)
    prior5 = history.lookup(dates,np.minimum(sizes,5))
    frame['history_median_arrivals'],frame['history_p95_arrivals'],frame['history_support_windows'] = prior.T
    frame['history_median_arrivals_5m'],frame['history_p95_arrivals_5m'] = prior5[:,:2].T
    frame['lookback_minutes'] = widths
    if minutes != 1:
        ends = unix_seconds(frame.window_end)
        p50,p95 = np.full(len(frame),np.nan),np.full(len(frame),np.nan)
        raw_groups = {c:g.sort_values('end_seconds') for c,g in completions.groupby('case_id',sort=False)}
        for c,ids in frame.groupby('case_id',sort=False).groups.items():
            if c not in raw_groups:
                continue
            rows = raw_groups[c]
            times,durations = rows.end_seconds.to_numpy(),rows.duration.to_numpy()
            ids = np.asarray(ids)
            lo = np.searchsorted(times,ends[ids]-widths[ids]*60,side='left')
            hi = np.searchsorted(times,ends[ids],side='left')
            for index,first,last in zip(ids,lo,hi):
                if last>first:
                    p50[index],p95[index] = np.quantile(durations[first:last],[.5,.95])
        frame['duration_p50_ms'],frame['duration_p95_ms'] = p50,p95
    # Preserve native decision boundaries/labels, not enlarged anomaly labels.
    return window_features(frame,baselines)


def observed_completions(folder, labels, cases, chunksize=100_000):
    pairs = labels.reset_index(drop=True)[['transactionId','case_id']].drop_duplicates()
    if pairs.transactionId.duplicated().any():
        raise ValueError('Ambiguous case mapping')
    mapping = pairs.set_index('transactionId').case_id.to_dict()
    target = cases.set_index('case_id')
    parts = []
    for raw in pd.read_csv(folder/'processes.csv',usecols=['transactionId','adapterType','processId','startTime','endTime'],dtype='string',encoding='utf-8-sig',chunksize=chunksize):
        raw['case_id'] = raw.transactionId.map(mapping)
        if raw.case_id.isna().any():
            raise ValueError('Unmapped observed execution')
        mask = raw.case_id.isin(target.index) & raw.adapterType.eq(raw.case_id.map(target.target_adapter)) & raw.processId.eq(raw.case_id.map(target.target_process_id))
        part = raw.loc[mask].copy()
        starts,ends = pd.to_datetime(part.startTime,format='ISO8601'),pd.to_datetime(part.endTime,format='ISO8601')
        parts.append(pd.DataFrame({'case_id':part.case_id,'end_seconds':unix_seconds(ends),
                                   'duration':(ends-starts).dt.total_seconds()*1000}))
    return pd.concat(parts,ignore_index=True)
