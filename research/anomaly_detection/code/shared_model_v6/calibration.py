"""Normal score calibration conditional on observed signal family, not experts.

One underlying IF/KM/LOF remains. No incident label or task identifier chooses
the family: it is the largest normal-scaled observed evidence coordinate.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.features import Evidence,RAW
from shared_model_target.calibration import _surprise

@dataclass
class SignalReference:
    encoder:object
    mode:str
    alpha:float
    kind:str
    global_scores:np.ndarray
    tables:dict
    support:dict

    def keys(self,f):
        values=self.encoder.transform(f,'peaks');dominant=np.argmax(values,axis=1).astype(str)
        gate=(f.traffic_assessed.to_numpy(int)+2*f.latency_assessed.to_numpy(int)).astype(str)
        if self.mode=='dominant':return dominant
        if self.mode=='availability_dominant':return np.char.add(np.char.add(gate,'|'),dominant)
        if self.mode=='phase_dominant':return np.array([g+'|'+p+'|'+d for g,p,d in zip(gate,f.phase.to_numpy(str),dominant)])
        raise ValueError(self.mode)
    @classmethod
    def fit(cls,cal,scores,encoder,mode='availability_dominant',alpha=1.,kind='cdf'):
        obj=cls(encoder,mode,alpha,kind,np.sort(scores),{},{});keys=obj.keys(cal)
        for key in np.unique(keys):
            mask=keys==key;n=cal.loc[mask,'case_id'].nunique()
            if mask.sum()>=80 and n>=5:obj.tables[key]=np.sort(scores[mask]);obj.support[key]=int(n)
        return obj
    def scale(self,s,ref):
        if self.kind=='cdf':return _surprise(s,ref)
        center=float(np.median(ref));scale=max(float(np.quantile(ref,.95)-center),1e-5)
        return (s-center)/scale
    def apply(self,f,s):
        result=self.scale(s,self.global_scores);keys=self.keys(f)
        for key,ref in self.tables.items():
            mask=keys==key;weight=self.alpha*min(self.support[key]/20,1.)
            result[mask]=weight*self.scale(s[mask],ref)+(1-weight)*result[mask]
        return result
