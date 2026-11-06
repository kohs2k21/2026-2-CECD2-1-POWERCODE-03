"""Finite FEATURE-only diagnostic, never named IF/K-means/LOF performance."""
import itertools
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import ROOT,OUT
from shared_model_v7.larger_probe import bank
from shared_model_v7.pmf_filter import Context
from shared_model_v6.search import curves


def run():
    d=bank()['time_burst'];dev=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    cal=dev['normal_calibration'];records=[]
    for duty,stay in itertools.product((.25,.5,.75),(.7,.85,.95)):
        c=Context(dev['rank_history'],duty,stay)
        normal=c.transform(cal);score=c.transform(d['frame']);thresholds=np.unique(normal)
        rows=curves(d['y'],score,thresholds)
        eligible=[r for r in rows if r['recall']>=.55 and r['normal_false_positive_rate']<.05]
        best=max(eligible,key=lambda r:r['precision'])if eligible else None
        records.append({'duty':duty,'stay':stay,'feature_only_P_at_R55':best})
        write(OUT/'pmf_feature_diagnostic.json',{'records':records,'not_model_performance':True,
              'labels_used_for_diagnostic_selection':True,'external_blind_test':False})
        print('PMF FEATURE ONLY',duty,stay,None if best is None else {k:round(best[k]*100,3)for k in ('precision','recall','f1','normal_false_positive_rate')},flush=True)


if __name__=='__main__':run()
