"""Select input-length-specific channel weights using development data only."""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from ..benchmark import load_split
from ..common import read_json,write_json
from ..experiment import metadata_for_split
from .moments import OUT,HORIZONS,NAMES,bank,compact_metrics


def make_weights(median,energy,duration,lower_requests,lower_duration,density_requests,density_duration):
    result=[]
    for name in NAMES:
        stat,rest=name.split('_',1)
        direction,channel=rest.split(':')
        w={'mean':1.,'median':median,'std':energy}[stat]
        if channel=='duration':w*=duration
        if direction=='lower': w*= (lower_requests if channel=='requests' else lower_duration)
        if direction=='density':w*= (density_requests if channel=='requests' else density_duration)
        if stat=='std' and direction=='lower':w=0.
        result.append(w)
    return np.asarray(result)


def select():
    d=load_split('development',True);y=d['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    masks=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    rng=np.random.default_rng(20261019)
    parameters=[(m,e,dur,lo,lo,0.,0.) for m in (0.,1.) for e in (.5,1.) for dur in (.5,1.,2.) for lo in (.25,1.)]
    parameters += [tuple(np.exp(rng.uniform(low,high))) for low,high in
        [(np.array([-3.,-2.,-1.2,-3.,-3.,-4.,-4.]),np.array([1.,1.,1.5,1.,1.,2.,2.]))]*180]
    rows=[]
    for density in ('knn',.1,.25,.5):
        features,_=bank('development',density=density)
        for h in HORIZONS:
            f=features[h];valid=np.isfinite(f).any(axis=2);safe=np.nan_to_num(f,nan=0.)
            for params in parameters:
                w=make_weights(*params);s=np.max(safe*w,axis=2);s[~valid]=np.nan
                for cap in (.045,.05,.055):
                    t=max(np.quantile(s[m&valid],1-cap,method='higher') for m in masks)
                    r=compact_metrics(y,s,t,groups)
                    rows.append(dict(hours=h,density=density,parameters=list(params),weights=w.tolist(),
                        threshold=float(t),cap=cap,**r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
            best=max([r for r in rows if r['hours']==h],key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
            print('Channel + length candidate',best,flush=True)
        write_json(OUT/'moment_balance_search.json',rows)
    chosen={}
    for h in HORIZONS:
        candidates=[r for r in rows if r['hours']==h]
        successful=[r for r in candidates if min(r[k] for k in ('accuracy','precision','recall','f1'))>=.65]
        chosen[str(h)]=(min(successful,key=lambda r:(r['cap'],-min(r[k] for k in ('accuracy','precision','recall','f1'))))
                         if successful else max(candidates,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1'))))
    write_json(OUT/'development_selection.json',{'models':chosen,'selection_used_test':False,
        'goal_met_development_all_lengths':all(min(r[k] for k in ('accuracy','precision','recall','f1'))>=.65 for r in chosen.values())})
    pd.DataFrame([{k:v for k,v in r.items() if k not in ('weights','parameters')} for r in rows]).to_csv(OUT/'moment_balance_search.csv',index=False)
    print('Chosen only on development',chosen,flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    select()
