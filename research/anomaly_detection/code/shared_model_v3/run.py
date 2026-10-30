"""Select using existing development folds; final labels are not read here."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from dataclasses import asdict,replace
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import read,write
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_v3.features import augment,SequenceReference
from shared_model_v3.models import Setting,Detector

OUT=ROOT/'reports/shared_model_v3';BASE=ROOT/'reports/shared_model_v2_release'

def prepare():
    OUT.mkdir(exist_ok=True);path=OUT/'development.joblib'
    if path.exists():
        assert sha256(path)==read(OUT/'preparation.json')['sha256'];return joblib.load(path)
    source=ROOT/'reports/shared_model_v2/development.joblib';d=joblib.load(source)
    for key in ('train','normal_calibration'):d[key]=augment(d[key])
    for key in ('search','confirmation'):d[key]['frame']=augment(d[key]['frame'])
    d['adapter_reference']=SequenceReference(d['adapter_reference'])
    joblib.dump(d,path,compress=3)
    write(OUT/'preparation.json',{'sha256':sha256(path),'parent_sha256':sha256(source),
        'feature_sha256':sha256(ROOT/'code/shared_model_v3/features.py')})
    return d

def candidates(a):
    for view in ('compact','sequence','minimal','legacy'):
        for transform in ('asinh','hybrid'):
            yield Setting(a,view=view,transform=transform)
    for weight in (.1,.5,1.):yield Setting(a,view='compact',adapter_weight=weight)
    if a=='if':
        for view in ('compact','sequence','legacy'):
            for samples in (128,512,1024):yield Setting(a,view=view,whitening=True,rotation=True,samples=samples)
    elif a=='kmeans':
        for k in (2,4,16,32):
            for view in ('compact','legacy'):yield Setting(a,view=view,clusters=k)
    else:
        for cap,k in ((2048,50),(2048,100),(2048,500),(4096,500),(4096,1000)):
            for view in ('compact','legacy'):yield Setting(a,view=view,reference_cap=cap,neighbors=k)

def tune(algorithms):
    if any((OUT/(a+'_selected.joblib')).exists()for a in algorithms):
        raise FileExistsError('Completed experiment is preserved; use a new output folder')
    d=prepare();normal=d['normal_calibration'];pkg=joblib.load(BASE/'models.joblib')
    folds=('search','confirmation');y=np.concatenate([d[k]['y']for k in folds])
    baseline={};selected={};records=[]
    for a in algorithms:
        model=pkg['models'][a];before={k:_stats(d[k]['y'],model.score(d[k]['frame']),model.threshold)for k in folds}
        baseline[a]=before;cap={k:before[k]['normal_false_positive_rate']for k in folds}
        key=(0.,0.);best=None;bestrecord=None
        for i,s in enumerate(candidates(a)):
            path=OUT/(a+'_candidate_'+str(i)+'.joblib')
            if path.exists():base=joblib.load(path)
            else:base=Detector(s).fit(d['train']);joblib.dump(base,path,compress=3)
            nr=base.score(normal);raw={k:base.score(d[k]['frame'])for k in folds}
            for mode,alpha in (('global',1.),('availability',.5),('availability',1.)):
                for kind in ('cdf','robust'):
                    ref=NormalReference.fit(normal,nr,mode,alpha,kind);ns=ref.apply(normal,nr)
                    ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                    for q in (.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995,.9975):
                        t=float(np.quantile(ns,q,method='higher'))
                        fm={k:_stats(d[k]['y'],ss[k],t)for k in folds}
                        eligible=all(fm[k]['normal_false_positive_rate']<=cap[k]for k in folds)
                        st=_stats(y,np.concatenate(list(ss.values())),t)
                        r={'algorithm':a,'candidate':i,'settings':asdict(s),'mode':mode,'alpha':alpha,
                            'kind':kind,'q':q,'threshold':t,'folds':fm,'eligible':eligible,**st}
                        records.append(r);rank=(st['f1'],-st['normal_false_positive_rate'])
                        if eligible and rank>key:
                            best,key,bestrecord=CalibratedDetector(base,ref,t),rank,r
            joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            write(OUT/(a+'_search.json'),[r for r in records if r['algorithm']==a])
            write(OUT/(a+'_selection.json'),bestrecord)
            print(a,i,s.view,s.transform,'best F1',round(key[0]*100,2),
                'R',round(bestrecord['recall']*100,2),'FP',round(-key[1]*100,2),flush=True)
        selected[a]=bestrecord
        joblib.dump(best,OUT/(a+'_selected.joblib'),compress=3)
    print('DONE',selected,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));a=p.parse_args()
    tune(a.algorithms)
