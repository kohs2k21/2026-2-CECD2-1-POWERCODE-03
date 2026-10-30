"""Single shared estimator with explicit optional-context missingness.

Normal training includes intact and stateless/drop-context views. Missing raw
history is marked unavailable, never made into fictitious time sequences.
"""
from dataclasses import dataclass,asdict
import time
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from sklearn.covariance import OAS
from threadpoolctl import threadpool_limits
from shared_model_v4.manifold import COLUMNS
from shared_model_v4.matched import MATCHED,Profile

@dataclass(frozen=True)
class Setting:
    algorithm:str
    view:str='all'
    transform:str='asinh'
    context_dropout:bool=True
    matched_weight:float=1.
    adapter_weight:float=.25
    clusters:int=16
    neighbors:int=400
    cap:int=4096
    samples:int=256
    whitening:bool=False
    seed:int=42

class Detector:
    def __init__(self,setting):self.setting=setting;self.algorithm=setting.algorithm;self.threshold=None
    def derive(self,f,drop=False):
        x=f[list(COLUMNS)].copy();m=self.profile.transform(f)
        if drop:m.loc[:,:]=0.
        return pd.concat([x,m],axis=1)
    def fit(self,f):
        start=time.perf_counter();s=self.setting;self.profile=Profile().fit(f)
        if s.view=='all':self.columns=(*COLUMNS,*MATCHED)
        elif s.view=='compact':self.columns=(*COLUMNS[:5],*COLUMNS[17:26],*MATCHED)
        else:self.columns=(*COLUMNS[:13],*COLUMNS[17:26],*MATCHED)
        intact=self.derive(f);parts=[intact]
        if s.context_dropout:parts.append(self.derive(f,True))
        frame=pd.concat(parts,ignore_index=True);raw=frame[list(self.columns)].to_numpy(float)
        self.median=np.nan_to_num(np.nanmedian(raw,axis=0));raw=np.where(np.isnan(raw),self.median,raw)
        q10,q90=np.quantile(raw,[.1,.9],axis=0);self.scale=np.maximum(q90-q10,.5)
        self.weights=np.array([s.matched_weight if c in MATCHED else s.adapter_weight if c.startswith('adapter_')else 1. for c in self.columns])
        self.weights[np.array([c.endswith('assessed')or c=='cadence_ready'for c in self.columns])]=.1
        self.weights[np.array([c=='matched_available'for c in self.columns])]=2.
        self.center=np.zeros(len(self.columns));self.mapping=np.eye(len(self.columns));self.encoder=None
        x=self.encode_derived(frame)
        if s.whitening:
            fit=OAS().fit(x);v,u=np.linalg.eigh(fit.covariance_);self.center=fit.location_;self.mapping=u@np.diag(1/np.sqrt(np.maximum(v,.2)))@u.T
        x=self.values_derived(frame)
        if s.algorithm=='if':self.estimator=IsolationForest(n_estimators=400,max_samples=s.samples,n_jobs=2,random_state=s.seed)
        elif s.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,batch_size=2048,n_init=10,max_iter=200,random_state=s.seed)
        else:
            x=np.unique(x,axis=0)
            if len(x)>s.cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.cap,False))]
            self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,n_jobs=2)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm=='kmeans':
                lab=self.estimator.predict(x);self.radius=np.ones(s.clusters)
                for k in range(s.clusters):
                    sub=x[lab==k]
                    if len(sub)>20:self.radius[k]=max(float(np.quantile(np.linalg.norm(sub-self.estimator.cluster_centers_[k],axis=1),.95)),.1)
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-start
        return self
    def encode_derived(self,f):
        x=f[list(self.columns)].to_numpy(float);x=np.where(np.isnan(x),self.median,x);x=(x-self.median)/self.scale
        if self.setting.transform=='asinh':x=np.arcsinh(x)
        return np.clip(x,-1e4,1e4)
    def values_derived(self,f):return (((self.encode_derived(f)-self.center)@self.mapping)*self.weights).astype(np.float32)
    def score(self,f):
        x=self.values_derived(self.derive(f));s=self.setting
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                score=np.full(len(x),np.inf)
                for k,c in enumerate(self.estimator.cluster_centers_):score=np.minimum(score,np.linalg.norm(x-c,axis=1)/self.radius[k])
            else:score=-self.estimator.score_samples(x)
        if not np.isfinite(score).all():raise ValueError('Nonfinite')
        return score
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'threshold':self.threshold,'fit_rows':self.fit_rows,
        'fit_seconds':self.fit_seconds,'estimator_count':1,'adapter_experts':False,'optional_native_context':True,
        'context_unavailable_has_explicit_mask':True}
