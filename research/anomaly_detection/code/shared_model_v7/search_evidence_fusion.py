"""Explicitly adaptive normal-statistical score fusion, never plain-model claims."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,itertools
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.evidence_fusion import Encoder,Reference,Detector
from shared_model_v7.calibration import FamilyQuantileReference
from shared_model_v7.optimize import optimize
from shared_model_v7.role_priority import TARGETS
from shared_model_v7.independent_data import prepare
from shared_model_v6.features import Evidence
from shared_model_v6.probe import bank
from shared_model_v6.search import curves


def candidates(a,models,dev,independent,d):
    j=ALGORITHMS.index(a);raw={}
    for t in TASKS:
        z=np.load(BASE/(t+'_scores.npz'));raw[t]=z['scores'][z['case_index']%25==0,j]
    yield 'release_v6',models[a],dev['normal_calibration'],models[a].score(dev['normal_calibration']),raw
    paths=[]
    if a=='if':paths.append(('role_independent_search',6,independent['normal_calibration']))
    if a=='kmeans':paths.append(('bounded_role_search',0,dev['normal_calibration']))
    if a=='lof':paths.append(('bounded_role_search',3,dev['normal_calibration']))
    for directory,number,cal in paths:
        cp=OUT/directory/(a+'_candidate_'+str(number)+'.joblib');cache=cp.parent/(a+'_raw_'+str(number)+'.joblib')
        if cp.exists()and cache.exists():
            payload=joblib.load(cache);assert len(payload['normal'])==len(cal)
            yield directory+'/'+cp.name,joblib.load(cp),cal,payload['normal'],payload['probe']


def run(algorithms):
    freeze();dest=OUT/'evidence_fusion_search';dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    independent=prepare();models=joblib.load(BASE/'models.joblib')['models'];normal_reference=joblib.load(OUT/'neighbourhood_1.joblib')['reference']
    core=Evidence(dev['rank_history']).fit(dev['train'])
    for a in algorithms:
        j=ALGORITHMS.index(a);bc=[]
        for t in TASKS:
            z=np.load(BASE/(t+'_scores.npz'));mask=z['case_index']%25==0
            r=curves(z['y'][mask],z['scores'][mask,j],[models[a].threshold])[0];bc.append([r[k]for k in('tn','fp','fn','tp')])
        records=[];best=None
        for tag,base,cal,normal,raw in candidates(a,models,dev,independent,d):
            for filter_index in(4,6):
                filter_setting=joblib.load(OUT/('count_filter_values_'+str(filter_index)+'.joblib'))['setting']
                enc=Encoder(core,normal_reference,dev['rank_history'],filter_setting).fit(cal)
                nkeys=np.argmax(enc.transform(cal,'peaks'),axis=1);groups={t:np.argmax(enc.transform(p['frame'],'peaks'),axis=1)for t,p in d.items()}
                for wp,wb in itertools.product((.25,.5,.75),repeat=2):
                    parent=Reference.fit(cal,normal,enc,wp,wb);ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
                    row,top,n=optimize(ns,nkeys,cal,ss,groups,d,np.array(bc),draws=1000,preferred_task=TARGETS[a])
                    row.update({'algorithm':a,'source':tag,'filter_index':filter_index,'model_weight_point':wp,'model_weight_burst':wb,
                        'not_bare_estimator_score':True,'normal_statistics_fusion':True})
                    rank=(row['probe_targets_met'],row['constraint_gap'],row['preferred_task_F1'],row['pooled']['f1'])
                    if best is None or rank>best[0]:
                        best=(rank,row);ref=FamilyQuantileReference.fit(cal,normal,parent,enc,row['levels']);atomic_dump(Detector(base,ref),dest/(a+'_selected.joblib'))
                    records.append(row);write(dest/(a+'_records.json'),records)
                    write(dest/(a+'_selection.json'),{'chosen':best[1],'candidates':len(records),'not_bare_estimator_comparison':True,'research_probe_labels_used':True,'external_blind_test':False})
                    print('EVIDENCE-FUSION',a,tag,filter_index,wp,wb,'minR',round(row['minimum_task_recall']*100,2),'pass',row['probe_targets_met'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
