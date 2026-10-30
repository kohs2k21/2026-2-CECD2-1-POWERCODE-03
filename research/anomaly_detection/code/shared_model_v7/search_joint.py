from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7 import joint_models as models
from shared_model_v7.joint_models import Setting,Detector
from shared_model_v7.protocol import BASE


def settings(a):
    if a=='if':
        for ratio in(.25,.5,1.):
            for gain in(.5,1.):yield Setting(a,view='radial',point_ratio=ratio,scan_gain=gain,samples=64)
    elif a=='kmeans':
        for clusters in(4,8,16):
            for gain in(1.,3.):yield Setting(a,view='hybrid',hybrid=True,clusters=clusters,point_gain=gain,scan_gain=gain,white=False)
    else:
        for neighbors in(200,600,1000):
            for gain in(.5,1.):yield Setting(a,view='compact',neighbors=neighbors,scan_gain=gain)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    models.REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference'];models.PARENTS=joblib.load(BASE/'models.joblib')['models']
    search.Detector=Detector;search.settings=settings
    search.run(args.algorithms,draws=1000,output=search.OUT/'joint_new_search')
