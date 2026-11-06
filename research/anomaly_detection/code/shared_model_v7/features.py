"""Normal-only multi-scale evidence; include accumulated, not only current burst.

All source columns are causal numerical features of the existing raw Process
processor. IDs select normal duration references upstream, never model inputs.
"""
import numpy as np
import pandas as pd
from shared_model_v4.rank_context import Context,RANK

BLOCKS={
 'point':('current_source_upper','current_source_delay','adapter_count_p95_upper_tail'),
 'delay':('seq_last_residual','seq_last_shift','rank_upper_memory'),
 'trend':('rank_duration_cusum','seq_delay_slope','seq_delay_ewma'),
 'burst':('arrival_dev_5m','arrival_logratio_5m','seq_burst_3m','seq_burst_8m','traffic_tail_1m'),
 'gap':('gap_expected_prefix','traffic_zero_logprob','gap_expected_recent'),
}
RAW=tuple(c for cs in BLOCKS.values()for c in cs)

class Evidence:
    def __init__(self,history,setting):self.context=Context(history,6);self.setting=setting
    def derive(self,f):return f if set(RANK)<=set(f)else pd.concat([f,self.context.transform(f)],axis=1)
    def fit(self,f):
        f=self.derive(f);raw=np.nan_to_num(f[list(RAW)].to_numpy(float),nan=0.)
        self.median=np.median(raw,axis=0);self.scale=np.maximum(np.quantile(raw,.95,axis=0)-self.median,.15)
        self.normal_refs=[np.sort(raw[:,i])for i in range(raw.shape[1])]
        return self
    def transform(self,f,view):
        f=self.derive(f);raw=np.nan_to_num(f[list(RAW)].to_numpy(float),nan=0.)
        if self.setting.tail:
            x=np.column_stack([-np.log((len(ref)-np.searchsorted(ref,raw[:,i],side='left')+1)/(len(ref)+1))for i,ref in enumerate(self.normal_refs)])
        else:x=np.minimum(np.maximum((raw-self.median)/self.scale,0.),100.)
        if self.setting.transform=='asinh':x=np.arcsinh(x)
        elif self.setting.transform=='sqrt':x=np.sqrt(x)
        if view=='raw':return x
        parts={k:x[:,[RAW.index(c)for c in cs]]for k,cs in BLOCKS.items()}
        peaks=np.column_stack([z.max(axis=1)for z in parts.values()])
        if view=='peaks':return peaks
        if view=='radial':return peaks.max(axis=1,keepdims=True)
        if view=='radial_pair':
            ordered=np.sort(peaks,axis=1)
            return np.column_stack([ordered[:,-1],ordered[:,-2],np.sqrt(np.mean(peaks*peaks,axis=1))])
        if view=='compact':
            values=[v for z in parts.values()for v in(z.max(axis=1),z.mean(axis=1))]
            values.append(np.sqrt(peaks[:,0]*peaks[:,1]))
            return np.column_stack(values)
        raise ValueError(view)
    def names(self,view):
        if view=='raw':return ['normal_scaled_'+c for c in RAW]
        if view=='peaks':return ['evidence_'+k+'_peak'for k in BLOCKS]
        if view=='radial':return ['evidence_joint_max']
        if view=='radial_pair':return ['joint_max','joint_second','joint_radius']
        return [*['evidence_'+k+'_'+s for k in BLOCKS for s in('peak','mean')],'evidence_delay_agreement']
