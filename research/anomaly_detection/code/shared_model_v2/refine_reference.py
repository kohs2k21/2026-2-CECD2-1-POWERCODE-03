"""Preserve normal multiplicity with smoothed LOF references; tighten KM FPR."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from dataclasses import asdict
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v2.run import prepare
from shared_model_v2.models import Setting,Detector
from shared_model_v2.context_calibration import ContextReference
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats

PARENT=ROOT/'reports/shared_model_v2_geometry'
OUT=ROOT/'reports/shared_model_v2_reference'


def candidates(a,pkg):
    if a=='kmeans':yield pkg['models'][a].base;return
    yield pkg['models'][a].base
    for noise in(.01,.03,.1):
        for k,cap in((150,2048),(300,4096)):
            yield Setting(a,memory=False,interactions=True,neighbors=k,reference_cap=cap,deduplicate=False,reference_noise=noise)
    for trim in(.005,.01):
        yield Setting(a,memory=False,interactions=True,neighbors=300,reference_cap=2048,trim=trim)
    yield Setting(a,memory=False,interactions=True,neighbors=150,reference_cap=4096,adapter_weight=.5)


def tune():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    data=prepare();normal=data['normal_calibration'];selected=read(PARENT/'selection.json');pkg=joblib.load(PARENT/'models.joblib')
    y=np.concatenate([data[n]['y']for n in('search','confirmation')]);records=[]
    for a in('kmeans','lof'):
        best=None;record=None;key=None;baseline=selected['baseline_development'][a]
        pooled_cap=baseline['normal_false_positive_rate']*(.85 if a=='kmeans'else 1.)
        cap={n:r['normal_false_positive_rate']+.01 for n,r in baseline['folds'].items()}
        for i,value in enumerate(candidates(a,pkg)):
            base=Detector(value).fit(data['train'])if isinstance(value,Setting)else value
            nr=base.score(normal);raw={n:base.score(data[n]['frame'])for n in('search','confirmation')}
            for mode in('global','availability','availability_phase'):
                for alpha in((1.,)if mode=='global'else(.25,.5,1.)):
                    for kind in('cdf','robust'):
                        ref=NormalReference.fit(normal,nr,mode,alpha,kind)if mode in('global','availability')else ContextReference.fit(base,normal,nr,mode,alpha,kind)
                        ns=ref.apply(normal,nr);ss={n:ref.apply(data[n]['frame'],raw[n])for n in raw};pooled=np.concatenate(list(ss.values()))
                        for q in(.95,.96,.97,.975,.98,.9825,.985,.9875,.99,.99125,.9925,.99375,.995):
                            threshold=float(np.quantile(ns,q,method='higher'));folds={n:_stats(data[n]['y'],ss[n],threshold)for n in raw};stats=_stats(y,pooled,threshold)
                            eligible=stats['normal_false_positive_rate']<=pooled_cap and all(folds[n]['normal_false_positive_rate']<=cap[n]for n in raw)
                            r={'settings':asdict(base.setting),'candidate':i,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':threshold,'folds':folds,'eligible':eligible,**stats}
                            records.append({'algorithm':a,**r});rank=(stats['f1'],-stats['normal_false_positive_rate'])
                            if eligible and(key is None or rank>key):best,key,record=CalibratedDetector(base,ref,threshold),rank,r
            write(OUT/'search_results.json',records);joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            write(OUT/'progress.json',{'algorithm':a,'candidate':i,'best':record,'normal_FPR_development_cap':pooled_cap})
            print(a,i,'best',round(key[0]*100,3)if key else None,'FP',round(-key[1]*100,3)if key else None,flush=True)
        if best is None:raise RuntimeError('No candidate meets development FP cap')
        pkg['models'][a]=best;selected['selected'][a]=record
    joblib.dump(pkg,OUT/'models.joblib',compress=3)
    selected.update({'model_sha256':sha256(OUT/'models.joblib'),'preceding_model_sha256':sha256(PARENT/'models.joblib'),
       'KM_development_FPR_cap_ratio_to_original':.85,'LOF_development_FPR_cap_ratio_to_original':1.,
       'ADBench_not_used_for_selection':True,'prior_final_results_informed_research':True})
    write(OUT/'selection.json',selected);write(OUT/'preparation.json',read(PARENT/'preparation.json'))
    print('Reference refinement frozen',[(a,r['f1'])for a,r in selected['selected'].items()],flush=True)


if __name__=='__main__':tune()
