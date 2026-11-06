"""Context-dependent normal score tables, never separate Adapter estimators."""
from dataclasses import dataclass
import numpy as np
from shared_model_target.calibration import NormalReference


@dataclass
class ContextReference:
    base:object
    mode:str
    inner:object
    @property
    def kind(self):return self.inner.kind
    @property
    def alpha(self):return self.inner.alpha
    @property
    def tables(self):return self.inner.tables
    def keys(self,frame):
        availability=(frame.traffic_assessed.to_numpy(int)+2*frame.latency_assessed.to_numpy(int)).astype(str)
        if self.mode=='adapter_availability':
            return np.char.add(np.char.add(frame.adapterType.to_numpy(str),'|'),availability)
        if self.mode=='availability_phase':
            phase=frame.phase.to_numpy(str)if'phase'in frame else np.repeat('steady',len(frame))
            return np.char.add(np.char.add(availability,'|'),phase)
        x=self.base.transform_values(self.base.encoded(frame)if hasattr(self.base,'encoded')else self.base.encoder.transform(frame))
        if hasattr(self.base,'project'):x=self.base.project(x)
        clusters=self.base.estimator.predict(x).astype(str)
        if self.mode=='cluster':return clusters
        if self.mode=='cluster_availability':return np.char.add(np.char.add(clusters,'|'),availability)
        raise ValueError(self.mode)
    def context(self,frame):
        f=frame[['case_id','adapterType','traffic_assessed','latency_assessed']].copy();f['adapterType']=self.keys(frame);return f
    @classmethod
    def fit(cls,base,frame,scores,mode,alpha,kind):
        self=cls(base,mode,None);self.inner=NormalReference.fit(self.context(frame),scores,'adapter',alpha,kind);return self
    def apply(self,frame,scores):return self.inner.apply(self.context(frame),scores)
