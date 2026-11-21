"""Historical normal-only source expansion, preserving held-out test templates.

The old global chronological split overrepresented the earliest collection.
Here each collection period is split chronologically 70/15/15 by whole source
workflow. All are earlier than the simulated monitoring episodes. No held-out
template, benchmark control row, or anomaly label enters normal fitting.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import copy
from collections import defaultdict
import joblib
import numpy as np
import pandas as pd
from model_research_v13.data import read,write
from model_evaluation.data import ReplayCatalog,history_lookup,sha256
from evaluation_benchmark.generate import Case
from model_improvement.data import rich_rows
from model_research_v15.inference import ProcessResearchMonitor

OUT=ROOT/'reports/shared_model_v4_expanded'

def source_split(bundle,reserved):
    periods=defaultdict(list)
    for t in bundle['templates']:
        if t['template_id']not in reserved:periods[t['source_period']].append(t)
    splits={k:[]for k in('train','calibration','threshold')}
    for period,templates in periods.items():
        # Every row in a source workflow stays in the same partition.
        templates.sort(key=lambda t:(min(str(r.get('startTime',''))for r in t['rows']),str(t['template_id'])))
        n=len(templates);a=int(n*.7);b=int(n*.85)
        for name,values in zip(splits,(templates[:a],templates[a:b],templates[b:])):splits[name].extend(values)
    sets={k:{t['template_id']for t in v}for k,v in splits.items()}
    assert not set.union(*sets.values())&reserved
    assert not any(sets[a]&sets[b]for a,b in(('train','calibration'),('train','threshold'),('calibration','threshold')))
    return splits,sets

def balanced_keys(catalog,limit):
    adapters=defaultdict(list)
    for k in sorted(catalog.groups,key=lambda k:(k[0],k[1],k[2])):adapters[k[0]].append(k)
    selected=[];offset=0
    while len(selected)<limit:
        added=False
        for a,keys in sorted(adapters.items()):
            if offset<len(keys):selected.append(keys[offset]);added=True
            if len(selected)>=limit:break
        if not added:break
        offset+=1
    return selected

def prepare():
    OUT.mkdir(exist_ok=True);path=OUT/'normal.joblib'
    if path.exists():assert sha256(path)==read(OUT/'preparation.json')['sha256'];return joblib.load(path)
    bundle=read(ROOT/'reports/evaluation_v2_work/inputs.json');state=joblib.load(ROOT/'reports/window_search_v2/training_inputs.joblib')
    reserved=set(state['reserved_template_ids']);splits,sets=source_split(bundle,reserved)
    old=joblib.load(ROOT/'reports/shared_model_v2_release/models.joblib');processor=ProcessResearchMonitor(None,old['feature_context'],'if')
    history=history_lookup(ROOT/'data/evaluation');frames={};audit={}
    for si,(name,templates)in enumerate(splits.items()):
        catalog=ReplayCatalog(templates);keys=balanced_keys(catalog,900 if name=='train'else 450)
        native=[];rows=[]
        for i,key in enumerate(keys):
            case=Case(catalog,'individual_delay',i,48100042+si*100000,history)
            case.key=key;case.fixed_ref=None;case.baseline(40 if name=='train'else 32)
            assert not case.events and not case.labels
            export=case.export();grid=pd.DataFrame(export['windows.csv']);grid=grid.loc[grid.scope.eq('TARGET')].reset_index(drop=True)
            native.append(grid);rows.append(rich_rows(case,case.normal))
        grid=pd.concat(native,ignore_index=True);completed=pd.concat(rows,ignore_index=True)
        # Featurizer/reference unchanged: common numerical inputs retain the same
        # definition as the frozen ADBench table and original benchmark caches.
        f=old['adapter_reference'].transform(processor.features(grid,completed));frames[name]=f
        audit[name]={'episodes':len(keys),'minutes':len(f),'Process_groups':f.groupby(['adapterType','processId']).ngroups,
            'source_templates':len(sets[name]),'source_periods':sorted({t['source_period']for t in templates}),
            'source_template_ids':sorted(sets[name]),'episode_ids':sorted(f.case_id.unique())}
        print('Expanded normals',name,audit[name]['episodes'],len(f),'groups',audit[name]['Process_groups'],flush=True)
    result={**frames,'adapter_reference':old['adapter_reference'],'feature_context':old['feature_context']}
    joblib.dump(result,path,compress=3)
    write(OUT/'preparation.json',{'sha256':sha256(path),'partitions':audit,
        'reserved_template_intersection':0,'test_or_validation_controls_used':False,
        'normal_source_partition_changed':'within each historical collection period 70/15/15 chronological by whole workflow',
        'all_source_periods_precede_monitoring_episodes':True,'common_featurizer_unchanged':True,
        'source_archive_sha256':sha256(ROOT/'reports/evaluation_v2_work/inputs.json')})
    return result

if __name__=='__main__':prepare()
