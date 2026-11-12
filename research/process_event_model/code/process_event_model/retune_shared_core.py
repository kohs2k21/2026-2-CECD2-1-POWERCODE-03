"""Actual-original-only sklearn refit, with fixed four-task minute validation."""
import argparse
import copy
import json

import joblib
import numpy as np

from .data import ROOT,write_json,digest
from .models import Settings,Detector
from .family_weights import FamilyConfidenceDetector
from .minute_refinement import ACTIVE_TASKS,EventPolicy,MinuteReplay,results,quality


def run(algorithm):
    source=ROOT/'reports/process_event_model/rate_conditioned'
    out=ROOT/'reports/process_event_model'/('shared_core_'+algorithm);out.mkdir(exist_ok=True)
    package=joblib.load(source/'event_models.joblib');parent=package['models'][algorithm]
    selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib');f=data['frame'];rp=MinuteReplay(f,data['windows'])
    normal=joblib.load(ROOT/'reports/process_event_model/native_event/original_features.joblib')['partitions']
    params=selection['models'][algorithm]['policy'];legacy={t:data['legacy'][t][algorithm] for t in ACTIVE_TASKS}
    baseline=results(data,rp.project(parent.raw_score(f),EventPolicy(**params)))
    def objective(rows):
        if any(m['false_positive_rate']>.06 for m in rows.values()):return -1e6
        if any(rows[t]['f1']<legacy[t]['f1']-.01 for t in ACTIVE_TASKS if t!='time_burst'):return -1e6
        return quality(rows,legacy)-.25*max(legacy['time_burst']['precision']-rows['time_burst']['precision'],0.)
    best=objective(baseline);best_model=copy.deepcopy(parent);record=dict(tasks=baseline,source_preserved=True)
    candidates=[]
    if algorithm=='if':settings=[Settings(algorithm,'native_context',samples=n,whiten=w) for n in (128,512,2048) for w in (False,True)]
    elif algorithm=='lof':settings=[Settings(algorithm,'native_context',neighbors=n,whiten=w) for n in (20,120,256) for w in (False,True)]
    else:settings=[Settings(algorithm,'native_context',clusters=n,whiten=w) for n in (16,48,96) for w in (False,True)]
    for setting in settings:
        core=Detector(setting).fit(normal['train'])
        calibration=FamilyConfidenceDetector(core,parent.model_weight).fit_calibration(normal['calibration'])
        model=copy.deepcopy(parent);model.base=core;model.settings=setting;model.fit_rows=core.fit_rows
        model.model.base=core;model.model.settings=setting
        model.model.model_stats=calibration.model_stats;model.model.default_stats=calibration.default_stats
        cold,mode,warm,point=model.model.parts(f)
        factor=model.traffic_factor(f);warm[:,3]/=factor
        point/=model.factor(f)
        cuts=model.model.cutoffs.copy()
        def evaluate(cc):
            score=(warm/cc[:4]).max(axis=1);score[cold]=point[cold]/cc[mode[cold]]
            rows=results(data,rp.project(score,EventPolicy(**params)))
            return objective(rows),rows
        q,rows=evaluate(cuts)
        for sweep in range(3):
            changed=False
            for k in (3,0,1,2,4,5,6,7):
                vals=point[(mode==k)&cold&~f.label.to_numpy(bool)] if k>=4 else warm[~cold&~f.label.to_numpy(bool),k]
                options=np.unique(np.r_[cuts[k]*np.array([.65,.8,.9,1.,1.1,1.25,1.5]),
                    np.quantile(vals,[.85,.94,.98,.995]) if len(vals) else []])
                for v in options:
                    if v<=0:continue
                    trial=cuts.copy();trial[k]=v;value,r=evaluate(trial)
                    if value>q+1e-10:cuts=trial;q=value;rows=r;changed=True
            if not changed:break
        model.model.cutoffs=cuts
        one=dict(settings=vars(setting),tasks=rows,objective=q,cutoffs=cuts.tolist())
        candidates.append(one)
        if q>best+1e-10:best=q;best_model=copy.deepcopy(model);record=one
        write_json(out/'progress.json',dict(selected=record,candidates=candidates))
        print('SHARED CORE',algorithm,vars(setting),{t:round(m['f1'],4) for t,m in rows.items()},
            'burstP',round(rows['time_burst']['precision'],4),'selected',best,flush=True)
    joblib.dump({**package,'models':{**package['models'],algorithm:best_model}},out/'event_models.joblib',compress=3)
    joblib.dump(data,out/'minute_development.joblib',compress=3)
    selection['models'][algorithm]=dict(**record,policy=params)
    write_json(out/'selection.json',dict(models=selection['models'],source_sha256=digest(source/'event_models.joblib'),
        candidates=candidates,estimator_fit_actual_original_only=True,no_generated_normal_rows=True,
        development_first400_cases_only=True,benchmark_unchanged=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--algorithm',choices=('if','kmeans','lof'),required=True)
    run(parser.parse_args().algorithm)
