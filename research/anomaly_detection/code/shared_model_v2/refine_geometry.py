"""Small-cluster and coherent-feature candidates, with pooled baseline FPR cap."""
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

PARENT=ROOT/'reports/shared_model_v2_context'
OUT=ROOT/'reports/shared_model_v2_geometry'


def candidates(a):
    if a=='kmeans':
        for k in(1,2,4,8):
            for memory in(False,True):yield Setting(a,memory=memory,clusters=k,geometry='radius')
        for k in(4,8,16,32):
            yield Setting(a,clusters=k,interactions=True,geometry='radius')
            yield Setting(a,clusters=k,interactions=True,geometry='nearest')
        for k in(2,4,8):yield Setting(a,clusters=k,interactions=True,trim=.01,geometry='radius')
    else:
        for k in(300,600,900):
            for interact in(False,True):yield Setting(a,memory=False,neighbors=k,reference_cap=2048,interactions=interact)
        yield Setting(a,memory=False,neighbors=500,reference_cap=1024)


def tune():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    data=prepare();normal=data['normal_calibration'];selected=read(PARENT/'selection.json');pkg=joblib.load(PARENT/'models.joblib')
    y=np.concatenate([data[n]['y']for n in('search','confirmation')]);records=[]
    for a in('kmeans','lof'):
        best=pkg['models'][a];record=selected['selected'][a];key=(record['f1'],-record['normal_false_positive_rate'])
        baseline=selected['baseline_development'][a];cap={n:r['normal_false_positive_rate']+.01 for n,r in baseline['folds'].items()}
        for i,setting in enumerate(candidates(a)):
            base=Detector(setting).fit(data['train']);nr=base.score(normal);raw={n:base.score(data[n]['frame'])for n in('search','confirmation')}
            modes=('global','availability','availability_phase')+(('cluster',)if a=='kmeans'else())
            for mode in modes:
                for alpha in((1.,)if mode=='global'else(.5,1.)):
                    for kind in('cdf','robust'):
                        ref=NormalReference.fit(normal,nr,mode,alpha,kind)if mode in('global','availability')else ContextReference.fit(base,normal,nr,mode,alpha,kind)
                        ns=ref.apply(normal,nr);ss={n:ref.apply(data[n]['frame'],raw[n])for n in raw};pooled=np.concatenate(list(ss.values()))
                        for q in(.90,.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995):
                            threshold=float(np.quantile(ns,q,method='higher'));folds={n:_stats(data[n]['y'],ss[n],threshold)for n in raw};stats=_stats(y,pooled,threshold)
                            eligible=stats['normal_false_positive_rate']<=baseline['normal_false_positive_rate']and all(folds[n]['normal_false_positive_rate']<=cap[n]for n in raw)
                            r={'settings':asdict(setting),'candidate':i,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':threshold,'folds':folds,'eligible':eligible,**stats}
                            records.append({'algorithm':a,**r});rank=(stats['f1'],-stats['normal_false_positive_rate'])
                            if eligible and rank>key:best,key,record=CalibratedDetector(base,ref,threshold),rank,r
            write(OUT/'search_results.json',records);joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            write(OUT/'progress.json',{'algorithm':a,'candidate':i,'best':record})
            print(a,i,'k',setting.clusters if a=='kmeans'else setting.neighbors,'interact',setting.interactions,'best',round(key[0]*100,3),'FP',round(-key[1]*100,3),flush=True)
        pkg['models'][a]=best;selected['selected'][a]=record
    joblib.dump(pkg,OUT/'models.joblib',compress=3)
    selected.update({'model_sha256':sha256(OUT/'models.joblib'),'preceding_model_sha256':sha256(PARENT/'models.joblib'),
       'pooled_development_FPR_no_higher_than_original':True,'fold_FPR_tolerance_absolute':.01,
       'ADBench_not_used_for_selection':True})
    write(OUT/'selection.json',selected);write(OUT/'preparation.json',read(PARENT/'preparation.json'))
    print('Geometry refinement frozen',[(a,r['f1'])for a,r in selected['selected'].items()],flush=True)


if __name__=='__main__':tune()
