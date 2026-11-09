"""Causal personal-rate count evidence with a persistent latent-score filter.

The predictive NB tail is only a feature: overlapping windows and the assumed
transition parameters mean the output is NOT a calibrated fault probability.
The first prefix is not certified healthy. No labels/endpoints/future counts
are read. A one-arrival isolated execution cannot seed a volume-burst state.
"""
import numpy as np
import pandas as pd
from scipy.stats import nbinom


class Context:
    def __init__(self,history):
        self.history={k:(float(np.mean(v)),max(float(np.var(v))/max(float(np.mean(v)),.1),1.))for k,v in history.items()}
    @staticmethod
    def groups(f):
        seen=set();starts=[0]
        for i,key in enumerate(zip(f.case_id,f.window_start)):
            if key in seen:starts.append(i);seen=set()
            seen.add(key)
        starts.append(len(f));out=[]
        for a,b in zip(starts[:-1],starts[1:]):
            part=f.iloc[a:b];date=pd.to_datetime(part.window_start,format='ISO8601')
            if not date.groupby(part.case_id,sort=False).diff().dropna().eq(pd.Timedelta(minutes=1)).all():raise ValueError('Contiguous minute grid required')
            out.extend(a+ids for ids in part.groupby('case_id',sort=False).indices.values())
        return out
    def emissions(self,f):
        date=pd.to_datetime(f.window_start,format='ISO8601');keys=list(zip(date.dt.dayofweek.ge(5),date.dt.hour))
        raw=f.native_execution_arrivals.to_numpy(float);valid=np.isfinite(raw);counts=np.nan_to_num(raw,nan=0.)
        if np.any(counts<0):raise ValueError('Negative native counts are invalid')
        logtail=np.zeros(len(f));gate=np.zeros(len(f));eligible=np.zeros(len(f),bool);groups=self.groups(f)
        for ids in groups:
            cumulative=np.r_[0,np.cumsum(counts[ids])];exposure=np.r_[0,np.cumsum(valid[ids])]
            for j,i in enumerate(ids):
                start=max(0,j-2);width=j-start+1
                if width<3 or not valid[ids[start:j+1]].all()or exposure[start]<3:continue
                rate,dispersion=self.history[keys[i]];old=cumulative[start];recent=cumulative[j+1]-old
                shape=(3*rate+.25+old)/dispersion;beta=(3+exposure[start])/dispersion
                p=float(nbinom.sf(recent-1,shape,beta/(beta+width)))
                logtail[i]=-np.log(max(p,1e-6));gate[i]=min(max(recent-1,0.),1.);eligible[i]=True
        return {'log_tail':logtail,'multiple_arrivals':gate,'eligible':eligible,'groups':groups}


def scores(emissions,onset=.01,recovery=.1,tilt=.25):
    if not(0<onset<1 and 0<recovery<1 and 0<tilt<1):raise ValueError('Invalid filter parameters')
    out=np.zeros(len(emissions['log_tail']));loglik=np.log(tilt)+(1-tilt)*emissions['log_tail']
    loglik=emissions['multiple_arrivals']*loglik+(1-emissions['multiple_arrivals'])*np.log(tilt)
    for ids in emissions['groups']:
        probability=0.
        for i in ids:
            if not emissions['eligible'][i]:probability=0.;continue
            prior=probability*(1-recovery)+(1-probability)*onset
            odds=prior/max(1-prior,1e-12)*np.exp(loglik[i]);probability=odds/(1+odds)
            out[i]=-np.log(max(1-probability,1e-9))
    return out
