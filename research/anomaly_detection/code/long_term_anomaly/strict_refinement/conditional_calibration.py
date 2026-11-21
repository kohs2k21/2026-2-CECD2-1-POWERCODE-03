"""Normal-only conditional threshold regression, development selection only.

Learn expected normal upper-tail scores from normal training trajectories.
Only reviewed-reference covariates enter the threshold regressor; no current
query, monitoring lag, case ID, task, or benchmark generation parameter does.
"""
from dataclasses import replace
from pathlib import Path
import argparse
import hashlib
import joblib
import numpy as np
from scipy.stats import skew
from sklearn.ensemble import HistGradientBoostingRegressor
from ..benchmark import load_split
from ..common import DATA, write_json, sha256
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import load_bank
from .ratio import prepare
from .scale_shrinkage import learn_prior


def reference_covariates(point,x,o,a):
    ref=point.prepare_reference(x[:672],o[:672],a[:672])
    profile,residual,slope,_=prepare(x[:672],o[:672],a[:672],.5,.5)
    z=(residual[:,:2]-ref.center)/ref.scale
    q=np.quantile(z,[.01,.05,.5,.95,.99],axis=0).ravel()
    features=np.r_[q,ref.rho,ref.scale,skew(z),np.mean(z**4,axis=0),
                   slope[:2]/ref.scale*24,np.ptp(profile[:,:2],axis=0)/ref.scale]
    return ref,features


def features(split,scale_mode):
    signature=hashlib.sha256((sha256(__file__)+sha256(OUT/'models.joblib')+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'conditional_calibration_{split}_{scale_mode}_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return {k:z[k] for k in z.files}
    d=load_split(split,False);point=load_bank(OUT/'models.joblib').point;prior=learn_prior()
    summaries=[];scores=[]
    for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']):
        ref,f=reference_covariates(point,x,o,a)
        if scale_mode:
            ref=replace(ref,scale=np.sqrt(np.array([0.,.25])*ref.scale**2+np.array([1.,.75])*prior**2))
        score=point.score_arrays(ref,x[672:],o[672:],a[672:])[0]
        summaries.append(f);scores.append(score)
    result=dict(reference_features=np.asarray(summaries),score=np.asarray(scores))
    np.savez_compressed(path,**result);return result


def search():
    initialize();path=OUT/'conditional_calibration_search.json'
    if path.exists():raise FileExistsError('Preserve completed experiment')
    d=load_split('development',True);y=d['y'];valid=d['collection_observed'][:,672:]
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    masks=[(groups==g)[:,None]&~y&valid for g in np.unique(groups)]
    rows=[]
    for scale_mode in (0,1):
        train,cal,dev=(features(split,scale_mode) for split in ('train','calibration','development'))
        target=train['score'].ravel();keep=np.isfinite(target)
        x=np.repeat(train['reference_features'],336,axis=0)[keep]
        for leaves,min_leaf,iterations in ((3,2000,80),(7,2000,80),(7,1000,160),(15,1000,80)):
            model=HistGradientBoostingRegressor(loss='quantile',quantile=.941,max_leaf_nodes=leaves,
                min_samples_leaf=min_leaf,max_iter=iterations,learning_rate=.06,l2_regularization=100.,
                early_stopping=False,random_state=20261103)
            model.fit(x,target[keep])
            train_factor=np.maximum(model.predict(train['reference_features']),.2)
            cal_factor=np.maximum(model.predict(cal['reference_features']),.2)
            dev_factor=np.maximum(model.predict(dev['reference_features']),.2)
            center=np.median(train_factor)
            for strength in (.25,.5,.75,1.):
                cs=cal['score']/(cal_factor[:,None]/center)**strength
                score=dev['score']/(dev_factor[:,None]/center)**strength
                threshold=max(float(np.nanquantile(cs,.941)),*(float(np.quantile(score[m],.941,method='higher')) for m in masks))
                r=compact_metrics(y,score,threshold,groups)
                row=dict(scale_mode=scale_mode,leaves=leaves,min_leaf=min_leaf,iterations=iterations,
                    strength=strength,normal_factor_center=float(center),threshold=threshold,
                    **r['median'],max_fpr=float(r['maximum_scenario_fpr']))
                rows.append(row)
            joblib.dump(model,OUT/f'conditional_calibration_candidate_{scale_mode}_{leaves}_{min_leaf}_{iterations}.joblib',compress=3)
        best=max(rows,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
        print('Normal-only conditional upper-tail calibration',scale_mode,'best',best,flush=True)
    write_json(path,dict(normal_fit_only=True,benchmark_anomaly_labels_used_for_fit=False,
        development_labels_used_for_selection=True,test_used=False,monitoring_hours_used=1,
        regressor_inputs='only normal-reference quantiles, autocorrelation, scales, skew, kurtosis, slope and phase range',
        normal_training_cases=64,normal_reference_hours=672,normal_training_query_hours=336,
        source_sha256=sha256(__file__),best=best,candidates=rows))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
