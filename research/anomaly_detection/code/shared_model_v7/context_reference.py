"""Normal score calibration conditioned on the other observed evidence.

An IF averages paths: a rare delay plus ordinary traffic can score lower than
a moderate delay plus sparse traffic. Compare the score to normals with
similar OTHER evidence instead of silently treating the sum as monotonic.
This is lookup calibration, no extra fault classifier or coverage guarantee.
"""
from dataclasses import dataclass
import numpy as np
from scipy.spatial import cKDTree
from shared_model_target.calibration import _surprise


@dataclass
class ContextReference:
    encoder:object
    neighbors:int
    alpha:float
    global_scores:np.ndarray
    tables:dict
    kind:str='cdf'
    mode:str='observed_family_other_evidence_neighbourhood'

    @classmethod
    def fit(cls,f,normal,encoder,neighbors=128,alpha=.5,kind='cdf'):
        obj=cls(encoder,neighbors,alpha,np.sort(normal),{},kind)
        peaks=encoder.transform(f,'peaks');family=np.argmax(peaks,axis=1);cases=f.case_id.to_numpy(str)
        rng=np.random.default_rng(42)
        for g in range(5):
            ids=np.flatnonzero(family==g)
            if len(ids)<80 or len(np.unique(cases[ids]))<5:continue
            chosen=[]
            for case in np.unique(cases[ids]):
                sub=ids[cases[ids]==case];chosen.extend(rng.choice(sub,min(2,len(sub)),replace=False))
            chosen=np.sort(chosen);other=[j for j in range(5)if j!=g];x=peaks[chosen][:,other]
            center=np.median(x,axis=0);scale=np.maximum(np.quantile(x,.95,axis=0)-center,.15)
            z=(x-center)/scale
            obj.tables[g]={'tree':cKDTree(z),'scores':normal[chosen],'cases':cases[chosen],'center':center,'scale':scale,
                'other':other,'family_scores':np.sort(normal[ids])}
        return obj
    def scale(self,values,reference):
        if self.kind=='cdf':return _surprise(values,reference)
        median=float(np.median(reference));width=max(float(np.quantile(reference,.95)-median),1e-5)
        return (values-median)/width
    def apply(self,f,scores):
        peak=self.encoder.transform(f,'peaks');family=np.argmax(peak,axis=1);cases=f.case_id.to_numpy(str)
        out=self.scale(scores,self.global_scores)
        for g,table in self.tables.items():
            ids=np.flatnonzero(family==g)
            if not len(ids):continue
            family_scores=table['family_scores'];fallback=self.scale(scores[ids],family_scores)
            out[ids]=fallback
            for begin in range(0,len(ids),512):
                batch=ids[begin:begin+512];x=(peak[batch][:,table['other']]-table['center'])/table['scale']
                k=min(self.neighbors+4,len(table['scores']));dist,near=table['tree'].query(x,k=k)
                if k==1:dist=dist[:,None];near=near[:,None]
                for j,i in enumerate(batch):
                    keep=table['cases'][near[j]]!=cases[i];chosen=near[j][keep][:self.neighbors];dd=dist[j][keep][:self.neighbors]
                    if len(chosen)<20:continue
                    local=table['scores'][chosen];weight=self.alpha/(1+float(np.median(dd)))
                    out[i]=weight*self.scale(np.array([scores[i]]),np.sort(local))[0]+(1-weight)*fallback[begin+j]
        if not np.isfinite(out).all():raise ValueError('Nonfinite conditioned score')
        return out
