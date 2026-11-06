"""A normal-statistical fusion may be declined in an observed stratum.

The SAME estimator is used either way. This is not an Adapter expert model
or a second anomaly learner. A fixed per-observed-stratum calibration choice
can retain the original estimator score, instead of forcing auxiliary fusion
where it increases FPs. Unknown/unobserved strata retain the frozen v6 score
and threshold exactly (v6 for unchanged cores, normal-only calibration for
declared alternative cores). Research truth selects choices; NOT a blind test.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v7.adapter_calibration import tables as parent_tables


def tables(normal_fused,normal_core,nkeys,cal,fused,core,groups,data,tasks,codec,legacy):
    fc,ft,fm,fp=parent_tables(normal_fused,nkeys,cal,fused,groups,data,tasks,codec,legacy)
    cc,ct,cm,cp=parent_tables(normal_core,nkeys,cal,core,groups,data,tasks,codec,legacy,True,True)
    counts=[];cuts=[];centers=[];modes=[]
    for i in range(codec.n_groups):
        # No research observations: preserve the original model, not an
        # unsupported new conditional threshold/fusion decision.
        if not fp[i]['observed_in_probe']:
            counts.append(cc[i]);cuts.append(ct[i]);centers.append(np.full(len(ct[i]),cm[i]));modes.append(np.ones(len(ct[i]),bool))
        else:
            counts.append(np.concatenate([fc[i],cc[i]],axis=1));cuts.append(np.r_[ft[i],ct[i]])
            centers.append(np.r_[np.full(len(ft[i]),fm[i]),np.full(len(ct[i]),cm[i])])
            modes.append(np.r_[np.zeros(len(ft[i]),bool),np.ones(len(ct[i]),bool)])
        fp[i]['original_estimator_score_option']=True
    return counts,cuts,centers,modes,fp


@dataclass
class Reference:
    parent:object
    encoder:object
    cutoffs:np.ndarray
    centers:np.ndarray
    original_score:np.ndarray
    codec:object
    alpha:float=1.
    mode:str='observed_stratum_choice_same_estimator_or_explicit_statistical_fusion'
    kind:str='exact_margin_not_fault_probability'
    @property
    def tables(self):return dict(enumerate(self.cutoffs))
    def apply(self,f,raw):
        group=self.codec.values(f);score=self.parent.apply(f,raw)
        mask=self.original_score[group];score[mask]=raw[mask]
        return 1+(score-self.cutoffs[group])/np.maximum(np.abs(self.cutoffs[group]-self.centers[group]),1e-5)
