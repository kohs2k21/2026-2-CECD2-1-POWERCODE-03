"""Controlled feature-domain adaptation preserving each frozen estimator."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from shared_model_v7 import search_features as search
from shared_model_v7.remap_models import Setting,Detector,Transport
from shared_model_v7.protocol import BASE

PARENTS=None;REFERENCE=None;DEV=None


def factory(s,history):
    transport=Transport(s,REFERENCE,history).fit(DEV['normal_calibration'])
    model=Detector(s,PARENTS[s.algorithm],transport,history,DEV['train'])
    # Generic search fits normal estimators. This controlled ablation instead
    # explicitly freezes the estimator; its normal feature mapping was fit.
    return model


def settings(a):
    for w in(.25,.5,1.,2.):yield Setting(a,point_weight=w)
    for kind in('scan','scan_3m','scan_tail','scan_own'):
        for w in(.5,1.,2.):yield Setting(a,point_weight=.5,burst_kind=kind,burst_weight=w)
    for w in(2.,4.,8.):yield Setting(a,point_weight=.5,burst_kind='scan_agreement',burst_weight=w)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    DEV=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    REFERENCE=joblib.load(search.OUT/'neighbourhood_1.joblib')['reference'];PARENTS=joblib.load(BASE/'models.joblib')['models']
    search.Detector=factory;search.settings=settings
    search.run(args.algorithms,draws=1000,output=search.OUT/'remap_search')
