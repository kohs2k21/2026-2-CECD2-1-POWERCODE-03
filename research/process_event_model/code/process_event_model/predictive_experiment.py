"""Original-row predictive-count feature study. Previous models are preserved."""
import argparse
import copy
import json

import joblib
import numpy as np
import pandas as pd

from .calibration import CalibratedEventDetector
from .data import ROOT,write_json,digest
from .experiment_v2 import actual_session_days
from .models import Detector,Settings,metrics
from .predictive import PredictiveHistory,NEW_FEATURES
from .tune_calibration import objective

SOURCE=ROOT/'reports/process_event_model/row_calibrated_v3'
PARENT=ROOT/'reports/process_event_model/row_context_v2'
OUT=ROOT/'reports/process_event_model/predictive_counts'


class PredictiveDetector(CalibratedEventDetector):
    def __init__(self,base,model_weight=.5):
        super().__init__(base,model_weight)
        self.columns=tuple(dict.fromkeys([*self.columns,'predictive_burst_1m','predictive_burst_3m']))

    @staticmethod
    def peaks(f):
        p=CalibratedEventDetector.peaks(f)
        p[:,3]=np.maximum(f.predictive_burst_1m.to_numpy(float),f.predictive_burst_3m.to_numpy(float))
        return p

    def record(self):
        return {**super().record(),'traffic_evidence':'disjoint-past Gamma-Poisson predictive tail, Fano effective-sample adjustment',
                'reference_counts_generated':False,'tail_is_guaranteed_p_value':False}


class FrozenCorePredictiveDetector(PredictiveDetector):
    """One shared estimator, with its preceding cold-context calibration intact.

    These are two statistical calibration paths around the SAME estimator, not
    a second cold expert classifier. New count evidence cannot alter a decision
    when fewer than three prior executions exist.
    """
    def __init__(self,parent,model_weight=.5):
        self.parent=parent
        super().__init__(parent.base,model_weight)

    def state(self,f):
        group,stat=super().state(f)
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        if cold.any():
            group[cold],stat[cold]=self.parent.state(f.loc[cold])
        return group,stat

    def components(self,f,raw=None):
        group,score=super().components(f,raw)
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        if cold.any():
            group[cold],score[cold]=self.parent.components(f.loc[cold],raw[cold] if raw is not None else None)
        return group,score

    def fit_calibration(self,normal):
        super().fit_calibration(normal)
        self.cutoffs[4:]=self.parent.cutoffs[4:]
        return self

    def record(self):
        return {**super().record(),'preceding_cold_decisions_preserved':True,
                'same_shared_sklearn_estimator_for_cold_and_warm':True}


def prepare_development():
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/'development.joblib'
    if path.exists():return joblib.load(path)
    source=joblib.load(PARENT/'development_400.joblib')
    bank={};analysis={}
    for t,f in source.items():
        x=PredictiveHistory().transform(f)
        assert len(x)==len(f)
        np.testing.assert_array_equal(x.event_row_id,f.event_row_id)
        bank[t]=x
        target=x.loc[x.target]
        analysis[t]={}
        for c in ('predictive_burst_1m','predictive_burst_3m'):
            analysis[t][c]={str(y):np.quantile(g[c],[.1,.5,.9,.95,.99]).tolist()
                           for y,g in target.groupby('label',sort=False)}
        print('Predictive development features',t,len(x),flush=True)
    joblib.dump(bank,path,compress=3)
    write_json(OUT/'development_feature_diagnostic.json',analysis)
    return bank


def prepare_original():
    path=OUT/'original_features.joblib'
    if path.exists():return joblib.load(path)
    base=joblib.load(PARENT/'original_features.joblib')
    raw=joblib.load(OUT.parent/'original_rows.joblib')
    dates=raw.availableTime.dt.tz_convert('Asia/Seoul').dt.normalize().astype(str)
    context=raw.loc[dates.isin(actual_session_days(raw))].copy()
    context['case_id']=context.source_period.astype(str)+':'+dates.loc[context.index]
    enriched=PredictiveHistory().transform(context).set_index('event_row_id')
    parts={}
    for name,f in base['partitions'].items():
        x=f.copy()
        for c in NEW_FEATURES:x[c]=enriched.loc[f.event_row_id,c].to_numpy()
        assert len(x)==len(f)
        assert not x.benchmark_source.any()
        parts[name]=x
    result=dict(reference=base['reference'],partitions=parts)
    joblib.dump(result,path,compress=3)
    write_json(OUT/'provenance.json',dict(actual_context_rows=len(context),additional_Process_rows=0,
        original_times_preserved=True,feature_code_sha256=digest(ROOT/'code/process_event_model/predictive.py'),
        baseline_and_test_count_intervals_disjoint=True,current_arrival_excluded=True,
        normal_history_generated=False,partitions={n:len(f) for n,f in parts.items()}))
    return result


def tune(frozen_core=False):
    bank=prepare_development();package=prepare_original()
    normal=package['partitions']['train'];cal=package['partitions']['calibration']
    targets={t:f.loc[f.target] for t,f in bank.items()}
    f=pd.concat(list(targets.values()),ignore_index=True)
    frames={};offset=0
    for t,x in targets.items():
        mask=np.zeros(len(f),bool);mask[offset:offset+len(x)]=True
        frames[t]=(mask,x.label.to_numpy(bool));offset+=len(x)
    clean=~f.label.to_numpy(bool)
    old=joblib.load(SOURCE/'event_models.joblib')
    selections={};history=[]
    for algorithm in ('if','kmeans','lof'):
        baseline=json.loads((SOURCE/f'{algorithm}_development.json').read_text(encoding='utf-8'))
        base_f1={t:m['f1'] for t,m in baseline['tasks'].items()}
        selected=(baseline['macro_f1'],old['models'][algorithm],dict(origin='unchanged preceding model',**baseline))
        for view in (('frozen_core',) if frozen_core else ('nb_compact','nb_context')):
            setting=old['models'][algorithm].settings if frozen_core else Settings(algorithm,view,samples=256,clusters=16,neighbors=60,whiten=False)
            base=old['models'][algorithm].base if frozen_core else Detector(setting).fit(normal)
            raw=base.raw_score(f);raw_cal=base.raw_score(cal)
            for weight in (.25,.5,.75,1.):
                model=(FrozenCorePredictiveDetector(old['models'][algorithm],weight) if frozen_core else PredictiveDetector(base,weight)).fit_calibration(cal)
                group,scores=model.components(f,raw);cg,cs=model.components(cal,raw_cal)
                cuts=np.ones(8);choices={}
                for g in range(8):
                    if frozen_core and g>=4:
                        cuts[g]=model.parent.cutoffs[g]
                        choices[g]=np.array([cuts[g]])
                        continue
                    v=scores[clean & (group==g)];orig=cs[cg==g]
                    if not len(v):v=orig
                    if not len(v):v=np.array([1.])
                    choices[g]=np.maximum(np.unique(np.r_[np.quantile(v,[.8,.85,.9,.94,.96,.98,.99,.995,.999]),
                        np.quantile(orig,[.9,.94,.98,.995]) if len(orig) else [],1e-6]),1e-6)
                    cuts[g]=max(float(np.quantile(v,.995)),float(np.quantile(orig,.995)) if len(orig) else 0.,1e-6)
                best=objective(frames,scores,cs,cuts,group,cg)
                for _ in range(3):
                    changed=False
                    for g in range(8):
                        for c in choices[g]:
                            trial=cuts.copy();trial[g]=c
                            candidate=objective(frames,scores,cs,trial,group,cg)
                            if candidate[0]>best[0]+1e-10:
                                cuts=trial;best=candidate;changed=True
                    if not changed:break
                model.cutoffs=cuts
                macro=float(np.mean([r['f1'] for r in best[1].values()]))
                safe=best[0]>=0 and all(m['f1']>=base_f1[t]-1e-12 for t,m in best[1].items())
                record=dict(settings=vars(setting),**model.record(),macro_f1=macro,tasks=best[1],
                            original_normal_calibration_alert_rate=best[2],all_task_f1_nonregression=safe)
                history.append(record)
                if safe and macro>selected[0]:selected=(macro,copy.deepcopy(model),record)
            print('PREDICTIVE',algorithm,view,'selected macro F1',round(selected[0],4),flush=True)
        selections[algorithm]=selected[1]
        write_json(OUT/f'{algorithm}_selection.json',selected[2])
    suffix='_frozen' if frozen_core else ''
    if any(type(model).__module__=='__main__' for model in selections.values()):
        raise RuntimeError('Refusing nonportable __main__ model classes; invoke the canonical module runner')
    joblib.dump(dict(reference=package['reference'],models=selections,processor='predictive_row_context'),OUT/f'event_models{suffix}.joblib',compress=3)
    write_json(OUT/f'search{suffix}.json',dict(candidates=history,original_only_estimator_fit=True,
        development_cases_per_task=400,source_model_sha256=digest(SOURCE/'event_models.joblib'),
        no_new_Process_rows=True,no_input_stop_detector=True))


if __name__=='__main__':
    # Serialized classes must live in their importable module, not __main__.
    from process_event_model.predictive_experiment import tune as canonical_tune, prepare_development as canonical_prepare
    parser=argparse.ArgumentParser();parser.add_argument('--development-only',action='store_true')
    parser.add_argument('--frozen-core',action='store_true')
    args=parser.parse_args()
    if args.development_only:canonical_prepare()
    else:canonical_tune(args.frozen_core)
