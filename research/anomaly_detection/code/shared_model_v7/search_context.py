from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.context_models import Detector,Setting

def settings(a):
    for geometry in ('radius','two_axis','white','both'):
        for trans in ('linear','asinh'):
            yield Setting(a,view='context',geometry=geometry,transform=trans,samples=128,neighbors=600)
    for geometry in ('radius','two_axis'):
        for ridge in (.001,.1):
            yield Setting(a,view='context',geometry=geometry,ridge=ridge,samples=256,neighbors=800,accumulated=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'context_search')
