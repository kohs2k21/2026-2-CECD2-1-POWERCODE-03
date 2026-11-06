"""Select a common lookback using NEW validation episodes, never final test F1.

Run this before evaluate.py. All six algorithms/routes keep fixed hyperparameters,
P99 thresholds, one-minute decisions, labels and denominators for every candidate.
"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd

from evaluation_benchmark.generate import Catalog,generate_case
from model_evaluation.data import SCENARIOS,history_lookup,normal_development,sha256,source_references,verify_benchmark
from model_evaluation.models import ComparisonConfig,METHODS,SixModels
from model_evaluation.metrics import Counts
from model_evaluation.windowing import CANDIDATES,PriorHistory,aggregate_windows,reserved_validation_ids,window_reference_split


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def selected_completion_rows(case,flows):
    return pd.DataFrame([{'case_id':case.case_id,'end_seconds':r['endTime']/1000,
                          'duration':r['endTime']-r['startTime'],
                          'start_minute':r['startTime']//60000*60,'transactionId':r['transactionId']}
                         for flow in flows.values() for r in flow
                         if (r['adapterType'],r['processId'])==case.key[:2]],
                        columns=['case_id','end_seconds','duration','start_minute','transactionId'])


def validation_bank(bundle,reserved,benchmark,count,seed,final_case_ids):
    templates = [t for t in bundle['templates'] if t['template_id'] in reserved]
    catalog = Catalog({**bundle,'templates':templates})
    history = history_lookup(benchmark)
    bank = {}
    for name in SCENARIOS:
        native,normal,raw,nraw,ids = [],[],[],[],[]
        for index in range(count):
            case = generate_case(catalog,name,index,seed+730_000,history)
            if case.case_id in final_case_ids:
                raise ValueError('Validation episode ID overlaps final benchmark')
            exported = case.export()
            obs = pd.DataFrame(exported['windows.csv'])
            obs = obs.loc[obs.scope.eq('TARGET')].reset_index(drop=True)
            labels = pd.DataFrame(exported['window_labels.csv'])
            labels = labels.loc[labels.scope.eq('TARGET')].reset_index(drop=True)
            obs['window_is_anomaly'],obs['scenario_types'] = labels.window_is_anomaly.to_numpy(dtype=bool),labels.scenario_types.to_numpy()
            controls = obs.drop(columns=['window_is_anomaly','scenario_types']).copy()
            r,nr = selected_completion_rows(case,case.observed),selected_completion_rows(case,case.normal)
            first = nr.groupby('transactionId').start_minute.min().value_counts()
            ends = pd.to_datetime(controls.window_end,format='ISO8601').astype('datetime64[ns]').astype('int64').to_numpy()/1e9
            starts = ends-60
            controls['execution_arrivals'] = [int(first.get(t,0)) for t in starts]
            if not np.array_equal(controls.execution_arrivals,labels.normal_execution_arrivals):
                raise ValueError('Validation normal arrivals do not match raw normal logs')
            nr['_minute'] = (nr.end_seconds//60)*60
            duration_groups = nr.groupby('_minute').duration
            for column,values in (('duration_p50_ms',duration_groups.median()),('duration_p95_ms',duration_groups.quantile(.95))):
                controls[column] = values.reindex(starts).to_numpy()
            native.append(obs); normal.append(controls); raw.append(r); nraw.append(nr)
            ids.append(case.case_id)
        bank[name] = {'observed':pd.concat(native,ignore_index=True),'normal':pd.concat(normal,ignore_index=True),
                      'observed_rows':pd.concat(raw,ignore_index=True),'normal_rows':pd.concat(nraw,ignore_index=True),'case_ids':ids}
        print(f'Validation {name}: {count} NEW cases + {count} paired controls',flush=True)
    return bank


def selected_references(search,benchmark,cache,minutes=None):
    record = json.loads((search/'selection.json').read_text(encoding='utf-8'))
    if record['benchmark_manifest_sha256']!=sha256(benchmark/'manifest.json') or record['source_cache_sha256']!=sha256(cache):
        raise ValueError('Window selection belongs to different inputs')
    if sha256(search/'training_inputs.joblib')!=record['training_inputs_sha256']:
        raise ValueError('Window training references changed')
    for name,digest in record['training_code_sha256'].items():
        if sha256(Path(__file__).parent/name)!=digest:
            raise ValueError(f'Window selection training code changed: {name}')
    state = joblib.load(search/'training_inputs.joblib')
    width = record['selected_minutes'] if minutes is None else minutes
    config = ComparisonConfig(**{**record['config'],'window_minutes':width})
    history = PriorHistory(benchmark/'reference_history_windows.csv')
    partitions = {n:aggregate_windows(p,state['completions'][n],width,history,state['baselines']) for n,p in state['native'].items()}
    return config,partitions,state,record


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    if any(args.output.iterdir()):
        raise FileExistsError('Choose a fresh search output directory')
    manifest,bundle = verify_benchmark(args.benchmark,args.source_cache)
    original,_,_,_ = source_references(bundle)
    reserved = reserved_validation_ids(bundle)
    refs,sets,support,baselines = window_reference_split(original,reserved)
    config = ComparisonConfig(seed=args.seed,replay_cases_per_adapter=args.replays_per_adapter)
    native,metadata,completions = normal_development(bundle,sets,args.benchmark,baselines,config,return_rows=True)
    state = {'native':native,'completions':completions,'metadata':metadata,'source_support':support,
             'baselines':baselines,'reserved_template_ids':sorted(reserved),
             'source_reference_counts':{n:len(p) for n,p in refs.items()}}
    joblib.dump(state,args.output/'training_inputs.joblib',compress=3)
    # ONLY read case IDs from final benchmark to assert disjointness, not its labels/scores.
    final_ids = set().union(*(set(pd.read_csv(args.benchmark/n/'cases.csv',usecols=['case_id']).case_id) for n in manifest['datasets']))
    bank = validation_bank(bundle,reserved,args.benchmark,args.validation_cases,args.seed,final_ids)
    joblib.dump(bank,args.output/'validation_inputs.joblib',compress=3)
    history = PriorHistory(args.benchmark/'reference_history_windows.csv')
    candidates = []
    for width in CANDIDATES:
        print(f'Training/evaluating candidate {width} minutes...',flush=True)
        cfg = replace(config,window_minutes=width)
        partitions = {n:aggregate_windows(p,completions[n],width,history,baselines) for n,p in native.items()}
        suite = SixModels('window',cfg).fit(partitions,support)
        scenario_results = []
        for name,data in bank.items():
            counts = Counts()
            for cohort in ('observed','normal'):
                frame = aggregate_windows(data[cohort],data[cohort+'_rows'],width,history,baselines)
                truth = frame.window_is_anomaly.to_numpy(dtype=bool) if cohort=='observed' else np.zeros(len(frame),bool)
                for begin in range(0,len(frame),cfg.chunk_rows):
                    prediction = suite.score(frame.iloc[begin:begin+cfg.chunk_rows])[1]
                    counts.update(truth[begin:begin+cfg.chunk_rows],prediction)
            scenario_results.append({'scenario':name,'metrics':counts.records()})
        macro = []
        pooled = Counts()
        for d in scenario_results:
            pooled.values += Counts.from_records(d['metrics']).values
        for method in METHODS:
            rows = [next(r for r in d['metrics'] if r['method']==method) for d in scenario_results]
            macro.append({'method':method,**{key:float(np.mean([r[key] for r in rows])) for key in ('accuracy','precision','recall','f1','normal_false_positive_rate')}})
        candidate = {'minutes':width,'objective_macro_f1':float(np.mean([r['f1'] for r in macro])),
                     'mean_macro_normal_fpr':float(np.mean([r['normal_false_positive_rate'] for r in macro])),
                     'macro':macro,'pooled':pooled.records(),'scenarios':scenario_results,
                     'supported_adapters':sorted(suite.experts['if'])}
        candidates.append(candidate)
        write_json(args.output/f'candidate_{width:02d}m.json',candidate)
        print(f"Candidate {width}m: mean six-model scenario-macro F1={candidate['objective_macro_f1']:.4%}",flush=True)
    chosen = max(candidates,key=lambda r:(r['objective_macro_f1'],-r['mean_macro_normal_fpr'],-r['minutes']))
    selection = {'selected_minutes':chosen['minutes'],'candidate_minutes':list(CANDIDATES),
                 'criterion':'Mean of six methods: equal-weight mean F1 across six basic scenarios. Tie: lower normal FPR, then shorter window.',
                 'decision_stride_minutes':1,'threshold_quantile':config.quantile,'validation_cases_per_scenario':args.validation_cases,
                 'validation_cases':sum(len(d['case_ids']) for d in bank.values()),'validation_case_ids':{n:d['case_ids'] for n,d in bank.items()},
                 'validation_source_templates':len(reserved),'validation_source_overlaps_window_reference':0,
                 'final_labels_used_for_selection':False,'hard_used_for_selection':False,
                 'config':asdict(config),'candidates':candidates,
                 'per_method_best_minutes':{m:max(candidates,key=lambda c:next(r['f1'] for r in c['macro'] if r['method']==m))['minutes'] for m in METHODS},
                 'benchmark_manifest_sha256':sha256(args.benchmark/'manifest.json'),'source_cache_sha256':sha256(args.source_cache),
                 'training_inputs_sha256':sha256(args.output/'training_inputs.joblib'),
                 'validation_inputs_sha256':sha256(args.output/'validation_inputs.joblib'),
                 'training_code_sha256':{n:sha256(Path(__file__).parent/n) for n in ('models.py','data.py','windowing.py','tune_windows.py')},
                 'completed_utc':datetime.now(timezone.utc).isoformat(),
                 'limitations':['Best among these six candidates on this validation generator, not a universal optimum.',
                    'Final benchmark was already evaluated in v1, so it is not a newly blind external test.',
                    'Quarter source-template holdout is hash-based, not a future chronological validation split.',
                    'Prior traffic history and episodes are synthetic; original patterns overlap final benchmark generation.',
                    'Incomplete early windows use available history; labels/decisions stay on the original minute grid.',
                    'Normal row comparison still uses its unchanged original reference protocol; window selection is separate.']}
    write_json(args.output/'selection.json',selection)
    verify_benchmark(args.benchmark,args.source_cache)
    print(f"SELECTED {chosen['minutes']} minutes using validation ONLY",flush=True)
    return selection


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark',type=Path,default=ROOT/'data/evaluation')
    parser.add_argument('--source-cache',type=Path,default=ROOT/'reports/evaluation_v2_work/inputs.json')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/window_search_v2')
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--replays-per-adapter',type=int,default=8)
    parser.add_argument('--validation-cases',type=int,default=240)
    args = parser.parse_args()
    if args.validation_cases<1: parser.error('--validation-cases must be positive')
    run(args)
