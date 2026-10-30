"""All distinct normal cutoffs, not a coarse 21-quantile approximation.

Label-aware Pareto pruning/selection is adaptive research: cutoff VALUES are
normal only, but all 15 task constraints use already-reused development truth.
It is NOT a blind evaluation and finite infeasibility is not impossibility of
all models. Cutoffs with the same task-wise TP vector retain the one with the
fewest FPs (the largest cutoff).
"""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint
from shared_model_v7.history_support import past_observations
from shared_model_v7.stratified_calibration import counts_at_thresholds
from shared_model_v7.search_calibration import metrics


def keys(encoder,f,history=True):
    family=np.argmax(encoder.transform(f,'peaks'),axis=1)
    return family*4+np.digitize(past_observations(f),[1,3,6])if history else family


def tables(normal,nkeys,cal,raw,groups,data,tasks,history=True,legacy_cutoff=None):
    width=4 if history else 1;alltables=[];thresholds=[];centers=[];provenance=[]
    for group in range(5*width):
        mask=nkeys==group;family=group//width;fm=nkeys//width==family
        ref=normal[mask]
        if len(ref)<20 or cal.loc[mask,'case_id'].nunique()<3:ref=normal[fm]
        if not len(ref):ref=normal
        center=float(np.median(ref));spread=max(float(np.quantile(ref,.95)-center),abs(center)*.01,1e-5)
        cuts=np.r_[np.unique(ref),float(np.max(ref))+3*spread]
        if legacy_cutoff is not None:cuts=np.r_[cuts,legacy_cutoff]
        cuts=np.unique(cuts);counts=np.zeros((len(tasks),len(cuts),4),int)
        for ti,t in enumerate(tasks):
            keep=groups[t]==group;counts[ti]=counts_at_thresholds(data[t]['y'][keep],raw[t][keep],cuts)
        # For equal TPs on ALL tasks, a higher threshold only removes FPs.
        best={}
        for i,vector in enumerate(counts[:,:,3].T):best[tuple(vector)]=i
        selected=np.array(sorted(best.values()));alltables.append(counts[:,selected]);thresholds.append(cuts[selected]);centers.append(center)
        provenance.append({'group':group,'calibration_rows':int(mask.sum()),'normal_reference_rows':len(ref),'all_normal_cutoffs':len(cuts),'Pareto_cutoffs':len(selected),
            'normal_robust_upper_fence_included':True,'legacy_normal_cutoff_included':legacy_cutoff is not None})
    return alltables,thresholds,np.array(centers),provenance


def solve(counts,baseline,minimum_recall=.55,time_limit=60):
    sizes=[c.shape[1]for c in counts];offsets=np.r_[0,np.cumsum(sizes)];flat=np.concatenate(counts,axis=1)
    nt,n,_=flat.shape;tp=flat[:,:,3].astype(float);fp=flat[:,:,1].astype(float)
    positive=baseline[:,2:].sum(axis=1);negative=baseline[:,:2].sum(axis=1)
    minimum=np.broadcast_to(np.asarray(minimum_recall,float),(nt,))
    rows=[];lower=[];upper=[]
    for a,b in zip(offsets[:-1],offsets[1:]):
        row=np.zeros(n);row[a:b]=1;rows.append(row);lower.append(1);upper.append(1)
    for i in range(nt):
        rows.extend([tp[i],fp[i]]);lower.extend([minimum[i]*positive[i],-np.inf]);upper.extend([np.inf,np.ceil(.05*negative[i])-1])
    def maintain(tpf,fpf,b,pos,recall):
        tn,oldfp,fn,oldtp=map(float,b);p=oldtp/max(oldtp+oldfp,1);f=2*oldtp/max(2*oldtp+oldfp+fn,1)
        rows.extend([tpf-fpf,(1-p)*tpf-p*fpf,(2-f)*tpf-f*fpf]);lower.extend([oldtp-oldfp,0,f*pos]);upper.extend([np.inf]*3)
        if recall:rows.append(tpf);lower.append(oldtp);upper.append(np.inf)
    for i in range(nt):maintain(tp[i],fp[i],baseline[i],positive[i],False)
    maintain(tp.sum(axis=0),fp.sum(axis=0),baseline.sum(axis=0),positive.sum(),True)
    objective=-(tp.sum(axis=0)-fp.sum(axis=0)+.001*tp.sum(axis=0))
    result=milp(c=objective,integrality=np.ones(n),bounds=Bounds(0,1),constraints=LinearConstraint(np.array(rows),np.array(lower),np.array(upper)),options={'time_limit':time_limit})
    receipt={'variables':n,'groups':len(counts),'status':int(result.status),'message':str(result.message),'finite_policy_infeasible':bool(result.status==2),
        'each_task_nonregression_enforced':True,'minimum_task_recall':minimum.tolist()}
    if result.x is None:return None,receipt
    chosen=np.array([np.argmax(result.x[a:b])for a,b in zip(offsets[:-1],offsets[1:])]);actual=sum(c[:,j]for c,j in zip(counts,chosen))
    # Do not trust a solver flag alone: independently check integer outcomes.
    tm=metrics(*actual.T);bm=metrics(*baseline.T);pool=metrics(*actual.sum(axis=0));oldpool=metrics(*baseline.sum(axis=0))
    valid=np.all(tm['recall']>=minimum)&np.all(tm['normal_false_positive_rate']<.05)
    valid&=all(np.all(tm[k]>=bm[k]-1e-12)for k in('accuracy','precision','f1'))
    valid&=all(pool[k]>=oldpool[k]-1e-12 for k in('accuracy','precision','recall','f1'))
    receipt.update({'counts':actual.tolist(),'independent_metric_constraints_valid':bool(valid)})
    return(chosen if valid else None),receipt


@dataclass
class CutReference:
    parent:object
    encoder:object
    cutoffs:np.ndarray
    centers:np.ndarray
    history:bool=True
    alpha:float=1.
    mode:str='exact_normal_cutoff_by_observed_signal_and_history_support'
    kind:str='exact_margin_not_a_probability'
    codec:object=None
    @property
    def tables(self):return dict(enumerate(self.cutoffs))
    def apply(self,f,raw):
        score=self.parent.apply(f,raw)
        codec=getattr(self,'codec',None);group=keys(self.encoder,f,self.history)if codec is None else codec.values(f)
        return 1.+(score-self.cutoffs[group])/np.maximum(np.abs(self.cutoffs[group]-self.centers[group]),1e-5)
