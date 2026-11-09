"""Higher-order normal residual resampling, development-only sensitivity study.

All AR parameters and innovations use reviewed normal references. A monitored
hour is queried independently against a stationary normal marginal density.
No monitoring lags, case IDs, benchmark seeds, event bounds or truth enter fit.
This adapts AR residual-resampling ideas, not their asymptotic guarantees.
"""
from concurrent.futures import ThreadPoolExecutor
from itertools import product
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
from .preprocessing_search import context
from .model import evidence_score

BANDWIDTHS=(.03,.07,.15)


def fit_ar(z, observed, order, ridge):
    z=np.asarray(z,float);observed=np.asarray(observed,bool)
    if z.ndim!=1 or len(z)<4*order or observed.shape!=z.shape:raise ValueError('Adequate one-channel normal reference required')
    lags=np.column_stack([z[order-j:-j] for j in range(1,order+1)])
    good=observed[order:].copy()
    for j in range(1,order+1):good &= observed[order-j:-j]
    if good.sum()<4*order:raise ValueError('Too few observed normal lag windows')
    a=lags[good];target=z[order:][good]
    gram=a.T@a;penalty=float(ridge)*np.trace(gram)/order
    coeff=np.linalg.solve(gram+np.eye(order)*(penalty+1e-8),a.T@target)
    # The resampled stationary marginal is undefined for explosive coefficients.
    # Shrink only the fitted normal parameters, never observed monitored values.
    for _ in range(200):
        if np.max(abs(np.roots(np.r_[1.,-coeff])))<.98:break
        coeff*=.98
    else:raise ValueError('Failed to obtain stable normal AR fit')
    innovation=target-a@coeff
    innovation=(innovation-innovation.mean())/max(innovation.std(),1e-6)
    return coeff,innovation


def fit_innovations(order,ridge):
    train=load_split('train',False);pool=[[],[]];scales=[]
    for x,o,a in zip(train['X'],train['collection_observed'],train['latency_available']):
        c=context(x[:672],o[:672],a[:672],.5,.5);scales.append(c.scale)
        for ch in range(2):
            good=o[:672] if ch==0 else o[:672]&a[:672]
            _,e=fit_ar(c.z[:,ch],good,order,ridge);pool[ch].append(e)
    pool=[np.concatenate(v) for v in pool]
    pool=[(v-v.mean())/v.std() for v in pool]
    # Independent model Monte Carlo randomness, shared across cases to reduce
    # simulation variability. Not obtained from any benchmark generation seed.
    rng=np.random.default_rng(20261102)
    draws=[v[rng.integers(len(v),size=133120)] for v in pool]
    return draws,np.median(scales,axis=0)


def _case(payload):
    x,o,a,order,ridge,draws,prior=payload
    c=context(x,o,a,.5,.5);grid=np.linspace(-12,12,4097);step=grid[1]-grid[0]
    edges=np.r_[grid-step/2,grid[-1]+step/2]
    scale=[c.scale,np.sqrt(np.array([0.,.25])*c.scale**2+np.array([1.,.75])*prior**2)]
    columns=[[[] for _ in BANDWIDTHS] for _ in scale]
    for ch in range(2):
        good=o[:672] if ch==0 else o[:672]&a[:672]
        coeff,_=fit_ar(c.z[:,ch],good,order,ridge)
        denominator=np.r_[1.,-coeff];impulse=np.zeros(4096);impulse[0]=1.
        response=lfilter([1.],denominator,impulse)
        innovation_scale=1/np.sqrt(np.sum(response**2))
        samples=lfilter([innovation_scale],denominator,draws[ch])[2048:]
        hist,_=np.histogram(samples,bins=edges);raw=hist/max(hist.sum(),1)/step
        for j,bw in enumerate(BANDWIDTHS):
            p=gaussian_filter1d(raw,bw/step,mode='constant');p/=p.sum()*step
            cdf=np.cumsum(p)*step
            up=(np.interp(grid-1,grid,cdf,left=0,right=1)-np.interp(grid-3,grid,cdf,left=0,right=1))/2
            down=(np.interp(grid+3,grid,cdf,left=0,right=1)-np.interp(grid+1,grid,cdf,left=0,right=1))/2
            broad=np.mean([gaussian_filter1d(p,s/step,mode='constant') for s in (.5,1.,2.)],axis=0)
            curves=np.clip(np.log(np.maximum([up,down,broad],1e-100))-np.log(np.maximum(p,1e-100)),-100,100)
            for i,s in enumerate(scale):
                z=c.current_residual[:,ch]/s[ch]
                columns[i][j].extend(np.interp(z,grid,v,left=100,right=100) for v in curves)
    result=np.asarray([[np.column_stack(fields) for fields in row] for row in columns])
    result[:,:,:,3:]=np.where(a[672:][None,None,:,None],result[:,:,:,3:],0.)
    result[:,:,~o[672:]]=np.nan
    return result


def bank(order,ridge):
    signature=hashlib.sha256((sha256(__file__)+sha256(DATA/'train_inputs.npz')+sha256(DATA/'development_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'AR_sieve_p{order}_ridge{ridge}_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return z['features']
    draws,prior=fit_innovations(order,ridge);d=load_split('development',False)
    args=((x,o,a,order,ridge,draws,prior) for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']))
    rows=[]
    with ThreadPoolExecutor(max_workers=6) as executor:
        for i,row in enumerate(executor.map(_case,args)):
            rows.append(row)
            if (i+1)%96==0:print('Normal AR sieve',order,ridge,i+1,flush=True)
    result=np.asarray(rows,dtype=np.float32).transpose(1,2,0,3,4)
    np.savez_compressed(path,features=result);return result


def search():
    initialize();path=OUT/'ar_sieve_search.json'
    if path.exists():raise FileExistsError('Preserve completed search')
    d=load_split('development',True);y=d['y'];available=d['latency_available'][:,672:].ravel()
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy();observed=d['collection_observed'][:,672:]
    masks=[(groups==g)[:,None]&~y&observed for g in np.unique(groups)]
    initial=read_json(OUT/'calibration.json')['record']['one_hour_config'];records=[];top=-np.inf
    banks={}
    def evaluate(f,weights,order,ridge,scale_mode,bw):
        nonlocal top
        score=evidence_score(f.reshape(-1,6).astype(float),weights,available,initial['temperature']).reshape(y.shape)
        threshold=max(float(np.quantile(score[m],.941,method='higher')) for m in masks)
        r=compact_metrics(y,score,threshold,groups);row=dict(order=order,ridge=ridge,scale_mode=scale_mode,
            bandwidth=bw,weights=np.asarray(weights).tolist(),threshold=threshold,
            **r['median'],max_fpr=float(r['maximum_scenario_fpr']))
        records.append(row);value=min(r['median'].values())
        if value>top+1e-5:
            top=value;print('Higher-order normal marginal candidate',row,flush=True)
        return -value-.02*row['f1']
    for order,ridge in product((1,2,4,8),(0.,.1,.5)):
        f=bank(order,ridge)
        for i,j in product(range(2),range(3)):
            evaluate(f[i,j],initial['weights'],order,ridge,i,BANDWIDTHS[j])
        write_json(OUT/'ar_sieve_coarse_progress.json',dict(normal_fit_only=True,test_used=False,candidates=records))
    leaders=sorted(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']),reverse=True)[:3]
    for seed,row in enumerate(leaders):
        f=bank(row['order'],row['ridge'])[row['scale_mode'],BANDWIDTHS.index(row['bandwidth'])]
        def objective(v):return evaluate(f,np.r_[1.,np.exp(v)],row['order'],row['ridge'],row['scale_mode'],row['bandwidth'])
        differential_evolution(objective,[(-4.,4.)]*5,x0=np.log(row['weights'][1:]),seed=20261102+seed,
            maxiter=14,popsize=6,workers=1,polish=False,tol=.0005)
    best=max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
    write_json(path,dict(normal_fit_only=True,test_used=False,normal_reference_hours=672,monitoring_hours_used=1,
        source_sha256=sha256(__file__),best=best,candidates=records,
        source='https://statistics.berkeley.edu/sites/default/files/tech-reports/431.pdf'))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
