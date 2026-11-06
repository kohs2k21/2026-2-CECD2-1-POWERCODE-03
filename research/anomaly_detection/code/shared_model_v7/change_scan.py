"""Past-only multiscale count change; no guaranteed p-value or clean prefix.

At each completed minute, compare counts in recent 1/2/3/5/8 minutes with
older observed counts plus a small historical prior. The old prefix is NOT
assumed normal and can be contaminated. No future endpoint enters the scan.
"""
import numpy as np
import pandas as pd
from scipy.special import xlogy

COLUMNS=('count_scan_1m','count_scan_3m','count_scan_8m','count_scan_peak',
         'count_scan_memory_3m','count_scan_memory_5m','count_scan_memory_8m')


class Context:
    def __init__(self,history):
        self.history={k:(float(np.mean(v)),max(float(np.var(v))/max(float(np.mean(v)),.1),1.))for k,v in history.items()}
    def transform(self,f):
        seen=set();starts=[0]
        for i,key in enumerate(zip(f.case_id,f.window_start)):
            if key in seen:starts.append(i);seen=set()
            seen.add(key)
        starts.append(len(f))
        if len(starts)>2:return pd.concat([self.transform(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])])
        dates=pd.to_datetime(f.window_start,format='ISO8601')
        if not dates.groupby(f.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Contiguous minute grid required')
        keys=list(zip(dates.dt.dayofweek.ge(5),dates.dt.hour));counts=f.native_execution_arrivals.to_numpy(float)
        valid=np.isfinite(counts);counts=np.nan_to_num(counts,nan=0.);out=np.zeros((len(f),len(COLUMNS)))
        for ids in f.groupby('case_id',sort=False).indices.values():
            local=counts[ids];cumulative=np.r_[0,np.cumsum(local)];peaks=[]
            for j,i in enumerate(ids):
                if not valid[i]:peaks.append(0.);continue
                prior,dispersion=self.history[keys[i]];values=[]
                for width in(1,2,3,5,8):
                    start=max(0,j-width+1);n1=j-start+1;c1=cumulative[j+1]-cumulative[start]
                    # Gamma-like pseudo counts regularize short/zero prefixes.
                    n0=start+3.;c0=cumulative[start]+3*prior;r0=c0/n0;r1=c1/n1;pooled=(c0+c1)/(n0+n1)
                    glr=(xlogy(c0,r0/max(pooled,1e-8))+xlogy(c1,r1/max(pooled,1e-8)))/dispersion if r1>r0 else 0.
                    values.append(np.sqrt(max(2*glr,0.)))
                peak=max(values);peaks.append(peak)
                # Explicit bounded memory is causal alert evidence, not truth
                # interval expansion. Full replay counts alarms after recovery.
                out[i]=[values[0],values[2],values[4],peak,max(peaks[-3:]),max(peaks[-5:]),max(peaks[-8:])]
        return pd.DataFrame(out,index=f.index,columns=COLUMNS)
