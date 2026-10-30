"""Normal-only conditional empirical background sensitivity experiment.

Unlike the AR simulation, use pooled normal residual histograms directly,
weighted by reference autocorrelation. All normal rows are training references;
development truth selects hyperparameters, and test is never loaded here.
"""
from itertools import product
import argparse
import numpy as np
from scipy.ndimage import gaussian_filter1d
from ..benchmark import load_split
from ..common import read_json, write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import evidence_score
from .preprocessing_search import context, query_features


def fit_background(normal):
    grid = np.linspace(-12, 12, 4097)
    step = grid[1] - grid[0]
    edges = np.r_[grid - step / 2, grid[-1] + step / 2]
    hist = np.empty((len(normal), 2, len(grid)))
    for i, ref in enumerate(normal):
        for ch in range(2):
            counts, _ = np.histogram(ref.z[:, ch], bins=edges)
            hist[i, ch] = counts / max(counts.sum(), 1) / step
    return grid, hist, np.asarray([ref.rho for ref in normal])


def conditional_curves(grid, hist, rho, rho_bw, bw):
    step = grid[1] - grid[0]
    buckets = np.arange(0, .951, .05)
    tables = np.empty((6, len(buckets), len(grid)))
    for i, r in enumerate(buckets):
        for ch in range(2):
            weights = np.exp(-.5 * ((rho[:, ch] - r) / rho_bw) ** 2)
            weights /= weights.sum()
            p = gaussian_filter1d(weights @ hist[:, ch], bw / step, mode='constant')
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
    return buckets, tables


def search():
    initialize()
    path = OUT / 'empirical_background_search.json'
    if path.exists():
        raise FileExistsError('Preserve completed study')
    train = load_split('train', False)
    dev = load_split('development', True)
    groups = metadata_for_split('development', dev['case_id']).scenario.to_numpy()
    y = dev['y']
    observed, available = dev['collection_observed'][:, 672:], dev['latency_available'][:, 672:]
    masks = [(groups == g)[:, None] & ~y & observed for g in np.unique(groups)]
    normal = [context(x[:672], o[:672], a[:672], .5, .5)
              for x, o, a in zip(train['X'], train['collection_observed'], train['latency_available'])]
    current = [context(x, o, a, .5, .5)
               for x, o, a in zip(dev['X'], dev['collection_observed'], dev['latency_available'])]
    priors = (np.median([c.scale for c in normal], axis=0), np.median([c.rho for c in normal], axis=0))
    grid, hist, rho = fit_background(normal)
    config = read_json(OUT / 'calibration.json')['record']['one_hour_config']
    records = []
    for rho_bw, bw in product((.03, .07, .15, .3, 1.), (.03, .07, .15)):
        buckets, tables = conditional_curves(grid, hist, rho, rho_bw, bw)
        for alpha in (np.array([1., 1.]), np.array([0., .25])):
            features = query_features(current, grid, buckets, tables, alpha, 1., priors, observed, available)
            score = evidence_score(features.reshape(-1, 6), config['weights'], available.ravel(),
                                   config['temperature']).reshape(y.shape)
            threshold = max(float(np.quantile(score[m], .941, method='higher')) for m in masks)
            r = compact_metrics(y, score, threshold, groups)
            records.append(dict(rho_bandwidth=rho_bw, bandwidth=bw, scale_alpha=alpha.tolist(),
                                threshold=threshold, **r['median'], max_fpr=float(r['maximum_scenario_fpr'])))
        best = max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
        print('Empirical normal background',rho_bw,bw,'best',best,flush=True)
    write_json(path, dict(normal_fit_only=True, test_used=False, best=best, candidates=records))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
