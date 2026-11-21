"""Observed completion-minute support BEFORE the current decision.

This is neither case age nor a known-normal prefix. Zero-completion minutes
do not add support. Repeated paired replay IDs reset independent state.
"""
import numpy as np
import pandas as pd


def past_observations(f):
    seen=set();starts=[0]
    for i,key in enumerate(zip(f.case_id,f.window_start)):
        if key in seen:starts.append(i);seen=set()
        seen.add(key)
    starts.append(len(f));out=np.zeros(len(f),int)
    for a,b in zip(starts[:-1],starts[1:]):
        part=f.iloc[a:b];times=pd.to_datetime(part.window_start,format='ISO8601')
        if not times.groupby(part.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Contiguous minutes required')
        obs=part.latency_assessed.to_numpy(int)
        for ids in part.groupby('case_id',sort=False).indices.values():out[a+ids]=np.cumsum(obs[ids])-obs[ids]
    return out
