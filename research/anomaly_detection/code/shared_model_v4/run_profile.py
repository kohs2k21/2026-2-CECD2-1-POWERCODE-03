"""Expand source coverage, then fit one shared estimator with optional context."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from shared_model_v4.stratified_data import prepare
from shared_model_v4.profile_model import Setting,Detector
from shared_model_v4.native_calibration import NativeReference,both_views
from shared_model_target.calibration import CalibratedDetector
from shared_model_improvement.run import ALGORITHMS,_stats

OUT=ROOT/'reports/shared_model_v4_profile';BASE=ROOT/'reports/shared_model_v3_release'

def candidates(a):
    for view in('compact','source','all'):
        for trans in('asinh','linear'):
            for weight in(.5,2.):yield Setting(a,view=view,transform=trans,matched_weight=weight)
    if a=='if':
        for n in(64,1024):yield Setting(a,samples=n,whitening=True)
    elif a=='kmeans':
        for k in(4,64):yield Setting(a,view='compact',clusters=k)
    else:
        for k in(50,800):yield Setting(a,view='compact',neighbors=k)

def run(algorithms):
    OUT.mkdir(exist_ok=True);normal=prepare();dev=joblib.load(ROOT/'reports/shared_model_v3_sensitive/development.joblib')
    cal=both_views(pd.concat([normal['calibration'],normal['threshold']],ignore_index=True))
    folds=('search','confirmation');y=np.concatenate([dev[k]['y']for k in folds]);pkg=joblib.load(BASE/'models.joblib');records=[]
    for a in algorithms:
        if(OUT/(a+'_selected.joblib')).exists():raise FileExistsError('Completed search preserved')
        old=pkg['models'][a];caps={k:_stats(dev[k]['y'],old.score(dev[k]['frame']),old.threshold)['normal_false_positive_rate']for k in folds}
        best=None;key=(-1.,0.);record=None
        for i,s in enumerate(candidates(a)):
            base=Detector(s).fit(normal['train']);nr=base.score(cal);raw={k:base.score(dev[k]['frame'])for k in folds}
            joblib.dump(base,OUT/(a+'_candidate_'+str(i)+'.joblib'),compress=3)
            for mode in('native_availability','native_full','native'):
                for alpha in(.5,1.):
                    for kind in('cdf','robust'):
                        ref=NativeReference.fit(cal,nr,mode,alpha,kind);ns=ref.apply(cal,nr)
                        ss={k:ref.apply(dev[k]['frame'],raw[k])for k in folds}
                        for q in(.95,.975,.98,.985,.99,.9925,.995,.9975):
                            t=float(np.quantile(ns,q,method='higher'));fm={k:_stats(dev[k]['y'],ss[k],t)for k in folds}
                            st=_stats(y,np.concatenate(list(ss.values())),t);eligible=all(fm[k]['normal_false_positive_rate']<=caps[k]for k in folds)
                            r={'algorithm':a,'candidate':i,'settings':s.__dict__,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':t,'folds':fm,'eligible':eligible,**st};records.append(r)
                            rank=(st['f1'],-st['normal_false_positive_rate'])
                            if eligible and rank>key:best,key,record=CalibratedDetector(base,ref,t),rank,r
            write(OUT/(a+'_search.json'),[r for r in records if r['algorithm']==a]);joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            write(OUT/(a+'_selection.json'),record)
            print(a,i,s.view,s.transform,'best F1',round(record['f1']*100,2),'R',round(record['recall']*100,2),'FP',round(record['normal_false_positive_rate']*100,2),flush=True)
        joblib.dump(best,OUT/(a+'_selected.joblib'),compress=3)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
