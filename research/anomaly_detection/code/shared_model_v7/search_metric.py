from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7.metric_models import Setting,Detector

def settings(a):
    if a=='if':
        for source in('v5','v6compact'):
            for n in(32,128,512):
                for white in(False,True):yield Setting(a,source=source,samples=n,white=white,rotate=True,max_features=.75)
    elif a=='kmeans':
        for geometry in('diagonal','local','density'):
            for k in(4,8,16,32):yield Setting(a,clusters=k,geometry=geometry,ridge=.01)
    else:
        for metric,p in(('minkowski',1),('minkowski',3),('chebyshev',2)):
            for k in(50,200,800):yield Setting(a,metric=metric,p=p,neighbors=k)
        for k in(50,200,800):
            for jitter in(0.,.001):yield Setting(a,neighbors=k,deduplicate=False,jitter=jitter)

def factory(s,history):
    if s.source=='v5':parent=joblib.load(ROOT/'reports/shared_model_v5/release/models.joblib')['models'][s.algorithm].base
    elif s.source=='v6compact':parent=joblib.load(ROOT/'reports/shared_model_v6/search'/('if_candidate_2.joblib'))
    else:parent=joblib.load(search.BASE/'models.joblib')['models'][s.algorithm].base
    return Detector(s,history,parent)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=factory;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'metric_search')
