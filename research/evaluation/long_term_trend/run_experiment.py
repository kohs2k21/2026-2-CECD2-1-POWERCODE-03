"""Train/selection screen and physically separate locked holdout commands."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import platform
import sys
import time
from pathlib import Path

os.environ.setdefault('PYTHONDONTWRITEBYTECODE','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('MKL_NUM_THREADS','1')
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits
from research.evaluation.long_term_trend import dataset,features,evaluation as ev
from research.evaluation.long_term_trend.models import RobustEncoder,StatisticalReference,fit_forest,SETTINGS
from research.evaluation.if_feature_comparison.models import load_eif,verify_sources


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_default(value):
    if isinstance(value,(np.integer,np.floating)):return value.item()
    if isinstance(value,np.bool_):return bool(value)
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,(pd.Timestamp,Path)):return str(value)
    raise TypeError(type(value).__name__)


def json_nullable(value):
    """Optional ledger/metric missingness is JSON null, never a normal zero."""
    if value is None or value is pd.NA or value is pd.NaT:return None
    if isinstance(value,np.ndarray):return json_nullable(value.tolist())
    if isinstance(value,np.generic):return json_nullable(value.item())
    if isinstance(value,float) and not np.isfinite(value):return None
    if isinstance(value,dict):return {key:json_nullable(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [json_nullable(item) for item in value]
    return value


def write_json(path,value):
    Path(path).write_text(json.dumps(json_nullable(value),default=json_default,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def save_pickle(path,value):
    with Path(path).open('wb') as handle:pickle.dump(value,handle,protocol=5)
    return {'path':str(Path(path).name),'sha256':digest(path),'bytes':Path(path).stat().st_size}


def source_hashes():
    paths=[Path(__file__).with_name(n) for n in ('run_experiment.py','evaluation.py','models.py','dataset.py','features.py','protocol.json')]
    paths += [ROOT/'research/evaluation/if_feature_comparison/models.py',ROOT/'research/evaluation/if_feature_comparison/third_party/manifest.json']
    return {str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in paths}


def canonical(pack):
    meta=pack['metadata'].reset_index(drop=True).copy()
    meta['decision_tick']=pd.to_datetime(meta.decision_tick,utc=True)
    meta['state']=meta.statecode.astype(str)
    meta['eligible']=meta.ready.astype(bool)
    meta['source_known']=meta.source_known_now.astype(bool)
    if 'row_id' not in meta:meta['row_id']=meta.group.astype(str)+'|'+meta.decision_tick.astype(str)
    if meta.row_id.duplicated().any():raise ValueError('Duplicate decision row key')
    lag=(meta.decision_tick-pd.to_datetime(meta.window_end,utc=True)).dt.total_seconds()/60
    if not lag.eq(2).all():raise ValueError('As-of watermark must be exactly two minutes')
    for variant,X in pack['X'].items():
        if len(X)!=len(meta):raise ValueError('Feature metadata alignment')
        if not np.isfinite(np.asarray(X)[meta.eligible]).all():raise ValueError('Nonfinite ready feature')
    return meta


def build_pack(item,reference,window):
    pack=features.build_features(item['stream'],reference,window,cadence_minutes=1)
    tick=pd.to_datetime(pack['metadata'].decision_tick,utc=True)
    mask=(tick>=pd.Timestamp(item['manifest']['scored_start']))&(tick<pd.Timestamp(item['manifest']['scored_end']))
    pack['metadata']=pack['metadata'].loc[mask].reset_index(drop=True)
    for kind in ('X','feature_validity'):
        pack[kind]={name:np.asarray(value)[mask.to_numpy()] for name,value in pack[kind].items()}
    return pack


def row_hash(meta):return hashlib.sha256('\n'.join(meta.row_id.astype(str)).encode()).hexdigest()


def timed(fn,*args,**kwargs):
    wall=time.perf_counter();cpu=time.process_time();result=fn(*args,**kwargs)
    return result,{'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu}


def scores_for(artifact,pack,meta,grid=None):
    mask=meta.eligible.to_numpy(bool).copy()
    if grid is not None:mask &= ev.decision_grid(meta,grid)
    values=np.full(len(meta),np.nan)
    X=pack['X'][artifact['variant']]
    if mask.any():
        if artifact['algorithm']=='sr':values[mask]=artifact['model'].score(X[mask],pack['feature_validity'][artifact['variant']][mask])
        else:values[mask]=artifact['model'].score_samples(artifact['encoder'].transform(X[mask]))
    if not np.isfinite(values[mask]).all():raise ValueError('Eligible score is missing/nonfinite')
    return values


def normal_scale(values):
    values=np.asarray(values);values=values[np.isfinite(values)]
    if not len(values):raise ValueError('No calibration scale')
    return max(float(np.quantile(values,.95)-np.median(values)),1e-6)


def acceptable(summary,p):
    if not summary['eligible_scoring_complete'] or not summary['eligible_scheduled_rows']:return False
    for row in summary['normal']:
        if not row['normal_common_eligible_hours']:continue
        if row['false_alarm_episodes_per_hour'] is None or row['positive_time_fraction'] is None:return False
        if row['false_alarm_episodes_per_hour']>p['calibration_alarm_budget_per_group_hour']+1e-12:return False
        if row['positive_time_fraction']>p['calibration_positive_tick_fraction_per_group']+1e-12:return False
    return True


def reduce_records(records):
    if not records:return {'events':0,'hits':0,'recall':None,'subgroups':{}}
    hits=sum(r['hit'] for r in records)
    subgroups={}
    for key in ('scenario','cohort'):
        if any(key not in r for r in records):raise ValueError(f'Missing required event {key}')
        subgroups[key]={}
        for value in sorted({r[key] for r in records}):
            subset=[r for r in records if r[key]==value];n=len(subset);h=sum(r['hit'] for r in subset)
            subgroups[key][value]={'events':n,'hits':h,'recall':h/n,'unassessed':sum(r['unassessed'] for r in subset)}
    macro=float(np.mean([r['recall'] for r in subgroups['scenario'].values()]))
    return {'events':len(records),'hits':hits,'recall':hits/len(records),'macro_scenario_recall':macro,
            'early_hits_subset':sum(r['early_hit'] for r in records),'unassessed':sum(r['unassessed'] for r in records),
            'preexisting_only':sum(r['preexisting_only'] for r in records),
            'mean_bounded_delay_minutes':float(np.mean([r['bounded_delay_minutes'] for r in records])),
            'subgroups':subgroups}


def choose(candidates,p,algorithm):
    choices=[]
    for variant in (['trend'] if algorithm=='sr' else p['variants']):
        for window in p['window_hours']:
            rows=[r for r in candidates if r['algorithm']==algorithm and r['variant']==variant and r['window_hours']==window]
            eligible=len(rows)==len(p['seeds']) and all(r.get('status')=='ok' and acceptable(r['summary'],p) for r in rows)
            events=[e for r in rows for e in r.get('events',[])];aggregate=reduce_records(events)
            false=sum(g['false_alarm_episodes'] for r in rows for g in r.get('summary',{}).get('normal',[]))
            choices.append({'algorithm':algorithm,'variant':variant,'window_hours':window,'eligible':eligible,
                            'aggregate':aggregate,'false_alarm_episodes':false})
    good=[r for r in choices if r['eligible']]
    if good:
        winner=min(good,key=lambda r:(-r['aggregate']['macro_scenario_recall'],r['aggregate']['mean_bounded_delay_minutes'],r['false_alarm_episodes'],r['window_hours'],r['variant']!='base'))
        return {**winner,'selection_status':'eligible_winner'},choices
    fallback={'algorithm':algorithm,'variant':'trend' if algorithm=='sr' else 'base','window_hours':1,
              'eligible':False,'selection_status':'ineligible_diagnostic_reference'}
    return fallback,choices


def key_for(algorithm,variant,window,seed):return f'{algorithm}-{variant}-w{window:02d}-s{seed}'


def ensure_output(path,fresh=False):
    path=Path(path).resolve()
    if path==ROOT or ROOT in path.parents:raise ValueError('Output must be outside the repository')
    if fresh and path.exists() and any(path.iterdir()):raise ValueError('Use a fresh nonempty-free output directory')
    path.mkdir(parents=True,exist_ok=True)
    return path


def screen(output):
    p=dataset.load_protocol();source=source_hashes();verify_sources()
    output=ensure_output(output,True);(output/'models').mkdir();(output/'cache').mkdir()
    manifest={'status':'running','opened_splits':['train','calibration','selection'],'sources':source,'protocol':p,
              'environment':{'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,'pandas':pd.__version__,'sklearn':sklearn.__version__},
              'forest_configuration':SETTINGS,'fits_expected':p['fit_budget']['fits'],'fits_attempted':0,'fits_completed':0,'failures':[],
              'artifacts':{},'references':{},'datasets':[],'costs':[],'started_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(output/'manifest.json',manifest);candidates=[]
    for seed in p['seeds']:
        splits={name:dataset.load_split(seed,name,p) for name in ('train','calibration','selection')}
        manifest['datasets'] += [x['manifest'] for x in splits.values()]
        reference=features.fit_reference(splits['train']['stream'],p)
        refname=f'reference-s{seed}.pkl';manifest['references'][str(seed)]=save_pickle(output/'models'/refname,reference)
        common={}
        for window in p['window_hours']:
            packs={};metas={}
            for split,item in splits.items():
                packs[split],cost=timed(build_pack,item,reference,window)
                metas[split]=canonical(packs[split]);h=row_hash(metas[split])
                signature=(h,hashlib.sha256(metas[split].eligible.to_numpy(bool).tobytes()).hexdigest())
                if split in common and signature!=common[split]:raise ValueError('Windows must share row keys and ready masks')
                common[split]=signature;manifest['costs'].append({'seed':seed,'window':window,'stage':'features','split':split,**cost})
            trainmask=metas['train'].eligible.to_numpy(bool)&metas['train'].train_mask.to_numpy(bool)&ev.decision_grid(metas['train'],5)
            train_indices=np.flatnonzero(trainmask)
            if len(train_indices)>p['training_cap']:
                train_indices=np.sort(np.random.default_rng(seed).choice(train_indices,p['training_cap'],replace=False))
            for algorithm,variant in [('sr','trend')]+[(a,v) for a in ('if','eif') for v in p['variants']]:
                key=key_for(algorithm,variant,window,seed);record={'key':key,'seed':seed,'algorithm':algorithm,'variant':variant,'window_hours':window}
                try:
                    X=packs['train']['X'][variant][train_indices];encoder=None
                    if algorithm=='sr':
                        model,cost=timed(StatisticalReference.fit,X,packs['train']['feature_validity'][variant][train_indices],packs['train']['feature_names'][variant],p['sr_neutral_feature_names'])
                    else:
                        manifest['fits_attempted']+=1;encoder=RobustEncoder.fit(X)
                        model,cost=timed(fit_forest,algorithm,seed,encoder.transform(X));manifest['fits_completed']+=1
                    manifest['costs'].append({'key':key,'stage':'train_statistics' if algorithm=='sr' else 'fit',**cost})
                    artifact={'algorithm':algorithm,'variant':variant,'seed':seed,'window_hours':window,'model':model,'encoder':encoder,'training_rows':len(train_indices),'feature_names':packs['train']['feature_names'][variant]}
                    cal,ccost=timed(scores_for,artifact,packs['calibration'],metas['calibration'])
                    selected,scost=timed(scores_for,artifact,packs['selection'],metas['selection'],5)
                    calibration,kcost=timed(ev.calibrate,cal,metas['calibration'],p)
                    artifact.update(calibration=calibration,calibration_spread=normal_scale(cal))
                    (summary,events,_,_),ecost=timed(ev.evaluate,selected,calibration['threshold'],metas['selection'],splits['selection']['events'],p,5)
                    record.update(status='ok',summary=summary,events=events,calibration=calibration,calibration_spread=artifact['calibration_spread'],
                                  calibration_unique_scores=int(len(np.unique(cal[np.isfinite(cal)]))),train_rows=len(train_indices))
                    manifest['artifacts'][key]=save_pickle(output/'models'/f'{key}.pkl',artifact)
                    np.savez_compressed(output/'cache'/f'{key}.npz',calibration=cal,selection=selected)
                    for stage,c in [('calibration_score',ccost),('selection_score',scost),('calibration_intervals',kcost),('selection_evaluation',ecost)]:manifest['costs'].append({'key':key,'stage':stage,**c})
                except Exception as exc:
                    record.update(status='failed',error=f'{type(exc).__name__}: {exc}');manifest['failures'].append({'key':key,'error':record['error']})
                candidates.append(record)
                write_json(output/'selection-candidates.json',candidates);write_json(output/'manifest.json',manifest)
                print(json.dumps({'key':key,'status':record['status'],'fits_completed':manifest['fits_completed']}),flush=True)
            if window==1:
                save_pickle(output/'cache'/f'metadata-s{seed}.pkl',{'calibration':metas['calibration'],'selection':metas['selection'],'events':splits['selection']['events']})
    winners={};choices={}
    for algorithm in ('sr','if','eif'):winners[algorithm],choices[algorithm]=choose(candidates,p,algorithm)
    lock={'version':p['version'],'sources':source,'protocol':p,'seeds':p['seeds'],'winners':winners,'arms':{},'selection_objective':'macro scenario actual-start recall; bounded delay; FP episode count; smaller W; base',
          'opened_splits':['train','calibration','selection'],'test_used':False,'artifacts':manifest['artifacts'],'references':manifest['references'],
          'datasets':manifest['datasets']}
    for seed in p['seeds']:
        with (output/'cache'/f'metadata-s{seed}.pkl').open('rb') as f:meta=pickle.load(f)
        branch={}
        for a in ('sr','if','eif'):
            w=winners[a];key=key_for(a,w['variant'],w['window_hours'],seed)
            row=next(r for r in candidates if r['key']==key)
            branch[a]={'key':key,'status':w['selection_status'],'available':row['status']=='ok'}
            if row['status']=='ok':branch[a].update(threshold=row['calibration']['threshold'],spread=row['calibration_spread'])
        arms={a:{'branches':[branch[a]],'threshold':0.0,'selection_status':branch[a]['status']} for a in ('sr','if','eif')}
        for a in ('if','eif'):
            name=f'sr+{a}';arms[name]={'branches':[branch['sr'],branch[a]],'threshold':None,'selection_status':'eligible_winner' if winners['sr']['eligible'] and winners[a]['eligible'] else 'ineligible_diagnostic_reference'}
            if all(b['available'] for b in arms[name]['branches']):
                cal=[];selection=[]
                for b in arms[name]['branches']:
                    with np.load(output/'cache'/f"{b['key']}.npz") as cached:
                        cal.append((cached['calibration']-b['threshold'])/b['spread'])
                        selection.append((cached['selection']-b['threshold'])/b['spread'])
                joint=np.maximum.reduce(cal);calibration=ev.calibrate(joint,meta['calibration'],p,minimum_threshold=0)
                arms[name]['threshold']=calibration['threshold'];arms[name]['calibration']=calibration
                joint_summary,_,_,_=ev.evaluate(np.maximum.reduce(selection),calibration['threshold'],meta['selection'],meta['events'],p,5)
                arms[name]['selection_summary']=joint_summary
                if not acceptable(joint_summary,p):arms[name]['selection_status']='ineligible_diagnostic_reference'
        lock['arms'][str(seed)]=arms
    if source_hashes()!=source:raise RuntimeError('Source mutation during screen; no selection lock issued')
    write_json(output/'selection-aggregate.json',choices);write_json(output/'selection-lock.json',lock)
    manifest.update(status='completed',completed_at=pd.Timestamp.now(tz='UTC').isoformat(),selection_lock_sha256=digest(output/'selection-lock.json'))
    manifest['disk_bytes']=sum(f.stat().st_size for f in output.rglob('*') if f.is_file())
    write_json(output/'manifest.json',manifest)


def checked_pickle(screen,record):
    path=screen/'models'/record['path']
    if digest(path)!=record['sha256']:raise ValueError(f'Artifact hash mismatch: {path.name}')
    with path.open('rb') as f:return pickle.load(f)


def missing_reference_metadata(item,p):
    """Preserve intended decisions without claiming an unsupported ready mask."""
    parts=[]
    for group,frame in item['stream'].groupby('group',sort=False):
        frame=frame.sort_values('bin_start').reset_index(drop=True)
        tick=pd.to_datetime(frame.bin_start,utc=True)
        prior=np.arange(len(frame))-p['watermark_lag_minutes']-1
        safe=np.maximum(prior,0)
        known=frame.source_complete.to_numpy(bool)&np.isfinite(frame['count'].to_numpy(float))
        known &= (frame['count'].to_numpy(float)==0)|np.isfinite(frame.duration_ms.to_numpy(float))
        available=pd.to_datetime(frame.available_at,utc=True).to_numpy()[safe]<=tick.to_numpy()
        source_known=(prior>=0)&known[safe]&available
        source_late=(prior>=0)&frame.source_complete.to_numpy(bool)[safe]&~available
        registry=item['registry']['aliases']
        mapped=np.array([registry.get(json.dumps([a,i],ensure_ascii=False)) for a,i in zip(frame.adapter,frame.interface)],object)
        category_observed=(prior>=0)&available
        category_supported=category_observed&(mapped[safe]==group)
        state=np.full(len(frame),'MODEL_NOT_READY',object)
        state[category_observed&~category_supported]='NEW_CATEGORY'
        state[source_late]='SOURCE_LATE'
        state[(prior<0)|~frame.source_complete.to_numpy(bool)[safe]|(available&~known[safe])]='SOURCE_UNKNOWN'
        keep=(tick>=pd.Timestamp(item['manifest']['scored_start']))&(tick<pd.Timestamp(item['manifest']['scored_end']))
        part=pd.DataFrame({'row_id':frame['split'].astype(str)+'|'+frame.seed.astype(str)+'|'+str(group)+'|'+tick.astype(str),
                           'group':group,'decision_tick':tick,'state':state,
                           'eligible':False,'source_known':source_known,'source_known_now':source_known,'source_late_now':source_late,
                           'category_supported':pd.array([bool(v) if observed else None for v,observed in zip(category_supported,category_observed)],dtype='boolean'),
                           'category_observed':category_observed,'common_eligible_known':False,'model_ready':False,'readiness':False,
                           'readiness_reason':'frozen training reference unavailable; common context/category readiness unknown'})
        parts.append(part.loc[keep])
    return pd.concat(parts,ignore_index=True)


def holdout(screen_path,output,split):
    screen_path=Path(screen_path).resolve();output=ensure_output(output,True)
    lock_path=screen_path/'selection-lock.json';lock=json.loads(lock_path.read_text(encoding='utf-8'))
    manifest=json.loads((screen_path/'manifest.json').read_text(encoding='utf-8'))
    if digest(lock_path)!=manifest['selection_lock_sha256']:raise ValueError('Selection lock hash mismatch')
    if lock['sources']!=source_hashes():raise ValueError('Source mutation since selection lock')
    p=lock['protocol'];verify_sources();load_eif();results=[];events_all=[];costs=[];data_manifests=[];common_checks=[];failures=[]
    for seed in lock['seeds']:
        item=dataset.load_split(seed,split,p);data_manifests.append(item['manifest'])
        ref_record=lock['references'].get(str(seed))
        reference=checked_pickle(screen_path,ref_record) if ref_record and (screen_path/'models'/ref_record['path']).is_file() else None
        if reference is None:failures.append({'seed':seed,'reason':'frozen training reference unavailable; all intended events retained unassessed'})
        branches={};packs={};metas={}
        for a in ('sr','if','eif'):
            w=lock['winners'][a];key=key_for(a,w['variant'],w['window_hours'],seed)
            if reference is None or key not in lock['artifacts'] or not (screen_path/'models'/lock['artifacts'][key]['path']).is_file():
                failures.append({'seed':seed,'key':key,'reason':'model or frozen reference unavailable'})
                continue
            artifact=checked_pickle(screen_path,lock['artifacts'][key]);window=w['window_hours']
            if window not in packs:
                packs[window],cost=timed(build_pack,item,reference,window)
                metas[window]=canonical(packs[window]);costs.append({'seed':seed,'window':window,'stage':'features_full_minute',**cost})
            raw,cost=timed(scores_for,artifact,packs[window],metas[window]);costs.append({'seed':seed,'algorithm':a,'stage':'score_full_minute_batch',**cost})
            for cadence in (p['cadence_candidates'] if split=='cadence_holdout' else [5]):
                grid_raw,gridcost=timed(scores_for,artifact,packs[window],metas[window],cadence)
                grid=ev.decision_grid(metas[window],cadence)&metas[window].eligible.to_numpy(bool)
                np.testing.assert_allclose(grid_raw[grid],raw[grid],rtol=1e-12,atol=1e-12)
                costs.append({'seed':seed,'algorithm':a,'cadence':cadence,'stage':'score_native_grid_batch','scored_rows':int(grid.sum()),**gridcost})
                if split=='cadence_holdout':
                    sample_ticks=np.sort(metas[window].loc[grid,'decision_tick'].unique())
                    sample_ticks=sample_ticks[np.unique(np.linspace(0,len(sample_ticks)-1,min(120,len(sample_ticks))).astype(int))] if len(sample_ticks) else []
                    walls=[];cpus=[]
                    for tick in sample_ticks:
                        indices=np.flatnonzero(grid&metas[window].decision_tick.eq(tick).to_numpy())
                        small={'X':{artifact['variant']:packs[window]['X'][artifact['variant']][indices]},
                               'feature_validity':{artifact['variant']:packs[window]['feature_validity'][artifact['variant']][indices]}}
                        _,samplecost=timed(scores_for,artifact,small,metas[window].iloc[indices].reset_index(drop=True))
                        walls.append(samplecost['wall_seconds']);cpus.append(samplecost['cpu_seconds'])
                    costs.append({'seed':seed,'algorithm':a,'cadence':cadence,'stage':'sampled_ready_tick_group_batch',
                                  'samples':len(walls),'selection':'120 evenly spaced ready native ticks, fixed before test',
                                  'wall_p50_seconds':float(np.median(walls)) if walls else None,'wall_p95_seconds':float(np.quantile(walls,.95)) if walls else None,
                                  'cpu_total_seconds':float(sum(cpus)),'excludes_feature_build':True,'is_operating_sla':False})
            branches[key]=(raw-artifact['calibration']['threshold'])/artifact['calibration_spread']
        meta=next(iter(metas.values())) if metas else None
        if meta is None:
            if reference is not None:
                pack,cost=timed(build_pack,item,reference,1);meta=canonical(pack)
                meta.loc[meta.eligible,'state']='MODEL_NOT_READY'
                costs.append({'seed':seed,'window':1,'stage':'unassessed_metadata_features',**cost})
            else:meta=missing_reference_metadata(item,p)
        if metas and len({row_hash(m) for m in metas.values()})!=1:raise ValueError('Selected windows lack common row keys')
        for arm,config in lock['arms'][str(seed)].items():
            keys=[b['key'] for b in config['branches']]
            raw=np.maximum.reduce([branches[k] for k in keys]) if all(k in branches for k in keys) else np.full(len(meta),np.nan)
            threshold=config['threshold'] if config['threshold'] is not None else float('inf')
            cadences=p['cadence_candidates'] if split=='cadence_holdout' else [5]
            issued_by_c={}
            for cadence in cadences:
                (summary,records,issued,episodes),cost=timed(ev.evaluate,raw,threshold,meta,item['events'],p,cadence)
                issued_by_c[cadence]=issued
                results.append({'seed':seed,'arm':arm,'selection_status':config['selection_status'],'scope':'native_cadence','summary':summary})
                events_all += [{'seed':seed,'arm':arm,'cadence':cadence,**r} for r in records]
                costs.append({'seed':seed,'arm':arm,'cadence':cadence,'stage':'interval_event_evaluation',**cost})
                issued.to_csv(output/f'decisions-{arm}-s{seed}-c{cadence}.csv',index=False)
                episodes.to_csv(output/f'episodes-{arm}-s{seed}-c{cadence}.csv',index=False)
            if split=='cadence_holdout':
                common=issued_by_c[15][['row_id','score','prediction','decision_ready']].reset_index(drop=True)
                for cadence,issued in issued_by_c.items():
                    aligned=issued.set_index('row_id').loc[common.row_id].reset_index()[common.columns]
                    pd.testing.assert_frame_equal(common,aligned,check_dtype=False)
                    common_checks.append({'seed':seed,'arm':arm,'cadence':cadence,'common_rows':len(common),'identical_score_prediction':True})
                common_summary,_,_,_=ev.evaluate(raw,threshold,meta,item['events'],p,15)
                results.append({'seed':seed,'arm':arm,'selection_status':config['selection_status'],'scope':'common_15minute_grid','summary':common_summary})
            finite=raw[np.isfinite(raw)]
            costs.append({'seed':seed,'arm':arm,'stage':'score_diagnostic','finite_rows':len(finite),'unique_scores':len(np.unique(finite)),
                          'tied_row_fraction':float(1-len(np.unique(finite))/len(finite)) if len(finite) else None})
        write_json(output/'results.json',results);write_json(output/'events.json',events_all);write_json(output/'costs.json',costs)
    aggregates=[]
    for arm in lock['arms'][str(lock['seeds'][0])]:
        for cadence in (p['cadence_candidates'] if split=='cadence_holdout' else [5]):
            records=[r for r in events_all if r['arm']==arm and r['cadence']==cadence]
            native=[r for r in results if r['arm']==arm and r['scope']=='native_cadence' and r['summary']['cadence_minutes']==cadence]
            normal=[]
            for group in p['groups']:
                rows=[g for r in native for g in r['summary']['normal'] if g['group']==group]
                hours=sum(g['normal_monitored_hours'] for g in rows);false=sum(g['false_alarm_episodes'] for g in rows)
                positive=sum(g['positive_normal_minutes'] for g in rows)
                normal.append({'group':group,'normal_monitored_hours':hours,'false_alarm_episodes':false,
                               'false_alarm_episodes_per_hour':false/hours if hours else None,'positive_normal_minutes':positive,
                               'positive_time_fraction':positive/(hours*60) if hours else None})
            recalls=[r['summary']['recall'] for r in native if r['summary']['recall'] is not None]
            aggregates.append({'arm':arm,'cadence':cadence,**reduce_records(records),'normal':normal,
                               'paired_seed_recall_range':[min(recalls),max(recalls)] if recalls else None,
                               'eligible_scoring_complete':all(r['summary']['eligible_scoring_complete'] for r in native)})
    write_json(output/'aggregate.json',aggregates);write_json(output/'common-grid-checks.json',common_checks)
    write_json(output/'manifest.json',{'status':'completed','split':split,'opened_splits':[split],'selection_lock_sha256':digest(lock_path),'sources':lock['sources'],
               'new_fits':0,'recalibrations':0,'datasets':data_manifests,'failures':failures,'disk_bytes':sum(f.stat().st_size for f in output.rglob('*') if f.is_file()),
               'completed_at':pd.Timestamp.now(tz='UTC').isoformat()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for name in ('screen','evaluate-blind','evaluate-cadence'):
        child=sub.add_parser(name);child.add_argument('--output',required=True,type=Path)
        if name!='screen':child.add_argument('--screen',required=True,type=Path)
    args=parser.parse_args()
    with threadpool_limits(limits=1):
        if args.command=='screen':screen(args.output)
        else:holdout(args.screen,args.output,'blind' if args.command=='evaluate-blind' else 'cadence_holdout')


if __name__=='__main__':main()
