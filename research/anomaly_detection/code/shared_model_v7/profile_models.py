from dataclasses import dataclass
import numpy as np
import pandas as pd
from shared_model_v6.models import Setting as ParentSetting,Detector as Base
from shared_model_v6.features import Evidence,RAW
from shared_model_v4.matched import Profile,MATCHED

@dataclass(frozen=True)
class Setting(ParentSetting):
    profile_mode:str='joint'
    central_trim:bool=False

class Encoder:
    def __init__(self,history,s):self.core=Evidence(history);self.setting=s
    def fit(self,f):
        self.core.fit(f)
        normal=f
        if self.setting.central_trim:
            normal=f.copy();ok=normal.latency_assessed.to_numpy(bool)&np.isfinite(normal.duration_p95_ms.to_numpy(float))
            group=normal.groupby(['adapterType','processId'],sort=False).duration_p95_ms
            low=group.transform(lambda z:z.quantile(.05));high=group.transform(lambda z:z.quantile(.75))
            normal=normal.loc[(~ok)|(normal.duration_p95_ms.ge(low)&normal.duration_p95_ms.le(high))].reset_index(drop=True)
        self.profile=Profile().fit(normal);derived=self.profile.transform(f);x=derived[list(MATCHED[:6])].to_numpy(float)
        self.center=np.median(x,axis=0);self.scale=np.maximum(np.quantile(x,.95,axis=0)-self.center,.15);return self
    def transform(self,f,view):
        peaks=self.core.transform(f,'peaks')
        raw=self.profile.transform(f)[list(MATCHED[:6])].to_numpy(float)
        extra=np.maximum((raw-self.center)/self.scale,0.);extra=np.minimum(extra,100.)
        point=np.maximum(extra[:,0],extra[:,2]);delay=np.maximum(extra[:,4],peaks[:,1]);trend=peaks[:,2]
        if self.setting.profile_mode=='joint':point=np.sqrt(peaks[:,0]*point)
        elif self.setting.profile_mode=='max':point=np.maximum(peaks[:,0],point)
        if view=='peaks':return np.column_stack([point,delay,trend,peaks[:,3],peaks[:,4]])
        if self.setting.profile_mode=='append':out=np.column_stack([peaks,extra])
        else:out=np.column_stack([point,delay,trend,peaks[:,3],peaks[:,4]])
        if self.setting.transform=='asinh':out=np.arcsinh(out)
        return out
    def names(self,view):
        return ['point','delay','trend','burst','gap']+(['profile_'+c for c in MATCHED[:6]]if self.setting.profile_mode=='append'else[])

class Detector(Base):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(history,s)
    def fit(self,f):super().fit(f);self.columns=tuple(dict.fromkeys([*RAW,'duration_p50_ms','duration_p95_ms','log_mean_count_5m']));return self
    def record(self):return {**super().record(),'normal_source_coverage_expanded':True,'Process_statistics_not_expert_estimators':True}
