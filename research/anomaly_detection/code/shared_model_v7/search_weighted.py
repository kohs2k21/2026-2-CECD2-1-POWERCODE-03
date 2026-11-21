from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.weighted import Setting,Detector

def settings(a):
    for view in ('two_axis','three_axis','radial'):
        for point in (2.,5.,10.):
            yield Setting(a,view=view,point_gain=point,samples=64,neighbors=600,accumulated_burst=True)
    for view in ('two_axis','three_axis','peaks'):
        for trans in ('linear','asinh'):
            yield Setting(a,view=view,transform=trans,point_gain=2.,samples=256,neighbors=600,accumulated_burst=True)
    if a=='if':
        for n in (16,32,512,1024):yield Setting(a,view='two_axis',point_gain=5.,samples=n,accumulated_burst=True)
    elif a=='kmeans':
        for k in (2,4,16,32):yield Setting(a,view='three_axis',point_gain=2.,clusters=k,white=True,accumulated_burst=True)
    else:
        for n in (100,200,1000):yield Setting(a,view='two_axis',point_gain=2.,neighbors=n,accumulated_burst=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings
    # A distinct output prevents cached model changes from being overwritten.
    search.run(args.algorithms,output=search.OUT/'weighted_search')
