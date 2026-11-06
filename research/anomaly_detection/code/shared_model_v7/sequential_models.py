from dataclasses import dataclass
import numpy as np
import pandas as pd
from shared_model_v6.models import Setting as ParentSetting,Detector as Base
from shared_model_v4.rank_context import Context as Rank,RANK
from shared_model_v7.sequential import Context,COLUMNS

BLOCKS={
 'point':('current_source_upper','current_source_delay'),
 'delay':('seq_last_residual','seq_last_shift','rank_upper_memory'),
 'trend':('rank_duration_cusum','seq_delay_slope'),
 'burst':(*COLUMNS,'arrival_ewma'),
 'gap':('gap_expected_prefix','traffic_zero_logprob'),
}
RAW=tuple(c for cs in BLOCKS.values()for c in cs)

@dataclass(frozen=True)
class Setting(ParentSetting):
    point_gain:float=1.
    flags:bool=False

class Encoder:
    def __init__(self,history,s):self.context=Context(history);self.rank=Rank(history,6);self.setting=s
    def derive(self,f):
        out=f
        if not set(RANK)<=set(out):out=pd.concat([out,self.rank.transform(out)],axis=1)
        if not set(COLUMNS)<=set(out):out=pd.concat([out,self.context.transform(out)],axis=1)
        return out
    def fit(self,f):
        f=self.derive(f);raw=np.nan_to_num(f[list(RAW)].to_numpy(float),nan=0.)
        self.center=np.median(raw,axis=0);self.scale=np.maximum(np.quantile(raw,.95,axis=0)-self.center,.15);return self
    def transform(self,f,view):
        f=self.derive(f);raw=np.nan_to_num(f[list(RAW)].to_numpy(float),nan=0.)
        x=np.maximum((raw-self.center)/self.scale,0.);x=np.minimum(x,100.)
        if self.setting.transform=='asinh':x=np.arcsinh(x)
        parts={k:x[:,[RAW.index(c)for c in cs]]for k,cs in BLOCKS.items()};peaks=np.column_stack([v.max(axis=1)for v in parts.values()]);peaks[:,0]*=self.setting.point_gain
        if view=='peaks':return peaks
        if view=='raw':out=x
        elif view=='two_axis':out=np.column_stack([peaks[:,0],peaks[:,1:].max(axis=1)])
        elif view=='compact':out=np.column_stack([v for z in parts.values()for v in(z.max(axis=1),z.mean(axis=1))])
        else:out=np.column_stack([peaks.max(axis=1),np.sort(peaks,axis=1)[:,-2],np.sqrt(np.mean(peaks*peaks,axis=1))])
        if self.setting.flags:
            out=np.column_stack([out,.25*f.latency_assessed.to_numpy(float),.25*np.log1p(f.completion_count_1m.to_numpy(float)),.25*f.traffic_assessed.to_numpy(float)])
        return out
    def names(self,view):
        if view=='raw':cols=['normal_scaled_'+c for c in RAW]
        elif view=='two_axis':cols=['point','other_max']
        elif view=='peaks':return ['point','delay','trend','burst','gap']
        elif view=='compact':cols=[k+'_'+m for k in BLOCKS for m in('peak','mean')]
        else:cols=['joint_max','joint_second','joint_radius']
        return cols+(['latency_available','log_completed','traffic_available']if self.setting.flags else[])

class Detector(Base):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(history,s)
    def fit(self,f):super().fit(f);self.columns=tuple(c for c in RAW if c not in COLUMNS);return self
    def record(self):return {**super().record(),'causal_likelihood_accumulation':True,'no_formal_evalue_or_false_alarm_guarantee':True}
