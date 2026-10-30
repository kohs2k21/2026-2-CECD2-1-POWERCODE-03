"""ADBench-only K-means: score the assigned cluster, not any broad cluster.

The old min(distance / cluster radius) can give a remote, diffuse cluster an
unduly broad acceptance region. Compare nearest-centroid assignment, cluster
covariance residuals, and the original policy on disjoint development data.
"""
from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from threadpoolctl import threadpool_limits
from shared_model_v4.manifold import COLUMNS

@dataclass(frozen=True)
class Setting:
    clusters:int=16
    score_mode:str='nearest'
    radius_power:float=.5
    covariance_floor:float=.1
    seed:int=42

class Detector:
    def __init__(self,setting,encoder):self.setting=setting;self.encoder=encoder;self.algorithm='kmeans';self.threshold=None;self.columns=COLUMNS
    def values(self,f):return self.encoder.transform(f)
    def fit(self,f):
        start=time.perf_counter();s=self.setting;x=self.values(f);p=x.shape[1]
        self.estimator=MiniBatchKMeans(n_clusters=s.clusters,batch_size=1024,n_init=20,max_iter=300,reassignment_ratio=0.,random_state=s.seed)
        with threadpool_limits(limits=2):self.estimator.fit(x);labels=self.estimator.predict(x)
        self.radius=np.ones(s.clusters);self.mappings=np.repeat(np.eye(p)[None,:,:],s.clusters,axis=0)
        for k,center in enumerate(self.estimator.cluster_centers_):
            sub=x[labels==k];residual=sub-center
            if len(sub)>=5:
                self.radius[k]=max(float(np.quantile(np.linalg.norm(residual,axis=1),.95)),.1)
                cov=(residual.T@residual)/len(residual);cov=.9*cov+.1*np.diag(np.diag(cov))
                floor=max(float(np.trace(cov)/p),.01)*s.covariance_floor;v,u=np.linalg.eigh(cov)
                self.mappings[k]=u@np.diag(1/np.sqrt(np.maximum(v,floor)))@u.T
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-start;return self
    def score(self,f):
        x=self.values(f);s=self.setting
        with threadpool_limits(limits=2):labels=self.estimator.predict(x)
        result=np.zeros(len(x))
        for k,center in enumerate(self.estimator.cluster_centers_):
            ids=labels==k;residual=x[ids]-center
            if s.score_mode=='mahalanobis':result[ids]=np.linalg.norm(residual@self.mappings[k],axis=1)
            else:result[ids]=np.linalg.norm(residual,axis=1)/(self.radius[k]**s.radius_power)
        assert np.isfinite(result).all();return result
    def record(self):return {'settings':asdict(self.setting),'threshold':self.threshold,'columns':list(COLUMNS),
        'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,'estimator_count':1,'ADBench_specific_not_service_model':True,'assigned_centroid_only':True}
