from dataclasses import dataclass
import numpy as np
from shared_model_v6.models import Setting as ParentSetting,Detector as Base
from shared_model_v7.weighted import Encoder as Parent
from shared_model_v7.semantic import References

@dataclass(frozen=True)
class Setting(ParentSetting):
    point_gain:float=1.
    burst_gain:float=1.
    accumulated_burst:bool=False
    semantic_weight:float=.5

class Encoder(Parent):
    def fit(self,f):
        super().fit(f);self.references=References().fit(f);x=self.references.transform(f);self.sc=np.median(x,axis=0);self.ss=np.maximum(np.quantile(x,.95,axis=0)-self.sc,.25);return self
    def transform(self,f,view):
        old=self.peaks(f);new=np.maximum((self.references.transform(f)-self.sc)/self.ss,0.)
        agreement=np.sqrt(old[:,0]*new[:,0]);old[:,0]=(1-self.setting.semantic_weight)*old[:,0]+self.setting.semantic_weight*agreement
        if view=='peaks':return old
        if view=='two_axis':return np.column_stack([old[:,0],old[:,1:].max(axis=1),new[:,1:]])
        if view=='compact':return np.column_stack([old,new])
        ordered=np.sort(old,axis=1);return np.column_stack([ordered[:,-1],ordered[:,-2],np.sqrt(np.mean(old*old,axis=1)),new])
    def names(self,view):
        if view=='peaks':return ['point','delay','trend','burst','gap']
        if view=='two_axis':return ['point','other_max','semantic_tail','semantic_margin']
        if view=='compact':return ['point','delay','trend','burst','gap','semantic_z','semantic_tail','semantic_margin']
        return ['joint_max','joint_second','joint_radius','semantic_z','semantic_tail','semantic_margin']

class Detector(Base):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(history,s)
    def fit(self,f):super().fit(f);self.columns=tuple(dict.fromkeys([*self.columns,'log_max_duration_1m','log_mean_count_5m','completion_count_1m']));return self
    def record(self):return {**super().record(),'Process_family_normal_statistics':True,'learned_identifier_features':False}
