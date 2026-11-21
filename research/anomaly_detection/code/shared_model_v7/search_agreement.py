from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7.agreement import Setting,Detector

def settings(a):
    for agreement in (.5,1.):
        for view in ('peaks','two_axis','radial_pair'):
            yield Setting(a,view=view,agreement=agreement,samples=128,neighbors=600,point_gain=2.)
    for gain in (.1,.25,.5,1.,2.):
        for agreement in (.5,1.):
            yield Setting(a,view='hybrid',agreement=agreement,extra_gain=gain,samples=128,neighbors=800)

def factory(setting,history):
    parent=None
    if setting.view=='hybrid':parent=joblib.load(search.BASE/'models.joblib')['models'][setting.algorithm].base
    return Detector(setting,history,parent)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=factory;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'agreement_search')
