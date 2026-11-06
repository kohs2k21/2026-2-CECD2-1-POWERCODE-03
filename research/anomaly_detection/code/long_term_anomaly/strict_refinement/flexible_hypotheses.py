"""Finer generic density-ratio alternatives, development selection only.

Normal AR innovation fit is unchanged. This studies generic shift and Gaussian
broadening hypotheses, not benchmark event templates, labels, or seeds.
"""
from itertools import product
from pathlib import Path
import argparse
import hashlib
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import lfilter
from scipy.optimize import differential_evolution
from ..benchmark import load_split
from ..common import DATA, read_json, write_json, sha256
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .shared_ar import context_parameters
from .preprocessing_search import context
from .model import evidence_score

RANGES = tuple((lo, lo + width) for lo, width in product((0., .1, .25, .5, 1., 2.), (.25, .5, 1., 2., 3.)))
SIGMAS = (.1, .25, .5, 1., 2., 4.)


def fit_density():
    d=load_split('train',False);innovation=[];scales=[]
    for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']):
        _,_,scale,rho,_,_,z,pair=context_parameters(x[:672],o[:672],a[:672])
        e=(z[1:]-rho*z[:-1])/np.sqrt(1-rho*rho)
        innovation.append(e[pair]);scales.append(scale)
    pool=np.concatenate(innovation);pool-=pool.mean(0);pool/=pool.std(0)
    grid=np.linspace(-12,12,4097);step=grid[1]-grid[0]
    edges=np.r_[grid-step/2,grid[-1]+step/2];buckets=np.arange(0,.951,.05)
    rng=np.random.default_rng(20261022);density=np.empty((2,len(buckets),len(grid)))
    for i,rho in enumerate(buckets):
        for ch in range(2):
            eps=pool[rng.integers(len(pool),size=160256),ch]
            samples=lfilter([np.sqrt(1-rho*rho)],[1,-rho],eps)[256:]
            hist,_=np.histogram(samples,bins=edges)
            p=gaussian_filter1d(hist/max(hist.sum(),1)/step,.03/step,mode='constant')
            density[ch,i]=p/(p.sum()*step)
    return grid,buckets,density,np.median(scales,axis=0)


def tables(grid,density):
    step=grid[1]-grid[0];cdf=np.cumsum(density,axis=-1)*step
    fields=[]
    for sign in (1,-1):
        for lo,hi in RANGES:
            result=np.empty_like(density)
            for ch,i in product(range(2),range(density.shape[1])):
                if sign==1:
                    q=(np.interp(grid-lo,grid,cdf[ch,i],left=0,right=1)
                       -np.interp(grid-hi,grid,cdf[ch,i],left=0,right=1))/(hi-lo)
                else:
                    q=(np.interp(grid+hi,grid,cdf[ch,i],left=0,right=1)
                       -np.interp(grid+lo,grid,cdf[ch,i],left=0,right=1))/(hi-lo)
                result[ch,i]=np.clip(np.log(np.maximum(q,1e-100))-np.log(np.maximum(density[ch,i],1e-100)),-100,100)
            fields.append(result)
    for sigma in (*SIGMAS, None):
        q=(gaussian_filter1d(density,sigma/step,axis=-1,mode='constant') if sigma is not None
           else np.mean([gaussian_filter1d(density,s/step,axis=-1,mode='constant') for s in (.5,1.,2.)],axis=0))
        fields.append(np.clip(np.log(np.maximum(q,1e-100))-np.log(np.maximum(density,1e-100)),-100,100))
    return np.asarray(fields)


def bank():
    signature=hashlib.sha256((sha256(__file__)+sha256(DATA/'train_inputs.npz')+sha256(DATA/'development_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'flexible_development_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return z['features']
    grid,buckets,density,prior=fit_density();curves=tables(grid,density)
    dev=load_split('development',False);features=[]
    for case,(x,o,a) in enumerate(zip(dev['X'],dev['collection_observed'],dev['latency_available'])):
        c=context(x,o,a,.5,.5);scales=(c.scale,np.sqrt(np.array([0.,.25])*c.scale**2+np.array([1.,.75])*prior**2))
        values=[]
        for scale in scales:
            z=c.current_residual/scale;channels=[]
            for ch in range(2):
                hi=min(len(buckets)-1,int(np.searchsorted(buckets,c.rho[ch],side='right')));lo=max(0,hi-1)
                f=(c.rho[ch]-buckets[lo])/max(buckets[hi]-buckets[lo],1e-10)
                table=curves[:,ch,lo]*(1-f)+curves[:,ch,hi]*f
                channels.append(np.stack([np.interp(z[:,ch],grid,t,left=100,right=100) for t in table],axis=-1))
            v=np.stack(channels,axis=-2);v[~a[672:],1]=0.;v[~o[672:]]=np.nan
            values.append(v)
        features.append(values)
        if (case+1)%96==0:print('Fine hypothesis features',case+1,flush=True)
    result=np.asarray(features,dtype=np.float32).transpose(1,0,2,3,4)
    np.savez_compressed(path,features=result)
    return result


def search():
    initialize();path=OUT/'flexible_hypothesis_search.json'
    if path.exists():raise FileExistsError('Preserve the completed search')
    f=bank();dev=load_split('development',True);y=dev['y'];available=dev['latency_available'][:,672:].ravel()
    groups=metadata_for_split('development',dev['case_id']).scenario.to_numpy()
    valid=dev['collection_observed'][:,672:];masks=[(groups==g)[:,None]&~y&valid for g in np.unique(groups)]
    initial=read_json(OUT/'calibration.json')['record']['one_hour_config'];n=len(RANGES)
    records=[];best_value=-np.inf
    def objective(v):
        nonlocal best_value
        w=np.r_[1.,np.exp(v[:5])];indices=np.rint(v[5:10]).astype(int);shrink=int(v[10]>=.5)
        r_up,r_down,d_up,r_var,d_var=indices
        query=np.stack([f[shrink,:,:,0,r_up],f[shrink,:,:,0,n+r_down],f[shrink,:,:,0,2*n+r_var],
            f[shrink,:,:,1,d_up],f[shrink,:,:,1,n+RANGES.index((1.,3.))],f[shrink,:,:,1,2*n+d_var]],axis=-1).astype(float)
        s=evidence_score(query.reshape(-1,6),w,available,.06538530001229582).reshape(y.shape)
        threshold=max(float(np.quantile(s[m],.941,method='higher')) for m in masks)
        m=compact_metrics(y,s,threshold,groups);med=m['median'];minimum=min(med.values())
        row=dict(weights=w.tolist(),request_up=RANGES[r_up],request_down=RANGES[r_down],duration_up=RANGES[d_up],
            request_variance_sigma=((*SIGMAS,None)[r_var]),duration_variance_sigma=((*SIGMAS,None)[d_var]),
            scale_shrinkage=shrink,threshold=threshold,**med,max_fpr=float(m['maximum_scenario_fpr']))
        records.append(row)
        if minimum>best_value+1e-5:
            best_value=minimum;print('Fine generic alternatives',row,flush=True)
        return -minimum-.02*med['f1']
    same=RANGES.index((1.,3.));x0=np.r_[np.log(initial['weights'][1:]),same,same,same,len(SIGMAS),len(SIGMAS),1.]
    objective(x0)
    result=differential_evolution(objective,[(-4.,4.)]*5+[(0,n-1)]*3+[(0,len(SIGMAS))]*2+[(0.,1.)],
        x0=x0,seed=20261031,maxiter=24,popsize=6,workers=1,polish=False,tol=.0005)
    best=max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
    write_json(path,dict(normal_fit_only=True,test_used=False,benchmark_unchanged=True,monitoring_hours_used=1,
        source_sha256=sha256(__file__),best=best,candidates=records,optimizer_message=str(result.message)))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
