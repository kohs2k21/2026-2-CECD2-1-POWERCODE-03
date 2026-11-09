"""Development search of current-hour density ratios, all old files immutable."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import argparse
import hashlib
import os
import numpy as np
import pandas as pd
from ..common import ROOT,REPORT,DATA,read_json,write_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import prepare,ratio_features,mix_score

OUT=REPORT/'strict_refinement'


def initialize():
    OUT.mkdir(exist_ok=True);(OUT/'cache').mkdir(exist_ok=True)
    path=OUT/'preservation.json'
    if path.exists():
        for name,h in read_json(path)['files'].items():assert sha256(ROOT/name)==h,name
        return
    files=read_json(REPORT/'strict_intervals/preservation.json')['files']
    for folder in (ROOT/'code/long_term_anomaly/strict_intervals',REPORT/'strict_intervals'):
        files.update({p.relative_to(ROOT).as_posix():sha256(p) for p in folder.rglob('*')
                      if p.is_file() and 'cache' not in p.parts and '__pycache__' not in p.parts})
    write_json(path,{'files':files})
    write_json(OUT/'protocol.json',{'goal_median_per_length':.65,'maximum_fpr':.06,
        'one_hour_only_monitored_input':True,'outside_monitoring_history':False,
        'normal_reference_hours':672,'normal_reference_is_reviewed_not_monitoring_state':True,
        'normal_distribution_transformations':'Uniform shift +/-0.25..4 normal SD, variance broadening .5/1/2 SD',
        'test_used_for_selection':False,'old_test_population_reused':True,'independent_blind_test':False,
        'benchmark_and_truth_unchanged':True})


def worker(payload):
    x,o,a,seasonal,trend=payload
    forecast,ref,slope,mid=prepare(x[:672],o[:672],a[:672],seasonal,trend)
    # Forecast offset is known from input timestamps, not an anomaly feature.
    current=x[672:]-forecast[np.arange(336)%24]-(np.arange(672,1008)-mid)[:,None]*slope
    return {bw:ratio_features(ref,current,o[672:],a[672:],bw) for bw in (.1,.25,.5)}


def bank(split,seasonal,trend):
    d=load_split(split,False)
    signature=hashlib.sha256((sha256(Path(__file__).with_name('ratio.py'))+sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'ratios_{split}_{seasonal}_{trend}_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return {bw:z[str(bw)] for bw in (.1,.25,.5)},d
    payloads=((x,o,a,seasonal,trend) for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']))
    with ProcessPoolExecutor(max_workers=min(6,os.cpu_count() or 2)) as p:
        values=list(p.map(worker,payloads,chunksize=4))
    result={bw:np.asarray([r[bw] for r in values]) for bw in (.1,.25,.5)}
    np.savez_compressed(path,**{str(k):v for k,v in result.items()})
    print('Normal-reference ratios',split,seasonal,trend,flush=True)
    return result,d


def search():
    initialize();d=load_split('development',True);y=d['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    availability=d['latency_available'][:,672:].ravel();rng=np.random.default_rng(20261019)
    weights=[np.array(w,float) for w in ((1,1,1,1,1,1),(1,1,1,3,.1,1),(1,1,1,1,.1,1),
                                        (1,3,1,3,.1,1),(1,3,1,1,.1,1),(1,1,3,1,.1,3))]
    weights.extend(np.r_[1.,np.exp(rng.uniform(-3.,3.,5))] for _ in range(100))
    rows=[]
    for seasonal,trend in ((1.,0.),(.5,0.),(0.,0.),(.5,.5)):
        fs,_=bank('development',seasonal,trend)
        for bw,f in fs.items():
            flat=f.reshape(-1,6)
            valid=np.isfinite(f).any(axis=2)
            for w in weights:
                for mode in ('mixture','maximum'):
                    s=mix_score(flat,w,availability,mode).reshape(y.shape)
                    for cap in (.05,.055):
                        t=max(float(np.quantile(s[m&valid],1-cap,method='higher')) for m in normal)
                        r=compact_metrics(y,s,t,groups)
                        rows.append(dict(seasonal_strength=seasonal,trend_strength=trend,bandwidth=bw,
                            weights=w.tolist(),mode=mode,cap=cap,threshold=t,**r['median'],
                            max_fpr=float(r['maximum_scenario_fpr'])))
            best=max(rows,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
            print('Current-hour ratio candidate',best,flush=True)
        write_json(OUT/'development_search.json',rows)
    pd.DataFrame([{k:v for k,v in r.items() if k!='weights'} for r in rows]).to_csv(OUT/'development_search.csv',index=False)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
