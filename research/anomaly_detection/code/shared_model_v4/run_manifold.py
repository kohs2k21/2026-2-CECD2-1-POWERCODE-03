"""Normal-only covariance geometry, selected using old log development folds."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_v4.manifold import Setting,Detector

OUT=ROOT/'reports/shared_model_v4_manifold';BASE=ROOT/'reports/shared_model_v3_release'

def candidates(a):
    for context in('global','availability','adapter'):
        for trans in('linear','asinh'):
            for floor in(.0001,.01):yield Setting(a,context=context,transform=trans,floor=floor)
    for mode in('radius','both'):
        for shrink in(.0,.5):yield Setting(a,mode=mode,shrinkage=shrink)
    if a=='if':
        for n in(32,1024):yield Setting(a,samples=n)
    elif a=='kmeans':
        for k in(4,64):yield Setting(a,clusters=k)
    else:
        for k in(35,600):yield Setting(a,neighbors=k)

def run(algorithms):
    OUT.mkdir(exist_ok=True);d=joblib.load(ROOT/'reports/shared_model_v2/development.joblib')
    normal=d['normal_calibration'];folds=('search','confirmation');y=np.concatenate([d[k]['y']for k in folds])
    # v3 KM needs extra causal columns; only for measuring the previous dev cap.
    extra=joblib.load(ROOT/'reports/shared_model_v3_sensitive/development.joblib')
    pkg=joblib.load(BASE/'models.joblib');records=[]
    for a in algorithms:
        if(OUT/(a+'_selected.joblib')).exists():raise FileExistsError('Preserved completed search')
        old=pkg['models'][a];before={k:_stats(d[k]['y'],old.score(extra[k]['frame']),old.threshold)for k in folds}
        caps={k:before[k]['normal_false_positive_rate']for k in folds}
        best=None;key=(-1.,0.);record=None
        for i,s in enumerate(candidates(a)):
            base=Detector(s).fit(d['train']);nr=base.score(normal);raw={k:base.score(d[k]['frame'])for k in folds}
            joblib.dump(base,OUT/(a+'_candidate_'+str(i)+'.joblib'),compress=3)
            for mode,alpha in(('global',1.),('availability',.5),('availability',1.),('adapter_availability',.5)):
                for kind in('cdf','robust'):
                    ref=NormalReference.fit(normal,nr,mode,alpha,kind);ns=ref.apply(normal,nr)
                    ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                    for q in(.95,.975,.98,.985,.99,.9925,.995,.9975):
                        t=float(np.quantile(ns,q,method='higher'));fm={k:_stats(d[k]['y'],ss[k],t)for k in folds}
                        st=_stats(y,np.concatenate(list(ss.values())),t);eligible=all(fm[k]['normal_false_positive_rate']<=caps[k]for k in folds)
                        r={'algorithm':a,'candidate':i,'settings':s.__dict__,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':t,'folds':fm,'eligible':eligible,**st};records.append(r)
                        rank=(st['f1'],-st['normal_false_positive_rate'])
                        if eligible and rank>key:best,key,record=CalibratedDetector(base,ref,t),rank,r
            write(OUT/(a+'_search.json'),[r for r in records if r['algorithm']==a])
            joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3);write(OUT/(a+'_selection.json'),record)
            print(a,i,s.context,s.transform,'best F1',round(record['f1']*100,2),'R',round(record['recall']*100,2),'FP',round(record['normal_false_positive_rate']*100,2),flush=True)
        joblib.dump(best,OUT/(a+'_selected.joblib'),compress=3)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
