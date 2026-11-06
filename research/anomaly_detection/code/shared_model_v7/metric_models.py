"""Controlled algorithmic ablations on frozen normal numerical geometry.

Cluster-local covariance avoids a broad spherical cluster hiding deviations.
LOF compares metrics and preservation of normal pattern multiplicity. IF
compares normal-only oblique numerical rotations, not a new expert ensemble.
"""
from dataclasses import dataclass,asdict
import time
import numpy as np
from scipy.special import logsumexp
from sklearn.cluster import MiniBatchKMeans
from sklearn.covariance import OAS
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits
from shared_model_v7.agreement import Geometry
from shared_model_v6.features import Evidence

@dataclass(frozen=True)
class Setting:
    algorithm:str
    view:str='frozen_geometry'
    transform:str='frozen'
    source:str='v6'
    geometry:str='radius'
    samples:int=128
    trees:int=500
    max_features:float=1.
    clusters:int=8
    neighbors:int=200
    cap:int=4096
    metric:str='euclidean'
    p:int=2
    deduplicate:bool=True
    jitter:float=0.
    rotate:bool=False
    white:bool=False
    ridge:float=.01
    seed:int=42

class Encoder:
    def __init__(self,base,history):self.geometry=Geometry(base);self.evidence=Evidence(history)
    def fit(self,f):self.evidence.fit(f);return self
    def transform(self,f,view):
        if view=='peaks':return self.evidence.transform(f,'peaks')
        return self.geometry.values(self.evidence.derive(f))

class Detector:
    def __init__(self,s,history,parent):
        self.setting=s;self.algorithm=s.algorithm;self.threshold=None;self.encoder=Encoder(parent,history)
        self.columns=parent.columns;self.transformed_columns=self.encoder.geometry.names
    def values(self,f):return ((self.encoder.transform(f,'geometry')-self.center)@self.mapping@self.rotation).astype(np.float32)
    def fit(self,f):
        start=time.perf_counter();s=self.setting;self.encoder.fit(f);x=self.encoder.transform(f,'geometry');dim=x.shape[1]
        self.center=np.zeros(dim);self.mapping=np.eye(dim);self.rotation=np.eye(dim)
        if s.white:
            fit=OAS().fit(x);v,u=np.linalg.eigh(fit.covariance_);self.center=fit.location_;self.mapping=u@np.diag(1/np.sqrt(np.maximum(v,.01)))@u.T
        if s.rotate:self.rotation=np.linalg.qr(np.random.default_rng(s.seed).normal(size=(dim,dim)))[0]
        x=self.values(f)
        if s.algorithm=='if':self.estimator=IsolationForest(n_estimators=s.trees,max_samples=s.samples,max_features=s.max_features,n_jobs=2,random_state=s.seed)
        elif s.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,n_init=10,batch_size=2048,max_iter=200,random_state=s.seed)
        else:
            if s.deduplicate:x=np.unique(x,axis=0)
            if len(x)>s.cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.cap,False))]
            if s.jitter:x=x+np.random.default_rng(s.seed+1).normal(0,s.jitter,x.shape)
            self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,n_jobs=2,metric=s.metric,p=s.p)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm=='kmeans':
                labels=self.estimator.predict(x);self.local=[];self.radius=[]
                for k,c in enumerate(self.estimator.cluster_centers_):
                    part=x[labels==k];delta=part-c
                    if len(part)>max(20,dim+2):cov=OAS().fit(part).covariance_
                    else:cov=np.eye(dim)
                    if s.geometry=='diagonal':cov=np.diag(np.diag(cov))
                    val,axes=np.linalg.eigh(cov);floor=max(float(np.trace(cov)/dim),.1)*s.ridge;val=np.maximum(val,floor)
                    precision=axes@np.diag(1/val)@axes.T;logdet=float(np.log(val).sum());prior=max(len(part)/len(x),1e-6)
                    self.local.append((precision,logdet,prior))
                    distances=np.sqrt(np.maximum(np.einsum('ij,jk,ik->i',delta,precision,delta),0.))
                    self.radius.append(max(float(np.quantile(distances,.95))if len(distances)else 1.,.1))
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-start;return self
    def score(self,f):
        x=self.values(f)
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                values=[]
                for k,c in enumerate(self.estimator.cluster_centers_):
                    precision,ld,prior=self.local[k];delta=x-c;square=np.maximum(np.einsum('ij,jk,ik->i',delta,precision,delta),0.)
                    values.append((-.5*(square+ld)+np.log(prior))if self.setting.geometry=='density'else np.sqrt(square)/self.radius[k])
                score=-logsumexp(np.column_stack(values),axis=1)if self.setting.geometry=='density'else np.min(np.column_stack(values),axis=1)
            else:score=-self.estimator.score_samples(x)
        if not np.isfinite(score).all():raise ValueError('Nonfinite scores')
        return score
    def predict(self,f):s=self.score(f);return s,s>self.threshold
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'transformed_columns':self.transformed_columns,'threshold':self.threshold,
        'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,'estimator_count':1,'adapter_experts':False,'old_estimator_embedded':False,
        'actual_estimator_parameters':self.estimator.get_params()}
