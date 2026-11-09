"""Normal-reference quantile alignment, current-row only, development search."""
from itertools import product
import argparse
import numpy as np
from ..benchmark import load_split
from ..common import write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import evidence_score, load_bank
from .preprocessing_search import context
from .flexible_hypotheses import fit_density


def linear_warp(x, source, target):
    """Monotone interpolation, bounded-slope extrapolation, identity on ties."""
    source=np.asarray(source,float);target=np.asarray(target,float)
    keep=np.r_[True,np.diff(source)>1e-8];source,target=source[keep],target[keep]
    if len(source)<2:return np.asarray(x,float)
    value=np.interp(x,source,target)
    lower=np.clip((target[1]-target[0])/(source[1]-source[0]),.25,4.)
    upper=np.clip((target[-1]-target[-2])/(source[-1]-source[-2]),.25,4.)
    return np.where(x<source[0],target[0]+lower*(x-source[0]),
                    np.where(x>source[-1],target[-1]+upper*(x-source[-1]),value))


def search():
    initialize();path=OUT/'quantile_warp_search.json'
    if path.exists():raise FileExistsError('Preserve completed experiment')
    grid,buckets,p0,prior=fit_density();cdf=np.cumsum(p0,axis=-1)*(grid[1]-grid[0])
    point=load_bank(OUT/'models.joblib').point
    dev=load_split('development',True);y=dev['y']
    observed,available=dev['collection_observed'][:,672:],dev['latency_available'][:,672:]
    groups=metadata_for_split('development',dev['case_id']).scenario.to_numpy()
    masks=[(groups==g)[:,None]&~y&observed for g in np.unique(groups)]
    contexts=[context(x,o,a,.5,.5) for x,o,a in zip(dev['X'],dev['collection_observed'],dev['latency_available'])]
    records=[]
    for quantiles in ((.05,.5,.95),(.01,.05,.25,.5,.75,.95,.99),
                      (.005,.01,.025,.05,.1,.25,.5,.75,.9,.95,.975,.99,.995)):
        for scale_mode in (0,1):
            features=[]
            for c in contexts:
                scale=(c.scale if scale_mode==0 else np.sqrt(np.array([0.,.25])*c.scale**2+np.array([1.,.75])*prior**2))
                raw=c.current_residual/scale
                normal=c.z*c.scale/scale
                channels=[]
                for ch in range(2):
                    hi=min(len(buckets)-1,int(np.searchsorted(buckets,c.rho[ch],side='right')));lo=max(0,hi-1)
                    t=(c.rho[ch]-buckets[lo])/max(buckets[hi]-buckets[lo],1e-10)
                    curve_cdf=cdf[ch,lo]*(1-t)+cdf[ch,hi]*t
                    target=np.interp(quantiles,curve_cdf,grid)
                    source=np.quantile(normal[:,ch],quantiles)
                    aligned=linear_warp(raw[:,ch],source,target)
                    curve=point.curves[ch*3:ch*3+3,lo]*(1-t)+point.curves[ch*3:ch*3+3,hi]*t
                    channels.append([np.stack([np.interp((1-alpha)*raw[:,ch]+alpha*aligned,grid,v,left=100,right=100)
                                               for v in curve],axis=-1) for alpha in (0.,.25,.5,.75,1.)])
                features.append(channels)
            features=np.asarray(features)
            for request_alpha,duration_alpha in product(range(5),range(5)):
                f=np.concatenate([features[:,0,request_alpha],features[:,1,duration_alpha]],axis=-1)
                f[:,:,3:]=np.where(available[:,:,None],f[:,:,3:],0.)
                f[~observed]=np.nan
                score=evidence_score(f.reshape(-1,6),point.config['weights'],available.ravel(),point.config['temperature']).reshape(y.shape)
                threshold=max(float(np.quantile(score[m],.941,method='higher')) for m in masks)
                r=compact_metrics(y,score,threshold,groups)
                records.append(dict(quantiles=quantiles,scale_shrinkage=scale_mode,
                    alignment_strength=[request_alpha*.25,duration_alpha*.25],threshold=threshold,
                    **r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
            best=max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
            print('Normal-only quantile alignment',len(quantiles),scale_mode,'best',best,flush=True)
    write_json(path,dict(normal_fit_only=True,test_used=False,monitoring_hours_used=1,
                        best=best,candidates=records))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
