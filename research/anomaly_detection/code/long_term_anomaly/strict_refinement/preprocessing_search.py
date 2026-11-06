"""Development-only normal profile / parameter shrinkage sensitivity study.

The already reported frozen artifact is never overwritten. All distribution
estimates use the first 672 normal training hours only. No monitoring history
or test cases enter this search. A one-hour query is evaluated independently.
"""
from dataclasses import dataclass
from itertools import product
import argparse
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import lfilter
from ..benchmark import load_split
from ..common import read_json, write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import evidence_score
from .ratio import prepare


@dataclass
class Context:
    scale: np.ndarray
    rho: np.ndarray
    z: np.ndarray
    pair: np.ndarray
    current_residual: np.ndarray


def context(x, observed, available, seasonal, trend):
    profile, residual, slope, midpoint = prepare(
        x[:672], observed[:672], available[:672], seasonal, trend)
    center = np.mean(residual[:, :2], axis=0)
    scale = np.maximum(np.std(residual[:, :2], axis=0), 1e-4)
    z = (residual[:, :2] - center) / scale
    good = observed[:672] & available[:672]
    pair = good[:-1] & good[1:]
    rho = np.array([np.clip(np.corrcoef(z[:-1, ch][pair], z[1:, ch][pair])[0, 1], 0, .95)
                    for ch in range(2)])
    hours = np.arange(len(x) - 672)
    current = (x[672:, :2] - profile[hours % 24, :2] - center
               - (672 + hours - midpoint)[:, None] * slope[:2])
    return Context(scale, rho, z, pair, current)


def normal_curves(contexts, bandwidth):
    innovations = []
    for c in contexts:
        e = (c.z[1:] - c.rho * c.z[:-1]) / np.sqrt(1 - c.rho ** 2)
        innovations.append(e[c.pair])
    pool = np.concatenate(innovations)
    pool = (pool - pool.mean(0)) / pool.std(0)
    grid = np.linspace(-12, 12, 4097)
    step = grid[1] - grid[0]
    edges = np.r_[grid - step / 2, grid[-1] + step / 2]
    buckets = np.arange(0, .951, .05)
    rng = np.random.default_rng(20261028)
    density = np.empty((len(buckets), 2, len(grid)))
    for i, rho in enumerate(buckets):
        for ch in range(2):
            eps = pool[rng.integers(len(pool), size=160256), ch]
            samples = lfilter([np.sqrt(1 - rho * rho)], [1, -rho], eps)[256:]
            counts, _ = np.histogram(samples, bins=edges)
            density[i, ch] = counts / max(counts.sum(), 1) / step
    curves = {}
    for bw in bandwidth:
        tables = np.empty((6, len(buckets), len(grid)))
        for i in range(len(buckets)):
            for ch in range(2):
                p = gaussian_filter1d(density[i, ch], bw / step, mode='constant')
                p /= p.sum() * step
                cdf = np.cumsum(p) * step
                up = (np.interp(grid - 1, grid, cdf, left=0, right=1)
                      - np.interp(grid - 3, grid, cdf, left=0, right=1)) / 2
                down = (np.interp(grid + 3, grid, cdf, left=0, right=1)
                        - np.interp(grid + 1, grid, cdf, left=0, right=1)) / 2
                broad = np.mean([gaussian_filter1d(p, s / step, mode='constant')
                                 for s in (.5, 1., 2.)], axis=0)
                tables[ch * 3:ch * 3 + 3, i] = np.clip(
                    np.log(np.maximum([up, down, broad], 1e-100))
                    - np.log(np.maximum(p, 1e-100)), -100, 100)
        curves[bw] = tables
    return grid, buckets, curves


def query_features(contexts, grid, buckets, curves, scale_alpha, rho_alpha, priors, observed, available):
    rows = []
    for c in contexts:
        scale = np.sqrt(scale_alpha * c.scale ** 2 + (1 - scale_alpha) * priors[0] ** 2)
        rho = rho_alpha * c.rho + (1 - rho_alpha) * priors[1]
        z = c.current_residual / scale
        columns = []
        for field in range(6):
            ch = field // 3
            hi = min(len(buckets) - 1, int(np.searchsorted(buckets, rho[ch], side='right')))
            lo = max(0, hi - 1)
            f = (rho[ch] - buckets[lo]) / max(buckets[hi] - buckets[lo], 1e-10)
            curve = curves[field, lo] * (1 - f) + curves[field, hi] * f
            columns.append(np.interp(z[:, ch], grid, curve, left=100, right=100))
        rows.append(np.column_stack(columns))
    features = np.asarray(rows)
    features[:, :, 3:] = np.where(available[:, :, None], features[:, :, 3:], 0)
    features[~observed] = np.nan
    return features


def search():
    initialize()
    out = OUT / 'preprocessing_search.json'
    if out.exists():
        raise FileExistsError('Preserve the completed sensitivity study')
    train = load_split('train', False)
    dev = load_split('development', True)
    groups = metadata_for_split('development', dev['case_id']).scenario.to_numpy()
    y = dev['y']
    observed, available = dev['collection_observed'][:, 672:], dev['latency_available'][:, 672:]
    normals = [(groups == g)[:, None] & ~y & observed for g in np.unique(groups)]
    config = read_json(OUT / 'calibration.json')['record']['one_hour_config']
    records = []
    for seasonal, trend in product((0., .5, 1.), (0., .5, 1.)):
        normal = [context(x[:672], o[:672], a[:672], seasonal, trend)
                  for x, o, a in zip(train['X'], train['collection_observed'], train['latency_available'])]
        current = [context(x, o, a, seasonal, trend)
                   for x, o, a in zip(dev['X'], dev['collection_observed'], dev['latency_available'])]
        priors = (np.median([c.scale for c in normal], axis=0), np.median([c.rho for c in normal], axis=0))
        grid, buckets, tables = normal_curves(normal, (.03, .07))
        for bw, req_a, dur_a, rho_a in product((.03, .07), (0., 1.), (.25, 1.), (0., .5, 1.)):
            alpha = np.array([req_a, dur_a])
            features = query_features(current, grid, buckets, tables[bw], alpha, rho_a, priors, observed, available)
            score = evidence_score(features.reshape(-1, 6), config['weights'], available.ravel(),
                                   config['temperature']).reshape(y.shape)
            threshold = max(float(np.quantile(score[m], .941, method='higher')) for m in normals)
            metrics = compact_metrics(y, score, threshold, groups)
            records.append(dict(seasonal_strength=seasonal, trend_strength=trend, bandwidth=bw,
                                scale_alpha=alpha.tolist(), rho_alpha=rho_a,
                                normal_scale_prior=priors[0].tolist(), normal_rho_prior=priors[1].tolist(),
                                threshold=threshold, **metrics['median'],
                                max_fpr=float(metrics['maximum_scenario_fpr'])))
        best = max(records, key=lambda r: (min(r[k] for k in ('accuracy', 'precision', 'recall', 'f1')), r['f1']))
        print('Profile/parameter sensitivity',seasonal,trend,'best',best,flush=True)
        write_json(out, dict(normal_fit_only=True, test_used=False, best=best, candidates=records))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
