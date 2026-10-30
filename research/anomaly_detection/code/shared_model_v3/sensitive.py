"""Resolve small shifts in nearly constant streams without future observations."""
import numpy as np
import pandas as pd

SMALL=('small_duration_shift','small_residual_shift','small_agreement','small_count_adjusted',
       'small_last_duration','small_last_residual','small_ewma','small_cusum','small_slope','small_stability')

def augment(f):
    f=f.copy().reset_index(drop=True);seen=set();starts=[0]
    for i,(c,t)in enumerate(zip(f.case_id,f.window_start)):
        if(c,t)in seen:starts.append(i);seen=set()
        seen.add((c,t))
    starts.append(len(f))
    if len(starts)>2:return pd.concat([augment(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])],ignore_index=True)
    d=np.log1p(f.duration_p50_ms.to_numpy(float));r=f.residual_mean_1m.to_numpy(float)
    w=f.log_mean_count_5m.to_numpy(float);counts=f.native_execution_arrivals.to_numpy(float)
    obs=f.latency_assessed.to_numpy(bool)&np.isfinite(d)&np.isfinite(r)
    output=np.zeros((len(f),len(SMALL)))
    for ids in f.groupby('case_id',sort=False).indices.values():
        dp=[];rp=[];wp=[];cp=[];last=None;ew=0.;cus=0.;recent=[]
        for age,i in enumerate(ids):
            dz=rz=agreement=adjusted=slope=stability=0.
            if obs[i]:
                if len(dp)>=3:
                    da=np.array(dp);ra=np.array(rp);dc=float(np.median(da));rc=float(np.median(ra))
                    ds=max(1.4826*float(np.median(np.abs(da-dc))),.03)
                    rs=max(1.4826*float(np.median(np.abs(ra-rc))),.05)
                    dz=max((d[i]-dc)/ds,0.);rz=max((r[i]-rc)/rs,0.)
                    agreement=np.sqrt(dz*rz)
                    adjusted=max((d[i]-dc-max(w[i]-float(np.median(wp)),0.))/ds,0.)
                    z=min(dz,50);ew=.4*z+.6*ew;cus=max(.9*cus+z-.5,0.)
                    if dz<.5:ew=0.;cus=0.
                    recent=[(t,v)for t,v in recent if age-t<=10]
                    points=recent+[(age,d[i])]
                    if len(points)>=3:
                        x=np.array([t for t,v in points],float);v=np.array([v for t,v in points]);x-=x.mean()
                        slope=max(float(np.dot(x,v-v.mean())/max(np.dot(x,x),1))*5/ds,0.)
                    last=(age,dz,rz,ew,cus,slope);stability=1/(1+ds)
                if len(dp)<5:dp.append(d[i]);rp.append(r[i]);wp.append(w[i])
                recent.append((age,d[i]))
            rate=float(np.mean(cp))if len(cp)>=3 else 1.
            elapsed=age-last[0]if last is not None else 1000
            decay=np.exp(-elapsed*max(rate,.1)/3)if elapsed<=10 else 0.
            memory=np.array(last[1:])*decay if last is not None else np.zeros(5)
            output[i]=[dz,rz,agreement,adjusted,*memory,stability]
            if len(cp)<8:cp.append(counts[i])
    for j,c in enumerate(SMALL):f[c]=np.minimum(output[:,j],300.)
    return f

class SensitiveReference:
    def __init__(self,parent):self.parent=parent
    def transform(self,f):return augment(self.parent.transform(f))
