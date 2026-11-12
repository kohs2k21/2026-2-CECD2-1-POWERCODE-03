"""Shared estimator with traffic cutoff references conditioned on observed rate.

The fitted estimator still uses only actual original rows. Cutoff multipliers
use the EXISTING development labels, not the held confirmation labels. The
condition is a past-observed numeric rate, never a scenario name or case ID.
"""
import argparse
import json

import joblib
import numpy as np

from .adapter_score_calibration import AdapterTrafficDetector
from .data import ROOT,write_json,digest
from .minute_refinement import ALGORITHMS,ACTIVE_TASKS,EventPolicy,MinuteReplay,results,quality


class RateTrafficDetector(AdapterTrafficDetector):
    rate_edges=np.array([.5,1.,2.,4.])
    dispersion_edges=np.array([1.5,3.,6.])

    def __init__(self,parent,rate_factors=None,dispersion_factors=None):
        super().__init__(parent,parent.traffic_factors,parent.traffic_support)
        self.rate_factors=np.ones(5) if rate_factors is None else np.asarray(rate_factors,float)
        self.dispersion_factors=np.ones(4) if dispersion_factors is None else np.asarray(dispersion_factors,float)

    @classmethod
    def codes(cls,f):
        rate=f.predictive_support_1m.to_numpy(float)/np.maximum(f.predictive_exposure_1m.to_numpy(float),1.)
        return np.searchsorted(cls.rate_edges,rate,side='right'),np.searchsorted(cls.dispersion_edges,
            f.predictive_dispersion_1m.to_numpy(float),side='right')

    def traffic_factor(self,f):
        rate,dispersion=self.codes(f)
        return super().traffic_factor(f)*self.rate_factors[rate]*self.dispersion_factors[dispersion]

    def calibration_context(self,f):
        rate,dispersion=self.codes(f)
        return {**super().calibration_context(f),'traffic_rate_reference_bin':rate,
                'traffic_dispersion_reference_bin':dispersion}

    def record(self):
        return {**super().record(),'past_rate_cutoff_edges':self.rate_edges.tolist(),
            'past_rate_cutoff_factors':self.rate_factors.tolist(),
            'dispersion_cutoff_edges':self.dispersion_edges.tolist(),
            'dispersion_cutoff_factors':self.dispersion_factors.tolist(),
            'cutoff_selection_labels':'existing first400 development cases only'}


def run():
    source=ROOT/'reports/process_event_model/adapter_traffic_calibrated'
    output=ROOT/'reports/process_event_model/rate_conditioned';output.mkdir(exist_ok=True)
    package=joblib.load(source/'event_models.joblib');selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib');f=data['frame'];replay=MinuteReplay(f,data['windows'])
    rc,dc=RateTrafficDetector.codes(f);models={};records={}
    for a in ALGORITHMS:
        parent=package['models'][a];base=parent.model
        cold,mode,warm,point=base.parts(f);normalized=warm/base.cutoffs[:4]
        normalized[:,3]/=parent.traffic_factor(f)
        other=normalized[:,:3].max(axis=1);cold_score=point/base.cutoffs[mode]/parent.factor(f)
        params=dict(selection['models'][a]['policy']);rf=np.ones(5);df=np.ones(4)
        legacy={t:data['legacy'][t][a] for t in ACTIVE_TASKS}
        def evaluate(rates,dispersions,p):
            score=np.maximum(other,normalized[:,3]/rates[rc]/dispersions[dc]);score[cold]=cold_score[cold]
            rows=results(data,replay.project(score,EventPolicy(**p)))
            if any(m['false_positive_rate']>.06 for m in rows.values()):return -1e6,rows
            value=quality(rows,legacy)-.25*max(legacy['time_burst']['precision']-rows['time_burst']['precision'],0.)
            return value,rows
        best,rows=evaluate(rf,df,params);baseline=rows
        for sweep in range(4):
            changed=False
            for axis,codes,vec in [('rate',rc,rf),('dispersion',dc,df)]:
                for index in range(len(vec)):
                    subset=(codes==index)&~cold&f.cohort.eq('normal').to_numpy()
                    if len(f.loc[subset,'case_id'].unique())<20:continue
                    for factor in (.35,.5,.65,.8,.9,1.,1.1,1.25,1.5,2.,3.):
                        rr=rf.copy();dd=df.copy()
                        (rr if axis=='rate' else dd)[index]=factor
                        q,r=evaluate(rr,dd,params)
                        # Nontraffic results must remain comparable to legacy.
                        safe=all(r[t]['f1']>=min(baseline[t]['f1'],legacy[t]['f1'])-.01 for t in ACTIVE_TASKS if t!='time_burst')
                        if safe and q>best+1e-10:rf=rr;df=dd;rows=r;best=q;changed=True
            for key,values in dict(traffic_ttl=(60.,90.,120.,180.,240.,300.),
                traffic_gate=(.1,.3,1.,2.,3.),traffic_excess_gate=(0.,.1,.3,.5,1.,2.),filter_weak=(False,True),
                traffic_clear=(-1.,0.,.3,1.),traffic_quiet_clear=(0,1,2)).items():
                for v in values:
                    pp=dict(params);pp[key]=v;q,r=evaluate(rf,df,pp)
                    safe=all(r[t]['f1']>=min(baseline[t]['f1'],legacy[t]['f1'])-.01 for t in ACTIVE_TASKS if t!='time_burst')
                    if safe and q>best+1e-10:params=pp;rows=r;best=q;changed=True
            print('RATE CONDITIONED',a,sweep,rf,df,{t:round(m['f1'],4) for t,m in rows.items()},
                'P',round(rows['time_burst']['precision'],4),flush=True)
            if not changed:break
        models[a]=RateTrafficDetector(parent,rf,df)
        records[a]=dict(policy=params,tasks=rows,objective=best,rate_factors=rf.tolist(),dispersion_factors=df.tolist())
        write_json(output/'progress.json',dict(models=records))
    joblib.dump({**package,'models':models},output/'event_models.joblib',compress=3)
    joblib.dump(data,output/'minute_development.joblib',compress=3)
    write_json(output/'selection.json',dict(models=records,source_sha256=digest(source/'event_models.joblib'),
        development_first400_cases_only=True,no_new_training_rows=True,benchmark_unchanged=True,
        sklearn_estimators_refitted=False))


if __name__=='__main__':
    from .rate_conditioned import run as canonical_run
    canonical_run()
