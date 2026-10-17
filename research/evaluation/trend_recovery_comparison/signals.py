"""Causal recent-normal evidence from frozen training tables and SR statistics.

These are postprocessing inputs, not replacement model predictions. A strong
flag represents this minute only; policy owns consecutive-minute counters.
"""
from __future__ import annotations
import hashlib
import json

import numpy as np
import pandas as pd

from research.evaluation.long_term_trend import features as original


def threshold_manifest(reference):
    """New policy thresholds derived only from immutable train table values."""
    groups={}
    for group,ref in reference['groups'].items():
        groups[group]={}
        for metric in ('count','duration_ms'):
            tables={}
            for key,table in ref[metric].items():
                values=np.asarray(table['values'],float)
                q01,q99=np.quantile(values,[.01,.99],method='linear')
                tables[key]={'q01':float(q01),'q05':table['q05'],'q95':table['q95'],'q99':float(q99),
                    'values_sha256':hashlib.sha256(values.tobytes()).hexdigest()}
            groups[group][metric]=tables
    record={'quantile_method':'linear','policy_percentiles':[.01,.05,.95,.99],'groups':groups,
            'new_policy_thresholds':True,'model_cut_recalibration':False}
    record['sha256']=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return record


def _new_tail(frame,reference,group,metric,thresholds):
    result=np.zeros(len(frame),dtype=np.int8)
    ticks=pd.DatetimeIndex(frame.bin_start)
    values=frame[metric].to_numpy(float)
    for hour,weekend in set(zip(ticks.hour,(ticks.dayofweek>=5).astype(int))):
        mask=(ticks.hour==hour)&((ticks.dayofweek>=5)==bool(weekend))
        tables=thresholds['groups'][group][metric]
        table=tables.get(f'{hour}:{weekend}',tables['pooled'])
        result[mask&(values<table['q01'])]=-1
        result[mask&(values>table['q99'])]=1
    return result


def build_signals(item,reference,stats,policy_spec,relation_active_cut):
    """Return scored-minute row keys plus count/latency/relation evidence arrays.

    quiet/strong/supported are bool[n], direction is -1/0/+1. No state counters
    occur here. Caller must compare row_id with its original feature metadata.
    """
    stream=item['stream'];original._validate(stream)
    p=reference['protocol'];lag=int(p['watermark_lag_minutes']);shift=lag+1
    if lag!=2 or p['minimum_relation_pairs']!=30:raise ValueError('Unexpected frozen source contract')
    names=stats.names
    ci=names.index('count_duration_correlation');ri=names.index('relation_absolute_residual')
    if policy_spec is not None:
        if policy_spec['relation_window_minutes']<30 or policy_spec['quantile_method']!='linear':
            raise ValueError('Keep minimum30 relation pairs and frozen quantile method')
        if not np.isfinite(relation_active_cut) or relation_active_cut<=policy_spec['relation_quiet_deviation']:
            raise ValueError('Require a finite hysteresis band above the quiet threshold')
    thresholds=threshold_manifest(reference)
    pieces=[]
    for group,source in stream.groupby('group',sort=False):
        frame=source.reset_index(drop=True);n=len(frame)
        tick=pd.DatetimeIndex(frame.bin_start)
        c=frame['count'].to_numpy(float);d=frame.duration_ms.to_numpy(float)
        obs=np.isfinite(d)&(c>0)
        known=frame.source_complete.to_numpy(bool)&np.isfinite(c)&((c==0)|obs)
        if 'coverage_known' in frame:known &= frame.coverage_known.to_numpy(bool)
        category=original._mapped(frame,reference['registry'])==group
        valid_source=known&category
        cr=original._lookup(frame,reference,group,'count')
        dr=original._lookup(frame,reference,group,'duration_ms')
        active=cr['q05']>0
        s=lambda a,fill=0.:original._shift(a,shift,fill)
        count_normal=(c>=cr['q05'])&(c<=cr['q95'])
        duration_normal=obs&(d>=dr['q05'])&(d<=dr['q95'])
        count_direction=s(_new_tail(frame,reference,group,'count',thresholds)).astype(np.int8)
        latency_direction=s(_new_tail(frame,reference,group,'duration_ms',thresholds)).astype(np.int8)
        result={'row_id':np.asarray([f'{sp}|{se}|{group}|{t.isoformat()}' for sp,se,t in zip(frame['split'],frame['seed'],tick)]),
                'decision_tick':tick}
        for family in ('count','latency','relation'):
            result[family]={}
            if policy_spec is None:
                result[family]={'quiet':np.zeros(n,bool),'strong':np.zeros(n,bool),'supported':np.zeros(n,bool),
                    'direction':np.zeros(n,np.int8),'reason':np.full(n,'policy_none',object)}
                continue
            width=int(policy_spec[f'{family}_window_minutes'])
            available=original._roll(s(valid_source.astype(float)),width,'sum')-original._pending(frame,width,lag,valid_source)
            active_n=original._roll(s(active.astype(float)),width,'sum')
            pair_mask=valid_source&obs
            pairs=original._roll(s(pair_mask.astype(float)),width,'sum')-original._pending(frame,width,lag,pair_mask)
            supported=(available==width)&(active_n==width)
            reason=np.full(n,'ready',object)
            reason[active_n!=width]='expected_inactive_or_uncertain'
            reason[available!=width]='source_or_category_unavailable'
            if family!='count':
                reason[supported&(pairs!=width)]='duration_or_pairs_unobserved'
                supported &= pairs==width
            if family=='count':
                quiet=original._roll(s(count_normal.astype(float)),width,'sum')==width
                direction=count_direction.copy();strong=direction!=0
            elif family=='latency':
                quiet=original._roll(s(duration_normal.astype(float)),width,'sum')==width
                direction=latency_direction.copy();strong=direction!=0
            else:
                x=s(c/np.maximum(cr['median'],1)-1,np.nan)
                y=s(np.where(obs,d/np.maximum(dr['median'],1)-1,np.nan),np.nan)
                pair=np.isfinite(x)&np.isfinite(y)
                px=np.where(pair,x,0);py=np.where(pair,y,0)
                number=original._roll(pair.astype(float),width,'sum')
                sx=original._roll(px,width,'sum');sy=original._roll(py,width,'sum')
                vx=number*original._roll(px*px,width,'sum')-sx*sx
                vy=number*original._roll(py*py,width,'sum')-sy*sy
                varying=(number>=p['minimum_relation_pairs'])&(vx>1e-10)&(vy>1e-10)
                reason[supported&~varying]='insufficient_pairs_or_variance'
                supported &= varying
                corr=np.divide(number*original._roll(px*py,width,'sum')-sx*sy,np.sqrt(np.maximum(vx*vy,0)),
                               out=np.full(n,np.nan),where=varying)
                relation=reference['groups'][group]['relation']
                residual=np.where(pair,np.abs(y-relation['intercept']-relation['beta']*x)/relation['scale'],np.nan)
                residual_mean=original._roll(residual,width)
                deviation=np.maximum(np.abs((corr-stats.median[ci])/stats.scale[ci]),
                                     np.abs((residual_mean-stats.median[ri])/stats.scale[ri]))
                quiet=deviation<=policy_spec['relation_quiet_deviation']
                strong=deviation>relation_active_cut;direction=strong.astype(np.int8)
                result[family].update(correlation=np.where(supported,corr,np.nan),
                    absolute_residual=np.where(supported,residual_mean,np.nan),deviation=np.where(supported,deviation,np.nan))
            quiet &= supported;strong &= supported;direction[~supported]=0
            if np.any(quiet&strong):raise ValueError('Quiet and strong evidence must be disjoint')
            result[family].update(quiet=quiet,strong=strong,supported=supported,direction=direction,reason=reason,
                expected_bins=np.full(n,width,int),available_bins=available.astype(int),pair_count=pairs.astype(int),
                window_start=tick-pd.Timedelta(minutes=lag+width),window_end=tick-pd.Timedelta(minutes=lag))
        pieces.append(result)
    start=pd.Timestamp(item['manifest']['scored_start']);end=pd.Timestamp(item['manifest']['scored_end'])
    combined={key:np.concatenate([piece[key] for piece in pieces]) for key in ('row_id','decision_tick')}
    mask=(pd.DatetimeIndex(combined['decision_tick'])>=start)&(pd.DatetimeIndex(combined['decision_tick'])<end)
    output={key:value[mask] for key,value in combined.items()}
    for family in ('count','latency','relation'):
        output[family]={key:np.concatenate([piece[family][key] for piece in pieces])[mask] for key in pieces[0][family]}
    output['threshold_manifest_sha256']=thresholds['sha256']
    return output
