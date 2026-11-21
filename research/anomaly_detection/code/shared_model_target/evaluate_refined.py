"""Evaluate frozen LOF refinement on verified, unchanged current-minute features."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats

PARENT=ROOT/'reports/shared_model_target_current'
OUTPUT=ROOT/'reports/shared_model_target_lof'


def assert_same_detector(left,right):
    # sklearn tree node records include unused padding bytes; compare named fields,
    # not pickle/joblib hashes that can vary after an unchanged save/load cycle.
    assert left.threshold==right.threshold
    assert joblib.hash(left.reference)==joblib.hash(right.reference)
    for key,value in left.base.__dict__.items():
        if key!='estimator':assert joblib.hash(value)==joblib.hash(right.base.__dict__[key]),key
    if left.algorithm!='if':
        assert joblib.hash(left.estimator)==joblib.hash(right.estimator)
        return
    for key,value in left.estimator.__dict__.items():
        if key!='estimators_':assert joblib.hash(value)==joblib.hash(right.estimator.__dict__[key]),key
    assert len(left.estimator.estimators_)==len(right.estimator.estimators_)
    for a,b in zip(left.estimator.estimators_,right.estimator.estimators_):
        for key,value in a.__dict__.items():
            if key!='tree_':assert joblib.hash(value)==joblib.hash(b.__dict__[key]),key
        x,y=a.tree_.__getstate__(),b.tree_.__getstate__()
        for key,value in x.items():
            if key=='nodes':
                for field in value.dtype.names:np.testing.assert_array_equal(value[field],y[key][field])
            else:np.testing.assert_array_equal(value,y[key])


def evaluate():
    selected=read(OUTPUT/'selection.json');parent_selected=read(PARENT/'selection.json')
    assert sha256(OUTPUT/'models.joblib')==selected['model_sha256']
    assert sha256(PARENT/'models.joblib')==parent_selected['model_sha256']
    pkg=joblib.load(OUTPUT/'models.joblib');old_pkg=joblib.load(PARENT/'models.joblib')
    assert joblib.hash(pkg['adapter_reference'])==joblib.hash(old_pkg['adapter_reference'])
    for a in ('if','kmeans'):
        assert_same_detector(pkg['models'][a],old_pkg['models'][a])
    digest=selected['benchmark_manifest_sha256']
    assert sha256(ROOT/'data/evaluation/manifest.json')==digest
    cache=PARENT/'feature_cache'/digest[:16]
    protocol=read(cache/'protocol.json')
    expected_hash=sha256(ROOT/'code/shared_model_target/features.py')+'_'+sha256(ROOT/'code/shared_model_target/current_features.py')
    assert protocol['feature_source_sha256']==expected_hash
    assert protocol['benchmark_sha256']==digest
    assert protocol['reference_model_sha256']==read(OUTPUT/'preparation.json')['parent_model_sha256']
    all_y,all_scores,datasets,samples=[],[],[],[]
    for name in read(ROOT/'data/evaluation/manifest.json')['datasets']:
        old=np.load(PARENT/(name+'_scores.npz'),allow_pickle=False)
        scores=old['scores'].copy();offset=0
        for i,entry in enumerate(read(cache/name/'index.json')):
            path=cache/name/entry['file'];assert sha256(path)==entry['sha256']
            payload=joblib.load(path);f=payload['frame'];meta=payload['metadata'];n=len(f)
            sl=slice(offset,offset+n)
            np.testing.assert_array_equal(old['y'][sl],np.asarray(meta['truth'],bool))
            np.testing.assert_array_equal(old['case_index'][sl],meta['case_index'])
            np.testing.assert_array_equal(old['adapter'][sl],f.adapterType.to_numpy(str))
            if i==0:
                for j,a in enumerate(('if','kmeans')):
                    np.testing.assert_array_equal(scores[offset:offset+80,j],pkg['models'][a].score(f.iloc[:80]))
            scores[sl,2]=pkg['models']['lof'].score(f)
            ids=np.random.default_rng(4242+i).choice(n,min(n,100),replace=False)
            sample=f.iloc[ids].copy();sample['actual_anomaly']=old['y'][sl][ids];sample['dataset']=name
            for j,a in enumerate(ALGORITHMS):sample[a+'_score']=scores[sl,j][ids]
            samples.append(sample);offset+=n
        assert offset==len(old['y'])
        y=old['y'];np.savez_compressed(OUTPUT/(name+'_scores.npz'),y=y,scores=scores,case_index=old['case_index'],adapter=old['adapter'])
        rows=[{'method':a,**_stats(y,scores[:,j],pkg['models'][a].threshold),
               'average_precision':float(average_precision_score(y,scores[:,j])),
               'roc_auc':float(roc_auc_score(y,scores[:,j]))}for j,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':rows});all_y.append(y);all_scores.append(scores)
        write(OUTPUT/'evaluation_progress.json',{'finished':[d['dataset']for d in datasets],'metrics':rows})
        print(name,[(r['method'],round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,2))for r in rows],flush=True)
    y,s=np.concatenate(all_y),np.concatenate(all_scores)
    rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
           'average_precision':float(average_precision_score(y,s[:,j])),
           'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
    target={'if':.4,'kmeans':.5,'lof':.5}
    summary={'pooled':rows,'datasets':datasets,
       'targets_met':{r['method']:r['f1']>=target[r['method']]and r['normal_false_positive_rate']<.1 for r in rows},
       'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),
          'benchmark_modified':False,'point_adjustment':False,'single_estimator_per_algorithm':True,
          'adapter_experts':False,'adapter_features':9,'not_external_blind_test':True,
          'feature_source_sha256':expected_hash,'readonly_feature_cache':str(cache.relative_to(ROOT)),
          'IF_and_kmeans_identical_models_and_scores':True,'same_reference_tables_verified':True}}
    write(OUTPUT/'summary.json',summary)
    joblib.dump(pd.concat(samples,ignore_index=True),OUTPUT/'analysis_samples.joblib',compress=3)
    print('Full benchmark',rows,flush=True)


if __name__=='__main__':evaluate()
