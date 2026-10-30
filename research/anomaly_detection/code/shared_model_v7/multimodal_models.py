from dataclasses import dataclass
import numpy as np
from shared_model_v6.models import Detector as Parent,Setting as ParentSetting
from shared_model_v6.features import Evidence,RAW
from shared_model_v7.multimodal import Reference,EXTRA


@dataclass(frozen=True)
class Setting(ParentSetting):
    work_weight:float=1.
    normal_neighbours:int=3
    mode:str='replace'


class Encoder:
    def __init__(self,history,s):self.core=Evidence(history);self.setting=s;self.fit_frame=None
    def fit(self,f):
        self.core.fit(f);self.reference=Reference(self.setting.work_weight,self.setting.normal_neighbours).fit(f)
        self.fit_frame=f;self.fit_extra=self.reference.transform(f,exclude_case=True)
        self.center=np.median(self.fit_extra,axis=0);self.scale=np.maximum(np.quantile(self.fit_extra,.95,axis=0)-self.center,.15)
        return self
    def transform(self,f,view):
        peaks=self.core.transform(f,'peaks').copy()
        raw=self.fit_extra if f is self.fit_frame else self.reference.transform(f)
        extra=np.maximum((raw-self.center)/self.scale,0.);point=extra.max(axis=1)
        if self.setting.mode=='replace':peaks[:,0]=point
        elif self.setting.mode=='joint':peaks[:,0]=np.sqrt(point*peaks[:,0])
        if view=='peaks':return peaks
        if self.setting.view=='radial':out=peaks.max(axis=1,keepdims=True)
        elif self.setting.mode=='append':out=np.column_stack([peaks,extra])
        else:out=peaks
        if self.setting.transform=='asinh':out=np.arcsinh(out)
        return out
    def names(self,view):
        if view=='radial':return ['normal_context_joint_max']
        return ['point','delay','trend','burst','gap']+([*EXTRA]if self.setting.mode=='append'else[])
    def release_training_cache(self):self.fit_frame=None;self.fit_extra=None


class Detector(Parent):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(history,s)
    def fit(self,f):
        super().fit(f);self.encoder.release_training_cache()
        self.columns=tuple(dict.fromkeys([*RAW,'log_max_duration_1m','log_mean_count_5m','adapterType','processId']))
        return self
    def record(self):return {**super().record(),'multimodal_normal_duration_feature':True,'reference_training_leave_one_case_out':True}
