"""Reuse the frozen numeric representation, refit a single estimator/geometry."""
from dataclasses import dataclass,asdict
import copy,time
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits

@dataclass(frozen=True)
class Setting:
    algorithm:str
    samples:int=256
    max_features:float=1.
    neighbors:int=400
    reference_cap:int=2048
    metric:str='euclidean'
    radius_power:float=1.
    nearest:bool=False
    seed:int=42

class Detector:
    def __init__(self,parent,setting):
        self.parent=copy.deepcopy(parent);self.geometry=setting;self.threshold=None
        for c in ('algorithm','columns','encoder','setting','fit_rows','fit_seconds'):
            setattr(self,c,getattr(parent,c))
    def values(self,f):
        p=self.parent;x=p.transform_values(p.encoded(f)if hasattr(p,'encoded')else p.encoder.transform(f))
        return p.project(x)if hasattr(p,'project')else x
    def fit(self,f):
        start=time.perf_counter();s=self.geometry;x=self.values(f)
        if self.algorithm=='if':
            estimator=IsolationForest(n_estimators=500,max_samples=s.samples,max_features=s.max_features,random_state=s.seed,n_jobs=2)
        elif self.algorithm=='lof':
            x=np.unique(x,axis=0)
            if len(x)>s.reference_cap:x=x[np.sort(np.random.default_rng(s.seed).choice(len(x),s.reference_cap,False))]
            estimator=LocalOutlierFactor(n_neighbors=s.neighbors,novelty=True,metric=s.metric,n_jobs=2)
        else:self.estimator=self.parent.estimator;return self
        with threadpool_limits(limits=2):estimator.fit(x)
        self.estimator=estimator;self.parent.estimator=estimator
        self.fit_rows=len(x);self.fit_seconds=time.perf_counter()-start
        return self
    def score(self,f):
        x=self.values(f);s=self.geometry
        with threadpool_limits(limits=2):
            if self.algorithm=='kmeans':
                p=self.parent;radius=p.cluster_scale**s.radius_power
                if s.nearest:
                    labels=self.estimator.predict(x)
                    out=np.linalg.norm(x-self.estimator.cluster_centers_[labels],axis=1)/radius[labels]
                else:
                    out=np.full(len(x),np.inf)
                    for k,c in enumerate(self.estimator.cluster_centers_):out=np.minimum(out,np.linalg.norm(x-c,axis=1)/radius[k])
            else:out=-self.estimator.score_samples(x)
        return out
    def record(self):
        r=self.parent.record();representation=r['settings'].copy();effective=representation.copy();s=self.geometry
        if self.algorithm=='if':effective.update(trees=len(self.estimator.estimators_),samples=s.samples,max_features=s.max_features)
        elif self.algorithm=='lof':effective.update(neighbors=s.neighbors,reference_cap=s.reference_cap,metric=s.metric)
        else:effective.update(radius_power=s.radius_power,nearest=s.nearest)
        return {**r,'settings':effective,'representation_settings_before_refit':representation,
            'threshold':self.threshold,'geometry_refit':asdict(s),'actual_estimator_parameters':self.estimator.get_params(),
            'fit_rows':self.fit_rows,'fit_seconds':self.fit_seconds,'estimator_count':1}
