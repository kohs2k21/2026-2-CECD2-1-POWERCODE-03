"""Independent normal reference and estimator folds; no self-distance leakage."""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.models import Detector as Parent,Setting as ParentSetting
from shared_model_v6.features import Evidence,RAW

# Set only by the research entry point, and copied into each fitted encoder.
# Serialized predictors carry the numeric lookup, not this mutable variable.
REFERENCE=None


@dataclass(frozen=True)
class Setting(ParentSetting):
    joint_weight:float=1.
    mode:str='replace'


class Encoder:
    def __init__(self,history,s):self.core=Evidence(history);self.setting=s
    def joint(self,f):
        nearest=self.reference.transform(f)[:,0]
        # Both inputs are continuous. A model threshold, NOT this minimum,
        # decides the alarm. Workload-conditioned normal distances can flag
        # holes between duration modes; upper evidence avoids flagging a
        # short normal mode simply because historical coverage is sparse.
        upper=np.maximum(f.current_source_upper.to_numpy(float),0.)
        return np.minimum(nearest,upper*self.setting.joint_weight)
    def fit(self,f):
        if REFERENCE is None:raise ValueError('A separate normal-only reference must be supplied')
        self.reference=REFERENCE;self.core.fit(f);x=self.joint(f)
        self.center=float(np.median(x));self.scale=max(float(np.quantile(x,.95)-self.center),.15)
        return self
    def transform(self,f,view):
        peak=self.core.transform(f,'peaks').copy();joint=np.maximum((self.joint(f)-self.center)/self.scale,0.)
        if self.setting.mode=='replace':peak[:,0]=joint
        elif self.setting.mode=='max':peak[:,0]=np.maximum(peak[:,0],joint)
        if view=='peaks':return peak
        if view=='radial':out=peak.max(axis=1,keepdims=True)
        elif self.setting.mode=='append':out=np.column_stack([peak,joint])
        else:out=peak
        if self.setting.transform=='asinh':out=np.arcsinh(out)
        return out
    def names(self,view):
        if view=='radial':return ['normal_context_joint_max']
        return ['point','delay','trend','burst','gap']+(['joint_normal_neighbourhood']if self.setting.mode=='append'else[])


class Detector(Parent):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(history,s)
    def fit(self,f):
        super().fit(f);self.columns=tuple(dict.fromkeys([*RAW,'log_max_duration_1m','log_mean_count_5m','adapterType','processId']))
        return self
    def record(self):return {**super().record(),'independent_normal_reference_estimator_calibration_folds':True,'normal_neighbourhood_feature_not_an_extra_detector':True}
