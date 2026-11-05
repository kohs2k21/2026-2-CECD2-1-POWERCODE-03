"""Adapter-conditioned score calibration, not Adapter expert estimators.

Fits scalar cold-context score references on EXISTING labelled development
normal examples only. The sklearn estimators remain fitted on actual original
logs. No extra synthetic logs, no task routing, no confirmation-case fitting.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from .data import ROOT,write_json,digest
from .minute_refinement import MinuteReplay,EventPolicy,quality,results,ACTIVE_TASKS,ALGORITHMS


class AdapterScoreDetector:
    threshold=1.

    def __init__(self,model,factors,support):
        self.model=model;self.base=model.base
        self.settings=model.settings;self.fit_rows=model.fit_rows;self.columns=model.columns
        self.model_weight=model.model_weight
        self.factors=dict(factors);self.support=dict(support)

    def factor(self,f):
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        return np.where(cold,f.adapterType.astype(str).map(self.factors).fillna(1.).to_numpy(float),1.)

    def calibration_context(self,f):
        return dict(adapter_score_reference_factor=self.factor(f),
                    adapter_score_reference_cases=f.adapterType.astype(str).map(
                        {a:s['cases'] for a,s in self.support.items()}).fillna(0).to_numpy(int))

    def raw_score(self,f):return self.model.raw_score(f)/self.factor(f)

    def state(self,f):
        group,score=self.model.state(f)
        return group,score/self.factor(f)

    def predict(self,f):
        score=self.raw_score(f);return score,score>1.

    def record(self):
        return {**self.model.record(),'adapter_score_calibration':True,'adapter_expert_estimators':False,
                'cold_only_score_references':True,'adapter_factors':self.factors,
                'reference_support':self.support,'unseen_adapter_factor':1.,
                'normal_reference_source':'existing first400 development cases; labelled negatives only',
                'additional_generated_Process_rows':0}


class AdapterTrafficDetector(AdapterScoreDetector):
    def __init__(self,cold_detector,factors,support):
        super().__init__(cold_detector.model,cold_detector.factors,cold_detector.support)
        self.traffic_factors=dict(factors);self.traffic_support=dict(support)

    def traffic_factor(self,f):
        return f.adapterType.astype(str).map(self.traffic_factors).fillna(1.).to_numpy(float)

    def normalized(self,f):
        cold,mode,warm,point=self.model.parts(f)
        normalized=warm/np.maximum(self.model.cutoffs[:4],1e-8)
        normalized[:,3]/=self.traffic_factor(f)
        score=normalized.max(axis=1);group=normalized.argmax(axis=1)
        score[cold]=point[cold]/self.model.cutoffs[mode[cold]]/self.factor(f)[cold]
        group[cold]=mode[cold]
        return group,score

    def raw_score(self,f):return self.normalized(f)[1]

    def state(self,f):return self.normalized(f)

    def calibration_context(self,f):
        cold=f.history_support.to_numpy(float)<np.log1p(3)
        return {**super().calibration_context(f),
                'adapter_traffic_reference_factor':np.where(cold,1.,self.traffic_factor(f)),
                'adapter_traffic_reference_cases':f.adapterType.astype(str).map(
                    {a:s['cases'] for a,s in self.traffic_support.items()}).fillna(0).to_numpy(int)}

    def record(self):
        return {**super().record(),'warm_traffic_only_score_references':self.traffic_factors,
                'warm_traffic_reference_support':self.traffic_support,
                'unchanged_latency_and_cold_score_channels':True}


def run(source,output):
    output.mkdir(exist_ok=True,parents=True)
    package=joblib.load(source/'event_models.joblib')
    selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib');f=data['frame'];replay=MinuteReplay(f,data['windows'])
    adapters=f.adapterType.astype(str).to_numpy()
    names=sorted(set(adapters));mapping={a:i for i,a in enumerate(names)}
    codes=np.array([mapping[a] for a in adapters]);cold=f.history_support.to_numpy(float)<np.log1p(3)
    models={};records={}
    for a in ALGORITHMS:
        parent=package['models'][a];base=parent.raw_score(f)
        params=selection['models'][a]['policy']
        baseline=results(data,replay.project(base,EventPolicy(**params)))
        factor=np.ones(len(names));support={};choices={}
        for adapter,index in mapping.items():
            mask=(codes==index)&cold&~f.label.to_numpy(bool)
            cases=int(f.loc[mask,'case_id'].nunique());rows=int(mask.sum())
            if cases<10 or rows<30:continue
            support[adapter]=dict(cases=cases,normal_rows=rows)
            choices[index]=np.unique(np.maximum(np.r_[1.,np.quantile(base[mask],[.8,.9,.94,.97,.98,.99,.995,1.])],1.))
        def evaluate(factors):
            denom=np.where(cold,factors[codes],1.)
            rows=results(data,replay.project(base/denom,EventPolicy(**params)))
            # This extension cannot trade away another development scenario.
            safe=all(rows[t]['f1']>=baseline[t]['f1']-1e-12 and rows[t]['false_positive_rate']<=.06 for t in ACTIVE_TASKS)
            return quality(rows,{t:data['legacy'][t][a] for t in ACTIVE_TASKS}) if safe else -1e6,rows
        best,rows=evaluate(factor)
        for _ in range(3):
            changed=False
            for index,options in choices.items():
                for v in options:
                    trial=factor.copy();trial[index]=v;q,r=evaluate(trial)
                    if q>best+1e-10:factor=trial;best=q;rows=r;changed=True
            if not changed:break
        factors={name:float(factor[index]) for name,index in mapping.items() if factor[index]!=1.}
        model=AdapterScoreDetector(parent,factors,support);models[a]=model
        records[a]=dict(policy=params,tasks=rows,objective=best,factors=factors,support=support)
        print('ADAPTER SCORE CALIBRATION',a,{t:round(m['f1'],4) for t,m in rows.items()},'changed',factors,flush=True)
    assert all(type(m).__module__!='__main__' for m in models.values())
    joblib.dump({**package,'models':models},output/'event_models.joblib',compress=3)
    write_json(output/'selection.json',dict(models=records,source_sha256=digest(source/'event_models.joblib'),
        calibration_first400_development_cases_only=True,normal_only_actual_estimator_fit=True,
        benchmark_development_labels_used_for_threshold_selection=True,
        no_confirmation_case_fitting=True,development_each_task_f1_nonregression=True))
    joblib.dump(data,output/'minute_development.joblib',compress=3)


if __name__=='__main__':
    from process_event_model.adapter_score_calibration import run as canonical_run
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=ROOT/'reports/process_event_model/parallel_geometric')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/process_event_model/adapter_calibrated')
    args=parser.parse_args();canonical_run(args.source,args.output)
