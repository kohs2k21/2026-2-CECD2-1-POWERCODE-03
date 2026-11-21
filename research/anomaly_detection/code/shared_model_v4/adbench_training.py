"""Separate ADBench-specific training, NOT service-model transfer.

Reconstruct the exact original official generator population and verify the
fixed 1,000 evaluation rows. Only its unused rows form train/cal/development.
No test vector (including a numerical duplicate) may enter those partitions.
This is a within-generator split, not an independent real-log benchmark.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import random,warnings,hashlib
from types import SimpleNamespace
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from shared_model_v2.adbench_eval import official_method,COLUMNS,FLAGS,prepare
from model_research_v13.data import read,write
from model_evaluation.data import sha256

OUT=ROOT/'reports/shared_model_v4_adbench_specific'

def fingerprint(x):return hashlib.sha256(np.ascontiguousarray(x,dtype=np.float64).tobytes()).hexdigest()

def build():
    OUT.mkdir(exist_ok=True);path=OUT/'development.joblib';fixed_path=ROOT/'data/adbench_1000/dataset.npz'
    if path.exists():
        audit=read(OUT/'preparation.json');assert sha256(path)==audit['development_sha256'];assert sha256(fixed_path)==audit['fixed_npz_sha256'];return joblib.load(path)
    fixed=np.load(fixed_path);fixed_ids={str(k):i for i,k in enumerate(fixed['sample_id'])};fixed_hashes={fingerprint(x)for x in fixed['X']}
    source=prepare()['train'];source=source.loc[source[list(FLAGS)].astype(bool).all(axis=1)].reset_index(drop=True)
    cols=[c for c in COLUMNS if c not in FLAGS];x=source[cols].to_numpy(float)
    median=np.nan_to_num(np.nanmedian(x,axis=0));x=np.where(np.isnan(x),median,x)
    q10,q90=np.quantile(x,[.1,.9],axis=0);scale=np.maximum(q90-q10,.5);variable=np.ptp(x,axis=0)>1e-9
    ids=np.random.default_rng(801000).choice(len(x),min(1600,len(x)),replace=False)
    normalized=((x[ids]-median)/scale)[:,variable]+np.random.default_rng(801001).normal(0,1e-5,(len(ids),int(variable.sum())))
    method,provenance=official_method();parts={k:[]for k in ('train','calibration','development')};audits=[]
    for mi,mode in enumerate(('global','local','cluster','dependency')):
        seed=801010+mi;np.random.seed(seed);random.seed(seed)
        xin=np.r_[normalized,np.zeros((400,normalized.shape[1]))];yin=np.r_[np.zeros(len(normalized),int),np.ones(400,int)]
        with warnings.catch_warnings(record=True),threadpool_limits(limits=2):
            warnings.simplefilter('ignore');values,labels=method(SimpleNamespace(seed=seed),xin,yin,mode,alpha=3,percentage=.1)
        rng=np.random.default_rng(seed+1000)
        picked=np.r_[rng.choice(np.flatnonzero(labels==0),200,replace=False),rng.choice(np.flatnonzero(labels==1),50,replace=False)];rng.shuffle(picked)
        restored=np.tile(median,(len(values),1));restored[:,variable]=values*scale[variable]+median[variable]
        frame=pd.DataFrame(restored,columns=cols)
        for c in FLAGS:frame[c]=True
        frame=frame[list(COLUMNS)];labels=labels.astype(bool)
        for j,p in enumerate(picked):
            ix=fixed_ids[f'ADBENCH-{mode}-{j:04d}']
            if not np.array_equal(frame.iloc[p].to_numpy(float),fixed['X'][ix]):raise AssertionError('Original population reconstruction differs; do not continue')
            assert labels[p]==fixed['y'][ix]
        remaining=np.setdiff1d(np.arange(len(frame)),picked)
        remaining=np.asarray([i for i in remaining if fingerprint(frame.iloc[i].to_numpy(float))not in fixed_hashes])
        normal=remaining[~labels[remaining]];abnormal=remaining[labels[remaining]]
        rng=np.random.default_rng(48400042+mi);rng.shuffle(normal);rng.shuffle(abnormal)
        n=len(normal);a=int(n*.7);b=int(n*.85)
        split={'train':normal[:a],'calibration':normal[a:b],'development':np.r_[normal[b:],abnormal]}
        for name,ii in split.items():
            sub=frame.iloc[ii].copy();sub['case_id']=[f'ADTRAIN-{mode}-{i}'for i in ii];sub['adapterType']='ADBENCH_GLOBAL_CONTEXT'
            sub['generation_mode']=mode;sub['label']=labels[ii];parts[name].append(sub)
        audits.append({'mode':mode,'reproduced_fixed_samples':len(picked),'unused_normal':len(normal),'unused_anomaly':len(abnormal),'split_rows':{k:len(v)for k,v in split.items()}})
        print('ADBench separate training population',mode,audits[-1],flush=True)
    result={k:pd.concat(v,ignore_index=True)for k,v in parts.items()}
    sets={k:{fingerprint(row)for row in f[list(COLUMNS)].to_numpy(float)}for k,f in result.items()}
    assert not any(h&fixed_hashes for h in sets.values())
    # Exact duplicate generated normals can occur across modes: assign any such
    # vector to its first partition, never permit train/cal/dev overlap.
    seen=set()
    for name in parts:
        f=result[name];keep=[]
        for row in f[list(COLUMNS)].to_numpy(float):
            h=fingerprint(row);keep.append(h not in seen);seen.add(h)
        result[name]=f.loc[keep].reset_index(drop=True)
    assert not result['train'].label.any()and not result['calibration'].label.any()
    joblib.dump(result,path,compress=3)
    write(OUT/'preparation.json',{'protocol':'ADBench-specific normal training; distinct from same-service-model transfer',
        'source_population':'unused rows of the exact official generator populations used for fixed evaluation',
        'fixed_npz_sha256':sha256(fixed_path),'fixed_samples_reconstructed_exactly':1000,'test_vectors_in_fit_or_calibration':0,
        'numerical_duplicates_removed_across_partitions':True,'training_normal_only':True,
        'generator_source_model_shared_with_test':True,'independent_production_generalization':False,
        'modes_not_input_features':True,'provenance':provenance,'modes':audits,
        'rows':{k:len(v)for k,v in result.items()},'development_sha256':sha256(path)})
    return result

if __name__=='__main__':build()
