"""Refine context scoring and K-means geometry; never read final labels."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import copy
from dataclasses import asdict
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v2.run import prepare,OUT as PARENT
from shared_model_v2.models import Setting,Detector
from shared_model_v2.context_calibration import ContextReference
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import ALGORITHMS,_stats

OUT=ROOT/'reports/shared_model_v2_context'


def tune():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    data=prepare();normal=data['normal_calibration'];selected=read(PARENT/'selection.json');pkg=joblib.load(PARENT/'models.joblib')
    y=np.concatenate([data[n]['y']for n in('search','confirmation')]);records=[]
    for a in ALGORITHMS:
        best=pkg['models'][a];record=selected['selected'][a];key=(record['f1'],-record['normal_false_positive_rate'])
        cap={n:r['normal_false_positive_rate']+.001 for n,r in selected['baseline_development'][a]['folds'].items()}
        models=[best.base]
        if a=='kmeans':
            for latency,traffic,refweight in ((2,1,1),(4,1,1),(.5,1,1),(1,2,1),(1,1,.1),(2,1,.1)):
                for k in(8,16):
                    models.append(Setting(a,memory=True,clusters=k,geometry='radius',latency_weight=latency,traffic_weight=traffic,reference_weight=refweight))
        for i,value in enumerate(models):
            base=value if not isinstance(value,Setting)else Detector(value).fit(data['train'])
            nr=base.score(normal);raw={n:base.score(data[n]['frame'])for n in('search','confirmation')}
            modes=('global','availability','adapter_availability','availability_phase')+ (('cluster','cluster_availability')if a=='kmeans'else())
            for mode in modes:
                for alpha in((1.,)if mode=='global'else(.5,1.)):
                    for kind in('cdf','robust'):
                        if mode in('global','availability'):ref=NormalReference.fit(normal,nr,mode,alpha,kind)
                        else:ref=ContextReference.fit(base,normal,nr,mode,alpha,kind)
                        ns=ref.apply(normal,nr);ss={n:ref.apply(data[n]['frame'],raw[n])for n in raw};pooled=np.concatenate(list(ss.values()))
                        for q in(.90,.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995):
                            threshold=float(np.quantile(ns,q,method='higher'))
                            folds={n:_stats(data[n]['y'],ss[n],threshold)for n in raw}
                            eligible=all(folds[n]['normal_false_positive_rate']<=cap[n]for n in raw)
                            stats=_stats(y,pooled,threshold)
                            r={'settings':asdict(base.setting),'candidate':i,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':threshold,
                               'folds':folds,'eligible':eligible,**stats};records.append({'algorithm':a,**r})
                            rank=(stats['f1'],-stats['normal_false_positive_rate'])
                            if eligible and rank>key:best,key,record=CalibratedDetector(base,ref,threshold),rank,r
            write(OUT/'search_results.json',records);joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            write(OUT/'progress.json',{'algorithm':a,'candidate':i,'best':record})
            print(a,i,'best',round(key[0]*100,3),'FP',round(-key[1]*100,3),'mode',record['mode'],flush=True)
        pkg['models'][a]=best;selected['selected'][a]=record
    joblib.dump(pkg,OUT/'models.joblib',compress=3)
    selected.update({'model_sha256':sha256(OUT/'models.joblib'),'preceding_model_sha256':sha256(PARENT/'models.joblib'),
       'normal_context_score_tables_not_experts':True,'ADBench_not_used_for_selection':True})
    write(OUT/'selection.json',selected);write(OUT/'preparation.json',read(PARENT/'preparation.json'))
    print('Context refinement frozen',[(a,r['f1'])for a,r in selected['selected'].items()],flush=True)


if __name__=='__main__':tune()
