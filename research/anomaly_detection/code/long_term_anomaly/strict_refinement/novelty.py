"""Normal-only local density and one-class boundary checks for current-hour logs."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
import pandas as pd
from sklearn.neighbors import LocalOutlierFactor
from sklearn.svm import OneClassSVM
from sklearn.ensemble import IsolationForest
from ..benchmark import load_split
from ..common import DATA,write_json,sha256
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import prepare,mix_score
from .experiment import OUT,initialize,bank as ratio_bank

SPECS=[('lof',space,k) for space in ('requests','duration','joint') for k in (8,24,64)]
SPECS += [('ocsvm',space,gamma) for space in ('requests','duration','joint') for gamma in (.25,1.,4.)]
SPECS += [('if','joint',128)]
NAMES=tuple(f'{method}:{space}:{parameter}' for method,space,parameter in SPECS)


def worker(payload):
    x,o,a,seasonal=payload
    forecast,ref,_,_=prepare(x[:672],o[:672],a[:672],seasonal,0.)
    current=x[672:,:2]-forecast[np.arange(336)%24,:2]
    ref=ref[:,:2];scale=np.maximum(np.std(ref,axis=0),.03)
    ref=ref/scale;current=np.nan_to_num(current/scale,nan=0.)
    ref=ref[(o[:672]&a[:672])]
    columns=[]
    for method,space,parameter in SPECS:
        cols=[0] if space=='requests' else [1] if space=='duration' else [0,1]
        normal,query=ref[:,cols],current[:,cols]
        if method=='lof':
            model=LocalOutlierFactor(n_neighbors=parameter,novelty=True,n_jobs=1).fit(normal)
            # Training calibration uses the explicit leave-one-out factors,
            # never novelty score_samples(X_train) which includes self effects.
            train=-model.negative_outlier_factor_;score=-model.score_samples(query)
        elif method=='ocsvm':
            model=OneClassSVM(nu=.1,gamma=parameter).fit(normal)
            train=-model.score_samples(normal);score=-model.score_samples(query)
        else:
            model=IsolationForest(n_estimators=parameter,max_samples=256,random_state=20261019,n_jobs=1).fit(normal)
            train=-model.score_samples(normal);score=-model.score_samples(query)
        center,tail=np.quantile(train,[.5,.95])
        z=(score-center)/max(tail-center,.05)
        if space!='requests':z[~a[672:]]=0.
        z[~o[672:]]=np.nan
        columns.append(z)
    return np.column_stack(columns)


def bank(split,seasonal):
    d=load_split(split)
    sig=hashlib.sha256((sha256(Path(__file__))+sha256(Path(__file__).with_name('ratio.py'))
                        +sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'novelty_{split}_{seasonal}_{sig}.npz'
    if path.exists():
        with np.load(path) as f:return f['features'],d
    payloads=((x,o,a,seasonal) for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']))
    with ProcessPoolExecutor(max_workers=min(6,os.cpu_count() or 2)) as p:
        result=np.asarray(list(p.map(worker,payloads,chunksize=4)))
    np.savez_compressed(path,features=result)
    print('Novelty boundaries',split,seasonal,result.shape,flush=True)
    return result,d


def search():
    initialize();d=load_split('development',True);y=d['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    rows=[]
    for seasonal in (.5,1.):
        f,_=bank('development',seasonal);valid=np.isfinite(f).any(axis=2)
        raw,_=ratio_bank('development',seasonal,0.)
        base=mix_score(raw[.1].reshape(-1,6),np.array([1.,17.411144939,.340323514,7.149344923,.092906181,.35489114]),
                       d['latency_available'][:,672:].ravel(),'maximum').reshape(y.shape)
        candidates=[(name,f[:,:,i]) for i,name in enumerate(NAMES)]
        for i,name in enumerate(NAMES):
            for weight in (.25,.5,1.,2.):
                candidates.append((name+f':ratio_max_{weight}',np.maximum(f[:,:,i]*weight,base)))
                candidates.append((name+f':ratio_sum_{weight}',np.maximum(f[:,:,i],0.)*weight+np.maximum(base,0.)))
        for method in ('lof','ocsvm'):
            parameters=(8,24,64) if method=='lof' else (.25,1.,4.)
            for p in parameters:
                indices=[NAMES.index(f'{method}:{space}:{p}') for space in ('requests','duration','joint')]
                for duration_weight in (.25,.5,1.,2.,4.):
                    for joint_weight in (0.,.5,1.):
                        score=np.max(f[:,:,indices]*np.asarray([1.,duration_weight,joint_weight]),axis=2)
                        candidates.append((f'{method}:{p}:duration_{duration_weight}:joint_{joint_weight}',score))
        for name,score in candidates:
            for cap in (.05,.055):
                t=max(np.quantile(score[m&valid],1-cap,method='higher') for m in normal)
                r=compact_metrics(y,score,float(t),groups)
                rows.append(dict(seasonal_strength=seasonal,method=name,threshold=float(t),cap=cap,
                                 **r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
        best=max(rows,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
        print('Novelty candidate',best,flush=True)
        write_json(OUT/'novelty_development_search.json',rows)
    pd.DataFrame(rows).to_csv(OUT/'novelty_development_search.csv',index=False)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
