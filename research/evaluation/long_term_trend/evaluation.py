"""Causal decision intervals, normal budgets, and actual-onset event evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

MINUTE_NS=60_000_000_000


def ns(values):
    return pd.to_datetime(values,utc=True).dt.as_unit('ns').astype('int64').to_numpy()


def decision_grid(metadata,cadence):
    return (ns(metadata['decision_tick'])//MINUTE_NS)%int(cadence)==0


def decisions(scores,metadata,cadence,threshold):
    grid=decision_grid(metadata,cadence)
    result=metadata.loc[grid,['row_id','group','decision_tick','state','eligible']].copy()
    values=np.asarray(scores,float)[grid]
    result['score']=values
    result['decision_ready']=result['eligible'].to_numpy(bool)&np.isfinite(values)
    result.loc[result.eligible&~result.decision_ready,'state']='MODEL_NOT_READY'
    result['prediction']=pd.array([bool(v>threshold) if ready else None for v,ready in
                                  zip(values,result['decision_ready'])],dtype='boolean')
    return result.reset_index(drop=True)


def decision_intervals(metadata,decision_rows,cadence):
    """A decision starts at its emit tick; coverage gates never backdate it."""
    parts=[]
    for group,meta in metadata.groupby('group',sort=False):
        meta=meta.sort_values('decision_tick').reset_index(drop=True)
        tick=ns(meta.decision_tick)
        if len(tick)>1 and not np.all(np.diff(tick)==MINUTE_NS):
            raise ValueError('Expected a complete one-minute decision timeline per group')
        issued=decision_rows.loc[decision_rows.group.eq(group)].sort_values('decision_tick')
        issued_tick=ns(issued.decision_tick)
        previous=np.searchsorted(issued_tick,tick,side='right')-1
        available=previous>=0
        safe=np.maximum(previous,0)
        eligible=meta.eligible.to_numpy(bool)
        if len(issued):
            previous_issued_index=np.searchsorted(tick,issued_tick)
            blocked_prefix=np.r_[0,np.cumsum(~eligible)]
            blocked_since=blocked_prefix[np.arange(len(meta))+1]-blocked_prefix[previous_issued_index[safe]]
            available &= issued.decision_ready.to_numpy(bool)[safe]
            available &= tick<issued_tick[safe]+cadence*MINUTE_NS
            available &= eligible&(blocked_since==0)
            positive=issued.prediction.fillna(False).to_numpy(bool)[safe]&available
        else:
            available[:]=False;positive=np.zeros(len(meta),bool)
        meta['prediction_available']=available
        meta['positive']=positive
        meta['interval_end']=pd.to_datetime(meta.decision_tick,utc=True)+pd.Timedelta(minutes=1)
        parts.append(meta)
    return pd.concat(parts,ignore_index=True)


def alarm_episodes(intervals,merge_gap_minutes):
    rows=[]
    for group,frame in intervals.groupby('group',sort=False):
        frame=frame.sort_values('decision_tick').reset_index(drop=True)
        tick=ns(frame.decision_tick)
        positives=np.flatnonzero(frame.positive.to_numpy(bool))
        unavailable_prefix=np.r_[0,np.cumsum(~frame.prediction_available.to_numpy(bool))]
        current=None
        for index in positives:
            split=current is None
            if current is not None:
                gap=(tick[index]-tick[current['last_index']])/MINUTE_NS-1
                unknown=unavailable_prefix[index]-unavailable_prefix[current['last_index']+1]
                split=gap>merge_gap_minutes or unknown>0
            if split:
                if current is not None:rows.append(current)
                current={'group':group,'start':pd.Timestamp(frame.decision_tick.iloc[index]),
                         'last_positive':pd.Timestamp(frame.decision_tick.iloc[index]),
                         'end':pd.Timestamp(frame.interval_end.iloc[index]),'positive_minutes':1,'last_index':index}
            else:
                current.update(last_positive=pd.Timestamp(frame.decision_tick.iloc[index]),
                               end=pd.Timestamp(frame.interval_end.iloc[index]),
                               positive_minutes=current['positive_minutes']+1,last_index=index)
        if current is not None:rows.append(current)
    for i,row in enumerate(rows):row.pop('last_index');row['episode_id']=f'episode-{i:06d}'
    return pd.DataFrame(rows,columns=['group','start','last_positive','end','positive_minutes','episode_id'])


def _truth_masks(intervals,events):
    inside=np.zeros(len(intervals),bool)
    tick=pd.to_datetime(intervals.decision_tick,utc=True)
    for event in events.to_dict('records'):
        inside |= (intervals.group.eq(event['group']).to_numpy() &
                   tick.ge(pd.Timestamp(event['start'])).to_numpy() & tick.lt(pd.Timestamp(event['end'])).to_numpy())
    return inside


def normal_statistics(intervals,episodes,events=None):
    normal=np.ones(len(intervals),bool) if events is None else ~_truth_masks(intervals,events)
    records=[]
    for group in sorted(intervals.group.unique()):
        mask=intervals.group.eq(group).to_numpy()&normal
        monitored=mask&intervals.prediction_available.to_numpy(bool)
        hours=float(monitored.sum()/60)
        group_episodes=episodes.loc[episodes.group.eq(group)]
        false=0
        for episode in group_episodes.to_dict('records'):
            event_overlap=False
            if events is not None:
                event_overlap=any(e['group']==group and pd.Timestamp(e['start'])<=episode['start']<pd.Timestamp(e['end'])
                                  for e in events.to_dict('records'))
            false+=int(not event_overlap)
        positive=int(np.sum(monitored&intervals.positive.to_numpy(bool)))
        records.append({'group':group,'normal_monitored_hours':hours,
                        'normal_source_known_hours':float(np.sum(mask&intervals.source_known.to_numpy(bool))/60),
                        'normal_common_eligible_hours':float(np.sum(mask&intervals.eligible.to_numpy(bool))/60),
                        'false_alarm_episodes':false,'false_alarm_episodes_per_hour':false/hours if hours else None,
                        'positive_normal_minutes':positive,'positive_time_fraction':positive/(hours*60) if hours else None})
    return records


def _get(protocol,name,default):
    return protocol.get(name,default)


def calibrate(scores,metadata,protocol,minimum_threshold=None):
    """A fixed candidate grid, checked on every cadence; no monotonicity assumption."""
    scores=np.asarray(scores,float)
    ready=metadata.eligible.to_numpy(bool)&np.isfinite(scores)
    if not ready.any():raise ValueError('No finite eligible calibration scores')
    point_budget=_get(protocol,'calibration_positive_tick_fraction_per_group',.01)
    episode_budget=_get(protocol,'calibration_alarm_budget_per_group_hour',.05)
    cadences=_get(protocol,'cadence_candidates',[1,5,15])
    qgrid=_get(protocol,'calibration_quantile_grid',[.99,.9925,.995,.9975,.999,.9995,1.0])
    minimum=-np.inf
    for cadence in cadences:
        on_grid=decision_grid(metadata,cadence)&ready
        for group in metadata.loc[metadata.eligible,'group'].unique():
            values=scores[on_grid&metadata.group.eq(group).to_numpy()]
            if not len(values):raise ValueError('No scheduled calibration exposure for a required group')
            minimum=max(minimum,float(np.quantile(values,1-point_budget,method='higher')))
    if minimum_threshold is not None:minimum=max(minimum,float(minimum_threshold))
    candidates=np.unique(np.r_[minimum,np.quantile(scores[ready],qgrid,method='higher'),np.max(scores[ready])])
    candidates=candidates[candidates>=minimum]
    checks=[]
    for threshold in candidates:
        all_groups=[];valid=True
        for cadence in cadences:
            issued=decisions(scores,metadata,cadence,threshold)
            intervals=decision_intervals(metadata,issued,cadence)
            episodes=alarm_episodes(intervals,_get(protocol,'alarm_merge_gap_minutes',10))
            rates=normal_statistics(intervals,episodes)
            for rate in rates:
                required=metadata.loc[metadata.group.eq(rate['group']),'eligible'].any()
                if not required:continue
                okay=rate['normal_monitored_hours']>0 and rate['false_alarm_episodes_per_hour']<=episode_budget+1e-12
                okay &= rate['positive_time_fraction'] is not None and rate['positive_time_fraction']<=point_budget+1e-12
                valid &= okay;all_groups.append({'cadence':cadence,**rate})
        checks.append({'threshold':float(threshold),'passed':bool(valid)})
        if valid:return {'threshold':float(threshold),'strict_greater_than':True,'groups':all_groups,
                         'candidate_checks':checks,'candidate_grid':'predeclared normal quantiles + per-group/cadence point floor + maximum',
                         'test_used':False,'cadences':cadences}
    raise ValueError('No predeclared calibration candidate satisfies every normal budget')


def evaluate(scores,threshold,metadata,events,protocol,cadence):
    issued=decisions(scores,metadata,cadence,threshold)
    intervals=decision_intervals(metadata,issued,cadence)
    episodes=alarm_episodes(intervals,_get(protocol,'alarm_merge_gap_minutes',10))
    events=events.sort_values(['end','start','event_id']).reset_index(drop=True).copy()
    assigned={}
    for episode in episodes.sort_values(['start','group','episode_id']).to_dict('records'):
        matches=[i for i,event in enumerate(events.to_dict('records')) if i not in assigned and
                 event['group']==episode['group'] and pd.Timestamp(event['start'])<=episode['start']<pd.Timestamp(event['end'])]
        if matches:assigned[matches[0]]=episode
    event_records=[];cap=_get(protocol,'bounded_delay_minutes',360)
    for index,event in enumerate(events.to_dict('records')):
        group=intervals.group.eq(event['group']).to_numpy()
        tick=pd.to_datetime(intervals.decision_tick,utc=True)
        inside=group&tick.ge(event['start']).to_numpy()&tick.lt(event['end']).to_numpy()
        available=inside&intervals.prediction_available.to_numpy(bool)
        episode=assigned.get(index)
        start=pd.Timestamp(event['start']);end=pd.Timestamp(event['end'])
        confirmed=pd.Timestamp(event.get('confirmed_start',event.get('detectable_start',start)))
        delay=(episode['start']-start).total_seconds()/60 if episode is not None else None
        previous=episodes.loc[episodes.group.eq(event['group']) & episodes.start.lt(start) & episodes.end.gt(start)]
        late=episodes.loc[episodes.group.eq(event['group']) & episodes.start.ge(end) &
                          episodes.start.lt(end+pd.Timedelta(minutes=_get(protocol,'late_grace_minutes',30)))]
        event_records.append({**event,'hit':episode is not None,'early_hit':bool(episode is not None and episode['start']<confirmed),
            'delay_minutes':delay,'censored':episode is None,'bounded_delay_minutes':min(delay,cap) if delay is not None else cap,
            'matched_episode_id':episode['episode_id'] if episode else None,
            'matched_episode_start':episode['start'].isoformat() if episode else None,
            'available_minutes':int(available.sum()),'unassessed':not bool(available.any()),
            'preexisting_episodes':len(previous),'preexisting_only':bool(len(previous) and episode is None),
            'late_episodes':len(late),'coverage_fraction':float(available.sum()/max((end-start).total_seconds()/60,1))})
    count=len(event_records);hits=sum(e['hit'] for e in event_records)
    opportunities=issued.eligible.to_numpy(bool)
    complete=not np.any(opportunities&~issued.decision_ready.to_numpy(bool))
    summary={'events':count,'hits':hits,'recall':hits/count if count else None,
             'early_hits':sum(e['early_hit'] for e in event_records),'unassessed_events':sum(e['unassessed'] for e in event_records),
             'censored_events':sum(e['censored'] for e in event_records),
             'mean_bounded_delay_minutes':float(np.mean([e['bounded_delay_minutes'] for e in event_records])) if count else None,
             'detected_delay_median_minutes':float(np.median([e['delay_minutes'] for e in event_records if e['hit']])) if hits else None,
             'normal':normal_statistics(intervals,episodes,events),'eligible_scheduled_rows':int(opportunities.sum()),
             'scored_eligible_rows':int(np.sum(opportunities&issued.decision_ready.to_numpy(bool))),
             'eligible_scoring_complete':bool(complete),'state_counts':metadata.state.value_counts().to_dict(),
             'decision_state_counts':issued.state.value_counts().to_dict(),
             'cadence_minutes':cadence,'episode_count':len(episodes)}
    return summary,event_records,issued,episodes
