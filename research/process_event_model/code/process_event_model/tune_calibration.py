"""Development-only calibration search; independent confirmation follows."""
import copy

import joblib
import numpy as np
import pandas as pd

from .calibration import CalibratedEventDetector
from .data import ROOT,write_json
from .experiment_v2 import OUT as SOURCE
from .models import Detector,Settings,metrics

OUT=ROOT/'reports/process_event_model/row_calibrated_v3'


def objective(frames,scores,cal_scores,cutoffs,groups,cal_groups):
    alarms=scores>cutoffs[groups]
    real_rate=float(np.mean(cal_scores>cutoffs[cal_groups]))
    results={}
    for t,(mask,y) in frames.items():results[t]=metrics(y,alarms[mask])
    max_fpr=max(m['false_positive_rate'] for m in results.values())
    value=float(np.mean([m['f1'] for m in results.values()]))
    violation=max(max_fpr-.06,real_rate-.06,0.)
    return (value if violation<=0 else -1.-violation),results,real_rate


def tune():
    OUT.mkdir(parents=True,exist_ok=True)
    source=joblib.load(SOURCE/'original_features.joblib')
    normal=source['partitions']['train'];cal=source['partitions']['calibration']
    bank=joblib.load(SOURCE/'development_400.joblib')
    target={t:f.loc[f.target].copy() for t,f in bank.items()}
    frame=pd.concat(list(target.values()),ignore_index=True)
    frames={};offset=0
    for t,f in target.items():
        mask=np.zeros(len(frame),bool);mask[offset:offset+len(f)]=True
        frames[t]=(mask,f.label.to_numpy(bool));offset+=len(f)
    clean=~frame.label.to_numpy(bool)
    chosen={};records=[]
    for algorithm in ('if','kmeans','lof'):
        best=None
        settings=[]
        old=joblib.load(SOURCE/f'{algorithm}_candidate.joblib')['model']
        settings.append(old.settings)
        for view in ('row_compact','row_point','row_context'):
            kwargs=dict(samples=256) if algorithm=='if' else dict(clusters=16) if algorithm=='kmeans' else dict(neighbors=60)
            settings.append(Settings(algorithm,view,**kwargs,whiten=False))
        for setting in settings:
            base=Detector(setting).fit(normal)
            raw=base.raw_score(frame);raw_cal=base.raw_score(cal)
            for weight in (.25,.5,.75,1.):
                model=CalibratedEventDetector(base,weight).fit_calibration(cal)
                groups,scores=model.components(frame,raw)
                cal_groups,cal_scores=model.components(cal,raw_cal)
                cutoffs=np.ones(8)
                options={}
                for g in range(8):
                    v=scores[clean & (groups==g)]
                    orig=cal_scores[cal_groups==g]
                    if len(v)==0:v=orig
                    if len(v)==0:v=np.array([1.])
                    options[g]=np.unique(np.maximum(np.r_[np.quantile(v,[.8,.85,.9,.94,.96,.98,.99,.995,.999]),
                        np.quantile(orig,[.9,.94,.98,.995]) if len(orig) else [],1e-6],1e-6))
                    cutoffs[g]=max(float(np.quantile(v,.995)),float(np.quantile(orig,.995)) if len(orig) else 0.,1e-6)
                selected=objective(frames,scores,cal_scores,cutoffs,groups,cal_groups)
                for _ in range(3):
                    changed=False
                    for g in range(8):
                        for threshold in options[g]:
                            trial=cutoffs.copy();trial[g]=threshold
                            result=objective(frames,scores,cal_scores,trial,groups,cal_groups)
                            if result[0]>selected[0]+1e-10:
                                cutoffs=trial;selected=result;changed=True
                    if not changed:break
                model.cutoffs=cutoffs
                row=dict(settings=vars(setting),**model.record(),macro_f1=float(np.mean([m['f1'] for m in selected[1].values()])),
                         tasks=selected[1],original_normal_calibration_alert_rate=selected[2],objective=selected[0])
                records.append(row)
                if best is None or selected[0]>best[0]:best=(selected[0],copy.deepcopy(model),row)
            print('V3',algorithm,setting.view,'best F1',round(best[2]['macro_f1'],4),flush=True)
        chosen[algorithm]=best[1]
        joblib.dump(dict(reference=source['reference'],model=best[1]),OUT/f'{algorithm}_candidate.joblib',compress=3)
        write_json(OUT/f'{algorithm}_development.json',best[2])
    joblib.dump(dict(reference=source['reference'],models=chosen,processor='row_context_v2'),OUT/'event_models.joblib',compress=3)
    write_json(OUT/'selection.json',dict(candidates=records,development_cases_per_task=400,
       target_scope_matches_legacy=True,original_only_estimator_fit=True,no_benchmark_changes=True,
       no_future_or_task_routing=True,normal_statistical_fusion=True,external_blind_test=False))


if __name__=='__main__':tune()
