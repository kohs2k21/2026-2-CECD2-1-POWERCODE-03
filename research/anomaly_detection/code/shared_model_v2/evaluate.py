"""Full unchanged benchmark using readonly verified parent feature caches."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_improvement.run import ALGORITHMS,_stats
from shared_model_v2.features import augment


def evaluate(folder,reuse=None):
    folder=Path(folder).resolve();selected=read(folder/'selection.json')
    assert sha256(folder/'models.joblib')==selected['model_sha256']
    pkg=joblib.load(folder/'models.joblib');digest=selected['benchmark_manifest_sha256']
    reuse=Path(reuse).resolve()if reuse is not None else None
    if reuse is not None:
        from shared_model_target.evaluate_refined import assert_same_detector
        prior_selection=read(reuse/'selection.json')
        assert sha256(reuse/'models.joblib')==prior_selection['model_sha256']
        prior=joblib.load(reuse/'models.joblib')
        for a in('if','kmeans'):assert_same_detector(pkg['models'][a],prior['models'][a])
    assert sha256(ROOT/'data/evaluation/manifest.json')==digest
    parent=ROOT/'reports/shared_model_target_current/feature_cache'/digest[:16]
    protocol=read(parent/'protocol.json')
    assert protocol['feature_source_sha256']==sha256(ROOT/'code/shared_model_target/features.py')+'_'+sha256(ROOT/'code/shared_model_target/current_features.py')
    cache=ROOT/'reports/shared_model_v2/feature_cache'/digest[:16];cache.mkdir(parents=True,exist_ok=True)
    expected={'parent_protocol':protocol,'memory_source_sha256':sha256(ROOT/'code/shared_model_v2/features.py')}
    if(cache/'protocol.json').exists():assert read(cache/'protocol.json')==expected
    else:write(cache/'protocol.json',expected)
    all_y,all_s,datasets,samples=[],[],[],[]
    for name in read(ROOT/'data/evaluation/manifest.json')['datasets']:
        ys,ss,indices,adapters=[],[],[],[];dest=cache/name;dest.mkdir(exist_ok=True);entries=[]
        old=np.load(reuse/(name+'_scores.npz'))if reuse is not None else None
        offset=0;cohort_cases={}
        for i,e in enumerate(read(parent/name/'index.json')):
            path=parent/name/e['file'];assert sha256(path)==e['sha256']
            cached=dest/e['file']
            if cached.exists():
                assert sha256(cached)==read(cached.with_suffix('.json'))['sha256'];payload=joblib.load(cached)
            else:
                source=joblib.load(path);payload={'frame':augment(source['frame']),'metadata':source['metadata']}
                joblib.dump(payload,cached,compress=3);write(cached.with_suffix('.json'),{'sha256':sha256(cached),'parent_sha256':e['sha256']})
            f,meta=payload['frame'],payload['metadata'];y=np.asarray(meta['truth'],bool)
            known=cohort_cases.setdefault(e['cohort'],set());case_ids=set(f.case_id)
            if known&case_ids:raise ValueError('Causal score state would span feature-cache chunks')
            known.update(case_ids)
            if old is None:s=np.column_stack([pkg['models'][a].score(f)for a in ALGORITHMS])
            else:
                sl=slice(offset,offset+len(f));np.testing.assert_array_equal(old['y'][sl],y)
                np.testing.assert_array_equal(old['case_index'][sl],meta['case_index'])
                np.testing.assert_array_equal(old['adapter'][sl],f.adapterType.to_numpy(str))
                s=old['scores'][sl].copy();s[:,2]=pkg['models']['lof'].score(f)
            offset+=len(f)
            ys.append(y);ss.append(s);indices.append(meta['case_index']);adapters.append(f.adapterType.to_numpy(str))
            ids=np.random.default_rng(4242+i).choice(len(f),min(100,len(f)),replace=False)
            sample=f.iloc[ids].copy();sample['actual_anomaly']=y[ids];sample['dataset']=name
            for j,a in enumerate(ALGORITHMS):sample[a+'_score']=s[ids,j]
            samples.append(sample);entries.append({'file':e['file'],'sha256':sha256(cached),'cohort':e['cohort'],'decisions':len(f)})
        write(dest/'index.json',entries);y,s=np.concatenate(ys),np.concatenate(ss)
        np.savez_compressed(folder/(name+'_scores.npz'),y=y,scores=s,case_index=np.concatenate(indices),adapter=np.concatenate(adapters))
        rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
           'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
        datasets.append({'dataset':name,'metrics':rows});all_y.append(y);all_s.append(s)
        write(folder/'evaluation_progress.json',{'finished':[d['dataset']for d in datasets],'metrics':rows})
        print(name,[(r['method'],round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,2))for r in rows],flush=True)
    y,s=np.concatenate(all_y),np.concatenate(all_s)
    rows=[{'method':a,**_stats(y,s[:,j],pkg['models'][a].threshold),
       'average_precision':float(average_precision_score(y,s[:,j])),'roc_auc':float(roc_auc_score(y,s[:,j]))}for j,a in enumerate(ALGORITHMS)]
    previous=read(ROOT/'reports/shared_model_target_lof/summary.json')['pooled']
    write(folder/'summary.json',{'pooled':rows,'datasets':datasets,
       'targets_met':{r['method']:r['f1']>=.55 and r['normal_false_positive_rate']<=previous[j]['normal_false_positive_rate']+.002 for j,r in enumerate(rows)},
       'protocol':{'decisions':len(y),'normal':int((~y).sum()),'anomaly':int(y.sum()),'original_benchmark_modified':False,
        'single_estimator_each':True,'adapter_experts':False,'point_adjustment':False,'no_external_blind_test':True,
        'feature_cache':str(cache.relative_to(ROOT)),'same_level_FPR_tolerance_absolute':.002}})
    joblib.dump(pd.concat(samples,ignore_index=True),folder/'analysis_samples.joblib',compress=3)
    print('Full benchmark',rows,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'reports/shared_model_v2');p.add_argument('--reuse',type=Path)
    args=p.parse_args();evaluate(args.output,args.reuse)
