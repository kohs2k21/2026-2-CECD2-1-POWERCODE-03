"""Preserve the old numerical geometry, append normal-tail evidence, refit ONE estimator."""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.features import Evidence,RAW
from shared_model_v6.models import Setting as BaseSetting,Detector as Base

@dataclass(frozen=True)
class Setting(BaseSetting):
    evidence_gain:float=.25
    evidence_view:str='radial_pair'

class FrozenGeometry:
    """Only normal numeric transform parameters: contains NO old estimator."""
    def __init__(self,parent):
        self.columns=tuple(parent.columns)
        for key in ('median','scale','weights','center','mapping','rotation'):setattr(self,key,np.array(getattr(parent,key),copy=True))
        self.transform_kind=parent.setting.transform
    def values(self,f):
        x=f[list(self.columns)].to_numpy(float);x=np.where(np.isnan(x),self.median,x);x=(x-self.median)/self.scale
        if self.transform_kind=='asinh':x=np.arcsinh(x)
        x=np.clip(x,-100,100)
        return ((((x-self.center)@self.mapping)*self.weights)@self.rotation).astype(np.float32)

class HybridEncoder:
    def __init__(self,history,setting,parent):
        self.geometry=FrozenGeometry(parent);self.evidence=Evidence(history,tail=True);self.setting=setting
    def fit(self,f):
        self.evidence.fit(f);e=self.evidence.transform(f,self.setting.evidence_view)
        self.extra_center=np.median(e,axis=0);q10,q90=np.quantile(e,[.1,.9],axis=0);self.extra_scale=np.maximum(q90-q10,.5);return self
    def names(self,view):return [*self.geometry.columns,*['added_'+c for c in self.evidence.names(self.setting.evidence_view)]]
    def transform(self,f,view):
        f=self.evidence.derive(f);x=self.geometry.values(f);e=self.evidence.transform(f,self.setting.evidence_view)
        extra=(e-self.extra_center)/self.extra_scale*self.setting.evidence_gain
        return np.column_stack([x,extra])

class Detector(Base):
    def __init__(self,setting,history,parent):
        super().__init__(setting,history);self.encoder=HybridEncoder(history,setting,parent)
    def fit(self,f):
        super().fit(f);self.columns=tuple(dict.fromkeys([*self.encoder.geometry.columns,*RAW]));return self
    def record(self):return {**super().record(),'old_geometry_retained':True,'old_estimator_retained_inside_encoder':False,'new_normal_tail_features_appended':True}
