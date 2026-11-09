"""Backend adapter preserving selected-batch-only monitoring semantics."""
from dataclasses import dataclass,replace
import joblib
import numpy as np
from ..strict_refinement.model import SharedARPoint,RefinedBank,PointReference,score_frame,HORIZONS
from ..strict_refinement.ratio import prepare
from ..strict_refinement.quantile_warp import linear_warp


@dataclass(frozen=True)
class AlignedReference:
    point: PointReference
    source_quantiles: np.ndarray
    target_quantiles: np.ndarray


class AlignedPoint(SharedARPoint):
    def __init__(self,grid,buckets,curves,config,normal_cdf,normal_scale_prior):
        super().__init__(grid,buckets,curves,config)
        self.normal_cdf=np.asarray(normal_cdf,float)
        self.normal_scale_prior=np.asarray(normal_scale_prior,float)
        if self.normal_cdf.shape!=(2,len(self.buckets),len(self.grid)):raise ValueError('Invalid learned normal CDF shape')
        if not np.isfinite(self.normal_cdf).all() or (np.diff(self.normal_cdf,axis=-1)<-1e-12).any():raise ValueError('Normal CDF must be finite and monotonic')
        if self.normal_scale_prior.shape!=(2,) or not np.isfinite(self.normal_scale_prior).all() or (self.normal_scale_prior<=0).any():raise ValueError('Positive normal scale priors required')
        for name in ('scale_alpha','alignment_strength'):
            v=np.asarray(config[name],float)
            if v.shape!=(2,) or not np.isfinite(v).all() or (v<0).any() or (v>1).any():raise ValueError('Two channel strengths inside [0,1] required')
        q=np.asarray(config['quantiles'],float)
        if q.ndim!=1 or len(q)<2 or (np.diff(q)<=0).any() or q[0]<=0 or q[-1]>=1:raise ValueError('Strict interior quantiles required')

    def prepare_reference(self,x,observed,available):
        ref=super().prepare_reference(x,observed,available)
        alpha=np.asarray(self.config['scale_alpha'])
        scale=np.sqrt(alpha*ref.scale**2+(1-alpha)*self.normal_scale_prior**2)
        ref=replace(ref,scale=scale)
        _,residual,_,_=prepare(x,observed,available,.5,.5)
        z=(residual[:,:2]-ref.center)/ref.scale
        source=np.quantile(z,self.config['quantiles'],axis=0).T;target=[]
        for ch in range(2):
            hi=min(len(self.buckets)-1,int(np.searchsorted(self.buckets,ref.rho[ch],side='right')));lo=max(0,hi-1)
            w=(ref.rho[ch]-self.buckets[lo])/max(self.buckets[hi]-self.buckets[lo],1e-10)
            cdf=self.normal_cdf[ch,lo]*(1-w)+self.normal_cdf[ch,hi]*w
            target.append(np.interp(self.config['quantiles'],cdf,self.grid))
        return AlignedReference(ref,source,np.asarray(target))

    def score_arrays(self,reference,x,observed,available,offset_hours=0):
        # Reuse the validated base contract before computing transformed latent
        # residuals. Neither call fits a normal distribution on monitoring data.
        ref=reference.point
        super().score_arrays(ref,x,observed,available,offset_hours)
        x=np.asarray(x,float);hours=offset_hours+np.arange(len(x))
        z=(x[:,:2]-ref.profile[hours%24,:2]
           -(ref.context_hours+hours-ref.midpoint)[:,None]*ref.slope[:2]-ref.center)/ref.scale
        aligned=z.copy()
        for ch,strength in enumerate(self.config['alignment_strength']):
            if strength:
                mapped=linear_warp(z[:,ch],reference.source_quantiles[ch],reference.target_quantiles[ch])
                aligned[:,ch]=(1-strength)*z[:,ch]+strength*mapped
        transformed=x.copy();transformed[:,:2]+=(aligned-z)*ref.scale
        return super().score_arrays(ref,transformed,observed,available,offset_hours)


class AlignedBank(RefinedBank):
    def predict_batch(self,reference,batch,analysis_hours=4):
        result=super().predict_batch(reference,batch,analysis_hours)
        if analysis_hours==1:result['model_type']='normal_AR_reference_quantile_alignment'
        return result

    def record(self):
        result=super().record();result['normal_reference_quantile_alignment']=True
        return result


def load_bank(path):
    bank=joblib.load(path)
    if not isinstance(bank,AlignedBank):raise TypeError('Expected AlignedBank artifact')
    return bank
