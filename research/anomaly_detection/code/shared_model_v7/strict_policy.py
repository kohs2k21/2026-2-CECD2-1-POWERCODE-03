"""Research selection with hard FPR non-increase and an explicit role objective.

All constraints apply to every supplied task/split, not only pooled metrics.
The preferred task is a selection objective, never an inference input. Labels
are reused development truth, so this is NOT an external blind evaluation.
"""
import numpy as np
from scipy.optimize import milp, Bounds, LinearConstraint
from shared_model_v7.search_calibration import metrics


def solve(counts, baseline, minimum_recall=.57, preferred=(), time_limit=60,fp_fraction=None,reference_rates=None,metric_buffer=0.):
    offsets=np.r_[0,np.cumsum([c.shape[1] for c in counts])]
    flat=np.concatenate(counts,axis=1); nt,n,_=flat.shape
    tp=flat[:,:,3].astype(float); fp=flat[:,:,1].astype(float)
    positive=baseline[:,2:].sum(axis=1); negative=baseline[:,:2].sum(axis=1)
    minimum=np.broadcast_to(np.asarray(minimum_recall,float),(nt,))
    fp_budget=baseline[:,1].astype(float) if fp_fraction is None else baseline[:,1]*np.broadcast_to(np.asarray(fp_fraction,float),(nt,))
    rows=[];lo=[];hi=[]
    for a,b in zip(offsets[:-1],offsets[1:]):
        row=np.zeros(n);row[a:b]=1;rows.append(row);lo.append(1);hi.append(1)
    def maintain(t,f,b,p,recall,index=None):
        tn,oldfp,fn,oldtp=map(float,b)
        precision=oldtp/max(oldtp+oldfp,1); f1=2*oldtp/max(2*oldtp+oldfp+fn,1)
        accuracy=(tn+oldtp)/max(tn+oldfp+fn+oldtp,1)
        if reference_rates is not None and index is not None:
            ref=reference_rates[index]
            precision=min(max(precision,ref['precision'])+metric_buffer,1.)
            f1=min(max(f1,ref['f1'])+metric_buffer,1.)
            accuracy=min(max(accuracy,ref['accuracy'])+metric_buffer,1.)
        rows.extend([t-f,(1-precision)*t-precision*f,(2-f1)*t-f1*f,f])
        lo.extend([accuracy*(tn+oldfp+p)-(tn+oldfp),0,f1*p,-np.inf])
        budget=oldfp if index is None else fp_budget[index]
        hi.extend([np.inf,np.inf,np.inf,budget])
        if recall:rows.append(t);lo.append(oldtp);hi.append(np.inf)
    for i in range(nt):
        rows.append(tp[i]);lo.append(minimum[i]*positive[i]);hi.append(np.inf)
        maintain(tp[i],fp[i],baseline[i],positive[i],False,i)
    maintain(tp.sum(axis=0),fp.sum(axis=0),baseline.sum(axis=0),positive.sum(),True)
    constraint=LinearConstraint(np.array(rows),np.array(lo),np.array(hi))
    def run(c):
        return milp(c=c,integrality=np.ones(n),bounds=Bounds(0,1),constraints=constraint,
                    options={'time_limit':time_limit})
    result=run(-(tp.sum(axis=0)-fp.sum(axis=0)+.001*tp.sum(axis=0)))
    receipt={'variables':n,'status':int(result.status),'message':str(result.message),
             'FPR_nonincrease_each_task_split':True,'minimum_recall':minimum.tolist(),
             'fp_budgets':fp_budget.tolist(),'metric_buffer':metric_buffer,
             'role_task_indices':list(preferred),'labels_used_for_selection':True}
    if result.x is None:return None,receipt
    def outcomes(res):
        choices=np.array([np.argmax(res.x[a:b]) for a,b in zip(offsets[:-1],offsets[1:])])
        return choices,sum(c[:,j] for c,j in zip(counts,choices))
    if preferred:
        ix=list(preferred); pt=tp[ix].sum(axis=0);pf=fp[ix].sum(axis=0);pos=positive[ix].sum()
        for unused in range(6):
            choices,actual=outcomes(result); b=actual[ix].sum(axis=0)
            old=2*b[3]/max(pos+b[3]+b[1],1)
            candidate=run(-((2-old)*pt-old*pf))
            if candidate.x is None:break
            unused_choices,newactual=outcomes(candidate);b=newactual[ix].sum(axis=0)
            new=2*b[3]/max(pos+b[3]+b[1],1)
            if new<old-1e-12:break
            result=candidate
            if new-old<1e-8:break
    choices,actual=outcomes(result)
    tm=metrics(*actual.T);bm=metrics(*baseline.T)
    pool=metrics(*actual.sum(axis=0));oldpool=metrics(*baseline.sum(axis=0))
    valid=np.all(tm['recall']>=minimum)&np.all(actual[:,1]<=fp_budget+1e-12)
    valid &= all(np.all(tm[k]>=bm[k]-1e-12) for k in ('accuracy','precision','f1'))
    valid &= all(pool[k]>=oldpool[k]-1e-12 for k in ('accuracy','precision','recall','f1'))
    if reference_rates is not None:
        valid &= all(tm[k][i]>=min(max(bm[k][i],reference_rates[i][k])+metric_buffer,1.)-1e-12
                     for i in range(nt)for k in ('accuracy','precision','f1'))
    receipt.update({'counts':actual.tolist(),'independently_valid':bool(valid),
                    'final_solver_status':int(result.status)})
    return (choices if valid else None),receipt
