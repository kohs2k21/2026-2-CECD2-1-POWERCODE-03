"""Additional past-only execution features with a less fragile local reference.

Keep original executions. The reference accumulates ACTUAL prior completions
for at most eight minutes, instead of relying only on the first five rows.
That prefix is not asserted healthy. No minute-grid Process rows are generated.
"""
from collections import deque

import numpy as np

from .predictive import PredictiveRowContext


ADAPTIVE_FEATURES=('adaptive_upper','adaptive_mean_shift','adaptive_ratio',
                   'adaptive_cusum','adaptive_recent_excess','adaptive_support')


class AdaptiveHistory:
    def __init__(self):
        self.states={}

    def transform(self, frame):
        f=frame.copy()
        times=f.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
        logs=np.log1p(f.duration_ms.to_numpy(float))
        columns=(['cohort'] if 'cohort' in f else [])+['case_id','adapterType','processId']
        out=np.zeros((len(f),len(ADAPTIVE_FEATURES)))
        for key,ids in f.groupby(columns,observed=True,sort=False).indices.items():
            ids=np.asarray(ids);ids=ids[np.argsort(times[ids],kind='stable')]
            s=self.states.setdefault(key,dict(first=times[ids[0]],last=-np.inf,n=0.,mean=0.,m2=0.,
                                             maximum=-np.inf,recent=deque(),cusum=0.))
            starts=np.r_[0,np.flatnonzero(np.diff(times[ids])!=0)+1]
            stops=np.r_[starts[1:],len(ids)]
            for lo,hi in zip(starts,stops):
                batch=ids[lo:hi];t=times[batch[0]]
                if t<=s['last']:raise ValueError('Out-of-order or split availability batch')
                while s['recent'] and s['recent'][0][0]<t-180:s['recent'].popleft()
                past=np.array([v for _,v in s['recent']],float)
                n=s['n'];ready=n>=5
                noise=max(np.sqrt(s['m2']/max(n-1,1)),.03)
                prior_sum=past.sum()
                updates=[]
                for i in batch:
                    if ready:
                        ratio=max(logs[i]-s['mean'],0.)
                        upper=max(logs[i]-s['maximum'],0.)/noise
                        avg=(prior_sum+logs[i])/(len(past)+1)
                        mean_shift=max(avg-s['mean'],0.)/(noise*np.sqrt(1/(len(past)+1)+1/n))
                        z=(logs[i]-s['mean'])/noise
                        cusum=max(.85*s['cusum']+min(z,10)-.5,0.) if z>.5 else 0.
                        recent_excess=(np.sum(past>s['maximum']+.01)+(logs[i]>s['maximum']+.01))/(len(past)+1)
                        out[i]=(upper,mean_shift,ratio,cusum,recent_excess,np.log1p(n))
                        updates.append(cusum)
                    else:
                        out[i,-1]=np.log1p(n)
                if t-s['first']<=480:
                    # Welford batch merge avoids artificial samples, tie-order
                    # dependence and cancellation of nearly constant durations.
                    values=logs[batch];bn=len(values);bm=float(values.mean())
                    bm2=float(np.sum((values-bm)**2));delta=bm-s['mean'];total=n+bn
                    s['mean']+=(delta*bn/total)
                    s['m2']+=bm2+delta*delta*n*bn/total
                    s['maximum']=max(s['maximum'],float(values.max()))
                    s['n']=total
                s['recent'].extend((t,logs[i]) for i in batch)
                s['cusum']=max(updates,default=0.)
                s['last']=t
        for j,c in enumerate(ADAPTIVE_FEATURES):f[c]=np.clip(out[:,j],0,100)
        return f


class AdaptiveRowContext(PredictiveRowContext):
    def __init__(self,reference):
        super().__init__(reference)
        self.adaptive=AdaptiveHistory()

    def transform(self,records):
        return self.adaptive.transform(super().transform(records))
