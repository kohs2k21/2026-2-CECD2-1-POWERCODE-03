"""Conditional numeric SR policy. Pure fit/apply, no labels, no LLM or ML fit.

The semantic rules/feature formulas stay fixed. Only reference scope and numeric
thresholds change under a new policy version. Missing input/reference is unknown.
"""
import numpy as np

POLICY_VERSION = 'conditional_numeric_SR_1.0.1'
MIN_ROWS = 20
MIN_RUNS = 10


def tri_or(states):
    a = np.asarray(states)
    return np.where((a == 1).any(axis=1), 1, np.where((a == -1).any(axis=1), -1, 0)).astype(np.int8)


def describe(values, run_ids, unit):
    ok = np.isfinite(values)
    n = int(ok.sum())
    runs = len(set(np.asarray(run_ids)[ok]))
    if n < MIN_ROWS or runs < MIN_RUNS:
        return {'supported': False, 'n': n, 'runs': runs, 'reason': 'insufficient_numeric_reference'}
    a = values[ok]
    q25, median, q75, p95, p99 = np.quantile(a, [.25, .5, .75, .95, .99], method='linear')
    # Stability floors are declared in the plan, not selected using TEST labels.
    floor = 1.0 if unit == 'second' else .01
    spread = max(float(p95 - median), float(q75 - q25), abs(float(median)) * .05, floor)
    return {'supported': True, 'n': n, 'runs': runs, 'median': float(median), 'p95': float(p95),
            'p99': float(p99), 'spread': spread, 'unit': unit, 'scale_floor': floor}


def fit_reference(values, metadata, names, units):
    groups = {'global': np.ones(len(values), dtype=bool)}
    kinds = metadata['TASK_KIND'].to_numpy(dtype=str)
    family = metadata['TASK_FAMILY'].to_numpy(dtype=str)
    for k in sorted(set(kinds)): groups['kind:' + k] = kinds == k
    for f in sorted(set(family)): groups['family:' + f] = family == f
    reference = {'version': POLICY_VERSION, 'names': list(names), 'units': dict(units),
                 'fit_rows': len(values), 'fit_runs': len(set(metadata.RUN_ID)),
                 'group_vocabulary_from': 'TRAIN only', 'minimum_rows': MIN_ROWS, 'minimum_runs': MIN_RUNS,
                 'groups': {}}
    for group, select in groups.items():
        reference['groups'][group] = {name: describe(values[select, j], metadata.RUN_ID.to_numpy()[select], units[name]) for j, name in enumerate(names)}
    return reference


def resolve(reference, family, kind, name, grouped=True):
    candidates = ['family:' + family, 'kind:' + kind, 'global'] if grouped else ['global']
    for group in candidates:
        record = reference['groups'].get(group, {}).get(name)
        if record is not None and record['supported']:
            return group, record
    return 'unavailable', None


def evaluate_rules(values, metadata, reference, policy, joint_cutoff=None):
    n, k = values.shape
    thresholds = np.full((n, k), np.nan)
    states = np.full((n, k), -1, dtype=np.int8)
    residuals = np.full((n, k), np.nan)
    sources = np.full((n, k), 'unavailable', dtype=object)
    family = metadata.TASK_FAMILY.to_numpy(dtype=str)
    kind = metadata.TASK_KIND.to_numpy(dtype=str)
    grouped = policy != 'global_p99'
    for f, t in sorted(set(zip(family, kind))):
        rows = np.flatnonzero((family == f) & (kind == t))
        for j, name in enumerate(reference['names']):
            group, r = resolve(reference, f, t, name, grouped)
            if r is None: continue
            sources[rows, j] = group
            residuals[rows, j] = (values[rows, j] - r['p95']) / r['spread']
            if policy == 'grouped_joint_p99':
                if joint_cutoff is None: continue
                threshold = r['p95'] + joint_cutoff * r['spread']
            else:
                threshold = r['p95'] if policy == 'grouped_p95' else r['p99']
            thresholds[rows, j] = threshold
            valid = rows[np.isfinite(values[rows, j])]
            states[valid, j] = (values[valid, j] > threshold).astype(np.int8)
    complete = np.isfinite(residuals).all(axis=1)
    score = np.full(n, np.nan)
    any_valid = np.isfinite(residuals).any(axis=1)
    score[any_valid] = np.max(np.where(np.isfinite(residuals[any_valid]), residuals[any_valid], -np.inf), axis=1)
    return {'state': tri_or(states), 'rule_states': states, 'thresholds': thresholds,
            'source_groups': sources, 'residuals': residuals, 'score': score, 'complete': complete}


def fit_joint_cutoff(calibration, threshold, reference, normalizer_class):
    c = evaluate_rules(calibration[0], calibration[1], reference, 'grouped_joint_p99')
    t = evaluate_rules(threshold[0], threshold[1], reference, 'grouped_joint_p99')
    for result, meta in [(c, calibration[1]), (t, threshold[1])]:
        if result['complete'].sum() < 200 or len(set(meta.RUN_ID.to_numpy()[result['complete']])) < 3:
            raise ValueError('Insufficient independent complete-case joint reference; no automatic substitution')
    norm = normalizer_class.fit(c['score'][c['complete']])
    normalized = norm.transform(t['score'][t['complete']])
    q = float(np.quantile(normalized, .99, method='linear'))
    # An affine normalize/inverse roundtrip can turn 1.0 into 1-epsilon,
    # changing strict > at real observed boundaries. Raw quantile is canonical.
    cutoff = max(0., float(np.quantile(t['score'][t['complete']], .99, method='linear')))
    return cutoff, {'cutoff': cutoff, 'calibration_complete_rows': int(c['complete'].sum()),
                    'threshold_complete_rows': int(t['complete'].sum()),
                    'calibration_total_rows': len(c['complete']), 'threshold_total_rows': len(t['complete']),
                    'normalizer_center': norm.center, 'normalizer_spread': norm.spread,
                    'normalized_threshold_p99': q, 'authoritative_quantile_space': 'raw_max_residual', 'only_complete_rows_set_the_cutoff': True,
                    'partial_observations_at_application': 'known violation warns; otherwise any unknown rule keeps SR unknown',
                    'threshold_is_at_least_conditional_TRAIN_p95': True}


def effective_conditions(reference, cutoff):
    rows = []
    for group, features in reference['groups'].items():
        for name, r in features.items():
            if not r['supported']: continue
            rows.append({'group': group, 'rule': name, 'operator': 'gt', 'threshold': r['p95'] + cutoff * r['spread'],
                         'unit': r['unit'], 'reference_rows': r['n'], 'reference_runs': r['runs'],
                         'train_p95': r['p95'], 'spread': r['spread'], 'joint_cutoff': cutoff,
                         'definition_policy_version': POLICY_VERSION})
    return rows
