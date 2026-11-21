from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7 import role_models as models
from shared_model_v7.role_models import Detector
from shared_model_v7.search_roles import settings
from shared_model_v7.independent_data import prepare
from shared_model_v7.protocol import BASE


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    dev=prepare();models.REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference'];models.PARENTS=joblib.load(BASE/'models.joblib')['models']
    search.Detector=Detector;search.settings=settings
    search.run(args.algorithms,draws=1000,output=search.OUT/'role_independent_search',development=dev,prefer_roles=True)
