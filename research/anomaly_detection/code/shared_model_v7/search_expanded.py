from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.expanded import prepare
from shared_model_v7.profile_models import Detector,Setting

def settings(a):
    for mode in ('joint','max','append'):
        for trim in(False,True):
            yield Setting(a,view='profile',profile_mode=mode,central_trim=trim,samples=128,neighbors=600)
    for white in(False,True):
        for trans in('linear','asinh'):yield Setting(a,view='profile',profile_mode='append',central_trim=True,white=white,transform=trans,samples=512,neighbors=800)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'expanded_search',development=prepare())
