"""One sklearn estimator with optional memory and robust distance geometry."""
from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.preprocessing import QuantileTransformer
from sklearn.covariance import OAS
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from sklearn.decomposition import PCA
from threadpoolctl import threadpool_limits
from adapter_feature_ablation.features import ADAPTER
from shared_model_target.current_features import CURRENT
from shared_model_improvement.models import StableEncoder
from shared_model_v2.features import MEMORY


@dataclass(frozen=True)
class Setting:
    algorithm:str
    memory:bool=True
    transform:str='hybrid'
    adapter_weight:float=.25
    memory_weight:float=.5
    whitening:bool=False
    rotation:bool=False
    clusters:int=16
    geometry:str='nearest'
    radius_quantile:float=.95
    neighbors:int=500
    reference_cap:int=4096
    samples:int=256
    trees:int=300
    trim:float=0.
    metric:str='euclidean'
    seed:int=42
    latency_weight:float=1.
    traffic_weight:float=1.
    reference_weight:float=1.
    interactions:bool=False
    deduplicate:bool=True
    reference_noise:float=0.
    projection:int=0


class Detector:
    def __init__(self,setting):self.setting,self.algorithm,self.threshold=setting,setting.algorithm,None
    def derive(self,frame):
        if not getattr(self.setting,'interactions',False):return frame
        f=frame.copy()
        p=np.maximum(f.current_prefix_delay.to_numpy(float),0)
        source=np.maximum(f.current_source_delay.to_numpy(float),0)
        upper=np.maximum(f.current_source_upper.to_numpy(float),0)
        tail=np.maximum(f.adapter_count_p95_upper_tail.to_numpy(float),0)
        trend=np.maximum(f.current_delay_trend.to_numpy(float),0)
        f['coherent_prefix_source']=np.log1p(p)*source
        f['coherent_tail_agreement']=np.sqrt(upper*tail)
        f['coherent_self_tail']=p/(1+p)*upper
        f['coherent_drift']=np.arcsinh(p)*np.arcsinh(trend)
        burst=np.maximum(f.current_arrival_excess.to_numpy(float),0)
        f['coherent_burst']=np.maximum(f.current_prefix_burst,0)*burst/(1+burst)
        f['coherent_gap']=np.sqrt(np.maximum(f.gap_expected_prefix,0)*np.maximum(f.gap_expected_recent,0))
        return f
    def encoded(self,frame):return self.encoder.transform(self.derive(frame))
    def fit(self,normal):
        start=time.perf_counter();s=self.setting
        self.columns=(*CURRENT,*(c for c in ADAPTER if c!='adapter_workload_z'),*(MEMORY if s.memory else ()))
        if s.interactions:self.columns+=('coherent_prefix_source','coherent_tail_agreement','coherent_self_tail','coherent_drift','coherent_burst','coherent_gap')
        normal=self.derive(normal)
        raw=normal[list(self.columns)].to_numpy(float);median=np.nan_to_num(np.nanmedian(raw,axis=0))
        raw=np.where(np.isnan(raw),median,raw);q10,q90=np.quantile(raw,[.1,.9],axis=0)
        scale=np.maximum(q90-q10,.5)
        quantiles=QuantileTransformer(n_quantiles=256,output_distribution='normal',subsample=None,random_state=s.seed).fit(raw)if s.transform=='quantile'else None
        self.encoder=StableEncoder(self.columns,median,scale,s.transform,np.ones(len(self.columns)),quantiles)
        self.weights=np.array([s.adapter_weight if c in ADAPTER else s.memory_weight if c in MEMORY else .25 if c.endswith('assessed')or c=='cadence_ready'else 1. for c in self.columns])
        for i,c in enumerate(self.columns):
            if c in CURRENT[:5]:self.weights[i]*=s.traffic_weight
            elif c in CURRENT[5:13]:self.weights[i]*=s.latency_weight
            elif c=='adapter_reference_log_duration':self.weights[i]*=s.reference_weight
        x=self.encoder.transform(normal);self.center=np.zeros(x.shape[1]);self.mapping=np.eye(x.shape[1]);self.rotation=np.eye(x.shape[1])
        if s.whitening:
            for mask in (np.array([c not in ADAPTER for c in self.columns]),np.array([c in ADAPTER for c in self.columns])):
                ids=np.flatnonzero(mask);fit=OAS().fit(x[:,ids]);v,u=np.linalg.eigh(fit.covariance_)
                self.center[ids]=fit.location_;self.mapping[np.ix_(ids,ids)]=u@np.diag(1/np.sqrt(np.maximum(v,.2)))@u.T
        if s.rotation:self.rotation=np.linalg.qr(np.random.default_rng(s.seed).normal(size=(x.shape[1],x.shape[1])))[0]
        x=self.transform_values(x);self.cluster_scale=None;self.cluster_variance=None
        if s.trim:
            leverage=np.sum(np.minimum(x*x,1e4),axis=1);x=x[leverage<=np.quantile(leverage,1-s.trim)]
        if s.projection:
            pca=PCA(n_components=s.projection,svd_solver='full').fit(x)
            self.projection_center=pca.mean_.astype(np.float32);self.projection_axes=pca.components_.astype(np.float32)
            centered=x-self.projection_center;z=centered@self.projection_axes.T
            error=np.linalg.norm(centered-z@self.projection_axes,axis=1)
            self.projection_error_scale=max(float(np.quantile(error,.95)),.1)
            x=self.project(x)
        if s.algorithm=='if':self.estimator=IsolationForest(n_estimators=s.trees,max_samples=s.samples,n_jobs=2,random_state=s.seed)
        elif s.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,batch_size=2048,n_init=10,max_iter=200,random_state=s.seed)
        elif s.algorithm=='lof':
            if s.deduplicate:x=np.unique(x,axis=0)
            if len(x)>s.reference_cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.reference_cap,replace=False))]
            if s.reference_noise:
                mask=(np.ones(x.shape[1],bool)if s.projection else
                      np.array([not(c.endswith('assessed')or c=='cadence_ready')for c in self.columns]))
                x=x.copy();x[:,mask]+=np.random.default_rng(s.seed+100).normal(0,s.reference_noise,(len(x),int(mask.sum()))).astype(np.float32)
            self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,metric=s.metric,n_jobs=2)
        else:raise ValueError(s.algorithm)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm=='kmeans':
                labels=self.estimator.predict(x);centers=self.estimator.cluster_centers_
                self.cluster_variance=np.ones_like(centers)
                if s.geometry=='diagonal':
                    global_var=np.var(x,axis=0)
                    for k in range(s.clusters):
                        sub=x[labels==k];self.cluster_variance[k]=np.maximum(.75*np.var(sub,axis=0)+.25*global_var,.25)if len(sub)>=30 else np.maximum(global_var,.25)
                self.cluster_scale=np.ones(s.clusters)
                for k in range(s.clusters):
                    sub=x[labels==k]
                    if len(sub)>=20:self.cluster_scale[k]=max(float(np.quantile(np.sqrt(np.sum((sub-centers[k])**2/self.cluster_variance[k],axis=1)),s.radius_quantile)),.1)
        self.fit_rows,self.fit_seconds=len(x),time.perf_counter()-start
        return self
    def transform_values(self,x):return ((((x-self.center)@self.mapping)*self.weights)@self.rotation).astype(np.float32)
    def project(self,x):
        if not getattr(self.setting,'projection',0):return x
        centered=x-self.projection_center;z=centered@self.projection_axes.T
        error=np.linalg.norm(centered-z@self.projection_axes,axis=1)/self.projection_error_scale
        return np.column_stack([z,np.log1p(error)]).astype(np.float32)
    def score(self,frame):
        x=self.project(self.transform_values(self.encoded(frame)));s=self.setting
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                if s.geometry=='nearest':
                    labels=self.estimator.predict(x)
                    scores=np.linalg.norm(x-self.estimator.cluster_centers_[labels],axis=1)/self.cluster_scale[labels]
                else:
                    scores=np.full(len(x),np.inf)
                    for k,c in enumerate(self.estimator.cluster_centers_):
                        d=np.sqrt(np.sum((x-c)**2/self.cluster_variance[k],axis=1))/self.cluster_scale[k]
                        scores=np.minimum(scores,d)
            else:scores=-self.estimator.score_samples(x)
        if not np.isfinite(scores).all():raise ValueError('Non-finite score')
        return scores
    def predict(self,f):
        s=self.score(f);return s,s>self.threshold
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'threshold':self.threshold,
        'adapter_feature_count':sum(c in ADAPTER for c in self.columns),'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,
        'estimator_count':1,'adapter_experts':False}
