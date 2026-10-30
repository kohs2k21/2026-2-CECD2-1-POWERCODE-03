"""Local continuous refinement of normal density evidence, development only."""
import argparse
import numpy as np
from scipy.special import logsumexp
from scipy.optimize import differential_evolution
from ..common import read_json,write_json
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .shared_ar import bank,BANDWIDTHS,RANGES
from .experiment import OUT,initialize
from .optimize_shared import components


def soft_score(features,weights,available,temperature):
    f=np.asarray(features,float);w=np.broadcast_to(np.asarray(weights,float),f.shape).copy()
    w[~np.asarray(available,bool),3:]=0.
    with np.errstate(divide='ignore'):logw=np.log(w)
    if temperature<=0:
        score=np.max(f+logw,axis=1)-np.max(logw,axis=1)
    else:
        score=temperature*(logsumexp((f+logw)/temperature,axis=1)-logsumexp(logw/temperature,axis=1))
    score[~np.isfinite(f).any(axis=1)]=np.nan
    return score


def search():
    initialize();features,d=bank('development');y=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    valid=np.isfinite(features[.03,0]).any(axis=2);available=d['latency_available'][:,672:].ravel()
    best=read_json(OUT/'optimized_AR_search.json')['best']
    br=BANDWIDTHS.index(best['bandwidth_requests']);bd=BANDWIDTHS.index(best['bandwidth_duration'])
    ru=RANGES.index(tuple(best['request_up']));rd=RANGES.index(tuple(best['request_down']));du=RANGES.index(tuple(best['duration_up']))
    query=components(features,(br,bd,ru,rd,du)).reshape(-1,6)
    records=[];top=-1.
    def evaluate(log_weights,temperature):
        nonlocal top
        w=np.r_[1.,np.exp(log_weights)]
        score=soft_score(query,w,available,temperature).reshape(y.shape)
        t=max(float(np.quantile(score[m&valid],.941,method='higher')) for m in normal)
        r=compact_metrics(y,score,t,groups);med=r['median'];minimum=min(med.values())
        row={**best,'weights':w.tolist(),'temperature':float(temperature),'threshold':t,**med,
             'max_fpr':float(r['maximum_scenario_fpr'])}
        records.append(row)
        if minimum>top+1e-5:
            top=minimum;print('Local point refinement',row,flush=True)
        return -minimum-.02*med['f1']
    evaluate(np.log(best['weights'][1:]),0.)
    def objective(v):return evaluate(v[:5],np.exp(v[5]))
    optimum=differential_evolution(objective,[(-4.,4.)]*5+[(-3.,1.)],
        x0=np.r_[np.log(best['weights'][1:]),-3.],seed=20261024,maxiter=24,popsize=10,tol=.0005,
        polish=False,workers=1)
    selected=max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
    write_json(OUT/'local_AR_search.json',{'test_used_for_selection':False,'best':selected,
        'candidates':records,'optimizer_message':str(optimum.message)})
    print('Best current-hour development candidate',selected,flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
