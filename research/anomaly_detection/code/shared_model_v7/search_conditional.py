from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v7.optimize import optimize
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_v7.conditional import CompletionReference
from shared_model_v6.probe import bank
from shared_model_v6.features import Evidence
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector

def run(algorithms,draws=18000):
    freeze();dest=OUT/'conditional_search';dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration']
    oldmodels=joblib.load(BASE/'models.joblib')['models'];old={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS}
    enc=Evidence(dev['rank_history']).fit(dev['train']);nkeys=np.argmax(enc.transform(cal,'peaks'),axis=1)
    groups={t:np.argmax(enc.transform(d[t]['frame'],'peaks'),axis=1)for t in TASKS}
    for j,a in enumerate(ALGORITHMS):
        bc=np.array([[curves(old[t]['y'][old[t]['case_index']%25==0],old[t]['scores'][old[t]['case_index']%25==0,j],[oldmodels[a].threshold])[0][k]for k in ('tn','fp','fn','tp')]for t in TASKS])
        sources={'v6_current':oldmodels[a].base}
        for folder in ('weighted_search','feature_search'):
            p=OUT/folder/(a+'_selected.joblib')
            if p.exists():sources[folder]=joblib.load(p).base
        sources['v6_alternative']=joblib.load(ROOT/'reports/shared_model_v6/attempt01/models.joblib')['models'][a].base
        best=None;records=[]
        for name,base in sources.items():
            cache=dest/(a+'_'+name+'_raw.joblib')
            if cache.exists():pack=joblib.load(cache);normal=pack['normal'];raw=pack['probe']
            else:
                normal=base.score(cal);raw={t:base.score(d[t]['frame'])for t in TASKS};joblib.dump({'normal':normal,'probe':raw},cache,compress=3)
            for finer in (False,True):
                for alpha in (.5,1.):
                    for kind in ('cdf','robust'):
                        parent=CompletionReference.fit(cal,normal,enc,alpha,finer,kind);ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
                        row,top,n=optimize(ns,nkeys,cal,ss,groups,d,bc,draws=draws)
                        row.update({'algorithm':a,'source':name,'finer':finer,'alpha':alpha,'kind':kind,'conditional_reference_groups':len(parent.tables),'feasible_policies':n})
                        rank=(row['probe_buffered_targets_met'],row['constraint_gap'],row['maintains_pooled'],row['maintains_each_task_F1'],row['pooled']['f1'])
                        if best is None or rank>best[0]:
                            best=(rank,row);ref=FamilyQuantileReference.fit(cal,normal,parent,enc,row['levels']);winner=CalibratedDetector(base,ref,1.)
                            joblib.dump(winner,dest/(a+'_selected.joblib'),compress=3)
                        records.append(row);write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':best[1],'candidates':len(records),'research_probe_labels_used':True,'external_blind_test':False})
            b=best[1];print('CONDITIONAL',a,name,'best',b['source'],'minR',round(b['minimum_task_recall']*100,2),'P/R/F1',[round(b['pooled'][k]*100,2)for k in ('precision','recall','f1')],'maxFPR',round(b['maximum_task_FPR']*100,2),'pass',b['probe_buffered_targets_met'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
