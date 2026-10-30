"""Causal rank runs and historical discrete traffic likelihoods.

Normal-only history PMFs, no fault labels or clean-prefix assumption. Missing
native history is explicitly unavailable, including independent ADBench rows.
"""
from functools import lru_cache
import numpy as np
import pandas as pd
from scipy.special import gammaln

RANK=('rank_upper_run','rank_lower_run','rank_upper_memory','rank_current_quantile',
    'rank_duration_lift','rank_duration_cusum','traffic_tail_1m','traffic_tail_3m',
    'traffic_tail_5m','traffic_zero_logprob','traffic_excess','stream_context_available')

def available(f):
    if not {'native_execution_arrivals','window_start','log_max_duration_1m'}<=set(f):return np.zeros(len(f),bool)
    return f.native_execution_arrivals.notna().to_numpy()&f.window_start.notna().to_numpy()

def no_context(f):
    f=f.copy();f['native_execution_arrivals']=np.nan
    f['case_id']=f.case_id.astype(str)+'|stream_unavailable'
    return f

class Context:
    def __init__(self,history,prefix=6):
        self.prefix=prefix;self.pmfs={};self.expectation={};self.zero={}
        for key,values in history.items():
            arr=np.asarray(values,int);pmf=np.bincount(arr).astype(float)/len(arr)
            self.pmfs[key]=pmf;self.expectation[key]=float(arr.mean());self.zero[key]=max(float(pmf[0]),1e-4)
    def transform(self,f):
        mask=available(f);out=np.zeros((len(f),len(RANK)));out[:,-1]=mask
        if not mask.any():return pd.DataFrame(out,index=f.index,columns=RANK)
        seen=set();starts=[0]
        for i,key in enumerate(zip(f.case_id,f.window_start)):
            if key in seen:starts.append(i);seen=set()
            seen.add(key)
        starts.append(len(f))
        if len(starts)>2:return pd.concat([self.transform(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])],axis=0)
        dates=pd.to_datetime(f.window_start,format='ISO8601')
        diffs=dates.groupby(f.case_id,sort=False).diff().dropna()
        if not diffs.eq(pd.Timedelta(minutes=1)).all():raise ValueError('Contiguous minute history required')
        values=f.log_max_duration_1m.to_numpy(float)
        obs=f.latency_assessed.to_numpy(bool)&np.isfinite(values)&mask
        counts=f.native_execution_arrivals.fillna(0).to_numpy(float)
        keys=list(zip(dates.dt.dayofweek.ge(5),dates.dt.hour))
        @lru_cache(maxsize=4096)
        def distribution(tuple_keys):
            p=np.array([1.])
            for key in tuple_keys:p=np.convolve(p,self.pmfs[key])
            return np.cumsum(p[::-1])[::-1]
        for ids in f.groupby('case_id',sort=False).indices.values():
            prefix=[];up=low=0;last=0.;age=100;cus=0.;zero=0.
            for position,i in enumerate(ids):
                if not mask[i]:continue
                if obs[i]:
                    x=values[i]
                    if len(prefix)>=3:
                        n=len(prefix);reference=np.asarray(prefix);maximum=max(prefix);minimum=min(prefix)
                        up=up+1 if x>maximum+1e-6 else 0;low=low+1 if x<minimum-1e-6 else 0
                        upper=float(gammaln(n+up+1)-gammaln(n+1)-gammaln(up+1))
                        lower=float(gammaln(n+low+1)-gammaln(n+1)-gammaln(low+1))
                        q=float(np.mean(reference<x)+.5*np.mean(reference==x))
                        cus=max(.9*cus+q-.6,0.)
                        if q<=.5:cus=0.
                        out[i,:6]=[upper,lower,upper,q,max(x-float(np.median(reference)),0.),cus]
                        last=upper;age=0
                    if len(prefix)<self.prefix:prefix.append(x)
                else:
                    age+=1;out[i,2]=last*np.exp(-age/5)if age<=8 else 0.
                key=keys[i];rate=self.expectation[key];excess=max(counts[i]-rate,0.)
                active=excess/(1+excess)
                for width,j in((1,6),(3,7),(5,8)):
                    recent=ids[max(0,position-width+1):position+1]
                    count=int(round(counts[recent].sum()));tail=distribution(tuple(keys[k]for k in recent))
                    p=float(tail[count])if count<len(tail)else 0.
                    out[i,j]=-np.log(max(p,1e-6))*(1. if width==1 else active)
                zero=zero-np.log(self.zero[key])if counts[i]==0 else 0.
                out[i,9]=min(zero,20.);out[i,10]=excess
        return pd.DataFrame(out,index=f.index,columns=RANK)
