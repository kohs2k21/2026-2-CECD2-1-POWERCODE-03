"""Causal self-normalization and evidence accumulation, never event-boundary aware.

case_id only separates independent replay streams. The same logic needs a
persistent Adapter+Process state in an online worker. Prefixes are observations,
not certified healthy periods. A new completion overwrites latency memory.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

SEQUENCE=('seq_duration_shift','seq_residual_shift','seq_count_adjusted_shift',
    'seq_delay_cusum','seq_delay_ewma','seq_delay_persistence','seq_delay_slope',
    'seq_last_shift','seq_last_residual','seq_last_age',
    'seq_arrival_deviance','seq_arrival_lift','seq_burst_3m','seq_burst_8m',
    'seq_burst_cusum','seq_gap_surprise','seq_gap_lift',
    'seq_recovery_duration','seq_recovery_arrival')

def augment(original):
    f=original.copy().reset_index(drop=True)
    # Paired normal and fault replays reuse IDs; reset their state at repetition.
    seen=set();starts=[0]
    for i,(c,t)in enumerate(zip(f.case_id,f.window_start)):
        if (c,t)in seen:starts.append(i);seen=set()
        seen.add((c,t))
    starts.append(len(f))
    if len(starts)>2:
        return pd.concat([augment(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])],ignore_index=True)
    times=pd.to_datetime(f.window_start,format='ISO8601')
    if not times.groupby(f.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():
        raise ValueError('Complete chronological minute grid required')
    counts=f.native_execution_arrivals.to_numpy(float)
    logduration=np.log1p(f.duration_p50_ms.to_numpy(float))
    residual=f.residual_mean_1m.to_numpy(float)
    workload=f.log_mean_count_5m.to_numpy(float)
    observed=f.latency_assessed.to_numpy(bool)&np.isfinite(logduration)&np.isfinite(residual)
    out=np.zeros((len(f),len(SEQUENCE)))
    for ids in f.groupby('case_id',sort=False).indices.values():
        cp=[];dp=[];rp=[];wp=[];past=[];zero=0;cus=0.;ew=0.;bcus=0.;last=None
        prev_duration=0.;prev_burst=0.
        for age,i in enumerate(ids):
            count=counts[i];zero=zero+1 if count==0 else 0
            ready=len(cp)>=3;dshift=rshift=adjusted=slope=0.
            if observed[i]:
                if len(dp)>=3:
                    center=float(np.median(dp));noise=max(float(np.median(np.abs(np.asarray(dp)-center)))*1.4826,.15)
                    rcenter=float(np.median(rp));rnoise=max(float(np.median(np.abs(np.asarray(rp)-rcenter)))*1.4826,.25)
                    dshift=max((logduration[i]-center)/noise,0.)
                    rshift=max((residual[i]-rcenter)/rnoise,0.)
                    # Throughput is totalCount per execution, not arrivals/min.
                    adjusted=max((logduration[i]-center-max(workload[i]-float(np.median(wp)),0.))/noise,0.)
                    z=min(dshift,20.);cus=max(0.,.85*cus+z-.5)
                    ew=.5*z+.5*ew
                    recent=[(t,v)for t,v in past if age-t<=10]+[(age,logduration[i])]
                    if len(recent)>=3:
                        x=np.array([t for t,v in recent],float);v=np.array([v for t,v in recent]);x-=x.mean()
                        slope=max(float(np.dot(x,v-v.mean())/max(np.dot(x,x),1.))*5/noise,0.)
                    # Recovery must not retain a high accumulated fault level.
                    if dshift<.5:cus=0.;ew=0.
                    last=(age,dshift,rshift)
                if len(dp)<5:
                    dp.append(logduration[i]);rp.append(residual[i]);wp.append(workload[i])
                past.append((age,logduration[i]))
                past=[(t,v)for t,v in past if age-t<=10]
            elapsed=age-last[0]if last is not None else 1000
            lshift=last[1]*np.exp(-elapsed/5)if elapsed<=8 else 0.
            lr=last[2]*np.exp(-elapsed/5)if elapsed<=8 else 0.
            persistence=sum(v>float(np.median(dp))+.15 for _,v in past)/max(len(past),1)if len(dp)>=3 else 0.
            dev=lift=b3=b8=gap=gaplift=0.
            if ready:
                rate=float(np.mean(cp));var=float(np.var(cp));width=min(age+1,3)
                # NB-like overdispersion guard; Poisson deviance is not a p-value.
                dispersion=max(var/max(rate,.1),1.)
                dev=np.sqrt(max(2*(count*np.log((count+.1)/(rate+.1))-(count-rate)),0.)/dispersion)if count>rate else 0.
                lift=max(np.log((count+.5)/(rate+.5)),0.)
                c=counts[ids[max(0,age-2):age+1]].sum();b3=max(np.log((c+.5)/(rate*width+.5)),0.)
                width8=min(age+1,8);c8=counts[ids[max(0,age-7):age+1]].sum()
                b8=max(np.log((c8+.5)/(rate*width8+.5)),0.)
                bcus=max(0.,.8*bcus+min(dev,10.)-.5)
                if count<=rate:bcus=0.
                gap=zero*rate/dispersion
                gaplift=np.log1p(zero*rate)
            out[i]=[dshift,rshift,adjusted,cus,ew,persistence*min(dshift,10),slope,
                lshift,lr,np.log1p(min(elapsed,30)),dev,lift,b3,b8,bcus,gap,gaplift,
                max(prev_duration-dshift,0)if observed[i]else 0,max(prev_burst-lift,0)]
            if len(cp)<8:cp.append(count)
            if observed[i]:prev_duration=dshift
            prev_burst=lift
    out=np.nan_to_num(np.minimum(out,100.),nan=0.,posinf=100.,neginf=0.)
    for j,c in enumerate(SEQUENCE):f[c]=out[:,j]
    return f

@dataclass
class SequenceReference:
    parent:object
    def transform(self,original):return augment(self.parent.transform(original))
