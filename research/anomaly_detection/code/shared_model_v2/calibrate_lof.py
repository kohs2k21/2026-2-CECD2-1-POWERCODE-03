"""LOF normal-score cutoff sensitivity within a prespecified dev FPR allowance."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from dataclasses import asdict
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v2.run import prepare
from shared_model_v2.context_calibration import ContextReference
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats

OUT=ROOT/'reports/shared_model_v2_lof_calibrated'


def tune():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    data=prepare();normal=data['normal_calibration'];base_protocol=read(ROOT/'reports/shared_model_v2_reference/selection.json')
    baseline=base_protocol['baseline_development']['lof'];cap=baseline['normal_false_positive_rate']+.003
    bank=[joblib.load(ROOT/'reports/shared_model_v2_lof_geometry/lof_selected.joblib').base,
          joblib.load(ROOT/'reports/shared_model_v2_reference/models.joblib')['models']['lof'].base,
          joblib.load(ROOT/'reports/shared_model_v2_geometry/models.joblib')['models']['lof'].base]
    best=None;record=None;key=None;records=[];y=np.concatenate([data[n]['y']for n in('search','confirmation')])
    for i,base in enumerate(bank):
        ns=base.score(normal);raw={n:base.score(data[n]['frame'])for n in('search','confirmation')}
        for mode in('global','availability','availability_phase'):
            for alpha in((1.,)if mode=='global'else(.25,.5,.75,1.)):
                for kind in('cdf','robust'):
                    ref=NormalReference.fit(normal,ns,mode,alpha,kind)if mode in('global','availability')else ContextReference.fit(base,normal,ns,mode,alpha,kind)
                    nr=ref.apply(normal,ns);ss={n:ref.apply(data[n]['frame'],raw[n])for n in raw};pooled=np.concatenate(list(ss.values()))
                    for q in(.97,.975,.98,.9825,.985,.98625,.9875,.98875,.99,.99125,.9925,.995):
                        threshold=float(np.quantile(nr,q,method='higher'));folds={n:_stats(data[n]['y'],ss[n],threshold)for n in raw};stats=_stats(y,pooled,threshold)
                        eligible=stats['normal_false_positive_rate']<=cap and all(folds[n]['normal_false_positive_rate']<=baseline['folds'][n]['normal_false_positive_rate']+.01 for n in raw)
                        r={'settings':asdict(base.setting),'candidate':i,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':threshold,'folds':folds,'eligible':eligible,**stats};records.append(r)
                        rank=(stats['f1'],-stats['normal_false_positive_rate'])
                        if eligible and(key is None or rank>key):best,key,record=CalibratedDetector(base,ref,threshold),rank,r
        joblib.dump(best,OUT/'lof_selected.joblib',compress=3);write(OUT/'search_results.json',records)
        write(OUT/'progress.json',{'candidate':i,'best':record,'development_FPR_cap':cap})
        print('LOF cutoff',i,'best',round(key[0]*100,3),'FP',round(-key[1]*100,3),flush=True)
    write(OUT/'selection.json',{'selected':record,'model_sha256':sha256(OUT/'lof_selected.joblib'),
       'development_selection_only':True,'development_FPR_allowance_above_parent':.003,
       'prior_final_results_informed_research':True,'ADBench_not_used':True,'original_benchmark_not_modified':True})


if __name__=='__main__':tune()
