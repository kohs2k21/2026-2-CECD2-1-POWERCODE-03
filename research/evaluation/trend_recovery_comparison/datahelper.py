"""Pinned source replay for development; fresh final data blocked by default."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import types

import numpy as np

from research.evaluation.long_term_trend import dataset as legacy

ROOT=Path(__file__).resolve().parents[3]
FINAL_MAPPING={201:101,237:137,273:173}


def protocol_hash(protocol):
    return hashlib.sha256(json.dumps(protocol,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def load_protocol():
    p=json.loads(Path(__file__).with_name('protocol.json').read_text(encoding='utf-8'))
    _validate_protocol(p)
    return p


def _validate_protocol(p):
    mapping={int(k):v for k,v in p['final']['data_seed_to_model_seed'].items()}
    if mapping!=FINAL_MAPPING or p['final']['scored_start_utc']!='2026-08-03T00:00:00Z' or p['final']['days']!=14:
        raise ValueError('Unexpected final seed mapping or period')
    if not p['final']['default_blocked']:
        raise ValueError('Final generation must remain blocked by default')


def _verify_sources(p):
    base=ROOT/'research/evaluation/trend_family_comparison/protocol.json'
    if hashlib.sha256(base.read_bytes()).hexdigest()!=p['base_protocol_file_sha256']:
        raise ValueError('Frozen base protocol changed')
    if json.loads(base.read_text(encoding='utf-8'))!=p['base_protocol']:
        raise ValueError('Embedded base protocol differs from frozen source file')
    for relative,expected in p['base_protocol']['legacy_source_sha256'].items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError(f'Frozen source changed: {relative}')


def _counterfactual(seed,source_protocol,original):
    source=Path(legacy.__file__).read_text(encoding='utf-8')
    needle='frame.loc[order_ids, "duration_ms"] = np.sort(frame.loc[local, "duration_ms"].to_numpy())[::-1]'
    if source.count(needle)!=1:raise ValueError('Expected pinned reassignment exactly once')
    module=types.ModuleType('recovery_same_rng_no_relation');module.__file__=legacy.__file__
    exec(compile(source.replace(needle,'pass'),'<pinned-recovery-counterfactual>','exec'),module.__dict__)
    control=module.load_split(seed,'blind',source_protocol)
    a,b=original['stream'],control['stream']
    if not a.drop(columns='duration_ms').equals(b.drop(columns='duration_ms')):
        raise ValueError('Counterfactual changed another source field')
    if not original['events'].drop(columns='relation_marginal_after_sha256').equals(control['events'].drop(columns='relation_marginal_after_sha256')):
        raise ValueError('Counterfactual changed event schedule')
    changed=np.zeros(len(a),bool)
    for event in original['events'].query("scenario=='relation_change'").itertuples():
        mask=(a.group==event.group)&(a.bin_start>=event.start)&(a.bin_start<event.end)
        changed|=mask.to_numpy()
        for _,hour in a.loc[mask].groupby(a.loc[mask,'bin_start'].dt.floor('h')):
            if not np.array_equal(np.sort(hour.duration_ms),np.sort(b.loc[hour.index,'duration_ms'])):
                raise ValueError('Counterfactual changed hourly marginal')
    if not a.loc[~changed,'duration_ms'].equals(b.loc[~changed,'duration_ms']):
        raise ValueError('Counterfactual changed duration outside targets')
    return control


def load_item(data_seed,phase='development',*,split='blind',relation_intervention=True,expected_source_sha256=None,allow_final=False,protocol=None):
    """Generate only declared replay/final, never fit or score a model.

    Development/fixture require the caller's frozen source hash. Final callers
    must separately check root GO and their source/policy/model artifact lock.
    """
    p=load_protocol() if protocol is None else protocol
    _validate_protocol(p)
    if phase not in ('development','fixture','final'):raise ValueError('Unknown source phase')
    if split not in ('calibration','blind') or (phase=='final' and split!='blind'):
        raise ValueError('Replay only calibration/blind; fresh final supports blind only')
    if not relation_intervention and split!='blind':
        raise ValueError('Relation counterfactual is blind-only')
    if phase=='final' and not allow_final:
        raise ValueError('Fresh final blocked until explicit root GO and verified freeze')
    source_p=copy.deepcopy(p['base_protocol'])
    if phase=='fixture':
        if data_seed!=9102 or p['fixture']['allowed_data_seeds']!=[9102]:raise ValueError('Only frozen fixture9102 replay allowed')
        model_seed=9102
        source_p.update(run_kind='fixture',seeds=[9102],split_days={'train':2,'calibration':2,'blind':14})
    elif phase=='development':
        if data_seed not in (101,137,173):raise ValueError('Use previous July development data only')
        model_seed=data_seed
    else:
        if data_seed not in FINAL_MAPPING:raise ValueError('Undeclared final data seed')
        model_seed=FINAL_MAPPING[data_seed]
        source_p['seeds']=list(FINAL_MAPPING)
        source_p['scored_starts_utc']['blind']=p['final']['scored_start_utc']
        source_p['split_days']['blind']=p['final']['days']
    if phase!='final' and expected_source_sha256 is None:
        raise ValueError('Replay requires a frozen manifest source SHA before generation')
    _verify_sources(p)
    original=legacy.load_split(data_seed,split,source_p)
    item=original if relation_intervention else _counterfactual(data_seed,source_p,original)
    if expected_source_sha256 is not None and item['manifest']['source_sha256']!=expected_source_sha256:
        raise ValueError('Replay differs from the frozen source manifest')
    item['original_events']=original['events'].copy()
    targets=original['events'].scenario.eq('relation_change')
    item['control_windows']=original['events'].loc[targets].copy().reset_index(drop=True)
    item['control_windows']['control_role']='relation_intervention_target' if relation_intervention else 'no_relation_reassignment_control'
    if not relation_intervention:item['events']=item['events'].loc[~targets].copy().reset_index(drop=True)
    item['model_seed']=model_seed;item['data_seed']=data_seed;item['source_phase']=phase
    item['manifest'].update(data_seed=data_seed,model_seed=model_seed,source_phase=phase,
        recovery_protocol_sha256=protocol_hash(p),relation_intervention=bool(relation_intervention),
        original_source_sha256=original['manifest']['source_sha256'],original_intended_events=len(item['original_events']),
        intended_events=len(item['events']),relation_control_windows=len(item['control_windows']),
        counterfactual_invariants_verified=not relation_intervention,real_source_used=False)
    return item
