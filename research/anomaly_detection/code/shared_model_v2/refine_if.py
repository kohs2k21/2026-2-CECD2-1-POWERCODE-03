"""IF coherence/robust-fit ablation, selected on development labels only."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from dataclasses import asdict
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import read,write
from shared_model_v2.run import prepare
from shared_model_v2.models import Detector,Setting
from shared_model_v2.context_calibration import ContextReference
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats

OUT=ROOT/'reports/shared_model_v2_if'


def tune():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    data=prepare();normal=data['normal_calibration'];parent=ROOT/'reports/shared_model_target_lof'
    best=joblib.load(parent/'models.joblib')['models']['if'];record=read(parent/'selection.json')['selected']['if']
    key=(record['f1'],-record['normal_false_positive_rate']);records=[]
    y=np.concatenate([data[n]['y']for n in('search','confirmation')])
    baseline=read(ROOT/'reports/shared_model_v2/selection.json')['baseline_development']['if']
    cap=baseline['normal_false_positive_rate']
    settings=[Setting('if',memory=False,interactions=True,whitening=True,rotation=True,samples=s,trim=t)for s in(128,256,512)for t in(0.,.01)]
    settings += [Setting('if',memory=True,memory_weight=.1,interactions=True,whitening=True,rotation=True,samples=s,trim=t)for s in(256,512)for t in(0.,.01)]
    for i,setting in enumerate(settings):
        base=Detector(setting).fit(data['train']);ns=base.score(normal);raw={n:base.score(data[n]['frame'])for n in('search','confirmation')}
        for mode in('global','availability','availability_phase'):
            for alpha in((1.,)if mode=='global'else(.25,.5,1.)):
                for kind in('cdf','robust'):
                    ref=NormalReference.fit(normal,ns,mode,alpha,kind)if mode in('global','availability')else ContextReference.fit(base,normal,ns,mode,alpha,kind)
                    nr=ref.apply(normal,ns);ss={n:ref.apply(data[n]['frame'],raw[n])for n in raw};pooled=np.concatenate(list(ss.values()))
                    for q in(.95,.96,.97,.975,.98,.985,.9875,.99,.9925,.995):
                        threshold=float(np.quantile(nr,q,method='higher'));folds={n:_stats(data[n]['y'],ss[n],threshold)for n in raw};stats=_stats(y,pooled,threshold)
                        eligible=stats['normal_false_positive_rate']<=cap and all(folds[n]['normal_false_positive_rate']<=baseline['folds'][n]['normal_false_positive_rate']+.01 for n in raw)
                        r={'settings':asdict(setting),'candidate':i,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':threshold,'folds':folds,'eligible':eligible,**stats};records.append(r)
                        rank=(stats['f1'],-stats['normal_false_positive_rate'])
                        if eligible and rank>key:best,key,record=CalibratedDetector(base,ref,threshold),rank,r
        joblib.dump(best,OUT/'if_selected.joblib',compress=3);write(OUT/'search_results.json',records)
        write(OUT/'progress.json',{'candidate':i,'best':record});print('IF',i,'best',round(key[0]*100,3),'FP',round(-key[1]*100,3),flush=True)
    write(OUT/'selection.json',{'selected':record,'model_sha256':sha256(OUT/'if_selected.joblib'),
       'development_selection_only':True,'ADBench_not_used':True,'original_benchmark_not_modified':True})


if __name__=='__main__':tune()
