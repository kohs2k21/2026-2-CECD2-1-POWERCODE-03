"""Issue53 raw-copy mutations; no scores, model fitting or research imports."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
import re

import numpy as np

VERSION = 'process-synthetic-generation-v1'
KEYS = ('transaction_id', 'process_hub_id', 'retry_count')


def timestamp(value):
    if not isinstance(value, str):
        return None
    if re.search(r'[.,]\d{7}', value):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def count(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and 0 <= number < 2**53 and number % 1 == 0 else None
    except (ValueError, InvalidOperation):
        return None


def parent_key(raw):
    return tuple(str(raw[key]) if raw.get(key) is not None else None for key in KEYS)


def reference_reason(row):
    raw = row['raw']
    if any(raw.get(key) in (None, '') for key in (*KEYS, 'process_id', 'adapter_type', 'channel_id', 'status')):
        return 'missing_required_process_field'
    if raw['status'] != 'S':
        return 'not_successful_quality_reference'
    start, end = timestamp(raw.get('start_time_raw')), timestamp(raw.get('end_time_raw'))
    try:
        if start is None or end is None or end < start:
            return 'invalid_process_time'
    except TypeError:
        return 'ambiguous_time_basis'
    numbers = [count(raw.get(key)) for key in ('total_count', 'success_count', 'error_count', 'retry_count')]
    if any(number is None for number in numbers) or numbers[0] != numbers[1] + numbers[2] or numbers[2] != 0:
        return 'count_contract_unverifiable'
    return None


def fit_generator(train):
    """Only caller-selected snapshot train rows may form adapter duration references."""
    groups = defaultdict(list)
    for row in train:
        if row.get('split') != 'train':
            raise ValueError('synthetic_generator_fit_requires_train')
        if not reference_reason(row) and row.get('label') != 'anomaly':
            raw = row['raw']
            groups[raw['adapter_type']].append((timestamp(raw['end_time_raw']) - timestamp(raw['start_time_raw'])).total_seconds() * 1000)
    return {'version': VERSION, 'fitSplit': 'train', 'adapterDurationMs': {str(key): median(values) for key, values in sorted(groups.items())}}


def generate(rows, fit, profile, *, scenario, severity, seed, target_rows, context_siblings=None):
    if scenario not in profile['scenarios'] or type(severity) is not int or severity not in profile['severity']:
        raise ValueError('synthetic_unsupported_scenario')
    ids = [row['rowId'] for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('synthetic_duplicate_identity')
    if any(row.get('split') not in ('test', 'validation') for row in rows):
        raise ValueError('synthetic_evaluation_split_required')
    base, output = deepcopy(rows), deepcopy(rows)
    result = {'status': 'generated', 'reason': None, 'rows': output, 'truth': [], 'changes': [],
              'parentChanges': [], 'rejects': [], 'scenario': scenario, 'severity': severity,
              'seed': seed, 'requestedRows': target_rows, 'selection': 'before_scores_no_detector_output',
              'generatorVersion': VERSION, 'parentPolicy': profile['parentPolicy']}
    if scenario == 'P01':
        result.update(status='unsupported', reason='failure_semantics_unconfirmed', generatedCount=0, rejectedCount=0)
        return result
    reasons, limits = {}, {}
    keys = [(*parent_key(row['raw']), str(row['raw'].get('process_id'))) for row in base]
    duplicates = {key for key, number in Counter(keys).items() if number > 1}
    dependency_limits = {}
    sibling_registry = {row['rowId']: row for row in base}
    for sibling in context_siblings or []:
        previous = sibling_registry.get(sibling['rowId'])
        if previous is not None and previous['raw'] != sibling['raw']:
            raise ValueError('synthetic_context_identity_conflict')
        sibling_registry[sibling['rowId']] = sibling
    for sibling in sibling_registry.values():
        child = sibling['raw']
        if child.get('depend_process_id') is None:
            continue
        key = (*parent_key(child), str(child['depend_process_id']))
        start = timestamp(child.get('start_time_raw'))
        try:
            if key not in dependency_limits:
                dependency_limits[key] = start
            elif start is None or dependency_limits[key] is None:
                dependency_limits[key] = None
            else:
                dependency_limits[key] = min(dependency_limits[key], start)
        except TypeError:
            dependency_limits[key] = None
    for index, row in enumerate(base):
        raw, parent = row['raw'], row.get('parent')
        reason = reference_reason(row)
        if not reason and keys[index] in duplicates:
            reason = 'duplicate_process_identity'
        if not reason and raw['adapter_type'] not in fit['adapterDurationMs']:
            reason = 'unseen_adapter_train_reference'
        if not reason and (not parent or parent_key(raw) != parent_key(parent['raw'])):
            reason = 'missing_or_ambiguous_parent'
        if not reason:
            start, end = timestamp(raw['start_time_raw']), timestamp(raw['end_time_raw'])
            pstart, pend = timestamp(parent['raw'].get('start_time_raw')), timestamp(parent['raw'].get('end_time_raw'))
            try:
                if pstart is None or pend is None or start < pstart or end > pend or pend < pstart:
                    reason = 'existing_parent_time_violation'
                else:
                    limit = pend
                    if keys[index] in dependency_limits:
                        dependency_start = dependency_limits[keys[index]]
                        if dependency_start is None:
                            reason = 'unknown_dependent_process_start'
                        else:
                            limit = min(limit, dependency_start)
                    if end > limit:
                        reason = 'existing_dependency_time_violation'
                    limits[index] = limit
            except TypeError:
                reason = 'ambiguous_time_basis'
        if reason:
            reasons[index] = reason
    candidates = [index for index in range(len(base)) if index not in reasons]
    rng = np.random.default_rng(seed)
    if scenario == 'P03':
        groups = defaultdict(list)
        for index, row in enumerate(base):
            raw = row['raw']
            groups[(raw.get('adapter_type'), (row.get('parent') or {}).get('raw', {}).get('interface_id'))].append(index)
        blocks = []
        for group in groups.values():
            times = {index: timestamp(base[index]['raw'].get('start_time_raw')) for index in group}
            if len({value.tzinfo is not None for value in times.values() if value is not None}) > 1:
                for index in group: reasons[index] = 'ambiguous_time_basis'
                continue
            order = sorted(group, key=lambda index: (times[index] is None,
                                                     times[index] if times[index] is not None else 0, base[index]['rowId']))
            for offset in range(max(0, len(order) - target_rows + 1)):
                block = order[offset:offset + target_rows]
                if all(index not in reasons for index in block):
                    blocks.append(block)
        selected = blocks[int(rng.integers(len(blocks)))] if blocks else []
    else:
        selected = rng.permutation(candidates)[:target_rows].tolist()
    planned = {}
    for index in selected:
        raw = base[index]['raw']
        if scenario == 'P04':
            before = count(raw['total_count'])
            after = before + profile['countIncrements'][severity - 1]
            if after >= 2**53:
                reasons[index] = 'count_increment_not_exactly_representable'
                continue
            planned[index] = {'total_count': after}
        else:
            delta = max(1, fit['adapterDurationMs'][raw['adapter_type']]) * (profile['delayMultipliers'][severity - 1] - 1)
            after = timestamp(raw['end_time_raw']) + timedelta(milliseconds=delta)
            if after > limits[index]:
                reasons[index] = 'insufficient_parent_or_dependency_headroom'
                continue
            planned[index] = {'end_time_raw': after.isoformat()}
    if scenario == 'P03' and len(planned) != target_rows:
        for index in selected:
            reasons.setdefault(index, 'consecutive_block_rejected_as_unit')
        planned = {}
    for index, changes in planned.items():
        row = output[index]
        for field, value in changes.items():
            row['raw'][field] = value
            result['changes'].append({'rowId': row['rowId'], 'field': field,
                                      'before': base[index]['raw'][field], 'after': value})
        result['truth'].append({'rowId': row['rowId'], 'label': 1, 'scenario': scenario,
                                'parentId': row['parent']['rowId']})
    result['rejects'] = [{'rowId': base[index]['rowId'], 'reason': reason, 'selected': index in selected}
                         for index, reason in sorted(reasons.items())]
    result.update(generatedCount=len(planned), rejectedCount=len(reasons), selectedCount=len(selected),
                  unfilledRequestedRows=target_rows - len(planned))
    if not planned:
        result.update(status='generation_failed', reason='no_valid_injection')
    return result
