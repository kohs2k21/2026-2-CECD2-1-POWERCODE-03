"""Past-only normal-count PMF filtering with intermittent elevated states.

Inspiration: Markov burst-state modeling (Kleinberg, 2002), NOT a reproduction
of its infinite-state/offline algorithm. The normal emission is empirical
weekday/hour history with a small Poisson smoothing component. Alternative
emissions add a Poisson count increment intermittently. No anomalous labels,
event boundary, generator phase, or future observation is read here.

This is an additional EXPLICIT statistical time-series feature, not evidence
that IF/K-means/LOF by themselves learned a temporal transition model. Its
posterior is model-conditional, NOT a calibrated operating fault probability.
"""
import numpy as np
import pandas as pd
from scipy.stats import poisson
from scipy.special import logsumexp
from shared_model_v7.count_filter import Context as Stream
from shared_model_v7.evidence_fusion import Encoder as Parent,_NEAR_CACHE
from shared_model_v7.remap_models import cached

_POSTERIORS={}


class Context:
    def __init__(self,history,duty=.5,stay=.85,onset=.005,stable_log=False):
        if not(np.all(np.asarray(duty)>0)and np.all(np.asarray(duty)<=1)and np.all(np.asarray(stay)>0)and np.all(np.asarray(stay)<1)and 0<onset<1):raise ValueError('Invalid state parameters')
        if np.asarray(duty).shape not in ((),(3,))or np.asarray(stay).shape not in ((),(3,)):raise ValueError('Scalar or three state parameters required')
        self.history={k:np.asarray(v,int)for k,v in history.items()}
        self.duty=duty;self.stay=stay;self.onset=onset;self.stable_log=stable_log
        self.signature=(tuple((k,tuple(np.bincount(v)))for k,v in sorted(self.history.items())),duty,stay,onset,stable_log)
    def log_emissions(self,key,counts):
        values=self.history[key];counts=np.asarray(counts,int);mean=max(float(values.mean()),.01)
        pmf=np.bincount(values).astype(float)/len(values)
        empirical=np.zeros(len(counts));inside=counts<len(pmf);empirical[inside]=pmf[counts[inside]]
        log_emp=np.full(len(counts),-np.inf);positive=empirical>0;log_emp[positive]=np.log(empirical[positive])
        normal=np.logaddexp(np.log(.99)+log_emp,np.log(.01)+poisson.logpmf(counts,mean))
        alternatives=[];support=np.flatnonzero(pmf)
        for index,increment in enumerate((1.,3.,6.)):
            terms=np.log(pmf[support])[None,:]+poisson.logpmf(counts[:,None]-support[None,:],increment)
            elevated=np.logaddexp(np.log(.99)+logsumexp(terms,axis=1),np.log(.01)+poisson.logpmf(counts,mean+increment))
            duty=float(np.broadcast_to(self.duty,(3,))[index])
            alternative=np.logaddexp(np.log1p(-duty)+normal,np.log(duty)+elevated)if duty<1 else elevated
            alternatives.append(alternative)
        return normal,np.column_stack(alternatives)
    def emissions(self,key,max_count):
        values=self.history[key];x=np.arange(max_count+1);emp=np.bincount(values,minlength=len(x))[:len(x)]/len(values)
        normal=.99*emp+.01*poisson.pmf(x,max(float(values.mean()),.01))
        alternatives=[]
        for index,increment in enumerate((1.,3.,6.)):
            elevated=np.convolve(normal,poisson.pmf(x,increment))[:len(x)]
            duty=float(np.broadcast_to(self.duty,(3,))[index])
            alternatives.append((1-duty)*normal+duty*elevated)
        return np.maximum(normal,1e-15),np.maximum(np.column_stack(alternatives),1e-15)
    def transform(self,f):
        native=f.native_execution_arrivals.to_numpy(float)
        valid=np.isfinite(native)
        if np.any(native[valid]<0)or np.any(native[valid]!=np.floor(native[valid])):raise ValueError('Native counts must be nonnegative integers')
        counts=np.nan_to_num(native,nan=0.).astype(int);date=pd.to_datetime(f.window_start,format='ISO8601')
        keys=list(zip(date.dt.dayofweek.ge(5),date.dt.hour));maximum=max(int(counts.max(initial=0)),64)
        if getattr(self,'stable_log',False):
            emissions={k:self.log_emissions(k,np.unique(counts))for k in set(keys)}
            lookup={int(v):i for i,v in enumerate(np.unique(counts))};out=np.zeros(len(f))
            for ids in Stream.groups(f):
                state=np.zeros(3)
                for i in ids:
                    if not valid[i]:state[:]=0.;continue
                    prior=state*self.stay+max(1-state.sum(),0.)*self.onset/3
                    log_prior=np.log(np.maximum(prior,1e-300));p0,p1=emissions[keys[i]];j=lookup[int(counts[i])]
                    weighted=log_prior+p1[j]
                    denominator=logsumexp(np.r_[np.log(max(1-prior.sum(),1e-300))+p0[j],weighted])
                    state=np.exp(weighted-denominator)
                    out[i]=-np.log(max(1-float(state.sum()),1e-12))
            return out
        emissions={k:self.emissions(k,maximum)for k in set(keys)}
        out=np.zeros(len(f))
        for ids in Stream.groups(f):
            state=np.zeros(3)
            for i in ids:
                if not valid[i]:state[:]=0.;continue
                prior=state*self.stay+(1-state.sum())*self.onset/3
                prior_normal=max(1-prior.sum(),1e-15)
                p0,p1=emissions[keys[i]];weighted=prior*p1[counts[i]]
                denominator=prior_normal*p0[counts[i]]+weighted.sum()
                state=weighted/max(denominator,1e-300)
                out[i]=-np.log(max(1-float(state.sum()),1e-12))
        return out


class Encoder(Parent):
    def __init__(self,core,reference,history,duty=.5,stay=.85,onset=.005,stable_log=False):
        super().__init__(core,reference,history,{'explicit_intermittent_PMF_state_feature':True,
                         'duty':duty,'stay':stay,'onset':onset,'normal_poisson_smoothing':.01,'stable_log_emissions':stable_log})
        self.pmf=Context(history,duty,stay,onset,stable_log)
    def auxiliary(self,f):
        near=cached(_NEAR_CACHE,(id(self.reference),id(f)),f,lambda:self.reference.transform(f)[:,0])
        point=np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*.5)
        state=cached(_POSTERIORS,(self.pmf.signature,id(f)),f,lambda:self.pmf.transform(f))
        return point,state
