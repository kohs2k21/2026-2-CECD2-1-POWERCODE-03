"""Expanded historical training, with independent source-template calibration.

Keep ALL templates used by the frozen normal featurizer inside train. Split the
remaining historical templates by period and workflow Adapter signature. This
does not claim a chronological source holdout; final replay sources stay held out.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from collections import defaultdict
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import source_references,sha256
from model_evaluation.windowing import window_reference_split
from shared_model_v4 import expanded_data as earlier

OUT=ROOT/'reports/shared_model_v4_stratified'

def split(bundle,reserved):
    _,oldsets,_,_=window_reference_split(source_references(bundle)[0],reserved)
    oldtrain=oldsets['train'];strata=defaultdict(list);parts={n:[]for n in('train','calibration','threshold')}
    for t in bundle['templates']:
        if t['template_id']in reserved:continue
        if t['template_id']in oldtrain:parts['train'].append(t);continue
        signature=tuple(sorted({r['adapterType']for r in t['rows']}))
        strata[t['source_period'],signature].append(t)
    rng=np.random.default_rng(48200042)
    for key,values in sorted(strata.items()):
        values=sorted(values,key=lambda t:t['template_id']);order=rng.permutation(len(values));values=[values[i]for i in order]
        n=len(values)
        if n>=4:
            a=max(1,n//2);b=a+max(1,(n-a)//2)
            for name,v in zip(parts,(values[:a],values[a:b],values[b:])):parts[name].extend(v)
        else:
            for i,t in enumerate(values):parts[('train','calibration','threshold')[i%3]].append(t)
    sets={n:{t['template_id']for t in ts}for n,ts in parts.items()}
    assert oldtrain<=sets['train']
    assert not set.union(*sets.values())&reserved
    assert not any(sets[a]&sets[b]for a,b in(('train','calibration'),('train','threshold'),('calibration','threshold')))
    return parts,sets

def prepare():
    OUT.mkdir(exist_ok=True);dest=OUT/'normal.joblib'
    if dest.exists():assert sha256(dest)==read(OUT/'preparation.json')['sha256'];return joblib.load(dest)
    earlier.OUT=OUT;earlier.source_split=split
    result=earlier.prepare()
    # Original replay episodes use source templates retained in expanded train.
    # Preserve short/cold-start normal regimes instead of forgetting them.
    old=joblib.load(ROOT/'reports/shared_model_v2/development.joblib')['train']
    assert not set(old.case_id)&set(result['train'].case_id)
    result['train']=pd.concat([result['train'],old],ignore_index=True)
    joblib.dump(result,dest,compress=3)
    receipt=read(OUT/'preparation.json');receipt['sha256']=sha256(dest)
    receipt['normal_source_partition_changed']='old featurizer source train retained; remaining historical templates split 50/25/25 by period and workflow Adapter signature'
    receipt['chronological_source_holdout']=False;receipt['old_replay_minutes_added']=len(old)
    receipt['total_train_minutes']=len(result['train'])
    receipt['frozen_featurizer_source_templates_kept_in_train']=True
    write(OUT/'preparation.json',receipt)
    print('Training including original cold starts',len(result['train']),flush=True)
    return result

if __name__=='__main__':prepare()
