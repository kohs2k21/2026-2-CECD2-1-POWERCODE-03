"""Frozen legacy fast evidence used as an explicitly separate common proxy.

The policy receives observations and frozen scores only. Event truth is used
only by compare(). Every replay starts empty; no detector fit or cut changes.
"""
from __future__ import annotations

from types import SimpleNamespace
import numpy as np
import pandas as pd

from . import metrics

POLICY={"count_window_minutes":5,"latency_window_minutes":5,"relation_window_minutes":30,
        "quiet_confirmation_minutes":10,"strong_confirmation_minutes":3,
        "relation_quiet_deviation":1.,"quantile_method":"linear"}
CONTRACT={"version":"trend-sr-legacy-fast-proxy-v1","policy":POLICY,
          "evidence":"frozen R00 UTC reference and sampled legacy43 statistics; same legacy signals",
          "meaning":"common postprocessing proxy, not proof of each new SR family's normality",
          "unsupported":"raw fallback; error evidence unsupported; invalid hysteresis raw fallback",
          "publication":"each branch changes gated output only on its native C; observations every minute",
          "source_unknown":"preserve raw finite/unknown mask immediately","ground_truth_input":False}


def build_evidence(bundle,item):
    from .runner import prepare
    from .methods import _legacy_stream
    from research.evaluation.trend_recovery_comparison import signals
    legacy=bundle['baseline'].legacy
    reference=legacy['reference']
    stats=SimpleNamespace(names=legacy['names'],
        median=np.array([v['median'] for v in legacy['statistics']]),
        scale=np.maximum(np.array([v['range90'] for v in legacy['statistics']]),.05))
    cut=bundle.get('baseline_calibration',bundle['calibration']).get('thresholds') or {}
    relation_cut=cut.get('relation')
    valid=relation_cut is not None and np.isfinite(relation_cut) and relation_cut>POLICY['relation_quiet_deviation']
    stream=_legacy_stream(prepare(item,1),'proxy',legacy['sampling_seed'])
    adapted={'stream':stream,'manifest':{'scored_start':item.manifest['evaluation_start'],
                                       'scored_end':item.manifest['evaluation_end']}}
    # The original signal contract rejects an inverted hysteresis band. Do not
    # silently alter a calibrated active cut to manufacture a valid band.
    result=signals.build_signals(adapted,reference,stats,POLICY if valid else None,
                                 relation_cut if valid else float('nan'))
    result['group']=np.array([key.split('|')[2] for key in result['row_id']])
    result['status']='supported' if valid else 'raw_fallback_invalid_legacy_hysteresis'
    return result


def apply_fast(bundle,item,raw=None,*,cadence=None,evidence=None,record=True):
    from .runner import score_candidate_bundle
    if raw is None:raw=score_candidate_bundle(bundle,item)
    raw=raw.reset_index(drop=True)
    evidence=build_evidence(bundle,item) if evidence is None else evidence
    keys=list((bundle['calibration'].get('thresholds') or {}).keys())
    entry=bundle['entry'];candidate_c=int(cadence or entry['cadence'])
    branch_c={key:(candidate_c if entry.get('method')=='R00' or key==entry.get('family') else 5) for key in keys}
    source=raw.source_eligible.to_numpy(bool)&raw.common_eligible.to_numpy(bool)
    cuts=bundle['calibration'].get('thresholds') or {}
    flags={key:np.where(source&np.isfinite(raw[key]),(raw[key].to_numpy(float)>cuts[key]).astype(float),np.nan)
           for key in keys}
    ev_index=pd.MultiIndex.from_arrays([evidence['group'],pd.DatetimeIndex(evidence['decision_tick']).as_unit('ns').asi8])
    query=pd.MultiIndex.from_arrays([raw.group,pd.DatetimeIndex(raw.decision_tick).as_unit('ns').asi8])
    positions=ev_index.get_indexer(query)
    if np.any(positions<0):raise ValueError('fast_evidence_timeline_mismatch')
    ev={}
    for key in keys:
        if key in ('count','latency','relation'):
            ev[key]={name:np.asarray(values)[positions] for name,values in evidence[key].items()}
        else:
            ev[key]={'supported':np.zeros(len(raw),bool),'quiet':np.zeros(len(raw),bool),
                     'strong':np.zeros(len(raw),bool),'direction':np.zeros(len(raw),int),
                     'reason':np.full(len(raw),'new_error_family_no_legacy_quiet',object)}
    output=raw.copy();prediction=raw.prediction.to_numpy(float).copy()
    ns=pd.DatetimeIndex(raw.decision_tick).as_unit('ns').asi8
    transitions=[];ledger=[]
    for group,indices in raw.groupby('group',sort=False).indices.items():
        states={key:{'state':'IDLE','quiet_since':None,'strong_count':0,'direction':0,'held':np.nan} for key in keys}
        last=None
        def transition(key,target,reason,i):
            state=states[key];before=state['state']
            if before!=target:
                transitions.append({'group':group,'decision_tick':raw.decision_tick.iloc[i],
                                    'branch':key,'from_state':before,'to_state':target,'reason':reason})
            state['state']=target
        def reset(key,target,reason,i):
            transition(key,target,reason,i)
            states[key].update(quiet_since=None,strong_count=0,direction=0)
        for i in sorted(indices,key=lambda value:ns[value]):
            minute=int(ns[i]//60_000_000_000)
            if last is not None and minute!=last+1:
                for key in keys:reset(key,'IDLE','missing_minute',i);states[key]['held']=np.nan
            last=minute
            for key in keys:
                state=states[key];value=flags[key][i];native=minute%branch_c[key]==0
                first=False
                if not np.isfinite(value):
                    reset(key,'IDLE','raw_branch_unknown',i);state['held']=np.nan
                elif value==0:reset(key,'IDLE','raw_negative',i)
                else:
                    if state['state']=='IDLE':transition(key,'ALERT','first_raw_positive',i);first=True
                    e=ev[key]
                    if not e['supported'][i]:reset(key,'ALERT','evidence_unsupported_raw_fallback',i)
                    elif first:
                        if e['quiet'][i]:state['quiet_since']=minute
                    elif state['state']=='ALERT':
                        if e['quiet'][i]:
                            if state['quiet_since'] is None:state['quiet_since']=minute
                            if minute-state['quiet_since']>=POLICY['quiet_confirmation_minutes']:
                                transition(key,'SUPPRESSED','quiet_confirmed',i);state.update(strong_count=0,direction=0)
                        else:state['quiet_since']=None
                    else:
                        direction=int(e['direction'][i]) if e['strong'][i] else 0
                        if direction:
                            state['strong_count']=state['strong_count']+1 if direction==state['direction'] else 1
                            state['direction']=direction
                        else:state.update(strong_count=0,direction=0)
                        if state['strong_count']>=POLICY['strong_confirmation_minutes']:
                            reset(key,'ALERT','strong_confirmed',i)
                if native:state['held']=0. if value==1 and state['state']=='SUPPRESSED' else value
                # A newly unsupported branch must never keep a stale decision.
                if not np.isfinite(value):state['held']=np.nan
            gated={key:states[key]['held'] for key in keys}
            values=np.array(list(gated.values()),float)
            candidate=1. if np.any(values==1) else (0. if len(values) and np.all(values==0) else np.nan)
            original=prediction[i]
            fallback=np.isfinite(original) and not np.isfinite(candidate)
            if fallback:
                candidate=original
                for key in keys:
                    if flags[key][i]==1 and gated[key]==0:
                        reset(key,'ALERT','arm_unknown_raw_fallback',i);states[key]['held']=1.
            # The policy cannot create availability or an onset that raw lacks.
            if not np.isfinite(original):candidate=np.nan
            if original==0:candidate=0.
            prediction[i]=candidate
            if record and any(minute%c==0 for c in branch_c.values()):
                ledger.append({'group':group,'decision_tick':raw.decision_tick.iloc[i],
                    'raw':original,'fast':candidate,'fallback':fallback,
                    **{f'{key}_state':state['state'] for key,state in states.items()}})
    output['prediction']=prediction
    if not np.array_equal(np.isfinite(prediction),np.isfinite(raw.prediction)):raise AssertionError('fast_changed_mask')
    if np.any((prediction==1)&(raw.prediction.to_numpy()!=1)):raise AssertionError('fast_created_positive')
    return output,{'transitions':transitions,'native_ledger':ledger,'evidence_status':evidence.get('status'),
                   'threshold_manifest_sha256':evidence.get('threshold_manifest_sha256'),'contract':CONTRACT}


def compare(raw,fast,events):
    """GT-dependent safety evaluation, separate from the observation-only policy."""
    before=metrics.evaluate(raw,events);after=metrics.evaluate(fast,events)
    left={row['event_id']:row for row in before['events']};right={row['event_id']:row for row in after['events']}
    lost=[];delays=[]
    for key,a in left.items():
        b=right[key]
        if a.get('hit') and not b.get('hit'):lost.append(key)
        if a.get('hit') and b.get('hit'):
            delay=(b.get('delay_minutes') or 0)-(a.get('delay_minutes') or 0)
            if delay>0:delays.append({'event_id':key,'extra_minutes':delay})
    active=np.zeros(len(raw),bool);ticks=pd.DatetimeIndex(raw.decision_tick)
    for row in events.itertuples():
        active|=(raw.group.to_numpy()==row.group)&(ticks>=row.start)&(ticks<row.end)
    suppression=(raw.prediction.to_numpy()==1)&(fast.prediction.to_numpy()==0)
    return {'raw':before,'fast':after,'safety':{'raw_hit_lost_event_ids':lost,
        'first_alert_extra_delays':delays,'active_suppression_minutes':int(np.sum(active&suppression)),
        'finite_mask_equal':bool(np.array_equal(np.isfinite(raw.prediction),np.isfinite(fast.prediction))),
        'strict_safety_passed':not lost and not delays and not np.any(active&suppression)}}


def normal_guard(bundle,item,cadences):
    from .runner import score_candidate_published
    published=score_candidate_published(bundle,item,cadences)
    evidence=build_evidence(bundle,item);checks=[]
    for cadence,raw in published.items():
        fast,_=apply_fast(bundle,item,raw,cadence=cadence,evidence=evidence,record=False)
        for row in metrics.evaluate(fast,item.events)['summary']:
            passed=(row['normal_prediction_available_minutes']>0 and
                    row['false_alarm_episodes_per_hour']<=.05 and row['positive_time_fraction']<=.01)
            checks.append({'cadence':int(cadence),'group':row['group'],'passed':bool(passed),**row})
    return {'passed':bool(bundle['calibration']['passed'] and all(row['passed'] for row in checks)),
            'candidate_checks':checks,'contract':CONTRACT,'evidence_status':evidence['status'],
            'thresholds_changed':False,'fit_count':0}
