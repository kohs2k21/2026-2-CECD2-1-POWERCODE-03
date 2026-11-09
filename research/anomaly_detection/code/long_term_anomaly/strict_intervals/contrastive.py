"""Normal-only, self-supervised noise discrimination for one-hour inputs.

Inspired by the data-vs-noise discrimination principle of NCE (Gutmann &
Hyvarinen, AISTATS 2010), not a reproduction of its exact likelihood model.
Synthetic training noise is not benchmark truth. Test data are never loaded.
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from ..benchmark import load_split
from ..common import DATA,write_json,sha256
from ..experiment import metadata_for_split
from .explore import OUT,reference_distributions,point_features
from .moments import compact_metrics


def transform(ref,r,observed,latency):
    f=point_features(ref,r,observed,latency,.99)
    center=np.median(ref[:,:2],axis=0)
    scale=np.maximum(np.std(ref[:,:2],axis=0),1e-5)
    z=(r[:,:2]-center)/scale
    quantiles=(np.quantile(ref[:,:2],[.01,.05,.1,.9,.95,.99],axis=0)-center)/scale
    rho=np.asarray([np.corrcoef(ref[:-1,j],ref[1:,j])[0,1] for j in range(2)])
    cdf=np.column_stack([(np.searchsorted(np.sort(ref[:,j]),r[:,j])+.5)/(len(ref)+1) for j in range(2)])
    values=np.column_stack([f,z,cdf,np.broadcast_to(quantiles.ravel(),(len(r),12)),np.broadcast_to(rho,(len(r),2))])
    values[~latency,2:4]=0.;values[~latency,5]=0.;values[~latency,7]=0.;values[~latency,9]=.5
    values[~observed]=np.nan
    return values


def worker(payload):
    x,o,a,augment,seed=payload
    forecast,ref=reference_distributions(x[:672],o[:672],a[:672],'phase_median')
    r=x[672:] - forecast[np.arange(336)%24]
    if not augment:return transform(ref,r,o[672:],a[672:])
    rng=np.random.default_rng(seed)
    features=[transform(ref,r,o[672:],a[672:])];labels=[np.zeros(336,dtype=int)]
    sigma=np.std(ref,axis=0)
    for replica in range(4):
        z=x[672:].astype(float).copy()
        channel=rng.integers(0,2,len(z))
        amount=rng.uniform(.5,4.,len(z))*rng.choice([-1.,1.],len(z))
        z[np.arange(len(z)),channel]+=amount*sigma[channel]
        z=np.maximum(z,0.)
        z[:,0]=np.log1p(np.rint(np.expm1(np.minimum(z[:,0],20))))
        latency=a[672:] & (z[:,0]>0)
        r_noise=z-forecast[np.arange(336)%24]
        features.append(transform(ref,r_noise,o[672:],latency))
        labels.append(np.ones(len(z),dtype=int))
    return np.concatenate(features),np.concatenate(labels)


def bank(split,augment=False):
    d=load_split(split,False)
    sig=hashlib.sha256((sha256(Path(__file__))+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'contrastive_{split}_{augment}_{sig}.npz'
    if path.exists():
        with np.load(path) as f:return {k:f[k] for k in f.files},d
    payloads=((x,o,a,augment,20261019+i) for i,(x,o,a) in enumerate(zip(d['X'],d['collection_observed'],d['latency_available'])))
    with ProcessPoolExecutor(max_workers=min(6,os.cpu_count() or 2)) as p:
        result=list(p.map(worker,payloads,chunksize=4))
    if augment:
        features=np.concatenate([r[0] for r in result]);labels=np.concatenate([r[1] for r in result])
        valid=np.isfinite(features).all(axis=1)
        result={'features':features[valid],'labels':labels[valid]}
    else:result={'features':np.asarray(result)}
    np.savez_compressed(path,**result)
    print('Contrastive features',split,augment,result['features'].shape,flush=True)
    return result,d


def search():
    train,_=bank('train',True)
    cal,_=bank('calibration')
    dev,d=bank('development')
    y=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    valid=np.isfinite(dev['features']).all(axis=2)
    cal_valid=np.isfinite(cal['features']).all(axis=2)
    rows=[]
    for depth,regularization,iterations in ((3,10.,160),(5,10.,160),(7,20.,160)):
        model=HistGradientBoostingClassifier(max_depth=depth,max_iter=iterations,max_leaf_nodes=31,
            min_samples_leaf=50,l2_regularization=regularization,learning_rate=.08,early_stopping=False,random_state=20261019)
        model.fit(train['features'],train['labels'])
        s=model.predict_proba(np.nan_to_num(dev['features'].reshape(-1,24),nan=0.))[:,1].reshape(y.shape)
        s[~valid]=np.nan
        cs=model.predict_proba(cal['features'][cal_valid])[:,1]
        t=max(np.quantile(cs,.945),*(np.quantile(s[(groups==g)[:,None]&~y&valid],.945,method='higher') for g in np.unique(groups)))
        r=compact_metrics(y,s,t,groups)
        row=dict(depth=depth,l2=regularization,iterations=iterations,threshold=float(t),**r['median'],max_fpr=float(r['maximum_scenario_fpr']),scenarios=r['scenarios'])
        rows.append(row)
        joblib.dump(model,OUT/f'contrastive_candidate_depth{depth}.joblib',compress=3)
        print('Normal-only noise discrimination',row,flush=True)
    write_json(OUT/'contrastive_development_search.json',{'train_input_is_normal_only':True,
        'benchmark_anomaly_labels_used_for_fit':False,'synthetic_noise_replications':4,'candidates':rows,
        'source':'https://proceedings.mlr.press/v9/gutmann10a.html'})


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
