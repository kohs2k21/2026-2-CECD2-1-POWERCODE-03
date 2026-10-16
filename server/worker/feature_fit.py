"""Train-only group statistics shared by Trainer and Detector; no implicit fallback."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from statistics import median

from feature_runtime import _evaluate, _number, validate_definitions

FIT_OPERATIONS = frozenset({'group_median', 'group_centered'})
FIT_VERSION = 'adapter-group-median-v1'


def requires_fit(expression):
    return expression.get('op') in FIT_OPERATIONS


def definitions_hash(definitions):
    validate_definitions(definitions)
    return hashlib.sha256(json.dumps(definitions, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _group(raw):
    value = raw.get('adapter_type')
    return value if isinstance(value, str) and value else None


def fit_features(definitions, rows, *, snapshot_id):
    """Caller supplies train split only; rows with explicit anomaly labels are excluded."""
    digest = definitions_hash(definitions)
    features = {}
    for definition in definitions:
        expression = definition['expression']
        if not requires_fit(expression):
            continue
        groups = {}
        for row in rows:
            if row.get('label') == 'anomaly':
                continue
            raw = row['raw']
            group = _group(raw)
            value = _evaluate(expression['value'], raw)
            if group is not None and value is not None and value.is_finite():
                groups.setdefault(group, []).append(value)
        if not groups:
            raise ValueError('fitted_feature_reference_empty')
        features[definition['name']] = {'groups': {key: str(median(values)) for key, values in sorted(groups.items())},
                                        'referenceCounts': {key: len(values) for key, values in sorted(groups.items())}}
    return {'version': FIT_VERSION, 'definitionsHash': digest, 'features': features,
            'reference': {'snapshotId': str(snapshot_id), 'split': 'train',
                          'excludedLabel': 'anomaly', 'unknownGroup': 'missing'}}


def validate_state(definitions, state):
    names = {item['name'] for item in definitions if requires_fit(item['expression'])}
    if not isinstance(state, dict) or state.get('version') != FIT_VERSION or \
            state.get('definitionsHash') != definitions_hash(definitions) or \
            not isinstance(state.get('features'), dict) or set(state['features']) != names or \
            state.get('reference', {}).get('split') != 'train':
        raise ValueError('preprocessing_definition_mismatch')
    for item in state['features'].values():
        if not isinstance(item, dict) or not isinstance(item.get('groups'), dict) or not item['groups'] or \
                any(not isinstance(key, str) or not key or _number(value) is None for key, value in item['groups'].items()):
            raise ValueError('invalid_preprocessing_state')


def transform(definitions, raw, state):
    validate_definitions(definitions)
    validate_state(definitions, state)
    values, missing = {}, []
    for definition in definitions:
        expression = definition['expression']
        name = definition['name']
        if requires_fit(expression):
            reference = state['features'][name]['groups'].get(_group(raw))
            value = Decimal(reference) if reference is not None else None
            if value is not None and expression['op'] == 'group_centered':
                original = _evaluate(expression['value'], raw)
                value = original - value if original is not None else None
        else:
            value = _evaluate(expression, raw)
        values[name] = str(value) if value is not None and value.is_finite() else None
        if values[name] is None:
            missing.append(name)
    return values, missing
