"""Normal-only, shrinkage-conditioned score calibration; NOT experts/conformal guarantees."""
from dataclasses import dataclass

import numpy as np


def _surprise(values, reference):
    n=len(reference)
    upper=(n-np.searchsorted(reference,values,side="left")+1)/(n+1)
    return -np.log(upper)


@dataclass
class NormalReference:
    mode: str
    alpha: float
    global_scores: np.ndarray
    tables: dict
    support: dict
    kind: str = "cdf"

    @staticmethod
    def keys(frame,mode):
        adapter=frame.adapterType.to_numpy(str)
        if mode=="global":return np.repeat("GLOBAL",len(frame))
        if mode=="adapter":return adapter
        if mode=="adapter_phase":return np.array([a+"|"+p for a,p in zip(adapter,frame.phase.to_numpy(str))])
        gate=frame.traffic_assessed.to_numpy(int)+2*frame.latency_assessed.to_numpy(int)
        if mode=="availability":return gate.astype(str)
        if mode=="adapter_availability":return np.array([a+"|"+str(g) for a,g in zip(adapter,gate)])
        raise ValueError(mode)

    @classmethod
    def fit(cls,frame,scores,mode,alpha,kind="cdf"):
        keys=cls.keys(frame,mode);tables={};support={}
        for key in np.unique(keys):
            mask=keys==key;n=frame.loc[mask,"case_id"].nunique()
            if mask.sum()>=80 and n>=5:
                tables[key]=np.sort(scores[mask]);support[key]=int(n)
        return cls(mode,alpha,np.sort(scores),tables,support,kind)

    def apply(self,frame,scores):
        if self.kind=="cdf":global_score=_surprise(scores,self.global_scores)
        else:
            center=np.median(self.global_scores);scale=max(float(np.quantile(self.global_scores,.95)-center),1e-5)
            global_score=(scores-center)/scale
        result=global_score.copy();keys=self.keys(frame,self.mode)
        for key in np.unique(keys):
            ref=self.tables.get(key)
            if ref is None:continue
            mask=keys==key
            weight=self.alpha*min(self.support[key]/20,1.)
            if self.kind=="cdf":local=_surprise(scores[mask],ref)
            else:
                center=np.median(ref);scale=max(float(np.quantile(ref,.95)-center),1e-5)
                local=(scores[mask]-center)/scale
            result[mask]=weight*local+(1-weight)*global_score[mask]
        return result


class CalibratedDetector:
    """One underlying fitted estimator, plus normal-score lookup statistics."""
    def __init__(self,base,reference,threshold):
        self.base,self.reference,self.threshold=base,reference,float(threshold)
        for attr in ("algorithm","setting","encoder","columns","estimator","fit_rows","fit_seconds"):
            setattr(self,attr,getattr(base,attr))

    def score(self,frame):return self.reference.apply(frame,self.base.score(frame))
    def predict(self,frame):
        score=self.score(frame)
        return score,score>self.threshold
    def record(self):
        return {**self.base.record(),"threshold":self.threshold,"calibration_mode":self.reference.mode,
                "calibration_kind":self.reference.kind,"local_weight":self.reference.alpha,
                "calibration_groups":len(self.reference.tables),"adapter_experts":False,
                "formal_coverage_guarantee":False,"unknown_group_fallback":"global normal-score reference"}
