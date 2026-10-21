"""PROCESS-row Soft Rule prototype: train-only fit, pure scoring, one interface.

Every method is a pair of pure functions
    fit(train_df, params) -> fitted (JSON-serialisable dict)
    score(df, fitted)     -> DataFrame[flag(boolean, NA = unjudged), score(float), reason(str), basis(str),
                                       fallback_reason(str | None: why the global fallback was used)]
plus a catalogue entry (methodId `sr:<key>`, parameterSchema, defaults, defaultsSource,
inputRequirements, fitStatistics, limitations) so the registry can be listed as JSON.

Only numpy / pandas are used. No row outside the caller-supplied train frame influences a fit.
The caller is responsible for passing status=S reference rows (train-success-only-v1); the
fitted dict records what it actually received so a reviewer can check.

Input frame columns (Trainer raw field names, snake_case):
    adapter_type, status, total_count, success_count, error_count, retry_count, duration_ms
"""
from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd

VERSION = 'sr-prototype-v0'
NUMERIC_FIELDS = ('duration_ms', 'total_count', 'success_count', 'error_count', 'retry_count')
MAD_TO_SIGMA = 1.4826      # 1 / Phi^-1(0.75); modified z = 0.6745 * (x - med) / MAD
MEANAD_TO_SIGMA = 1.2533   # sqrt(pi / 2)

# Output vocabulary, listed in catalog(); tests check score() stays inside it.
REASON_CODES = frozenset({
    'above_threshold', 'within_threshold', 'value_missing', 'value_negative',
    'group_unseen', 'group_missing', 'group_support_insufficient', 'scale_zero', 'pooled_support_insufficient',
    'threshold_unavailable', 'total_zero_or_missing',
    'error_ratio_exceeded', 'retry_ratio_exceeded', 'error_and_retry_ratio_exceeded',
    'conservation_violated', 'conserved',
})
# ratio_rule partial verdict (decision D): '<reason>;<side>_unjudged:<cause>'
REASON_SUFFIX = {'pattern': ';<side>_unjudged:<cause>', 'side': ['error', 'retry'],
                 'cause': ['threshold_unavailable', 'value_missing']}
BASIS_VALUES = ['group', 'global', 'group+pooled', 'formula', 'unavailable',
                'error:<parameter|train_quantile_<q>|unavailable>,retry:<same> (ratio_rule)']
FALLBACK_REASON_CODES = frozenset({'group_unseen', 'group_missing', 'group_support_insufficient'})


# --------------------------------------------------------------------------- helpers
def _numeric(series):
    return pd.to_numeric(series, errors='coerce').astype(float)


def _group_key(df, group_by):
    raw = df[group_by] if group_by in df else pd.Series([None] * len(df), index=df.index)
    return pd.Series([str(v) if (v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v) != '')
                      else None for v in raw.tolist()], index=df.index, dtype=object)


def _merge_params(defaults, params):
    merged = copy.deepcopy(defaults)
    for key, value in (params or {}).items():
        if key not in defaults:
            raise ValueError(f'unknown_parameter:{key}')
        merged[key] = value
    return merged


def _train_summary(train):
    status = train['status'].astype(str).value_counts().to_dict() if 'status' in train else {}
    return {'rows': int(len(train)), 'statusCounts': {str(k): int(v) for k, v in status.items()}}


def _fit_groups(train, field, group_by, min_rows, stat_fn, *, nonnegative_only=False):
    values = _numeric(train[field]) if field in train else pd.Series(np.nan, index=train.index)
    keys = _group_key(train, group_by)
    mask = values.notna() & keys.notna()
    if nonnegative_only:
        mask &= values.ge(0)
    groups = {}
    for key, part in values[mask].groupby(keys[mask]):
        arr = part.to_numpy(dtype=float)
        entry = {'n': int(len(arr)), 'sufficient': bool(len(arr) >= min_rows)}
        if entry['sufficient']:
            entry.update(stat_fn(arr))
        groups[str(key)] = entry
    pooled = values[mask].to_numpy(dtype=float)
    global_entry = {'n': int(len(pooled)), 'sufficient': bool(len(pooled) >= min_rows)}
    if global_entry['sufficient']:
        global_entry.update(stat_fn(pooled))
    return groups, global_entry, int(mask.sum())


def _resolve(keys, fitted, names):
    """Per-row reference statistics: group first, optional global fallback, else unavailable.

    Returns (basis, why, stats, fallback_reason):
      basis           'group' | 'global' | 'unavailable'
      why             None for basis='group'; otherwise the group lookup outcome
                      (group_missing / group_unseen / group_support_insufficient)
      fallback_reason same code as `why` but only where basis='global' (kept in the output so a
                      global-fallback verdict still says why the group statistics were not used)
    """
    stats, glob, fallback = fitted['groups'], fitted['global'], fitted['params']['fallback']
    basis, why, fallback_reason, columns = [], [], [], {name: [] for name in names}
    for key in keys.tolist():
        entry = stats.get(key) if key is not None else None
        if entry is not None and entry['sufficient']:
            basis.append('group'); why.append(None); fallback_reason.append(None); src = entry
        else:
            reason = ('group_missing' if key is None else
                      'group_unseen' if entry is None else 'group_support_insufficient')
            if fallback == 'global' and glob['sufficient']:
                basis.append('global'); why.append(reason); fallback_reason.append(reason); src = glob
            else:
                basis.append('unavailable'); why.append(reason); fallback_reason.append(None); src = None
        for name in names:
            columns[name].append(float(src[name]) if src is not None and src.get(name) is not None else np.nan)
    frame = pd.DataFrame({name: pd.Series(col, index=keys.index, dtype=float) for name, col in columns.items()})
    return (pd.Series(basis, index=keys.index, dtype=object),
            pd.Series(why, index=keys.index, dtype=object), frame,
            pd.Series(fallback_reason, index=keys.index, dtype=object))


def _assemble(index, hit, score, judged, reason, basis, fallback_reason=None):
    flag = pd.Series(pd.NA, index=index, dtype='boolean')
    flag.loc[judged] = hit.loc[judged].astype(bool)
    score = score.where(judged, np.nan)
    if fallback_reason is None:
        fallback_reason = pd.Series([None] * len(index), index=index, dtype=object)
    return pd.DataFrame({'flag': flag, 'score': score.astype(float),
                         'reason': reason.astype(object), 'basis': basis.astype(object),
                         'fallback_reason': fallback_reason.astype(object)}, index=index)


def _threshold_reason(judged, hit, unjudged_reason):
    reason = unjudged_reason.copy()
    reason.loc[judged & hit] = 'above_threshold'
    reason.loc[judged & ~hit] = 'within_threshold'
    return reason


def _field_schema():
    return {'type': 'string', 'enum': list(NUMERIC_FIELDS)}


# --------------------------------------------------------------------------- 1. robust z-score
ROBUST_Z = {
    'methodId': 'sr:robust_z', 'key': 'robust_z', 'displayName': 'Group robust z-score (median/MAD)',
    'kind': 'sr', 'status': 'prototype', 'unit': 'PROCESS_row',
    'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'field': _field_schema(), 'groupBy': {'type': 'string', 'enum': ['adapter_type']},
        'k': {'type': 'number', 'exclusiveMinimum': 0}, 'side': {'type': 'string', 'enum': ['upper', 'both']},
        'minGroupRows': {'type': 'integer', 'minimum': 1}, 'fallback': {'type': 'string', 'enum': ['none', 'global']},
        'madZeroPolicy': {'type': 'string', 'enum': ['unjudged', 'meanad']}}},
    'defaults': {'field': 'duration_ms', 'groupBy': 'adapter_type', 'k': 3.5, 'side': 'upper',
                 'minGroupRows': 30, 'fallback': 'none', 'madZeroPolicy': 'unjudged'},
    'defaultsSource': ('k=3.5: Iglewicz & Hoaglin (1993) modified z-score convention, not tuned on project data; '
                       'fallback=none mirrors Trainer feature_fit group_median (unseen group -> missing, no global substitute); '
                       'minGroupRows=30 is a prototype guess, undecided'),
    'inputRequirements': {'fields': ['<field>', 'adapter_type'], 'fitSplit': 'train',
                          'trainFilter': 'status==S reference rows supplied by caller', 'minTrainRows': 'minGroupRows per group'},
    'fitStatistics': ['per-group n, median, MAD, meanAD', 'global n, median, MAD, meanAD'],
    'limitations': ['MAD=0 (discrete/constant groups) leaves rows unjudged unless madZeroPolicy=meanad',
                    'one-sided upper by default: short durations / low counts are never flagged',
                    'per-row group lookup; unseen adapter has no verdict when fallback=none'],
}


def _robust_stats(arr):
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    meanad = float(np.mean(np.abs(arr - med)))
    return {'median': med, 'mad': mad, 'meanad': meanad}


def fit_robust_z(train, params=None):
    p = _merge_params(ROBUST_Z['defaults'], params)
    groups, glob, used = _fit_groups(train, p['field'], p['groupBy'], p['minGroupRows'], _robust_stats)
    return {'methodId': ROBUST_Z['methodId'], 'version': VERSION, 'params': p, 'fitSplit': 'train',
            'train': {**_train_summary(train), 'usedRows': used}, 'groups': groups, 'global': glob}


def score_robust_z(df, fitted):
    p = fitted['params']
    keys = _group_key(df, p['groupBy'])
    x = _numeric(df[p['field']]) if p['field'] in df else pd.Series(np.nan, index=df.index)
    basis, why, s, fallback_reason = _resolve(keys, fitted, ['median', 'mad', 'meanad'])
    scale = s['mad'] * MAD_TO_SIGMA
    if p['madZeroPolicy'] == 'meanad':
        scale = scale.where(s['mad'].gt(0), s['meanad'] * MEANAD_TO_SIGMA)
    z = (x - s['median']) / scale
    judged = x.notna() & basis.ne('unavailable') & scale.gt(0) & z.notna()
    hit = (z > p['k']) if p['side'] == 'upper' else (z.abs() > p['k'])
    unjudged_reason = why.copy()
    unjudged_reason.loc[x.isna()] = 'value_missing'
    unjudged_reason.loc[x.notna() & basis.ne('unavailable') & ~scale.gt(0)] = 'scale_zero'
    return _assemble(df.index, hit.fillna(False), z, judged, _threshold_reason(judged, hit.fillna(False), unjudged_reason),
                     basis, fallback_reason)


# --------------------------------------------------------------------------- 2. IQR fence
IQR_FENCE = {
    'methodId': 'sr:iqr_fence', 'key': 'iqr_fence', 'displayName': 'Group Tukey IQR fence',
    'kind': 'sr', 'status': 'prototype', 'unit': 'PROCESS_row',
    'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'field': _field_schema(), 'groupBy': {'type': 'string', 'enum': ['adapter_type']},
        'k': {'type': 'number', 'exclusiveMinimum': 0}, 'side': {'type': 'string', 'enum': ['upper', 'both']},
        'minGroupRows': {'type': 'integer', 'minimum': 1}, 'fallback': {'type': 'string', 'enum': ['none', 'global']}}},
    'defaults': {'field': 'duration_ms', 'groupBy': 'adapter_type', 'k': 1.5, 'side': 'upper',
                 'minGroupRows': 30, 'fallback': 'none'},
    'defaultsSource': ('k=1.5 inner fence (Tukey 1977); 3.0 = far-out fence. Same as trend_sr_comparison R03 variants '
                       '{1.5, 3.0}. Not tuned on project data'),
    'inputRequirements': {'fields': ['<field>', 'adapter_type'], 'fitSplit': 'train',
                          'trainFilter': 'status==S reference rows supplied by caller', 'minTrainRows': 'minGroupRows per group'},
    'fitStatistics': ['per-group n, q1, q3, iqr', 'global n, q1, q3, iqr'],
    'limitations': ['IQR=0 (discrete counts with narrow spread) leaves rows unjudged',
                    'fence is linear in IQR: heavy-tailed durations produce many hits at k=1.5'],
}


def _iqr_stats(arr):
    q1, q3 = (float(v) for v in np.quantile(arr, [0.25, 0.75]))
    return {'q1': q1, 'q3': q3, 'iqr': q3 - q1}


def fit_iqr_fence(train, params=None):
    p = _merge_params(IQR_FENCE['defaults'], params)
    groups, glob, used = _fit_groups(train, p['field'], p['groupBy'], p['minGroupRows'], _iqr_stats)
    return {'methodId': IQR_FENCE['methodId'], 'version': VERSION, 'params': p, 'fitSplit': 'train',
            'train': {**_train_summary(train), 'usedRows': used}, 'groups': groups, 'global': glob}


def score_iqr_fence(df, fitted):
    p = fitted['params']
    keys = _group_key(df, p['groupBy'])
    x = _numeric(df[p['field']]) if p['field'] in df else pd.Series(np.nan, index=df.index)
    basis, why, s, fallback_reason = _resolve(keys, fitted, ['q1', 'q3', 'iqr'])
    upper = (x - s['q3']) / s['iqr']
    lower = (s['q1'] - x) / s['iqr']
    score = upper if p['side'] == 'upper' else pd.concat([upper, lower], axis=1).max(axis=1)
    judged = x.notna() & basis.ne('unavailable') & s['iqr'].gt(0) & score.notna()
    hit = score > p['k']
    unjudged_reason = why.copy()
    unjudged_reason.loc[x.isna()] = 'value_missing'
    unjudged_reason.loc[x.notna() & basis.ne('unavailable') & ~s['iqr'].gt(0)] = 'scale_zero'
    return _assemble(df.index, hit.fillna(False), score, judged, _threshold_reason(judged, hit.fillna(False), unjudged_reason),
                     basis, fallback_reason)


# --------------------------------------------------------------------------- 3. quantile tail (P99)
QUANTILE_TAIL = {
    'methodId': 'sr:quantile_tail', 'key': 'quantile_tail', 'displayName': 'Group empirical quantile threshold (P99)',
    'kind': 'sr', 'status': 'prototype', 'unit': 'PROCESS_row',
    'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'field': _field_schema(), 'groupBy': {'type': 'string', 'enum': ['adapter_type']},
        'quantile': {'type': 'number', 'exclusiveMinimum': 0, 'exclusiveMaximum': 1},
        'minGroupRows': {'type': 'integer', 'minimum': 1}, 'fallback': {'type': 'string', 'enum': ['none', 'global']}}},
    'defaults': {'field': 'duration_ms', 'groupBy': 'adapter_type', 'quantile': 0.99,
                 'minGroupRows': 1000, 'fallback': 'global'},
    'defaultsSource': ('research process_anomaly_pipeline QuantileBook: p99 with min_p99_support=1000 per group, '
                       'GLOBAL fallback, values >= 0 only (config.py, quantiles.py). Research choice, not validated as operating value'),
    'inputRequirements': {'fields': ['<field>', 'adapter_type'], 'fitSplit': 'train',
                          'trainFilter': 'status==S reference rows supplied by caller; negative values dropped',
                          'minTrainRows': 'minGroupRows per group (1000 by research default)'},
    'fitStatistics': ['per-group n, threshold (quantile)', 'global n, threshold'],
    'limitations': ['expected hit rate on in-distribution rows ~ (1 - quantile) by construction: this is the design false-alarm budget, not an error',
                    'min support 1000 leaves small adapters on global fallback or unjudged',
                    'discrete counts: threshold often equals the maximum train value, so +1 shifts are only partly visible'],
}


def fit_quantile_tail(train, params=None):
    p = _merge_params(QUANTILE_TAIL['defaults'], params)
    q = p['quantile']
    groups, glob, used = _fit_groups(train, p['field'], p['groupBy'], p['minGroupRows'],
                                     lambda arr: {'threshold': float(np.quantile(arr, q))}, nonnegative_only=True)
    return {'methodId': QUANTILE_TAIL['methodId'], 'version': VERSION, 'params': p, 'fitSplit': 'train',
            'train': {**_train_summary(train), 'usedRows': used}, 'groups': groups, 'global': glob}


def score_quantile_tail(df, fitted):
    p = fitted['params']
    keys = _group_key(df, p['groupBy'])
    x = _numeric(df[p['field']]) if p['field'] in df else pd.Series(np.nan, index=df.index)
    basis, why, s, fallback_reason = _resolve(keys, fitted, ['threshold'])
    thr = s['threshold']
    score = (x / thr).where(thr.gt(0), x - thr)
    judged = x.notna() & x.ge(0) & basis.ne('unavailable') & thr.notna()
    hit = x > thr
    unjudged_reason = why.copy()
    unjudged_reason.loc[x.isna()] = 'value_missing'
    unjudged_reason.loc[x.notna() & x.lt(0)] = 'value_negative'
    return _assemble(df.index, hit.fillna(False), score, judged, _threshold_reason(judged, hit.fillna(False), unjudged_reason),
                     basis, fallback_reason)


# --------------------------------------------------------------------------- 4. centered pooled tail
CENTERED_TAIL = {
    'methodId': 'sr:centered_tail', 'key': 'centered_tail', 'displayName': 'Group-centered deviation vs pooled train quantile',
    'kind': 'sr', 'status': 'prototype', 'unit': 'PROCESS_row',
    'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'field': _field_schema(), 'groupBy': {'type': 'string', 'enum': ['adapter_type']},
        'quantile': {'type': 'number', 'exclusiveMinimum': 0, 'exclusiveMaximum': 1},
        'minGroupRows': {'type': 'integer', 'minimum': 1}, 'minPooledRows': {'type': 'integer', 'minimum': 1}}},
    'defaults': {'field': 'duration_ms', 'groupBy': 'adapter_type', 'quantile': 0.99,
                 'minGroupRows': 30, 'minPooledRows': 200},
    'defaultsSource': ('centering = Trainer feature DSL group_centered (value - train group median; unseen group -> missing). '
                       'Pooled P99 of centered values is a prototype proposal so small adapters share one threshold; '
                       'quantile 0.99 copied from research QuantileBook. minGroupRows/minPooledRows are prototype guesses'),
    'inputRequirements': {'fields': ['<field>', 'adapter_type'], 'fitSplit': 'train',
                          'trainFilter': 'status==S reference rows supplied by caller',
                          'minTrainRows': 'minPooledRows overall; minGroupRows per group for the median'},
    'fitStatistics': ['per-group n, median', 'pooled n, threshold = quantile of (x - group median)'],
    'limitations': ['assumes deviations are comparable across groups in absolute field units (ms or count); '
                    'adapters with very different scales dominate the pooled quantile',
                    'no global fallback for the median (Trainer contract), so unseen adapters are unjudged'],
}


def fit_centered_tail(train, params=None):
    p = _merge_params(CENTERED_TAIL['defaults'], params)
    groups, _, used = _fit_groups(train, p['field'], p['groupBy'], p['minGroupRows'],
                                  lambda arr: {'median': float(np.median(arr))})
    values = _numeric(train[p['field']]) if p['field'] in train else pd.Series(np.nan, index=train.index)
    keys = _group_key(train, p['groupBy'])
    medians = keys.map(lambda k: groups.get(k, {}).get('median') if k is not None else None).astype(float)
    centered = (values - medians).dropna().to_numpy(dtype=float)
    pooled = {'n': int(len(centered)), 'sufficient': bool(len(centered) >= p['minPooledRows'])}
    if pooled['sufficient']:
        pooled['threshold'] = float(np.quantile(centered, p['quantile']))
        pooled['mad'] = float(np.median(np.abs(centered - np.median(centered))))
    return {'methodId': CENTERED_TAIL['methodId'], 'version': VERSION, 'params': {**p, 'fallback': 'none'},
            'fitSplit': 'train', 'train': {**_train_summary(train), 'usedRows': used},
            'groups': groups, 'global': {'n': pooled['n'], 'sufficient': False}, 'pooled': pooled}


def score_centered_tail(df, fitted):
    p = fitted['params']
    keys = _group_key(df, p['groupBy'])
    x = _numeric(df[p['field']]) if p['field'] in df else pd.Series(np.nan, index=df.index)
    basis, why, s, fallback_reason = _resolve(keys, fitted, ['median'])   # fallback forced 'none': always None
    pooled = fitted['pooled']
    centered = x - s['median']
    judged = x.notna() & basis.eq('group') & pd.Series(bool(pooled['sufficient']), index=df.index) & centered.notna()
    thr = pooled.get('threshold', np.nan)
    hit = centered > thr
    unjudged_reason = why.copy()
    unjudged_reason.loc[x.isna()] = 'value_missing'
    if not pooled['sufficient']:
        unjudged_reason.loc[:] = 'pooled_support_insufficient'
    basis = basis.where(basis.ne('group'), 'group+pooled')
    return _assemble(df.index, hit.fillna(False), centered, judged, _threshold_reason(judged, hit.fillna(False), unjudged_reason),
                     basis, fallback_reason)


# --------------------------------------------------------------------------- 5. ratio rule
RATIO_RULE = {
    'methodId': 'sr:ratio_rule', 'key': 'ratio_rule', 'displayName': 'Count ratio thresholds (error/total, retry/total)',
    'kind': 'sr', 'status': 'prototype', 'unit': 'PROCESS_row',
    'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'errorRatioMax': {'type': ['number', 'null'], 'minimum': 0},
        'retryRatioMax': {'type': ['number', 'null'], 'minimum': 0},
        'fitQuantile': {'type': 'number', 'exclusiveMinimum': 0, 'exclusiveMaximum': 1},
        'minTrainRows': {'type': 'integer', 'minimum': 1}}},
    'defaults': {'errorRatioMax': 0.0, 'retryRatioMax': None, 'fitQuantile': 0.99, 'minTrainRows': 200},
    'defaultsSource': ('errorRatioMax=0: Trainer synthetic reference requires error_count==0 for S rows '
                       '(synthetic_generation.reference_reason) and research rule success_with_error; '
                       'retryRatioMax=null means fit = train quantile of retry/total (prototype proposal). Thresholds undecided'),
    'inputRequirements': {'fields': ['total_count', 'error_count', 'retry_count'], 'fitSplit': 'train',
                          'trainFilter': 'status==S reference rows supplied by caller', 'minTrainRows': 'minTrainRows for fitted ratios'},
    'fitStatistics': ['train quantile and max of error/total and retry/total (rows with total>0)'],
    'limitations': ['rows with total_count==0 or missing counts are unjudged (ratio undefined)',
                    'partial verdict: when only one threshold (error or retry) is available the row is judged on that '
                    'side alone and the reason carries a ";<side>_unjudged:<cause>" suffix (decision D)',
                    'overlaps HR status semantics for status=F rows; intended as an S-row soft check, HR/SR boundary undecided',
                    'retry_count is per-execution in the ESB extract; ratio to total_count may not be meaningful for every adapter'],
}


def _ratio(df, numerator, denominator):
    num = _numeric(df[numerator]) if numerator in df else pd.Series(np.nan, index=df.index)
    den = _numeric(df[denominator]) if denominator in df else pd.Series(np.nan, index=df.index)
    return (num / den).where(den.gt(0))


def fit_ratio_rule(train, params=None):
    p = _merge_params(RATIO_RULE['defaults'], params)
    stats = {}
    for name, num in (('errorRatio', 'error_count'), ('retryRatio', 'retry_count')):
        ratio = _ratio(train, num, 'total_count').dropna().to_numpy(dtype=float)
        entry = {'n': int(len(ratio)), 'sufficient': bool(len(ratio) >= p['minTrainRows'])}
        if entry['sufficient']:
            entry.update(quantile=float(np.quantile(ratio, p['fitQuantile'])), max=float(ratio.max()))
        stats[name] = entry
    resolved = {}
    for key, name in (('errorRatioMax', 'errorRatio'), ('retryRatioMax', 'retryRatio')):
        if p[key] is not None:
            resolved[key] = {'value': float(p[key]), 'basis': 'parameter'}
        elif stats[name]['sufficient']:
            resolved[key] = {'value': stats[name]['quantile'], 'basis': f"train_quantile_{p['fitQuantile']}"}
        else:
            resolved[key] = {'value': None, 'basis': 'unavailable'}
    return {'methodId': RATIO_RULE['methodId'], 'version': VERSION, 'params': p, 'fitSplit': 'train',
            'train': _train_summary(train), 'trainRatios': stats, 'thresholds': resolved}


def score_ratio_rule(df, fitted):
    """Each side (error, retry) is judged when its threshold exists and its ratio is defined.
    A row is judged if at least one side is (partial verdict, decision D); the unjudged side is
    reported in the reason suffix ``;<side>_unjudged:<threshold_unavailable|value_missing>``.
    Rows with total_count <= 0 / missing stay unjudged (ratio undefined on both sides)."""
    thr = fitted['thresholds']
    error_ratio = _ratio(df, 'error_count', 'total_count')
    retry_ratio = _ratio(df, 'retry_count', 'total_count')
    total = _numeric(df['total_count']) if 'total_count' in df else pd.Series(np.nan, index=df.index)
    e_max, r_max = thr['errorRatioMax']['value'], thr['retryRatioMax']['value']
    e_excess = (error_ratio - e_max) if e_max is not None else pd.Series(np.nan, index=df.index)
    r_excess = (retry_ratio - r_max) if r_max is not None else pd.Series(np.nan, index=df.index)
    e_judged = error_ratio.notna() & (e_max is not None)
    r_judged = retry_ratio.notna() & (r_max is not None)
    judged = e_judged | r_judged
    e_hit, r_hit = e_judged & e_excess.gt(0), r_judged & r_excess.gt(0)
    hit = e_hit | r_hit
    score = pd.concat([e_excess.where(e_judged), r_excess.where(r_judged)], axis=1).max(axis=1, skipna=True)
    reason = pd.Series('within_threshold', index=df.index, dtype=object)
    reason.loc[r_hit] = 'retry_ratio_exceeded'
    reason.loc[e_hit] = 'error_ratio_exceeded'
    reason.loc[e_hit & r_hit] = 'error_and_retry_ratio_exceeded'
    for side, side_judged, available in (('error', e_judged, e_max is not None), ('retry', r_judged, r_max is not None)):
        partial = judged & ~side_judged
        cause = 'threshold_unavailable' if not available else 'value_missing'
        reason.loc[partial] = reason.loc[partial] + f';{side}_unjudged:{cause}'
    total_ok = total.notna() & total.gt(0)
    reason.loc[~judged] = 'value_missing'
    reason.loc[~judged & (e_max is None and r_max is None)] = 'threshold_unavailable'
    reason.loc[~judged & ~total_ok] = 'total_zero_or_missing'
    basis = pd.Series(f"error:{thr['errorRatioMax']['basis']},retry:{thr['retryRatioMax']['basis']}", index=df.index, dtype=object)
    return _assemble(df.index, hit.fillna(False), score, judged, reason, basis)


# --------------------------------------------------------------------------- 6. count conservation
COUNT_CONSERVATION = {
    'methodId': 'sr:count_conservation', 'key': 'count_conservation', 'displayName': 'Count conservation total = success + error',
    'kind': 'sr', 'status': 'prototype', 'unit': 'PROCESS_row',
    'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'tolerance': {'type': 'number', 'minimum': 0}}},
    'defaults': {'tolerance': 0},
    'defaultsSource': ('research-only contract total_count = success_count + error_count (process_anomaly_pipeline rules '
                       'count_conservation; synthetic lab P04). Not confirmed as a customer fault semantic'),
    'inputRequirements': {'fields': ['total_count', 'success_count', 'error_count'], 'fitSplit': 'none',
                          'trainFilter': 'not applicable (formula rule); fit only records train violation count'},
    'fitStatistics': ['train rows checked, train violations (diagnostic only)'],
    'limitations': ['formula rule with no learned statistic: belongs to HR as much as SR; placement is a decision for the catalogue owner',
                    'any missing count leaves the row unjudged'],
}


def _conservation_residual(df):
    cols = {name: (_numeric(df[name]) if name in df else pd.Series(np.nan, index=df.index))
            for name in ('total_count', 'success_count', 'error_count')}
    return cols['total_count'] - (cols['success_count'] + cols['error_count'])


def fit_count_conservation(train, params=None):
    p = _merge_params(COUNT_CONSERVATION['defaults'], params)
    residual = _conservation_residual(train)
    return {'methodId': COUNT_CONSERVATION['methodId'], 'version': VERSION, 'params': p, 'fitSplit': 'none',
            'train': {**_train_summary(train), 'checkedRows': int(residual.notna().sum()),
                      'violations': int(residual.abs().gt(p['tolerance']).sum())}}


def score_count_conservation(df, fitted):
    residual = _conservation_residual(df)
    judged = residual.notna()
    hit = residual.abs() > fitted['params']['tolerance']
    reason = pd.Series('value_missing', index=df.index, dtype=object)
    reason.loc[judged & hit] = 'conservation_violated'
    reason.loc[judged & ~hit] = 'conserved'
    basis = pd.Series('formula', index=df.index, dtype=object)
    return _assemble(df.index, hit.fillna(False), residual, judged, reason, basis)


# --------------------------------------------------------------------------- registry
METHODS = {
    'robust_z': {'meta': ROBUST_Z, 'fit': fit_robust_z, 'score': score_robust_z},
    'iqr_fence': {'meta': IQR_FENCE, 'fit': fit_iqr_fence, 'score': score_iqr_fence},
    'quantile_tail': {'meta': QUANTILE_TAIL, 'fit': fit_quantile_tail, 'score': score_quantile_tail},
    'centered_tail': {'meta': CENTERED_TAIL, 'fit': fit_centered_tail, 'score': score_centered_tail},
    'ratio_rule': {'meta': RATIO_RULE, 'fit': fit_ratio_rule, 'score': score_ratio_rule},
    'count_conservation': {'meta': COUNT_CONSERVATION, 'fit': fit_count_conservation, 'score': score_count_conservation},
}


def catalog():
    return {'catalogVersion': VERSION, 'unit': 'PROCESS_row', 'identifierScheme': 'sr:<key>',
            'interface': {'fit': 'fit(train_df, params) -> fitted',
                          'score': 'score(df, fitted) -> flag/score/reason/basis/fallback_reason',
                          'scoreRow': 'score_row(key, row_dict, fitted) -> dict'},
            'reasonCodes': sorted(REASON_CODES), 'reasonSuffix': REASON_SUFFIX,
            'basisValues': BASIS_VALUES, 'fallbackReasonCodes': sorted(FALLBACK_REASON_CODES),
            'items': [copy.deepcopy(entry['meta']) for entry in METHODS.values()]}


def fit(key, train, params=None):
    return METHODS[key]['fit'](train, params)


def score(key, df, fitted):
    return METHODS[key]['score'](df, fitted)


def score_row(key, row, fitted):
    """Detector-style single observation verdict. `row` is a dict of Trainer raw fields."""
    frame = pd.DataFrame([row])
    out = METHODS[key]['score'](frame, fitted).iloc[0]
    flag, fallback_reason = out['flag'], out['fallback_reason']
    return {'methodId': fitted['methodId'], 'flag': None if pd.isna(flag) else bool(flag),
            'score': None if pd.isna(out['score']) else float(out['score']),
            'reason': str(out['reason']), 'basis': str(out['basis']),
            'fallback_reason': None if fallback_reason is None or pd.isna(fallback_reason) else str(fallback_reason)}
