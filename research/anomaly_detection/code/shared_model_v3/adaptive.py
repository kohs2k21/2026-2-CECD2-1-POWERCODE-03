"""Cadence-aware latency memory and observed start/completion conservation.

No predicted end times or future durations. Flow fields require start counters
to be available at start; this is not implementable from end-only events.
"""
import numpy as np
import pandas as pd

ADAPTIVE=('state_last_shift','state_last_residual','state_last_ewma','state_last_cusum',
    'state_last_slope','state_delay_exposure','flow_unfinished','flow_pressure_3m')

def augment(f):
    f=f.copy().reset_index(drop=True);out=np.zeros((len(f),len(ADAPTIVE)))
    seen=set();starts=[0]
    for i,(c,t)in enumerate(zip(f.case_id,f.window_start)):
        if (c,t)in seen:starts.append(i);seen=set()
        seen.add((c,t))
    starts.append(len(f))
    if len(starts)>2:return pd.concat([augment(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])],ignore_index=True)
    counts=f.native_execution_arrivals.to_numpy(float)
    obs=f.latency_assessed.to_numpy(bool)
    evidence=f[['seq_duration_shift','seq_residual_shift','seq_delay_ewma','seq_delay_cusum','seq_delay_slope']].to_numpy(float)
    started=f.process_rows_started.to_numpy(float);done=f.completed_rows.to_numpy(float)
    for ids in f.groupby('case_id',sort=False).indices.values():
        prefix=[];last=None;balance=0.;recent=[]
        for age,i in enumerate(ids):
            rate=float(np.mean(prefix))if len(prefix)>=3 else 1.
            if obs[i]:last=(age,evidence[i].copy())
            elapsed=age-last[0]if last is not None else 1000
            decay=np.exp(-elapsed*max(rate,.1)/3.)if elapsed<=15 else 0.
            memory=last[1]*decay if last is not None else np.zeros(5)
            balance=max(balance+started[i]-done[i],0.)
            recent.append(started[i]-done[i]);recent=recent[-3:]
            out[i]=[*memory,min(elapsed*rate,10.)*memory[0],
                np.log1p(balance/(rate+1)),np.log1p(max(sum(recent),0)/(rate+1))]
            if len(prefix)<8:prefix.append(counts[i])
    for j,c in enumerate(ADAPTIVE):f[c]=out[:,j]
    return f

class AdaptiveReference:
    def __init__(self,parent):self.parent=parent
    def transform(self,f):return augment(self.parent.transform(f))
