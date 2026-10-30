"""Normal conditional neighbourhood AND past-only multiscale change features.

Refits one anomaly estimator; unlike transport, all models learn the changed
numeric feature geometry. Source IDs select normal statistics only. No task
identity, future endpoint or evaluation label enters the representation.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.features import Evidence,RAW
from shared_model_v6.models import Setting as ParentSetting,Detector as Parent
from shared_model_v7.agreement import Geometry
from shared_model_v7.remap_models import cached,_NEAR_CACHE,_SCAN_CACHE
from shared_model_v7.change_scan import Context

REFERENCE=None
PARENTS=None


@dataclass(frozen=True)
class Setting(ParentSetting):
    point_ratio:float=.5
    scan_ratio:float=4.
    point_gain:float=1.
    scan_gain:float=1.
    hybrid:bool=False


class Encoder:
    def __init__(self,s,history):
        self.setting=s;self.core=Evidence(history);self.scan=Context(history)
        if s.hybrid:self.geometry=Geometry(PARENTS[s.algorithm].base)
    def extras(self,f,training=False):
        key=(id(self.reference),id(f),'leave_case'if training else'serving')
        near=cached(_NEAR_CACHE,key,f,lambda:self.reference.transform(f,exclude_case=training)[:,0])
        signature=tuple(sorted(self.scan.history.items()));scan=cached(_SCAN_CACHE,(signature,id(f)),f,lambda:self.scan.transform(f))
        point=np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*self.setting.point_ratio)
        burst=np.minimum(scan.count_scan_peak.to_numpy(),scan.count_scan_3m.to_numpy()*self.setting.scan_ratio)
        return np.column_stack([point,burst])
    def fit(self,f):
        if REFERENCE is None:raise ValueError('Normal-only reference required')
        self.reference=REFERENCE;self.core.fit(f);self.fit_frame=f;self.fit_extra=self.extras(f,training=True)
        self.center=np.median(self.fit_extra,axis=0);self.scale=np.maximum(np.quantile(self.fit_extra,.95,axis=0)-self.center,.15)
        return self
    def transform(self,f,view):
        raw=self.fit_extra if f is getattr(self,'fit_frame',None)else self.extras(f)
        extra=np.maximum((raw-self.center)/self.scale,0.)*np.array([self.setting.point_gain,self.setting.scan_gain])
        peaks=self.core.transform(f,'peaks').copy();peaks[:,0]=extra[:,0];peaks[:,3]=extra[:,1]
        if view=='peaks':return peaks
        if view=='radial':out=peaks.max(axis=1,keepdims=True)
        elif view=='compact':
            out=self.core.transform(f,'compact').copy();out[:,0:2]=extra[:,0:1];out[:,6:8]=extra[:,1:2]
            out[:,-1]=np.sqrt(peaks[:,0]*peaks[:,1])
        elif view=='hybrid':out=np.column_stack([self.geometry.values(f),extra])
        else:out=peaks
        if self.setting.transform=='asinh':out=np.arcsinh(out)
        return out
    def names(self,view):
        if view=='radial':return ['joint_max']
        if view=='compact':return self.core.names(view)
        if view=='hybrid':return [*self.geometry.names,'joint_point','joint_burst']
        return ['point','delay','trend','burst','gap']
    def release(self):self.fit_frame=None;self.fit_extra=None


class Detector(Parent):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(s,history)
    def fit(self,f):
        super().fit(f);self.encoder.release()
        self.columns=tuple(dict.fromkeys([*RAW,'log_max_duration_1m','log_mean_count_5m','native_execution_arrivals','adapterType','processId']))
        return self
    def record(self):return {**super().record(),'conditional_normal_neighbourhood_and_causal_scan':True,'old_estimator_embedded':False}
