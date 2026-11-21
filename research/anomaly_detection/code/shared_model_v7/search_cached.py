"""Exact constrained re-evaluation of all finished v7 cached representations."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,re
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.optimize import optimize
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_v7.search_frozen import IdentityReference
from shared_model_v7.conditional import CompletionReference
from shared_model_v6.probe import bank
from shared_model_v6.features import Evidence
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector

def run(algorithms,conditional=False):
    freeze();dest=OUT/('exact_conditional'if conditional else'exact_search');dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration']
    old={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS};models=joblib.load(BASE/'models.joblib')['models'];fallback=Evidence(dev['rank_history']).fit(dev['train'])
    for j,a in enumerate(ALGORITHMS):
        bc=np.array([[curves(old[t]['y'][old[t]['case_index']%25==0],old[t]['scores'][old[t]['case_index']%25==0,j],[models[a].threshold])[0][k]for k in ('tn','fp','fn','tp')]for t in TASKS])
        records=[];best=None
        for directory in ('feature_search','weighted_search'):
            for cp in sorted((OUT/directory).glob(a+'_candidate_*.joblib')):
                number=int(re.search(r'_(\d+)\.joblib$',cp.name)[1]);cache=cp.parent/(a+'_raw_'+str(number)+'.joblib')
                if not cache.exists():continue
                base=joblib.load(cp);pack=joblib.load(cache);normal=pack['normal'];raw=pack['probe'];enc=base.encoder if base.encoder is not None else fallback
                nkeys=np.argmax(enc.transform(cal,'peaks'),axis=1);groups={t:np.argmax(enc.transform(d[t]['frame'],'peaks'),axis=1)for t in TASKS}
                variants=[('identity',IdentityReference())]
                if conditional:
                    variants=[(str(finer)+'_'+str(alpha),CompletionReference.fit(cal,normal,enc,alpha,finer))for finer in(False,True)for alpha in(.5,1.)]
                for tag,parent in variants:
                    ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
                    row,top,n=optimize(ns,nkeys,cal,ss,groups,d,bc,draws=1000)
                    row.update({'algorithm':a,'candidate':cp.name,'directory':directory,'conditional':tag,'source':str(cp.relative_to(ROOT))})
                    rank=(row['probe_targets_met'],row['probe_buffered_targets_met'],row['constraint_gap'],row['maintains_pooled'],row['maintains_each_task_F1'],row['pooled']['f1'])
                    if best is None or rank>best[0]:
                        best=(rank,row);ref=FamilyQuantileReference.fit(cal,normal,parent,enc,row['levels']);winner=CalibratedDetector(base,ref,1.)
                        atomic_dump(winner,dest/(a+'_selected.joblib'))
                    records.append(row)
                write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':best[1],'candidates':len(records),'research_probe_labels_used':True,'external_blind_test':False})
                b=best[1];print('EXACT',a,directory,number,'best',b['candidate'],'minR',round(b['minimum_task_recall']*100,2),'P/R/F1',[round(b['pooled'][k]*100,2)for k in ('precision','recall','f1')],
                    'pass',b['probe_targets_met'],'feasibleSource',not row['exact_feasibility'].get('globally_infeasible'),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--conditional',action='store_true');args=p.parse_args();run(args.algorithms,args.conditional)
