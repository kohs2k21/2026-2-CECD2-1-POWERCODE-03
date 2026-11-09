"""Normal-only noise discrimination using normal AR evidence as input.

Normal training trajectories are the only empirical fit population. Artificial
query perturbations are generic independent draws, never benchmark truth. This
adapts a data-vs-noise discrimination idea; it is not the exact NCE estimator.
"""
import argparse
import hashlib
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from ..benchmark import load_split
from ..common import DATA, read_json, write_json, sha256
from ..experiment import metadata_for_split
from ..strict_intervals.moments import compact_metrics
from .experiment import OUT, initialize
from .model import load_bank
from .point_diagnostic import features_for


def features(split, augment=False):
    signature=hashlib.sha256((sha256(__file__)+sha256(OUT/'models.joblib')+
        sha256(DATA/f'{split}_inputs.npz')).encode()).hexdigest()[:16]
    path=OUT/'cache'/f'ar_contrastive_{split}_{augment}_{signature}.npz'
    if path.exists():
        with np.load(path) as z:return {k:z[k] for k in z.files}
    d=load_split(split,False);point=load_bank(OUT/'models.joblib').point
    rng=np.random.default_rng(20261101);xs=[];labels=[]
    for i,(x,o,a) in enumerate(zip(d['X'],d['collection_observed'],d['latency_available'])):
        values=[features_for(point,x,o,a)]
        if augment:
            ref=point.prepare_reference(x[:672],o[:672],a[:672])
            for replica in range(8):
                noise=x.copy();kind=rng.integers(0,5,len(x)-672)
                effect=np.zeros((len(kind),2))
                amount=rng.uniform(.15,3.,len(kind))
                effect[kind==0,0]=amount[kind==0]
                effect[kind==1,0]=-amount[kind==1]
                effect[kind==2,1]=amount[kind==2]
                effect[kind==3]=amount[kind==3,None]*np.array([1.,1.])
                effect[kind==4]=rng.normal(size=((kind==4).sum(),2))*amount[kind==4,None]
                noise[672:,:2]=np.maximum(noise[672:,:2]+effect*ref.scale,0.)
                values.append(features_for(point,noise,o,a))
        xs.append(np.concatenate(values) if augment else values[0])
        if augment:labels.append(np.r_[np.zeros(len(values[0]),int),np.ones(8*len(values[0]),int)])
        if (i+1)%64==0:print('AR contrastive features',split,i+1,flush=True)
    if augment:
        f=np.concatenate(xs);label=np.concatenate(labels);valid=np.isfinite(f).any(axis=1)
        result=dict(features=f[valid],labels=label[valid])
    else:result=dict(features=np.asarray(xs))
    np.savez_compressed(path,**result);return result


def search():
    initialize();path=OUT/'ar_contrastive_search.json'
    if path.exists():raise FileExistsError('Preserve completed experiment')
    train=features('train',True);cal=features('calibration');dev=features('development')
    d=load_split('development',True);y=d['y'];observed=d['collection_observed'][:,672:]
    groups=metadata_for_split('development',d['case_id']).scenario.to_numpy()
    masks=[(groups==g)[:,None]&~y&observed for g in np.unique(groups)]
    f=dev['features'];c=cal['features'];rows=[]
    for leaves,regularization in ((7,100.),(15,100.),(31,100.),(31,10.)):
        model=HistGradientBoostingClassifier(max_leaf_nodes=leaves,max_iter=240,
            min_samples_leaf=150,l2_regularization=regularization,learning_rate=.06,
            early_stopping=False,random_state=20261101)
        model.fit(train['features'],train['labels'],sample_weight=np.where(train['labels']==0,8.,1.))
        score=model.predict_proba(f.reshape(-1,f.shape[-1]))[:,1].reshape(y.shape);score[~observed]=np.nan
        cv=np.isfinite(c).any(axis=2);cs=model.predict_proba(c[cv])[:,1]
        t=max(float(np.quantile(cs,.941)),*(float(np.quantile(score[m],.941,method='higher')) for m in masks))
        r=compact_metrics(y,score,t,groups)
        row=dict(leaves=leaves,l2=regularization,threshold=t,**r['median'],
                 max_fpr=float(r['maximum_scenario_fpr']),scenarios=r['scenarios'])
        rows.append(row)
        joblib.dump(model,OUT/f'ar_contrastive_candidate_{leaves}_{regularization:g}.joblib',compress=3)
        print('AR-feature normal-only noise discrimination',row,flush=True)
    write_json(path,dict(normal_empirical_fit_only=True,benchmark_anomaly_labels_used_for_fit=False,
        development_labels_used_for_setting_selection=True,normal_reference_hours=672,
        normal_training_query_hours=336,normal_training_cases=64,noise_replications=8,
        monitoring_hours_per_decision=1,test_used=False,candidates=rows,
        source='https://proceedings.mlr.press/v9/gutmann10a.html',source_sha256=sha256(__file__)))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();search()
