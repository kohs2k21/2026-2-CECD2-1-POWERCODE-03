"""Normal-only joint density diagnostics, restricted to one monitoring hour."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import differential_evolution
from sklearn.mixture import GaussianMixture
from ..benchmark import load_split
from ..common import DATA, write_json, sha256
from ..experiment import metadata_for_split
from .explore import reference_distributions, bank, OUT
from .moments import compact_metrics


def worker(payload):
    x, obs, lat = payload
    forecast, r = reference_distributions(x[:672],obs[:672],lat[:672],'phase_median')
    r = r[:,:2]
    current = x[672:,:2] - forecast[np.arange(336)%24,:2]
    scale = np.maximum(np.std(r,axis=0),1e-5)
    r, current = r/scale, current/scale
    # Placeholders are queried only to keep array shapes; invalid joint rows
    # are explicitly disabled/abstained below, never treated as real zeros.
    current=np.nan_to_num(current,nan=0.)
    result=[]
    tree=cKDTree(r)
    for k in (4,16,48):
        norm=tree.query(r,k=k+1)[0][:,-1]
        test=tree.query(current,k=k)[0][:,-1]
        result.append(test / max(np.quantile(norm,.95),.01))
    for components in (2,4,8):
        model=GaussianMixture(n_components=components,reg_covar=.01,random_state=20261019,n_init=1).fit(r)
        norm=-model.score_samples(r); test=-model.score_samples(current)
        center,tail=np.quantile(norm,[.5,.95])
        result.append((test-center)/max(tail-center,.05))
    f=np.column_stack(result)
    f[~lat[672:]]=0.
    f[~obs[672:]]=np.nan
    return f


def features():
    split='development'
    d=load_split(split)
    sig=hashlib.sha256((sha256(Path(__file__))+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'joint_{split}_{sig}.npz'
    if path.exists():
        with np.load(path) as f:return f['features'],d
    with ProcessPoolExecutor(max_workers=min(6,os.cpu_count() or 2)) as p:
        f=np.asarray(list(p.map(worker,zip(d['X'],d['collection_observed'],d['latency_available']),chunksize=4)))
    np.savez_compressed(path,features=f)
    return f,d


def search():
    f,d=features()
    truth=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    valid=np.isfinite(f).any(axis=2)
    rows=[]
    for i in range(f.shape[2]):
        s=f[:,:,i]
        t=max(np.quantile(s[(groups==g)[:,None]&~truth&valid],.945,method='higher') for g in np.unique(groups))
        r=compact_metrics(truth,s,t,groups)
        rows.append(dict(model='knn' if i<3 else 'gmm',parameter=(4,16,48,2,4,8)[i],threshold=float(t),
                         **r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
    write_json(OUT/'joint_development_search.json',rows)
    print('Joint normal-density development',rows,flush=True)
    points,_=bank('development','phase_median',.99)
    # Broad, deterministic channel/tail weighting range. Anomaly labels are
    # development selection only; each density estimator fit normal context.
    all_features=np.concatenate([points,f],axis=2)
    safe=np.nan_to_num(all_features,nan=0.)
    masks=[(groups==g)[:,None]&~truth&valid for g in np.unique(groups)]
    history=[]
    def objective(log_w):
        w=np.r_[1.,np.exp(log_w)]
        s=np.max(safe*w,axis=2);s[~valid]=np.nan
        t=max(np.quantile(s[m],.945,method='higher') for m in masks)
        r=compact_metrics(truth,s,t,groups)
        med=r['median']; minimum=min(med.values())
        history.append(dict(weights=w.tolist(),threshold=float(t),**med,max_fpr=float(r['maximum_scenario_fpr'])))
        return -minimum - .02*med['f1']
    fit=differential_evolution(objective,[(-4.,4.)]*11,seed=20261019,maxiter=14,popsize=5,
                               polish=False,workers=1)
    best=min(history,key=lambda r:-min(r[k] for k in ('accuracy','precision','recall','f1'))-.02*r['f1'])
    write_json(OUT/'joint_balance_search.json',{'best':best,'candidates':history})
    print('Joint + marginal candidate',best,flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    search()
