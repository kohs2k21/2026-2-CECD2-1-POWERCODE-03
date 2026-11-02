"""Live counterpart of minute-objective replay; no silence-triggered onset."""
import numpy as np
import pandas as pd
from collections import deque

from .features import timestamps
from .minute_refinement import EventPolicy,traffic_lifetime


class EventPolicyTracker:
    def __init__(self,**params):
        self.policy=EventPolicy(**params)
        self.states={}

    def update(self,predictions,detector=None):
        f=predictions.copy().reset_index(drop=True);p=self.policy
        warm=f.history_support.to_numpy(float)>=np.log1p(3)
        if 'native_latency_run' in f:upper=f.native_latency_run.to_numpy(float)
        elif 'adaptive_upper' in f:upper=np.maximum(f.adaptive_upper,f.adaptive_mean_shift)
        else:upper=f.local_upper.to_numpy(float)
        fast=f.predictive_burst_1m.to_numpy(float);slow=f.predictive_burst_3m.to_numpy(float)
        traffic=np.log1p(f.native_traffic_llr.to_numpy(float)) if 'native_traffic_llr' in f else np.maximum(fast,slow)
        if p.traffic_rank:
            if 'native_traffic_rank' not in f:raise ValueError('Native traffic rank features required')
            traffic=f.native_traffic_rank.to_numpy(float)
        if p.traffic_micro:
            traffic=np.log1p(f.micro_cusum.to_numpy(float))*np.log1p(f.micro_density.to_numpy(float))
        if p.traffic_ewma:traffic=np.maximum(f.ewma_rate_lift_1m.to_numpy(float),f.ewma_rate_lift_3m.to_numpy(float))
        delay=warm&(upper>=p.delay_gate);busy=warm&(traffic>=p.traffic_gate)
        if p.point_tail_gate<1e9:
            corroborated=warm&(f.row_upper_tail.to_numpy(float)>=p.point_tail_gate)&(f.local_upper.to_numpy(float)>=p.point_shift_gate)
            corroborated &= f.reference_level.to_numpy(float)<=p.point_reference_max
            delay |= corroborated
        if p.traffic_excess_gate>0:
            excess=f.native_traffic_current.to_numpy(float) if 'native_traffic_current' in f else np.zeros(len(f))
            busy &= excess>=p.traffic_excess_gate
        if p.traffic_fast:busy &= fast>=slow
        alarm=f.anomaly.to_numpy(bool)
        family=None
        if p.traffic_model_family:
            if 'calibrated_evidence_family' in f:family=f.calibrated_evidence_family.to_numpy(int)
            elif detector is not None:family=detector.state(f)[0]
            else:raise ValueError('Calibrated evidence families are required')
            busy=warm&(family==3)
            if p.traffic_excess_gate>0:busy &= excess>=p.traffic_excess_gate
            if p.traffic_fast:busy &= fast>=slow
        confirmed=alarm&(~warm|delay|busy) if p.filter_weak else alarm.copy()
        pending=np.zeros(len(f),bool);confirmation_count=np.zeros(len(f),int)
        clock=f.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
        active=np.zeros(len(f),bool);actions=np.full(len(f),'none',object);expiry=np.full(len(f),np.nan)
        clear_traffic=f.micro_density.to_numpy(float) if p.traffic_micro else f.native_previous_traffic_rank.to_numpy(float) if p.traffic_rank else fast if p.traffic_clear_fast else traffic
        if p.traffic_ewma:clear_traffic=f.ewma_rate_lift_1m.to_numpy(float) if p.traffic_clear_fast else traffic
        clear_mask=clear_traffic<=p.traffic_clear
        if p.traffic_model_family_clear:
            if family is None:raise ValueError('Family-based recovery requires model-family onset')
            clear_mask |= (family!=3)|~alarm
        if p.traffic_quiet_clear>0:
            if 'native_quiet_run' not in f:raise ValueError('Completed-minute recovery features are required by this policy')
            clear_mask |= f.native_quiet_run.to_numpy(float)>=p.traffic_quiet_clear
        for key,ids in f.groupby(['case_id','adapterType','processId'],sort=False,observed=True).indices.items():
            ids=np.asarray(ids);ids=ids[np.argsort(clock[ids],kind='stable')]
            s=self.states.setdefault(key,dict(delay=-np.inf,traffic=-np.inf,last=-np.inf,
                candidates=deque(maxlen=max(p.traffic_confirmations,1))))
            for now in np.unique(clock[ids]):
                if now<=s['last']:raise ValueError('Out-of-order or split delivery in event policy')
                batch=ids[clock[ids]==now]
                before=max(s['delay'],s['traffic'])>=now
                eligible=busy[batch].copy()
                if p.traffic_confirmations>1:
                    candidate=alarm[batch]&eligible&~delay[batch]
                    queue=s['candidates']
                    while queue and queue[0]<now-p.traffic_confirm_seconds:queue.popleft()
                    queue.extend([now]*min(int(candidate.sum()),p.traffic_confirmations))
                    confirmation_count[batch]=len(queue)
                    if len(queue)<p.traffic_confirmations:
                        rejected=batch[candidate]
                        pending[rejected]=True;confirmed[rejected]=False
                        eligible[candidate]=False
                if (alarm[batch]&delay[batch]).any() and p.delay_ttl>0:s['delay']=now+p.delay_ttl
                elif (upper[batch]<=p.delay_clear).all():s['delay']=-np.inf
                if (alarm[batch]&eligible).any() and p.traffic_ttl>0:
                    lifetime=p.traffic_ttl
                    if p.traffic_ttl_score_gain>0 or p.traffic_gap_events>0:
                        strength=f.iloc[batch].loc[alarm[batch]&eligible,'score'].max()
                        if p.traffic_gap_events>0 and 'past_log_count_1m' not in f:raise ValueError('Past arrival counts required')
                        count=np.expm1(f.iloc[batch].past_log_count_1m.max())+len(batch) if 'past_log_count_1m' in f else 0.
                        lifetime=float(traffic_lifetime(lifetime,strength,count,p))
                    s['traffic']=now+lifetime
                elif clear_mask[batch].all():s['traffic']=-np.inf
                end=max(s['delay'],s['traffic']);after=end>=now
                active[batch]=after;expiry[batch]=end if after else np.nan
                actions[batch]='update' if after and before else 'open' if after else 'close' if before else 'point' if confirmed[batch].any() else 'pending' if pending[batch].any() else 'none'
                s['last']=now
        f['confirmed_anomaly']=confirmed
        f['traffic_candidate_pending']=pending
        f['traffic_confirmation_count_capped']=confirmation_count
        f['incident_active']=active;f['incident_action']=actions
        f['incident_expires_at']=pd.to_datetime(expiry,unit='s',utc=True)
        return f

    def snapshot(self,now):
        seconds=timestamps(pd.Series([now])).iloc[0].value/1e9
        if any(seconds<s['last'] for s in self.states.values()):
            raise ValueError('Cannot query historical state after processing later deliveries')
        rows=[]
        for (case,adapter,process),s in self.states.items():
            end=max(s['delay'],s['traffic'])
            if end>=seconds:
                rows.append(dict(case_id=case,adapterType=adapter,sourceProcessId=process,status='active',
                    expires_at=pd.to_datetime(end,unit='s',utc=True),
                    observation_kind='existing_incident_state_not_Process_row'))
        return pd.DataFrame(rows)
