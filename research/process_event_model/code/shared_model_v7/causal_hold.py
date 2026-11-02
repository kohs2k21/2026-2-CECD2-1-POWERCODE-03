"""Past-only high-confidence burst-score persistence; not point adjustment.

Only an observed burst-evidence family and multiple recent arrivals can seed
memory. Its confidence cutoff is fitted from normal model scores. Holds have
fixed maximum length; no event truth/end is used, and full evaluation counts
every false alert AFTER recovery. This is model + temporal alert policy, NOT
a claim about the bare IF/K-means/LOF estimator.
"""
import numpy as np
from shared_model_target.calibration import _surprise
from shared_model_v7.remap_models import cached
from shared_model_v7.evidence_fusion import Encoder as Parent,_NEAR_CACHE
from shared_model_v7.count_filter import Context as CountContext

_SCORES={}
_HOLDS={}


class CachedPrimary:
    def __init__(self,core):
        self.core=core
        for key in('algorithm','setting','encoder','columns','estimator','fit_rows','fit_seconds','threshold'):setattr(self,key,getattr(core,key))
    def score(self,f):return cached(_SCORES,(id(self.core),id(f)),f,lambda:self.core.score(f))
    def record(self):return self.core.record()


def held_scores(f,raw,family,normal_burst_scores,seed_cutoff,length,refractory=0):
    counts=f.native_execution_arrivals.to_numpy(float);out=np.zeros(len(f));normal=np.sort(normal_burst_scores)
    for ids in CountContext.groups(f):
        recent=[];seeds=[];cooldown_until=-1;active_until=-1;active_value=0.
        for age,i in enumerate(ids):
            if not np.isfinite(counts[i]):recent=[];seeds=[];cooldown_until=-1;active_until=-1;active_value=0.;continue
            recent.append(counts[i]);recent=recent[-3:]
            if refractory:
                current=0.
                if family[i]==3 and sum(recent)>1 and raw[i]>seed_cutoff:
                    current=float(_surprise(np.array([raw[i]]),normal)[0])
                    if age>=cooldown_until:
                        active_value=current;active_until=age+length;cooldown_until=age+max(length,refractory)
                    elif age<active_until:active_value=max(active_value,current)
                # Current strong evidence is NEVER silenced by the refractory
                # period; only automatic extension of old memory is bounded.
                out[i]=max(current,active_value if age<active_until else 0.)
                continue
            if family[i]==3 and sum(recent)>1 and raw[i]>seed_cutoff:
                seeds.append((age,float(_surprise(np.array([raw[i]]),normal)[0])))
            seeds=[(at,value)for at,value in seeds if age-at<length]
            if seeds:out[i]=max(value for at,value in seeds)
    return out


class Encoder(Parent):
    def __init__(self,core,reference,history,primary,normal_frame,normal_scores,seed_quantile,length,refractory=0):
        super().__init__(core,reference,history,{'seed_normal_quantile':seed_quantile,'maximum_hold_minutes':length,
            'refractory_minutes':refractory,'repeat_seeds_extend_memory':not bool(refractory)})
        self.primary=primary;self.length=length;self.refractory=refractory
        family=np.argmax(core.transform(normal_frame,'peaks'),axis=1);ref=normal_scores[family==3]
        if len(ref)<40:ref=normal_scores
        self.seed_reference=np.sort(ref);self.seed_cutoff=float(np.quantile(ref,seed_quantile,method='higher'))
    def auxiliary(self,f):
        near=cached(_NEAR_CACHE,(id(self.reference),id(f)),f,lambda:self.reference.transform(f)[:,0])
        point=np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*.5)
        score=self.primary.score(f);family=np.argmax(self.core.transform(f,'peaks'),axis=1)
        refractory=getattr(self,'refractory',0);key=(id(self.primary),self.seed_cutoff,self.length,refractory,id(f))
        state=cached(_HOLDS,key,f,lambda:held_scores(f,score,family,self.seed_reference,self.seed_cutoff,self.length,refractory))
        return point,state
