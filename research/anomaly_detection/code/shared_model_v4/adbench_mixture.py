"""ADBench-only learned normal-regime covariance representation.

One downstream IF/K-means/LOF plus ONE explicit Gaussian-mixture preprocessor.
This is not a pure one-estimator baseline or a service-model improvement.
Neither generation mode nor anomaly labels enter the representation.
"""
from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits
from shared_model_v4.manifold import COLUMNS,Representation as GlobalRepresentation,Setting as GlobalSetting

@dataclass(frozen=True)
class Setting:
    algorithm:str
    components:int=8
    regularization:float=1e-6
    representation:str='local'
    samples:int=256
    clusters:int=8
    neighbors:int=50
    seed:int=42

class Representation:
    def __init__(self,components,regularization):self.components=components;self.regularization=regularization
    def fit(self,f):
        raw=f[list(COLUMNS)].to_numpy(float);self.active=np.ptp(raw,axis=0)>1e-9
        self.median=np.median(raw,axis=0);q10,q90=np.quantile(raw,[.1,.9],axis=0);self.scale=np.maximum(q90-q10,.5)
        x=self.raw(f)
        with threadpool_limits(limits=2):
            self.mixture=GaussianMixture(n_components=self.components,covariance_type='full',reg_covar=self.regularization,n_init=2,max_iter=250,random_state=42).fit(x)
        self.logpdf_center=float(np.median(self.mixture.score_samples(x)));self.logpdf_scale=max(float(np.subtract(*np.quantile(self.mixture.score_samples(x),[.9,.1]))),.1)
        return self
    def raw(self,f):return ((f[list(COLUMNS)].to_numpy(float)-self.median)/self.scale)[:,self.active]
    def transform(self,f,view):
        x=self.raw(f);gm=self.mixture;posterior=gm.predict_proba(x);labels=posterior.argmax(axis=1);local=np.zeros_like(x);minimum=np.full(len(x),np.inf)
        for k in range(self.components):
            white=(x-gm.means_[k])@gm.precisions_cholesky_[k];d=np.sqrt(np.mean(white*white,axis=1));minimum=np.minimum(minimum,d)
            local[labels==k]=white[labels==k]
        pdf=-(gm.score_samples(x)-self.logpdf_center)/self.logpdf_scale
        summary=np.column_stack([minimum,pdf,np.sqrt(np.mean(local*local,axis=1)),np.max(np.abs(local),axis=1)])
        if view=='summary':z=summary
        elif view=='both':z=np.column_stack([x,local,np.log1p(np.maximum(summary,0))])
        else:z=np.column_stack([local,np.log1p(np.maximum(summary,0))])
        return np.clip(z,-1e6,1e6).astype(np.float32)

class Detector:
    def __init__(self,setting,encoder):self.setting=setting;self.algorithm=setting.algorithm;self.encoder=encoder;self.threshold=None
    def fit(self,f):
        start=time.perf_counter();s=self.setting;self.columns=COLUMNS;x=self.encoder.transform(f,s.representation)
        if self.algorithm=='if':self.estimator=IsolationForest(n_estimators=400,max_samples=s.samples,random_state=42,n_jobs=2)
        elif self.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,n_init=10,batch_size=1024,max_iter=250,random_state=42)
        else:self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,n_jobs=2)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if self.algorithm=='kmeans':
                lab=self.estimator.predict(x);self.radius=np.ones(s.clusters)
                for k in range(s.clusters):
                    group=x[lab==k]
                    if len(group)>=20:self.radius[k]=max(float(np.quantile(np.linalg.norm(group-self.estimator.cluster_centers_[k],axis=1),.95)),.1)
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-start;return self
    def score(self,f):
        x=self.encoder.transform(f,self.setting.representation)
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                result=np.full(len(x),np.inf)
                for k,c in enumerate(self.estimator.cluster_centers_):result=np.minimum(result,np.linalg.norm(x-c,axis=1)/self.radius[k])
            else:result=-self.estimator.score_samples(x)
        assert np.isfinite(result).all();return result
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'threshold':self.threshold,'normal_only_training':True,
        'downstream_estimator_count':1,'additional_learned_normal_GMM_preprocessor':1,'service_model':False,'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds}
