"""Dense normal-cutoff control and fused-history candidate verification."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.dense_policy import keys,tables,solve,CutReference
from shared_model_v7.search_frozen import IdentityReference
from shared_model_v7.evidence_fusion import Encoder,Reference,Detector
from shared_model_v7.search_evidence_fusion import candidates
from shared_model_v7.independent_data import prepare
from shared_model_v7.search_calibration import metrics
from shared_model_v7.role_priority import TARGETS
from shared_model_v6.features import Evidence
from shared_model_v6.probe import bank
from shared_model_v6.search import curves
from shared_model_target.calibration import CalibratedDetector


def run(algorithms):
    freeze();dest=OUT/'dense_fusion_search';dest.mkdir(exist_ok=True);d=bank();dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');independent=prepare()
    models=joblib.load(BASE/'models.joblib')['models'];core=Evidence(dev['rank_history']).fit(dev['train']);reference=joblib.load(OUT/'neighbourhood_1.joblib')['reference']
    for a in algorithms:
        j=ALGORITHMS.index(a);bc=[]
        for t in TASKS:
            z=np.load(BASE/(t+'_scores.npz'));mask=z['case_index']%25==0;r=curves(z['y'][mask],z['scores'][mask,j],[models[a].threshold])[0]
            bc.append([r[k]for k in('tn','fp','fn','tp')])
        bc=np.array(bc);records=[];best=None
        for tag,base,cal,normal,raw in candidates(a,models,dev,independent,d):
            if tag=='release_v6':
                ng=keys(core,cal);tg={t:keys(core,p['frame'])for t,p in d.items()}
                counts,cuts,center,provenance=tables(normal,ng,cal,raw,tg,d,TASKS,legacy_cutoff=base.threshold)
                choice,control=solve(counts,bc,minimum_recall=0.)
                if choice is None:raise AssertionError('Unmodified baseline cannot be reproduced: '+str(control))
                write(dest/(a+'_baseline_control.json'),control);print('DENSE CONTROL',a,'passed exact nonregression',flush=True)
            prior=read(OUT/'evidence_fusion_search'/(a+'_records.json'))
            matched=sorted([r for r in prior if r['source']==tag],key=lambda r:r['constraint_gap'],reverse=True)[:2]
            for record in matched:
                setting=joblib.load(OUT/('count_filter_values_'+str(record['filter_index'])+'.joblib'))['setting']
                enc=Encoder(core,reference,dev['rank_history'],setting).fit(cal)
                parent=Reference.fit(cal,normal,enc,record['model_weight_point'],record['model_weight_burst'])
                ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
                for history in(False,True):
                    ng=keys(enc,cal,history);tg={t:keys(enc,p['frame'],history)for t,p in d.items()}
                    counts,cuts,center,provenance=tables(ns,ng,cal,ss,tg,d,TASKS,history=history)
                    choice,receipt=solve(counts,bc)
                    row={'algorithm':a,'source':tag,'filter_index':record['filter_index'],'model_weight_point':record['model_weight_point'],
                        'model_weight_burst':record['model_weight_burst'],'history_support_groups':history,'solver':receipt,'normal_cutoff_provenance':provenance,'probe_targets_met':choice is not None}
                    if choice is not None:
                        actual=np.array(receipt['counts']);tm=metrics(*actual.T);pm=metrics(*actual.sum(axis=0));row.update({'pooled':{k:float(v)for k,v in pm.items()},
                            'tasks':{t:{k:float(v[i])for k,v in tm.items()}for i,t in enumerate(TASKS)},'cutoffs':[float(c[j])for c,j in zip(cuts,choice)]})
                        rank=tm['f1'][list(TASKS).index(TARGETS[a])]
                        if best is None or rank>best[0]:
                            ref=CutReference(parent,enc,np.array(row['cutoffs']),center,history);winner=Detector(base,ref)
                            # Verify actual predictions against selected counts.
                            got=[]
                            for t in TASKS:
                                r=curves(d[t]['y'],winner.score(d[t]['frame']),[1.])[0];got.append([r[k]for k in('tn','fp','fn','tp')])
                            np.testing.assert_array_equal(got,actual)
                            best=(float(rank),row);atomic_dump(winner,dest/(a+'_selected.joblib'))
                    records.append(row);write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':None if best is None else best[1],
                        'adaptive_research_labels_used':True,'external_blind_test':False,'normal_cutoff_grid':'all distinct calibration values + predetermined robust upper fence; Pareto pruning'})
                    print('DENSE FUSION',a,tag,history,'variables',receipt['variables'],'pass',choice is not None,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
