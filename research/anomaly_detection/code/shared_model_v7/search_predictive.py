from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.predictive import Detector,Setting
from shared_model_v7.expanded import prepare

def settings(a):
    for mode in('joint','regression','append'):
        for white in(False,True):yield Setting(a,view='predictive',baseline_mode=mode,white=white,samples=128,neighbors=600)
    for trans in('linear','asinh'):yield Setting(a,view='predictive',baseline_mode='append',transform=trans,samples=512,neighbors=200)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'predictive_search',development=prepare())
