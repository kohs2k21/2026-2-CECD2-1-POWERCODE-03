"""Train-only SR statistics, frozen IF/EIF branches, and budgeted raw-flag OR."""
from __future__ import annotations

import copy
import hashlib
import numpy as np

from research.evaluation.long_term_trend import evaluation as ev
from research.evaluation.long_term_trend.models import StatisticalReference, RobustEncoder, fit_forest
from research.evaluation.if_feature_comparison.models import load_eif

FORESTS = {'if43':('if',False),'eif43':('eif',False),'ifrel2':('if',True),'eifrel2':('eif',True)}
RELATION = ['count_duration_correlation','relation_absolute_residual']
CLOCK = ['hour_sin','hour_cos','weekend']
NEUTRAL = ['duration_observed','relation_valid','relation_pair_fraction']


def forest_score_upper_bound(model, algorithm):
    """Conservative bound: each tree's shortest leaf need not be jointly reachable."""
    minima=[]
    if algorithm=='if':
        def correction(size):
            size=np.asarray(size,float);value=np.zeros_like(size);value[size==2]=1.
            many=size>2
            value[many]=2*(np.log(size[many]-1)+np.euler_gamma)-2*(size[many]-1)/size[many]
            return value
        for estimator in model.model.estimators_:
            tree=estimator.tree_;leaves=tree.children_left<0
            paths=tree.compute_node_depths()-1+correction(tree.n_node_samples)
            minima.append(float(paths[leaves].min()))
        normalizer=float(correction([model.model.max_samples_])[0])
    else:
        correction=load_eif().c_factor
        for tree in model.model.Trees:
            stack=[(tree.root,0)];lengths=[]
            while stack:
                node,depth=stack.pop()
                if node.ntype=='exNode':lengths.append(depth+(correction(node.size) if node.size>1 else 0.))
                else:stack.extend([(node.left,depth+1),(node.right,depth+1)])
            minima.append(float(min(lengths)))
        normalizer=model.model.c
    return float(2.**(-np.mean(minima)/normalizer))


def family_indices(names):
    physical = [n for n in names if n not in CLOCK+NEUTRAL]
    count = [n for n in physical if n.startswith('count_') and n not in RELATION]
    relation = [n for n in physical if n in RELATION]
    latency = [n for n in physical if n not in count+relation]
    assert len(physical)==37 and len(count+latency+relation)==37
    assert sorted(count+latency+relation)==sorted(physical)
    return {f'sr_{key}':[names.index(n) for n in values]
            for key,values in [('count',count),('latency',latency),('relation',relation)]}


def fit_detectors(pack, metadata, seed, protocol):
    X=np.asarray(pack['X']['trend']);valid=np.asarray(pack['feature_validity']['trend'],bool)
    names=pack['feature_names']['trend']
    train=metadata.eligible.to_numpy(bool)&metadata.train_mask.to_numpy(bool)&ev.decision_grid(metadata,5)
    indices=np.flatnonzero(train)
    if len(indices)>protocol['training_cap']:
        indices=np.sort(np.random.default_rng(seed).choice(indices,protocol['training_cap'],replace=False))
    statistics=StatisticalReference.fit(X[indices],valid[indices],names,NEUTRAL)
    artifact={'seed':seed,'statistics':statistics,'families':family_indices(names),'feature_names':names,
              'forests':{},'fits_attempted':0,'fits_completed':0,'sr_training_rows':len(indices),
              'legacy_active_features':int(statistics.active.sum())}
    if 'family_feature_names' in protocol:
        assert {k.removeprefix('sr_'):[names[i] for i in v] for k,v in artifact['families'].items()}==protocol['family_feature_names']
    relation_indices=[names.index(n) for n in RELATION]
    for key,(algorithm,relation_only) in FORESTS.items():
        columns=relation_indices if relation_only else list(range(len(names)))
        usable=indices[valid[indices][:,columns].all(axis=1)] if relation_only else indices
        forest={'columns':columns,
                                  'training_rows':len(usable),'relation_only':relation_only,
                                  'upper_bound_contract':'per-tree shortest-leaf conservative bound; passing cut below bound does not prove a jointly attainable vector',
                                  'training_row_id_sha256':hashlib.sha256('\n'.join(metadata.iloc[usable].row_id).encode()).hexdigest(),
                                  'training_first_tick':str(metadata.iloc[usable].decision_tick.min()),
                                  'training_last_tick':str(metadata.iloc[usable].decision_tick.max()),
                                  'training_cap':protocol['training_cap'],
                                  'cap_policy':'seeded uniform without replacement on full eligible C5 train rows, then relation-valid subset for relation-only branch',
                                  'full_pre_cap_train_rows':int(train.sum()),'full_post_cap_train_rows':len(indices)}
        if len(usable)<256:
            forest.update(status='not_ready',reason='fewer than 256 valid train rows')
        else:
            artifact['fits_attempted']+=1
            try:
                encoder=RobustEncoder.fit(X[usable][:,columns])
                model=fit_forest(algorithm,seed,encoder.transform(X[usable][:,columns]))
                forest.update(status='ok',reason=None,model=model,encoder=encoder,
                              score_upper_bound=forest_score_upper_bound(model,algorithm))
                artifact['fits_completed']+=1
            except Exception as exc:
                forest.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
        artifact['forests'][key]=forest
    return artifact


def score_detectors(artifact, pack, metadata):
    X=np.asarray(pack['X']['trend']);valid=np.asarray(pack['feature_validity']['trend'],bool)
    eligible=metadata.eligible.to_numpy(bool);stats=artifact['statistics']
    raw={};expected={}
    deviation=np.abs((X-stats.median)/stats.scale)
    for key,columns in [('old_sr',np.flatnonzero(stats.active))]+list(artifact['families'].items()):
        usable=valid[:,columns]&np.isfinite(X[:,columns])&stats.active[None,columns]
        ready=eligible&(usable.all(axis=1) if key=='sr_relation' else usable.any(axis=1))
        values=np.full(len(X),np.nan)
        values[ready]=np.max(np.where(usable[ready],deviation[ready][:,columns],-np.inf),axis=1)
        raw[key]=values;expected[key]=ready
    for key,forest in artifact['forests'].items():
        columns=forest['columns'];ready=eligible.copy()
        if forest['relation_only']:ready &= valid[:,columns].all(axis=1)
        values=np.full(len(X),np.nan)
        if ready.any() and forest['status']=='ok':values[ready]=forest['model'].score_samples(forest['encoder'].transform(X[ready][:,columns]))
        raw[key]=values;expected[key]=ready
    for key,values in raw.items():
        model_available=key not in artifact['forests'] or artifact['forests'][key]['status']=='ok'
        if model_available and not np.isfinite(values[expected[key]]).all():raise ValueError(f'Unexpected missing score: {key}')
        if np.isfinite(values[~expected[key]]).any():raise ValueError(f'Unexpected supported score: {key}')
    return raw,expected


def or_flags(raw, branches):
    matrix=np.column_stack([raw[b['score_key']] for b in branches])
    cuts=np.array([b['threshold'] for b in branches])
    ready=np.isfinite(matrix);positive=(ready&(matrix>cuts)).any(axis=1)
    result=np.full(len(matrix),np.nan)
    result[ready.all(axis=1)]=0.;result[positive]=1.
    return result


def guard_flags(flags, metadata, protocol):
    records=[];passed=True
    required=set(protocol['groups']);present=set(metadata.group.unique())
    if not required.issubset(present):
        return {'passed':False,'groups':[],'missing_required_groups':sorted(required-present),
                'guard_denominator':'prediction_available conditional monitored exposure'}
    for cadence in protocol['cadence_candidates']:
        issued=ev.decisions(flags,metadata,cadence,.5)
        intervals=ev.decision_intervals(metadata,issued,cadence)
        episodes=ev.alarm_episodes(intervals,protocol['alarm_merge_gap_minutes'])
        for row in ev.normal_statistics(intervals,episodes):
            okay=row['normal_monitored_hours']>0
            okay &= row['false_alarm_episodes_per_hour'] is not None and row['false_alarm_episodes_per_hour']<=protocol['calibration_alarm_budget_per_group_hour']+1e-12
            okay &= row['positive_time_fraction'] is not None and row['positive_time_fraction']<=protocol['calibration_positive_tick_fraction_per_group']+1e-12
            common=row['normal_common_eligible_hours']
            row.update(cadence=cadence,passed=bool(okay),
                       false_episodes_per_common_eligible_hour=row['false_alarm_episodes']/common if common else None,
                       positive_fraction_common_eligible=row['positive_normal_minutes']/(common*60) if common else None)
            records.append(row);passed &= bool(okay)
    return {'passed':bool(passed),'groups':records,'guard_denominator':'prediction_available conditional monitored exposure'}


def calibrate_arms(raw, metadata, protocol, score_upper_bounds=None, unavailable_reasons=None):
    cuts={};arms={}
    def branch(key,fraction):
        cache=(key,fraction)
        if cache not in cuts:
            if key in (unavailable_reasons or {}):raise ValueError(f'{key}: {unavailable_reasons[key]}')
            finite=np.isfinite(raw[key])&metadata.eligible.to_numpy(bool)
            for cadence in protocol['cadence_candidates']:
                for group in protocol['groups']:
                    if not np.any(finite&ev.decision_grid(metadata,cadence)&metadata.group.eq(group).to_numpy()):
                        raise ValueError(f'{key}: no finite required calibration exposure for {group}/C{cadence}')
            p=copy.deepcopy(protocol)
            for name in ['calibration_alarm_budget_per_group_hour','calibration_positive_tick_fraction_per_group']:
                p[name]*=fraction
            calibration=ev.calibrate(raw[key],metadata,p)
            threshold=calibration['threshold']
            if key in FORESTS and not threshold<1:raise ValueError(f'{key}: raw cut is unreachable (>=1)')
            upper_bound=(score_upper_bounds or {}).get(key,1.)
            if key in FORESTS and threshold>=upper_bound:raise ValueError(f'{key}: cut >= fitted forest conservative score upper bound')
            cuts[cache]={'score_key':key,'threshold':threshold,'budget_fraction':fraction,'calibration':calibration,
                         'score_upper_bound':upper_bound if key in FORESTS else None,
                         'upper_bound_check':'passed conservative bound; attainability not guaranteed' if key in FORESTS else None}
        return cuts[cache]
    definitions={'old_sr':[('old_sr',1.)],**{k:[(k,1.)] for k in FORESTS},
                 'family_sr':[(k,1/3) for k in ['sr_count','sr_latency','sr_relation']]}
    for forest in FORESTS:
        definitions[f'family_sr+{forest}']=[(k,1/6) for k in ['sr_count','sr_latency','sr_relation']]+[(forest,.5)]
    for name,definition in definitions.items():
        try:
            branches=[branch(key,fraction) for key,fraction in definition]
            guard=guard_flags(or_flags(raw,branches),metadata,protocol)
            arms[name]={'eligible':guard['passed'],'branches':branches,'union_calibration':guard,
                        'reason':None if guard['passed'] else 'actual OR failed a group/cadence normal guard'}
        except ValueError as exc:
            arms[name]={'eligible':False,'branches':[],'reason':str(exc),'union_calibration':None}
    return arms


def arm_flags(raw, config):
    return or_flags(raw,config['branches']) if config['eligible'] else np.full(len(next(iter(raw.values()))),np.nan)
