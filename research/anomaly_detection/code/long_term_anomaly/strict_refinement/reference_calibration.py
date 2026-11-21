"""Normal-reference-only leave-one-out calibration of ratio features."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
import pandas as pd
from scipy.special import logsumexp
from ..common import DATA,read_json,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import prepare,log_cdf_difference,mix_score
from .experiment import OUT,initialize,bank as raw_bank


def leave_one_out_features(ref,observed,available,bw):
    columns=[];n=len(ref)
    for ch in range(2):
        values=ref[:,ch]/max(float(np.std(ref[:,ch])),.03)
        diff=values[:,None]-values[None,:]
        matrix=-.5*(diff/bw)**2
        np.fill_diagonal(matrix,-np.inf)
        p0=logsumexp(matrix,axis=1)-np.log((n-1)*bw*np.sqrt(2*np.pi))
        up=log_cdf_difference((diff-.25)/bw,(diff-4.)/bw);np.fill_diagonal(up,-np.inf)
        down=log_cdf_difference((diff+4.)/bw,(diff+.25)/bw);np.fill_diagonal(down,-np.inf)
        up=logsumexp(up,axis=1)-np.log((n-1)*3.75)
        down=logsumexp(down,axis=1)-np.log((n-1)*3.75)
        var=[]
        for noise in (.5,1.,2.):
            width=np.sqrt(bw*bw+noise*noise)
            matrix=-.5*(diff/width)**2;np.fill_diagonal(matrix,-np.inf)
            var.append(logsumexp(matrix,axis=1)-np.log((n-1)*width*np.sqrt(2*np.pi)))
        broad=logsumexp(var,axis=0)-np.log(3)
        columns.extend([up-p0,down-p0,broad-p0])
    features=np.clip(np.column_stack(columns),-100,100)
    features[np.ix_(~available,[3,4,5])]=np.nan;features[~observed]=np.nan
    return features


def worker(payload):
    x,o,a,seasonal=payload
    _,ref,_,_=prepare(x[:672],o[:672],a[:672],seasonal,0.)
    return {bw:leave_one_out_features(ref,o[:672],a[:672],bw) for bw in (.1,.25,.5)}


def reference_bank(split,seasonal):
    d=load_split(split)
    signature=hashlib.sha256((sha256(Path(__file__))+sha256(Path(__file__).with_name('ratio.py'))
                            +sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'normal_ratio_reference_{split}_{seasonal}_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return {bw:z[str(bw)] for bw in (.1,.25,.5)}
    payloads=((x,o,a,seasonal) for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']))
    with ProcessPoolExecutor(max_workers=min(6,os.cpu_count() or 2)) as p:
        values=list(p.map(worker,payloads,chunksize=4))
    result={bw:np.asarray([v[bw] for v in values]) for bw in (.1,.25,.5)}
    np.savez_compressed(path,**{str(k):v for k,v in result.items()})
    print('Normal-only LOO reference',split,seasonal,flush=True)
    return result


def search():
    initialize();d=load_split('development',True);y=d['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    avail=d['latency_available'][:,672:].ravel()
    rng=np.random.default_rng(20261020)
    weights=[np.array(w,float) for w in ((1,1,1,1,1,1),(1,8,.1,4,.1,1),(1,16,.1,8,.1,1))]
    weights.extend(np.r_[1.,np.exp(rng.uniform(-3,3,5))] for _ in range(60))
    rows=[]
    for seasonal in (.5,1.):
        raw,_=raw_bank('development',seasonal,0.)
        ref=reference_bank('development',seasonal)
        for bwr in (.1,.25,.5):
            for bwd in (.1,.25,.5):
                f=np.concatenate([raw[bwr][:,:,:3],raw[bwd][:,:,3:]],axis=2)
                r=np.concatenate([ref[bwr][:,:,:3],ref[bwd][:,:,3:]],axis=2)
                med=np.nanmedian(r,axis=1);spread=np.maximum(np.nanquantile(r,.95,axis=1)-med,.1)
                for norm in ('none','per_feature','combined_score'):
                    query=(f-med[:,None])/spread[:,None] if norm=='per_feature' else f
                    flat=query.reshape(-1,6);valid=np.isfinite(f).any(axis=2)
                    for w in weights:
                        s=mix_score(flat,w,avail,'maximum').reshape(y.shape)
                        if norm=='combined_score':
                            ref_available=d['latency_available'][:,:672].ravel()
                            sr=mix_score(r.reshape(-1,6),w,ref_available,'maximum').reshape(len(r),-1)
                            center=np.nanmedian(sr,axis=1);width=np.maximum(np.nanquantile(sr,.95,axis=1)-center,.1)
                            s=(s-center[:,None])/width[:,None]
                        t=max(float(np.quantile(s[m&valid],.945,method='higher')) for m in normal)
                        result=compact_metrics(y,s,t,groups)
                        rows.append(dict(seasonal_strength=seasonal,bandwidth_requests=bwr,bandwidth_duration=bwd,
                            normalization=norm,weights=w.tolist(),threshold=t,cap=.055,**result['median'],
                            max_fpr=float(result['maximum_scenario_fpr'])))
                best=max(rows,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
                print('Reference-calibrated point candidate',best,flush=True)
            write_json(OUT/'reference_calibration_search.json',rows)
    pd.DataFrame([{k:v for k,v in r.items() if k!='weights'} for r in rows]).to_csv(OUT/'reference_calibration_search.csv',index=False)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
