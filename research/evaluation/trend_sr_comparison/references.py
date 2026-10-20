"""Train-only frozen normal references; explicit bucket fallback and scales."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import numpy as np
import pandas as pd

from .features import aggregate, rolling


def statistics(values):
    values = np.sort(np.asarray(values, float))
    values = values[np.isfinite(values)]
    if not len(values):
        return {'status': 'unsupported', 'reason': 'no_finite_reference', 'n': 0}
    q = np.quantile(values, [.05, .1, .25, .5, .75, .9, .95])
    return {'status': 'ok', 'n': len(values), 'values': values,
            'mean': float(values.mean()), 'std': float(values.std(ddof=1)) if len(values)>1 else 0.,
            'median': float(q[3]), 'mad': float(np.median(np.abs(values-q[3]))),
            'q05': float(q[0]), 'q10': float(q[1]), 'q25': float(q[2]),
            'q75': float(q[4]), 'q90': float(q[5]), 'q95': float(q[6]),
            'iqr': float(q[4]-q[2]), 'range90': float(q[5]-q[1])}


def scaled_distance(value, center, scale):
    """A point mass is a stated discrete reference, never a universal epsilon."""
    difference = np.asarray(value, float)-center
    if np.ndim(scale) == 0:
        if scale > 0:
            return np.abs(difference)/scale
        return np.where(np.isnan(difference), np.nan, np.where(difference == 0, 0., 1.))
    return np.divide(np.abs(difference), scale, out=np.where(difference == 0, 0., 1.), where=scale>0)


def signed_standardize(value, center, scale):
    difference = np.asarray(value, float)-center
    # A declared point mass uses a signed discrete departure; equality is a
    # supported zero, while missing reference/data remain missing.
    out=np.sign(difference)
    return np.divide(difference, scale, out=out, where=np.asarray(scale)>0)


def bucket_key(index, kind):
    index = pd.DatetimeIndex(index).tz_convert('Asia/Seoul')-pd.Timedelta(minutes=1)
    if kind == 'B0':
        return np.zeros(len(index), int)
    if kind == 'B1':
        return index.hour.to_numpy()+24*(index.dayofweek.to_numpy() >= 5)
    if kind == 'B2':
        return index.hour.to_numpy()+24*index.dayofweek.to_numpy()
    raise ValueError('undeclared_reference_kind')


@dataclass
class NormalReference:
    group: str
    aggregation: int
    kind: str
    metrics: dict
    bucket_map: dict
    fit_manifest: dict
    relation: dict

    def lookup(self, a, metric, statistic='median'):
        keys = bucket_key(a.index, self.kind)
        out = np.full(len(a.index), np.nan)
        for key in np.unique(keys):
            table = self.metrics[metric].get(self.bucket_map[metric].get(int(key), 'pooled'))
            if table and table['status'] == 'ok':
                out[keys == key] = table[statistic]
        return out

    def residuals(self, a):
        out = {}
        for metric in ('count', 'duration_ms'):
            center = self.lookup(a, metric, 'mean')
            scale = self.lookup(a, metric, 'std')
            values = a.values[metric].copy()
            values[~a.valid] = np.nan
            out[metric] = signed_standardize(values, center, scale)
        return out


def fit_normal(aggregates, kind='B1', *, split='train', minimum=30, minimum_days=4):
    if split != 'train':
        raise ValueError('normal_reference_train_only')
    result = {}
    for group, a in aggregates.items():
        inside = (a.index >= a.start) & (a.index < a.end)
        context=rolling(a.valid.astype(float),max(1,1440//a.minutes),'sum')==max(1,1440//a.minutes)
        fitmask = a.valid & inside & context & (a.available_at <= a.end)
        tables, maps, counts = {}, {}, {}
        for metric in ('count', 'duration_ms', 'error_rate'):
            if metric == 'error_rate':
                v = np.divide(a.values['error_count'], a.values['total_count'],
                              out=np.full(len(a.index), np.nan), where=a.values['total_count']>0)
            else:
                v = a.values[metric]
            valid = fitmask & np.isfinite(v)
            pooled = statistics(v[valid])
            tables[metric] = {'pooled': pooled}
            mapping = {}
            levels = ('B0',) if kind == 'B0' else ('B1', 'B0') if kind == 'B1' else ('B2', 'B1', 'B0')
            all_keys = range({'B0': 1, 'B1': 48, 'B2': 168}[kind])
            for key in all_keys:
                # Map a target calendar bucket to the most specific supported train bucket.
                for level in levels:
                    mapped = 0 if level == 'B0' else ((key % 24)+24*((key//24)>=5) if level=='B1' and kind=='B2' else key)
                    if level == 'B0':
                        mask = valid
                        name = 'pooled'
                    else:
                        mask = valid & (bucket_key(a.index, level) == mapped)
                        name = f'{level}:{mapped}'
                    days = len(np.unique(a.index[mask].normalize()))
                    if mask.sum() >= minimum and days >= minimum_days:
                        if name not in tables[metric]:
                            tables[metric][name] = statistics(v[mask])
                        mapping[int(key)] = name
                        counts[f'{metric}:{key}'] = {'level': level, 'n': int(mask.sum()), 'days': days}
                        break
                else:
                    mapping[int(key)] = 'unavailable'
            maps[metric] = mapping
        ref = NormalReference(group, a.minutes, kind, tables, maps,
                              {'split': 'train', 'rows': int(fitmask.sum()), 'buckets': counts,
                               'first': str(a.index[fitmask].min()), 'last': str(a.index[fitmask].max()),
                               'input_columns': ['count','duration_ms','error_count','total_count',
                                                 'source_complete','category_supported'],
                               'minimum_rows': minimum, 'minimum_days': minimum_days}, {})
        residual = ref.residuals(a)
        x, y = residual['count'], residual['duration_ms']
        pair = fitmask & np.isfinite(x) & np.isfinite(y) & (a.values['count']>0)
        if pair.sum() >= minimum and np.ptp(x[pair]) > 0:
            design = np.column_stack([np.ones(pair.sum()), x[pair]])
            coefficients = np.linalg.lstsq(design, y[pair], rcond=None)[0]
            r = y[pair]-design@coefficients
            ref.relation = {'status':'ok','intercept':float(coefficients[0]), 'beta':float(coefficients[1]),
                            'scale':statistics(r),'n':int(pair.sum())}
        else:
            ref.relation = {'status':'unsupported','reason':'insufficient_or_constant_train_pairs'}
        result[group] = ref
    return result


def fingerprint(reference):
    """Stable ndarray-aware digest, used for frozen restoration checks."""
    def convert(value):
        if isinstance(value, np.ndarray):
            return {'shape':value.shape,'dtype':str(value.dtype),'sha256':hashlib.sha256(value.tobytes()).hexdigest()}
        if hasattr(value, '__dict__'):
            return {k:convert(v) for k,v in value.__dict__.items()}
        if isinstance(value, dict):
            return {str(k):convert(v) for k,v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        if isinstance(value, np.generic):
            return value.item()
        return value
    return hashlib.sha256(json.dumps(convert(reference), sort_keys=True, allow_nan=False).encode()).hexdigest()
