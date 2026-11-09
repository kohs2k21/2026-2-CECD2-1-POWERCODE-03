"""Short, observation-reset memory. No backward fill or future completion use."""
from dataclasses import dataclass
import numpy as np
import pandas as pd

MEMORY=('last_source_delay_3m','last_source_delay_8m','last_adapter_delay_3m',
        'last_prefix_delay_3m','last_source_upper_3m','last_delay_age',
        'burst_5m_active','burst_15m_active','arrival_rate_change_positive')


def augment(original):
    f=original.copy().reset_index(drop=True)
    if f.duplicated(['case_id','window_start']).any():raise ValueError('Separate paired replay cohorts first')
    times=pd.to_datetime(f.window_start,format='ISO8601')
    if not times.groupby(f.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():
        raise ValueError('Contiguous, chronological minute grid required')
    observed=f.latency_assessed.to_numpy(bool)&np.isfinite(f.residual_mean_1m.to_numpy(float))
    inputs=f[['current_source_delay','adapter_count_p95_z','current_prefix_delay','current_source_upper']].to_numpy(float)
    out=np.zeros((len(f),6))
    for ids in f.groupby('case_id',sort=False).indices.values():
        last=None;last_age=None
        for age,i in enumerate(ids):
            if observed[i]:
                last=np.nan_to_num(np.maximum(inputs[i],0.));last_age=age
            if last is None:continue
            elapsed=age-last_age
            short=np.exp(-elapsed/3.)if elapsed<=3 else 0.
            long=np.exp(-elapsed/8.)if elapsed<=8 else 0.
            out[i]=[last[0]*short,last[0]*long,np.arcsinh(last[1])*short,
                    np.arcsinh(last[2])*short,last[3]*short,np.log1p(min(elapsed,30))]
    for j,c in enumerate(MEMORY[:6]):f[c]=out[:,j]
    # Aggregate traffic is relevant only while there is contemporaneous positive
    # traffic evidence. No fault labels or event boundaries enter this gate.
    active=f.current_arrival_excess.clip(lower=0)/(1+f.current_arrival_excess.clip(lower=0))
    f['burst_5m_active']=np.maximum(f.arrival_logratio_5m,0)*active
    f['burst_15m_active']=np.maximum(f.arrival_logratio_15m,0)*active
    f['arrival_rate_change_positive']=np.maximum(f.own_arrival_change,0)
    return f


@dataclass
class MemoryReference:
    parent:object
    def transform(self,original):return augment(self.parent.transform(original))
