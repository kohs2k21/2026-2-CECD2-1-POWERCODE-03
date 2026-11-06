"""Development-only supervised diagnostic, NOT an unsupervised model candidate.

Keep matched trajectories with identical normal references in the same fold.
No test inputs or truth are opened. Its ROC operating points use held-out labels
for diagnosis only; they are not a calibrated deployment performance estimate.
"""
import argparse
import hashlib
import numpy as np
from scipy.stats import skew
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold
from ..benchmark import load_split
from ..common import write_json, sha256
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import load_bank
from .preprocessing_search import context
from .ratio import prepare


def features_for(point, x, observed, available):
    ref = point.prepare_reference(x[:672], observed[:672], available[:672])
    c = context(x, observed, available, .5, .5)
    z = c.current_residual / ref.scale
    fields = []
    for j in range(6):
        ch = j // 3
        hi = min(len(point.buckets)-1, int(np.searchsorted(point.buckets, ref.rho[ch], side='right')))
        lo = max(hi-1, 0)
        w = (ref.rho[ch]-point.buckets[lo])/max(point.buckets[hi]-point.buckets[lo],1e-10)
        curve = point.curves[j,lo]*(1-w)+point.curves[j,hi]*w
        fields.append(np.interp(z[:,ch],point.grid,curve,left=100,right=100))
    q = np.quantile(c.z, [.01,.05,.25,.5,.75,.95,.99], axis=0).ravel()
    summary = np.r_[q, ref.rho, ref.scale, skew(c.z), np.mean(c.z**4,axis=0)]
    normal = c.z
    ranks = np.column_stack([(np.searchsorted(np.sort(normal[:,j]),z[:,j])+.5)/(len(normal)+1)
                             for j in range(2)])
    f = np.column_stack([z, *fields, ranks, np.broadcast_to(summary,(len(z),len(summary)))])
    f[~available[672:],1] = np.nan
    f[~available[672:],5:8] = np.nan
    f[~available[672:],9] = np.nan
    f[~observed[672:]] = np.nan
    return f


def main():
    initialize()
    path = OUT / 'point_supervised_diagnostic.json'
    if path.exists():
        raise FileExistsError('Preserve the completed diagnostic')
    dev = load_split('development',True)
    point = load_bank(OUT/'models.joblib').point
    features = np.asarray([features_for(point,x,o,a)
        for x,o,a in zip(dev['X'],dev['collection_observed'],dev['latency_available'])])
    folds = np.array([hashlib.sha256(x[:672].tobytes()).hexdigest() for x in dev['X']])
    # A pair can differ in NaN payload/dtype on some export paths. The reviewed
    # reference is canonicalized, and exact reference duplicates are grouped.
    canonical = np.array([hashlib.sha256(np.nan_to_num(x[:672],nan=-1e30).astype('<f8').tobytes()).hexdigest()
                          for x in dev['X']])
    assert len(np.unique(folds))==len(np.unique(canonical))
    groups = metadata_for_split('development',dev['case_id']).scenario.to_numpy()
    y=dev['y']; observed=dev['collection_observed'][:,672:]
    rows=[]
    for leaves, iterations in ((15,160),(31,240)):
        scores = np.full(y.shape,np.nan)
        for fold,(train,valid) in enumerate(GroupKFold(4).split(features,y,canonical)):
            assert not set(canonical[train]) & set(canonical[valid])
            xtrain=features[train].reshape(-1,features.shape[-1])
            target=y[train].ravel(); keep=observed[train].ravel()
            model=HistGradientBoostingClassifier(max_leaf_nodes=leaves,max_iter=iterations,
                learning_rate=.06,min_samples_leaf=120,l2_regularization=50.,
                early_stopping=False,random_state=20261030)
            model.fit(xtrain[keep],target[keep])
            pred=model.predict_proba(features[valid].reshape(-1,features.shape[-1]))[:,1]
            scores[valid]=pred.reshape(y[valid].shape)
            print('Supervised diagnostic only',leaves,'fold',fold+1,flush=True)
        scores[~observed]=np.nan
        threshold=max(float(np.quantile(scores[(groups==g)[:,None]&~y&observed],.941,method='higher'))
                      for g in np.unique(groups))
        r=compact_metrics(y,scores,threshold,groups)
        row=dict(leaves=leaves,iterations=iterations,roc_operating_point=threshold,
                 **r['median'],max_fpr=float(r['maximum_scenario_fpr']),scenarios=r['scenarios'])
        rows.append(row);print('Development group-CV diagnostic',row,flush=True)
    write_json(path,dict(diagnostic_only=True,not_an_unsupervised_model=True,
        not_deployment_metrics=True,development_labels_used_for_diagnostic_fitting=True,
        test_used=False,monitoring_hours_per_decision=1,duplicate_reference_groups=len(np.unique(canonical)),
        fold_count=4,candidates=rows,source_sha256=sha256(__file__)))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();main()
