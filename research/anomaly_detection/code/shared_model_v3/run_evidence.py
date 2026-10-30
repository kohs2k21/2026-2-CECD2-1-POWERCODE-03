"""Second branch: cadence-aware memory + one-sided frozen-normal representation."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v3 import run
from shared_model_v3.adaptive import augment,AdaptiveReference
from shared_model_v3.evidence import Setting,Detector

OUT=ROOT/'reports/shared_model_v3_evidence'

def prepare():
    OUT.mkdir(exist_ok=True);path=OUT/'development.joblib'
    if path.exists():assert sha256(path)==read(OUT/'preparation.json')['sha256'];return joblib.load(path)
    d=run.prepare()
    for k in ('train','normal_calibration'):d[k]=augment(d[k])
    for k in ('search','confirmation'):d[k]['frame']=augment(d[k]['frame'])
    d['adapter_reference']=AdaptiveReference(d['adapter_reference'])
    joblib.dump(d,path,compress=3)
    write(OUT/'preparation.json',{'sha256':sha256(path),'parent_sha256':sha256(ROOT/'reports/shared_model_v3/development.joblib'),
        'feature_sha256':sha256(ROOT/'code/shared_model_v3/adaptive.py')})
    return d

def candidates(a):
    for view in ('summary','state','rank'):
        for transform in ('asinh','hybrid'):
            for weight in (.25,.75):yield Setting(a,view=view,transform=transform,adapter_weight=weight)
    if a=='if':
        for view in ('summary','state','rank'):
            for n in (128,1024):yield Setting(a,view=view,samples=n,rotation=True,whitening=True)
    elif a=='kmeans':
        for view in ('summary','state'):
            for n in (2,4,16):yield Setting(a,view=view,clusters=n)
    else:
        for view in ('summary','state'):
            for n in (50,100,500):yield Setting(a,view=view,neighbors=n)
    # Flow counters in historical paired controls were not independently rebuilt.
    # Do not select the exploratory flow=True candidate from those caches.

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=['if','kmeans','lof']);args=p.parse_args()
    # Capture parent preparation before substituting orchestration globals.
    parent_prepare=run.prepare
    def loaded():
        global prepare
        run.prepare=parent_prepare
        try:return prepare()
        finally:run.prepare=loaded
    run.OUT=OUT
    # Parent preparation already exists; load it directly to avoid global OUT substitution.
    parent_path=ROOT/'reports/shared_model_v3/development.joblib'
    def parent_read():return joblib.load(parent_path)
    parent_prepare=parent_read
    run.prepare=loaded;run.Detector=Detector;run.candidates=candidates
    run.tune(args.algorithms)
