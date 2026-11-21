"""Re-select frozen normal-trained candidates using FIVE-TASK constraints.

No fixed test scores/labels are read. Development annotations are explicitly
used for hyperparameters. Unlike v4, no weak task may disappear in pooled F1.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
from model_research_v13.data import read,write
from shared_model_target.calibration import NormalReference,CalibratedDetector
from shared_model_improvement.run import _stats
from shared_model_v5.protocol import TASKS,ALGORITHMS,OUT,gates,freeze

DEST=OUT/'existing_search'

def prepare():
    d=joblib.load(ROOT/'reports/shared_model_v4_rank/development.joblib')
    for fold in ('search','confirmation'):
        keep=np.isin(d[fold]['scenario'],TASKS)
        d[fold]={'frame':d[fold]['frame'].loc[keep].reset_index(drop=True),'y':d[fold]['y'][keep],
            'scenario':d[fold]['scenario'][keep]}
    return d

def task_metrics(data,scores,threshold):
    return [{'task':name,**_stats(data['y'][mask],scores[mask],threshold)}for name in TASKS
        for mask in [data['scenario']==name]]

def sources(a):
    for release in ('shared_model_v3_release','shared_model_v4_final'):
        p=ROOT/'reports'/release/'models.joblib';m=joblib.load(p)['models'][a]
        yield release,m.base
    for i in range(19):yield 'rank_'+str(i),joblib.load(ROOT/'reports/shared_model_v4_rank'/(a+'_candidate_'+str(i)+'.joblib'))

def run(algorithms):
    freeze();DEST.mkdir(exist_ok=True);d=prepare();folds=('search','confirmation');cal=d['normal_calibration']
    for a in algorithms:
        if(DEST/(a+'_selected.joblib')).exists():raise FileExistsError('Completed search preserved')
        best=None;rank=None;winner=None;records=[]
        for name,base in sources(a):
            nr=base.score(cal);raw={k:base.score(d[k]['frame'])for k in folds}
            joblib.dump({'normal':nr,**raw},DEST/(a+'_'+name+'_raw_scores.joblib'),compress=3)
            joblib.dump(base,DEST/(a+'_'+name+'_base.joblib'),compress=3)
            for mode in ('availability','adapter_availability'):
                for alpha in (.5,1.):
                    for kind in ('cdf','robust'):
                        ref=NormalReference.fit(cal,nr,mode,alpha,kind);ns=ref.apply(cal,nr)
                        ss={k:ref.apply(d[k]['frame'],raw[k])for k in folds}
                        for q in (.90,.925,.95,.96,.97,.975,.98,.985,.99,.9925,.995,.9975):
                            threshold=float(np.quantile(ns,q,method='higher'))
                            by_task={k:task_metrics(d[k],ss[k],threshold)for k in folds};gg={k:gates(by_task[k])for k in folds}
                            fp=max(g['maximum_task_normal_FPR']for g in gg.values());minimum=min(g['minimum_task_F1']for g in gg.values());median=min(g['median_task_F1']for g in gg.values())
                            row={'algorithm':a,'source':name,'mode':mode,'alpha':alpha,'kind':kind,'q':q,'threshold':threshold,
                                'folds':by_task,'gates':gg,'all_development_gates_met':all(g['all_met']for g in gg.values()),
                                'minimum_across_folds':minimum,'median_lower_fold':median,'maximum_task_FPR':fp}
                            records.append(row)
                            key=(row['all_development_gates_met'],minimum,median,-fp)
                            if fp<.05 and(rank is None or key>rank):
                                rank=key;winner=row;best=CalibratedDetector(base,ref,threshold)
            write(DEST/(a+'_search.json'),records);write(DEST/(a+'_selection.json'),winner)
            joblib.dump(best,DEST/(a+'_best_so_far.joblib'),compress=3)
            if winner is None:
                print('BALANCED',a,name,'no candidate satisfying per-task FPR yet',flush=True)
            else:
                print('BALANCED',a,name,'best',winner['source'],'min',round(winner['minimum_across_folds']*100,2),
                    'median',round(winner['median_lower_fold']*100,2),'FPR',round(winner['maximum_task_FPR']*100,2),
                    'all_dev',winner['all_development_gates_met'],flush=True)
        if best is None:
            raise RuntimeError(f'{a}: no feasible development candidate; selection not published')
        joblib.dump(best,DEST/(a+'_selected.joblib'),compress=3)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));run(p.parse_args().algorithms)
