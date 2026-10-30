"""Freeze normal-only Adapter/availability score tables, not expert estimators."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_v3.select import load

OUT=ROOT/'reports/shared_model_v3_calibration';BASE=ROOT/'reports/shared_model_v2_release'

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    d=joblib.load(ROOT/'reports/shared_model_v3_sensitive/development.joblib');normal=d['normal_calibration']
    pkg=joblib.load(BASE/'models.joblib');candidate=joblib.load(ROOT/'reports/shared_model_v3_candidate1/models.joblib')
    folds=('search','confirmation');y=np.concatenate([d[k]['y']for k in folds]);records=[];chosen={}
    for a in ALGORITHMS:
        old=pkg['models'][a];rawold={k:old.score(d[k]['frame'])for k in folds}
        bm=_stats(y,np.concatenate(list(rawold.values())),old.threshold)
        caps={k:_stats(d[k]['y'],rawold[k],old.threshold)['normal_false_positive_rate']for k in folds}
        best=old;key=(0.,bm['f1'],bm['recall']);record={'from_parent':True,'threshold':old.threshold,**bm}
        sources=[('v2_release',old.base),('v3_candidate1',candidate['models'][a].base)]
        folder=ROOT/'reports/shared_model_v3_sensitive'
        rows=read(folder/(a+'_search.json'));rows=[r for r in rows if r['eligible']]
        ids=[]
        for r in sorted(rows,key=lambda r:r['f1'],reverse=True):
            if r['candidate']not in ids:ids.append(r['candidate'])
            if len(ids)>=3:break
        sources += [('sensitive_'+str(i),load(folder/(a+'_candidate_'+str(i)+'.joblib')))for i in ids]
        for source,base in sources:
            nr=base.score(normal);raw={k:base.score(d[k]['frame'])for k in folds}
            for mode in('availability','adapter','adapter_availability','adapter_phase'):
                for alpha in(.5,1.):
                    for kind in('cdf','robust'):
                        ref=NormalReference.fit(normal,nr,mode,alpha,kind);ns=ref.apply(normal,nr)
                        ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                        for q in(.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995,.9975):
                            t=float(np.quantile(ns,q,method='higher'));fm={k:_stats(d[k]['y'],ss[k],t)for k in folds}
                            st=_stats(y,np.concatenate(list(ss.values())),t);eligible=all(fm[k]['normal_false_positive_rate']<=caps[k]for k in folds)
                            r={'algorithm':a,'source':source,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':t,'folds':fm,'eligible':eligible,**st}
                            records.append(r);rank=(min(st['f1']-bm['f1'],st['recall']-bm['recall']),st['f1'],st['recall'])
                            if eligible and rank>key:best,key,record=CalibratedDetector(base,ref,t),rank,r
            print(a,source,'best F1',round(record['f1']*100,2),'R',round(record['recall']*100,2),'FP',round(record['normal_false_positive_rate']*100,2),flush=True)
        pkg['models'][a]=best;chosen[a]=record;write(OUT/'search.json',records)
    pkg['adapter_reference']=candidate['adapter_reference'];joblib.dump(pkg,OUT/'models.joblib',compress=3)
    write(OUT/'selection.json',{'selected':chosen,'model_sha256':sha256(OUT/'models.joblib'),
        'parent_sha256':sha256(BASE/'models.joblib'),'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'normal_only_reference_fit':True,'development_labels_for_selection':True,'benchmark_modified':False,
        'single_estimator_each':True,'adapter_experts':False})
    print('Calibration frozen',chosen,flush=True)

if __name__=='__main__':run()
