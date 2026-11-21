"""One estimator after normal-only completion-regime covariance normalization."""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.models import Setting as BaseSetting,Detector as Base
from shared_model_v6.features import Evidence,RAW
from shared_model_v7.weighted import EXTRA

@dataclass(frozen=True)
class Setting(BaseSetting):
    regime:bool=True
    geometry:str='radius'
    ridge:float=.01
    accumulated:bool=False

class Encoder:
    def __init__(self,history,s):self.core=Evidence(history);self.setting=s
    def keys(self,f):return np.minimum(f.completion_count_1m.fillna(0).to_numpy(int),2)
    def raw(self,f):
        x=self.core.transform(f,'raw')
        if self.setting.accumulated:
            e=np.nan_to_num(f[list(EXTRA)].to_numpy(float),nan=0.)
            e=np.maximum((e-self.ec)/self.es,0.);x=np.column_stack([x,e])
        return np.arcsinh(x)if self.setting.transform=='asinh'else x
    def stats(self,x):
        center=np.mean(x,axis=0);cov=np.cov(x,rowvar=False)
        cov=.8*cov+.2*np.diag(np.diag(cov))
        val,axes=np.linalg.eigh(cov);floor=max(float(np.trace(cov)/len(cov)),.1)*self.setting.ridge
        mapping=axes@np.diag(1/np.sqrt(np.maximum(val,floor)))@axes.T
        return center,mapping
    def fit(self,f):
        self.core.fit(f);e=np.nan_to_num(f[list(EXTRA)].to_numpy(float),nan=0.);self.ec=np.median(e,axis=0);self.es=np.maximum(np.quantile(e,.95,axis=0)-self.ec,.15)
        x=self.raw(f);self.global_stats=self.stats(x);self.tables={};keys=self.keys(f)
        if self.setting.regime:
            for key in np.unique(keys):
                mask=keys==key
                if mask.sum()>=80 and f.loc[mask,'case_id'].nunique()>=5:self.tables[int(key)]=self.stats(x[mask])
        return self
    def transform(self,f,view):
        if view=='peaks':return self.core.transform(f,'peaks')
        x=self.raw(f);c,m=self.global_stats;z=(x-c)@m
        for key,(c,m)in self.tables.items():
            mask=self.keys(f)==key;z[mask]=(x[mask]-c)@m
        if self.setting.geometry=='white':return z
        radius=np.column_stack([np.sqrt(np.mean(z*z,axis=1)),np.max(np.abs(z),axis=1),np.sqrt(np.mean(np.maximum(z,0.)**2,axis=1))])
        if self.setting.geometry=='radius':return radius
        if self.setting.geometry=='two_axis':return np.column_stack([np.sqrt(np.mean(np.maximum(z[:,:2],0.)**2,axis=1)),np.sqrt(np.mean(z[:,2:]**2,axis=1))])
        return np.column_stack([z,np.log1p(radius)])
    def names(self,view):
        n=len(RAW)+(len(EXTRA)if self.setting.accumulated else 0)
        if self.setting.geometry=='white':return ['regime_white_'+str(i)for i in range(n)]
        if self.setting.geometry=='radius':return ['regime_radius','regime_max','regime_positive_radius']
        if self.setting.geometry=='two_axis':return ['regime_point_radius','regime_other_radius']
        return ['regime_white_'+str(i)for i in range(n)]+['regime_log_radius','regime_log_max','regime_log_positive_radius']

class Detector(Base):
    def __init__(self,setting,history):super().__init__(setting,history);self.encoder=Encoder(history,setting)
    def fit(self,f):super().fit(f);self.columns=tuple([*RAW,*EXTRA]);return self
    def record(self):return {**super().record(),'normal_completion_regime_covariance':len(self.encoder.tables),'no_contextual_expert_estimators':True}
