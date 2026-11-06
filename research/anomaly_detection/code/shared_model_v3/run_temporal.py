"""Joint F1/Recall optimization of causal score processing on development only."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v3.temporal_score import Detector,Setting

OUT=ROOT/'reports/shared_model_v3_temporal';BASE=ROOT/'reports/shared_model_v2_release'

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    d=joblib.load(ROOT/'reports/shared_model_v3_evidence/development.joblib');normal=d['normal_calibration']
    pkg=joblib.load(BASE/'models.joblib');candidate=joblib.load(ROOT/'reports/shared_model_v3_candidate1/models.joblib')
    folds=('search','confirmation');y=np.concatenate([d[k]['y']for k in folds]);records=[];chosen={}
    for a in ALGORITHMS:
        old=pkg['models'][a];bs={k:old.score(d[k]['frame'])for k in folds}
        bm=_stats(y,np.concatenate(list(bs.values())),old.threshold)
        caps={k:_stats(d[k]['y'],bs[k],old.threshold)['normal_false_positive_rate']for k in folds}
        best=old;key=(0.,bm['f1'],bm['recall']);record={'from_parent':True,'threshold':old.threshold,**bm}
        for source,base in (('v2_release',old),('v3_candidate1',candidate['models'][a])):
            ns=base.score(normal);raw={k:base.score(d[k]['frame'])for k in folds}
            center=float(np.median(ns));scale=max(float(np.quantile(ns,.95)-center),.05)
            settings=[Setting(decay=d,allowance=k,weight=w,hold=h)for d,k,w in((.5,.25,.5),(.8,.25,.5),(.9,.25,.5),(.8,.5,1.),(.9,.5,1.),(.95,.5,.5))for h in(0.,1.)]
            settings+=[Setting(decay=0.,weight=0.,hold=h)for h in(.5,1.,1.5)]
            for i,s in enumerate(settings):
                m=Detector(base,s,center,scale);n=m.filter(normal,ns)
                ss={k:m.filter(d[k]['frame'],raw[k])for k in folds}
                for q in(.95,.96,.97,.975,.98,.985,.99,.9925,.995,.9975):
                    t=float(np.quantile(n,q,method='higher'));fm={k:_stats(d[k]['y'],ss[k],t)for k in folds}
                    st=_stats(y,np.concatenate(list(ss.values())),t)
                    eligible=all(fm[k]['normal_false_positive_rate']<=caps[k]for k in folds)
                    r={'algorithm':a,'source':source,'candidate':i,'temporal':s.__dict__,'q':q,'threshold':t,'eligible':eligible,'folds':fm,**st}
                    records.append(r);rank=(min(st['f1']-bm['f1'],st['recall']-bm['recall']),st['f1'],st['recall'])
                    if eligible and rank>key:m.threshold=t;best,key,record=m,rank,r
                print(a,source,i,'best F1',round(record['f1']*100,2),'R',round(record['recall']*100,2),flush=True)
        pkg['models'][a]=best;chosen[a]=record
        joblib.dump(best,OUT/(a+'_selected.joblib'),compress=3);write(OUT/'search.json',records)
    pkg['adapter_reference']=candidate['adapter_reference'];joblib.dump(pkg,OUT/'models.joblib',compress=3)
    write(OUT/'selection.json',{'selected':chosen,'model_sha256':sha256(OUT/'models.joblib'),
        'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),'parent_sha256':sha256(BASE/'models.joblib'),
        'benchmark_modified':False,'point_adjustment':False,'single_estimator_each':True,'adapter_experts':False,
        'normal_only_estimator_fit':True,'development_labels_for_selection':True,'temporal_score_processing':True})
    print('Frozen temporal',chosen,flush=True)

if __name__=='__main__':run()
