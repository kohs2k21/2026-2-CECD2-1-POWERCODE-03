"""Frozen normal covariance/linear subspace features, one detector afterwards.

No Adapter expert estimators. Context tables are normal first/second moments;
unknown groups use global moments. 35 common inputs also exist in ADBench.
"""
from dataclasses import dataclass,asdict
import time
import numpy as np
from sklearn.covariance import OAS
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits
from shared_model_target.current_features import CURRENT
from shared_model_v2.features import MEMORY
from adapter_feature_ablation.features import ADAPTER

COLUMNS=(*CURRENT,*(c for c in ADAPTER if c!='adapter_workload_z'),*MEMORY)

@dataclass(frozen=True)
class Setting:
    algorithm:str
    transform:str='linear'
    floor:float=.001
    shrinkage:float=.1
    context:str='global'
    summary:bool=True
    mode:str='white'
    samples:int=256
    max_features:float=1.
    clusters:int=16
    neighbors:int=200
    cap:int=4096
    seed:int=42

class Representation:
    def __init__(self,setting):self.setting=setting
    def keys(self,f):
        mode=self.setting.context
        if mode=='global':return np.repeat('global',len(f))
        gate=f.traffic_assessed.to_numpy(int)+2*f.latency_assessed.to_numpy(int)
        if mode=='availability':return gate.astype(str)
        return np.array([a+'|'+str(g)for a,g in zip(f.adapterType.to_numpy(str),gate)])
    def raw(self,f):
        x=f[list(COLUMNS)].to_numpy(float);x=np.where(np.isnan(x),self.median,x)
        if not np.isfinite(x).all():raise ValueError('Non-finite input')
        x=(x-self.median)/self.scale
        if self.setting.transform=='asinh':x=np.arcsinh(x)
        return x
    def stats(self,x):
        center=x.mean(axis=0);cov=np.cov(x,rowvar=False)
        # Relative ridge avoids numerical amplification while preserving small
        # normal residual directions. This is not a density expert/model.
        ridge=max(float(np.trace(cov)/len(cov)),.1)*self.setting.floor
        diagonal=np.diag(np.diag(cov));cov=(1-self.setting.shrinkage)*cov+self.setting.shrinkage*diagonal
        values,vectors=np.linalg.eigh(cov);values=np.maximum(values,ridge)
        return center,vectors,values
    def fit(self,f):
        x=f[list(COLUMNS)].to_numpy(float);self.median=np.nan_to_num(np.nanmedian(x,axis=0))
        x=np.where(np.isnan(x),self.median,x);q10,q90=np.quantile(x,[.1,.9],axis=0);self.scale=np.maximum(q90-q10,.5)
        x=self.raw(f);self.global_stats=self.stats(x);self.tables={}
        keys=self.keys(f)
        for key in np.unique(keys):
            mask=keys==key
            if mask.sum()>=300 and f.loc[mask,'case_id'].nunique()>=5:self.tables[key]=self.stats(x[mask])
        self.radius_scale=None
        return self
    def apply_stats(self,x,stats):
        center,axes,variance=stats;project=(x-center)@axes
        white=project/np.sqrt(variance)
        # Projected residual and leverage: no fitted anomaly classifier here.
        low=np.arange(len(variance))<max(2,len(variance)//3)
        radial=np.sqrt(np.mean(white*white,axis=1));low_norm=np.sqrt(np.mean(white[:,low]**2,axis=1))
        return white,np.column_stack([radial,low_norm,np.max(np.abs(white),axis=1)])
    def transform(self,f):
        x=self.raw(f);white,summary=self.apply_stats(x,self.global_stats)
        if self.setting.context!='global':
            keys=self.keys(f)
            for key in np.unique(keys):
                if key in self.tables:
                    ids=keys==key;white[ids],summary[ids]=self.apply_stats(x[ids],self.tables[key])
        if self.setting.mode=='radius':z=summary
        elif self.setting.mode=='both':z=np.column_stack([x,white])
        else:z=white
        if self.setting.summary and self.setting.mode!='radius':z=np.column_stack([z,np.log1p(summary)])
        return np.clip(z,-1e6,1e6).astype(np.float32)

class Detector:
    def __init__(self,setting):self.setting=setting;self.algorithm=setting.algorithm;self.threshold=None
    def make_encoder(self,setting):return Representation(setting)
    def fit(self,f):
        t=time.perf_counter();s=self.setting;self.columns=COLUMNS
        self.encoder=self.make_encoder(s).fit(f);x=self.encoder.transform(f)
        self.fit_rows=len(x)
        if s.algorithm=='if':self.estimator=IsolationForest(n_estimators=400,max_samples=s.samples,max_features=s.max_features,random_state=s.seed,n_jobs=2)
        elif s.algorithm=='kmeans':self.estimator=MiniBatchKMeans(n_clusters=s.clusters,batch_size=2048,n_init=10,max_iter=200,random_state=s.seed)
        else:
            x=np.unique(x,axis=0)
            if len(x)>s.cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.cap,replace=False))]
            self.fit_rows=len(x);self.estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,n_jobs=2)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm=='kmeans':
                lab=self.estimator.predict(x);self.radius=np.ones(s.clusters)
                for k in range(s.clusters):
                    sub=x[lab==k]
                    if len(sub)>20:self.radius[k]=max(float(np.quantile(np.linalg.norm(sub-self.estimator.cluster_centers_[k],axis=1),.95)),.1)
        self.fit_seconds=time.perf_counter()-t
        return self
    def score(self,f):
        x=self.encoder.transform(f)
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                score=np.full(len(x),np.inf)
                for k,c in enumerate(self.estimator.cluster_centers_):score=np.minimum(score,np.linalg.norm(x-c,axis=1)/self.radius[k])
            else:score=-self.estimator.score_samples(x)
        if not np.isfinite(score).all():raise ValueError('Nonfinite scores')
        return score
    def predict(self,f):
        s=self.score(f);return s,s>self.threshold
    def record(self):return {'settings':asdict(self.setting),'columns':list(self.columns),'threshold':self.threshold,
        'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,'estimator_count':1,'adapter_experts':False,
        'adapter_feature_count':9,'normal_covariance_tables':len(self.encoder.tables)}
