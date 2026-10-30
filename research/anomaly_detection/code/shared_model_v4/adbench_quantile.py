"""Normal marginal Gaussianization + linear residuals, no extra classifier.

Quantile mapping and covariance are fitted on normal train only. Keeping a raw
residual summary avoids silently clipping away outside-training-range values.
"""
from dataclasses import dataclass
import numpy as np
from sklearn.preprocessing import QuantileTransformer
from shared_model_v4.manifold import Setting as ParentSetting,Detector as ParentDetector,COLUMNS

@dataclass(frozen=True)
class Setting(ParentSetting):
    quantile_view:str='rank'

class Representation:
    def __init__(self,setting):self.setting=setting;self.tables={}
    def fit(self,f):
        raw=f[list(COLUMNS)].to_numpy(float);self.active=np.ptp(raw,axis=0)>1e-9;raw=raw[:,self.active]
        self.median=np.median(raw,axis=0);q10,q90=np.quantile(raw,[.1,.9],axis=0);self.scale=np.maximum(q90-q10,.5)
        x=(raw-self.median)/self.scale
        self.qt=QuantileTransformer(n_quantiles=min(1000,len(x)),output_distribution='normal',subsample=None,random_state=42).fit(x)
        g=self.qt.transform(x);self.rank_stats=self.stats(g);self.raw_stats=self.stats(x)
        return self
    def stats(self,x):
        center=x.mean(axis=0);cov=np.cov(x,rowvar=False);s=self.setting
        cov=(1-s.shrinkage)*cov+s.shrinkage*np.diag(np.diag(cov));v,u=np.linalg.eigh(cov)
        floor=max(float(np.trace(cov)/len(cov)),1e-8)*s.floor
        return center,u,np.maximum(v,floor)
    def white(self,x,stats):
        center,u,v=stats;w=((x-center)@u)/np.sqrt(v)
        return w,np.column_stack([np.sqrt(np.mean(w*w,axis=1)),np.max(np.abs(w),axis=1)])
    def transform(self,f):
        x=(f[list(COLUMNS)].to_numpy(float)[:,self.active]-self.median)/self.scale
        w,summary=self.white(self.qt.transform(x),self.rank_stats);raw,rs=self.white(x,self.raw_stats)
        if self.setting.quantile_view=='hybrid':z=np.column_stack([w,raw,np.log1p(summary),np.log1p(rs)])
        elif self.setting.quantile_view=='summary':z=np.column_stack([summary,np.log1p(rs)])
        else:z=np.column_stack([w,np.log1p(summary),np.log1p(rs)])
        return np.clip(z,-1e6,1e6).astype(np.float32)

class Detector(ParentDetector):
    def make_encoder(self,setting):return Representation(setting)
    def record(self):return {**super().record(),'normal_quantile_mapping':True,'no_additional_anomaly_classifier':True,'ADBench_specific_not_service_model':True}
