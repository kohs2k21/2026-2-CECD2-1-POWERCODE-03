"""No Adapter experts: compare statistical calibration after the same core."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,itertools
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.causal_hold import CachedPrimary,Encoder as HoldEncoder
from shared_model_v7.evidence_fusion import Encoder,Reference,Detector
from shared_model_v7.adapter_calibration import Codec,tables
from shared_model_v7.dense_policy import solve,CutReference
from shared_model_v7.role_priority import TARGETS
from shared_model_v7.search_calibration import metrics
from shared_model_v6.features import Evidence
from shared_model_v6.probe import bank
from shared_model_v6.search import curves


def run(algorithms,auxiliary='hold',output='adapter_fusion_search'):
    freeze();dest=OUT/output;dest.mkdir(exist_ok=True);dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration'];d=bank()
    models=joblib.load(BASE/'models.joblib')['models'];core=Evidence(dev['rank_history']).fit(dev['train']);reference=joblib.load(OUT/'neighbourhood_1.joblib')['reference']
    for a in algorithms:
        j=ALGORITHMS.index(a);primary=CachedPrimary(models[a]);normal=primary.score(cal);raw={};bc=[]
        for t in TASKS:
            z=np.load(BASE/(t+'_scores.npz'));mask=z['case_index']%25==0;raw[t]=z['scores'][mask,j]
            r=curves(d[t]['y'],raw[t],[primary.threshold])[0];bc.append([r[k]for k in('tn','fp','fn','tp')])
        bc=np.array(bc);records=[];best=None
        if auxiliary=='exact':
            from shared_model_v7.exact_count_fusion import Encoder as ExactEncoder
            encoders=[(view,ExactEncoder(core,reference,dev['rank_history'],view).fit(cal))for view in('3m','short','agreement')]
        elif auxiliary=='bounded':
            encoders=[]
            for seed,length,refractory in itertools.product((.95,.99),(5,8),(12,20)):
                encoders.append((f'bounded_{seed}_{length}_{refractory}',HoldEncoder(core,reference,dev['rank_history'],primary,cal,normal,seed,length,refractory).fit(cal)))
        else:
            encoders=[('count',Encoder(core,reference,dev['rank_history'],joblib.load(OUT/'count_filter_values_6.joblib')['setting']).fit(cal))]
            for seed,length in((.99,5),(.99,8),(.995,5),(.995,8)):
                encoders.append((str(seed)+'_'+str(length),HoldEncoder(core,reference,dev['rank_history'],primary,cal,normal,seed,length).fit(cal)))
        for tag,enc in encoders:
            codec=Codec.fit(enc,cal);nk=codec.values(cal);tk={t:codec.values(p['frame'])for t,p in d.items()}
            for burst_weight in(.25,.5,.75):
                parent=Reference.fit(cal,normal,enc,.25,burst_weight,preserve_other=True)
                ns=parent.apply(cal,normal);ss={t:parent.apply(d[t]['frame'],raw[t])for t in TASKS}
                c,cuts,center,p=tables(ns,nk,cal,ss,tk,d,TASKS,codec,legacy=primary.threshold);choice,receipt=solve(c,bc)
                row={'algorithm':a,'temporal_policy':tag,'model_weight_point':.25,'model_weight_burst':burst_weight,'Adapter_expert_estimators':0,
                    'normal_Adapter_statistical_cutoffs':True,'solver':receipt,'normal_cutoff_provenance':p,'probe_targets_met':choice is not None}
                if choice is not None:
                    actual=np.array(receipt['counts']);tm=metrics(*actual.T);pm=metrics(*actual.sum(axis=0));row.update({'pooled':{k:float(v)for k,v in pm.items()},
                        'tasks':{t:{k:float(v[i])for k,v in tm.items()}for i,t in enumerate(TASKS)},'cutoffs':[float(c[j])for c,j in zip(cuts,choice)]})
                    rank=float(tm['f1'][list(TASKS).index(TARGETS[a])])
                    if best is None or rank>best[0]:
                        ref=CutReference(parent,enc,np.array(row['cutoffs']),center,codec=codec);ref.mode='normal_Adapter_statistics_evidence_history_cutoff'
                        winner=Detector(primary,ref);got=[]
                        for t in TASKS:
                            r=curves(d[t]['y'],winner.score(d[t]['frame']),[1.])[0];got.append([r[k]for k in('tn','fp','fn','tp')])
                        np.testing.assert_array_equal(got,actual);best=(rank,row);atomic_dump(winner,dest/(a+'_selected.joblib'))
                records.append(row);write(dest/(a+'_records.json'),records);write(dest/(a+'_selection.json'),{'chosen':None if best is None else best[1],
                    'adaptive_research_labels_used':True,'external_blind_test':False,'normal_lookup_tables_not_Adapter_expert_models':True})
                print('ADAPTER CALIBRATION',a,tag,burst_weight,'groups',codec.n_groups,'variables',receipt['variables'],'pass',choice is not None,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));p.add_argument('--auxiliary',choices=['hold','exact','bounded'],default='hold');p.add_argument('--output',default='adapter_fusion_search')
    args=p.parse_args();run(args.algorithms,args.auxiliary,args.output)
