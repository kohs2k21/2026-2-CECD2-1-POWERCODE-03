"""Raw FlowBench CSV -> frozen 8 AST features -> numerical SR and 3 MLs.

No old feature cache, anomaly labels, fitting or threshold selection is used.
The label-free logical projection is passed to a separate C3 execution process.
"""
from pathlib import Path
from copy import deepcopy
from collections import Counter
import argparse,csv,gzip,json,sys,time
import numpy as np
import pandas as pd
import joblib
from threadpoolctl import threadpool_limits
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hybrid.common import ROOT,read,write,sha,named,seal
NUMERIC=ROOT/'vendor/numeric/research/models/feature_rule_spec'
sys.path[:0]=[str(NUMERIC),str(ROOT/'vendor/flowbench/compat'),str(ROOT/'vendor/flowbench')]
from specmodel.dataset import Dataset
from specmodel.calculations import compile_feature
from sr_policy import evaluate_rules

FIELDS=['runtime','queue_delay','wms_delay','post_script_delay','stage_in_delay','stage_out_delay','execute_start','execute_end']

def materialize(workflow,files,out):
    folder=ROOT/'profiles/flowbench'/workflow
    taskrows=[];metadata=[];runs=[];seen=set();pins=[]
    graph=read(folder/'graph.json');catalog=sorted(set(graph)|{n for children in graph.values() for n in children})
    with gzip.open(out/'logical_records.jsonl.gz','wt') as logical:
        for path in files:
            path=Path(path).resolve();before=sha(path)
            run=workflow+':'+path.stem
            if run in seen:raise ValueError('Duplicate run identity; use unique source filenames')
            seen.add(run);runs.append({'RUN_ID':run})
            with path.open(encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f)
                required={'','type','kickstart_transformations',*FIELDS}
                if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)) or required-set(reader.fieldnames):raise ValueError('FlowBench schema differs; no implicit mapping')
                rows=list(reader)
            if not rows or len({r[''] for r in rows})!=len(rows) or any(not r[''] for r in rows):raise ValueError('Missing/duplicate task occurrence IDs')
            logical_rows=[]
            for row in rows:
                if None in row or any(v is None for v in row.values()):raise ValueError('Malformed raw CSV row')
                rid=run+':'+row[''];kind=row['type'].strip();transformation=row['kickstart_transformations'].strip()
                taskrows.append(dict(ROW_ID=rid,RUN_ID=run,TASK_KIND=kind,**{n:row[n] for n in FIELDS}))
                metadata.append(dict(ROW_ID=rid,RUN_ID=run,NODE_ID=row[''],TASK_KIND=kind,TRANSFORMATION=transformation,TASK_FAMILY=kind+'|'+transformation))
                vals={}
                for n in FIELDS:
                    raw=row[n];v=None if raw in ('','None','nan','NaN') else float(raw)
                    if v is not None and not np.isfinite(v):raise ValueError('Nonfinite source field')
                    vals[n]=v
                logical_rows.append(dict(RUN_ID=run,NODE_ID=row[''],TASK_KIND=kind,**vals))
            # This projection contains source metadata only, no raw anomaly_type,
            # ground truth, source path tag or injection parameter.
            logical.write(json.dumps(dict(source_version='flowbench-'+workflow+'-'+before,records=dict(Task=logical_rows,RunContext=[dict(RUN_ID=run)],TaskCatalog=[dict(NODE_ID=n) for n in catalog])),separators=(',',':'))+'\n')
            if sha(path)!=before:raise ValueError('Source changed during projection')
            pins.append(dict(path=str(path),sha256=before,rows=len(rows)))
    capture=deepcopy(read(folder/'capture_template.json'));meta_dir=out/'metadata';meta_dir.mkdir()
    # Exact original unit authority file and unchanged per-column authority.
    unitbytes=(folder/'units.json').read_bytes();(meta_dir/'units.json').write_bytes(unitbytes)
    for name,records in [('RUN',runs),('TASK',taskrows)]:
        path=meta_dir/(name+'.csv')
        with path.open('x',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
        table=next(t for t in capture['tables'] if t['name']==name)
        table.update(file=path.name,sha256=sha(path),row_count=len(records))
    capture.update(split='application',dataset_id='frozen-hybrid-'+workflow,
      period=dict(controlled_domain='Complete supplied public workflow-run CSV exports; retrospective application.'),
      source_identity=dict(workflow=workflow,source_commit='58df4e69f7b0fe4f818bad6400699cdb04e0b1cb',partition='application'))
    write(meta_dir/'capture.json',capture)
    ds=Dataset(meta_dir/'capture.json');definitions=read(folder/'numerical_definitions.json')['definitions']
    features={};audits=[]
    for name,definition in definitions.items():
        result=compile_feature(ds,definition,'frozen_hybrid_'+name)
        if not result['passed']:raise ValueError('Frozen feature failed: '+name+' '+repr(result['errors']))
        if result['row_ids']!=[[r['ROW_ID']] for r in taskrows]:raise ValueError('Feature grain or population changed')
        values=np.asarray([np.nan if v is None else float(v) for v in result['values']],dtype=np.float64)
        if np.isinf(values).any():raise ValueError('Infinite generated feature')
        features[name]=values
        f=result['compiled_feature']
        audits.append(dict(name=name,definition_sha=f['definition_sha'],semantic_definition_sha=f['semantic_definition_sha'],independent_verification=f['independent_calculation'],unit=f['unit'],dtype=f['dtype'],valid=int(np.isfinite(values).sum())))
    ds.assert_unchanged()
    frame=pd.DataFrame(features,index=[r['ROW_ID'] for r in taskrows]);meta=pd.DataFrame(metadata).set_index('ROW_ID').loc[frame.index]
    np.savez_compressed(out/'features.npz',values=frame.to_numpy(),columns=np.asarray(frame.columns,dtype=str),row_ids=np.asarray(frame.index,dtype=str))
    write(out/'feature_audit.json',dict(raw_source_pins=pins,full_raw_recalculation=True,features=audits,rows=len(frame),LLM_calls=0))
    return frame,meta,pins

def run(workflow,files,output):
    start=time.perf_counter();out=Path(output);out.mkdir(parents=True,exist_ok=False)
    folder=ROOT/'profiles/flowbench'/workflow
    frame,meta,pins=materialize(workflow,files,out);after_features=time.perf_counter()
    available=np.isfinite(frame.to_numpy()).any(axis=1);result=meta.copy();model_info=[]
    for algorithm in ['if','kmeans','lof']:
        path=folder/'models'/f'{algorithm}.joblib';bundle=joblib.load(path)
        if list(frame.columns)!=bundle['features']:raise ValueError('Frozen ML feature order differs')
        expanded=frame.copy()
        for name in bundle['features']:expanded['missing__'+name]=frame[name].isna().astype(float)
        if list(expanded.columns)!=bundle['expanded_columns']:raise ValueError('Frozen missing-indicator contract differs')
        score=np.full(len(frame),np.nan);began=time.perf_counter()
        x=bundle['encoder'].transform(expanded)
        with threadpool_limits(limits=2):score[available]=bundle['normalizer'].transform(bundle['raw_model'].score(x[available]))
        states=np.full(len(frame),-1,dtype=np.int8);states[available]=(score[available]>bundle['threshold']).astype(np.int8)
        result[algorithm]=[named(s) for s in states];result[algorithm+'_score']=score
        model_info.append(dict(algorithm=algorithm,sha256=sha(path),threshold=bundle['threshold'],score_direction='larger_is_more_anomalous',seconds=time.perf_counter()-began))
    reference=read(folder/'reference.json');policy=read(folder/'frozen_policy.json')
    names=reference['names']
    sr=evaluate_rules(frame[names].to_numpy(),meta,reference,'grouped_joint_p99',policy['joint_cutoff'])
    result['numeric_SR']=[named(s) for s in sr['state']]
    result['fired_numeric_rule_ids']=[json.dumps([names[j] for j,s in enumerate(row) if s==1]) for row in sr['rule_states']]
    result['unavailable_numeric_rule_ids']=[json.dumps([names[j] for j,s in enumerate(row) if s==-1]) for row in sr['rule_states']]
    result['numeric_SR_score']=sr['score']
    from hybrid.common import combine
    result['ML']=[combine(row) for row in result[['if','kmeans','lof']].itertuples(index=False,name=None)]
    result.to_csv(out/'predictions.csv.gz',index_label='entity_id',compression='gzip')
    np.savez_compressed(out/'numeric_rule_evidence.npz',rule_states=sr['rule_states'],thresholds=sr['thresholds'],row_ids=np.asarray(result.index,dtype=str),rule_names=np.asarray(names,dtype=str))
    for pin in pins:
        if sha(pin['path'])!=pin['sha256']:raise ValueError('Source changed after scoring')
    write(out/'summary.json',dict(rows=len(frame),raw_feature_calculation=True,feature_definitions=len(frame.columns),numeric_SR_count=len(names),ML_models=model_info,
      feature_seconds=after_features-start,total_seconds=time.perf_counter()-start,model_fit_calls=0,LLM_calls=0,threshold_changes=0,
      score_direction='larger_is_more_anomalous',numeric_policy=policy['version'],primary_numeric_policy='grouped_joint_p99',
      numeric_SR=dict(Counter(result.numeric_SR)),ML=dict(Counter(result.ML)),raw_sources=pins))
    seal(out);print(json.dumps(dict(rows=len(frame),seconds=time.perf_counter()-start)),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--workflow',required=True,choices=['1000genome','montage']);g=p.add_mutually_exclusive_group(required=True);g.add_argument('--input-dir',type=Path);g.add_argument('--run-list',type=Path);p.add_argument('--output',required=True);a=p.parse_args()
    files=sorted(a.input_dir.glob('*.csv')) if a.input_dir else [Path(x) for x in read(a.run_list)]
    if not files:raise ValueError('No source workflow CSVs')
    run(a.workflow,files,a.output)

if __name__=='__main__':main()
