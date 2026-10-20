"""Causal, non-overlapping aggregates and exact shared rolling calculations.

Detector inputs are a whitelist. Ground truth never enters an aggregate or score.
An aggregate endpoint is an evidence timestamp, not its publication timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import numpy as np
import pandas as pd
from scipy.stats import rankdata

MINUTE_NS = 60_000_000_000
METRICS = ('count', 'duration_ms', 'error_count', 'total_count')
INPUTS = ('group', 'bin_start', 'endpoint', 'available_at', *METRICS,
          'source_complete', 'activity_expected', 'category_supported')


@dataclass
class Aggregate:
    group: str
    index: pd.DatetimeIndex
    values: dict
    complete: np.ndarray
    category: np.ndarray
    activity: np.ndarray
    available_at: pd.DatetimeIndex
    minutes: int
    source: pd.DataFrame
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def valid(self):
        return self.complete & self.category


def validate_frame(frame):
    """Return an input-only copy. No implicit zero filling or identity inference."""
    f = frame.copy()
    if 'endpoint' not in f:
        if not isinstance(f.index, pd.DatetimeIndex):
            raise ValueError('endpoint_required')
        f['endpoint'] = f.index
    if 'bin_start' not in f:
        f['bin_start'] = pd.DatetimeIndex(f.endpoint) - pd.Timedelta(minutes=1)
    missing = set(INPUTS) - set(f)
    if missing:
        raise ValueError(f'missing_observable_columns:{sorted(missing)}')
    f = f.loc[:, list(INPUTS)].copy().reset_index(drop=True)
    for key in ('bin_start', 'endpoint', 'available_at'):
        t = pd.DatetimeIndex(f[key])
        if t.tz is None:
            raise ValueError(f'timezone_required:{key}')
        f[key] = t.tz_convert('UTC')
    for group, g in f.groupby('group', sort=False):
        ends = pd.DatetimeIndex(g.endpoint).as_unit('ns').asi8
        if not len(ends) or np.any(np.diff(ends) != MINUTE_NS):
            raise ValueError(f'non_dense_minute_group:{group}')
        if np.any(ends - pd.DatetimeIndex(g.bin_start).as_unit('ns').asi8 != MINUTE_NS):
            raise ValueError('source_bin_not_one_minute')
    for name in ('count', 'error_count', 'total_count'):
        a = f[name].to_numpy(float)
        if np.any(np.isfinite(a) & ((a < 0) | (a != np.floor(a)))):
            raise ValueError(f'invalid_count:{name}')
    if np.any(f.error_count.to_numpy(float) > f.total_count.to_numpy(float)):
        raise ValueError('error_exceeds_total')
    return f


def aggregate(frame, minutes=1, *, start=None, end=None):
    if minutes not in (1, 5, 15):
        raise ValueError('undeclared_aggregate_minutes')
    f = validate_frame(frame)
    output = {}
    for group, raw in f.groupby('group', sort=False):
        raw = raw.reset_index(drop=True)
        ns = pd.DatetimeIndex(raw.endpoint).as_unit('ns').asi8
        # Source [start,end) belongs to the next epoch-aligned Abin endpoint.
        key = ((ns - 1) // (minutes * MINUTE_NS) + 1) * minutes * MINUTE_NS
        blocks = raw.assign(_key=key).groupby('_key', sort=True)
        count = blocks.size()
        values = {}
        for metric in METRICS:
            values[metric] = (blocks[metric].mean() if metric == 'duration_ms'
                              else blocks[metric].sum(min_count=minutes)).to_numpy(float)
        complete = (blocks.source_complete.all() & (count == minutes)).to_numpy(bool)
        category = blocks.category_supported.all().to_numpy(bool)
        activity = blocks.activity_expected.any().to_numpy(bool)
        index = pd.DatetimeIndex(pd.to_datetime(count.index.to_numpy(), utc=True))
        available = pd.DatetimeIndex(blocks.available_at.max())
        a = Aggregate(str(group), index, values, complete, category, activity,
                      available, minutes, raw,
                      pd.Timestamp(start) if start is not None else index[0],
                      pd.Timestamp(end) if end is not None else index[-1] + pd.Timedelta(minutes=1))
        output[str(group)] = a
    return output


def rolling(a, width, operation='mean', *, minimum=None):
    width = int(width)
    if width < 1:
        return np.full(len(a), np.nan)
    minimum = width if minimum is None else int(minimum)
    r = pd.Series(np.asarray(a, float)).rolling(width, min_periods=minimum)
    return getattr(r, operation)().to_numpy(float)


def shift(a, steps, fill=np.nan):
    a = np.asarray(a)
    result = np.full(a.shape, fill, dtype=float)
    if steps == 0:
        return a.copy()
    if steps < len(a):
        result[steps:] = a[:-steps]
    return result


def run_length(mask):
    mask = np.asarray(mask, bool)
    i = np.arange(len(mask))
    return i - np.maximum.accumulate(np.where(mask, -1, i))


def rolling_slope(a, width):
    """OLS slope per bin; stable local x coordinates avoid calendar cancellation."""
    a = np.asarray(a, float)
    n = len(a)
    if width < 2:
        return np.full(n, np.nan)
    finite = np.isfinite(a)
    y = np.where(finite, a, 0.)
    x = np.arange(n, dtype=float)
    sy = rolling(y, width, 'sum')
    sxy = rolling(x*y, width, 'sum') - (x-width+1)*sy
    sx = width*(width-1)/2
    sxx = width*(width-1)*(2*width-1)/6
    result = (width*sxy-sx*sy)/(width*sxx-sx*sx)
    result[rolling(finite.astype(float), width, 'sum') != width] = np.nan
    return result


def pearson(x, y, width, minimum=30):
    return pd.Series(x).rolling(width, min_periods=minimum).corr(pd.Series(y)).to_numpy(float)


def spearman(x, y, width, minimum=30, batch=128):
    """Exact tied-rank correlation, bounded batches; no global-rank approximation."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    out = np.full(len(x), np.nan)
    if width < minimum or width > len(x):
        return out
    xx = np.lib.stride_tricks.sliding_window_view(x, width)
    yy = np.lib.stride_tricks.sliding_window_view(y, width)
    for lo in range(0, len(xx), batch):
        left, right = xx[lo:lo+batch], yy[lo:lo+batch]
        pair = np.isfinite(left) & np.isfinite(right)
        n = pair.sum(axis=1)
        # Invalid pairs sort after every finite value. Masking their tied ranks
        # afterward leaves the exact 1..n finite tied ranks and avoids scipy's
        # per-row missing-value fallback.
        rx = rankdata(np.where(pair, left, np.inf), axis=1)
        ry = rankdata(np.where(pair, right, np.inf), axis=1)
        rx[~pair]=np.nan;ry[~pair]=np.nan
        rx -= (n[:, None]+1)/2
        ry -= (n[:, None]+1)/2
        den = np.sqrt(np.nansum(rx*rx, axis=1)*np.nansum(ry*ry, axis=1))
        r = np.divide(np.nansum(rx*ry, axis=1), den,
                      out=np.full(len(left), np.nan), where=(n >= minimum) & (den > 1e-12))
        out[width-1+lo:width-1+lo+len(left)] = np.clip(r, -1, 1)
    return out


def _quantile_integral(sorted_reference, p):
    """Exact integral from 0 to p of the empirical inverse CDF."""
    reference = np.asarray(sorted_reference, float)
    n = len(reference)
    prefix = np.r_[0., np.cumsum(reference)] / n
    position = np.clip(np.asarray(p)*n, 0, n)
    k = np.minimum(np.floor(position).astype(int), n-1)
    return prefix[k] + (position-k)*reference[k]/n


def distribution_distances(a, width, reference, *, minimum=None, batch=128):
    """Exact two-empirical-CDF KS statistic and Wasserstein-1 distance.

    The reference retains every training value, not quantile-compressed samples.
    The W1 calculation integrates reference quantiles within each recent atom.
    """
    a = np.asarray(a, float)
    reference = np.sort(np.asarray(reference, float))
    reference = reference[np.isfinite(reference)]
    minimum = width if minimum is None else minimum
    ks, distance = np.full(len(a), np.nan), np.full(len(a), np.nan)
    if width > len(a) or width < 1 or not len(reference):
        return ks, distance
    windows = np.lib.stride_tricks.sliding_window_view(a, width)
    m = len(reference)
    for lo in range(0, len(windows), batch):
        z = np.sort(windows[lo:lo+batch], axis=1)
        count = np.isfinite(z).sum(axis=1)
        # Equal finite counts share the same quantile interval grid.
        for n in np.unique(count[count >= minimum]):
            which = np.flatnonzero(count == n)
            v = z[which, :n]
            left = np.arange(n, dtype=float)/n
            right = (np.arange(n, dtype=float)+1)/n
            frleft = np.searchsorted(reference, v, side='left')/m
            frright = np.searchsorted(reference, v, side='right')/m
            kval = np.maximum(np.max(right-frright, axis=1), np.max(frleft-left, axis=1))
            cross = np.clip(frright, left, right)
            integral = (v*(2*cross-left-right) + _quantile_integral(reference, left)
                        + _quantile_integral(reference, right)
                        - 2*_quantile_integral(reference, cross))
            idx = width-1+lo+which
            ks[idx] = np.clip(kval, 0, 1)
            distance[idx] = np.maximum(integral.sum(axis=1), 0)
    return ks, distance


def ewma(a, halflife_bins):
    """One update per new bin; unknown resets, never a zero observation."""
    a = np.asarray(a, float)
    alpha = 1-2**(-1/halflife_bins)
    result = np.full(len(a), np.nan)
    finite = np.isfinite(a)
    boundaries = np.flatnonzero(finite & ~np.r_[False, finite[:-1]])
    ends = np.flatnonzero(finite & ~np.r_[finite[1:], False])+1
    for start, end in zip(boundaries, ends):
        # Prepend the fixed target 0 so the initial state is explicit.
        result[start:end] = pd.Series(np.r_[0., a[start:end]]).ewm(alpha=alpha, adjust=False).mean().to_numpy()[1:]
    return result


def finite_cusum(a, width, k=.5):
    """Exact max suffix sum up to width bins, O(n), reset on unknown."""
    result = np.full(len(a), np.nan)
    queue = deque()
    cumulative = 0.
    run = 0
    for i, value in enumerate(np.asarray(a, float)):
        if not np.isfinite(value):
            queue.clear(); cumulative = 0.; run = 0
            continue
        run += 1
        # Prefix immediately before the current point is an allowed suffix start.
        while queue and queue[-1][1] >= cumulative:
            queue.pop()
        queue.append((i-1, cumulative))
        while queue and queue[0][0] < i-width:
            queue.popleft()
        cumulative += value-k
        if run >= width:
            result[i] = max(0., cumulative-queue[0][1])
    return result


def unbounded_cusum(a, k=.5):
    result = np.full(len(a), np.nan)
    value = 0.
    for i, x in enumerate(np.asarray(a, float)):
        if not np.isfinite(x):
            value = 0.; continue
        value = max(0., value+x-k)
        result[i] = value
    return result


def publish_score(aggregate_group, values, cadence, *, watermark=2, common_minutes=1440):
    """Publish on C grid, causal hold; current unknown invalidates a held output.

    Returned rows include all real minute intervals [tick,tick+1min). The
    availability mask depends on source support, not on a positive prediction.
    """
    a = aggregate_group
    tick = pd.date_range(a.start, a.end, freq='min', inclusive='left')
    ns = tick.as_unit('ns').asi8
    evidence = ((ns-watermark*MINUTE_NS)//(a.minutes*MINUTE_NS))*(a.minutes*MINUTE_NS)
    pos = np.searchsorted(a.index.as_unit('ns').asi8, evidence)
    inside = (pos < len(a.index))
    safe = np.clip(pos, 0, len(a.index)-1)
    inside &= a.index.as_unit('ns').asi8[safe] == evidence
    available = a.available_at.as_unit('ns').asi8[safe] <= ns
    current_valid = inside & available & a.valid[safe]
    # Maximal common source context is an independent quality mask.
    complete_window = rolling(a.valid.astype(float), max(1, common_minutes//a.minutes), 'sum')
    pending_common = rolling(a.available_at.as_unit('ns').asi8.astype(float),max(1,common_minutes//a.minutes),'max')
    common = (current_valid & (complete_window[safe] == max(1, common_minutes//a.minutes))
              & (pending_common[safe] <= ns))
    raw = np.asarray(values, float)
    grid = (ns//MINUTE_NS)%cadence == 0
    native = np.where(grid & common, raw[safe], np.nan)
    origin = np.maximum.accumulate(np.where(grid, np.arange(len(tick)), -1))
    held = np.full(len(tick), np.nan)
    has = origin >= 0
    held[has] = native[origin[has]]
    held[~common] = np.nan
    # A one-minute failure inside A's hold period is not hidden by a prior Abin.
    source = a.source
    source_end = pd.DatetimeIndex(source.endpoint).as_unit('ns').asi8
    minute_pos = np.searchsorted(source_end, ns-watermark*MINUTE_NS)
    safe_minute = np.clip(minute_pos, 0, len(source)-1)
    minute_known = ((minute_pos < len(source)) & (source_end[safe_minute] == ns-watermark*MINUTE_NS)
                    & source.source_complete.to_numpy(bool)[safe_minute]
                    & source.category_supported.to_numpy(bool)[safe_minute]
                    & (pd.DatetimeIndex(source.available_at).as_unit('ns').asi8[safe_minute] <= ns))
    minute_window=max(1,common_minutes)
    minute_valid=source.source_complete.to_numpy(bool)&source.category_supported.to_numpy(bool)
    minute_complete=rolling(minute_valid.astype(float),minute_window,'sum')
    minute_pending=rolling(pd.DatetimeIndex(source.available_at).as_unit('ns').asi8.astype(float),minute_window,'max')
    minute_common=(minute_known&(minute_complete[safe_minute]==minute_window)
                   &(minute_pending[safe_minute]<=ns))
    held[~minute_common] = np.nan
    return pd.DataFrame({'group': a.group, 'decision_tick': tick, 'score': held,
                         'source_eligible': minute_known, 'common_eligible': minute_common,
                         'score_supported': np.isfinite(held),
                         'on_grid': grid, 'evidence_end': pd.to_datetime(evidence, utc=True),
                         'last_observation_end': pd.to_datetime(evidence, utc=True),
                         'source_age_minutes': (ns-evidence)/MINUTE_NS,
                         'next_expected_bin': pd.to_datetime(evidence+a.minutes*MINUTE_NS, utc=True)})
