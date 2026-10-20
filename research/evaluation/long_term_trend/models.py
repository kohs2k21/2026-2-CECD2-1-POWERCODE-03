"""Read-only reuse of the published IF/EIF implementations and train-only statistics."""
from dataclasses import dataclass
import numpy as np

from research.evaluation.if_feature_comparison.models import StandardIF,ExtendedIF,verify_sources,SETTINGS


@dataclass
class RobustEncoder:
    median:np.ndarray
    scale:np.ndarray

    @classmethod
    def fit(cls,X):
        X=np.asarray(X,float)
        if X.ndim!=2 or len(X)<256 or not np.isfinite(X).all():
            raise ValueError('Insufficient finite normal training features')
        if not np.any(np.ptp(X,axis=0)>0):raise ValueError('All normal training features are constant')
        q10,q90=np.quantile(X,[.1,.9],axis=0)
        return cls(np.median(X,axis=0),np.maximum(q90-q10,.05))

    def transform(self,X):
        X=np.asarray(X,float)
        if not np.isfinite(X).all():raise ValueError('Ready model features must be finite')
        return ((X-self.median)/self.scale).astype(np.float32)


@dataclass
class StatisticalReference:
    median:np.ndarray
    scale:np.ndarray
    active:np.ndarray
    names:list

    @classmethod
    def fit(cls,X,validity,names,validity_feature_names):
        X=np.asarray(X,float);validity=np.asarray(validity,bool)
        if X.shape!=validity.shape:raise ValueError('Feature-specific validity must match X')
        median=np.zeros(X.shape[1]);scale=np.ones(X.shape[1]);active=np.zeros(X.shape[1],bool)
        excluded=set(validity_feature_names)
        for j,name in enumerate(names):
            values=X[validity[:,j]&np.isfinite(X[:,j]),j]
            if name in excluded or not len(values):continue
            median[j]=np.median(values);q10,q90=np.quantile(values,[.1,.9])
            scale[j]=max(float(q90-q10),.05);active[j]=True
        if not active.any():raise ValueError('No valid physical statistic for the SR reference')
        return cls(median,scale,active,list(names))

    def score(self,X,validity):
        X=np.asarray(X,float);valid=np.asarray(validity,bool)&self.active[None,:]&np.isfinite(X)
        deviation=np.where(valid,np.abs((X-self.median)/self.scale),-np.inf)
        scores=np.max(deviation,axis=1);scores[~valid.any(axis=1)]=np.nan
        return scores


def fit_forest(algorithm,seed,X):
    verify_sources()
    model={'if':StandardIF,'eif':ExtendedIF}[algorithm](seed)
    return model.fit(X)
