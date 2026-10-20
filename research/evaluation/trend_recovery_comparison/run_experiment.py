"""Sealed recovery-policy replay with immutable models and raw cuts; no fitting."""
from __future__ import annotations
import argparse
import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path

for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[key]='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from research.evaluation.long_term_trend import evaluation as ev
from research.evaluation.long_term_trend.run_experiment import canonical,build_pack
from research.evaluation.if_feature_comparison.models import load_eif,verify_sources
from research.evaluation.trend_family_comparison import detectors
from research.evaluation.trend_family_comparison import run_experiment as frozen
from research.evaluation.trend_recovery_comparison import datahelper,signals
from research.evaluation.trend_recovery_comparison.policy import apply_policy
from research.evaluation.trend_recovery_comparison.metrics import compare_alerts

ARMS={'sr_only':'family_sr+if43','sr_ifrel2':'family_sr+ifrel2','sr_eifrel2':'family_sr+eifrel2'}
FAMILIES={'sr_count':'count','sr_latency':'latency','sr_relation':'relation','ifrel2':'relation','eifrel2':'relation'}


def source_hashes():
    result=frozen.sources()
    for path in Path(__file__).parent.rglob('*'):
        if path.suffix in ('.py','.json'):
            result[path.relative_to(ROOT).as_posix()]=frozen.digest(path)
    return result


def configurations(lock,seed):
    original=lock['arms'][str(seed)];result={}
    reference=None
    for arm,name in ARMS.items():
        c=copy.deepcopy(original[name])
        sr=[b for b in c['branches'] if b['score_key'].startswith('sr_')]
        signature=[(b['score_key'],b['threshold']) for b in sr]
        if reference is None:reference=signature
        if signature!=reference:raise ValueError('Joint SR cuts differ across fixed arms')
        if arm=='sr_only':c['branches']=sr
        result[arm]=c
    return result


def seal(screen,prior_blind,output,phase,approval=None,policy_cal=None):
    screen=Path(screen).resolve();prior_blind=Path(prior_blind).resolve()
    lockpath=screen/'normal-lock.json';m=json.loads((screen/'manifest.json').read_text(encoding='utf-8'))
    p=datahelper.load_protocol();lock=json.loads(lockpath.read_text(encoding='utf-8'))
    if frozen.digest(lockpath)!=m['normal_lock_sha256']:raise ValueError('Normal lock hash')
    if phase!='fixture' and frozen.digest(lockpath)!=p['base_normal_lock_sha256']:raise ValueError('Unrecognized main normal lock')
    if bool(lock['fixture'])!=(phase=='fixture'):raise ValueError('Wrong frozen artifact phase')
    if lock['sources']!=frozen.sources():raise ValueError('Original computational sources changed')
    prior=json.loads((prior_blind/'manifest.json').read_text(encoding='utf-8'))
    if prior.get('status')!='completed' or prior['normal_lock_sha256']!=frozen.digest(lockpath):raise ValueError('Prior replay provenance')
    files=[lockpath,screen/'manifest.json',prior_blind/'manifest.json']
    cal=None
    if policy_cal is not None:
        cal=json.loads(Path(policy_cal).read_text(encoding='utf-8'))
        if cal['sources']!=source_hashes() or cal['normal_lock_sha256']!=frozen.digest(lockpath):raise ValueError('Policy calibration lock provenance')
        expected_seeds=[9102] if phase=='fixture' else [101,137,173]
        if cal.get('status')!='completed' or cal.get('model_seeds')!=expected_seeds:raise ValueError('Incomplete policy calibration lock')
        files.append(Path(policy_cal).resolve())
    if phase=='final':
        if cal is None:raise ValueError('Final seal requires frozen policy calibration disposition')
        if approval is None:raise ValueError('Final seal requires independent accepted development record')
        accepted=json.loads(Path(approval).read_text(encoding='utf-8'))
        if accepted.get('status')!='accepted' or accepted.get('sources')!=source_hashes():raise ValueError('Development acceptance does not cover these sources')
        if accepted.get('policy_cal_sha256')!=frozen.digest(policy_cal):raise ValueError('Development acceptance excludes policy calibration lock')
        files.append(Path(approval).resolve())
    for seed in lock['seeds']:
        configurations(lock,seed)
        files.extend(screen/lock[k][str(seed)]['path'] for k in ('artifacts','references'))
        files.extend([screen/f'calibration-raw-s{seed}.npz',screen/m['calibration_metadata'][str(seed)]['path']])
        if phase!='final':
            files.extend(prior_blind/f'{prefix}-{variant}-s{seed}.{suffix}' for variant in ('actual','counterfactual') for prefix,suffix in [('raw','npz'),('metadata','csv')])
    verify_sources();output=frozen.ensure_output(output);snapshot=source_hashes()
    for relative in snapshot:
        target=output/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,target)
    record={'created_at':pd.Timestamp.now(tz='Asia/Seoul').isoformat(),'phase':phase,'sources':snapshot,
        'protocol':p,'screen':str(screen),'prior_blind':str(prior_blind),
        'dependencies':{str(path):frozen.digest(path) for path in files},'normal_lock_sha256':frozen.digest(lockpath),
        'environment':frozen.environment(),'new_fits':0,'raw_cut_recalibrations':0,'final_data_generated':False,
        'development_approved':phase=='final','policy_calibration':cal,
        'policy_cal_sha256':frozen.digest(policy_cal) if policy_cal is not None else None}
    frozen.write_json(output/'seal.json',record);return record


def check_seal(path):
    record=json.loads(Path(path).read_text(encoding='utf-8'))
    if record['sources']!=source_hashes():raise ValueError('Recovery sources changed after seal')
    if record['protocol']!=datahelper.load_protocol():raise ValueError('Protocol differs from seal')
    for filename,expected in record['dependencies'].items():
        if frozen.digest(filename)!=expected:raise ValueError(f'Frozen dependency changed: {Path(filename).name}')
    return record


def selected_scores(artifact,pack,metadata):
    """Restore only the two predeclared forests; fit and cut search are absent."""
    reduced=copy.copy(artifact)
    reduced['forests']={k:artifact['forests'][k] for k in ('ifrel2','eifrel2')}
    raw,expected=detectors.score_detectors(reduced,pack,metadata)
    return {k:raw[k] for k in FAMILIES},{k:expected[k] for k in FAMILIES}


def timed(costs,stage,context,fn,*args,**kwargs):
    wall=time.perf_counter();cpu=time.process_time();result=fn(*args,**kwargs)
    costs.append({**context,'stage':stage,'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu})
    return result


def run(seal_path,output,split='blind',allow_final=False):
    sealed=check_seal(seal_path);phase=sealed['phase'];p=sealed['protocol']
    if phase=='final' and (not allow_final or split!='blind'):raise ValueError('Explicit root GO required for final blind')
    if phase=='final' and not sealed.get('development_approved',False):raise ValueError('Final seal needs approved development evidence')
    screen=Path(sealed['screen']);prior=Path(sealed['prior_blind']);lock=json.loads((screen/'normal-lock.json').read_text(encoding='utf-8'))
    prior_manifest=json.loads((prior/'manifest.json').read_text(encoding='utf-8'))
    output=frozen.ensure_output(output);load_eif();verify_sources()
    seeds=[9102] if phase=='fixture' else list(map(int,p['final']['data_seed_to_model_seed'])) if phase=='final' else [101,137,173]
    results=[];event_rows=[];pairs=[];costs=[];datasets=[];comparisons=[]
    manifest={'status':'running','started_at':pd.Timestamp.now(tz='Asia/Seoul').isoformat(),'phase':phase,'split':split,
        'seal_sha256':frozen.digest(seal_path),'new_fits':0,'raw_cut_recalibrations':0,'datasets':datasets}
    frozen.write_json(output/'manifest.json',manifest)
    for data_seed in seeds:
        model_seed=p['final']['data_seed_to_model_seed'][str(data_seed)] if phase=='final' else data_seed
        reference=frozen.checked_pickle(screen,lock['references'][str(model_seed)])
        artifact=frozen.checked_pickle(screen,lock['artifacts'][str(model_seed)])
        configs=configurations(lock,model_seed);relation_cut=next(b['threshold'] for b in configs['sr_only']['branches'] if b['score_key']=='sr_relation')
        frozen.write_json(output/f'cuts-s{data_seed}.json',configs)
        frozen.write_json(output/f'recovery-thresholds-s{data_seed}.json',signals.threshold_manifest(reference))
        controls={}
        for variant in (('normal',) if split=='calibration' else ('actual','counterfactual')):
            context={'data_seed':data_seed,'model_seed':model_seed,'variant':variant}
            expected=None
            if phase!='final':
                source_records=lock['datasets'] if split=='calibration' else prior_manifest['datasets']
                matches=[r for r in source_records if r['seed']==data_seed and r['split']==split and (split=='calibration' or r['variant']==variant)]
                if len(matches)!=1:raise ValueError('Expected exactly one original source manifest')
                expected=matches[0]['source_sha256']
            item=timed(costs,'source_replay',context,datahelper.load_item,data_seed,phase,split=split,
                relation_intervention=variant!='counterfactual',expected_source_sha256=expected,allow_final=allow_final,protocol=p)
            datasets.append({'variant':variant,**item['manifest']})
            if split=='calibration' or phase=='development':
                if split=='calibration':
                    m=json.loads((screen/'manifest.json').read_text(encoding='utf-8'));meta=frozen.checked_pickle(screen,m['calibration_metadata'][str(data_seed)])
                    rawpath=screen/f'calibration-raw-s{data_seed}.npz'
                else:
                    meta=pd.read_csv(prior/f'metadata-{variant}-s{data_seed}.csv');meta['decision_tick']=pd.to_datetime(meta.decision_tick,utc=True)
                    rawpath=prior/f'raw-{variant}-s{data_seed}.npz'
                with np.load(rawpath,allow_pickle=False) as stored:
                    raw={k:stored[k].copy() for k in FAMILIES};expected_masks={k:stored[f'{k}_expected_ready'].copy() for k in FAMILIES}
            else:
                pack=timed(costs,'features',context,build_pack,item,reference,1);meta=canonical(pack)
                raw,expected_masks=timed(costs,'selected_frozen_scores',context,selected_scores,artifact,pack,meta)
            if phase=='fixture' and split=='blind':
                rawpath=screen/f'calibration-raw-s{data_seed}.npz' if split=='calibration' else prior/f'raw-{variant}-s{data_seed}.npz'
                stored=np.load(rawpath,allow_pickle=False)
                for k in raw:
                    if not np.array_equal(raw[k],stored[k],equal_nan=True) or not np.array_equal(expected_masks[k],stored[f'{k}_expected_ready']):raise ValueError('Frozen raw/support replay mismatch')
                if split=='calibration':
                    m=json.loads((screen/'manifest.json').read_text(encoding='utf-8'));oldmeta=frozen.checked_pickle(screen,m['calibration_metadata'][str(data_seed)])
                else:oldmeta=pd.read_csv(prior/f'metadata-{variant}-s{data_seed}.csv',usecols=['row_id'])
                if not meta.row_id.equals(oldmeta.row_id):raise ValueError('Frozen metadata row mismatch')
            meta.to_csv(output/f'metadata-{variant}-s{data_seed}.csv',index=False)
            item['events'].to_csv(output/f'truth-{variant}-s{data_seed}.csv',index=False)
            item['control_windows'].to_csv(output/f'controls-{variant}-s{data_seed}.csv',index=False)
            np.savez_compressed(output/f'raw-{variant}-s{data_seed}.npz',**raw,**{f'{k}_expected_ready':v for k,v in expected_masks.items()})
            evidence={name:None if spec is None else timed(costs,'recovery_evidence',dict(context,policy=name),signals.build_signals,item,reference,artifact['statistics'],spec,relation_cut) for name,spec in p['policies'].items()}
            for name,e in evidence.items():
                if e is not None:
                    if not np.array_equal(e['row_id'],meta.row_id.to_numpy()):raise ValueError('Replayed source and frozen metadata row alignment')
                    if not np.array_equal(pd.DatetimeIndex(pd.to_datetime(e['decision_tick'],utc=True)).as_unit('ns').asi8,
                                          pd.DatetimeIndex(pd.to_datetime(meta.decision_tick,utc=True)).as_unit('ns').asi8):raise ValueError('Replayed source and frozen metadata time alignment')
                    np.savez_compressed(output/f'evidence-{variant}-{name}-s{data_seed}.npz',row_id=e['row_id'].astype(str),
                        **{f'{family}_{k}':np.asarray(v).astype(str) if np.asarray(v).dtype==object else v for family in ('count','latency','relation') for k,v in e[family].items()})
            for arm,config in configs.items():
                branch_flags={b['score_key']:np.where(np.isfinite(raw[b['score_key']]),(raw[b['score_key']]>b['threshold']).astype(float),np.nan) for b in config['branches']}
                if not config['eligible']:branch_flags={k:np.full(len(meta),np.nan) for k in branch_flags}
                baseline=detectors.arm_flags(raw,config)
                for cadence in ([1,5,15] if split=='calibration' else [1,5]):
                    bsummary,brecords,bissued,bepisodes=frozen.evaluate_arm(baseline,meta,item['events'],lock['protocol'],cadence)
                    for name,spec in p['policies'].items():
                        flags,ledger,transitions=timed(costs,'policy',dict(context,arm=arm,policy=name,cadence=cadence),apply_policy,
                            branch_flags,meta[['row_id','group','decision_tick','eligible']],evidence[name],{k:FAMILIES[k] for k in branch_flags},spec,cadence)
                        if name=='none' and not np.array_equal(flags[ev.decision_grid(meta,cadence)],baseline[ev.decision_grid(meta,cadence)],equal_nan=True):raise AssertionError('No-clear baseline changed')
                        summary,records,issued,episodes=frozen.evaluate_arm(flags,meta,item['events'],lock['protocol'],cadence)
                        comparison=compare_alerts(baseline,flags,meta,item['events'],brecords,records,cadence)
                        disposition=sealed.get('policy_calibration')
                        cal_entry=disposition['dispositions'][str(model_seed)][arm][name] if disposition is not None else None
                        label=dict(context,arm=arm,policy=name,cadence=cadence,eligible=config['eligible'],detector_eligible=config['eligible'],
                            policy_cal_eligible=cal_entry['policy_cal_eligible'] if cal_entry else None,
                            policy_cal_failures=cal_entry['failures'] if cal_entry else None)
                        comparisons.append({**label,**comparison})
                        normal_guards=[]
                        for rate in summary['normal']:
                            reasons=[]
                            if rate['normal_monitored_hours']<=0:reasons.append('no_monitored_exposure')
                            if rate['false_alarm_episodes_per_hour'] is None or rate['false_alarm_episodes_per_hour']>.05+1e-12:reasons.append('episode_budget')
                            if rate['positive_time_fraction'] is None or rate['positive_time_fraction']>.01+1e-12:reasons.append('positive_time_budget')
                            normal_guards.append(dict(rate,passed=not reasons,failure_reasons=reasons))
                        first_delays=[r['first_positive_added_delay_minutes'] for r in comparison['event_details'] if r['baseline_hit']]
                        onset_delays=[r['matched_onset_added_delay_minutes'] for r in comparison['event_details'] if r['baseline_hit']]
                        preservation=not comparison['lost_hit_ids'] and all(v is not None and v<=0 for v in first_delays+onset_delays)
                        strict_safety=comparison['active_suppressed_minutes']==0 and comparison['active_premature_clear_count']==0
                        reduction=sum(r['positive_normal_minutes'] for r in bsummary['normal'])-sum(r['positive_normal_minutes'] for r in summary['normal'])
                        results.append({**label,'summary':summary,'baseline_summary':bsummary,'normal_guards':normal_guards,
                            'normal_positive_reduction_minutes':reduction,'baseline_hit_and_first_alert_preserved':preservation,
                            'strict_active_GT_safety_passed':strict_safety,'normal_positive_strictly_decreased':reduction>0,
                            'adoption_conditions_passed':bool(config['eligible'] and cal_entry and cal_entry['policy_cal_eligible'] and preservation and strict_safety and reduction>0 and all(g['passed'] for g in normal_guards))})
                        event_rows.extend({**label,**r} for r in records)
                        stem=f'{variant}-{arm}-{name}-s{data_seed}-c{cadence}'
                        ledger.to_csv(output/f'policy-{stem}.csv',index=False);transitions.to_csv(output/f'transitions-{stem}.csv',index=False)
                        issued.to_csv(output/f'decisions-{stem}.csv',index=False);episodes.to_csv(output/f'episodes-{stem}.csv',index=False)
                        targets=frozen.control_records(flags,meta,item['control_windows'],lock['protocol'],cadence) if split=='blind' else []
                        controls[(variant,arm,name,cadence)]={r['event_id']:r for r in targets}
        if split=='blind':
            for arm in configs:
                for name in p['policies']:
                    for cadence in (1,5):
                        a=controls[('actual',arm,name,cadence)];c=controls[('counterfactual',arm,name,cadence)]
                        pairs.extend({'data_seed':data_seed,'model_seed':model_seed,'arm':arm,'policy':name,'cadence':cadence,'event_id':event_id,
                            'actual_hit':r['hit'],'counterfactual_hit':c[event_id]['hit'],'actual_only':bool(r['hit'] and not c[event_id]['hit'])} for event_id,r in a.items())
        print(json.dumps({'phase':phase,'split':split,'data_seed':data_seed,'new_fits':0,'raw_cut_recalibrations':0}),flush=True)
    for filename,value in [('results.json',results),('events.json',event_rows),('comparisons.json',comparisons),('paired-relation.json',pairs),('costs.json',costs)]:frozen.write_json(output/filename,value)
    if split=='calibration':
        dispositions={}
        for seed in seeds:
            dispositions[str(seed)]={}
            for arm in ARMS:
                dispositions[str(seed)][arm]={}
                for name in p['policies']:
                    selected=[r for r in results if r['data_seed']==seed and r['arm']==arm and r['policy']==name]
                    cells={(r['cadence'],g['group']) for r in selected for g in r['normal_guards']}
                    required={(c,g) for c in (1,5,15) for g in p['base_protocol']['groups']}
                    failures=[dict(cadence=r['cadence'],**g) for r in selected for g in r['normal_guards'] if not g['passed']]
                    failures.extend({'cadence':c,'group':g,'failure_reasons':['missing_guard_cell']} for c,g in sorted(required-cells))
                    detector_eligible=all(r['detector_eligible'] for r in selected) and bool(selected)
                    dispositions[str(seed)][arm][name]={'detector_eligible':detector_eligible,
                        'policy_cal_eligible':bool(detector_eligible and not failures),'failures':failures}
        frozen.write_json(output/'policy-cal-lock.json',{'status':'completed','sources':sealed['sources'],
            'normal_lock_sha256':sealed['normal_lock_sha256'],'model_seeds':seeds,'phase':phase,
            'calibration_source_manifests':datasets,'dispositions':dispositions,'new_fits':0,'raw_cut_recalibrations':0,
            'failed_policy_output_preserved':True})
    check_seal(seal_path)
    manifest.update(status='completed',completed_at=pd.Timestamp.now(tz='Asia/Seoul').isoformat(),environment=frozen.environment(),
        normal_lock_sha256=sealed['normal_lock_sha256'],sources=sealed['sources'],protocol=p,
        file_bytes=sum(path.stat().st_size for path in output.rglob('*') if path.is_file()))
    frozen.write_json(output/'manifest.json',manifest)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('seal');s.add_argument('--screen',type=Path,required=True);s.add_argument('--prior-blind',type=Path,required=True)
    s.add_argument('--phase',choices=['fixture','development','final'],required=True);s.add_argument('--output',type=Path,required=True)
    s.add_argument('--approval',type=Path)
    s.add_argument('--policy-cal',type=Path)
    r=sub.add_parser('replay');r.add_argument('--seal',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    r.add_argument('--split',choices=['calibration','blind'],default='blind');r.add_argument('--allow-final',action='store_true')
    a=parser.parse_args()
    with threadpool_limits(limits=1):
        if a.command=='seal':seal(a.screen,a.prior_blind,a.output,a.phase,a.approval,a.policy_cal)
        else:run(a.seal,a.output,a.split,a.allow_final)


if __name__=='__main__':main()
