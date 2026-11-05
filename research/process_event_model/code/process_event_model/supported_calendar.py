"""Use learned calendar count references only inside their rate support.

An absolute count reference from sparse development sessions must NOT silently
be applied to a real Process normally executing hundreds of times per minute.
Outside covered historical rates, the preceding local-context score is used.
"""
import copy
import json

import joblib
import numpy as np

from .calendar_traffic import CalendarTrafficDetector
from .data import ROOT,write_json,digest
from .minute_refinement import ALGORITHMS,ACTIVE_TASKS,EventPolicy,MinuteReplay,results,quality


def supported(f,bounds):
    exposure=f.predictive_exposure_1m.to_numpy(float)
    rate=f.predictive_support_1m.to_numpy(float)/np.maximum(exposure,1.)
    return (exposure>=3.)&(rate>=bounds['lower_rate'])&(rate<=bounds['upper_rate'])


class SupportedCalendarDetector(CalendarTrafficDetector):
    def __init__(self,calendar,bounds):
        self.__dict__=copy.deepcopy(calendar.__dict__)
        self.rate_support=dict(bounds)

    def normalized(self,f):
        raw=self.base.raw_score(f);cold,mode,warm,point=self.model.parts(f,raw)
        channels=warm/self.model.cutoffs[:4]
        channels[:,3]/=self.traffic_factor(f)
        peak=self.peak(f);center,noise=self.model.model_stats.get(3,self.model.default_stats)
        learned=np.log1p(np.maximum((raw-center)/noise,0.))
        new=(self.count_weight*learned+(1-self.count_weight)*np.log1p(peak/self.count_scale))/self.count_cutoff/self.traffic_factor(f)
        new[peak<=0]=0.
        mask=supported(f,self.rate_support)
        channels[mask,3]=new[mask]
        group=channels.argmax(axis=1);score=channels.max(axis=1)
        group[cold]=mode[cold];score[cold]=point[cold]/self.model.cutoffs[mode[cold]]/self.factor(f)[cold]
        return group,score

    def calibration_context(self,f):
        return {**super().calibration_context(f),'calendar_count_reference_supported':supported(f,self.rate_support)}

    def record(self):
        return {**super().record(),'past_rate_support':self.rate_support,
            'outside_support_uses_preceding_local_context_score':True}


def run():
    base=ROOT/'reports/process_event_model';source=base/'calendar_traffic';out=base/'supported_calendar';out.mkdir(exist_ok=True)
    package=joblib.load(source/'event_models.joblib');selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    fallback=joblib.load(base/'event_candidate/event_models.joblib')
    fallback_sel=json.loads((base/'event_candidate/selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib');f=data['frame'];rp=MinuteReplay(f,data['windows'])
    healthy=f.loc[f.cohort.eq('normal')&f.predictive_exposure_1m.ge(3)].copy()
    healthy['rate']=healthy.predictive_support_1m/healthy.predictive_exposure_1m
    # One rate estimate per independent healthy session, not busy-row weighting.
    session_rates=healthy.groupby('_uid',sort=False).rate.median().to_numpy(float)
    low,high=np.quantile(session_rates,[.005,.995])
    bounds=dict(lower_rate=float(low),upper_rate=float(high),normal_sessions=len(session_rates),
        minimum_past_exposure_minutes=3.,quantiles=[.005,.995],source='existing development NORMAL sessions only')
    models={};records={}
    for a in ALGORITHMS:
        parent=package['models'][a]
        if isinstance(parent,CalendarTrafficDetector):
            model=SupportedCalendarDetector(parent,bounds)
            scores=model.raw_score(f);old=fallback['models'][a].raw_score(f)
            np.testing.assert_allclose(scores[~supported(f,bounds)],old[~supported(f,bounds)],rtol=1e-12,atol=1e-12)
            params=selection['models'][a]['policy'];rows=results(data,rp.project(scores,EventPolicy(**params)))
            prior_params=fallback_sel['models'][a]['policy']
            prior_rows=results(data,rp.project(old,EventPolicy(**prior_params)))
            legacy={t:data['legacy'][t][a] for t in ACTIVE_TASKS}
            def objective(r):
                if any(m['false_positive_rate']>.06 for m in r.values()):return -1e6
                return quality(r,legacy)-.25*max(legacy['time_burst']['precision']-r['time_burst']['precision'],0.)
            if objective(rows)<objective(prior_rows):model=fallback['models'][a];params=prior_params;rows=prior_rows
        else:
            model=parent;params=selection['models'][a]['policy'];rows=selection['models'][a]['tasks']
        models[a]=model;records[a]=dict(policy=params,tasks=rows,settings=vars(model.settings),model_record=model.record())
        print('SUPPORTED CALENDAR',a,{t:round(m['f1'],4) for t,m in rows.items()},'burstP',round(rows['time_burst']['precision'],4),flush=True)
    joblib.dump({**package,'models':models},out/'event_models.joblib',compress=3)
    joblib.dump(data,out/'minute_development.joblib',compress=3)
    write_json(out/'selection.json',dict(models=records,source_sha256=digest(source/'event_models.joblib'),
        development_first400_cases_only=True,rate_reference_support=bounds,benchmark_unchanged=True,
        original_only_sklearn_fit=True,additional_count_reference_uses_existing_synthetic_normal_controls=True,
        outside_rate_support_fallback_verified=True))


if __name__=='__main__':
    from .supported_calendar import run as canonical_run
    canonical_run()
