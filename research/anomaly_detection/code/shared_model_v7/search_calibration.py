"""Recorded research calibration search; no test rows fitted by estimators.

The fixed benchmark has been reused in prior releases. Whole 200-case probe
labels select policies explicitly; full 5,000-case results must be reported
as adaptive research results, never independent blind-test performance.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v6.probe import bank
from shared_model_v6.features import Evidence
from shared_model_v6.search import curves
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_target.calibration import CalibratedDetector

Q=np.array([.80,.85,.875,.90,.925,.95,.96,.97,.975,.98,.9825,.985,.9875,.99,.991,.9925,.994,.995,.996,.9975,.999])

def metrics(tn,fp,fn,tp):
    total=tn+fp+fn+tp
    return {'accuracy':(tn+tp)/np.maximum(total,1),'precision':tp/np.maximum(tp+fp,1),
        'recall':tp/np.maximum(tp+fn,1),'f1':2*tp/np.maximum(2*tp+fp+fn,1),'normal_false_positive_rate':fp/np.maximum(tn+fp,1)}

def run(algorithms,seed=42,draws=24000):
    freeze();dest=OUT/'calibration_search';dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    enc=Evidence(dev['rank_history']).fit(dev['train']);cal=dev['normal_calibration'];calkeys=np.argmax(enc.transform(cal,'peaks'),axis=1)
    groups={t:np.argmax(enc.transform(d[t]['frame'],'peaks'),axis=1)for t in TASKS}
    models=joblib.load(BASE/'models.joblib')['models'];old={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS}
    for j,a in enumerate(ALGORITHMS):
        base=models[a];normal=base.score(cal);thresholds=[]
        for g in range(5):
            mask=calkeys==g;nc=cal.loc[mask,'case_id'].nunique();ref=normal[mask]if mask.sum()>=80 and nc>=5 else normal
            thresholds.append(np.quantile(ref,Q,method='higher'))
        thresholds=np.array(thresholds);counts=np.zeros((5,5,len(Q),4),int);basecounts=[]
        for ti,t in enumerate(TASKS):
            keep=old[t]['case_index']%25==0;y=d[t]['y'];score=old[t]['scores'][keep,j];assert np.array_equal(y,old[t]['y'][keep])
            basecounts.append([curves(y,score,[base.threshold])[0][k]for k in ('tn','fp','fn','tp')])
            for g in range(5):
                mask=groups[t]==g
                if not mask.any():continue
                rr=curves(y[mask],score[mask],thresholds[g])
                counts[ti,g]=np.array([[r[k]for k in ('tn','fp','fn','tp')]for r in rr])
        bm=metrics(*np.array(basecounts).sum(axis=0));bt=metrics(*np.array(basecounts).T)
        rng=np.random.default_rng(seed);choices=rng.integers(0,len(Q),size=(draws,5))
        # Uniform family-level cutoffs and conservative perturbations are explicit controls.
        choices=np.vstack([np.repeat(np.arange(len(Q))[:,None],5,axis=1),choices])
        allcounts=sum(counts[:,g,choices[:,g],:].transpose(1,0,2)for g in range(5))
        tm=metrics(*allcounts.transpose(2,0,1));pooled=metrics(*allcounts.sum(axis=1).T)
        minr=tm['recall'].min(axis=1);maxfp=tm['normal_false_positive_rate'].max(axis=1)
        nonreg=np.ones(len(choices),bool)
        for k in ('accuracy','precision','recall','f1'):nonreg&=pooled[k]>=bm[k]-1e-12
        feasible=(minr>=.57)&(maxfp<.048)&nonreg
        # Buffer in probe, no manufactured algorithm role penalties/rewards.
        taskstable=np.all(tm['f1']>=bt['f1'][None,:]-1e-12,axis=1)
        gap=np.minimum(minr-.55,0.)+sum(np.minimum(pooled[k]-bm[k],0.)for k in ('accuracy','precision','recall','f1'))
        ids=sorted(range(len(choices)),key=lambda i:(bool(feasible[i]),bool(nonreg[i]),minr[i]>=.55,float(gap[i]),bool(taskstable[i]),float(pooled['f1'][i]),-float(maxfp[i])),reverse=True)
        eligible=[i for i in ids if maxfp[i]<.05]
        selected=eligible[0]if eligible else ids[0]
        def record(i):
            return {'algorithm':a,'levels':[float(q)for q in Q[choices[i]]],'normal_cutoffs':thresholds[np.arange(5),choices[i]].tolist(),
                'pooled':{k:float(v[i])for k,v in pooled.items()},'tasks':{t:{k:float(v[i,ti])for k,v in tm.items()}for ti,t in enumerate(TASKS)},
                'minimum_task_recall':float(minr[i]),'maximum_task_FPR':float(maxfp[i]),'maintains_pooled':bool(nonreg[i]),
                'maintains_each_task_F1':bool(taskstable[i]),'probe_buffered_targets_met':bool(feasible[i])}
        row=record(selected);ref=FamilyQuantileReference.fit(cal,base.base.score(cal),base.reference,enc,row['levels']);model=CalibratedDetector(base.base,ref,1.)
        joblib.dump(model,dest/(a+'_selected.joblib'),compress=3)
        write(dest/(a+'_selection.json'),{'chosen':row,'top20':[record(i)for i in eligible[:20]],'baseline_probe':{k:float(v)for k,v in bm.items()},
            'configurations':len(choices),'feasible':int(feasible.sum()),'all_choices_sha_seed':seed,
            'research_probe_labels_used':True,'external_blind_test':False,'no_estimator_fit':True})
        np.savez_compressed(dest/(a+'_search_arrays.npz'),choices=choices,normal_quantiles=Q,counts=allcounts,normal_cutoffs=thresholds)
        print('FAMILY CAL',a,'feasible',int(feasible.sum()),'minimumR',round(row['minimum_task_recall']*100,2),'P/R/F1',[round(row['pooled'][k]*100,2)for k in ('precision','recall','f1')],
            'maxFP',round(row['maximum_task_FPR']*100,2),'levels',row['levels'],'pooledmaintain',row['maintains_pooled'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--draws',type=int,default=24000);args=p.parse_args();run(args.algorithms,draws=args.draws)
