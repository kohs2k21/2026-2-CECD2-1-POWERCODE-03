"""Single IF/K-means/LOF with contextual features and shrinkage geometry."""
from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.preprocessing import QuantileTransformer
from sklearn.covariance import OAS
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits
from adapter_feature_ablation.features import BASE,ADAPTER
from shared_model_improvement.models import StableEncoder
from shared_model_target.features import NEW


@dataclass(frozen=True)
class Setting:
    algorithm:str
    view:str="context"
    transform:str="quantile"
    adapter_weight:float=.25
    whitening:bool=False
    rotation:bool=False
    clusters:int=8
    geometry:str="radius"
    neighbors:int=75
    reference_cap:int=4096
    samples:int=256
    trees:int=300
    metric:str="euclidean"
    seed:int=42


class TargetDetector:
    def __init__(self,setting):self.setting,self.algorithm,self.threshold=setting,setting.algorithm,None

    def fit(self,normal):
        start=time.perf_counter();s=self.setting
        if s.view=="current":
            from shared_model_target.current_features import CURRENT
            base=CURRENT
        elif s.view=="context":
            base=(*BASE[:6],"duration_upper_max_1m","duration_upper_mean_5m","fixed_positive_cusum","duration_trend_z")
        elif s.view=="balanced":base=BASE[:21]
        elif s.view=="full":base=BASE
        else:raise ValueError(s.view)
        added=tuple(c for c in ADAPTER if c!="adapter_workload_z")
        extra=() if s.view=="current" else NEW
        self.columns=tuple(dict.fromkeys((*base,*extra,*added)))
        raw=normal[list(self.columns)].to_numpy(float);median=np.nan_to_num(np.nanmedian(raw,axis=0))
        raw=np.where(np.isnan(raw),median,raw)
        q10,q90=np.quantile(raw,[.1,.9],axis=0)
        scale=np.maximum(q90-q10,np.array([.05 if c in BASE and s.transform=="hybrid" else .5 for c in self.columns]))
        weights=np.array([s.adapter_weight if c in ADAPTER else (.25 if c.endswith("assessed") or c=="cadence_ready" else 1.) for c in self.columns])
        quantiles=(QuantileTransformer(n_quantiles=256,output_distribution="normal",subsample=None,random_state=s.seed).fit(raw)
                   if s.transform=="quantile" else None)
        self.encoder=StableEncoder(self.columns,median,scale,s.transform,np.ones(len(self.columns)),quantiles)
        x=self.encoder.transform(normal)
        self.center=np.zeros(x.shape[1]);self.mapping=np.eye(x.shape[1]);self.weights=weights
        if s.whitening:
            for mask in (np.array([c not in ADAPTER for c in self.columns]),np.array([c in ADAPTER for c in self.columns])):
                ids=np.flatnonzero(mask);fit=OAS().fit(x[:,ids]);values,vectors=np.linalg.eigh(fit.covariance_)
                self.center[ids]=fit.location_;self.mapping[np.ix_(ids,ids)]=vectors@np.diag(1/np.sqrt(np.maximum(values,.2)))@vectors.T
        self.rotation=np.eye(x.shape[1])
        if s.rotation:self.rotation=np.linalg.qr(np.random.default_rng(s.seed).normal(size=(x.shape[1],x.shape[1])))[0]
        x=self.transform_values(x)
        self.cluster_scale=None;self.cluster_variance=None
        if s.algorithm=="if":self.estimator=IsolationForest(n_estimators=s.trees,max_samples=s.samples,n_jobs=2,random_state=s.seed)
        elif s.algorithm=="kmeans":self.estimator=MiniBatchKMeans(n_clusters=s.clusters,batch_size=2048,n_init=10,max_iter=150,random_state=s.seed)
        elif s.algorithm=="lof":
            x=np.unique(x,axis=0)
            if len(x)>s.reference_cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.reference_cap,replace=False))]
            self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,metric=s.metric,n_jobs=2)
        else:raise ValueError(s.algorithm)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm=="kmeans":
                labels=self.estimator.predict(x);centers=self.estimator.cluster_centers_
                self.cluster_variance=np.ones_like(centers)
                if s.geometry=="diagonal":
                    global_var=np.var(x,axis=0)
                    for k in range(s.clusters):
                        subset=x[labels==k]
                        self.cluster_variance[k]=np.maximum(.75*np.var(subset,axis=0)+.25*global_var,.25) if len(subset)>=30 else np.maximum(global_var,.25)
                self.cluster_scale=np.ones(s.clusters)
                for k in range(s.clusters):
                    subset=x[labels==k]
                    if len(subset)>=20:self.cluster_scale[k]=max(float(np.quantile(np.sqrt(np.sum((subset-centers[k])**2/self.cluster_variance[k],axis=1)),.95)),.1)
        self.fit_rows,self.fit_seconds=len(x),time.perf_counter()-start
        return self

    def transform_values(self,x):return (((x-self.center)@self.mapping)*self.weights@self.rotation).astype(np.float32)

    def score(self,frame):
        x=self.transform_values(self.encoder.transform(frame))
        with threadpool_limits(limits=2):
            if self.algorithm=="kmeans":
                scores=np.full(len(x),np.inf)
                for k,center in enumerate(self.estimator.cluster_centers_):
                    distance=np.sqrt(np.sum((x-center)**2/self.cluster_variance[k],axis=1))/self.cluster_scale[k]
                    scores=np.minimum(scores,distance)
            else:scores=-self.estimator.score_samples(x)
        if not np.isfinite(scores).all():raise ValueError("Non-finite score")
        return scores

    def predict(self,frame):
        scores=self.score(frame);return scores,scores>self.threshold

    def record(self):return {"settings":asdict(self.setting),"columns":list(self.columns),"threshold":self.threshold,
        "adapter_feature_count":sum(c in ADAPTER for c in self.columns),"fit_rows":self.fit_rows,"fit_seconds":self.fit_seconds,
        "estimator_count":1,"adapter_experts":False}
