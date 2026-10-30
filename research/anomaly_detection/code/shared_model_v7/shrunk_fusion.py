"""Normal Adapter statistics with FEWER label-selected cutoff parameters.

Empirical-Bayes-style shrinkage combines an Adapter's normal score reference
with its observable signal/history parent. The weighting uses only normal
row/case support, not task truth. After this normal-only transformation, only
20 shared signal/history cutoffs (+ original-core unknown fallback) may be
selected. This contrasts with selecting 440 independent Adapter cutoffs.
No additional anomaly estimator is fitted; no formal coverage is claimed.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_target.calibration import _surprise
from shared_model_v7.continuous_fusion import CountContinuousReference
from shared_model_v7.adapter_calibration import Codec
from shared_model_v7.dense_policy import keys


class Reference(CountContinuousReference):
    @classmethod
    def fit(cls,normal,raw,encoder,point_weight,burst_weight,preserve_other=False,model_scaling='cdf'):
        self=super().fit(normal,raw,encoder,point_weight,burst_weight,preserve_other,model_scaling)
        score=CountContinuousReference.apply(self,normal,raw)
        self.context_codec=Codec.fit(encoder,normal);group=self.context_codec.values(normal)
        self.parent_refs={};self.local_refs={};self.global_ref=np.sort(score)
        for g in np.unique(group//self.context_codec.stride):
            mask=group//self.context_codec.stride==g;self.parent_refs[int(g)]=np.sort(score[mask])
        for g in np.unique(group):
            mask=group==g;cases=normal.loc[mask,'case_id'].nunique()
            if mask.sum()>=12 and cases>=3:
                weight=min(float(mask.sum())/100,float(cases)/8,1.)
                self.local_refs[int(g)]=(np.sort(score[mask]),weight)
        return self
    @staticmethod
    def rarity(value,ref):
        width=max(float(np.quantile(ref,.95)-np.median(ref)),1e-4)
        return _surprise(value,ref)+np.log1p(np.maximum(value-ref[-1],0.)/width)
    def apply(self,f,raw):
        score=super().apply(f,raw);group=self.context_codec.values(f);out=np.zeros(len(f))
        for g in np.unique(group):
            mask=group==g;parent=self.parent_refs.get(int(g)//self.context_codec.stride,self.global_ref)
            value=self.rarity(score[mask],parent)
            if int(g)in self.local_refs:
                ref,weight=self.local_refs[int(g)];value=(1-weight)*value+weight*self.rarity(score[mask],ref)
            out[mask]=value
        return out


@dataclass
class SharedCutoffCodec:
    encoder:object
    known:frozenset
    stride:int=1
    n_groups:int=21
    @classmethod
    def fit(cls,encoder,normal):return cls(encoder,frozenset(normal.adapterType.astype(str).unique()))
    def values(self,f):
        group=keys(self.encoder,f)
        return np.where(f.adapterType.astype(str).isin(self.known).to_numpy(),group,20)
