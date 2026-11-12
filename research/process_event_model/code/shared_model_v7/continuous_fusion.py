"""Normal-scaled continuous auxiliary tails instead of saturated ECDF ranks.

An ECDF cannot distinguish two observations beyond its largest normal sample.
This candidate retains their magnitude using a normal median/P95 scale. The
result is NOT a calibrated probability or a new hard anomaly rule.
"""
import numpy as np
from shared_model_v7.evidence_fusion import Reference as Parent
from shared_model_target.calibration import _surprise


class Reference(Parent):
    @staticmethod
    def transport(value,normal):
        center=float(np.median(normal));width=max(float(np.quantile(normal,.95)-center),1e-4)
        return 3*np.log1p(np.maximum((value-center)/width,0.))/np.log(2.)
    def point_value(self,value,normal):return self.transport(value,normal)
    def apply(self,f,scores):
        point,state=self.encoder.auxiliary(f);family=np.argmax(self.encoder.transform(f,'peaks'),axis=1)
        out=self.model_score(scores,self.global_scores)
        for g,ref in self.tables.items():
            mask=family==g;out[mask]=self.model_score(scores[mask],ref)
        p=family==0;b=family==3
        out[p]=self.model_weight_point*out[p]+(1-self.model_weight_point)*self.point_value(point[p],self.point_scores)
        out[b]=self.model_weight_burst*out[b]+(1-self.model_weight_burst)*self.transport(state[b],self.state_scores)
        if self.preserve_other_model_scores:out[~(p|b)]=scores[~(p|b)]
        return out


class CountContinuousReference(Reference):
    """Duration rarity remains an ECDF; volume-state magnitude stays continuous."""
    def point_value(self,value,normal):return _surprise(value,normal)
