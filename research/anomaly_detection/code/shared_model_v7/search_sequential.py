from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.sequential_models import Detector,Setting

def settings(a):
    for view in ('raw','compact','two_axis','radial_pair'):
        for trans in ('linear','asinh'):
            yield Setting(a,view=view,transform=trans,flags=True,samples=128,neighbors=600)
    if a=='if':
        for n in (32,512,2048):yield Setting(a,view='two_axis',samples=n,point_gain=3.,flags=True)
    elif a=='kmeans':
        for k in (2,16,32):yield Setting(a,view='raw',clusters=k,white=True,flags=True)
    else:
        for n in (50,200,1200):yield Setting(a,view='compact',neighbors=n,flags=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'sequential_search')
