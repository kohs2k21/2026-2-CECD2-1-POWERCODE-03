"""Normal-only shared innovation density with per-reference AR(1) conditioning.

Training normal residual innovations are pooled across groups. Stationary
normal AR distributions are simulated with independent Monte Carlo randomness,
NOT the benchmark's seeds, labels, event bounds or generator parameters.
Monitoring rows are still scored independently against a fixed normal reference.
"""
from pathlib import Path
import argparse
import hashlib
import numpy as np
from scipy.signal import lfilter
from scipy.ndimage import gaussian_filter1d
from ..common import DATA,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import prepare,mix_score
from .experiment import OUT,initialize

RANGES=((.25,4.),(.5,2.5),(1.,3.),(1.5,3.5))
BANDWIDTHS=(.03,.07,.15,.3)


def context_parameters(x,o,a):
    profile,ref,slope,mid=prepare(x,o,a,.5,.5)
    center=np.mean(ref[:,:2],axis=0)
    scale=np.maximum(np.std(ref[:,:2],axis=0),1e-4)
    z=(ref[:,:2]-center)/scale
    good=o&a;pair=good[:-1]&good[1:]
    rho=np.array([np.clip(np.corrcoef(z[:-1,j][pair],z[1:,j][pair])[0,1],0,.95) for j in range(2)])
    return profile,center,scale,rho,slope,mid,z,pair


def fit_normal_density():
    d=load_split('train',False);innovations=[];estimated_rho=[]
    for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']):
        _,_,_,rho,_,_,z,pair=context_parameters(x[:672],o[:672],a[:672])
        e=(z[1:]-rho*z[:-1])/np.sqrt(1-rho*rho)
        innovations.append(e[pair]);estimated_rho.append(rho)
    pool=np.concatenate(innovations);pool-=pool.mean(axis=0);pool/=pool.std(axis=0)
    grid=np.linspace(-12,12,4097);step=grid[1]-grid[0]
    edges=np.r_[grid-step/2,grid[-1]+step/2]
    buckets=np.arange(0,.951,.05);rng=np.random.default_rng(20261022)
    densities=np.empty((len(buckets),2,len(grid)))
    for i,rho in enumerate(buckets):
        for ch in range(2):
            e=pool[rng.integers(len(pool),size=160256),ch]
            stationary=lfilter([np.sqrt(1-rho*rho)],[1,-rho],e)[256:]
            counts,_=np.histogram(stationary,bins=edges)
            densities[i,ch]=counts/max(counts.sum(),1)/step
    curves={}
    for bw in BANDWIDTHS:
        for index,(lo,hi) in enumerate(RANGES):
            table=np.empty((len(buckets),2,3,len(grid)))
            for i in range(len(buckets)):
                for ch in range(2):
                    p=gaussian_filter1d(densities[i,ch],bw/step,mode='constant')
                    p/=p.sum()*step
                    cdf=np.cumsum(p)*step
                    up=(np.interp(grid-lo,grid,cdf,left=0,right=1)-np.interp(grid-hi,grid,cdf,left=0,right=1))/(hi-lo)
                    down=(np.interp(grid+hi,grid,cdf,left=0,right=1)-np.interp(grid+lo,grid,cdf,left=0,right=1))/(hi-lo)
                    broad=np.mean([gaussian_filter1d(p,sigma/step,mode='constant') for sigma in (.5,1.,2.)],axis=0)
                    table[i,ch]=np.clip(np.log(np.maximum(np.vstack([up,down,broad]),1e-100))-np.log(np.maximum(p,1e-100)),-100,100)
            curves[bw,index]=table
    return dict(grid=grid,buckets=buckets,curves=curves,training_normal_cases=len(d['X']),
                pooled_innovations=len(pool),estimated_train_rho=np.asarray(estimated_rho),
                benchmark_truth_used=False,benchmark_generator_seed_used=False)


def learned_density():
    sig=hashlib.sha256((sha256(Path(__file__))+sha256(DATA/'train_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'shared_AR_density_{sig}.npz'
    if path.exists():
        with np.load(path) as z:
            curves={(bw,i):z[f'curve_{bw}_{i}'] for bw in BANDWIDTHS for i in range(4)}
            return dict(grid=z['grid'],buckets=z['buckets'],curves=curves)
    model=fit_normal_density()
    np.savez_compressed(path,grid=model['grid'],buckets=model['buckets'],**{f'curve_{bw}_{i}':v for (bw,i),v in model['curves'].items()})
    write_json(OUT/'shared_AR_training.json',{k:v for k,v in model.items() if k not in ('grid','buckets','curves')})
    print('Shared AR normal density learned from 64 normal cases',flush=True)
    return model


def features_for_case(model,x,o,a):
    profile,center,scale,rho,slope,mid,_,_=context_parameters(x[:672],o[:672],a[:672])
    r=x[672:,:2]-profile[np.arange(336)%24,:2]-(np.arange(672,1008)-mid)[:,None]*slope[:2]-center
    z=r/scale
    result={}
    for (bw,index),table in model['curves'].items():
        columns=[]
        for ch in range(2):
            high=min(len(model['buckets'])-1,int(np.searchsorted(model['buckets'],rho[ch],side='right')))
            low=max(0,high-1)
            fraction=(rho[ch]-model['buckets'][low])/max(model['buckets'][high]-model['buckets'][low],1e-10)
            for kind in range(3):
                curve=table[low,ch,kind]*(1-fraction)+table[high,ch,kind]*fraction
                columns.append(np.interp(z[:,ch],model['grid'],curve,left=100,right=100))
        f=np.column_stack(columns);f[np.ix_(~a[672:],[3,4,5])]=0.;f[~o[672:]]=np.nan
        result[bw,index]=f
    return result


def bank(split):
    d=load_split(split)
    sig=hashlib.sha256((sha256(Path(__file__))+sha256(DATA/'train_inputs.npz')+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'shared_AR_features_{split}_{sig}.npz'
    if path.exists():
        with np.load(path) as z:return {(bw,i):z[f'{bw}_{i}'] for bw in BANDWIDTHS for i in range(4)},d
    model=learned_density();features=[]
    for i,(x,o,a) in enumerate(zip(d['X'],d['collection_observed'],d['latency_available'])):
        features.append(features_for_case(model,x,o,a))
        if (i+1)%96==0:print('Shared AR features',split,i+1,flush=True)
    result={key:np.asarray([f[key] for f in features]) for key in model['curves']}
    np.savez_compressed(path,**{f'{bw}_{i}':v for (bw,i),v in result.items()})
    return result,d


def search():
    initialize();features,d=bank('development');y=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normals=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    valid=np.isfinite(features[.03,0]).any(axis=2);available=d['latency_available'][:,672:].ravel()
    rng=np.random.default_rng(20261022)
    weights=[np.array(w,float) for w in ((1,16,.1,8,.1,1),(1,8,.1,4,.1,1),(1,1,1,1,1,1))]
    weights += [np.r_[1.,np.exp(rng.uniform(-3.,3.,5))] for _ in range(70)]
    rows=[]
    for bw in BANDWIDTHS:
        for index in range(4):
            f=features[bw,index]
            for w in weights:
                for mode in ('mixture','maximum'):
                    score=mix_score(f.reshape(-1,6),w,available,mode).reshape(y.shape)
                    for cap in (.05,.055,.059):
                        t=max(float(np.quantile(score[m&valid],1-cap,method='higher')) for m in normals)
                        r=compact_metrics(y,score,t,groups)
                        rows.append(dict(bandwidth=bw,shift_range=RANGES[index],weights=w.tolist(),mode=mode,
                            cap=cap,threshold=t,**r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
        best=max(rows,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
        print('Shared-innovation AR candidate',best,flush=True)
        write_json(OUT/'shared_AR_search.json',rows)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
