"""Normal-only estimator fit, transparently adaptive minimum-Recall research."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from dataclasses import replace
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.optimize import optimize
from shared_model_v7.models import Detector,Setting
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_v7.search_frozen import IdentityReference
from shared_model_v6.probe import bank
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector

def settings(a):
    for view in ('peaks','compact','raw'):
        for trans in ('linear','asinh'):
            yield Setting(a,view=view,transform=trans,samples=64,neighbors=600)
    if a=='if':
        for n in (16,32,128,256):yield Setting(a,view='compact',samples=n)
        for v in ('peaks','compact'):yield Setting(a,view=v,samples=64,max_features=.5,trees=600)
        for view in ('peaks','compact','radial_pair'):yield Setting(a,view=view,tail=True,samples=64)
    elif a=='kmeans':
        for k in (2,4,16,32):yield Setting(a,view='compact',clusters=k)
        for v in ('peaks','compact','raw'):yield Setting(a,view=v,white=True)
        for v in ('peaks','compact'):yield Setting(a,view=v,tail=True)
    else:
        for n in (50,100,200,1000):yield Setting(a,view='compact',neighbors=n)
        for v in ('peaks','compact'):yield Setting(a,view=v,white=True,neighbors=600)
        for v in ('peaks','compact','raw'):yield Setting(a,view=v,tail=True,neighbors=600)

def run(algorithms,draws=12000,output=None,development=None,prefer_roles=False):
    freeze();dest=OUT/'feature_search'if output is None else Path(output);dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')if development is None else development;cal=dev['normal_calibration']
    old={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS};models=joblib.load(BASE/'models.joblib')['models']
    for a in algorithms:
        j=ALGORITHMS.index(a)
        bc=np.array([[curves(old[t]['y'][old[t]['case_index']%25==0],old[t]['scores'][old[t]['case_index']%25==0,j],[models[a].threshold])[0][k]for k in ('tn','fp','fn','tp')]for t in TASKS]);best=None;records=[]
        for i,setting in enumerate(settings(a)):
            cp=dest/(a+'_candidate_'+str(i)+'.joblib');cache=dest/(a+'_raw_'+str(i)+'.joblib')
            if cp.exists()and cache.exists():base=joblib.load(cp);ss=joblib.load(cache);normal=ss['normal'];raw=ss['probe']
            else:
                base=Detector(setting,dev['rank_history']).fit(dev['train']);normal=base.score(cal);raw={t:base.score(d[t]['frame'])for t in TASKS}
                atomic_dump(base,cp);atomic_dump({'normal':normal,'probe':raw},cache)
            enc=base.encoder;nkeys=np.argmax(enc.transform(cal,'peaks'),axis=1);groups={t:np.argmax(enc.transform(d[t]['frame'],'peaks'),axis=1)for t in TASKS}
            from shared_model_v7.role_priority import TARGETS
            row,top,n=optimize(normal,nkeys,cal,raw,groups,d,bc,draws=draws,preferred_task=TARGETS[a]if prefer_roles else None)
            row.update({'algorithm':a,'candidate':i,'setting':setting.__dict__,'feasible_policies':n,'source':str(cp.relative_to(ROOT))})
            rank=(row.get('probe_targets_met',False),row['probe_buffered_targets_met'],row['constraint_gap'],row['maintains_pooled'],row['maintains_each_task_F1'],row['preferred_task_F1'],row['pooled']['f1'])
            if best is None or rank>best[0]:
                best=(rank,row);ref=FamilyQuantileReference.fit(cal,normal,IdentityReference(),enc,row['levels']);winner=CalibratedDetector(base,ref,1.)
                atomic_dump(winner,dest/(a+'_selected.joblib'))
            records.append(row);write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':best[1],'candidates':len(records),'research_probe_labels_used':True,'external_blind_test':False})
            b=best[1];print('FEATURE',a,i,setting.view,setting.transform,'candidate_minR',round(row['minimum_task_recall']*100,2),'best',b['candidate'],
                'minR',round(b['minimum_task_recall']*100,2),'P/R/F1',[round(b['pooled'][k]*100,2)for k in ('precision','recall','f1')],'maxFPR',round(b['maximum_task_FPR']*100,2),'pass',b['probe_buffered_targets_met'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--draws',type=int,default=12000);args=p.parse_args();run(args.algorithms,args.draws)
