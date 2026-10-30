from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.semantic_models import Detector,Setting

def settings(a):
    for weight in (.5,1.):
        for view in ('two_axis','compact','radial_pair'):
            yield Setting(a,view=view,semantic_weight=weight,samples=128,neighbors=600,accumulated_burst=True)
    for trans in ('linear','asinh'):
        for white in (False,True):yield Setting(a,view='compact',transform=trans,white=white,samples=512,neighbors=800,accumulated_burst=False)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings;search.run(args.algorithms,draws=1000,output=search.OUT/'semantic_search')
