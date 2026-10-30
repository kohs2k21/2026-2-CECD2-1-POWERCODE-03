from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from sklearn.covariance import OAS
from threadpoolctl import threadpool_limits
from shared_model_v6.features import Evidence,RAW

@dataclass(frozen=True)
class Setting:
    algorithm:str
    view:str='peaks'
    transform:str='linear'
    conditional:bool=False
    white:bool=False
    samples:int=128
    trees:int=400
    clusters:int=8
    neighbors:int=100
    cap:int=4096
    max_features:float=1.
    burst_weight:float=1.
    gap_weight:float=1.
    seed:int=42
    tail:bool=False

class Detector:
    def __init__(self,setting,history):
        self.setting=setting;self.algorithm=setting.algorithm;self.encoder=Evidence(history,setting.conditional,setting.transform,setting.tail);self.threshold=None
    def fit(self,f):
        start=time.perf_counter();s=self.setting;self.encoder.fit(f);self.columns=tuple(RAW)
        self.transformed_columns=self.encoder.names(s.view);self.center=np.zeros(len(self.transformed_columns));self.mapping=np.eye(len(self.center))
        x=self.encoder.transform(f,s.view)
        if s.white:
            fit=OAS().fit(x);v,u=np.linalg.eigh(fit.covariance_);self.center=fit.location_;self.mapping=u@np.diag(1/np.sqrt(np.maximum(v,.1)))@u.T
        self.weights=np.array([s.burst_weight if 'burst' in c else s.gap_weight if 'gap' in c else 1. for c in self.transformed_columns])
        x=self.values(f)
        if s.algorithm=='if':self.estimator=IsolationForest(n_estimators=s.trees,max_samples=s.samples,max_features=s.max_features,random_state=s.seed,n_jobs=2)
        elif s.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,n_init=10,batch_size=2048,max_iter=200,random_state=s.seed)
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
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-start;return self
    def values(self,f):return (((self.encoder.transform(f,self.setting.view)-self.center)@self.mapping)*self.weights).astype(np.float32)
    def score(self,f):
        x=self.values(f)
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                score=np.full(len(x),np.inf)
                for k,c in enumerate(self.estimator.cluster_centers_):score=np.minimum(score,np.linalg.norm(x-c,axis=1)/self.radius[k])
            else:score=-self.estimator.score_samples(x)
        assert np.isfinite(score).all();return score
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'transformed_columns':self.transformed_columns,
        'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,'threshold':self.threshold,'estimator_count':1,'adapter_experts':False,
        'normal_only_evidence_scaling':True,'hard_rules_added':False,'actual_estimator_parameters':self.estimator.get_params()}
