"""Gated SR pilot, exhaustive development, frozen final replay.

All generated files stay in the explicitly supplied personal artifact directory.
Final generation requires a separately written root authorization and a sealed
selection lock. The CLI never interprets pilot success as a final gate.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import pickle
import platform
import sys
import time
import traceback
import warnings

import numpy as np
import pandas as pd

from . import dataset, features as ft, metrics
from .methods import METHODS, Configuration, FeatureCache, variants, fit_method, score_method
from .references import fit_normal

WINDOWS=(5,10,15,30,45,60,90,120,180,240,300,360,420,480,540,600,660,720,1080,1440)
CADENCES=(1,2,5,10,15,30,60)
REPOSITORY_ROOT=Path(__file__).resolve().parents[3]


def _jsonable(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,(pd.Timestamp,datetime)):return value.isoformat()
    if isinstance(value,Path):return str(value)
    raise TypeError(type(value).__name__)


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=_jsonable,allow_nan=False),encoding='utf-8')


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save_pickle(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,'wb') as stream:pickle.dump(value,stream,protocol=5)


def load_pickle(path):
    with gzip.open(path,'rb') as stream:return pickle.load(stream)


def environment():
    import scipy,psutil
    import statsmodels,patsy
    package_root=Path(statsmodels.__file__).parent.parent
    package_evidence={}
    for name,version in (('statsmodels',statsmodels.__version__),('patsy',patsy.__version__)):
        folder=package_root/f'{name}-{version}.dist-info'
        package_evidence[name]={key:file_hash(folder/key) for key in ('METADATA','RECORD')}
    return {'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
            'numpy':np.__version__,'pandas':pd.__version__,'scipy':scipy.__version__,
            'statsmodels':statsmodels.__version__,'patsy':patsy.__version__,'psutil':psutil.__version__,
            'isolated_package_path':str(package_root),'isolated_package_evidence':package_evidence,
            'threads':1,'real_customer_data':False}


def registry():
    rows=[]
    def append(config,cadences,cohort):
        payload=asdict(config)
        payload.update(cadences=list(cadences),cohort=cohort,
                       cell_id=hashlib.sha256(json.dumps(asdict(config),sort_keys=True).encode()).hexdigest()[:16])
        rows.append(payload)
    for method in METHODS:
        for window in WINDOWS:
            for variant in variants(method):append(Configuration(method,window,variant=variant),CADENCES,'core')
        for variant in variants(method):
            for baseline in ('B0','B2'):append(Configuration(method,60,baseline=baseline,variant=variant),(5,),'baseline_sensitivity')
            for aggregation in (5,15):append(Configuration(method,60,aggregation=aggregation,variant=variant),(1,5,15,60),'aggregation_sensitivity')
    for k in (.25,.5,1.):
        append(Configuration('R07',60,variant={'k':k,'unbounded':True}),CADENCES,'unbounded_cusum_control')
    return {'version':'trend-sr-method-registry-v1','methods':METHODS,'windows':WINDOWS,'cadences':CADENCES,
            'configs':rows,'nominal_core_cells':24*20*7,
            'actual_configurations':len(rows),
            'log_tail_numerical_floor':float(np.log(np.finfo(np.float64).tiny)),
            'tail_floor_meaning':'finite numerical saturation, not probability of service fault',
            'zero_scale_policy':'explicit point_mass_indicator_0_equal_1_different; no global epsilon',
            'legacy_recipe':'R00 A1/B1 directly calls legacy43; A/B sensitivities adapted and separately named',
            'calendar_timezone':'Asia/Seoul','fit':'train_only','cut':'normal_cal_only',
            'forecast':'hourly24period, train_weekly_rolling_origin_scale, frozen_open_loop',
            'final_reference_seed_map':dataset.PROTOCOL['final_reference_seed_map']}


def _bounds(item,days=None):
    start=pd.Timestamp(item.manifest['evaluation_start']);end=pd.Timestamp(item.manifest['evaluation_end'])
    return start,min(end,start+pd.Timedelta(days=days)) if days is not None else end


def prepare(item,aggregation,days=None):
    start,end=_bounds(item,days)
    frame=item.frame
    # Preserve declared source prefixes, never generate future data to pad train.
    endpoint=frame.index if 'endpoint' not in frame else pd.DatetimeIndex(frame.endpoint)
    return ft.aggregate(frame.loc[endpoint<=end],aggregation,start=start,end=end)


class Publisher:
    def __init__(self):self.cache={}

    def publish(self,traces,cadences):
        output={}
        for cadence in cadences:
            groups=[]
            for group,trace in traces.items():
                a=trace.aggregate;key=(id(a),cadence)
                if key not in self.cache:
                    # A finite positional probe constructs only the source/time
                    # mapping. Actual unsupported scores remain NaN after mapping.
                    plan=ft.publish_score(a,np.arange(len(a.index),dtype=float),cadence)
                    mapping=plan.pop('score').to_numpy(float)
                    self.cache[key]=(plan,mapping)
                plan,mapping=self.cache[key]
                frame=plan.copy(deep=False)
                supported=np.isfinite(mapping)
                for family,raw in trace.values.items():
                    values=np.full(len(frame),np.nan)
                    values[supported]=raw[mapping[supported].astype(int)]
                    frame[family]=values
                if trace.values:
                    frame['score_supported']=np.isfinite(frame[list(trace.values)].to_numpy(float)).all(axis=1)
                groups.append(frame)
            output[cadence]=pd.concat(groups,ignore_index=True)
        return output


def _families(traces):
    return sorted(set.intersection(*[set(trace.values) for trace in traces.values()]))


def _summary(rows):
    if not rows:return {}
    def average(key):
        values=[row.get(key) for row in rows if row.get(key) is not None]
        return float(np.mean(values)) if values else None
    keys=('event_recall','macro_subtype_recall','macro_family_recall','mean_loss360_minutes','mean_loss1440_minutes',
          'positive_time_fraction','false_alarm_episodes_per_hour','normal_availability')
    result={key:average(key) for key in keys}
    independent=[row['normal_all_branch_supported_minutes']/row['normal_source_minutes']
                 for row in rows if row.get('normal_source_minutes',0)>0]
    result['independent_all_branch_availability']=float(np.mean(independent)) if independent else None
    return result


def _export_source(item,path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    write_json(path/'manifest.json',item.manifest)
    item.frame.to_pickle(path/'source.pkl.gz',compression='gzip')
    item.events.to_csv(path/'truth-events.csv',index=False)
    item.quality.to_csv(path/'quality.csv',index=False)


def _save_raw(traces,path):
    arrays={}
    for group,trace in traces.items():
        arrays[f'{group}__endpoint_ns']=trace.aggregate.index.as_unit('ns').asi8
        for family,values in trace.values.items():arrays[f'{group}__{family}']=values
    np.savez_compressed(path,**arrays)


def prediction_fingerprint(frame):
    h=hashlib.sha256()
    for group,part in frame.groupby('group',sort=True):
        part=part.sort_values('decision_tick')
        values=part.prediction.to_numpy(float)
        h.update(str(group).encode());h.update(pd.DatetimeIndex(part.decision_tick).as_unit('ns').asi8.tobytes())
        for mask in (np.isfinite(values),values==1.,part.score_supported.to_numpy(bool)):
            h.update(np.packbits(mask).tobytes())
    return h.hexdigest()


def run_pilot(output,window=60,train_days=10,score_days=2):
    """Ten train days, two calibration/selection days, every declared variant."""
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'environment.json',environment());write_json(output/'method_registry.json',registry())
    started=time.perf_counter();seed=9501
    train=dataset.generate(seed,'train','normal');cal=dataset.generate(seed,'cal','normal')
    selection=dataset.generate(seed,'selection','main')
    aggs={name:prepare(item,1,days) for name,item,days in [('train',train,train_days),('cal',cal,score_days),('selection',selection,score_days)]}
    norm=fit_normal(aggs['train'],'B1')
    caches={name:FeatureCache(value,norm) for name,value in aggs.items()}
    publisher=Publisher();rows=[]
    for method in METHODS:
        for variant in variants(method):
            cfg=Configuration(method,window,variant=variant);step=time.perf_counter()
            try:
                forecasts=None
                if method in ('R21','R22','R23'):
                    from .forecasting import fit_forecasts
                    forecasts=fit_forecasts(aggs['train'],method,variant)
                artifact=fit_method(aggs['train'],cfg,norm,caches['train'],forecast=forecasts,seed=seed)
                raw=score_method(artifact,aggs['cal'],caches['cal'])
                published=publisher.publish(raw,(1,5,15,60))
                families=_families(raw)
                calibration=metrics.calibrate(published,score_columns=tuple(families)) if families else {'passed':False,'reason':'no_families'}
                replay=score_method(artifact,aggs['selection'],caches['selection'])
                finite={group:{family:int(np.isfinite(value).sum()) for family,value in trace.values.items()} for group,trace in replay.items()}
                row={'method':method,'variant':variant,'status':'completed','finite':finite,
                     'calibration_passed':calibration['passed'],'forecast_status':None if forecasts is None else {g:f['status'] for g,f in forecasts.items()},
                     'wall_seconds':time.perf_counter()-step}
                save_pickle(output/f'{method}-{len(rows)}-reference.pkl.gz',artifact)
                _save_raw(replay,output/f'{method}-{len(rows)}-raw.npz')
            except Exception as exc:
                row={'method':method,'variant':variant,'status':'failed','reason':f'{type(exc).__name__}:{exc}',
                     'traceback':traceback.format_exc(),'wall_seconds':time.perf_counter()-step}
            rows.append(row)
            print(json.dumps(row,default=_jsonable),flush=True)
            write_json(output/'pilot.json',{'status':'running','rows':rows})
    result={'status':'completed' if all(r['status']=='completed' for r in rows) else 'failed',
            'methods_attempted':len(set(r['method'] for r in rows)),'variants_attempted':len(rows),
            'rows':rows,'window':window,'train_days':train_days,'score_days':score_days,
            'wall_seconds':time.perf_counter()-started,'not_a_final_gate':True}
    write_json(output/'pilot.json',result)
    return result


def _gate(path,phase):
    gate=json.loads(Path(path).read_text(encoding='utf-8'))
    if gate.get('phase')!=phase or gate.get('approved') is not True:
        raise ValueError(f'{phase}_gate_missing')
    return gate


def source_digest():
    root=Path(__file__).parent
    result={path.relative_to(root).as_posix():file_hash(path)
            for path in sorted(root.rglob('*')) if path.is_file() and path.suffix in ('.py','.json')}
    dependencies=('long_term_trend/features.py','long_term_trend/dataset.py','long_term_trend/protocol.json',
                  'long_term_trend/models.py','trend_family_comparison/detectors.py',
                  'trend_recovery_comparison/signals.py')
    result.update({f'../{name}':file_hash(root.parent/name) for name in dependencies})
    return result


def _cfg(row):
    return Configuration(row['method'],row['window'],row['aggregation'],row['baseline'],row['variant'])


def save_reference(path,artifact,normal_path,forecast_path=None,legacy_path=None):
    """Externalize shared refs instead of writing the same arrays in every cell."""
    compact=copy_reference(artifact)
    if compact.legacy and compact.legacy.get('status')=='ok':
        compact.legacy=dict(compact.legacy)
        compact.legacy.pop('reference',None)
    compact.normal={};compact.forecast=None
    relative=lambda p:os.path.relpath(p,Path(path).parent) if p else None
    save_pickle(path,{'artifact':compact,'normal_path':relative(normal_path),
                      'forecast_path':relative(forecast_path),'legacy_path':relative(legacy_path)})


def copy_reference(reference):
    import copy
    return copy.copy(reference)


def load_reference(path):
    payload=load_pickle(path);artifact=payload['artifact']
    resolve=lambda p:(Path(path).parent/p).resolve()
    artifact.normal=load_pickle(resolve(payload['normal_path']))
    artifact.forecast=load_pickle(resolve(payload['forecast_path'])) if payload['forecast_path'] else None
    if payload['legacy_path']:
        artifact.legacy['reference']=load_pickle(resolve(payload['legacy_path']))
    return artifact


def _package(published,baseline,family):
    """Replace one family; the other two stay at the fixed R00 W60/C5 recipe."""
    result={}
    fixed=baseline[5]
    for cadence,frame in published.items():
        if not np.array_equal(frame.group.to_numpy(),fixed.group.to_numpy()) or not np.array_equal(
                pd.DatetimeIndex(frame.decision_tick).asi8,pd.DatetimeIndex(fixed.decision_tick).asi8):
            raise ValueError('package_source_timeline_mismatch')
        merged=frame.copy(deep=False)
        for other in ('count','latency','relation'):
            if other!=family:merged[other]=fixed[other].to_numpy(float)
        result[cadence]=merged
    return result


def _cuts_columns(family):
    return ('count','latency','relation','error') if family=='error' else ('count','latency','relation')


def run_development(output,gate_path,*,limit=None):
    gate=_gate(gate_path,'development')
    if gate.get('source_hashes')!=source_digest():raise ValueError('review_gate_source_hash_mismatch')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    reg=registry();write_json(output/'method_registry.json',reg)
    write_json(output/'environment.json',environment());write_json(output/'source_hashes.json',source_digest())
    all_records=[];coverage=[];started=time.perf_counter()
    if limit is not None:
        configs=reg['configs'][:limit]
        phase='integration_only_not_exhaustive'
    else:configs=reg['configs'];phase='exhaustive_development'
    for seed in dataset.PROTOCOL['development_seeds']:
        seedroot=output/f'seed-{seed}';seedroot.mkdir()
        data={split:dataset.generate(seed,split,suite) for split,suite in
              [('train','normal'),('cal','normal'),('selection','main')]}
        data['error']=dataset.generate(seed,'selection','error')
        for split,item in data.items():_export_source(item,seedroot/'sources'/split)
        agg_cache={};normal_cache={};feature_cache={};forecast_cache={};publisher=Publisher()
        def aggregates(split,a):
            key=(split,a)
            if key not in agg_cache:agg_cache[key]=prepare(data[split],a)
            return agg_cache[key]
        def context(a,b):
            key=(a,b)
            if key not in normal_cache:
                normal_cache[key]=fit_normal(aggregates('train',a),b)
            normal=normal_cache[key]
            caches={}
            for split in data:
                key2=(split,a,b)
                if key2 not in feature_cache:feature_cache[key2]=FeatureCache(aggregates(split,a),normal)
                caches[split]=feature_cache[key2]
            return normal,caches
        normal,cache=context(1,'B1')
        baseline=fit_method(aggregates('train',1),Configuration('R00',60),normal,cache['train'],seed=seed)
        basecal=publisher.publish(score_method(baseline,aggregates('cal',1),cache['cal']),CADENCES)
        baseselect={suite:publisher.publish(score_method(baseline,aggregates(suite,1),cache[suite]),CADENCES)
                    for suite in ('selection','error')}
        basecut=metrics.calibrate(basecal,score_columns=('count','latency','relation'))
        normalpath=seedroot/'normal-A1-B1.pkl.gz';save_pickle(normalpath,normal)
        legacypath=seedroot/'legacy-source-reference.pkl.gz';save_pickle(legacypath,baseline.legacy['reference'])
        basepath=seedroot/'baseline-reference.pkl.gz'
        save_reference(basepath,baseline,normalpath,legacy_path=legacypath)
        write_json(seedroot/'baseline-calibration.json',basecut)
        for number,row in enumerate(configs):
            cfg=_cfg(row);cell=row['cell_id'];cellroot=seedroot/'cells'/cell;cellroot.mkdir(parents=True)
            step=time.perf_counter();cpu=time.process_time();disposition='completed';reason=None
            try:
                normal,caches=context(cfg.aggregation,cfg.baseline)
                forecast=None;forecastpath=None
                if cfg.method in ('R21','R22','R23'):
                    fkey=(cfg.method,json.dumps(cfg.variant,sort_keys=True))
                    if fkey not in forecast_cache:
                        from .forecasting import fit_forecasts
                        forecast_cache[fkey]=fit_forecasts(aggregates('train',1),cfg.method,cfg.variant)
                    forecast=forecast_cache[fkey]
                    forecastpath=seedroot/f"forecast-{cfg.method}-{hashlib.sha256(fkey[1].encode()).hexdigest()[:8]}.pkl.gz"
                    if not forecastpath.exists():save_pickle(forecastpath,forecast)
                artifact=fit_method(aggregates('train',cfg.aggregation),cfg,normal,caches['train'],forecast=forecast,seed=seed)
                normpath=seedroot/f'normal-A{cfg.aggregation}-{cfg.baseline}.pkl.gz'
                if not normpath.exists():save_pickle(normpath,normal)
                save_reference(cellroot/'reference.pkl.gz',artifact,normpath,forecastpath,
                               legacypath if artifact.legacy and artifact.legacy.get('status')=='ok' else None)
                score_cpu=time.process_time()
                rawcal=score_method(artifact,aggregates('cal',cfg.aggregation),caches['cal'])
                suite='error' if cfg.method in ('R13','R14') else 'selection'
                raw=score_method(artifact,aggregates(suite,cfg.aggregation),caches[suite])
                score_cpu=time.process_time()-score_cpu
                _save_raw(rawcal,cellroot/'cal-raw.npz');_save_raw(raw,cellroot/'selection-raw.npz')
                published=publisher.publish(rawcal,row['cadences'])
                selected=publisher.publish(raw,row['cadences'])
                families=_families(raw)
                views=[('baseline',None,('count','latency','relation'))] if cfg.method=='R00' else [
                    entry for family in families for entry in [('branch',family,(family,)),('replacement_package',family,_cuts_columns(family))]]
                for view,family,columns in views:
                    calinput=published if view!='replacement_package' else _package(published,basecal,family)
                    testinput=selected if view!='replacement_package' else _package(selected,baseselect[suite],family)
                    calibration=metrics.calibrate(calinput,score_columns=columns)
                    name=view+(f'-{family}' if family else '')
                    write_json(cellroot/f'calibration-{name}.json',calibration)
                    for cadence,frame in testinput.items():
                        if calibration['passed']:
                            prediction=metrics.apply_thresholds(frame,calibration['thresholds'])
                            evaluated=metrics.evaluate(prediction,data[suite].events)
                        else:
                            prediction=frame.copy(deep=False);prediction['prediction']=np.nan;prediction['score_supported']=False
                            evaluated=metrics.evaluate(prediction,data[suite].events)
                        write_json(cellroot/f'evaluation-{name}-C{cadence}.json',evaluated)
                        unbounded=cfg.method=='R07' and cfg.variant.get('unbounded',False)
                        record={'seed':seed,'cell_id':cell,'method':cfg.method,'window':None if unbounded else cfg.window,
                                'configuration_window_minutes':cfg.window,
                                'parameter_kind':next(iter(raw.values())).parameter_kind,
                                'effective_window_minutes':None if unbounded or cfg.method=='R06' else cfg.window,
                                'halflife_minutes':cfg.window if cfg.method=='R06' else None,
                                'maximum_memory_minutes':cfg.window if cfg.method=='R07' and not unbounded else None,
                                'aggregation':cfg.aggregation,'baseline':cfg.baseline,'variant':cfg.variant,
                                'cohort':row['cohort'],'cadence':cadence,'view':view,'family':family,'suite':suite,
                                'calibration_passed':calibration['passed'],'thresholds':calibration['thresholds'],
                                'reference_file':str(cellroot/'reference.pkl.gz'),
                                'prediction_fingerprint':prediction_fingerprint(prediction),
                                'incremental_shared_score_cpu_seconds':score_cpu,
                                **_summary(evaluated['summary'])}
                        all_records.append(record)
                        for group_summary in evaluated['summary']:
                            coverage.append({**{k:v for k,v in record.items() if k not in ('thresholds','variant')},
                                             'group':group_summary['group'],'disposition':('completed' if calibration['passed'] else 'calibration_ineligible'),
                                             'normal_source_minutes':group_summary['normal_source_minutes'],
                                             'normal_all_branch_supported_minutes':group_summary['normal_all_branch_supported_minutes'],
                                             'reason':calibration.get('reason')})
                write_json(cellroot/'fit-diagnostics.json',{
                    'groups':{g:{k:v for k,v in state.items() if k!='statistics' and k not in ('inverse','mean')} for g,state in artifact.groups.items()},
                    'forecast':None if forecast is None else {g:{k:v for k,v in f.items() if k not in ('metrics','_prediction_cache')} for g,f in forecast.items()},
                    'fit_seconds':artifact.fit_seconds})
            except Exception as exc:
                disposition='failed';reason=f'{type(exc).__name__}:{exc}'
                write_json(cellroot/'failure.json',{'reason':reason,'traceback':traceback.format_exc()})
                for cadence in row['cadences']:
                    coverage.append({'seed':seed,'cell_id':cell,'method':cfg.method,'window':cfg.window,
                                     'aggregation':cfg.aggregation,'baseline':cfg.baseline,'cadence':cadence,
                                     'cohort':row['cohort'],'disposition':'failed','reason':reason})
            timing={'seed':seed,'cell_id':cell,'method':cfg.method,'window':cfg.window,
                    'status':disposition,'reason':reason,'wall_seconds':time.perf_counter()-step,
                    'cpu_seconds':time.process_time()-cpu,'config_number':number+1,'configs_total':len(configs)}
            write_json(cellroot/'timing.json',timing)
            print(json.dumps(timing,default=_jsonable),flush=True)
            if (number+1)%100==0:
                pd.DataFrame(coverage).to_csv(output/'coverage.partial.csv',index=False)
                pd.DataFrame(all_records).to_pickle(output/'results.partial.pkl.gz',compression='gzip')
        # Bound memory by seed; source generator caches are separately bounded.
        pd.DataFrame(coverage).to_csv(output/'coverage.csv',index=False)
        pd.DataFrame(all_records).to_pickle(output/'results.pkl.gz',compression='gzip')
        pd.DataFrame(all_records).to_csv(output/'results.csv',index=False)
    manifest={'phase':phase,'status':'completed' if not any(c['disposition']=='failed' for c in coverage) else 'completed_with_failures',
              'seeds':dataset.PROTOCOL['development_seeds'],'configs_per_seed':len(configs),
              'expected_configs_per_seed':reg['actual_configurations'],'wall_seconds':time.perf_counter()-started,
              'source_hashes':source_digest(),'registry_hash':file_hash(output/'method_registry.json'),
              'result_rows':len(all_records),'coverage_rows':len(coverage),'final_data_generated':False}
    write_json(output/'development-manifest.json',manifest)
    if limit is None:seal_selection(output,all_records)
    return manifest


def seal_selection(output,records):
    output=Path(output);records=pd.DataFrame(records)
    eligible=records[(records.view=='replacement_package')&records.calibration_passed]
    ranking=[]
    for (cell,family,cadence),part in eligible.groupby(['cell_id','family','cadence'],dropna=False):
        if set(part.seed)!=set(dataset.PROTOCOL['development_seeds']):continue
        ranking.append({'cell_id':cell,'family':family,'cadence':int(cadence),'method':part.method.iloc[0],
                        'suite':part.suite.iloc[0],
                        'window':int(part.window.iloc[0]) if part.window.notna().any() else None,
                        'configuration_window_minutes':int(part.configuration_window_minutes.iloc[0]),
                        'parameter_kind':part.parameter_kind.iloc[0],
                        'aggregation':int(part.aggregation.iloc[0]),
                        'baseline':part.baseline.iloc[0],
                        'five_seed_prediction_signature':hashlib.sha256(json.dumps(
                            sorted(zip(part.seed.tolist(),part.prediction_fingerprint.tolist())),sort_keys=True).encode()).hexdigest(),
                        **{key:float(part[key].mean()) if part[key].notna().any() else None for key in
                           ('independent_all_branch_availability','macro_family_recall','macro_subtype_recall',
                            'mean_loss360_minutes','positive_time_fraction','incremental_shared_score_cpu_seconds')}})
    def rank(x):
        return (-(x['independent_all_branch_availability'] or 0),-(x['macro_family_recall'] or 0),
                x['mean_loss360_minutes'] if x['mean_loss360_minutes'] is not None else 1e9,
                x['positive_time_fraction'] if x['positive_time_fraction'] is not None else 1e9,
                x['incremental_shared_score_cpu_seconds'] if x['incremental_shared_score_cpu_seconds'] is not None else 1e9,
                x['method'],x['window'] if x['window'] is not None else float('inf'),x['aggregation'],x['baseline'],x['cadence'],x['cell_id'])
    ranking.sort(key=rank)
    representatives={};unique=[]
    # Exact same-input output/support equivalence is distinct from equal hit totals.
    for row in sorted(ranking,key=lambda x:(x['method'],x['window'] if x['window'] is not None else float('inf'),x['aggregation'],x['cadence'],x['cell_id'])):
        signature=(row['suite'],row['five_seed_prediction_signature'])
        if signature not in representatives:
            representatives[signature]=row['cell_id'];unique.append(row)
        else:row['equivalent_prediction_representative']=representatives[signature]
    unique.sort(key=rank)
    def dominates(left,right):
        keys=(('independent_all_branch_availability',-1),('macro_family_recall',-1),
              ('mean_loss360_minutes',1),('positive_time_fraction',1),('incremental_shared_score_cpu_seconds',1))
        a=[direction*left[key] for key,direction in keys if left[key] is not None and right[key] is not None]
        b=[direction*right[key] for key,direction in keys if left[key] is not None and right[key] is not None]
        return bool(a) and all(x<=y for x,y in zip(a,b)) and any(x<y for x,y in zip(a,b))
    nondominated=[]
    for row in [row for row in unique if row['suite']=='selection']:
        if any(dominates(other,row) for other in nondominated):continue
        nondominated=[other for other in nondominated if not dominates(row,other)]
        nondominated.append(row)
    selected=sorted(nondominated,key=rank)[:3]
    error_selected=[]
    for method in ('R13','R14'):
        choices=[row for row in ranking if row['suite']=='error' and row['method']==method]
        if choices:error_selected.append(sorted(choices,key=rank)[0])
    lock={'version':'trend-sr-selection-lock-v1','created_at':datetime.now(timezone.utc).isoformat(),
          'source_hashes':source_digest(),'registry_hash':file_hash(output/'method_registry.json'),
          'selection_rule':'all5seed_cal_eligible; exact five-seed prediction+finite+support equivalence dedup(simple representative); nondominated alternatives; independent availability, family macro, loss360, normal occupancy, shared incremental score CPU, deterministic simplicity',
          'baseline':{'method':'R00','window':60,'cadence':5,'aggregation':1,'baseline':'B1'},
          'selected':selected,'error_selected':error_selected,
          'selection_strata':'main15 subtypes only in selected; error3 separate, best fixed setting per R13/R14',
          'final_reference_seed_map':dataset.PROTOCOL['final_reference_seed_map'],
          'final_data_generated':False}
    files={}
    for seed in dataset.PROTOCOL['development_seeds']:
        root=output/f'seed-{seed}'
        paths=[root/'baseline-reference.pkl.gz',root/'baseline-calibration.json']
        from . import recovery
        cal=dataset.generate(seed,'cal','normal')
        for number,entry in enumerate([lock['baseline'],*selected,*error_selected]):
            guard=recovery.normal_guard(load_selected_candidate(output,entry,seed),cal,CADENCES)
            guardpath=root/f'recovery-calibration-guard-candidate-{number}.json'
            write_json(guardpath,guard);paths.append(guardpath)
        for row in [*selected,*error_selected]:
            cell=root/'cells'/row['cell_id']
            paths += [cell/'reference.pkl.gz',cell/f"calibration-replacement_package-{row['family']}.json"]
            if row['suite']=='error':paths.append(cell/'calibration-branch-error.json')
        for path in paths:
            files[str(path)]=file_hash(path)
            if path.suffixes[-2:]==['.pkl','.gz']:
                payload=load_pickle(path)
                for key in ('normal_path','forecast_path','legacy_path'):
                    if payload.get(key):
                        dependency=(path.parent/payload[key]).resolve()
                        files[str(dependency)]=file_hash(dependency)
    lock['artifact_hashes']=files
    write_json(output/'selection-ranking.json',ranking)
    write_json(output/'error-selection-ranking.json',[row for row in ranking if row['suite']=='error'])
    write_json(output/'selection-lock.json',lock)
    write_json(output/'selection-lock-sha256.json',{'sha256':file_hash(output/'selection-lock.json')})
    return lock


def load_selected_candidate(development,entry,seed):
    root=Path(development)/f'seed-{seed}'
    baseline=load_reference(root/'baseline-reference.pkl.gz')
    baseline_calibration=json.loads((root/'baseline-calibration.json').read_text(encoding='utf-8'))
    if entry.get('method')=='R00':
        return {'baseline':baseline,'candidate':baseline,'entry':entry,
                'calibration':baseline_calibration,'baseline_calibration':baseline_calibration}
    cell=root/'cells'/entry['cell_id']
    return {'baseline':baseline,'candidate':load_reference(cell/'reference.pkl.gz'),'entry':entry,
            'baseline_calibration':baseline_calibration,
            'branch_calibration':json.loads((cell/f"calibration-branch-{entry['family']}.json").read_text(encoding='utf-8')),
            'calibration':json.loads((cell/f"calibration-replacement_package-{entry['family']}.json").read_text(encoding='utf-8'))}


def score_candidate_published(bundle,item,cadences):
    """Score once, publish frozen cuts at each requested C; no fit or new cuts."""
    candidate=bundle['candidate'];entry=bundle['entry'];publisher=Publisher()
    aggs=prepare(item,candidate.config.aggregation)
    raw=score_method(candidate,aggs)
    published=publisher.publish(raw,cadences)
    if entry.get('method')!='R00':
        baseaggs=prepare(item,1);base=score_method(bundle['baseline'],baseaggs)
        published=_package(published,publisher.publish(base,[5]),entry['family'])
    calibration=bundle['calibration']
    for cadence,frame in published.items():
        if calibration['passed']:published[cadence]=metrics.apply_thresholds(frame,calibration['thresholds'])
        else:
            frame=frame.copy();frame['prediction']=np.nan;frame['score_supported']=False
            published[cadence]=frame
    return published


def score_candidate_bundle(bundle,item):
    """Public immutable fit/load/score API for the separate stress runner."""
    return score_candidate_published(bundle,item,[bundle['entry']['cadence']])[bundle['entry']['cadence']]


def run_final(development,output,gate_path):
    development=Path(development);gate=_gate(gate_path,'final')
    lockpath=development/'selection-lock.json';lock=json.loads(lockpath.read_text(encoding='utf-8'))
    if gate.get('selection_lock_sha256')!=file_hash(lockpath):raise ValueError('final_gate_lock_mismatch')
    if lock['source_hashes']!=source_digest():raise ValueError('frozen_source_changed')
    for path,digest in lock['artifact_hashes'].items():
        if file_hash(path)!=digest:raise ValueError(f'frozen_artifact_changed:{path}')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'authorization.json',gate);write_json(output/'selection-lock.json',lock)
    records=[]
    for seed in dataset.PROTOCOL['final_seeds']:
        devseed=dataset.PROTOCOL['final_reference_seed_map'][str(seed)]
        main=dataset.generate(seed,'final','main',authorize_final=True)
        error=dataset.generate(seed,'final','error',authorize_final=True)
        normal=dataset.generate(seed,'final','normal',authorize_final=True)
        recurrence=dataset.generate(seed,'final','recurrence',authorize_final=True)
        sources=[('main',main),('error',error),('normal',normal),('recurrence',recurrence)]
        for name,item in sources:
            _export_source(item,output/f'seed-{seed}'/'sources'/name)
        entries=[lock['baseline'],*lock['selected'],*lock.get('error_selected',[])]
        for number,entry in enumerate(entries):
            bundle=load_selected_candidate(development,entry,devseed)
            root=output/f'seed-{seed}'/f'candidate-{number}'
            root.mkdir(parents=True)
            for name,item in sources:
                prediction=score_candidate_bundle(bundle,item)
                evaluated=metrics.evaluate(prediction,item.events)
                suite_root=root/name;suite_root.mkdir()
                prediction.to_pickle(suite_root/'minute-predictions.pkl.gz',compression='gzip')
                write_json(suite_root/'evaluation.json',evaluated)
                from . import recovery
                fast,ledger=recovery.apply_fast(bundle,item,prediction)
                fast.to_pickle(suite_root/'fast-minute-predictions.pkl.gz',compression='gzip')
                save_pickle(suite_root/'fast-policy-ledger.pkl.gz',ledger)
                paired=recovery.compare(prediction,fast,item.events)
                write_json(suite_root/'fast-paired-evaluation.json',paired)
                if entry.get('suite')=='error' and name=='error':
                    branch=bundle['branch_calibration']
                    if branch['passed']:standalone=metrics.apply_thresholds(prediction,branch['thresholds'])
                    else:
                        standalone=prediction.copy();standalone['prediction']=np.nan;standalone['score_supported']=False
                    standalone.to_pickle(suite_root/'error-branch-minute-predictions.pkl.gz',compression='gzip')
                    write_json(suite_root/'error-branch-evaluation.json',metrics.evaluate(standalone,item.events))
                for summary in evaluated['summary']:
                    records.append({'seed':seed,'candidate':number,'entry':entry,'suite':name,
                                    'selection_stratum':entry.get('suite','fixed_baseline'),**summary})
    pd.DataFrame(records).to_pickle(output/'results.pkl.gz',compression='gzip')
    pd.DataFrame(records).to_csv(output/'results.csv',index=False)
    write_json(output/'final-manifest.json',{'status':'completed','new_fits':0,'new_calibrations':0,
               'candidate_reselection':False,'selection_lock_sha256':file_hash(lockpath),'source_hashes':source_digest()})
    return records


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['pilot','registry','development','final'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--gate')
    parser.add_argument('--development')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--pilot-window',type=int,default=60)
    parser.add_argument('--pilot-train-days',type=int,default=10)
    parser.add_argument('--pilot-score-days',type=int,default=2)
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if output.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise ValueError('generated_artifacts_must_stay_outside_the_git_repository')
    if args.phase=='registry':write_json(output,registry())
    elif args.phase=='pilot':run_pilot(output,args.pilot_window,args.pilot_train_days,args.pilot_score_days)
    elif args.phase=='development':run_development(output,args.gate,limit=args.limit)
    else:run_final(args.development,output,args.gate)


if __name__=='__main__':main()
