"""Observable-family normal-quantile calibration on ONE frozen estimator.

Family is argmax of numeric evidence, never a ground-truth task. Quantiles
are normal-only lookup statistics. Selected quantile levels are research
hyperparameters, not probabilities/production coverage guarantees.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.features import Evidence

@dataclass
class FamilyQuantileReference:
    parent:object
    encoder:object
    medians:np.ndarray
    cutoffs:np.ndarray
    levels:tuple
    support:dict
    mode:str='normal_quantile_by_observed_family'
    kind:str='normal_quantile_margin'
    alpha:float=1.

    @property
    def tables(self):return {str(i):np.array([self.medians[i],v])for i,v in enumerate(self.cutoffs)}
    def keys(self,f):return np.argmax(self.encoder.transform(f,'peaks'),axis=1)
    def apply(self,f,raw):
        score=self.parent.apply(f,raw);keys=self.keys(f)
        return (score-self.medians[keys])/np.maximum(self.cutoffs[keys]-self.medians[keys],1e-5)

    @classmethod
    def fit(cls,cal,raw,parent,encoder,levels):
        scores=parent.apply(cal,raw);keys=np.argmax(encoder.transform(cal,'peaks'),axis=1)
        med=[];cut=[];support={}
        for i,q in enumerate(levels):
            mask=keys==i;n=cal.loc[mask,'case_id'].nunique()
            ref=scores[mask]if mask.sum()>=80 and n>=5 else scores
            med.append(float(np.median(ref)));cut.append(float(np.quantile(ref,q,method='higher')))
            support[str(i)]={'rows':int(mask.sum()),'cases':int(n),'global_fallback':bool(mask.sum()<80 or n<5)}
        return cls(parent,encoder,np.asarray(med),np.asarray(cut),tuple(levels),support)
