"""One K-means estimator with explicit aggregate-delay inputs.

Unlike the previous minimal rank view, this candidate exposes recent mean
delay, delayed-execution persistence and cohort delay magnitude to the SAME
clustering estimator. It keeps point/trend/volume/gap evidence for coverage.
All scaling, centers and radii are fitted on normal training rows only.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.models import Detector as Parent,Setting as ParentSetting
from shared_model_v6.features import Evidence,RAW
from shared_model_v7.role_context import COHORT


@dataclass(frozen=True)
class Setting(ParentSetting):
    view:str='cohort'
    transform:str='asinh'
    cohort_gain:float=1.
    algorithm:str='kmeans'


class Encoder:
    def __init__(self,history,setting):self.core=Evidence(history);self.setting=setting
    def fit(self,f):
        self.core.fit(f)
        x=np.nan_to_num(f[list(COHORT)].to_numpy(float),nan=0.)
        self.center=np.median(x,axis=0);self.scale=np.maximum(np.quantile(x,.95,axis=0)-self.center,.15)
        return self
    def transform(self,f,view):
        f=self.core.derive(f)
        if view=='peaks':return self.core.transform(f,'peaks')
        if view!='cohort':raise ValueError('Unknown cohort model view')
        e=self.core.transform(f,'peaks')
        x=np.maximum((np.nan_to_num(f[list(COHORT)].to_numpy(float),nan=0.)-self.center)/self.scale,0.)
        return np.arcsinh(np.column_stack([e,x*self.setting.cohort_gain]))
    def names(self,view):return [*self.core.names('peaks'),*COHORT]


class Detector(Parent):
    def __init__(self,setting,history):super().__init__(setting,history);self.encoder=Encoder(history,setting)
    def fit(self,f):
        super().fit(f);self.columns=tuple(dict.fromkeys([*RAW,*COHORT]));return self
    def record(self):
        return {**super().record(),'aggregate_delay_normal_training':True,
                'actual_encoder_transform':'normal_median_P95_scale_then_asinh',
                'one_estimator_not_old_model_ensemble':True,'normal_only_fallback_threshold':True}
