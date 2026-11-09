from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7 import role_models as models
from shared_model_v7.role_models import Setting,Detector
from shared_model_v7.protocol import BASE


def settings(a):
    if a=='if':
        for view in('point_pair','point_detail'):
            for sample in(16,64,256):yield Setting(a,view='role',role_view=view,samples=sample,max_features=.5)
        for sample in(16,64):yield Setting(a,view='role',role_view='point_pair',samples=sample,cold_point=True)
    elif a=='kmeans':
        for clusters in(4,8,16):
            for gain in(.5,1.,2.):yield Setting(a,view='role',role_view='cohort',clusters=clusters,extra_gain=gain)
    else:
        for neighbors in(100,400,800):
            for gain in(.25,.5,1.):yield Setting(a,view='role',role_view='trajectory',neighbors=neighbors,extra_gain=gain)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    models.REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference'];models.PARENTS=joblib.load(BASE/'models.joblib')['models']
    search.Detector=Detector;search.settings=settings
    search.run(args.algorithms,draws=1000,output=search.OUT/'role_search',prefer_roles=True)
