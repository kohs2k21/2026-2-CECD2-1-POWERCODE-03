"""Development-only optimization of shared-innovation normal density ratios."""
import argparse
import numpy as np
from scipy.optimize import differential_evolution
from ..common import read_json,write_json
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import mix_score
from .shared_ar import bank,BANDWIDTHS,RANGES
from .experiment import OUT,initialize


def components(features,indices):
    br,bd,ru,rd,du=indices
    return np.stack([features[BANDWIDTHS[br],ru][:,:,0],features[BANDWIDTHS[br],rd][:,:,1],
                     features[BANDWIDTHS[br],0][:,:,2],features[BANDWIDTHS[bd],du][:,:,3],
                     features[BANDWIDTHS[bd],0][:,:,4],features[BANDWIDTHS[bd],0][:,:,5]],axis=2)


def search():
    initialize();f,d=bank('development');y=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    available=d['latency_available'][:,672:].ravel();valid=np.isfinite(f[.03,0]).any(axis=2)
    initial=max(read_json(OUT/'shared_AR_search.json'),key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
    bw=list(BANDWIDTHS).index(initial['bandwidth']);interval=RANGES.index(tuple(initial['shift_range']))
    x0=np.r_[np.log(initial['weights'][1:]),[bw,bw,interval,interval,interval],int(initial['mode']=='maximum')]
    records=[];best_minimum=-np.inf;lookup={}
    def objective(values):
        nonlocal best_minimum
        indices=tuple(np.rint(values[5:10]).astype(int));mode='maximum' if values[10]>=.5 else 'mixture'
        weights=np.r_[1.,np.exp(values[:5])]
        if indices not in lookup:
            if len(lookup)>=32:lookup.pop(next(iter(lookup)))
            lookup[indices]=components(f,indices).reshape(-1,6)
        score=mix_score(lookup[indices],weights,available,mode).reshape(y.shape)
        threshold=max(float(np.quantile(score[m&valid],.941,method='higher')) for m in normal)
        result=compact_metrics(y,score,threshold,groups);median=result['median'];minimum=min(median.values())
        row=dict(bandwidth_requests=BANDWIDTHS[indices[0]],bandwidth_duration=BANDWIDTHS[indices[1]],
            request_up=RANGES[indices[2]],request_down=RANGES[indices[3]],duration_up=RANGES[indices[4]],
            weights=weights.tolist(),mode=mode,cap=.059,threshold=threshold,**median,
            max_fpr=float(result['maximum_scenario_fpr']))
        records.append(row)
        if minimum>best_minimum+1e-5:
            best_minimum=minimum;print('Improved shared-AR development candidate',row,flush=True)
        return -minimum-.03*median['f1']-.005*np.mean(list(median.values()))
    optimizer=differential_evolution(objective,[(-4.,4.)]*5+[(0.,3.)]*5+[(0.,1.)],seed=20261023,
        x0=x0,maxiter=20,popsize=6,polish=False,workers=1,updating='immediate')
    best=max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
    write_json(OUT/'optimized_AR_search.json',{'test_used_for_selection':False,'normal_fit_only':True,
        'candidates':records,'best':best,'optimizer_message':str(optimizer.message)})
    print('Selected development candidate, no new test opened',best,flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
