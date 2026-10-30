"""Normal-only estimator fitting, disjoint development model/threshold choice.

Fixed test data are scored once after selection, never used in the search loop.
This is deliberately separate from operational log model artifacts.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,dataclasses
import joblib
import numpy as np
import pandas as pd
from shared_model_v4.adbench_training import build,OUT,COLUMNS,FLAGS
from shared_model_v4.manifold import Setting,Detector
from model_research_v13.data import read,write
from shared_model_improvement.run import ALGORITHMS,_stats
from model_evaluation.data import sha256

def candidates(a):
    for shrinkage in (0.,.01,.1):
        for mode in ('white','radius','both'):
            for trans in ('linear','asinh'):
                yield Setting(a,transform=trans,shrinkage=shrinkage,mode=mode,floor=.0001,summary=True,samples=256,clusters=16,neighbors=50)
    if a=='if':
        for n in (32,1024):yield Setting(a,mode='radius',shrinkage=.01,samples=n)
    elif a=='kmeans':
        for k in (1,4,64):yield Setting(a,mode='white',shrinkage=.01,clusters=k)
    else:
        for k in (10,200,800):yield Setting(a,mode='white',shrinkage=.01,neighbors=k)

def run(algorithms):
    d=build();dev=d['development'];y=dev.label.to_numpy(bool);normal=~y
    protocol=read(ROOT/'reports/shared_model_v4/protocol.json');caps=protocol['adbench_false_positive_caps'];selected={};records=[]
    for a in algorithms:
        if (OUT/(a+'_selected.joblib')).exists():raise FileExistsError('Completed selection preserved')
        best=None;key=(-1.,0.);record=None
        for i,s in enumerate(candidates(a)):
            base=Detector(s).fit(d['train']);ns=base.score(d['calibration']);scores=base.score(dev)
            joblib.dump(base,OUT/(a+'_candidate_'+str(i)+'.joblib'),compress=3)
            for q in (.95,.975,.98,.985,.99,.9925,.995,.9975,.999,1.):
                t=float(np.quantile(ns,q,method='higher'));st=_stats(y,scores,t)
                eligible=st['normal_false_positive_rate']<=caps[a]/800
                r={'method':a,'candidate':i,'settings':dataclasses.asdict(s),'threshold_quantile':q,'threshold':t,'eligible':eligible,**st};records.append(r)
                rank=(st['f1'],-st['normal_false_positive_rate'])
                if eligible and rank>key:base.threshold=t;best,record,key=__import__('copy').deepcopy(base),r,rank
            write(OUT/(a+'_search.json'),[r for r in records if r['method']==a]);write(OUT/(a+'_selection.json'),record)
            print('ADBench-specific',a,i,'dev best',round(record['f1']*100,2),'R',round(record['recall']*100,2),'FP',round(record['normal_false_positive_rate']*100,2),flush=True)
        joblib.dump(best,OUT/(a+'_selected.joblib'),compress=3);selected[a]=record
    # Model and threshold choices have now ended. Independent 1,000 fixed points.
    values=np.load(ROOT/'data/adbench_1000/dataset.npz');f=pd.DataFrame(values['X'],columns=values['columns'])
    for c in FLAGS:f[c]=f[c].astype(bool)
    f['adapterType']='ADBENCH_GLOBAL_CONTEXT';f['case_id']=values['sample_id'];rows=[];scores={}
    for a in algorithms:
        model=joblib.load(OUT/(a+'_selected.joblib'));s=model.score(f);scores[a]=s
        st=_stats(values['y'],s,model.threshold)
        rows.append({'method':a,**st,'threshold':model.threshold,'F1_target_met':st['f1']>=.6,'FP_cap':caps[a],'FP_cap_met':st['fp']<=caps[a],
            'by_mode':{m:_stats(values['y'][values['generation_mode']==m],s[values['generation_mode']==m],model.threshold)for m in ('global','local','cluster','dependency')}})
    np.savez_compressed(OUT/'scores.npz',y=values['y'],generation_mode=values['generation_mode'],sample_id=values['sample_id'],**scores)
    write(OUT/'results.json',{'protocol':'ADBench-specific retraining, NOT unchanged service-model transfer','test_rows_fit':0,'normal_only_fit':True,
        'fixed_test_npz_sha256':sha256(ROOT/'data/adbench_1000/dataset.npz'),'results':rows,'within_generator_split':True})
    print('ADBench-specific fixed test',[(r['method'],r['f1'],r['fp'],r['F1_target_met']and r['FP_cap_met'])for r in rows],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args();run(args.algorithms)
