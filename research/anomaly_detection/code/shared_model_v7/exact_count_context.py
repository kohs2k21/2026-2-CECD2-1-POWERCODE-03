"""Ungated multiscale count tails from historical normal count PMFs.

Existing traffic_tail_3m/5m multiply their evidence by CURRENT excess. Here a
recent spike remains in its actual trailing window even at a quiet current
minute. Convolution assumes independent increments; calibrated production
coverage/fault probabilities are NOT claimed for correlated operating logs.
"""
from functools import lru_cache
import numpy as np
import pandas as pd
from shared_model_v7.count_filter import Context as Stream

WIDTHS=(2,3,5,8,12)
COLUMNS=tuple('exact_count_tail_'+str(w)+'m'for w in WIDTHS)


class Context:
    def __init__(self,history):
        self.pmfs={}
        for k,v in history.items():
            values=np.asarray(v,int);p=np.bincount(values).astype(float);self.pmfs[k]=p/p.sum()
    @lru_cache(maxsize=4096)
    def distribution(self,keys):
        p=np.array([1.])
        for k in keys:p=np.convolve(p,self.pmfs[k])
        return np.cumsum(p[::-1])[::-1]
    def transform(self,f):
        date=pd.to_datetime(f.window_start,format='ISO8601');keys=list(zip(date.dt.dayofweek.ge(5),date.dt.hour))
        native=f.native_execution_arrivals.to_numpy(float);valid=np.isfinite(native);native=np.nan_to_num(native,nan=0.)
        if np.any(native<0):raise ValueError('Negative counts are invalid')
        out=np.zeros((len(f),len(WIDTHS)))
        for ids in Stream.groups(f):
            cumulative=np.r_[0,np.cumsum(native[ids])];observed=np.r_[0,np.cumsum(valid[ids])]
            for j,i in enumerate(ids):
                for column,width in enumerate(WIDTHS):
                    a=j-width+1
                    if a<0 or observed[j+1]-observed[a]!=width:continue
                    count=int(round(cumulative[j+1]-cumulative[a]));tail=self.distribution(tuple(keys[k]for k in ids[a:j+1]))
                    p=tail[count]if count<len(tail)else 0.;out[i,column]=-np.log(max(float(p),1e-6))
        return pd.DataFrame(out,index=f.index,columns=COLUMNS)
