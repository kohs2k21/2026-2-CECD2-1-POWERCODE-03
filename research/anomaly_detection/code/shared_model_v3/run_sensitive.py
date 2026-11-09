from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v3 import run
from shared_model_v3.sensitive import augment,SensitiveReference,SMALL
from shared_model_v3.models import Setting
from shared_model_v3.sensitive_detector import Detector
from shared_model_v3.features import SEQUENCE
from shared_model_target.current_features import CURRENT
from adapter_feature_ablation.features import ADAPTER

OUT=ROOT/'reports/shared_model_v3_sensitive'

def prepare():
    OUT.mkdir(exist_ok=True);path=OUT/'development.joblib'
    if path.exists():assert sha256(path)==read(OUT/'preparation.json')['sha256'];return joblib.load(path)
    d=joblib.load(ROOT/'reports/shared_model_v3/development.joblib')
    for k in ('train','normal_calibration'):d[k]=augment(d[k])
    for k in ('search','confirmation'):d[k]['frame']=augment(d[k]['frame'])
    d['adapter_reference']=SensitiveReference(d['adapter_reference']);joblib.dump(d,path,compress=3)
    write(OUT/'preparation.json',{'sha256':sha256(path),'parent_sha256':sha256(ROOT/'reports/shared_model_v3/development.joblib'),
        'feature_sha256':sha256(ROOT/'code/shared_model_v3/sensitive.py')})
    return d

def candidates(a):
    for view in ('small_compact','small','small_minimal'):
        for trans in ('asinh','hybrid'):
            for w in (.25,.75):yield Setting(a,view=view,transform=trans,adapter_weight=w)
    if a=='if':
        for view in ('small_compact','small'):
            for n in (128,512):yield Setting(a,view=view,rotation=True,whitening=True,samples=n)
    elif a=='kmeans':
        for k in (2,4,16):yield Setting(a,view='small_compact',clusters=k)
    else:
        for k in (50,100,500,800):yield Setting(a,view='small_compact',neighbors=k)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=['if','kmeans','lof']);args=p.parse_args()
    run.OUT=OUT;run.prepare=prepare;run.Detector=Detector;run.candidates=candidates
    run.tune(args.algorithms)
