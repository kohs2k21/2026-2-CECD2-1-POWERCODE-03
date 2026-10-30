"""Role-prioritized selection on two complete-case folds with hard FPR caps.

Each estimator is tuned independently. No other model's score is lowered to
manufacture a rank. Gains must subsequently survive ALL 5,000 paired cases.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.larger_probe import bank
from shared_model_v7.causal_hold import CachedPrimary,Encoder as HoldEncoder
from shared_model_v7.exact_count_fusion import Encoder as ExactEncoder
from shared_model_v7.evidence_fusion import Reference,Detector
from shared_model_v7.adapter_calibration import Codec,tables
from shared_model_v7.dense_policy import CutReference
from shared_model_v7.strict_policy import solve
from shared_model_v7.role_priority import TARGETS
from shared_model_v7.search_calibration import metrics
from shared_model_v6.features import Evidence
from shared_model_v6.search import curves


def run(algorithms,minimum,output,auxiliary='original',switch_score=False,guard=False,continuous=False,quick=False,
        continuous_count=False,shared_traffic=False,stable_pmf=False,weights=(.25,.5,.75),shrinkage=False,primary_model=None,burst_fp_fraction=.95,independent_calibration=False):
    freeze();dest=OUT/output;dest.mkdir(exist_ok=True)
    dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    if independent_calibration:
        from shared_model_v7.independent_data import prepare
        dev=prepare()
    cal=dev['normal_calibration'];data=bank();models=joblib.load(BASE/'models.joblib')['models']
    core=Evidence(dev['rank_history']).fit(dev['train'])
    near=joblib.load(OUT/'neighbourhood_1.joblib')['reference']
    splits=[(t,k)for t in TASKS for k in range(2)]
    names=[t+'__fold'+str(k)for t,k in splits]
    for a in algorithms:
        alternative=None if primary_model is None else joblib.load(primary_model)
        if alternative is not None and alternative.algorithm!=a:raise ValueError('Wrong alternative estimator')
        if alternative is not None and alternative.threshold is None:
            alternative.threshold=float(np.quantile(alternative.score(cal),.995,method='higher'))
        primary=CachedPrimary(models[a]if alternative is None else alternative);normal=primary.score(cal);j=ALGORITHMS.index(a)
        raw={t:data[t]['baseline_scores'][:,j]if alternative is None else primary.score(data[t]['frame'])for t in TASKS};bc=[];splitdata={};masks={}
        for name,(t,k) in zip(names,splits):
            mask=data[t]['case_index']%10==k*5;masks[name]=mask
            y=data[t]['y'][mask];splitdata[name]={'y':y}
            r=curves(y,data[t]['baseline_scores'][mask,j],[models[a].threshold])[0];bc.append([r[v] for v in ('tn','fp','fn','tp')])
        bc=np.array(bc);records=[];best=None
        if auxiliary in ('pmf','intensity'):
            from shared_model_v7.pmf_filter import Encoder as PMFEncoder
            views=((.75,.7),(.9,.7))if quick else ((.75,.7),(1.,.7),(1.,.85))
            if auxiliary=='intensity':views=(((.8,.5,.25),(.6,.8,.95)),)
            encoders=[(f'pmf_{duty}_{stay}',PMFEncoder(core,near,dev['rank_history'],duty,stay,stable_log=stable_pmf).fit(cal))for duty,stay in views]
        else:
            encoders=[('exact3',ExactEncoder(core,near,dev['rank_history'],'3m').fit(cal)),
                      ('hold5',HoldEncoder(core,near,dev['rank_history'],primary,cal,normal,.95,5,12).fit(cal))]
        for tag,enc in encoders:
            codec_type=Codec
            if shared_traffic:
                from shared_model_v7.adapter_calibration import TrafficSharedCodec
                codec_type=TrafficSharedCodec
            if shrinkage:
                from shared_model_v7.shrunk_fusion import SharedCutoffCodec
                codec_type=SharedCutoffCodec
            codec=codec_type.fit(enc,cal);nk=codec.values(cal)
            tk={t:codec.values(data[t]['frame'])for t in TASKS}
            for weight in weights:
                reference_type=Reference
                if continuous:
                    from shared_model_v7.continuous_fusion import Reference as ContinuousReference
                    reference_type=ContinuousReference
                if continuous_count:
                    from shared_model_v7.continuous_fusion import CountContinuousReference
                    reference_type=CountContinuousReference
                if shrinkage:
                    from shared_model_v7.shrunk_fusion import Reference as ShrunkReference
                    reference_type=ShrunkReference
                parent=reference_type.fit(cal,normal,enc,.25,weight,preserve_other=True)
                ss={t:parent.apply(data[t]['frame'],raw[t])for t in TASKS}
                splitss={name:ss[t][masks[name]] for name,(t,k) in zip(names,splits)}
                splitkeys={name:tk[t][masks[name]] for name,(t,k) in zip(names,splits)}
                if switch_score:
                    from shared_model_v7.switch_calibration import tables as switch_tables
                    splitcore={name:raw[t][masks[name]]for name,(t,k)in zip(names,splits)}
                    counts,cuts,center,modes,provenance=switch_tables(parent.apply(cal,normal),normal,nk,cal,splitss,splitcore,splitkeys,splitdata,names,codec,primary.threshold)
                else:
                    counts,cuts,center,provenance=tables(parent.apply(cal,normal),nk,cal,splitss,splitkeys,splitdata,names,codec,legacy=primary.threshold)
                preferred=tuple(i for i,(t,k) in enumerate(splits)if t==TARGETS[a])
                if guard:
                    from model_research_v13.data import read
                    full=read(BASE/'summary.json')
                    references=[next(r for d in full['datasets']if d['dataset']==t for r in d['metrics']if r['method']==a)for t,k in splits]
                    budget=[.25 if t=='individual_delay'else(burst_fp_fraction if t=='time_burst'else .9)for t,k in splits]
                    choice,receipt=solve(counts,bc,minimum,preferred,fp_fraction=budget,reference_rates=references,metric_buffer=.001)
                else:choice,receipt=solve(counts,bc,minimum,preferred)
                row={'algorithm':a,'temporal_policy':tag,'model_weight_point':.25,'model_weight_burst':weight,
                     'solver':receipt,'normal_cutoff_provenance':provenance,'probe_targets_met':choice is not None,
                     'normal_only_alternative_core':None if primary_model is None else str(primary_model)}
                if choice is not None:
                    actual=np.array(receipt['counts']).reshape(5,2,4).sum(axis=1)
                    tm=metrics(*actual.T);pm=metrics(*actual.sum(axis=0))
                    row.update({'pooled':{k:float(v)for k,v in pm.items()},
                                'tasks':{t:{k:float(v[i])for k,v in tm.items()}for i,t in enumerate(TASKS)},
                                'cutoffs':[float(c[j])for c,j in zip(cuts,choice)]})
                    rank=float(tm['f1'][list(TASKS).index(TARGETS[a])])
                    if best is None or rank>best[0]:
                        if switch_score:
                            from shared_model_v7.switch_calibration import Reference as SwitchReference
                            ref=SwitchReference(parent,enc,np.array(row['cutoffs']),np.array([c[j]for c,j in zip(center,choice)]),
                                                np.array([c[j]for c,j in zip(modes,choice)]),codec)
                            row['same_estimator_original_score_groups']=int(ref.original_score.sum())
                        else:
                            ref=CutReference(parent,enc,np.array(row['cutoffs']),center,codec=codec)
                            ref.mode='two_fold_research_normal_Adapter_statistics_hard_FPR_nonincrease'
                        winner=Detector(primary,ref);got=[]
                        for t in TASKS:
                            r=curves(data[t]['y'],winner.score(data[t]['frame']),[1.])[0]
                            got.append([r[k]for k in ('tn','fp','fn','tp')])
                        np.testing.assert_array_equal(got,actual)
                        best=(rank,row);atomic_dump(winner,dest/(a+'_selected.joblib'))
                records.append(row);write(dest/(a+'_records.json'),records)
                write(dest/(a+'_selection.json'),{'chosen':None if best is None else best[1],
                      'adaptive_reused_benchmark_not_blind':True,'two_whole_case_folds':True,
                      'other_models_not_used_for_selection':True,'FPR_nonincrease_hard_constraint':True})
                print('STRICT ROLE',a,tag,weight,'pass',choice is not None,receipt['message'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS))
    p.add_argument('--minimum',type=float,default=.57);p.add_argument('--output',default='strict_roles1000')
    p.add_argument('--auxiliary',choices=['original','pmf','intensity'],default='original')
    p.add_argument('--switch-score',action='store_true')
    p.add_argument('--guard',action='store_true')
    p.add_argument('--continuous',action='store_true');p.add_argument('--quick',action='store_true')
    p.add_argument('--continuous-count',action='store_true');p.add_argument('--shared-traffic',action='store_true')
    p.add_argument('--stable-pmf',action='store_true');p.add_argument('--weights',type=float,nargs='+',default=[.25,.5,.75])
    p.add_argument('--shrinkage',action='store_true')
    p.add_argument('--primary-model',type=Path)
    p.add_argument('--burst-fp-fraction',type=float,default=.95)
    p.add_argument('--independent-calibration',action='store_true')
    args=p.parse_args();run(args.algorithms,args.minimum,args.output,args.auxiliary,args.switch_score,args.guard,args.continuous,args.quick,
                            args.continuous_count,args.shared_traffic,args.stable_pmf,args.weights,args.shrinkage,args.primary_model,args.burst_fp_fraction,args.independent_calibration)
