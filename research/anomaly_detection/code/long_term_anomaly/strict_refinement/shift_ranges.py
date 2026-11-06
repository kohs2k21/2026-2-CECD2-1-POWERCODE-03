"""Compare small/medium/large generic normal perturbation ranges on development."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
from ..benchmark import load_split
from ..common import DATA,sha256,write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import prepare,ratio_features,mix_score
from .experiment import OUT,initialize

RANGES=((.25,4.),(.5,2.5),(1.,3.),(1.5,3.5))


def worker(payload):
    x,o,a=payload
    forecast,ref,slope,mid=prepare(x[:672],o[:672],a[:672],.5,.5)
    residual=x[672:]-forecast[np.arange(336)%24]-(np.arange(672,1008)-mid)[:,None]*slope
    return {i:ratio_features(ref,residual,o[672:],a[672:],.1,interval) for i,interval in enumerate(RANGES)}


def bank(split):
    d=load_split(split)
    signature=hashlib.sha256((sha256(Path(__file__))+sha256(Path(__file__).with_name('ratio.py'))
                            +sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'shift_ranges_{split}_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return {i:z[str(i)] for i in range(len(RANGES))},d
    values=[]
    with ThreadPoolExecutor(max_workers=4) as p:
        for i,value in enumerate(p.map(worker,zip(d['X'],d['collection_observed'],d['latency_available']))):
            values.append(value)
            if (i+1)%48==0:print('Shift-size features',split,i+1,'/',len(d['X']),flush=True)
    result={i:np.asarray([v[i] for v in values]) for i in range(len(RANGES))}
    np.savez_compressed(path,**{str(k):v for k,v in result.items()})
    return result,d


def search():
    initialize();features,d=bank('development');truth=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~truth for g in np.unique(groups)]
    valid=np.isfinite(features[0]).any(axis=2);available=d['latency_available'][:,672:].ravel()
    rng=np.random.default_rng(20261021)
    weights=[np.array(w,float) for w in ((1,16,.1,8,.1,1),(1,8,.1,4,.1,1),(1,4,.1,2,.1,1),(1,32,.1,8,.1,1))]
    weights += [np.r_[1.,np.exp(rng.uniform(-3.5,3.5,5))] for _ in range(40)]
    rows=[]
    for req_up in range(4):
        for req_down in range(4):
            for dur_up in range(4):
                f=features[0].copy();f[:,:,0]=features[req_up][:,:,0]
                f[:,:,1]=features[req_down][:,:,1];f[:,:,3]=features[dur_up][:,:,3]
                for w in weights:
                    for mode in ('maximum','mixture'):
                        score=mix_score(f.reshape(-1,6),w,available,mode).reshape(truth.shape)
                        t=max(float(np.quantile(score[m&valid],.945,method='higher')) for m in normal)
                        r=compact_metrics(truth,score,t,groups)
                        rows.append(dict(request_up=RANGES[req_up],request_down=RANGES[req_down],duration_up=RANGES[dur_up],
                            weights=w.tolist(),mode=mode,threshold=t,cap=.055,**r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
        best=max(rows,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
        print('Generic shift-size candidate',best,flush=True)
        write_json(OUT/'shift_range_search.json',rows)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
