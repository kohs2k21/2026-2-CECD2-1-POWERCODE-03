"""Vectorized calibration candidates; never uses task IDs during inference."""
import numpy as np
from shared_model_v7.search_calibration import Q,metrics
from shared_model_v6.search import curves
from shared_model_v7.exact_policy import solve_policy

def optimize(normal,nkeys,cal,raw,groups,d,baseline_counts,draws=10000,seed=42,preferred_task=None):
    from shared_model_v7.protocol import TASKS
    thresholds=[]
    for g in range(5):
        mask=nkeys==g;nc=cal.loc[mask,'case_id'].nunique();ref=normal[mask]if mask.sum()>=80 and nc>=5 else normal
        thresholds.append(np.quantile(ref,Q,method='higher'))
    thresholds=np.array(thresholds);counts=np.zeros((5,5,len(Q),4),int)
    for ti,t in enumerate(TASKS):
        for g in range(5):
            mask=groups[t]==g
            if mask.any():
                rows=curves(d[t]['y'][mask],raw[t][mask],thresholds[g])
                counts[ti,g]=[[r[k]for k in ('tn','fp','fn','tp')]for r in rows]
    bm=metrics(*baseline_counts.sum(axis=0));bt=metrics(*baseline_counts.T)
    rng=np.random.default_rng(seed)
    choices=np.vstack([np.repeat(np.arange(len(Q))[:,None],5,axis=1),rng.integers(0,len(Q),size=(draws,5))])
    preferred_index=None if preferred_task is None else list(TASKS).index(preferred_task)
    exact,solver=solve_policy(counts,baseline_counts,Q,minimum_recall=.55,preferred_task=preferred_index)
    if exact is not None:choices=np.vstack([choices,exact])
    buffered=None
    if exact is not None:
        buffered,_=solve_policy(counts,baseline_counts,Q,minimum_recall=.57,preferred_task=preferred_index)
        if buffered is not None:choices=np.vstack([choices,buffered])
    allcounts=sum(counts[:,g,choices[:,g],:].transpose(1,0,2)for g in range(5))
    tm=metrics(*allcounts.transpose(2,0,1));pm=metrics(*allcounts.sum(axis=1).T)
    minr=tm['recall'].min(axis=1);maxfp=tm['normal_false_positive_rate'].max(axis=1)
    nonreg=np.ones(len(choices),bool)
    for k in ('accuracy','precision','recall','f1'):nonreg&=pm[k]>=bm[k]-1e-12
    per_task_nonreg=np.ones(len(choices),bool)
    for k in ('accuracy','precision','f1'):per_task_nonreg&=np.all(tm[k]>=bt[k][None,:]-1e-12,axis=1)
    required=(minr>=.55)&(maxfp<.05)&nonreg&per_task_nonreg
    feasible=(minr>=.57)&(maxfp<.048)&nonreg&per_task_nonreg
    stable=np.all(tm['f1']>=bt['f1'][None,:]-1e-12,axis=1)
    # Failed candidates must be ranked against the CONFIRMED task-wise scope,
    # not against pooled improvements which can conceal a task regression.
    gap=np.minimum(tm['recall']-.55,0.).sum(axis=1)
    gap+=sum(np.minimum(tm[k]-bt[k][None,:],0.).sum(axis=1)for k in ('accuracy','precision','f1'))
    gap+=sum(np.minimum(pm[k]-bm[k],0.)for k in ('accuracy','precision','recall','f1'))
    gap-=np.maximum(tm['normal_false_positive_rate']-.05,0.).sum(axis=1)
    role_f1=pm['f1']if preferred_index is None else tm['f1'][:,preferred_index]
    ids=sorted(range(len(choices)),key=lambda i:(bool(required[i]),bool(feasible[i]),float(gap[i]),bool(nonreg[i]),bool(stable[i]),float(role_f1[i]),float(pm['f1'][i]),-float(maxfp[i])),reverse=True)
    eligible=[i for i in ids if maxfp[i]<.05]
    i=eligible[0]if eligible else ids[0]
    def record(i):return {'levels':[float(q)for q in Q[choices[i]]],
        'normal_cutoffs':thresholds[np.arange(5),choices[i]].tolist(),
        'pooled':{k:float(v[i])for k,v in pm.items()},'tasks':{t:{k:float(v[i,ti])for k,v in tm.items()}for ti,t in enumerate(TASKS)},
        'minimum_task_recall':float(minr[i]),'maximum_task_FPR':float(maxfp[i]),'maintains_pooled':bool(nonreg[i]),
        'maintains_each_task_F1':bool(stable[i]),'maintains_each_task_accuracy_precision_F1':bool(per_task_nonreg[i]),'probe_targets_met':bool(required[i]),'probe_buffered_targets_met':bool(feasible[i]),'constraint_gap':float(gap[i]),'exact_feasibility':solver,
        'preferred_task':preferred_task,'preferred_task_F1':float(role_f1[i])}
    return record(i),[record(z)for z in eligible[:5]],int(feasible.sum())
