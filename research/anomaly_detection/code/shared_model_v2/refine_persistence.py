"""Temporal score ablation selected without reading benchmark/ADBench labels."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from dataclasses import asdict
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import read,write
from shared_model_v2.run import prepare
from shared_model_v2.score_filter import FilteredDetector
from shared_model_v2.context_calibration import ContextReference
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats

OUT=ROOT/'reports/shared_model_v2_persistence'


def tune(protection=0.,only_primary=False):
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    data=prepare();normal=data['normal_calibration'];baseline=read(ROOT/'reports/shared_model_v2_reference/selection.json')['baseline_development']['lof']
    folders=('reports/shared_model_v2_lof_calibrated','reports/shared_model_target_lof','reports/shared_model_v2_reference')
    bank=[]
    for folder in folders:
        f=ROOT/folder;path=f/'lof_selected.joblib'if(f/'lof_selected.joblib').exists()else f/'models.joblib'
        p=joblib.load(path);bank.append(p.base if hasattr(p,'base')else p['models']['lof'].base)
    if only_primary:bank=bank[:1]
    y=np.concatenate([data[n]['y']for n in('search','confirmation')]);best=None;record=None;key=None;records=[]
    for j,base in enumerate(bank):
        ns=base.score(normal);raw={n:base.score(data[n]['frame'])for n in('search','confirmation')}
        for beta in(.25,.5,.75):
            for window in(3,5):
                filtered=FilteredDetector(base,beta,window,8,True,protection)
                nr=filtered.filter(normal,ns);rs={n:filtered.filter(data[n]['frame'],raw[n])for n in raw}
                for mode in('global','availability','availability_phase'):
                    for alpha in((1.,)if mode=='global'else(.5,1.)):
                        for kind in('cdf','robust'):
                            ref=NormalReference.fit(normal,nr,mode,alpha,kind)if mode in('global','availability')else ContextReference.fit(filtered,normal,nr,mode,alpha,kind)
                            nc=ref.apply(normal,nr);ss={n:ref.apply(data[n]['frame'],rs[n])for n in raw};pooled=np.concatenate(list(ss.values()))
                            for q in(.95,.96,.97,.975,.98,.985,.9875,.99,.9925,.995):
                                threshold=float(np.quantile(nc,q,method='higher'));folds={n:_stats(data[n]['y'],ss[n],threshold)for n in raw};stats=_stats(y,pooled,threshold)
                                eligible=stats['normal_false_positive_rate']<=baseline['normal_false_positive_rate']and all(folds[n]['normal_false_positive_rate']<=baseline['folds'][n]['normal_false_positive_rate']+.01 for n in raw)
                                r={'settings':asdict(base.setting),'base_candidate':j,'filter':{'beta':beta,'window':window,'ttl':8,'traffic_protection':protection},'mode':mode,'alpha':alpha,'kind':kind,'q':q,
                                   'threshold':threshold,'folds':folds,'eligible':eligible,**stats};records.append(r)
                                rank=(stats['f1'],-stats['normal_false_positive_rate'])
                                if eligible and(key is None or rank>key):best,key,record=CalibratedDetector(filtered,ref,threshold),rank,r
                joblib.dump(best,OUT/'lof_selected.joblib',compress=3);write(OUT/'search_results.json',records)
                write(OUT/'progress.json',{'base':j,'beta':beta,'window':window,'best':record})
                print('LOF filter',j,beta,window,'best',round(key[0]*100,3)if key else None,'FP',round(-key[1]*100,3)if key else None,flush=True)
    write(OUT/'selection.json',{'selected':record,'model_sha256':sha256(OUT/'lof_selected.joblib'),
       'development_selection_only':True,'ADBench_not_used':True,'original_benchmark_not_modified':True,
       'prior_benchmark_results_informed_research':True,'causal_score_smoothing_not_point_adjustment':True})


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);p.add_argument('--protection',type=float,default=0.);p.add_argument('--only-primary',action='store_true')
    args=p.parse_args()
    if args.output is not None:OUT=args.output.resolve()
    tune(args.protection,args.only_primary)
