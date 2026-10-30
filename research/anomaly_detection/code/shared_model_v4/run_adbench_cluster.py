from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from shared_model_v4.adbench_training import build,OUT as PARENT,FLAGS
from shared_model_v4.adbench_cluster import Setting,Detector
from shared_model_v4.calibrate_adbench import upper_bound,standardized_f1
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import _stats

OUT=PARENT/'assigned_cluster'

def run():
    OUT.mkdir(exist_ok=True)
    if(OUT/'selection.json').exists():raise FileExistsError('Assigned cluster already selected')
    d=build();y=d['development'].label.to_numpy(bool);records=[];best=(-1.,0.);winner=None
    # Encoders were fitted on the same disjoint normal train; no test refit.
    encoders=[('raw',PARENT/'kmeans_candidate_16.joblib'),('raw_white',PARENT/'kmeans_candidate_0.joblib'),
        ('rank',PARENT/'quantile/kmeans_candidate_0.joblib'),('hybrid',PARENT/'quantile/kmeans_candidate_1.joblib')]
    for name,path in encoders:
        encoder=joblib.load(path).encoder
        for k in (4,16,64,128):
            for mode,power in (('nearest',0.),('nearest',.5),('nearest',1.),('mahalanobis',0.)):
                s=Setting(k,mode,power);model=Detector(s,encoder).fit(d['train']);ns=model.score(d['calibration']);ds=model.score(d['development'])
                candidate=f'{name}_{k}_{mode}_{power}.joblib';joblib.dump(model,OUT/candidate,compress=3)
                ts=[(q,1.,float(np.quantile(ns,q,method='higher')))for q in (.985,.99,.9925,.995,.9975,.999,1.)]
                ts += [(1.,m,float(ns.max()*m))for m in (1.1,1.25,1.5,2.)]
                for q,margin,t in ts:
                    r={'method':'kmeans','encoder_source':str(path.relative_to(ROOT)),'candidate_file':candidate,'settings':s.__dict__,'threshold_quantile':q,'threshold_margin':margin,'threshold':t,**_stats(y,ds,t)}
                    r['normal_FPR_UCB95']=upper_bound(r['fp'],r['normal']);r['eligible']=r['normal_FPR_UCB95']<=6/800
                    records.append(r);key=(standardized_f1(r),-r['normal_false_positive_rate'])
                    if r['eligible']and key>best:best=key;winner=r
                print('AD assigned cluster',candidate,'dev best',best,flush=True)
        write(OUT/'search.json',records);write(OUT/'winner_so_far.json',winner)
    model=joblib.load(OUT/winner['candidate_file']);model.threshold=winner['threshold'];joblib.dump(model,OUT/'kmeans_specific.joblib',compress=3)
    write(OUT/'selection.json',{'selected':winner,'model_sha256':sha256(OUT/'kmeans_specific.joblib'),'test_quantile_inversion':False,'adaptive_not_blind':True,
        'policy':'95% exact normal FPR upper bound <= 0.75%; 20%-prevalence standardized F1; normal cal quantiles/margins'})
    z=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(z['X'],columns=z['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=z['sample_id'];scores=model.score(f);r={'method':'kmeans',**_stats(z['y'],scores,model.threshold)}
    r.update({'F1_target_met':r['f1']>=.6,'FP_cap':6,'FP_cap_met':r['fp']<=6,'threshold':model.threshold,
        'by_mode':{m:_stats(z['y'][z['generation_mode']==m],scores[z['generation_mode']==m],model.threshold)for m in ('global','local','cluster','dependency')}})
    write(OUT/'results.json',{'results':[r],'ADBench_specific_retrained':True,'same_service_model_transfer':False,'test_points_fit':0,'fixed_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz')})
    np.savez_compressed(OUT/'scores.npz',y=z['y'],generation_mode=z['generation_mode'],sample_id=z['sample_id'],kmeans=scores)
    print('AD ASSIGNED CLUSTER FIXED',r,flush=True)

if __name__=='__main__':run()
