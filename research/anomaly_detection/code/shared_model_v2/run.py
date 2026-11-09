"""Development-only selection with the previous model's FPR as a guardrail."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,copy
from dataclasses import asdict
import joblib
import numpy as np
from model_evaluation.data import sha256
from model_research_v13.data import read,write
from shared_model_target.run_current import prepare as parent_prepare
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v2.features import augment,MemoryReference
from shared_model_v2.models import Setting,Detector

OUT=ROOT/'reports/shared_model_v2'
PARENT=ROOT/'reports/shared_model_target_lof'


def prepare():
    OUT.mkdir(exist_ok=True);path=OUT/'development.joblib'
    if path.exists():
        assert sha256(path)==read(OUT/'preparation.json')['sha256'];return joblib.load(path)
    source=parent_prepare();data={'train':augment(source['train']),'normal_calibration':augment(source['normal_calibration']),
        'adapter_reference':MemoryReference(source['adapter_reference']),'feature_context':source['feature_context']}
    for split in ('search','confirmation'):
        s=source[split];f=s['frame'];parts=[]
        # The development frame concatenates observed and paired normal cohorts
        # with the same case IDs. Transform each contiguous cohort separately.
        start=0;seen=set()
        for i,(case,t)in enumerate(zip(f.case_id,f.window_start)):
            key=(case,t)
            if key in seen:
                parts.append(augment(f.iloc[start:i]));start=i;seen=set()
            seen.add(key)
        parts.append(augment(f.iloc[start:]));new=__import__('pandas').concat(parts,ignore_index=True)
        np.testing.assert_array_equal(new.case_id,f.case_id);np.testing.assert_array_equal(new.window_start,f.window_start)
        data[split]={**s,'frame':new}
    joblib.dump(data,path,compress=3)
    write(OUT/'preparation.json',{'sha256':sha256(path),'parent_model_sha256':sha256(PARENT/'models.joblib'),
        'parent_development_sha256':sha256(ROOT/'reports/shared_model_target_current/development.joblib'),
        'feature_source_sha256':sha256(ROOT/'code/shared_model_v2/features.py'),'train_reference_only':True})
    return data


def candidates(a):
    if a=='kmeans':
        for memory in (False,True):
            for k in (8,16,32,64):
                for geometry in ('nearest','radius'):
                    yield Setting(a,memory=memory,clusters=k,geometry=geometry)
        for transform in ('asinh','quantile'):
            for k in (16,32):yield Setting(a,transform=transform,clusters=k)
        yield Setting(a,clusters=32,trim=.005)
    elif a=='if':
        for weight in (.25,.5,1.):
            for samples in (256,512):yield Setting(a,memory_weight=weight,whitening=True,rotation=True,samples=samples)
        for memory in (False,True):
            yield Setting(a,memory=memory,whitening=False,rotation=False,samples=512)
            yield Setting(a,memory=memory,whitening=True,rotation=True,samples=1024)
    else:
        for memory in (False,True):
            for cap,k in ((2048,300),(2048,500),(4096,500),(4096,1000),(8192,1000)):
                yield Setting(a,memory=memory,reference_cap=cap,neighbors=k)
        yield Setting(a,transform='asinh',neighbors=500)
        yield Setting(a,neighbors=500,memory_weight=1.)


def tune():
    if (OUT/'selection.json').exists():raise FileExistsError('Frozen models are not overwritten')
    data=prepare();normal=data['normal_calibration'];pkg=joblib.load(PARENT/'models.joblib')
    assert sha256(PARENT/'models.joblib')==read(PARENT/'selection.json')['model_sha256']
    chosen={};allrecords=[];baseline={}
    for a in ALGORITHMS:
        old=pkg['models'][a];folds={n:_stats(data[n]['y'],old.score(data[n]['frame']),old.threshold)for n in ('search','confirmation')}
        y=np.concatenate([data[n]['y']for n in folds]);os=np.concatenate([old.score(data[n]['frame'])for n in folds])
        baseline[a]={'folds':folds,**_stats(y,os,old.threshold)}
        cap={n:folds[n]['normal_false_positive_rate']+.001 for n in folds}
        best=old;record={'from_parent':True,'folds':folds,**baseline[a]};key=(baseline[a]['f1'],-baseline[a]['normal_false_positive_rate'])
        for i,setting in enumerate(candidates(a)):
            model=Detector(setting).fit(data['train']);nr=model.score(normal)
            raw={n:model.score(data[n]['frame'])for n in folds}
            for mode,alpha in (('global',1.),('availability',.5),('availability',1.)):
                for kind in ('cdf','robust'):
                    ref=NormalReference.fit(normal,nr,mode,alpha,kind);ns=ref.apply(normal,nr)
                    ss={n:ref.apply(data[n]['frame'],raw[n])for n in folds}
                    for q in (.90,.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995):
                        threshold=float(np.quantile(ns,q,method='higher'))
                        metrics={n:_stats(data[n]['y'],ss[n],threshold)for n in folds}
                        eligible=all(metrics[n]['normal_false_positive_rate']<=cap[n]for n in folds)
                        pooled=_stats(y,np.concatenate(list(ss.values())),threshold)
                        r={'settings':asdict(setting),'candidate':i,'mode':mode,'alpha':alpha,'kind':kind,'q':q,
                           'threshold':threshold,'folds':metrics,'eligible':eligible,**pooled}
                        allrecords.append({'algorithm':a,**r})
                        rank=(pooled['f1'],-pooled['normal_false_positive_rate'])
                        if eligible and rank>key:best,key,record=CalibratedDetector(model,ref,threshold),rank,r
            write(OUT/'search_results.json',allrecords)
            joblib.dump(best,OUT/(a+'_best_so_far.joblib'),compress=3)
            write(OUT/'progress.json',{'algorithm':a,'candidate':i,'best':record,'baseline':baseline[a],'fold_fpr_caps':cap})
            print(a,i,'memory',setting.memory,'best F1',round(key[0]*100,3),'FP',round(-key[1]*100,3),flush=True)
        pkg['models'][a]=best;chosen[a]=record
    pkg['adapter_reference']=data['adapter_reference']
    joblib.dump(pkg,OUT/'models.joblib',compress=3)
    write(OUT/'selection.json',{'selected':chosen,'baseline_development':baseline,
       'model_sha256':sha256(OUT/'models.joblib'),'parent_model_sha256':sha256(PARENT/'models.joblib'),
       'benchmark_manifest_sha256':read(PARENT/'selection.json')['benchmark_manifest_sha256'],
       'normal_estimator_and_reference_fit_only':True,'development_labels_for_selection':True,
       'previous_confirmation_is_development':True,'prior_benchmark_informed_design':True,
       'single_estimator_each':True,'adapter_experts':False,'benchmark_modified':False,
       'FPR_development_tolerance_absolute':.001,'fresh_confirmation_required':True})
    print('Selection frozen',chosen,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=('prepare','tune'),required=True)
    {'prepare':prepare,'tune':tune}[p.parse_args().stage]()
