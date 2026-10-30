"""Train-only rank features; development selection with frozen FPR constraints."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import write
from shared_model_v4.rank_model import Setting,Detector
from shared_model_v4.rank_context import Context,RANK
from model_evaluation.data import history_lookup
from shared_model_target.calibration import CalibratedDetector,NormalReference
from shared_model_improvement.run import ALGORITHMS,_stats

OUT=ROOT/'reports/shared_model_v4_rank';BASE=ROOT/'reports/shared_model_v3_release'

def prepare():
    path=OUT/'development.joblib'
    if path.exists():return joblib.load(path)
    d=joblib.load(ROOT/'reports/shared_model_v3_sensitive/development.joblib');history=history_lookup(ROOT/'data/evaluation')
    context=Context(history,6)
    for key in ('train','normal_calibration','search','confirmation'):
        f=d[key] if isinstance(d[key],pd.DataFrame)else d[key]['frame']
        f=f.drop(columns=list(RANK),errors='ignore');frame=pd.concat([f,context.transform(f)],axis=1)
        if isinstance(d[key],pd.DataFrame):d[key]=frame
        else:d[key]['frame']=frame
        print('rank feature cache',key,len(frame),flush=True)
    d['rank_history']=history;joblib.dump(d,path,compress=3);return d

def candidates(a):
    for view in ('compact','minimal','sequence','all'):
        for trans in ('linear','asinh'):
            for white in (False,True):
                yield Setting(a,rank_view=view,transform=trans,whitening=white,trim=0.,samples=64,clusters=8,neighbors=200)
    if a=='if':
        for n in (32,256,1024):yield Setting(a,rank_view='compact',samples=n,trim=0.)
    elif a=='kmeans':
        for k in (2,32,64):yield Setting(a,rank_view='compact',clusters=k,trim=0.)
    else:
        for k in (50,400,800):yield Setting(a,rank_view='compact',neighbors=k,reference_cap=4096,trim=0.)

def run(algorithms):
    OUT.mkdir(exist_ok=True);d=prepare();folds=('search','confirmation');cal=d['normal_calibration']
    y=np.concatenate([d[k]['y']for k in folds]);old=joblib.load(BASE/'models.joblib')['models']
    for a in algorithms:
        if (OUT/(a+'_selected.joblib')).exists():raise FileExistsError('Search already complete')
        caps={k:_stats(d[k]['y'],old[a].score(d[k]['frame']),old[a].threshold)['normal_false_positive_rate']for k in folds}
        best=None;rank=(-1.,0.);record=None;records=[]
        for i,s in enumerate(candidates(a)):
            base=Detector(s,d['rank_history']).fit(d['train']);nr=base.score(cal);raw={k:base.score(d[k]['frame'])for k in folds}
            joblib.dump(base,OUT/(a+'_candidate_'+str(i)+'.joblib'),compress=3)
            for mode in ('availability','adapter_availability','global'):
                for alpha in (.5,1.):
                    for kind in ('cdf','robust'):
                        ref=NormalReference.fit(cal,nr,mode,alpha,kind);ns=ref.apply(cal,nr)
                        ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                        for q in (.90,.925,.95,.975,.98,.985,.99,.9925,.995,.9975):
                            t=float(np.quantile(ns,q,method='higher'));fm={k:_stats(d[k]['y'],ss[k],t)for k in folds}
                            st=_stats(y,np.concatenate(list(ss.values())),t);eligible=all(fm[k]['normal_false_positive_rate']<=caps[k]for k in folds)
                            r={'algorithm':a,'candidate':i,'settings':s.__dict__,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':t,'folds':fm,'eligible':eligible,**st};records.append(r)
                            key=(st['f1'],-st['normal_false_positive_rate'])
                            if eligible and key>rank:best,rank,record=CalibratedDetector(base,ref,t),key,r
            write(OUT/(a+'_search.json'),records);write(OUT/(a+'_selection.json'),record)
            joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            print(a,i,s.rank_view,s.transform,s.whitening,'bestF1',round(record['f1']*100,2),'R',round(record['recall']*100,2),'FP',round(record['normal_false_positive_rate']*100,2),flush=True)
        joblib.dump(best,OUT/(a+'_selected.joblib'),compress=3)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
