"""Development-only conditional normalization of current-hour evidence.

Use the fixed, reviewed normal reference to estimate a group's score scale.
No additional monitoring row is used. This is a sensitivity experiment, not a
claim of finite-sample conformal coverage under dependent time series.
"""
from dataclasses import replace
from itertools import product
import argparse
import numpy as np
from ..benchmark import load_split
from ..common import write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import evidence_score, load_bank
from .ratio import prepare
from .scale_shrinkage import learn_prior


def normal_scores(point, reference, x, o, a):
    _, residual, _, _ = prepare(x, o, a, .5, .5)
    z = (residual[:, :2] - reference.center) / reference.scale
    columns = []
    for field in range(6):
        ch = field // 3
        hi = min(len(point.buckets) - 1, int(np.searchsorted(point.buckets, reference.rho[ch], side='right')))
        lo = max(0, hi - 1)
        f = (reference.rho[ch] - point.buckets[lo]) / max(point.buckets[hi] - point.buckets[lo], 1e-10)
        curve = point.curves[field, lo] * (1 - f) + point.curves[field, hi] * f
        columns.append(np.interp(z[:, ch], point.grid, curve, left=100, right=100))
    features = np.column_stack(columns)
    features[~o] = np.nan
    return evidence_score(features, point.config['weights'], a, point.config['temperature'])


def search():
    initialize()
    path = OUT / 'score_calibration_search.json'
    if path.exists():
        raise FileExistsError('Preserve completed study')
    point = load_bank(OUT / 'models.joblib').point
    prior = learn_prior()
    dev = load_split('development', True)
    groups = metadata_for_split('development', dev['case_id']).scenario.to_numpy()
    y = dev['y']
    observed = dev['collection_observed'][:, 672:]
    normal_masks = [(groups == g)[:, None] & ~y & observed for g in np.unique(groups)]
    records = []
    for alpha in (np.array([1., 1.]), np.array([0., .25])):
        scores, normal = [], []
        for x, o, a in zip(dev['X'], dev['collection_observed'], dev['latency_available']):
            ref = point.prepare_reference(x[:672], o[:672], a[:672])
            ref = replace(ref, scale=np.sqrt(alpha * ref.scale ** 2 + (1 - alpha) * prior ** 2))
            scores.append(point.score_arrays(ref, x[672:], o[672:], a[672:])[0])
            normal.append(normal_scores(point, ref, x[:672], o[:672], a[:672]))
        scores = np.asarray(scores)
        normal = np.asarray(normal)
        for q, strength in product((.9, .94, .97, .99), (.25, .5, .75, 1.)):
            ref_quantile = np.nanquantile(normal, q, axis=1)
            # Positive upper-tail scale, with a conservative floor for flat groups.
            factor = (1 - strength) + strength * np.maximum(ref_quantile / np.nanmedian(ref_quantile), .1)
            score = scores / factor[:, None]
            threshold = max(float(np.quantile(score[m], .941, method='higher')) for m in normal_masks)
            r = compact_metrics(y, score, threshold, groups)
            records.append(dict(scale_alpha=alpha.tolist(), quantile=q, strength=strength,
                threshold=threshold, **r['median'], max_fpr=float(r['maximum_scenario_fpr'])))
        best = max(records, key=lambda r: (min(r[k] for k in ('accuracy','precision','recall','f1')), r['f1']))
        print('Normal-reference score normalization',best,flush=True)
    write_json(path, dict(normal_fit_only=True, test_used=False, best=best, candidates=records))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
