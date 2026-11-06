"""Exact finite normal-quantile policy selection, with all metric constraints.

Research/development labels select FIVE quantile hyperparameters. Scores and
threshold values are fitted only from normal references. No task is used in
serving; this is not a blind evaluation and not a supervised estimator fit.
"""
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint

def solve_policy(counts,baseline_counts,quantiles,minimum_recall=.55,strict_tasks=True,preferred_task=None):
    # counts[task, observable_family, normal_quantile, (tn,fp,fn,tp)]
    nt,ng,nq,_=counts.shape;n=ng*nq;flat=counts.reshape(nt,n,4)
    tp=flat[:,:,3];fp=flat[:,:,1];total_tp=tp.sum(axis=0);total_fp=fp.sum(axis=0)
    totals=baseline_counts.sum(axis=0);normal=baseline_counts[:,:2].sum(axis=1);positive=baseline_counts[:,2:].sum(axis=1)
    rows=[];lower=[];upper=[]
    for g in range(ng):
        row=np.zeros(n);row[g*nq:(g+1)*nq]=1;rows.append(row);lower.append(1);upper.append(1)
    for t in range(nt):
        rows.append(tp[t]);lower.append(minimum_recall*positive[t]);upper.append(np.inf)
        rows.append(fp[t]);lower.append(-np.inf);upper.append(np.ceil(.05*normal[t])-1)
    def nonreg(ctp,cfp,b,pos,retain_recall=True):
        btn,bfp,bfn,btp=map(float,b);precision=btp/max(btp+bfp,1);f1=2*btp/max(2*btp+bfp+bfn,1)
        rows.extend([ctp-cfp,(1-precision)*ctp-precision*cfp,(2-f1)*ctp-f1*cfp])
        lower.extend([btp-bfp,0,f1*pos]);upper.extend([np.inf]*3)
        if retain_recall:rows.append(ctp);lower.append(btp);upper.append(np.inf)
    nonreg(total_tp,total_fp,totals,float(positive.sum()))
    if strict_tasks:
        for t in range(nt):nonreg(tp[t],fp[t],baseline_counts[t],positive[t],False)
    # Maximize correct decisions, then detection count; no model-role bonus.
    c=-(total_tp-total_fp+.001*total_tp)
    result=milp(c=c,integrality=np.ones(n),bounds=Bounds(0,1),constraints=LinearConstraint(np.asarray(rows),np.asarray(lower),np.asarray(upper)),options={'time_limit':30})
    if result.x is None:return None,{'status':int(result.status),'message':str(result.message),'globally_infeasible':bool(result.status==2)}
    # Optional requested specialization is an objective, NEVER a relaxation of
    # the task-wise precision/F1/accuracy/Recall/FPR constraints above.
    if preferred_task is not None:
        if preferred_task not in range(nt):raise ValueError('Preferred task outside fixed scope')
        for unused in range(8):
            chosen=np.argmax(result.x.reshape(ng,nq),axis=1)
            current=counts[:,np.arange(ng),chosen,:].sum(axis=1)[preferred_task]
            oldf=2*current[3]/max(positive[preferred_task]+current[3]+current[1],1)
            objective=-((2-oldf)*tp[preferred_task]-oldf*fp[preferred_task])
            candidate=milp(c=objective,integrality=np.ones(n),bounds=Bounds(0,1),constraints=LinearConstraint(np.asarray(rows),np.asarray(lower),np.asarray(upper)),options={'time_limit':30})
            if candidate.x is None:break
            chosen=np.argmax(candidate.x.reshape(ng,nq),axis=1);actual=counts[:,np.arange(ng),chosen,:].sum(axis=1)[preferred_task]
            newf=2*actual[3]/max(positive[preferred_task]+actual[3]+actual[1],1)
            if newf<oldf-1e-12:break
            result=candidate
            if newf-oldf<=1e-9:break
    choices=np.argmax(result.x.reshape(ng,nq),axis=1)
    actual=counts[:,np.arange(ng),choices,:].sum(axis=1)
    return choices,{'status':int(result.status),'message':str(result.message),'counts':actual.tolist(),'objective':float(result.fun),'strict_per_task_nonregression':strict_tasks,
        'preferred_task_index':preferred_task,'preference_does_not_relax_other_constraints':True}
