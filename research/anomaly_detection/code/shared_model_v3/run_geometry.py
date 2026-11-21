"""Fixed-feature geometric ablation with joint F1/Recall and strict dev FPR caps."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_v3.geometry import Setting,Detector

OUT=ROOT/'reports/shared_model_v3_geometry';BASE=ROOT/'reports/shared_model_v2_release'

def candidates(a):
    if a=='if':
        for n in(32,64,128,512,1024):
            for f in(.25,.5,1.):yield Setting(a,samples=n,max_features=f)
    elif a=='kmeans':
        for r in(0.,.25,.5,.75,1.,1.25):
            for nearest in(False,True):yield Setting(a,radius_power=r,nearest=nearest)
    else:
        for metric in('manhattan','chebyshev','cosine'):
            for k in(50,100,400,800):yield Setting(a,metric=metric,neighbors=k)

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Frozen')
    d=joblib.load(ROOT/'reports/shared_model_v2/development.joblib');normal=d['normal_calibration']
    pkg=joblib.load(BASE/'models.joblib');folds=('search','confirmation');y=np.concatenate([d[k]['y']for k in folds]);chosen={};records=[]
    for a in ALGORITHMS:
        old=pkg['models'][a];rawold={k:old.score(d[k]['frame'])for k in folds}
        bm=_stats(y,np.concatenate(list(rawold.values())),old.threshold)
        caps={k:_stats(d[k]['y'],rawold[k],old.threshold)['normal_false_positive_rate']for k in folds}
        best=old;key=(0.,bm['f1'],bm['recall']);record={'from_parent':True,'threshold':old.threshold,**bm}
        for i,s in enumerate(candidates(a)):
            m=Detector(old.base,s).fit(d['train']);nr=m.score(normal);raw={k:m.score(d[k]['frame'])for k in folds}
            joblib.dump(m,OUT/(a+'_candidate_'+str(i)+'.joblib'),compress=3)
            for mode,alpha in(('global',1.),('availability',.5),('availability',1.),('adapter_availability',.5)):
                for kind in('cdf','robust'):
                    ref=NormalReference.fit(normal,nr,mode,alpha,kind);ns=ref.apply(normal,nr)
                    ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                    for q in(.95,.96,.97,.975,.98,.985,.99,.9925,.995,.9975):
                        t=float(np.quantile(ns,q,method='higher'));fm={k:_stats(d[k]['y'],ss[k],t)for k in folds}
                        st=_stats(y,np.concatenate(list(ss.values())),t);eligible=all(fm[k]['normal_false_positive_rate']<=caps[k]for k in folds)
                        r={'algorithm':a,'candidate':i,'settings':s.__dict__,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':t,'folds':fm,'eligible':eligible,**st}
                        records.append(r);rank=(min(st['f1']-bm['f1'],st['recall']-bm['recall']),st['f1'],st['recall'])
                        if eligible and rank>key:best,key,record=CalibratedDetector(m,ref,t),rank,r
            write(OUT/(a+'_search.json'),[r for r in records if r['algorithm']==a])
            joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            print(a,i,s.metric,'best F1',round(record['f1']*100,2),'R',round(record['recall']*100,2),'FP',round(record['normal_false_positive_rate']*100,2),flush=True)
        pkg['models'][a]=best;chosen[a]=record
    joblib.dump(pkg,OUT/'models.joblib',compress=3)
    write(OUT/'selection.json',{'selected':chosen,'model_sha256':sha256(OUT/'models.joblib'),
        'parent_sha256':sha256(BASE/'models.joblib'),'benchmark_manifest_sha256':sha256(ROOT/'data/evaluation/manifest.json'),
        'normal_only_estimator_fit':True,'development_labels_for_selection':True,'benchmark_modified':False,
        'single_estimator_each':True,'adapter_experts':False,'fixed_features_changed_geometry':True})
    print('Geometry frozen',chosen,flush=True)

if __name__=='__main__':run()
