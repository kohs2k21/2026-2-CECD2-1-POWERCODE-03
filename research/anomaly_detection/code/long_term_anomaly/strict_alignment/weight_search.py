"""Development-only evidence tuning after frozen normal-reference alignment.

The density fit, input resolution, original labels and selected-batch-only
inference are unchanged. No test inputs or truth are opened by this module.
"""
from itertools import product
import argparse
import hashlib
import numpy as np
from scipy.optimize import differential_evolution
from scipy.special import logsumexp
from ..benchmark import load_split
from ..common import DATA,read_json,write_json,sha256
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from ..strict_refinement.model import evidence_score
from ..strict_refinement.quantile_warp import linear_warp
from .model import load_bank
from .run import OUT,initialize


def features(split):
    d=load_split(split,False)
    sig=hashlib.sha256((sha256(__file__)+sha256(OUT/'models.joblib')+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'aligned_evidence_{split}_{sig}.npz'
    if path.exists():
        with np.load(path) as z:return z['features'],d
    point=load_bank(OUT/'models.joblib').point;rows=[]
    for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']):
        reference=point.prepare_reference(x[:672],o[:672],a[:672]);ref=reference.point
        hours=np.arange(336)
        z=(x[672:,:2]-ref.profile[hours%24,:2]-(672+hours-ref.midpoint)[:,None]*ref.slope[:2]-ref.center)/ref.scale
        for ch,strength in enumerate(point.config['alignment_strength']):
            if strength:
                mapped=linear_warp(z[:,ch],reference.source_quantiles[ch],reference.target_quantiles[ch])
                z[:,ch]=(1-strength)*z[:,ch]+strength*mapped
        columns=[]
        for j in range(6):
            ch=j//3;hi=min(len(point.buckets)-1,int(np.searchsorted(point.buckets,ref.rho[ch],side='right')));lo=max(0,hi-1)
            weight=(ref.rho[ch]-point.buckets[lo])/max(point.buckets[hi]-point.buckets[lo],1e-10)
            curve=point.curves[j,lo]*(1-weight)+point.curves[j,hi]*weight
            columns.append(np.interp(z[:,ch],point.grid,curve,left=100,right=100))
        f=np.column_stack(columns);f[np.ix_(~a[672:],[3,4,5])]=0.;f[~o[672:]]=np.nan
        expected=point.score_arrays(reference,x[672:],o[672:],a[672:])[0]
        actual=evidence_score(f,point.config['weights'],a[672:],point.config['temperature'])
        np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-8,equal_nan=True)
        rows.append(f)
    result=np.asarray(rows);np.savez_compressed(path,features=result);return result,d


def search():
    initialize();path=OUT/'post_alignment_weight_search.json'
    if path.exists():raise FileExistsError('Preserve completed search')
    f,d=features('development');cf,cd=features('calibration')
    y=load_split('development',True)['y'];groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    observed=d['collection_observed'][:,672:];available=d['latency_available'][:,672:].ravel()
    cavail=cd['latency_available'][:,672:].ravel()
    masks=[(groups==g)[:,None]&~y&observed for g in np.unique(groups)]
    initial=load_bank(OUT/'models.joblib').point.config
    records=[];top=-np.inf
    def objective(v,cap=.0598):
        nonlocal top
        weights=np.r_[1.,np.exp(v[:5])];temp=float(np.exp(v[5]))
        s=evidence_score(f.reshape(-1,6),weights,available,temp).reshape(y.shape)
        cs=evidence_score(cf.reshape(-1,6),weights,cavail,temp)
        threshold=max(float(np.nanquantile(cs,1-cap)),*(float(np.quantile(s[m],1-cap,method='higher')) for m in masks))
        r=compact_metrics(y,s,threshold,groups);med=r['median'];minimum=min(med.values())
        row=dict(weights=weights.tolist(),temperature=temp,cap=cap,threshold=threshold,
                 **med,max_fpr=float(r['maximum_scenario_fpr']))
        records.append(row)
        if minimum>top+1e-5:
            top=minimum;print('Post-alignment evidence candidate',row,flush=True)
        return -minimum-.02*med['f1']
    start=np.r_[np.log(initial['weights'][1:]),np.log(initial['temperature'])]
    for cap in (.055,.059,.0598):objective(start,cap)
    result=differential_evolution(objective,[(-6.,6.)]*5+[(-4.,1.)],x0=start,seed=20261104,
        maxiter=36,popsize=10,workers=1,polish=False,tol=.0005)
    best=max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
    write_json(path,dict(normal_density_fit_unchanged=True,test_used=False,development_labels_used_for_selection=True,
        outside_monitoring_batch_used=False,benchmark_or_truth_changed=False,source_sha256=sha256(__file__),
        best=best,candidates=records,optimizer_message=str(result.message)))


if __name__=='__main__':argparse.ArgumentParser(description=__doc__).parse_args();search()
