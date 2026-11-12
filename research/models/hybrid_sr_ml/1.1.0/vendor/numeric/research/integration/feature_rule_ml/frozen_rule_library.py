"""Preserve existing model rules and evaluate them using the final condition engine."""
import copy
from collections import Counter
import numpy as np
from .common import InputFailure, digest
from specmodel.rule_logic import value_condition

FIELDS = ('expression', 'grain', 'population', 'group_by', 'missing_policy')
ROLE = {'hr': 'HR', 'observed': 'observed_SR', 'baseline': 'numeric_SR'}


def structure(feature):
    return {k: feature[k] for k in FIELDS}


def compile_library(records, definitions):
    """Pure structural checks; no new role, threshold, or explanatory text."""
    features = {r['input']['feature']['sha']: copy.deepcopy(r['input']['feature']) for r in definitions}
    if len(features) != len(definitions):
        raise InputFailure('Duplicate source feature definition SHA')
    bykey = {r['id']: r['input'] for r in definitions}
    rules = []
    for record in records:
        f, d = record['feature'], record['decision']
        if f['sha'] not in features or f != features[f['sha']]:
            raise InputFailure('Rule does not match its exact original feature definition')
        if d['state'] != 'proposed' or d['request'] not in ROLE or not d.get('condition'):
            raise InputFailure('Only previously proposed, executable role records may be imported')
        key = record['key'].rsplit(':', 1)[0]
        if key not in bykey:
            raise InputFailure('Original rule input record missing')
        original_input = bykey[key]
        if original_input['source_version'] != record['source_version']:
            raise InputFailure('Original rule input source differs')
        if d['request'] == 'baseline':
            b = record['baseline']
            if b.get('state') != 'verified' or b.get('reference_split') != 'train':
                raise InputFailure('Frozen numerical rule requires its original verified TRAIN reference')
            if b.get('reference_valid_n', 0) < b.get('minimum_n', 20):
                raise InputFailure('Reference sample minimum not met')
            if b['unit'] != f['unit'] or b['value'] != d['condition'].get('value') or b['operator'] != d['condition']['op']:
                raise InputFailure('Rule condition differs from frozen reference')
        rules.append({'id': record['key'], 'feature_sha': f['sha'], 'role': ROLE[d['request']],
                      'original': copy.deepcopy(record), 'original_input': copy.deepcopy(original_input)})
    if len({r['id'] for r in rules}) != len(rules):
        raise InputFailure('Duplicate rule IDs')
    return {'contract': 'frozen-model-rule-library1.0', 'features': features, 'rules': rules,
            'new_model_generations': 0, 'roles': dict(Counter(r['role'] for r in rules)),
            'unique_feature_conditions': len({(r['feature_sha'], digest(r['original']['decision']['condition'])) for r in rules}),
            'not_current_LLM_regeneration': True, 'operational_approval': False}


def combine_codes(arrays, size):
    """-2 outside population; -1 unassessable; 0 nonwarning; 1 warning."""
    if not arrays:
        return np.full(size, -1, dtype=np.int8)
    a = np.stack(arrays)
    if not np.isin(a, [-2, -1, 0, 1]).all():
        raise InputFailure('Unknown rule state')
    return np.where((a == 1).any(axis=0), 1,
                    np.where((a != -2).any(axis=0) & ((a == 0) | (a == -2)).all(axis=0), 0, -1)).astype(np.int8)


def named(code):
    return {1: 'warning', 0: 'nonwarning', -1: 'unassessable', -2: 'outside_population'}[int(code)]


def state_counts(codes):
    return {named(k): int(v) for k, v in zip(*np.unique(codes, return_counts=True))}


def apply_rule(values, population, dtype, condition):
    codes = np.full(len(population), -2, dtype=np.int8)
    for i in np.flatnonzero(population):
        v = values[i]
        if dtype == 'boolean' and v is not None and type(v) is not bool:
            raise InputFailure('Boolean feature must retain actual Boolean values')
        outcome = value_condition(v, condition)
        codes[i] = -1 if outcome is None else 1 if outcome else 0
    return codes
