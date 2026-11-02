"""Calendar count-tail references; existing development controls only.

Counts for covered synthetic normal sessions may include true empty minutes
internally. Those are reference statistics, never invented Process input or
estimator training rows. No current-session future count is ever consulted.
"""
import copy
import json

import joblib
import numpy as np
import pandas as pd

from .adapter_score_calibration import AdapterTrafficDetector
from .adaptive_models import NativeDetector
from .rate_conditioned import RateTrafficDetector
from .data import ROOT,write_json,digest
from .minute_refinement import ALGORITHMS,ACTIVE_TASKS,EventPolicy,MinuteReplay,results,quality


def append_observed_count(frame):
    """All rows of one delivery see the same already-observed minute count."""
    f=frame.copy()
    keys=['cohort','case_id','adapterType','processId']
    minute=(f.availableTime.astype('datetime64[ns, UTC]').astype('int64')//60_000_000_000).to_numpy()
    temp=f[keys+['availableTime']].copy();temp['_minute']=minute;temp['_row']=np.arange(len(f))
    temp=temp.sort_values(keys+['availableTime'],kind='stable')
    temp['_count']=temp.groupby(keys+['_minute'],sort=False,observed=True).cumcount()+1
    temp['_count']=temp.groupby(keys+['availableTime'],sort=False,observed=True)._count.transform('max')
    f['native_observed_minute_count']=temp.sort_values('_row')._count.to_numpy().clip(0,100)
    return f


def fit_count_reference(f,*,covered_normal_sessions=False):
    if not covered_normal_sessions:
        raise ValueError('Known continuous normal coverage is required; sparse original logs are not proof of zero executions')
    values={};all_counts=[];sessions=0
    for _,part in f.loc[f.cohort.eq('normal')].groupby('_uid',sort=False):
        times=part.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()//60_000_000_000
        low,high=int(times.min()+1),int(times.max())
        if high-low<10:continue
        minutes=np.arange(low,high);counts=np.bincount(times-times.min(),minlength=int(times.max()-times.min()+1))[1:-1]
        assert len(counts)==len(minutes)
        calendar=pd.to_datetime(minutes*60,unit='s',utc=True).tz_convert('Asia/Seoul')
        for weekend,hour,count in zip(calendar.weekday>=5,calendar.hour,counts):
            values.setdefault((int(weekend),int(hour)),[]).append(int(count))
        all_counts.extend(counts.tolist());sessions+=1
    if sessions<20 or not all_counts:raise ValueError('Insufficient covered normal sessions for a count reference')
    def table(v):
        counts=np.bincount(v,minlength=16)
        return {'counts':counts.tolist(),'minutes':len(v)}
    return dict(global_table=table(all_counts),calendar_tables={f'{w}:{h}':table(v) for (w,h),v in values.items() if len(v)>=60},
        normal_sessions=sessions,normal_covered_minutes=len(all_counts),minimum_calendar_minutes=60,
        global_shrinkage_minutes=30.,source='existing first400 development normal controls',
        no_generated_Process_rows=True,zero_counts_only_inside_known_covered_synthetic_normal_sessions=True)


def count_tail(f,reference):
    calendar=f.availableTime.dt.tz_convert('Asia/Seoul')
    keys=(calendar.dt.weekday.ge(5).astype(int).astype(str)+':'+calendar.dt.hour.astype(str)).to_numpy()
    count=f.native_observed_minute_count.to_numpy(int)
    result=np.zeros(len(f));global_table=reference['global_table'];global_n=global_table['minutes']
    global_counts=np.asarray(global_table['counts']);global_tail=np.r_[np.cumsum(global_counts[::-1])[::-1],0.]
    for key in np.unique(keys):
        ids=np.flatnonzero(keys==key);k=count[ids]
        gp=(global_tail[np.minimum(k,len(global_tail)-1)]+.5)/(global_n+1.)
        local=reference['calendar_tables'].get(key)
        if local:
            hist=np.asarray(local['counts']);tail=np.r_[np.cumsum(hist[::-1])[::-1],0.]
            pseudo=reference['global_shrinkage_minutes']
            probability=(tail[np.minimum(k,len(tail)-1)]+pseudo*gp+.5)/(local['minutes']+pseudo+1.)
        else:probability=gp
        result[ids]=-np.log(np.maximum(probability,1e-12))
    return np.clip(result,0,20)


class CalendarTrafficDetector(AdapterTrafficDetector):
    def __init__(self,parent,reference,power=.5,weight=.15,cutoff=2.,scale=1.):
        super().__init__(parent,parent.traffic_factors,parent.traffic_support)
        self.count_reference=reference;self.count_power=power;self.count_weight=weight
        self.count_cutoff=cutoff;self.count_scale=scale
        self.past_rate_factors=copy.deepcopy(getattr(parent,'rate_factors',None))
        self.past_dispersion_factors=copy.deepcopy(getattr(parent,'dispersion_factors',None))

    def traffic_factor(self,f):
        factor=super().traffic_factor(f)
        if self.past_rate_factors is not None:
            r,d=RateTrafficDetector.codes(f)
            factor=factor*self.past_rate_factors[r]*self.past_dispersion_factors[d]
        return factor

    def peak(self,f):
        return NativeDetector.peaks(f)[:,3]**(1-self.count_power)*count_tail(f,self.count_reference)**self.count_power

    def normalized(self,f):
        raw=self.base.raw_score(f);cold,mode,warm,point=self.model.parts(f,raw)
        channels=warm/self.model.cutoffs[:4];peak=self.peak(f)
        center,noise=self.model.model_stats.get(3,self.model.default_stats)
        learned=np.log1p(np.maximum((raw-center)/noise,0.))
        channels[:,3]=(self.count_weight*learned+(1-self.count_weight)*np.log1p(peak/self.count_scale))/self.count_cutoff/self.traffic_factor(f)
        channels[peak<=0,3]=0.
        group=channels.argmax(axis=1);score=channels.max(axis=1)
        group[cold]=mode[cold];score[cold]=point[cold]/self.model.cutoffs[mode[cold]]/self.factor(f)[cold]
        return group,score

    def calibration_context(self,f):
        return {**super().calibration_context(f),'calendar_count_upper_tail':count_tail(f,self.count_reference)}

    def record(self):
        return {**super().record(),'calendar_count_reference':self.count_reference,
            'count_power':self.count_power,'count_model_weight':self.count_weight,
            'count_cutoff':self.count_cutoff,'count_stat_scale':self.count_scale,
            'normal_reference_includes_existing_synthetic_dev_controls':True,
            'original_only_sklearn_estimator_fit':True}


def run():
    source=ROOT/'reports/process_event_model/event_candidate';out=ROOT/'reports/process_event_model/calendar_traffic';out.mkdir(exist_ok=True)
    package=joblib.load(source/'event_models.joblib');sel=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib');data['frame']=append_observed_count(data['frame']);f=data['frame']
    reference=fit_count_reference(f,covered_normal_sessions=True);write_json(out/'count_reference.json',reference)
    rp=MinuteReplay(f,data['windows']);models={};records={}
    tail=count_tail(f,reference);native=NativeDetector.peaks(f)[:,3]
    for a in ALGORITHMS:
        parent=package['models'][a];base=parent.model;raw=base.base.raw_score(f)
        cold,mode,warm,point=base.parts(f,raw);other=(warm[:,:3]/base.cutoffs[:3]).max(axis=1)
        cold_score=point/base.cutoffs[mode]/parent.factor(f);factor=parent.traffic_factor(f)
        params=dict(sel['models'][a]['policy']);legacy={t:data['legacy'][t][a] for t in ACTIVE_TASKS}
        baseline=results(data,rp.project(parent.raw_score(f),EventPolicy(**params)))
        def objective(rows):
            if any(m['false_positive_rate']>.06 for m in rows.values()):return -1e6
            if any(rows[t]['f1']<min(baseline[t]['f1'],legacy[t]['f1'])-.01 for t in ACTIVE_TASKS if t!='time_burst'):return -1e6
            return quality(rows,legacy)-.25*max(legacy['time_burst']['precision']-rows['time_burst']['precision'],0.)
        best=objective(baseline);rows=baseline;chosen={'source_preserved':True};best_model=copy.deepcopy(parent);best_params=dict(params)
        center,noise=base.model_stats.get(3,base.default_stats);learned=np.log1p(np.maximum((raw-center)/noise,0.))
        clean=(~cold)&f.cohort.eq('normal').to_numpy()
        for power in (.25,.5,.75,1.):
            peak=native**(1-power)*tail**power;scale=max(float(np.quantile(peak[clean],.95)),.25)
            statistic=np.log1p(peak/scale)
            for weight in (.03,.15,.35,.65):
                channel=np.where(peak>0,weight*learned+(1-weight)*statistic,0.)/factor
                cuts=np.unique(np.r_[.3,.5,.7,1.,1.5,2.,base.cutoffs[3],np.quantile(channel[clean],[.85,.94,.97,.98,.99,.995])])
                for cutoff in cuts:
                    score=np.maximum(other,channel/max(cutoff,1e-8));score[cold]=cold_score[cold]
                    for ttl in (60.,120.,180.,240.,300.):
                        p=dict(params,traffic_ttl=ttl);r=results(data,rp.project(score,EventPolicy(**p)));q=objective(r)
                        if q>best+1e-10:
                            best=q;rows=r;best_params=p;chosen=dict(power=power,weight=weight,cutoff=float(cutoff),scale=scale)
                            best_model=CalendarTrafficDetector(parent,reference,power,weight,cutoff,scale)
            print('CALENDAR TRAFFIC',a,power,chosen,{t:round(m['f1'],4) for t,m in rows.items()},
                'P',round(rows['time_burst']['precision'],4),flush=True)
        assert results(data,rp.project(best_model.raw_score(f),EventPolicy(**best_params)))==rows
        models[a]=best_model;records[a]=dict(policy=best_params,tasks=rows,objective=best,selected=chosen,settings=vars(best_model.settings))
        write_json(out/'progress.json',dict(models=records))
    joblib.dump({**package,'models':models},out/'event_models.joblib',compress=3)
    joblib.dump(data,out/'minute_development.joblib',compress=3)
    write_json(out/'selection.json',dict(models=records,source_sha256=digest(source/'event_models.joblib'),
        development_first400_cases_only=True,reference_uses_existing_normal_controls=True,
        no_generated_normal_logs=True,original_only_sklearn_fit=True,benchmark_unchanged=True))


if __name__=='__main__':
    from .calendar_traffic import run as canonical_run
    canonical_run()
