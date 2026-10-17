"""Normal-only fitting/cuts and physically separate frozen paired holdout evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import platform
import shutil
import sys
import time
from pathlib import Path

for name in ['OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS']:
    os.environ[name]='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_info,threadpool_limits
from research.evaluation.long_term_trend import evaluation as ev,features
from research.evaluation.long_term_trend.run_experiment import canonical,build_pack,json_nullable,json_default
from research.evaluation.if_feature_comparison.models import load_eif,verify_sources,SETTINGS
from research.evaluation.trend_family_comparison import dataset,detectors


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,value):
    Path(path).write_text(json.dumps(json_nullable(value),default=json_default,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def sources():
    paths=[]
    for package in ['long_term_trend','trend_family_comparison']:
        base=ROOT/'research/evaluation'/package
        paths.extend(base.rglob('*.py'));paths.append(base/'protocol.json')
    paths.extend(ROOT/'research/evaluation/if_feature_comparison'/p for p in ['models.py','third_party/manifest.json'])
    return {str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in sorted(set(paths))}


def ensure_output(path):
    path=Path(path).resolve()
    if path==ROOT or ROOT in path.parents:raise ValueError('Output must be outside repository')
    if path.exists() and any(path.iterdir()):raise ValueError('Use an empty new output directory')
    path.mkdir(parents=True,exist_ok=True)
    return path


def environment():
    return {'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,'pandas':pd.__version__,
            'sklearn':sklearn.__version__,'threadpools':threadpool_info(),'forest_configuration':SETTINGS}


def save_pickle(path,value):
    with path.open('wb') as f:pickle.dump(value,f,protocol=5)
    return {'path':path.name,'sha256':digest(path),'bytes':path.stat().st_size}


def checked_pickle(folder,record):
    path=folder/record['path']
    if digest(path)!=record['sha256']:raise ValueError(f'Artifact SHA mismatch: {path.name}')
    with path.open('rb') as f:return pickle.load(f)


def seal(output):
    output=ensure_output(output);verify_sources();snapshot=sources()
    for relative in snapshot:
        target=output/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,target)
        assert digest(target)==snapshot[relative]
    record={'created_at':pd.Timestamp.now(tz='Asia/Seoul').isoformat(),'sources':snapshot,'environment':environment(),
            'protocol':dataset.load_protocol(),'main_data_opened':False}
    write_json(output/'seal.json',record)
    return record


def normal_rows(summary):
    for row in summary['normal']:
        common=row['normal_common_eligible_hours']
        row['false_episodes_per_common_eligible_hour']=row['false_alarm_episodes']/common if common else None
        row['positive_fraction_common_eligible']=row['positive_normal_minutes']/(common*60) if common else None
    return summary


def evaluate_arm(flags,meta,events,p,cadence):
    summary,records,issued,episodes=ev.evaluate(flags,.5,meta,events,p,cadence)
    summary=normal_rows(summary)
    summary['arm_assessed_scheduled_rows']=int(np.sum(np.isfinite(flags)&ev.decision_grid(meta,cadence)))
    summary['assessment_policy']='ternary OR; raw branch support is input-defined, arm assessment also depends on ready branch positives'
    return summary,records,issued,episodes


def branch_ledger(raw,expected,meta,config,cadence,model_status=None):
    grid=ev.decision_grid(meta,cadence)
    result=meta.loc[grid,['row_id','group','decision_tick','state','eligible','known_zero','pair_count','window_start','window_end']].copy()
    for key in dict.fromkeys(b['score_key'] for b in config.get('branches',[])):
        result[f'{key}_raw']=raw[key][grid];result[f'{key}_expected_ready']=expected[key][grid]
        result[f'{key}_score_ready']=expected[key][grid]&np.isfinite(raw[key][grid])
        result[f'{key}_model_status']=(model_status or {}).get(key,'statistics_available')
    for branch in config.get('branches',[]):
        key=branch['score_key'];values=raw[key][grid];ready=expected[key][grid]&np.isfinite(values)
        result[f'{key}_cut']=branch['threshold']
        result[f'{key}_positive']=pd.array([bool(v>branch['threshold']) if supported else None for v,supported in zip(values,ready)],dtype='boolean')
    return result


def screen(seal_path,output,fixture=False):
    frozen=json.loads(Path(seal_path).read_text(encoding='utf-8'))
    if frozen['sources']!=sources():raise ValueError('Code differs from pre-experiment source seal')
    p=dataset.fixture_protocol() if fixture else dataset.load_protocol()
    output=ensure_output(output);verify_sources()
    manifest={'status':'running','created_at':pd.Timestamp.now(tz='Asia/Seoul').isoformat(),'sources':sources(),
              'source_seal_sha256':digest(seal_path),'protocol':p,'fixture':fixture,'opened_splits':['train','calibration'],
              'environment':environment(),'fits_budget':len(p['seeds'])*4,'fits_attempted':0,'fits_completed':0,'datasets':[],'artifacts':{},'references':{},'failures':[],'costs':[]}
    lock={'protocol':p,'fixture':fixture,'sources':manifest['sources'],'source_seal_sha256':manifest['source_seal_sha256'],
          'seeds':p['seeds'],'artifacts':{},'references':{},'arms':{},'datasets':[],'opened_splits':['train','calibration'],'test_used':False}
    write_json(output/'manifest.json',manifest)
    for seed in p['seeds']:
        started=time.perf_counter()
        items={name:dataset.load_split(seed,name,p) for name in ['train','calibration']}
        reference=features.fit_reference(items['train']['stream'],p)
        reference_record=save_pickle(output/f'reference-s{seed}.pkl',reference)
        packs={name:build_pack(item,reference,1) for name,item in items.items()}
        metas={name:canonical(pack) for name,pack in packs.items()}
        artifact=detectors.fit_detectors(packs['train'],metas['train'],seed,p)
        manifest['fits_attempted']+=artifact['fits_attempted']
        manifest['fits_completed']+=artifact['fits_completed']
        raw,expected=detectors.score_detectors(artifact,packs['calibration'],metas['calibration'])
        unavailable={k:f['reason'] for k,f in artifact['forests'].items() if f['status']!='ok'}
        manifest['failures'].extend({'seed':seed,'branch':k,'reason':v} for k,v in unavailable.items())
        arms=detectors.calibrate_arms(raw,metas['calibration'],p,{k:f['score_upper_bound'] for k,f in artifact['forests'].items() if f['status']=='ok'},unavailable)
        artifact_record=save_pickle(output/f'detectors-s{seed}.pkl',artifact)
        manifest['artifacts'][str(seed)]=artifact_record;manifest['references'][str(seed)]=reference_record
        lock['artifacts'][str(seed)]=artifact_record;lock['references'][str(seed)]=reference_record;lock['arms'][str(seed)]=arms
        manifest['datasets'].extend(item['manifest'] for item in items.values());lock['datasets']=manifest['datasets']
        np.savez_compressed(output/f'calibration-raw-s{seed}.npz',**raw,**{f'{k}_expected_ready':v for k,v in expected.items()})
        meta_record=save_pickle(output/f'calibration-metadata-s{seed}.pkl',metas['calibration'])
        manifest.setdefault('calibration_metadata',{})[str(seed)]=meta_record
        for arm,config in arms.items():
            flags=detectors.arm_flags(raw,config)
            for cadence in p['cadence_candidates']:
                issued=ev.decisions(flags,metas['calibration'],cadence,.5)
                issued.to_csv(output/f'calibration-decisions-{arm}-s{seed}-c{cadence}.csv',index=False)
        training={k:{n:v for n,v in f.items() if n not in ['model','encoder']} for k,f in artifact['forests'].items()}
        write_json(output/f'training-provenance-s{seed}.json',training)
        manifest['costs'].append({'seed':seed,'stage':'normal_fit_calibration','wall_seconds':time.perf_counter()-started})
        write_json(output/'manifest.json',manifest)
        print(json.dumps({'phase':'normal_screen','seed':seed,'fits_completed':manifest['fits_completed'],
                          'eligible_arms':[a for a,c in arms.items() if c['eligible']]}),flush=True)
    if sources()!=manifest['sources']:raise ValueError('Source mutation during normal phase')
    assert manifest['fits_completed']<=manifest['fits_attempted']<=len(p['seeds'])*4
    write_json(output/'normal-lock.json',lock)
    manifest.update(status='completed',normal_lock_sha256=digest(output/'normal-lock.json'),completed_at=pd.Timestamp.now(tz='Asia/Seoul').isoformat())
    write_json(output/'manifest.json',manifest)
    return lock


def control_records(flags,meta,controls,p,cadence):
    _,records,_,_=ev.evaluate(flags,.5,meta,controls,p,cadence)
    return records


def holdout(screen_path,output):
    screen_path=Path(screen_path).resolve();manifest=json.loads((screen_path/'manifest.json').read_text(encoding='utf-8'))
    lock_path=screen_path/'normal-lock.json'
    if digest(lock_path)!=manifest['normal_lock_sha256']:raise ValueError('Normal lock SHA mismatch')
    lock=json.loads(lock_path.read_text(encoding='utf-8'))
    if lock['sources']!=sources():raise ValueError('Source mutation after normal cut lock')
    if lock['opened_splits']!=['train','calibration'] or lock['test_used']:raise ValueError('Invalid phase lock')
    verify_sources();load_eif();p=lock['protocol'];output=ensure_output(output)
    results=[];events_all=[];controls_all=[];paired=[];data_records=[];costs=[];support=[]
    for seed in p['seeds']:
        reference=checked_pickle(screen_path,lock['references'][str(seed)])
        artifact=checked_pickle(screen_path,lock['artifacts'][str(seed)])
        actual=dataset.load_split(seed,'blind',p,allow_main_blind=True)
        cf=dataset.load_split(seed,'blind',p,relation_intervention=False,allow_main_blind=True)
        actual_controls=actual.get('control_windows',actual['events'].loc[actual['events'].scenario.eq('relation_change')])
        cf_controls=cf['control_windows']
        by_variant={}
        for variant,item in [('actual',actual),('counterfactual',cf)]:
            started=time.perf_counter();cpu=time.process_time();pack=build_pack(item,reference,1);meta=canonical(pack)
            costs.append({'seed':seed,'variant':variant,'stage':'features','wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu})
            started=time.perf_counter();cpu=time.process_time()
            raw,expected=detectors.score_detectors(artifact,pack,meta)
            costs.append({'seed':seed,'variant':variant,'stage':'all_frozen_branch_scores','wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu})
            data_records.append({'variant':variant,**item['manifest']})
            meta.to_csv(output/f'metadata-{variant}-s{seed}.csv',index=False)
            for key,mask in expected.items():
                for group in p['groups']:
                    g=meta.group.eq(group).to_numpy();common=meta.eligible.to_numpy(bool)&g
                    model_available=key not in artifact['forests'] or artifact['forests'][key]['status']=='ok'
                    support.append({'seed':seed,'variant':variant,'branch':key,'group':group,
                                    'common_eligible_minutes':int(common.sum()),'expected_supported_minutes':int((mask&g).sum()),
                                    'expected_unavailable_minutes':int((common&~mask).sum()),'model_available':model_available,
                                    'model_unavailable_supported_minutes':int((mask&g).sum()) if not model_available else 0,
                                    'unexpected_missing_scores':int((mask&g&~np.isfinite(raw[key])).sum()) if model_available else 0})
            for arm,config in lock['arms'][str(seed)].items():
                flags=detectors.arm_flags(raw,config)
                by_variant[(variant,arm)]={}
                for cadence in p['holdout_cadences']:
                    summary,records,issued,episodes=evaluate_arm(flags,meta,item['events'],p,cadence)
                    results.append({'seed':seed,'variant':variant,'arm':arm,'cadence':cadence,'eligible':config['eligible'],
                                    'ineligible_reason':config['reason'],'summary':summary})
                    events_all.extend({'seed':seed,'variant':variant,'arm':arm,'cadence':cadence,**r} for r in records)
                    targets=actual_controls if variant=='actual' else cf_controls
                    diagnostic=control_records(flags,meta,targets,p,cadence)
                    controls_all.extend({'seed':seed,'variant':variant,'arm':arm,'cadence':cadence,**r} for r in diagnostic)
                    by_variant[(variant,arm)][cadence]={r['event_id']:r for r in diagnostic}
                    issued.to_csv(output/f'decisions-{variant}-{arm}-s{seed}-c{cadence}.csv',index=False)
                    episodes.to_csv(output/f'episodes-{variant}-{arm}-s{seed}-c{cadence}.csv',index=False)
                    if cadence==p['budget_policy']['primary_holdout_cadence']:
                        branch_ledger(raw,expected,meta,config,cadence,{k:f['status'] for k,f in artifact['forests'].items()}).to_csv(output/f'branches-{variant}-{arm}-s{seed}-c{cadence}.csv',index=False)
            np.savez_compressed(output/f'raw-{variant}-s{seed}.npz',**raw,**{f'{k}_expected_ready':v for k,v in expected.items()})
        for arm in lock['arms'][str(seed)]:
            for cadence in p['holdout_cadences']:
                for event_id,a in by_variant[('actual',arm)][cadence].items():
                    c=by_variant[('counterfactual',arm)][cadence][event_id]
                    paired.append({'seed':seed,'arm':arm,'cadence':cadence,'event_id':event_id,'group':a['group'],
                                   'actual_hit':a['hit'],'counterfactual_hit':c['hit'],'actual_only':bool(a['hit'] and not c['hit']),
                                   'both_hit':bool(a['hit'] and c['hit']),'cf_only':bool(c['hit'] and not a['hit']),
                                   'actual_episode_start':a['matched_episode_start'],'counterfactual_episode_start':c['matched_episode_start'],
                                   'actual_available_minutes':a['available_minutes'],'counterfactual_available_minutes':c['available_minutes']})
        print(json.dumps({'phase':'paired_holdout','seed':seed,'new_fits':0,'recalibrations':0}),flush=True)
    aggregates=[]
    for variant in ['actual','counterfactual']:
        for arm in lock['arms'][str(p['seeds'][0])]:
            for cadence in p['holdout_cadences']:
                r=[r for r in events_all if r['variant']==variant and r['arm']==arm and r['cadence']==cadence]
                normal=[g for result in results if result['variant']==variant and result['arm']==arm and result['cadence']==cadence for g in result['summary']['normal']]
                aggregate={'variant':variant,'arm':arm,'cadence':cadence,'events':len(r),'hits':sum(e['hit'] for e in r),
                           'unassessed_events':sum(e['unassessed'] for e in r),
                           'mean_bounded_delay_minutes':float(np.mean([e['bounded_delay_minutes'] for e in r])),
                           'detected_delay_median_minutes':float(np.median([e['delay_minutes'] for e in r if e['hit']])) if any(e['hit'] for e in r) else None,
                           'scenario':{s:{'events':sum(e['scenario']==s for e in r),'hits':sum(e['scenario']==s and e['hit'] for e in r)} for s in p['event_types']},
                           'normal':[]}
                for group in p['groups']:
                    rows=[g for g in normal if g['group']==group];hours=sum(g['normal_monitored_hours'] for g in rows)
                    common=sum(g['normal_common_eligible_hours'] for g in rows);false=sum(g['false_alarm_episodes'] for g in rows);positive=sum(g['positive_normal_minutes'] for g in rows)
                    aggregate['normal'].append({'group':group,'normal_monitored_hours':hours,'common_eligible_hours':common,'false_alarm_episodes':false,
                                               'positive_normal_minutes':positive,'false_alarm_episodes_per_hour':false/hours if hours else None,
                                               'positive_time_fraction':positive/(hours*60) if hours else None,
                                               'false_episodes_per_common_eligible_hour':false/common if common else None,
                                               'positive_fraction_common_eligible':positive/(common*60) if common else None})
                aggregates.append(aggregate)
    for name,value in [('results.json',results),('events.json',events_all),('control-windows.json',controls_all),('paired-relation.json',paired),('aggregate.json',aggregates),('costs.json',costs),('branch-support.json',support)]:
        write_json(output/name,value)
    if sources()!=lock['sources']:raise ValueError('Source mutation during holdout')
    write_json(output/'manifest.json',{'status':'completed','created_at':pd.Timestamp.now(tz='Asia/Seoul').isoformat(),'sources':sources(),'fixture':lock['fixture'],
               'normal_lock_sha256':digest(lock_path),'opened_splits':['blind_actual','blind_relation_counterfactual'],'new_fits':0,'recalibrations':0,
               'datasets':data_records,'environment':environment(),'protocol':p})


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    child=sub.add_parser('seal');child.add_argument('--output',required=True,type=Path)
    child=sub.add_parser('screen');child.add_argument('--output',required=True,type=Path);child.add_argument('--seal',required=True,type=Path);child.add_argument('--fixture',action='store_true')
    child=sub.add_parser('evaluate-blind');child.add_argument('--screen',required=True,type=Path);child.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    with threadpool_limits(limits=1):
        if args.command=='seal':seal(args.output)
        elif args.command=='screen':screen(args.seal,args.output,args.fixture)
        else:holdout(args.screen,args.output)


if __name__=='__main__':main()
