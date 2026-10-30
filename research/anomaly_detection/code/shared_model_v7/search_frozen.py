"""Revisit existing normal-fit candidates under the new minimum-Recall gate."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,re
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v7.optimize import optimize
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_v6.probe import bank
from shared_model_v6.features import Evidence
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector

class IdentityReference:
    def apply(self,f,raw):return np.asarray(raw).copy()

def run(algorithms,draws=10000):
    freeze();dest=OUT/'frozen_search';dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration']
    enc=Evidence(dev['rank_history']).fit(dev['train']);nkeys=np.argmax(enc.transform(cal,'peaks'),axis=1)
    groups={t:np.argmax(enc.transform(d[t]['frame'],'peaks'),axis=1)for t in TASKS};models=joblib.load(BASE/'models.joblib')['models']
    old={t:np.load(BASE/(t+'_scores.npz'))for t in TASKS}
    for j,a in enumerate(ALGORITHMS):
        bc=np.array([[curves(old[t]['y'][old[t]['case_index']%25==0],old[t]['scores'][old[t]['case_index']%25==0,j],[models[a].threshold])[0][k]for k in ('tn','fp','fn','tp')]for t in TASKS])
        records=[];best=None
        for directory,probe in [('search','probe'),('joint_search','probe'),('tail_search','probe'),('focus_search','focus_probe'),('hybrid_search','hybrid_probe')]:
            for path in sorted((ROOT/'reports/shared_model_v6'/directory).glob(a+'_candidate_*.joblib')):
                number=int(re.search(r'_(\d+)\.joblib$',path.name)[1]);cache=ROOT/'reports/shared_model_v6'/probe/(directory+'_'+path.stem+'_raw.joblib')
                if not cache.exists():continue
                normal=joblib.load(path.parent/(a+'_raw_'+str(number)+'.joblib'))['normal'];raw=joblib.load(cache)
                row,top,n=optimize(normal,nkeys,cal,raw,groups,d,bc,draws=draws)
                row.update({'algorithm':a,'source':str(path.relative_to(ROOT)),'source_sha256':__import__('model_evaluation.data',fromlist=['sha256']).sha256(path),'feasible_policies':n})
                rank=(row['probe_buffered_targets_met'],row['constraint_gap'],row['maintains_pooled'],row['maintains_each_task_F1'],row['pooled']['f1'])
                if best is None or rank>best[0]:
                    best=(rank,row)
                    base=joblib.load(path);ref=FamilyQuantileReference.fit(cal,normal,IdentityReference(),enc,row['levels']);winner=CalibratedDetector(base,ref,1.)
                    joblib.dump(winner,dest/(a+'_selected.joblib'),compress=3)
                records.append(row);write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':best[1],'candidates':len(records),'normal_only_fit':True,'reused_research_labels_select_policies':True,'external_blind_test':False})
                b=best[1]
                print('FROZEN',a,directory,number,'best',b['source'].split('/')[-1],'minR',round(b['minimum_task_recall']*100,2),'P/R/F1',[round(b['pooled'][k]*100,2)for k in ('precision','recall','f1')],'pass',b['probe_buffered_targets_met'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--draws',type=int,default=10000);args=p.parse_args();run(args.algorithms,args.draws)
