"""Concordant causal evidence: require independent references to agree softly."""
from dataclasses import dataclass
import copy
import numpy as np
from shared_model_v7.weighted import Encoder as ParentEncoder,Setting as ParentSetting,EXTRA
from shared_model_v6.features import RAW
from shared_model_v6.models import Detector as BaseDetector

@dataclass(frozen=True)
class Setting(ParentSetting):
    agreement:float=.5
    extra_gain:float=.25

class Encoder(ParentEncoder):
    def peaks(self,f):
        raw=self.core.transform(f,'raw');s=self.setting
        get=lambda c:raw[:,RAW.index(c)]
        point=np.maximum(get('current_source_upper'),get('current_source_delay'))
        last=get('seq_last_shift');rank=get('rank_upper_memory')
        delay=(1-s.agreement)*np.maximum(last,rank)+s.agreement*np.sqrt(last*rank)
        trend=np.sqrt(get('rank_duration_cusum')*np.maximum(get('seq_delay_slope'),get('seq_delay_ewma')))
        extra=np.maximum((np.nan_to_num(f[list(EXTRA)].to_numpy(float),nan=0.)-self.center)/self.scale,0.)
        own,prior=extra[:,0],extra[:,1]
        burst=(1-s.agreement)*np.maximum(own,prior)+s.agreement*np.sqrt(own*prior)
        gap=np.sqrt(get('gap_expected_prefix')*get('traffic_zero_logprob'))
        values=np.column_stack([point*s.point_gain,delay,trend,burst*s.burst_gain,gap])
        if s.transform=='asinh':values=np.arcsinh(values)
        return values

class Geometry:
    """Frozen normal transformation only, deliberately excludes the old estimator."""
    def __init__(self,base):
        self.transformer=copy.deepcopy(base)
        pending=[self.transformer];seen=set()
        while pending:
            obj=pending.pop()
            if id(obj)in seen:continue
            seen.add(id(obj))
            if hasattr(obj,'estimator'):obj.estimator=None
            for key in('parent','base'):
                child=getattr(obj,key,None)
                if child is not None:pending.append(child)
        self.columns=tuple(base.columns);self.encoder=copy.deepcopy(base.encoder);self.view=base.setting.view
        self.transform_kind=base.setting.transform
        for key in ('median','scale','weights','center','mapping','rotation'):
            if hasattr(base,key):setattr(self,key,np.array(getattr(base,key),copy=True))
        self.names=list(getattr(base,'transformed_columns',self.columns))
    def values(self,f):
        frozen=getattr(self,'transformer',None)
        if frozen is not None:
            if hasattr(frozen,'values'):return frozen.values(f)
            x=frozen.encoded(f)if hasattr(frozen,'encoded')else frozen.encoder.transform(f)
            if hasattr(frozen,'transform_values'):x=frozen.transform_values(x)
            if hasattr(frozen,'project'):x=frozen.project(x)
            return np.asarray(x,dtype=np.float32)
        if self.encoder is None:
            x=f[list(self.columns)].to_numpy(float);x=np.where(np.isnan(x),self.median,x);x=(x-self.median)/self.scale
            if self.transform_kind=='asinh':x=np.arcsinh(x)
            x=np.clip(x,-100,100)
        else:
            import inspect
            if len(inspect.signature(self.encoder.transform).parameters)==1:x=self.encoder.transform(f)
            else:x=self.encoder.transform(f,self.view)
        x=((x-self.center)@self.mapping)*self.weights
        if hasattr(self,'rotation'):x=x@self.rotation
        return x.astype(np.float32)

class HybridEncoder(Encoder):
    def __init__(self,history,setting,parent):super().__init__(history,setting);self.geometry=Geometry(parent)
    def fit(self,f):
        super().fit(f);extra=self.peaks(f);self.e_center=np.median(extra,axis=0)
        self.e_scale=np.maximum(np.quantile(extra,.95,axis=0)-self.e_center,.25);return self
    def transform(self,f,view):
        if view!='hybrid':return super().transform(f,view)
        f=self.core.derive(f);x=self.geometry.values(f);e=np.arcsinh((self.peaks(f)-self.e_center)/self.e_scale)*self.setting.extra_gain
        return np.column_stack([x,e])
    def names(self,view):
        if view!='hybrid':return super().names(view)
        return [*['preserved_'+c for c in self.geometry.names],*['added_'+c for c in super().names('peaks')]]

class Detector(BaseDetector):
    def __init__(self,setting,history,parent=None):
        super().__init__(setting,history)
        self.encoder=Encoder(history,setting)if parent is None else HybridEncoder(history,setting,parent)
    def fit(self,f):
        super().fit(f);self.columns=tuple(dict.fromkeys([*(self.encoder.geometry.columns if hasattr(self.encoder,'geometry')else()),*RAW,*EXTRA]));return self
    def record(self):return {**super().record(),'soft_reference_agreement':True,'old_estimator_embedded':False}
