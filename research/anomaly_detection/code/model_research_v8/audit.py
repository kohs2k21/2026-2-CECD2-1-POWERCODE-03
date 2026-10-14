"""Recount labels, source overlap, branch fingerprints and paired-case CIs."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.data import sha256,verify_benchmark
from model_evaluation.metrics import Counts,metric_record
from model_evaluation.tune_windows import write_json
from model_improvement.data import BENCHMARK,SOURCE_CACHE
from model_research_v8.recalibrate import frozen,load,OUTPUT
from model_research_v8.bridge import frozen as old_bridge
from model_research_v8.compat import legacy_classes


def metrics(values):
    tn,fp,fn,tp=np.moveaxis(values,-1,0)
    return np.stack([(tn+tp)/(tn+fp+fn+tp),tp/np.maximum(tp+fp,1),tp/np.maximum(tp+fn,1),2*tp/np.maximum(2*tp+fp+fn,1),fp/np.maximum(tn+fp,1)],axis=-1)


def equal_forests(a,b):
    # Tree node structured-array padding is not a semantic model parameter and
    # can differ across pickle/deepcopy. Compare actual learned arrays instead.
    assert a.settings==b.settings
    assert len(a.models)==len(b.models)
    for x,y in zip(a.rotations,b.rotations):np.testing.assert_array_equal(x,y)
    for x,y in zip(a.models,b.models):
        assert x.get_params()==y.get_params()
        assert len(x.estimators_)==len(y.estimators_)
        for xx,yy in zip(x.estimators_features_,y.estimators_features_):np.testing.assert_array_equal(xx,yy)
        for xx,yy in zip(x.estimators_,y.estimators_):
            for field in ('children_left','children_right','feature','threshold','value','impurity','n_node_samples','weighted_n_node_samples'):
                np.testing.assert_array_equal(getattr(xx.tree_,field),getattr(yy.tree_,field))


def main():
    verify_benchmark(BENCHMARK,SOURCE_CACHE);model,selection=frozen()
    with legacy_classes():old,_=old_bridge()
    data=load()
    for a,f in model.families.items():
        prior=old.families[a]
        if a=='kmeans':
            assert joblib.hash(f.base)==joblib.hash(prior.base)
            assert joblib.hash(f.global_model)==joblib.hash(prior.global_model)
            for adapter in f.experts:assert joblib.hash(f.experts[adapter])==joblib.hash(prior.experts[adapter])
        else:
            for new_detector,old_detector in [(f.base.global_model,prior.base.global_model),
                                              *[(f.base.experts[k],prior.base.experts[k]) for k in f.base.experts]]:
                assert joblib.hash(new_detector.encoder)==joblib.hash(old_detector.encoder)
                equal_forests(new_detector.raw_model,old_detector.raw_model)
            for new_latent,old_latent in [(f.global_model,prior.global_model),*[(f.experts[k],prior.experts[k]) for k in f.experts]]:
                assert joblib.hash(new_latent.encoder)==joblib.hash(old_latent.encoder)
                equal_forests(new_latent.model,old_latent.model)
            for key,value in f.base.__dict__.items():
                if key not in ('global_model','experts'):assert joblib.hash(value)==joblib.hash(prior.base.__dict__[key])
    summary=json.loads((OUTPUT/'summary.json').read_text(encoding='utf-8'))
    counters=Counts();grouped={};overlap=np.zeros((2,4),np.int64);blocks=[];fallback=0;model_stats={}
    for dataset in summary['datasets']:
        name=dataset['dataset'];ncase=dataset['case_count'];paired=np.zeros((ncase,2,6,4),np.int64)
        for cohort,digest in dataset['arrays_sha256'].items():
            path=OUTPUT/name/f'{cohort}.npz';assert sha256(path)==digest
            with np.load(path,allow_pickle=False) as a,np.load(ROOT/'reports/model_improvement_v4'/name/f'{cohort}.npz',allow_pickle=False) as v4,np.load(ROOT/'reports/model_research_v7'/name/f'{cohort}.npz',allow_pickle=False) as v7:
                for key in ('truth','case_index','window_end_seconds','scenario_bits','adapter','process'):
                    np.testing.assert_array_equal(a[key],v4[key]);np.testing.assert_array_equal(a[key],v7[key])
                y,p=a['truth'],a['prediction'];np.testing.assert_array_equal(p,a['margin']>0)
                counters.update(y,p);unsupported=~a['covered'];fallback+=int(unsupported.sum())
                for start in (0,3):
                    np.testing.assert_array_equal(a['margin'][unsupported,start],a['margin'][unsupported,start+1]);np.testing.assert_array_equal(a['margin'][unsupported,start],a['margin'][unsupported,start+2])
                for field in ('duration_reference_level','traffic_assessed','latency_assessed','phase'):
                    for value in np.unique(a[field]):
                        key=f'{field}={value}';c=grouped.setdefault(key,Counts());c.update(y,p,a[field]==value)
                flags=[p[:,2]&~p[:,5],~p[:,2]&p[:,5],p[:,2]&p[:,5],~p[:,2]&~p[:,5]]
                for truth in (0,1):
                    for i,mask in enumerate(flags):overlap[truth,i]+=int(np.sum(mask&(y==truth)))
                baseline=np.column_stack([v7['prediction'][:,:3],v4['prediction'][:,3:]])
                for vi,pred in enumerate((baseline,p)):
                    for mi,z in enumerate(pred.T):
                        for ci,mask in enumerate((~y&~z,~y&z,y&~z,y&z)):
                            paired[:,vi,mi,ci]+=np.bincount(a['case_index'][mask],minlength=ncase)
        blocks.append(paired)
    for recomputed,stored in zip(counters.records(),summary['pooled']):
        for k in ('tn','fp','fn','tp','n'):assert recomputed[k]==stored[k]
        for k in ('accuracy','precision','recall','f1','normal_false_positive_rate'):assert abs(recomputed[k]-stored[k])<1e-12
    # Stratified by scenario; observed/control pair and all minutes resampled
    # together. This captures generator-case sampling, NOT production uncertainty.
    rng=np.random.default_rng(20261013);samples=[]
    for _ in range(400):
        totals=sum(b[rng.integers(0,len(b),len(b))].sum(axis=0) for b in blocks)
        samples.append(metrics(totals)[1]-metrics(totals)[0])
    bounds=np.quantile(np.asarray(samples),[.025,.975],axis=0)
    names=('accuracy','precision','recall','f1','normal_false_positive_rate')
    ci=[{'method':r['method'],'baseline':'v7 IF / v4 KMeans',**{k:{'lower_pp':float(bounds[0,i,j]*100),'upper_pp':float(bounds[1,i,j]*100)} for j,k in enumerate(names)}}
        for i,r in enumerate(summary['pooled'])]
    for version,folder in (('v8','model_research_v8'),('v9','model_research_v9'),('v10','model_research_v10'),('v11','model_research_v11')):
        p=ROOT/'reports'/folder/f'models_{version}.joblib';model_stats[version]={'bytes':p.stat().st_size,'sha256':sha256(p)}
    normal_ids=set(data['calibration'].case_id)|set(data['threshold'].case_id)
    validation_ids=set().union(*(set(d['observed'].case_id) for d in data['bank'].values()))
    assert not normal_ids&validation_ids
    result={'decisions':summary['pooled'][0]['n'],'normal':summary['pooled'][0]['normal'],'anomaly':summary['pooled'][0]['anomaly'],
            'fallback_decisions':fallback,'exact_fallback':True,'labels_times_case_indices_equal_v4_v7':True,'metrics_recounted':True,
            'base_and_conditional_trees_centers_unchanged_from_v10':True,'normal_calibration_cases_disjoint_from_v11_validation':True,
            'threshold_calibration_source_templates_shared_with_original_test':True,'not_blind_external_evaluation':True,
            'group_metrics':{k:c.records() for k,c in grouped.items()},'models':model_stats,'paired_case_bootstrap':ci,
            'bootstrap_protocol':{'repetitions':400,'seed':20261013,'stratified_by_scenario':True,'normal_observed_pair_kept':True,
                'limitation':'Conditional synthetic case sampling uncertainty only; shared source templates/adaptive research not captured.'},
            'alert_overlap':[{'truth':bool(t),'category':category,'n':int(overlap[t,i]),'fraction':float(overlap[t,i]/overlap[t].sum())}
                             for t in (0,1) for i,category in enumerate(('IF only','KMeans only','both','neither'))]}
    write_json(OUTPUT/'audit.json',result);verify_benchmark(BENCHMARK,SOURCE_CACHE)
    print(json.dumps({k:result[k] for k in result if k not in ('group_metrics','paired_case_bootstrap','alert_overlap')},indent=2))


if __name__=='__main__':main()
