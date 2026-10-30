"""Normal raw-score references conditional on observed completion volume.

One estimator remains. Maxima/distances vary with how many completions were
observed in the minute; the reference distribution accounts for that nuisance.
No phase, case duration, task identity, event boundary or label is a key.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_target.calibration import _surprise

@dataclass
class CompletionReference:
    encoder:object
    alpha:float
    global_scores:np.ndarray
    tables:dict
    support:dict
    finer:bool=False
    kind:str='cdf'
    mode:str='observed_family_completion_volume'

    def keys(self,f):
        family=np.argmax(self.encoder.transform(f,'peaks'),axis=1)
        n=np.nan_to_num(f.completion_count_1m.to_numpy(float),nan=0.)
        if self.finer:bins=np.minimum(np.floor(np.log2(n+1)),3).astype(int)
        else:bins=np.minimum(n,2).astype(int)
        return np.array([str(g)+'|'+str(b)for g,b in zip(family,bins)])
    @classmethod
    def fit(cls,cal,normal,encoder,alpha=1.,finer=False,kind='cdf'):
        obj=cls(encoder,alpha,np.sort(normal),{},{},finer,kind);keys=obj.keys(cal)
        for key in np.unique(keys):
            mask=keys==key;cases=cal.loc[mask,'case_id'].nunique()
            if mask.sum()>=40 and cases>=5:
                obj.tables[key]=np.sort(normal[mask]);obj.support[key]=int(cases)
        return obj
    def scale(self,s,ref):
        if self.kind=='cdf':return _surprise(s,ref)
        center=np.median(ref);spread=max(float(np.quantile(ref,.95)-center),1e-5)
        return (s-center)/spread
    def apply(self,f,scores):
        out=self.scale(scores,self.global_scores);keys=self.keys(f)
        for key,ref in self.tables.items():
            mask=keys==key;w=self.alpha*min(self.support[key]/20,1.)
            out[mask]=w*self.scale(scores[mask],ref)+(1-w)*out[mask]
        return out
