"""Current-row joint shift hypotheses, normal-trained, development tuning only."""
from itertools import product
import argparse
import numpy as np
from scipy.optimize import differential_evolution
from scipy.special import logsumexp
from ..benchmark import load_split
from ..common import read_json, write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import load_bank
from .scale_shrinkage import ScaleShrunkPoint, learn_prior


def expand(f):
    return np.concatenate([f, np.stack([f[..., i] + f[..., j]
                           for i, j in product(range(3), range(3, 6))], axis=-1)], axis=-1)


def search():
    initialize()
    path = OUT / 'joint_evidence_search.json'
    if path.exists():
        raise FileExistsError('Preserve completed search')
    dev = load_split('development', True)
    y = dev['y']
    groups = metadata_for_split('development', dev['case_id']).scenario.to_numpy()
    observed, available = dev['collection_observed'][:, 672:], dev['latency_available'][:, 672:]
    masks = [(groups == g)[:, None] & ~y & observed for g in np.unique(groups)]
    base = load_bank(OUT / 'models.joblib').point
    config = {**base.config, 'scale_shrinkage': [0., .25], 'normal_scale_prior': learn_prior().tolist()}
    point = ScaleShrunkPoint(base.grid, base.buckets, base.curves, config)
    features = []
    for x, o, a in zip(dev['X'], dev['collection_observed'], dev['latency_available']):
        ref = point.prepare_reference(x[:672], o[:672], a[:672])
        from .preprocessing_search import Context, query_features
        from .ratio import prepare
        profile, residual, slope, midpoint = prepare(x[:672], o[:672], a[:672], .5, .5)
        current = x[672:, :2] - profile[np.arange(336) % 24, :2] - ref.center
        current -= (672 + np.arange(336) - midpoint)[:, None] * slope[:2]
        c = Context(ref.scale, ref.rho, np.zeros((672, 2)), np.ones(671, bool), current)
        f = query_features([c], point.grid, point.buckets, point.curves, np.ones(2), 1.,
                           (ref.scale, ref.rho), o[672:][None, :], a[672:][None, :])[0]
        features.append(f)
    f = expand(np.asarray(features)).reshape(-1, 15)
    effective = np.ones_like(f, bool)
    effective[~available.ravel(), 3:] = False
    records = []
    best_minimum = -np.inf

    def objective(values):
        nonlocal best_minimum
        logw = np.r_[0., values[:14]]
        t = np.exp(values[14])
        fweighted = np.where(effective, f + logw, -np.inf)
        score = (t * logsumexp(fweighted / t, axis=1)).reshape(y.shape)
        threshold = max(float(np.quantile(score[m], .941, method='higher')) for m in masks)
        r = compact_metrics(y, score, threshold, groups)
        row = dict(weights=np.exp(logw).tolist(), temperature=float(t), threshold=threshold,
                   **r['median'], max_fpr=float(r['maximum_scenario_fpr']))
        records.append(row)
        minimum = min(r['median'].values())
        if minimum > best_minimum + 1e-5:
            best_minimum = minimum
            print('Joint same-hour evidence',row,flush=True)
        return -minimum - .02 * row['f1']

    initial = np.r_[np.log(config['weights'][1:]), np.full(9, -4.), np.log(config['temperature'])]
    objective(initial)
    result = differential_evolution(objective, [(-5., 5.)] * 14 + [(-3., 1.)],
                                    seed=20261029, x0=initial, maxiter=20, popsize=5,
                                    polish=False, workers=1, tol=.0005)
    best = max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
    write_json(path, dict(normal_fit_only=True, test_used=False, scale_alpha=[0., .25],
                         best=best, candidates=records, optimizer_message=str(result.message)))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
