"""Small declared normal-only K-means role ablation, not a benchmark fit."""
import itertools
import joblib
import numpy as np
from model_research_v13.data import write
from shared_model_v7.protocol import ROOT,OUT,freeze,atomic_dump
from shared_model_v7.cohort_kmeans import Detector,Setting


def run():
    freeze();dest=OUT/'cohort_kmeans_cores';dest.mkdir(exist_ok=True)
    d=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib');records=[]
    for clusters,white in itertools.product((8,16),(False,True)):
        s=Setting(clusters=clusters,white=white);m=Detector(s,d['rank_history']).fit(d['train'])
        normal=m.score(d['normal_calibration']);m.threshold=float(np.quantile(normal,.995,method='higher'))
        path=dest/f'kmeans_{clusters}_{int(white)}.joblib';atomic_dump(m,path)
        records.append({'file':path.name,**m.record(),'fault_labels_used_for_estimator_or_initial_threshold':False})
        print('NORMAL COHORT KMEANS',clusters,white,'rows',m.fit_rows,'fallback',m.threshold,flush=True)
    write(dest/'normal_fit.json',{'models':records,'normal_only_train_and_calibration':True,'not_goal_acceptance':True})


if __name__=='__main__':run()
