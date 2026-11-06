"""Causal score accumulation around ONE frozen estimator, not point adjustment."""
from dataclasses import dataclass,asdict
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class Setting:
    decay:float=.8
    allowance:float=.5
    weight:float=.5
    hold:float=0.
    lifetime:int=8

class Detector:
    def __init__(self,base,setting,center,scale,threshold=None):
        self.base,self.temporal,self.center,self.scale=base,setting,center,scale
        self.threshold=threshold
        for k in ('algorithm','columns','estimator','fit_rows','fit_seconds','encoder','setting'):
            setattr(self,k,getattr(base,k))
    def filter(self,f,scores):
        if 'window_start'not in f:raise ValueError('Temporal score requires actual chronological minute inputs')
        z=np.arcsinh((scores-self.center)/self.scale);out=z.copy();s=self.temporal
        seen=set();starts=[0]
        for i,(c,t)in enumerate(zip(f.case_id,f.window_start)):
            if(c,t)in seen:starts.append(i);seen=set()
            seen.add((c,t))
        starts.append(len(f))
        if len(starts)>2:return np.concatenate([self.filter(f.iloc[a:b],scores[a:b])for a,b in zip(starts[:-1],starts[1:])])
        dates=pd.to_datetime(f.window_start,format='ISO8601')
        if not dates.groupby(f.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Missing/nonchronological minute')
        observed=f.latency_assessed.to_numpy(bool);counts=f.native_execution_arrivals.to_numpy(float)
        for ids in f.groupby('case_id',sort=False).indices.values():
            cus=0.;last=None;prefix=[]
            for age,i in enumerate(ids):
                v=z[i];cus=max(s.decay*cus+v-s.allowance,0.)
                if v<=0:cus=0.
                out[i]=v+s.weight*np.log1p(cus)
                if observed[i]:last=(age,max(v,0.))
                if not observed[i]and last is not None:
                    elapsed=age-last[0];rate=float(np.mean(prefix))if len(prefix)>=3 else 1.
                    if elapsed<=s.lifetime:out[i]=max(out[i],s.hold*last[1]*np.exp(-elapsed*max(rate,.1)/4.))
                if len(prefix)<8:prefix.append(counts[i])
        return out
    def score(self,f):return self.filter(f,self.base.score(f))
    def predict(self,f):
        s=self.score(f);return s,s>self.threshold
    def record(self):return {**self.base.record(),'threshold':self.threshold,'temporal_score':asdict(self.temporal),
        'temporal_center':self.center,'temporal_scale':self.scale,'future_or_truth_used':False}
