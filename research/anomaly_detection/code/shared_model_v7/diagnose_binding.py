"""Which task's 55% target binds the finite policy? Diagnostic, not adoption."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v7.dense_policy import keys,tables,solve
from shared_model_v7.evidence_fusion import Encoder,Reference
from shared_model_v6.features import Evidence
from shared_model_v6.probe import bank
from shared_model_v6.search import curves


def run():
    freeze();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration'];d=bank();models=joblib.load(BASE/'models.joblib')['models']
    normalref=joblib.load(OUT/'neighbourhood_1.joblib')['reference'];core=Evidence(dev['rank_history']).fit(dev['train']);setting=joblib.load(OUT/'count_filter_values_6.joblib')['setting']
    enc=Encoder(core,normalref,dev['rank_history'],setting).fit(cal);nk=keys(enc,cal);tk={t:keys(enc,p['frame'])for t,p in d.items()};results={}
    for j,a in enumerate(ALGORITHMS):
        raw={};baseline=[]
        for t in TASKS:
            z=np.load(BASE/(t+'_scores.npz'));mask=z['case_index']%25==0;raw[t]=z['scores'][mask,j]
            r=curves(d[t]['y'],raw[t],[models[a].threshold])[0];baseline.append([r[k]for k in('tn','fp','fn','tp')])
        normal=models[a].score(cal);parent=Reference.fit(cal,normal,enc,.25,.75,preserve_other=True)
        ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
        c,cuts,center,p=tables(ns,nk,cal,ss,tk,d,TASKS);baseline=np.array(baseline);can,_=solve(c,baseline,minimum_recall=0.,time_limit=10)
        if can is None:results[a]={'retains_old_metrics':False};continue
        row={'retains_old_metrics':True,'scope':'finite frozen evidence-fusion policy only; NOT a bound for all models','tasks':{}}
        for ti,t in enumerate(TASKS):
            lower=0.;upper=1.
            for unused in range(8):
                target=(lower+upper)/2;minimum=np.zeros(5);minimum[ti]=target
                chosen,receipt=solve(c,baseline,minimum_recall=minimum,time_limit=10)
                if chosen is not None:lower=target
                elif receipt['finite_policy_infeasible']:upper=target
                else:break
            row['tasks'][t]={'max_Recall_lower_bound':lower,'infeasible_upper_bound':upper,'other_task_Recall_targets_relaxed_for_diagnosis_only':True}
            print('BINDING',a,t,round(lower*100,2),round(upper*100,2),flush=True)
        results[a]=row;write(OUT/'binding_diagnostics.json',{'diagnostic_relaxations_not_adopted':True,'results':results})

if __name__=='__main__':run()
