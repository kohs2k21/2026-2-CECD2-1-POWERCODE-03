"""Exact finite context-quantile calibration; labels select hyperparameters.

The source estimator and all numeric cutoff VALUES are normal-only. Selecting
one of 21 normal quantiles in each observable stratum is explicitly adaptive
benchmark research, not an untouched external validation. Full-case replay is
required after this probe and no original release is overwritten.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import write,read
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.stratified_calibration import Groups,StratifiedReference,counts_at_thresholds
from shared_model_v7.exact_policy import solve_policy
from shared_model_v7.search_calibration import Q as ORIGINAL_Q,metrics
from shared_model_v6.probe import bank
from shared_model_v6.features import Evidence
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector

Q=np.unique(np.r_[0.,.1,.25,.5,.65,.75,ORIGINAL_Q])


def sources(a,models,d,cal,directories):
    j=ALGORITHMS.index(a);model=models[a]
    # Read immutable completed release arrays; do not fit any benchmark row.
    raw={}
    for t in TASKS:
        old=np.load(BASE/(t+'_scores.npz'));raw[t]=old['scores'][old['case_index']%25==0,j]
    yield 'release_v6',model,model.score(cal),raw
    # All completed fitted representations can be reconsidered without refit.
    for directory in directories:
        if directory=='multimodal_selected':
            choice=read(OUT/'multimodal_search'/(a+'_selection.json'))['chosen'];paths=[ROOT/choice['source']]
        else:paths=sorted((OUT/directory).glob(a+'_candidate_*.joblib'),key=lambda p:int(p.stem.rsplit('_',1)[1]))
        for path in paths:
            number=path.stem.rsplit('_',1)[1];cache=path.parent/(a+'_raw_'+number+'.joblib')
            if not cache.exists():continue
            p=joblib.load(cache);yield directory+'/'+path.stem,joblib.load(path),p['normal'],p['probe']


def run(algorithms,modes,limit=None,directories=('feature_search','weighted_search','metric_search'),output='stratified_search',prefer_roles=False):
    freeze();dest=OUT/output;dest.mkdir(exist_ok=True);d=bank()
    dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration']
    encoder=Evidence(dev['rank_history']).fit(dev['train']);models=joblib.load(BASE/'models.joblib')['models']
    reference=joblib.load(OUT/'neighbourhood_1.joblib')['reference']if any(m in('point_joint','joint_severity')for m in modes)else None
    for a in algorithms:
        j=ALGORITHMS.index(a);bc=[]
        for t in TASKS:
            old=np.load(BASE/(t+'_scores.npz'));mask=old['case_index']%25==0
            r=curves(old['y'][mask],old['scores'][mask,j],[models[a].threshold])[0]
            bc.append([r[k]for k in('tn','fp','fn','tp')])
        bc=np.array(bc);records=[];best=None;partitions={}
        for mode in modes:
            groups=Groups(encoder,mode,reference).fit(cal);signal,nkeys=groups.values(cal)
            partitions[mode]=(groups,signal,nkeys,sorted(set(nkeys)),{t:groups.values(d[t]['frame'])[1]for t in TASKS})
        for number,(tag,base,normal,raw)in enumerate(sources(a,models,d,cal,directories)):
            if limit is not None and number>=limit:break
            for mode in modes:
                groups,signal,nkeys,keys,ids=partitions[mode]
                for shrink in(0.,40.):
                    counts=np.zeros((5,len(keys),len(Q),4),int)
                    for g,key in enumerate(keys):
                        mask=nkeys==key;g0=int(key.split('|')[0]);local=normal[mask];parent=normal[signal==g0]
                        if len(parent)<40:parent=normal
                        cases=cal.loc[mask,'case_id'].nunique()
                        if len(local)<20 or cases<3:local=parent;weight=0.
                        else:weight=len(local)/(len(local)+shrink)if shrink else 1.
                        cuts=weight*np.quantile(local,Q,method='higher')+(1-weight)*np.quantile(parent,Q,method='higher')
                        for ti,t in enumerate(TASKS):
                            mask=ids[t]==key;counts[ti,g]=counts_at_thresholds(d[t]['y'][mask],raw[t][mask],cuts)
                    # Unseen context groups use a fixed normal-family P99 cutoff.
                    fixed=np.zeros((5,4),int);fallback=StratifiedReference.fit(cal,normal,groups,[],[],shrink)
                    for ti,t in enumerate(TASKS):
                        mask=~np.isin(ids[t],keys);pred=fallback.apply(d[t]['frame'],raw[t])>1;y=d[t]['y']
                        fixed[ti]=[np.sum(mask&~y&~pred),np.sum(mask&~y&pred),np.sum(mask&y&~pred),np.sum(mask&y&pred)]
                    # A dummy fixed group encodes the invariant fallback counts.
                    allcounts=np.concatenate([counts,np.repeat(fixed[:,None,None,:],len(Q),axis=2)],axis=1)
                    from shared_model_v7.role_priority import TARGETS
                    preferred=list(TASKS).index(TARGETS[a])if prefer_roles else None
                    chosen,solver=solve_policy(allcounts,bc,Q,preferred_task=preferred)
                    rec={'source':tag,'mode':mode,'shrinkage_minutes':shrink,'normal_groups':len(keys),'normal_quantile_candidates':Q.tolist(),'exact_feasibility':solver,'probe_targets_met':chosen is not None}
                    if chosen is not None:
                        cm=allcounts[:,np.arange(len(keys)+1),chosen].sum(axis=1);tm=metrics(*cm.T);pm=metrics(*cm.sum(axis=0))
                        rec.update({'pooled':{k:float(v)for k,v in pm.items()},'tasks':{t:{k:float(v[i])for k,v in tm.items()}for i,t in enumerate(TASKS)},'levels':dict(zip(keys,Q[chosen[:-1]].tolist()))})
                        rank=float(pm['f1'])if preferred is None else float(tm['f1'][preferred]);ref=StratifiedReference.fit(cal,normal,groups,keys,Q[chosen[:-1]],shrink)
                        if best is None or rank>best[0]:
                            best=(rank,rec);atomic_dump(CalibratedDetector(base,ref,1.),dest/(a+'_selected.joblib'))
                    records.append(rec);write(dest/(a+'_records.json'),records)
                    write(dest/(a+'_selection.json'),{'chosen':None if best is None else best[1],'candidates':len(records),'research_probe_labels_used':True,'external_blind_test':False})
                    print('STRATA',a,tag,mode,shrink,'groups',len(keys),'pass',chosen is not None,'best_F1',None if best is None else round(best[0]*100,2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--modes',nargs='+',default=['adapter','duration','hour']);p.add_argument('--limit',type=int)
    p.add_argument('--directories',nargs='+',default=['feature_search','weighted_search','metric_search']);p.add_argument('--output',default='stratified_search');p.add_argument('--prefer-roles',action='store_true');args=p.parse_args()
    run(args.algorithms,args.modes,args.limit,args.directories,args.output,args.prefer_roles)
