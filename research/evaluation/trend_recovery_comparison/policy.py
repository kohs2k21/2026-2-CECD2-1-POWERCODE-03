"""Typed alarm clearing; only native model emissions enter the state machine.

Inputs contain observations and predictions, never event labels. Each invocation
replays a complete timeline from an empty state, so repeated calls are idempotent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ternary_or(values):
    values=np.asarray(values,float)
    if np.any(values==1):return 1.
    return 0. if np.all(values==0) else np.nan


def apply_policy(branch_flags,metadata,signals,branch_families,policy,cadence):
    """Return native arm flags[n], native ledger, and minute transition ledger.

    metadata requires row_id/group/decision_tick/eligible. Non-native raw values
    are deliberately ignored. Quiet confirmation uses elapsed time (11 adjacent
    minute observations span10min); strong confirmation uses3 adjacent bins.
    """
    if cadence not in (1,5,15):raise ValueError('Only C1/C5 holdout and C15 calibration emissions')
    keys=list(branch_flags);n=len(metadata)
    if not keys or set(keys)!=set(branch_families):raise ValueError('Branch mapping mismatch')
    if metadata.row_id.duplicated().any():raise ValueError('Duplicate row_id')
    ticks=pd.to_datetime(metadata.decision_tick,utc=True)
    row_ids=metadata.row_id.to_numpy();eligible=metadata.eligible.to_numpy(bool)
    if metadata.assign(_tick=ticks).duplicated(['group','_tick']).any():raise ValueError('Duplicate group/minute')
    ns=ticks.dt.as_unit('ns').astype('int64').to_numpy();minute=60_000_000_000
    if np.any(ns%minute):raise ValueError('Expected integer minute timestamps')
    raw={k:np.asarray(v,float) for k,v in branch_flags.items()}
    for v in raw.values():
        if len(v)!=n or np.any(np.isfinite(v)&~np.isin(v,[0,1])):raise ValueError('Invalid raw flags')
    if policy is not None:
        if not np.array_equal(np.asarray(signals['row_id']),metadata.row_id.to_numpy()):raise ValueError('Evidence row alignment')
        if not np.array_equal(pd.DatetimeIndex(pd.to_datetime(signals['decision_tick'],utc=True)).as_unit('ns').asi8,ns):raise ValueError('Evidence time alignment')
        for family in set(branch_families.values()):
            e=signals[family]
            if any(len(e[k])!=n for k in ('supported','quiet','strong','direction','reason')):raise ValueError('Evidence length')
            if np.any(np.asarray(e['quiet'])&np.asarray(e['strong'])):raise ValueError('Conflicting evidence')
            if np.any(~np.isin(e['direction'],[-1,0,1])):raise ValueError('Invalid direction')
    output=np.full(n,np.nan);rows=[];changes=[]
    for group,indices in metadata.groupby('group',sort=False).indices.items():
        indices=sorted(indices,key=lambda i:ns[i]);last=None;last_native=None
        held={k:np.nan for k in keys}
        states={k:{'state':'IDLE','quiet_since':None,'strong_count':0,'direction':0} for k in keys}
        def reset(k,reason,i,target='IDLE'):
            s=states[k];previous=s['state']
            s.update(state=target,quiet_since=None,strong_count=0,direction=0)
            if previous!=target:
                changes.append({'row_id':row_ids[i],'group':group,'decision_tick':ticks.iloc[i],
                    'branch':k,'from_state':previous,'to_state':target,'reason':reason})
        def transition(k,target,reason,i):
            previous=states[k]['state'];states[k]['state']=target
            if previous!=target:
                changes.append({'row_id':row_ids[i],'group':group,'decision_tick':ticks.iloc[i],
                    'branch':k,'from_state':previous,'to_state':target,'reason':reason})
        for i in indices:
            now=int(ns[i]//minute);native=now%cadence==0
            if last is not None and now!=last+1:
                for k in keys:reset(k,'missing_minute',i)
                held={k:np.nan for k in keys};last_native=None
            last=now
            if not eligible[i]:
                for k in keys:reset(k,'source_unknown',i)
                held={k:np.nan for k in keys};last_native=None
            elif native:
                held={k:float(raw[k][i]) for k in keys};last_native=now
            elif last_native is None or now-last_native>=cadence:
                held={k:np.nan for k in keys}
            baseline=ternary_or(list(held.values()))
            if not np.isfinite(baseline):
                for k in keys:reset(k,'arm_unknown',i)
            first=set()
            for k in keys:
                s=states[k];v=held[k]
                if not np.isfinite(v):reset(k,'branch_unknown',i);continue
                if v==0:reset(k,'raw_negative',i);continue
                if s['state']=='IDLE':transition(k,'ALERT','first_raw_positive',i);first.add(k)
                if policy is None:continue
                e=signals[branch_families[k]]
                if not bool(e['supported'][i]):reset(k,'evidence_unsupported_raw_fallback',i,'ALERT');continue
                if k in first:
                    if bool(e['quiet'][i]):s['quiet_since']=now
                    continue
                if s['state']=='ALERT':
                    if bool(e['quiet'][i]):
                        if s['quiet_since'] is None:s['quiet_since']=now
                        if now-s['quiet_since']>=policy['quiet_confirmation_minutes']:
                            transition(k,'SUPPRESSED','quiet_confirmed',i);s.update(strong_count=0,direction=0)
                    else:s['quiet_since']=None
                else:
                    direction=int(e['direction'][i]) if bool(e['strong'][i]) else 0
                    if direction:
                        s['strong_count']=s['strong_count']+1 if direction==s['direction'] else 1
                        s['direction']=direction
                    else:s.update(strong_count=0,direction=0)
                    if s['strong_count']>=policy['strong_confirmation_minutes']:
                        reset(k,'strong_confirmed',i,'ALERT')
            if not native:continue
            gated={k:(0. if held[k]==1 and states[k]['state']=='SUPPRESSED' else held[k]) for k in keys}
            alert=ternary_or(list(gated.values()));fallback=np.isfinite(baseline) and not np.isfinite(alert)
            fallback_keys=[k for k in keys if held[k]==1 and gated[k]==0] if fallback else []
            if fallback:
                for k in fallback_keys:reset(k,'arm_unknown_raw_fallback',i,'ALERT');gated[k]=held[k]
                alert=baseline
            output[i]=alert
            record={'row_id':row_ids[i],'group':group,'decision_tick':ticks.iloc[i],
                'baseline':baseline,'alert':alert,'fallback':bool(fallback),'fallback_branches':','.join(fallback_keys)}
            for k in keys:
                s=states[k];e=signals[branch_families[k]] if policy is not None else None
                record.update({f'{k}_raw':held[k],f'{k}_output':gated[k],f'{k}_state':s['state'],
                    f'{k}_quiet_elapsed':now-s['quiet_since'] if s['quiet_since'] is not None else 0,
                    f'{k}_strong_count':s['strong_count'],f'{k}_evidence_supported':bool(e['supported'][i]) if e is not None else None,
                    f'{k}_evidence_reason':str(e['reason'][i]) if e is not None else 'policy_none'})
            rows.append(record)
    ledger=pd.DataFrame(rows)
    if not np.array_equal(np.isfinite(ledger.baseline),np.isfinite(ledger.alert)):raise AssertionError('Availability changed')
    if np.any(ledger.alert.to_numpy(float)>ledger.baseline.to_numpy(float)):raise AssertionError('New positive introduced')
    return output,ledger,pd.DataFrame(changes,columns=['row_id','group','decision_tick','branch','from_state','to_state','reason'])
