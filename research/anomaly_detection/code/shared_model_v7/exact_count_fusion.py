"""Exact trailing-count evidence in explicit statistical score fusion."""
import numpy as np
from shared_model_v7.evidence_fusion import Encoder as Parent,_NEAR_CACHE
from shared_model_v7.exact_count_context import Context
from shared_model_v7.remap_models import cached

_EXACT={}


class Encoder(Parent):
    def __init__(self,core,reference,history,view='3m'):
        super().__init__(core,reference,history,{'ungated_exact_count_evidence':view,'independent_increment_assumption_not_operating_guarantee':True})
        self.exact=Context(history);self.view=view
    def auxiliary(self,f):
        near=cached(_NEAR_CACHE,(id(self.reference),id(f)),f,lambda:self.reference.transform(f)[:,0])
        point=np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*.5)
        x=cached(_EXACT,(self.signature,id(f)),f,lambda:self.exact.transform(f).to_numpy())
        if self.view=='3m':state=x[:,1]
        elif self.view=='short':state=x[:,:2].max(axis=1)
        elif self.view=='agreement':state=np.minimum(x[:,:2].max(axis=1),x[:,2:].max(axis=1))
        else:raise ValueError('Unknown exact count view')
        return point,state
