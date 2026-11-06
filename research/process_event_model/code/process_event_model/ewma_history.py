"""Event-time exponential rate context, no regular-grid training observations.

Estimate a slow reference from all actually received past executions, not only
an early six-minute sample. The current delivery enters the fast rate but not
its slow comparison baseline. No synthetic normal traffic prior is used.
"""
import numpy as np

from .native_history import NativeRowContext

EWMA_FEATURES=('ewma_rate_lift_1m','ewma_rate_lift_3m','ewma_rate_support')


class EWMAHistory:
    def __init__(self):self.states={}

    def transform(self,frame):
        f=frame.copy()
        clock=f.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
        out=np.zeros((len(f),3));tau=np.array([60.,180.]);slow_tau=1200.
        cols=(['cohort'] if 'cohort' in f else [])+['case_id','adapterType','processId']
        for key,indices in f.groupby(cols,sort=False,observed=True).indices.items():
            ids=np.asarray(indices);ids=ids[np.argsort(clock[ids],kind='stable')]
            state=self.states.setdefault(key,dict(first=clock[ids[0]],last=-np.inf,fast=np.zeros(2),slow=0.,seen=0))
            starts=np.r_[0,np.flatnonzero(np.diff(clock[ids])!=0)+1];stops=np.r_[starts[1:],len(ids)]
            for lo,hi in zip(starts,stops):
                batch=ids[lo:hi];now=clock[batch[0]]
                if now<=state['last']:raise ValueError('Out-of-order or split availability batch in rate history')
                elapsed=now-state['last'] if np.isfinite(state['last']) else 0.
                age=now-state['first'];first=not np.isfinite(state['last'])
                state['fast']=np.exp(-elapsed/tau)*state['fast']+len(batch)
                state['slow']*=np.exp(-elapsed/slow_tau)
                lift=np.zeros(2)
                if age>=120 and state['seen']>=3:
                    exposure=slow_tau*(-np.expm1(-age/slow_tau))
                    reference=max((state['slow']+.5)/(exposure+60.),.05/60.)
                    reference*=1+1/np.sqrt(max(state['slow'],1.))
                    # Palm/event-conditioned correction for the one execution
                    # being scored; other simultaneous actual rows stay visible.
                    expected=reference+1/tau
                    lift=np.maximum(np.log((state['fast']/tau)/expected),0.)
                out[batch]=(*lift,np.log1p(state['seen']))
                if not first:state['slow']+=len(batch)
                state['seen']+=len(batch);state['last']=now
        for j,c in enumerate(EWMA_FEATURES):f[c]=np.clip(out[:,j],0,100)
        return f


class EWMANativeRowContext(NativeRowContext):
    def __init__(self,reference):
        super().__init__(reference);self.ewma=EWMAHistory()

    def transform(self,records):return self.ewma.transform(super().transform(records))
