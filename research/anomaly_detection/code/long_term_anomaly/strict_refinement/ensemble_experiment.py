"""Development-only shared/local density agreement; no test input access."""
import argparse
import numpy as np
from ..common import read_json,write_json
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .ratio import mix_score
from .shared_ar import bank as global_bank,BANDWIDTHS,RANGES
from .shift_ranges import bank as local_bank,RANGES as LOCAL_RANGES
from .optimize_shared import components
from .model import evidence_score
from .experiment import OUT,initialize


def local_components(features,config):
    f=features[0].copy()
    f[:,:,0]=features[LOCAL_RANGES.index(tuple(config['request_up']))][:,:,0]
    f[:,:,1]=features[LOCAL_RANGES.index(tuple(config['request_down']))][:,:,1]
    f[:,:,3]=features[LOCAL_RANGES.index(tuple(config['duration_up']))][:,:,3]
    return f


def combine(shared,local,method,weight):
    if method=='maximum':return np.maximum(shared,local*weight)
    if method=='agreement':return np.minimum(shared,local*weight)
    if method=='average':return (1-weight)*shared+weight*local
    raise ValueError('Unknown ensemble method')


def search():
    initialize();fg,d=global_bank('development');fl,dl=local_bank('development')
    np.testing.assert_array_equal(d['case_id'],dl['case_id'])
    truth=load_split('development',True)['y'];groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normals=[(groups==g)[:,None]&~truth for g in np.unique(groups)];available=d['latency_available'][:,672:].ravel()
    g=read_json(OUT/'calibration.json')['record']['one_hour_config']
    indices=(BANDWIDTHS.index(g['bandwidth_requests']),BANDWIDTHS.index(g['bandwidth_duration']),
             RANGES.index(tuple(g['request_up'])),RANGES.index(tuple(g['request_down'])),RANGES.index(tuple(g['duration_up'])))
    sg=evidence_score(components(fg,indices).reshape(-1,6),g['weights'],available,g['temperature']).reshape(truth.shape)/g['threshold']
    valid=np.isfinite(sg);rows=[]
    candidates=read_json(OUT/'shift_range_search.json')
    candidates=sorted(candidates,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']),reverse=True)
    local_configs=[];seen=set()
    for candidate in candidates:
        key=(tuple(candidate['request_up']),tuple(candidate['request_down']),tuple(candidate['duration_up']))
        if key not in seen:
            local_configs.append(candidate);seen.add(key)
        if len(local_configs)>=12:break
    for i,local in enumerate(local_configs):
        sl=mix_score(local_components(fl,local).reshape(-1,6),local['weights'],available,local['mode']).reshape(truth.shape)/local['threshold']
        for method in ('maximum','agreement','average'):
            weights=(.25,.5,.75,1.,1.25,1.5,2.,3.) if method!='average' else np.linspace(0,1,21)
            for weight in weights:
                score=combine(sg,sl,method,float(weight))
                for cap in (.05,.055,.059):
                    threshold=max(float(np.quantile(score[m&valid],1-cap,method='higher')) for m in normals)
                    r=compact_metrics(truth,score,threshold,groups)
                    rows.append(dict(local_index=i,local=local,method=method,weight=float(weight),cap=cap,
                        threshold=threshold,**r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
        best=max(rows,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1']))
        print('Shared + local development candidate', {k:v for k,v in best.items() if k!='local'},flush=True)
        write_json(OUT/'ensemble_development_search.json',{'global':g,'best':best,'candidates':rows,'test_used':False})


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
