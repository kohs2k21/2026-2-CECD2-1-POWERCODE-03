"""Causal traffic likelihood accumulation, not a hard rule or alarm guarantee.

Uses normal historical count PMFs and a past-only, optionally contaminated
observed prefix. Exponential-tilt likelihood increments accumulate without
resetting merely because one current count returns below the baseline.
"""
import numpy as np
import pandas as pd
from scipy.special import logsumexp

COLUMNS=('traffic_lr_fast','traffic_lr_slow','traffic_lr_prefix','traffic_lr_agreement',
         'traffic_recent_excess_fraction','traffic_ewma_lift')

class Context:
    def __init__(self,history):
        self.history={};self.thetas=np.array([.2,.5,1.])
        for key,values in history.items():
            x=np.asarray(values,int);p=np.bincount(x).astype(float)+.25;p/=p.sum()
            support=np.arange(len(p));mgf=logsumexp(np.log(p)[None,:]+self.thetas[:,None]*support[None,:],axis=1)
            self.history[key]=(mgf,float(x.mean()),float(np.quantile(x,.95)))
    def transform(self,f):
        seen=set();starts=[0]
        for i,key in enumerate(zip(f.case_id,f.window_start)):
            if key in seen:starts.append(i);seen=set()
            seen.add(key)
        starts.append(len(f))
        if len(starts)>2:return pd.concat([self.transform(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])])
        dates=pd.to_datetime(f.window_start,format='ISO8601')
        if not dates.groupby(f.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Contiguous chronological minutes required')
        native=f.native_execution_arrivals.to_numpy(float);valid=np.isfinite(native);native=np.nan_to_num(native,nan=0.)
        keys=list(zip(dates.dt.dayofweek.ge(5),dates.dt.hour));out=np.zeros((len(f),len(COLUMNS)))
        for ids in f.groupby('case_id',sort=False).indices.values():
            past=[];fast=np.zeros(3);slow=np.zeros(3);own=np.zeros(3);ewma=0.;exceed=[]
            for i in ids:
                if not valid[i]:continue
                count=native[i];mgf,rate,p95=self.history[keys[i]];increment=self.thetas*count-mgf
                fast=np.maximum(0.,.8*fast+increment);slow=np.maximum(0.,.95*slow+increment)
                # Prefix moments use ONLY counts before this observation.
                if len(past)>=3:
                    prefix=np.asarray(past);local=logsumexp(self.thetas[:,None]*prefix[None,:],axis=1)-np.log(len(prefix))
                    local=.5*local+.5*mgf;own=np.maximum(0.,.9*own+self.thetas*count-local)
                exceed.append(float(count>p95));exceed=exceed[-8:]
                ewma=.2*max(count-rate,0.)+.8*ewma
                fv,sv,pv=float(fast.max()),float(slow.max()),float(own.max())
                out[i]=[fv,sv,pv,np.sqrt(max(sv*pv,0.)),np.mean(exceed),np.log1p(ewma/(rate+.5))]
                if len(past)<8:past.append(count)
        return pd.DataFrame(np.minimum(out,100.),index=f.index,columns=COLUMNS)
