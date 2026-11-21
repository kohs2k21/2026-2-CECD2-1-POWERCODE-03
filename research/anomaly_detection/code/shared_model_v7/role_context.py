"""Causal duration-trajectory descriptors, not future event classifications."""
import numpy as np
import pandas as pd

TREND=('trajectory_lag0','trajectory_lag1','trajectory_lag2','trajectory_lag3',
       'trajectory_slope','trajectory_linear_slope','trajectory_positive_fraction','trajectory_acceleration')
COHORT=('residual_mean_5m','positive_persistence_15m','duration_upper_mean_5m',
        'current_population_delay','seq_delay_persistence')


class Context:
    def transform(self,f):
        seen=set();starts=[0]
        for i,key in enumerate(zip(f.case_id,f.window_start)):
            if key in seen:starts.append(i);seen=set()
            seen.add(key)
        starts.append(len(f))
        if len(starts)>2:return pd.concat([self.transform(f.iloc[a:b])for a,b in zip(starts[:-1],starts[1:])])
        times=pd.to_datetime(f.window_start,format='ISO8601')
        if not times.groupby(f.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Contiguous minute history required')
        residual=f.residual_mean_1m.to_numpy(float);observed=f.latency_assessed.to_numpy(bool)&np.isfinite(residual)
        result=np.zeros((len(f),len(TREND)))
        for ids in f.groupby('case_id',sort=False).indices.values():
            prefix=[];past=[];last=np.zeros(len(TREND));last_age=100
            for age,i in enumerate(ids):
                if observed[i]:
                    value=residual[i]
                    if len(prefix)>=3:
                        center=float(np.median(prefix));scale=max(1.4826*float(np.median(np.abs(np.array(prefix)-center))),.25)
                        recent=(past+[(age,value)])[-8:];t=np.array([p[0]for p in recent],float);v=np.array([p[1]for p in recent],float)
                        lag=np.maximum((v[::-1][:4]-center)/scale,0.);out=np.zeros(len(TREND));out[:len(lag)]=lag
                        if len(recent)>=4:
                            x=t-t.mean();z=v-v.mean();slope=float(x@z/max(x@x,1.));fit=slope*x
                            r2=max(1-float(np.sum((z-fit)**2))/max(float(z@z),1e-8),0.)
                            active=max((value-center)/scale,0.);active=active/(1+active)
                            out[4]=max(slope*5/scale,0.)*active;out[5]=out[4]*r2
                            out[6]=float(np.mean(np.diff(v)>0))*active
                            if len(recent)>=6:
                                def sl(a,b):
                                    xx=t[a:b]-t[a:b].mean();yy=v[a:b]-v[a:b].mean();return float(xx@yy/max(xx@xx,1.))
                                out[7]=max((sl(-3,None)-sl(0,3))*5/scale,0.)*active
                        last=out;last_age=age
                    if len(prefix)<6:prefix.append(value)
                    past.append((age,value));past=past[-8:]
                elapsed=age-last_age
                if elapsed<=8:result[i]=last*np.exp(-elapsed/5)
        return pd.DataFrame(np.minimum(result,100.),index=f.index,columns=TREND)
