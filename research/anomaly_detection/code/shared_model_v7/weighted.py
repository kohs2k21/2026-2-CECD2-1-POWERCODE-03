"""Retain v6 stable signals while repairing point compression/traffic memory."""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.features import Evidence,RAW
from shared_model_v6.models import Setting as ParentSetting,Detector as ParentDetector

EXTRA=('seq_burst_8m','arrival_dev_5m')

@dataclass(frozen=True)
class Setting(ParentSetting):
    point_gain:float=1.
    burst_gain:float=1.
    accumulated_burst:bool=False

class Encoder:
    def __init__(self,history,setting):self.core=Evidence(history,tail=setting.tail);self.setting=setting
    def fit(self,f):
        self.core.fit(f);x=np.nan_to_num(f[list(EXTRA)].to_numpy(float),nan=0.);self.center=np.median(x,axis=0)
        self.scale=np.maximum(np.quantile(x,.95,axis=0)-self.center,.15);return self
    def peaks(self,f):
        x=self.core.transform(f,'peaks').copy();s=self.setting
        if s.accumulated_burst:
            more=np.maximum((np.nan_to_num(f[list(EXTRA)].to_numpy(float),nan=0.)-self.center)/self.scale,0.)
            x[:,3]=np.maximum(x[:,3],more.max(axis=1))
        x[:,0]*=s.point_gain;x[:,3]*=s.burst_gain
        if s.transform=='asinh':x=np.arcsinh(x)
        return x
    def transform(self,f,view):
        x=self.peaks(f)
        if view=='peaks':return x
        if view=='two_axis':return np.column_stack([x[:,0],x[:,1:].max(axis=1)])
        if view=='three_axis':return np.column_stack([x[:,0],x[:,1:3].max(axis=1),x[:,3:].max(axis=1)])
        if view=='radial':return x.max(axis=1,keepdims=True)
        if view=='radial_pair':
            ranked=np.sort(x,axis=1);return np.column_stack([ranked[:,-1],ranked[:,-2],np.sqrt(np.mean(x*x,axis=1))])
        raise ValueError(view)
    def names(self,view):
        return {'peaks':['point','delay','trend','burst','gap'],'two_axis':['point','other_max'],
            'three_axis':['point','delay_max','traffic_max'],'radial':['joint_max'],'radial_pair':['joint_max','joint_second','joint_radius']}[view]

class Detector(ParentDetector):
    def __init__(self,setting,history):
        super().__init__(setting,history);self.encoder=Encoder(history,setting)
    def fit(self,f):
        super().fit(f);self.columns=tuple(dict.fromkeys([*RAW,*EXTRA]));return self
    def record(self):return {**super().record(),'point_identity_preserved':self.setting.view in('two_axis','three_axis','peaks')}
