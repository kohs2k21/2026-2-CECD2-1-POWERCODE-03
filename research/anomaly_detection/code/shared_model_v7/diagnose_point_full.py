"""Full-case feature ablation, NOT a model release or untouched validation."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
from shared_model_v7.protocol import OUT,freeze,atomic_dump
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v6.search import curves


def run():
    freeze();reference=joblib.load(OUT/'neighbourhood_1.joblib')['reference'];truth=[];near=[];upper=[]
    index=read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372/individual_delay/index.json')
    for e in index:
        p=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372/individual_delay'/e['file']
        assert sha256(p)==read(p.with_suffix('.json'))['sha256'];pack=joblib.load(p);f=pack['frame']
        near.append(reference.transform(f)[:,0]);upper.append(f.current_source_upper.to_numpy(float));truth.append(np.asarray(pack['metadata']['truth'],bool))
        print('POINT full feature replay',e['file'],flush=True)
    y=np.concatenate(truth);n=np.concatenate(near);u=np.concatenate(upper);dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');cal=dev['normal_calibration']
    nr=reference.transform(cal)[:,0];ur=cal.current_source_upper.to_numpy(float);result=[]
    for ratio in(.25,.5,1.,2.):
        s=np.minimum(n,u*ratio);ns=np.minimum(nr,ur*ratio);q=np.r_[np.linspace(.8,.999,160),.9995,.9999,1.];threshold=np.quantile(ns,q,method='higher')
        rows=curves(y,s,threshold)
        for level,cut,r in zip(q,threshold,rows):result.append({'point_ratio':ratio,'normal_q':float(level),'normal_cutoff':float(cut),**r})
    eligible=[r for r in result if r['recall']>=.55 and r['precision']>=.986472 and r['normal_false_positive_rate']<.05]
    best=max(eligible,key=lambda r:r['f1'])if eligible else None
    atomic_dump({'y':y,'normal_nearest':n,'source_upper':u},OUT/'point_full_features.joblib')
    write(OUT/'point_full_diagnostic.json',{'scope':'feature ablation over all 5000 paired cases; NOT a fitted IF/K-means/LOF result','adaptive_research_not_external_blind_test':True,
        'rows':len(y),'positive':int(y.sum()),'negative':int((~y).sum()),'best_high_precision_55Recall':best,'curves':result})
    print('POINT FULL feature best',best,flush=True)

if __name__=='__main__':run()
