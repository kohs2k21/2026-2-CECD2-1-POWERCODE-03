"""Joint, causal minute-objective selection without replacing Process rows.

The replay clock queries existing incident state; it can never open a new alarm.
Benchmark truth is used only by the development objective, never by replay.
"""
import argparse
from dataclasses import dataclass, asdict
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .data import ROOT, digest, write_json
from .models import metrics
from .scope import ACTIVE_TASKS

OUT=ROOT/'reports/process_event_model/minute_refinement'
SOURCE=ROOT/'reports/process_event_model/current'
ALGORITHMS=('if','kmeans','lof')


@dataclass(frozen=True)
class EventPolicy:
    delay_gate: float = 1.
    delay_clear: float = .5
    delay_ttl: float = 300.
    traffic_gate: float = 1.
    traffic_clear: float = .5
    traffic_ttl: float = 180.
    filter_weak: bool = False
    traffic_fast: bool = False
    traffic_clear_fast: bool = False
    traffic_excess_gate: float = 0.
    traffic_quiet_clear: int = 0
    point_tail_gate: float = 1e9
    point_shift_gate: float = 3.
    point_reference_max: int = 4
    traffic_rank: bool = False
    traffic_micro: bool = False
    traffic_ewma: bool = False
    traffic_confirmations: int = 1
    traffic_confirm_seconds: float = 30.
    traffic_ttl_score_gain: float = 0.
    traffic_gap_events: float = 0.
    traffic_model_family: bool = False
    traffic_model_family_clear: bool = False


def traffic_lifetime(ttl, score, current_count, policy):
    """Past-observed arrival-rate timeout; never creates a new alarm."""
    lifetime=np.full_like(np.asarray(score,dtype=float),ttl)
    if policy.traffic_ttl_score_gain>0:
        lifetime*=np.clip(policy.traffic_ttl_score_gain*np.log(np.maximum(score,1.)),0.,1.)
    if policy.traffic_gap_events>0:
        lifetime=np.minimum(lifetime,60.*policy.traffic_gap_events/np.maximum(current_count,1.))
    return lifetime


class MinuteReplay:
    """Vectorized atomic-delivery replay; truth and task are NOT input here."""
    def __init__(self, frame, windows):
        # uid isolates real execution histories in the test harness only.
        keys=sorted(set(frame._uid)|set(windows._uid))
        codes={k:i for i,k in enumerate(keys)}
        f=frame.copy()
        f['_group']=f._uid.map(codes)
        w=windows.copy()
        w['_group']=w._uid.map(codes)
        origin=w.groupby('_group')._end.min().to_dict()
        f['_time']=f._time-f._group.map(origin)+f._group*1e7
        w['_end']=w._end-w._group.map(origin)+w._group*1e7
        order=np.lexsort((f._time.to_numpy(),f._group.to_numpy()))
        self.order=order
        self.frame=f.iloc[order].reset_index(drop=True)
        time=self.frame._time.to_numpy(float)
        group=self.frame._group.to_numpy(int)
        self.starts=np.r_[0,np.flatnonzero((np.diff(time)!=0)|(np.diff(group)!=0))+1]
        self.times=time[self.starts]
        self.groups=group[self.starts]
        self.has_rate='past_log_count_1m' in self.frame
        batch_sizes=np.repeat(np.diff(np.r_[self.starts,len(self.frame)]),np.diff(np.r_[self.starts,len(self.frame)]))
        self.recent_count=(np.expm1(self.frame.past_log_count_1m.to_numpy(float))+batch_sizes
                           if self.has_rate else np.zeros(len(self.frame)))
        self.ends=w._end.to_numpy(float)
        self.window_groups=w._group.to_numpy(int)
        self.high=np.searchsorted(self.times,self.ends,side='left')
        self.low=np.searchsorted(self.times,self.ends-60,side='left')
        self.last=self.high-1
        valid=self.last>=0
        self.valid=valid & (self.groups[np.maximum(self.last,0)]==self.window_groups)
        self.warm=self.frame.history_support.to_numpy(float)>=np.log1p(3)
        self.upper=(self.frame.native_latency_run.to_numpy(float) if 'native_latency_run' in self.frame else
                    np.maximum(self.frame.adaptive_upper.to_numpy(float),
                              self.frame.adaptive_mean_shift.to_numpy(float))
                    if 'adaptive_upper' in self.frame else self.frame.local_upper.to_numpy(float))
        self.traffic=np.maximum(self.frame.predictive_burst_1m.to_numpy(float),
                                self.frame.predictive_burst_3m.to_numpy(float))
        self.traffic_fast=self.frame.predictive_burst_1m.to_numpy(float)
        self.traffic_slow=self.frame.predictive_burst_3m.to_numpy(float)
        if 'native_traffic_llr' in self.frame:
            self.traffic=np.log1p(self.frame.native_traffic_llr.to_numpy(float))
        self.traffic_excess=(self.frame.native_traffic_current.to_numpy(float)
                             if 'native_traffic_current' in self.frame else np.zeros(len(self.frame)))
        self.quiet=(self.frame.native_quiet_run.to_numpy(float)
                    if 'native_quiet_run' in self.frame else np.zeros(len(self.frame)))
        self.point_tail=self.frame.row_upper_tail.to_numpy(float) if 'row_upper_tail' in self.frame else np.zeros(len(self.frame))
        self.point_shift=self.frame.local_upper.to_numpy(float)
        self.point_level=self.frame.reference_level.to_numpy(float) if 'reference_level' in self.frame else np.full(len(self.frame),4)
        self.rank=self.frame.native_traffic_rank.to_numpy(float) if 'native_traffic_rank' in self.frame else np.zeros(len(self.frame))
        self.previous_rank=self.frame.native_previous_traffic_rank.to_numpy(float) if 'native_previous_traffic_rank' in self.frame else np.zeros(len(self.frame))
        self.micro_density=self.frame.micro_density.to_numpy(float) if 'micro_density' in self.frame else np.zeros(len(self.frame))
        self.micro_signal=(np.log1p(self.frame.micro_cusum.to_numpy(float))*np.log1p(self.micro_density)
                           if 'micro_cusum' in self.frame else np.zeros(len(self.frame)))
        self.ewma_fast=self.frame.ewma_rate_lift_1m.to_numpy(float) if 'ewma_rate_lift_1m' in self.frame else np.zeros(len(self.frame))
        self.ewma_slow=self.frame.ewma_rate_lift_3m.to_numpy(float) if 'ewma_rate_lift_3m' in self.frame else np.zeros(len(self.frame))

    def project(self, scores, policy, diagnostics=False, families=None):
        # scores correspond to the original frame order; simultaneous delivery
        # rows share state and are reduced atomically before any clock query.
        score=np.asarray(scores)[self.order]
        alarm=score>1.
        delay=self.warm & (self.upper>=policy.delay_gate)
        corroborated=self.warm & (self.point_tail>=policy.point_tail_gate) & (self.point_shift>=policy.point_shift_gate)
        corroborated &= self.point_level<=policy.point_reference_max
        delay |= corroborated
        signal=np.maximum(self.ewma_fast,self.ewma_slow) if policy.traffic_ewma else self.micro_signal if policy.traffic_micro else self.rank if policy.traffic_rank else self.traffic
        traffic=self.warm & (signal>=policy.traffic_gate)
        family=None
        if policy.traffic_model_family:
            if families is None:raise ValueError('Calibrated evidence families are required')
            family=np.asarray(families)[self.order]
            traffic=self.warm&(family==3)
        if policy.traffic_excess_gate>0:traffic &= self.traffic_excess>=policy.traffic_excess_gate
        if policy.traffic_fast:traffic &= self.traffic_fast>=self.traffic_slow
        pending=np.zeros(len(score),bool)
        if policy.traffic_confirmations>1:
            candidates=alarm&traffic&~delay
            batch_counts=np.add.reduceat(candidates.astype(int),self.starts)
            cumulative=np.r_[0,np.cumsum(batch_counts)]
            lo=np.searchsorted(self.times,self.times-policy.traffic_confirm_seconds,side='left')
            count=cumulative[np.arange(len(self.times))+1]-cumulative[lo]
            confirmed=np.repeat(count>=policy.traffic_confirmations,np.diff(np.r_[self.starts,len(score)]))
            pending=candidates&~confirmed
            traffic &= ~pending
        fresh=alarm & (~self.warm|delay|traffic) if policy.filter_weak else alarm
        fresh &= ~pending
        hits=np.maximum.reduceat(fresh,self.starts)
        count=np.r_[0,np.cumsum(hits)]
        result=count[self.high]>count[self.low]
        parts={'fresh':result.copy()} if diagnostics else None
        clear_signal=self.micro_density if policy.traffic_micro else self.previous_rank if policy.traffic_rank else self.traffic_fast if policy.traffic_clear_fast else self.traffic
        if policy.traffic_ewma:clear_signal=self.ewma_fast if policy.traffic_clear_fast else np.maximum(self.ewma_fast,self.ewma_slow)
        traffic_clear=clear_signal<=policy.traffic_clear
        if policy.traffic_model_family_clear:
            if family is None:raise ValueError('Family-based recovery requires model-family onset')
            traffic_clear |= (family!=3)|~alarm
        if policy.traffic_quiet_clear>0:traffic_clear |= self.quiet>=policy.traffic_quiet_clear
        index=np.arange(len(self.times))
        for name,onset,clear,ttl in (
            ('delay',alarm & delay, self.upper<=policy.delay_clear, policy.delay_ttl),
            ('traffic',alarm & traffic, traffic_clear, policy.traffic_ttl)):
            if ttl<=0:continue
            on=np.maximum.reduceat(onset,self.starts)
            # Any qualifying event beats a clearing event in the same delivery.
            off=np.minimum.reduceat(clear,self.starts) & ~on
            last_on=np.maximum.accumulate(np.where(on,index,-1))
            last_off=np.maximum.accumulate(np.where(off,index,-1))
            event_on=last_on[np.maximum(self.last,0)]
            event_off=last_off[np.maximum(self.last,0)]
            source=np.maximum(event_on,0)
            active=self.valid & (event_on>=0) & (event_on>event_off)
            active &= self.groups[source]==self.window_groups
            lifetimes=ttl
            if name=='traffic' and (policy.traffic_ttl_score_gain>0 or policy.traffic_gap_events>0):
                if policy.traffic_gap_events>0 and not self.has_rate:raise ValueError('Past arrival counts required')
                strength=np.maximum.reduceat(np.where(onset,score,1.),self.starts)
                rates=np.maximum.reduceat(self.recent_count,self.starts)
                lifetimes=traffic_lifetime(ttl,strength[source],rates[source],policy)
            active &= self.ends-self.times[source]<=lifetimes
            result |= active
            if diagnostics:parts[name]=active
        return (result,parts) if diagnostics else result


def prepare(folder=SOURCE, confirmation=False):
    folder=Path(folder)
    package=joblib.load(folder/'event_models.joblib')
    sha=digest(folder/'event_models.joblib')
    cache=OUT/f"{'confirmation' if confirmation else 'development'}_{sha[:12]}.joblib"
    if cache.exists():return package,joblib.load(cache)
    bank=None if confirmation else joblib.load(ROOT/'reports/process_event_model/predictive_counts/development.joblib')
    feature_folder=None
    if confirmation:
        import hashlib
        signature=hashlib.sha256(joblib.hash(package['reference']).encode()).hexdigest()[:12]
        signature+='-predictive-'+digest(ROOT/'code/process_event_model/predictive.py')[:12]
        feature_folder=ROOT/'reports/process_event_model/confirmation_features'/signature
    frames=[];windows=[];truth=[];tasks=[];old={}
    for ti,t in enumerate(ACTIVE_TASKS):
        if confirmation:
            pair,cases=joblib.load(feature_folder/f'{t}_400.joblib')
            part={c:f.loc[f.target].copy() for c,f in pair.items()}
            ids=cases.case_id
        else:
            part={c:f.loc[f.target].copy() for c,f in bank[t].groupby('cohort',sort=False)}
            ids=part['observed'].case_id.unique()
        w=pd.read_csv(ROOT/'data/evaluation'/t/'window_labels.csv',encoding='utf-8-sig')
        w=w.loc[w.scope.eq('TARGET') & w.case_id.isin(ids)].copy()
        w['_end']=(pd.to_datetime(w.window_end,format='ISO8601').dt.tz_localize('Asia/Seoul')
                   .dt.tz_convert('UTC').astype('datetime64[ns, UTC]').astype('int64')/1e9)
        for c in ('observed','normal'):
            f=part[c]
            f['_uid']=t+':'+c+':'+f.case_id.astype(str)
            f['_time']=f.availableTime.astype('datetime64[ns, UTC]').astype('int64')/1e9
            for a,m in package['models'].items():
                f[f'_score_{a}']=m.predict(f)[0]
                f[f'_state_{a}']=m.state(f)[0]
            frames.append(f)
            ww=w.copy();ww['_uid']=t+':'+c+':'+ww.case_id.astype(str)
            windows.append(ww)
            truth.append(w.window_is_anomaly.to_numpy(bool) if c=='observed' else np.zeros(len(w),bool))
            tasks.append(np.full(len(w),ti))
        z=np.load(ROOT/'reports/shared_model_v7/release'/f'{t}_scores.npz')
        mask=z['case_index']>=400 if confirmation else z['case_index']<400
        old[t]={a:metrics(z['y'][mask],z['scores'][mask,j]>1.) for j,a in enumerate(ALGORITHMS)}
        print('Minute objective data:',t,confirmation,flush=True)
    frame=pd.concat(frames,ignore_index=True)
    w=pd.concat(windows,ignore_index=True)
    result=dict(frame=frame,windows=w,truth=np.concatenate(truth),tasks=np.concatenate(tasks),legacy=old)
    joblib.dump(result,cache,compress=3)
    return package,result


def results(data, predicted):
    return {t:metrics(data['truth'][data['tasks']==i],predicted[data['tasks']==i])
            for i,t in enumerate(ACTIVE_TASKS)}


def quality(rows, legacy):
    values=np.array([rows[t]['f1'] for t in ACTIVE_TASKS])
    target=np.array([legacy[t]['f1'] for t in ACTIVE_TASKS])
    violation=sum(max(m['false_positive_rate']-.06,0.) for m in rows.values())
    # Minimize per-scenario loss first: aggregate gains cannot hide a weak task.
    gaps=np.maximum(target-values,0.)
    return float(values.mean()-2*gaps.sum()-100*violation)


def tune():
    OUT.mkdir(parents=True,exist_ok=True)
    package,data=prepare()
    replay=MinuteReplay(data['frame'],data['windows'])
    selected={};history=[]
    for a in ALGORITHMS:
        f=data['frame'];base=f[f'_score_{a}'].to_numpy(float);groups=f[f'_state_{a}'].to_numpy(int)
        multipliers=np.ones(8)
        params=asdict(EventPolicy())
        def score(mult,p):
            r=results(data,replay.project(base/mult[groups],EventPolicy(**p)))
            return quality(r,{t:data['legacy'][t][a] for t in ACTIVE_TASKS}),r
        best,rows=score(multipliers,params)
        # Coordinate optimization changes score cutoffs AND event hysteresis,
        # unlike the preceding row-objective threshold and fixed-alert searches.
        options=dict(delay_gate=(.3,1.,2.,3.,5.,8.),delay_clear=(0.,.3,.5,1.,2.),
                     delay_ttl=(60.,120.,180.,300.,600.),
                     traffic_gate=(.3,1.,2.,3.,5.),traffic_clear=(-1.,0.,.3,1.,2.),
                     traffic_ttl=(30.,60.,90.,120.,180.,300.,600.),filter_weak=(False,True),
                     traffic_fast=(False,True),traffic_clear_fast=(False,True))
        cut_options={}
        for g in range(8):
            v=base[(groups==g)&~f.label.to_numpy(bool)]
            cut_options[g]=np.maximum(np.unique(np.r_[.35,.5,.65,.8,.9,1.,1.1,1.25,1.5,2.,3.,
                np.quantile(v,[.5,.7,.85,.9,.94,.97,.98,.99,.995,.999]) if len(v) else []]),1e-6)
        for sweep in range(5):
            changed=False
            for g in (4,0,1,2,3,5,6,7):
                for value in cut_options[g]:
                    trial=multipliers.copy();trial[g]=value
                    q,r=score(trial,params)
                    if q>best+1e-10:
                        multipliers=trial;best=q;rows=r;changed=True
            for name,values in options.items():
                for value in values:
                    p=dict(params);p[name]=value
                    q,r=score(multipliers,p)
                    if q>best+1e-10:
                        params=p;best=q;rows=r;changed=True
            print('JOINT MINUTE',a,sweep,round(best,4),{t:round(m['f1'],4) for t,m in rows.items()},flush=True)
            if not changed:break
        selected[a]=dict(multipliers=multipliers.tolist(),policy=params,tasks=rows,objective=best,
                         legacy={t:data['legacy'][t][a] for t in ACTIVE_TASKS})
        write_json(OUT/f'{a}_development.json',selected[a])
    write_json(OUT/'selection.json',dict(models=selected,source_model_sha256=digest(SOURCE/'event_models.joblib'),
        scope=list(ACTIVE_TASKS),selection_cases_per_task=400,evaluation_unit='same legacy minute queries',
        no_new_Process_rows=True,no_silence_onset=True,no_task_routing_at_inference=True,
        previously_observed_benchmark_not_external_blind_test=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    prepare() if args.prepare_only else tune()
