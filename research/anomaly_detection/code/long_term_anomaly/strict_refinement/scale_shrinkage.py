"""Empirical normal-scale shrinkage, with no extra monitoring information."""
from dataclasses import replace
import argparse
import numpy as np
from ..benchmark import load_split
from ..common import read_json,write_json
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .model import SharedARPoint,evidence_score,load_bank
from .shared_ar import context_parameters
from .experiment import OUT,initialize


class ScaleShrunkPoint(SharedARPoint):
    def prepare_reference(self,x,observed,available):
        ref=super().prepare_reference(x,observed,available)
        alpha=np.asarray(self.config['scale_shrinkage'],float)
        prior=np.asarray(self.config['normal_scale_prior'],float)
        scale=np.sqrt(alpha*ref.scale**2+(1-alpha)*prior**2)
        return replace(ref,scale=scale)


def learn_prior():
    d=load_split('train',False)
    scales=np.asarray([context_parameters(x[:672],o[:672],a[:672])[2]
                       for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available'])])
    return np.median(scales,axis=0)


def search():
    initialize();prior=learn_prior();base=load_bank(OUT/'models.joblib').point
    d=load_split('development',False);y=load_split('development',True)['y']
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    normal=[(groups==g)[:,None]&~y for g in np.unique(groups)]
    prepared=[]
    for x,o,a in zip(d['X'],d['collection_observed'],d['latency_available']):
        prepared.append(base.prepare_reference(x[:672],o[:672],a[:672]))
    records=[]
    for request_alpha in (0.,.25,.5,.75,1.):
        for duration_alpha in (0.,.25,.5,.75,1.):
            config={**base.config,'scale_shrinkage':[request_alpha,duration_alpha],'normal_scale_prior':prior.tolist()}
            model=ScaleShrunkPoint(base.grid,base.buckets,base.curves,config)
            scores=[]
            for ref,x,o,a in zip(prepared,d['X'],d['collection_observed'],d['latency_available']):
                scale=np.sqrt(np.asarray(config['scale_shrinkage'])*ref.scale**2+
                              (1-np.asarray(config['scale_shrinkage']))*prior**2)
                ref=replace(ref,scale=scale)
                scores.append(model.score_arrays(ref,x[672:],o[672:],a[672:])[0])
            score=np.asarray(scores);valid=np.isfinite(score)
            for cap in (.05,.055,.059):
                t=max(float(np.quantile(score[m&valid],1-cap,method='higher')) for m in normal)
                r=compact_metrics(y,score,t,groups)
                records.append(dict(scale_shrinkage=[request_alpha,duration_alpha],normal_scale_prior=prior.tolist(),
                    threshold=t,cap=cap,**r['median'],max_fpr=float(r['maximum_scenario_fpr'])))
        best=max(records,key=lambda r:min(r[k] for k in ('accuracy','precision','recall','f1')))
        print('Normal-scale shrinkage candidate',best,flush=True)
    write_json(OUT/'scale_shrinkage_search.json',{'normal_fit_only':True,'test_used':False,
        'best':max(records,key=lambda r:(min(r[k] for k in ('accuracy','precision','recall','f1')),r['f1'])),
        'candidates':records})


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
