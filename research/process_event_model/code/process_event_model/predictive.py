"""Causal event-row traffic evidence with explicit small-sample uncertainty.

No normal traffic rate is invented. The baseline and test lookback are disjoint
parts of the ACTUALLY observed timestamp history. Counts exclude the current
delivery (event-conditioned/Palm sampling); this is not a random clock sample.
Gamma-Poisson predictive tails use an overdispersion effective-sample guard, so
the result is a feature, NOT a guaranteed p-value or false-alarm bound.
SciPy parameterization: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.nbinom.html
"""
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import nbinom

from .row_context import RowContext


NEW_FEATURES=('predictive_burst_1m','predictive_burst_3m',
              'predictive_exposure_1m','predictive_exposure_3m',
              'predictive_support_1m','predictive_support_3m',
              'predictive_dispersion_1m','predictive_dispersion_3m')


class PredictiveHistory:
    def __init__(self):
        self.states={}

    def transform(self,frame):
        f=frame.copy()
        t=f.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
        group_columns=(['cohort'] if 'cohort' in f else [])+['case_id','adapterType','processId']
        out={name:np.zeros(len(f)) for name in NEW_FEATURES}
        for key,ids in f.groupby(group_columns,observed=True,sort=False).indices.items():
            ids=np.asarray(ids);ids=ids[np.argsort(t[ids],kind='stable')]
            current=t[ids]
            state=self.states.get(key)
            if state is None:
                state=dict(first=float(current[0]),last=None,prefix=np.array([],float),recent=np.array([],float))
                self.states[key]=state
            if state['last'] is not None and current[0]<=state['last']:
                raise ValueError('Out-of-order/split availability batch in predictive history')
            all_times=np.sort(np.r_[state['prefix'],state['recent'],current],kind='stable')
            first=state['first']
            founders=np.searchsorted(all_times,first,side='right')
            edges=first+np.arange(7)*60.
            minute_counts=np.diff(np.searchsorted(all_times,edges,side='left')).astype(float)
            minute_counts[0]=max(0.,minute_counts[0]-founders)
            fanos=np.ones(7)
            for complete_minutes in range(3,7):
                v=minute_counts[:complete_minutes]
                fanos[complete_minutes]=max(float(np.var(v,ddof=1)/max(float(v.mean()),.1)),1.)
            hi=np.searchsorted(all_times,current,side='left')
            for width in (1,3):
                seconds=width*60.
                exposure=np.minimum(np.maximum(current-first-seconds,0.),360.)
                cutoff=first+exposure
                baseline=np.maximum(np.searchsorted(all_times,cutoff,side='left')-founders,0)
                past=hi-np.searchsorted(all_times,current-seconds,side='left')
                complete=np.minimum(np.floor(exposure/60.).astype(int),6)
                dispersion=fanos[complete]
                valid=exposure>=60.
                score=np.zeros(len(ids))
                if valid.any():
                    shape=(baseline[valid]+.5)/dispersion[valid]
                    rate=(exposure[valid]/60.)/dispersion[valid]
                    probability=rate/(rate+width)
                    score[valid]=np.minimum(-nbinom.logsf(past[valid]-1,shape,probability),20.)
                out[f'predictive_burst_{width}m'][ids]=np.nan_to_num(score,nan=0.,posinf=20.)
                out[f'predictive_exposure_{width}m'][ids]=exposure/60.
                out[f'predictive_support_{width}m'][ids]=baseline
                out[f'predictive_dispersion_{width}m'][ids]=dispersion
            state['prefix']=all_times[all_times<=first+360.]
            state['recent']=all_times[(all_times>first+360.) & (all_times>=current[-1]-180.)]
            state['last']=float(current[-1])
        for name,values in out.items():f[name]=values
        return f


class PredictiveRowContext(RowContext):
    def __init__(self,reference):
        super().__init__(reference)
        self.predictive=PredictiveHistory()

    def transform(self,records):
        return self.predictive.transform(super().transform(records))
