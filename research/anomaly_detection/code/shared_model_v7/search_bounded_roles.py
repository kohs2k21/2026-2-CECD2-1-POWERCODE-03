from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7 import role_models
from shared_model_v7 import bounded_role_models as models
from shared_model_v7.bounded_role_models import Setting,Detector
from shared_model_v7.independent_data import prepare
from shared_model_v7.protocol import BASE


def settings(a):
    if a=='if':
        yield Setting(a,view='role',role_view='point_pair',samples=128,max_features=1,trees=800,subspace_blend=0.)
        for sample in(32,128,512):
            for blend in(.5,1.):yield Setting(a,view='role',role_view='point_pair',samples=sample,max_features=1,trees=800,subspace_blend=blend)
    elif a=='kmeans':
        for clusters in(8,16):
            for gain in(.05,.2,.5):yield Setting(a,view='role',role_view='cohort',clusters=clusters,extra_gain=gain)
    else:
        for neighbors in(400,800):
            for gain in(.05,.2,.5):yield Setting(a,view='role',role_view='trajectory',neighbors=neighbors,extra_gain=gain)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    role_models.REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference'];role_models.PARENTS=joblib.load(BASE/'models.joblib')['models']
    search.Detector=Detector;search.settings=settings
    for a in args.algorithms:
        dev=prepare()if a=='if'else joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
        models.CALIBRATION_NORMAL=dev['normal_calibration']
        search.run([a],draws=1000,output=search.OUT/'bounded_role_search',development=dev,prefer_roles=True)
