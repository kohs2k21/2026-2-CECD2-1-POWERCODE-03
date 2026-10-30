from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7 import search_remap as parent
from shared_model_v7.remap_models import Setting
from shared_model_v7.protocol import BASE


def settings(a):
    for w in(.25,.5,1.,2.):yield Setting(a,point_weight=w,point_context='adapter')
    for kind in('scan','scan_tail','scan_agreement'):
        for w in(1.,4.):yield Setting(a,point_weight=.5,burst_kind=kind,burst_weight=w,point_context='adapter')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    parent.DEV=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    parent.REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference'];parent.PARENTS=joblib.load(BASE/'models.joblib')['models']
    search.Detector=parent.factory;search.settings=settings
    search.run(args.algorithms,draws=1000,output=search.OUT/'remap_adapter_search')
