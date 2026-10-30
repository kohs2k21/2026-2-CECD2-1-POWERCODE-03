"""One estimator. Alternative compact views and train-only robust scaling."""
from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from sklearn.covariance import OAS
from threadpoolctl import threadpool_limits
from adapter_feature_ablation.features import ADAPTER
from shared_model_target.current_features import CURRENT
from shared_model_v3.features import SEQUENCE

@dataclass(frozen=True)
class Setting:
    algorithm:str
    view:str='sequence'
    adapter_weight:float=.25
    transform:str='asinh'
    samples:int=256
    trees:int=300
    clusters:int=8
    neighbors:int=300
    reference_cap:int=2048
    rotation:bool=False
    whitening:bool=False
    trim:float=.01
    scale_floor:float=.1
    seed:int=42

class Detector:
    def __init__(self,setting):self.setting,self.algorithm,self.threshold=setting,setting.algorithm,None
    def fit(self,f):
        t=time.perf_counter();s=self.setting
        adapter=tuple(c for c in ADAPTER if c!='adapter_workload_z')
        if s.view=='sequence':cols=(*SEQUENCE,*adapter,*CURRENT)
        elif s.view=='compact':cols=(*SEQUENCE,*adapter,'current_source_upper','current_source_lower','traffic_assessed','latency_assessed')
        elif s.view=='minimal':cols=('seq_residual_shift','seq_last_residual','seq_arrival_lift','seq_burst_3m','seq_gap_lift',
            'current_source_upper','current_source_lower',*adapter)
        elif s.view=='legacy':cols=(*CURRENT,*adapter,'residual_change','duration_slope','prefix_duration_z','recent_residual_lift',
            'seq_duration_shift','seq_last_shift','seq_arrival_deviance','seq_burst_3m','seq_gap_surprise')
        elif s.view in ('rank','summary','state','small','small_compact','small_minimal'):cols=self.derived_columns
        else:raise ValueError(s.view)
        self.columns=cols;self.encoder=None;x=f[list(cols)].to_numpy(float)
        self.median=np.nan_to_num(np.nanmedian(x,axis=0));x=np.where(np.isnan(x),self.median,x)
        q10,q90=np.quantile(x,[.1,.9],axis=0);self.scale=np.maximum(q90-q10,s.scale_floor)
        self.weights=np.array([s.adapter_weight if c in ADAPTER else .1 if c.endswith('assessed')or c=='cadence_ready'else 1. for c in cols])
        self.center=np.zeros(len(cols));self.mapping=np.eye(len(cols));self.rotation=np.eye(len(cols))
        x=self.encode(f)
        if s.whitening:
            fit=OAS().fit(x);v,u=np.linalg.eigh(fit.covariance_);self.center=fit.location_
            self.mapping=u@np.diag(1/np.sqrt(np.maximum(v,.2)))@u.T
        if s.rotation:self.rotation=np.linalg.qr(np.random.default_rng(s.seed).normal(size=(len(cols),len(cols))))[0]
        x=self.values(f)
        if s.trim:
            leverage=np.sum(x*x,axis=1);x=x[leverage<=np.quantile(leverage,1-s.trim)]
        if s.algorithm=='if':self.estimator=IsolationForest(n_estimators=s.trees,max_samples=s.samples,random_state=s.seed,n_jobs=2)
        elif s.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,batch_size=2048,n_init=10,max_iter=200,random_state=s.seed)
        else:
            x=np.unique(x,axis=0)
            if len(x)>s.reference_cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.reference_cap,replace=False))]
            self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,n_jobs=2)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm=='kmeans':
                lab=self.estimator.predict(x);self.radius=np.ones(s.clusters)
                for k in range(s.clusters):
                    v=x[lab==k]
                    if len(v)>20:self.radius[k]=max(float(np.quantile(np.linalg.norm(v-self.estimator.cluster_centers_[k],axis=1),.95)),.1)
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-t
        return self
    def encode(self,f):
        x=f[list(self.columns)].to_numpy(float);x=np.where(np.isnan(x),self.median,x);x=(x-self.median)/self.scale
        if self.setting.transform=='asinh':x=np.arcsinh(x)
        elif self.setting.transform=='hybrid':
            mask=np.array([c in ADAPTER for c in self.columns]);x[:,mask]=np.arcsinh(x[:,mask])
        elif self.setting.transform=='sqrt':x=np.sign(x)*np.sqrt(np.abs(x))
        return np.clip(x,-100,100)
    def values(self,f):return ((((self.encode(f)-self.center)@self.mapping)*self.weights)@self.rotation).astype(np.float32)
    def score(self,f):
        x=self.values(f)
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                scores=np.full(len(x),np.inf)
                for k,c in enumerate(self.estimator.cluster_centers_):scores=np.minimum(scores,np.linalg.norm(x-c,axis=1)/self.radius[k])
            else:scores=-self.estimator.score_samples(x)
        if not np.isfinite(scores).all():raise ValueError('Nonfinite score')
        return scores
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'threshold':self.threshold,
        'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,'estimator_count':1,'adapter_experts':False,
        'adapter_feature_count':sum(c in ADAPTER for c in self.columns)}
