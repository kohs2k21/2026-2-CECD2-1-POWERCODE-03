"""Controlled normal-only score-geometry diagnosis; adaptive probe selection."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.context_reference import ContextReference
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_v7.optimize import optimize
from shared_model_v7.role_priority import TARGETS
from shared_model_v6.features import Evidence
from shared_model_v6.probe import bank
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector


def candidates(a,models,cal,d):
    j=ALGORITHMS.index(a);raw={}
    for t in TASKS:
        z=np.load(BASE/(t+'_scores.npz'));raw[t]=z['scores'][z['case_index']%25==0,j]
    yield 'release_v6',models[a],models[a].score(cal),raw
    # These completed candidates all use the original disjoint normal
    # score-calibration cases. The independent-fit variants are not mixed in.
    for directory in('multimodal_search','joint_new_search','role_search'):
        selection=OUT/directory/(a+'_selection.json')
        if not selection.exists():continue
        chosen=read(selection)['chosen'];path=ROOT/chosen['source'];number=path.stem.rsplit('_',1)[1]
        p=joblib.load(path.parent/(a+'_raw_'+number+'.joblib'))
        yield directory+'/'+path.name,joblib.load(path),p['normal'],p['probe']


def run(algorithms):
    freeze();dest=OUT/'context_reference_search';dest.mkdir(exist_ok=True);dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    cal=dev['normal_calibration'];d=bank();models=joblib.load(BASE/'models.joblib')['models'];fallback=Evidence(dev['rank_history']).fit(dev['train'])
    nkeys=np.argmax(fallback.transform(cal,'peaks'),axis=1);groups={t:np.argmax(fallback.transform(p['frame'],'peaks'),axis=1)for t,p in d.items()}
    for a in algorithms:
        j=ALGORITHMS.index(a);bc=[]
        for t in TASKS:
            z=np.load(BASE/(t+'_scores.npz'));mask=z['case_index']%25==0
            row=curves(z['y'][mask],z['scores'][mask,j],[models[a].threshold])[0];bc.append([row[k]for k in('tn','fp','fn','tp')])
        records=[];best=None
        for tag,base,normal,raw in candidates(a,models,cal,d):
            for k in(64,256):
                for alpha in(.5,1.):
                    for kind in('cdf','robust'):
                        parent=ContextReference.fit(cal,normal,fallback,k,alpha,kind)
                        ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
                        row,top,n=optimize(ns,nkeys,cal,ss,groups,d,np.array(bc),draws=1000,preferred_task=TARGETS[a])
                        row.update({'algorithm':a,'source':tag,'neighbors':k,'local_weight':alpha,'kind':kind,'normal_only_context_calibration':True})
                        rank=(row['probe_targets_met'],row['constraint_gap'],row['preferred_task_F1'],row['pooled']['f1'])
                        if best is None or rank>best[0]:
                            best=(rank,row);ref=FamilyQuantileReference.fit(cal,normal,parent,fallback,row['levels']);atomic_dump(CalibratedDetector(base,ref,1.),dest/(a+'_selected.joblib'))
                        records.append(row);write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':best[1],'research_probe_labels_used':True,'external_blind_test':False})
                        print('CONTEXT-REF',a,tag,k,alpha,kind,'minR',round(row['minimum_task_recall']*100,2),'strict_pass',row['probe_targets_met'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
