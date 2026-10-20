"""Paired controlled comparison. Test truth is used only after thresholds freeze."""
from __future__ import annotations
import argparse
import gc
import hashlib
import importlib.metadata
import json
import pickle
import platform
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psutil
from threadpoolctl import threadpool_limits

from dataset import build_dataset, load_protocol
from features import build_features, fit_reference
from models import SETTINGS, build_model, smoke, verify_sources, eif_equivalence

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path,data):Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def safe_number(x):return float(x) if np.isfinite(x) else None
def score_profile(scores):
    u,c=np.unique(scores,return_counts=True)
    return {'n':len(scores),'unique':len(u),'duplicate_fraction':1-len(u)/len(scores),
            'largest_tie_fraction':float(c.max()/len(scores)),'min':float(np.min(scores)),'max':float(np.max(scores)),
            'rounded_3_unique':len(np.unique(np.round(scores,3)))}

class TrainScaler:
    def fit(self,X):
        self.median=np.median(X,axis=0);q10,q90=np.quantile(X,[.1,.9],axis=0)
        self.scale=np.maximum(q90-q10,.05)
        return self
    def transform(self,X):return ((X-self.median)/self.scale).astype(np.float32)

def _meta(pack):
    metadata=pack['metadata'].copy()
    for target,old in (('readiness','ready'),('train_mask','fit_eligible')):
        if target not in metadata and old in metadata:metadata[target]=metadata[old]
    if 'row_id' not in metadata:
        metadata['row_id']=metadata['split'].astype(str)+'|'+metadata.group.astype(str)+'|'+metadata.endpoint.astype(str)
    return metadata

def _episode_starts(pred,meta,gap_minutes):
    times=pd.to_datetime(meta.emitted_at,utc=True).dt.as_unit('ns').astype('int64').to_numpy()
    result=[]
    for group in sorted(meta.group.unique()):
        selected=np.flatnonzero(pred & meta.group.eq(group).to_numpy())
        if len(selected):
            delta=np.diff(times[selected])/60e9
            starts=selected[np.r_[True,delta>gap_minutes]]
            result.extend(starts.tolist())
    return np.array(result,dtype=int)

def calibrate(scores,meta,protocol,minimum_threshold=None):
    """Lowest raw threshold meeting per-group episode and occupancy guards."""
    budget=protocol['calibration_alarm_budget_per_group_hour']
    point_budget=protocol['calibration_positive_tick_fraction_per_group']
    assert np.isfinite(scores).all() and len(scores)==len(meta)
    groups={g:np.flatnonzero(meta.group.eq(g).to_numpy()) for g in sorted(meta.group.unique())}
    # A point guard rules out the pathological all-positive, one-long-episode solution.
    minimum=max(float(np.quantile(scores[ids],1-point_budget,method='higher')) for ids in groups.values())
    if minimum_threshold is not None:minimum=max(minimum,minimum_threshold)
    candidates=np.unique(scores[scores>=minimum])
    if minimum_threshold is not None:candidates=np.unique(np.r_[minimum,candidates])
    for threshold in candidates:
        pred=scores>threshold;valid=True;details={}
        for group,ids in groups.items():
            m=meta.iloc[ids].reset_index(drop=True);p=pred[ids]
            hours=len(ids)*protocol['cadence_minutes']/60
            episodes=len(_episode_starts(p,m,protocol['alarm_merge_gap_minutes']))
            rate=episodes/hours;occupancy=float(np.mean(p))
            details[group]={'hours':hours,'episodes':episodes,'episodes_per_hour':rate,'point_positive_fraction':occupancy}
            valid &= rate<=budget+1e-12 and occupancy<=point_budget+1e-12
        if valid:return {'threshold':float(threshold),'strict_greater_than':True,'groups':details,'test_used':False}
    raise AssertionError('Maximum normal score must permit zero calibration alarms')

def _normalizer(scores):
    center=float(np.median(scores));spread=max(float(np.quantile(scores,.95)-center),float(np.std(scores)),1e-6)
    return center,spread

def rule_scores(pack,mask):
    names=pack['feature_names']['enhanced'];X=pack['X']['enhanced'][mask]
    c=lambda name:X[:,names.index(name)]
    return np.maximum.reduce([c('count_above_q95_ratio')*c('count_above_q95_run_minutes'),
                              c('count_below_q05_ratio')*c('count_below_q05_run_minutes'),
                              c('latency_above_q95_ratio')*c('latency_above_q95_run_minutes')]).astype(float)

def evaluate(scores,threshold,meta,events,protocol):
    pred=np.asarray(scores)>threshold
    stamps=pd.to_datetime(meta.emitted_at,utc=True)
    alerts=_episode_starts(pred,meta,protocol['alarm_merge_gap_minutes'])
    available_alerts=set(alerts.tolist())
    episode_ends={}
    for group in sorted(meta.group.unique()):
        selected=np.flatnonzero(pred & meta.group.eq(group).to_numpy())
        starts=[i for i in alerts if meta.group.iloc[i]==group]
        for pos,start in enumerate(starts):
            stop=starts[pos+1] if pos+1<len(starts) else len(meta)
            members=selected[(selected>=start)&(selected<stop)]
            episode_ends[start]=stamps.iloc[members[-1]]
    positive_truth=np.zeros(len(meta),bool);late_truth=np.zeros(len(meta),bool)
    event_records=[]
    for event in events.sort_values(['detectable_start','group']).to_dict('records'):
        group=meta.group.eq(event['group']).to_numpy()
        start,end,detectable=(pd.Timestamp(event[k]) for k in ('start','end','detectable_start'))
        window=group & stamps.ge(start).to_numpy() & stamps.lt(end).to_numpy()
        positive_truth |= window
        late=group & stamps.ge(end).to_numpy() & stamps.lt(end+pd.Timedelta(minutes=protocol['late_grace_minutes'])).to_numpy()
        late_truth |= late
        detectable_mask=window & stamps.ge(detectable).to_numpy()
        hits=np.array([i for i in alerts if i in available_alerts and detectable_mask[i]],dtype=int)
        first=stamps.iloc[hits[0]] if len(hits) else None
        if len(hits):available_alerts.remove(int(hits[0]))
        delay=(first-detectable).total_seconds()/60 if first is not None else None
        pre_detect=np.flatnonzero(pred & window & stamps.lt(detectable).to_numpy())
        preexisting=[i for i in alerts if group[i] and stamps.iloc[i]<start and episode_ends[i]>=start]
        early=[i for i in alerts if group[i] and start<=stamps.iloc[i]<detectable]
        late_starts=[i for i in alerts if late[i]]
        event_records.append({**{k:event[k] for k in ('event_id','group','scenario','duration_minutes','count_multiplier','duration_multiplier','seed')},
                              'hit':bool(len(hits)),'delay_minutes':delay,'censored':not bool(len(hits)),
                              'bounded_delay_minutes':min(delay,protocol['bounded_delay_minutes']) if delay is not None else protocol['bounded_delay_minutes'],
                              'opportunities':int(np.sum(detectable_mask)),'pre_detectable_positive_rows':len(pre_detect),
                              'preexisting_episode_count':len(preexisting),'late_episode_count':len(late_starts),
                              'early_episode_count':len(early),'early_event_coverage':bool(early),
                              'sustained_positive_without_new_episode':bool(np.any(pred & detectable_mask) and not len(hits)),
                              'matched_episode_start':first.isoformat() if first is not None else None,
                              'mean_score':safe_number(np.mean(scores[detectable_mask])) if detectable_mask.any() else None,
                              'max_score':safe_number(np.max(scores[detectable_mask])) if detectable_mask.any() else None,
                              'late_hit':bool(np.any(pred & late))})
    # Recovery grace alarms are visible separately; they never count as on-time hits.
    normal=~positive_truth & ~late_truth
    group_rates=[]
    for group in sorted(meta.group.unique()):
        m=meta.group.eq(group).to_numpy();hours=np.sum(m & normal)*protocol['cadence_minutes']/60
        fp=sum(bool(m[i] and normal[i]) for i in alerts)
        group_rates.append({'group':group,'normal_hours':hours,'false_alarm_episodes':fp,
                            'false_alarm_episodes_per_hour':fp/hours if hours else None,
                            'normal_positive_rows':int(np.sum(pred & m & normal)),
                            'normal_point_positive_fraction':float(np.mean(pred[m & normal])) if np.any(m & normal) else None})
    return {'event_count':len(event_records),'event_hits':sum(r['hit'] for r in event_records),
            'event_recall':float(np.mean([r['hit'] for r in event_records])),
            'mean_bounded_delay_minutes':float(np.mean([r['bounded_delay_minutes'] for r in event_records])),
            'detected_delay_median_minutes':safe_number(np.median([r['delay_minutes'] for r in event_records if r['hit']])) if any(r['hit'] for r in event_records) else None,
            'censored_event_count':sum(r['censored'] for r in event_records),'rows':len(meta),
            'positive_rows':int(pred.sum()),'normal_rates':group_rates,'score_profile':score_profile(scores),
            'late_positive_rows':int(np.sum(pred & late_truth))},event_records,pred

def self_check():
    p=load_protocol();times=pd.date_range('2026-01-01',periods=24,freq='5min',tz='UTC')
    meta=pd.DataFrame({'emitted_at':times,'group':'g'})
    event=pd.DataFrame([{'event_id':'counterexample','group':'g','scenario':'burst','start':times[5],
                        'detectable_start':times[5],'end':times[15],'duration_minutes':50,
                        'count_multiplier':2,'duration_multiplier':1,'seed':11}])
    ongoing=np.zeros(24);ongoing[4:15]=1
    summary,events,_=evaluate(ongoing,.5,meta,event,p)
    assert summary['event_hits']==0 and events[0]['preexisting_episode_count']==1
    assert events[0]['sustained_positive_without_new_episode']
    fresh=np.zeros(24);fresh[6:8]=1
    summary,events,_=evaluate(fresh,.5,meta,event,p)
    assert summary['event_hits']==1 and events[0]['delay_minutes']==5
    same=pd.concat([event,event.assign(event_id='overlap')],ignore_index=True)
    assert evaluate(fresh,.5,meta,same,p)[0]['event_hits']==1
    gradual=event.assign(scenario='gradual_increase',detectable_start=times[8])
    early=np.zeros(24);early[6:11]=1
    summary,records,_=evaluate(early,.5,meta,gradual,p)
    assert summary['event_hits']==0 and records[0]['early_episode_count']==1
    assert records[0]['preexisting_episode_count']==0 and records[0]['early_event_coverage']
    meta2=pd.DataFrame({'emitted_at':pd.date_range('2026-01-01',periods=2016,freq='5min',tz='UTC'),'group':'g'})
    pred=np.zeros(2016,bool);pred[[1,2,20]]=True
    assert _episode_starts(pred,meta2,10).tolist()==[1,20]
    assert calibrate(np.zeros(2016),meta2,p)['threshold']==0
    assert calibrate(np.arange(2016,dtype=float),meta2,p)['groups']['g']['point_positive_fraction']<=.01
    return {'preexisting_episode_not_new_hit':True,'fresh_episode_hit':True,'episode_to_event_one_to_one':True,
            'early_episode_separate_from_preexisting_and_strict_hit':True,'episode_time_units_correct':True,'strict_ties_and_point_guard':True}

def run(output,seeds,windows,algorithms,variants,tag):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    protocol=load_protocol()
    if 'calibration_positive_tick_fraction_per_group' not in protocol:raise ValueError('Freeze the point-positive guard in reviewed protocol first')
    run_dir=output/tag
    if run_dir.exists():raise FileExistsError('Use a fresh run tag; never overwrite a sealed test run')
    run_dir.mkdir();(run_dir/'models').mkdir();(run_dir/'scores').mkdir()
    paths=[Path(__file__),Path(__file__).with_name('models.py'),Path(__file__).with_name('dataset.py'),Path(__file__).with_name('features.py'),Path(__file__).with_name('protocol.json')]
    hashes={p.name:sha(p) for p in paths}
    manifest={'status':'running','protocol':protocol,'source_sha256':hashes,'model_settings':SETTINGS,
              'official_sources':verify_sources(),'python':sys.executable,'python_version':platform.python_version(),
              'dependencies':{p:importlib.metadata.version(p) for p in ('numpy','pandas','scikit-learn','scipy','torch','joblib','psutil','tqdm')},
              'seeds':seeds,'window_hours':windows,'algorithms':algorithms,'variants':variants,'runs':[],
              'test_based_retuning':False,'data_manifests':[]}
    write_json(run_dir/'manifest.json',manifest)
    all_events=[];all_records=[];process=psutil.Process()
    for seed in seeds:
        data=build_dataset(seed,protocol);manifest['data_manifests'].append(data['manifest'])
        reference=fit_reference(data['train'],protocol)
        reference_digest=hashlib.sha256(pickle.dumps(reference)).hexdigest()
        baseline_keys=None
        for width in windows:
            ft0=time.perf_counter()
            packs={split:build_features(data[split],reference,width,protocol['cadence_minutes']) for split in ('train','calibration','test')}
            feature_seconds=time.perf_counter()-ft0
            metas={split:_meta(pack) for split,pack in packs.items()}
            masks={split:meta.readiness.to_numpy(bool).copy() for split,meta in metas.items()}
            masks['train'] &= metas['train'].train_mask.to_numpy(bool)
            for split in ('train','calibration'):
                assert metas[split].loc[masks[split],'normal_confirmed'].all()
            keys={split:meta.loc[masks[split],'row_id'].tolist() for split,meta in metas.items()}
            if baseline_keys is None:baseline_keys=keys
            else:assert keys==baseline_keys,'Every W must use identical group-endpoints'
            selected=np.flatnonzero(masks['train'])
            if len(selected)>protocol['training_cap']:
                selected=np.sort(np.random.default_rng(seed).choice(selected,protocol['training_cap'],replace=False))
            cal_meta=metas['calibration'].loc[masks['calibration']].reset_index(drop=True)
            test_meta=metas['test'].loc[masks['test']].reset_index(drop=True)
            cal_rule=rule_scores(packs['calibration'],masks['calibration']);test_rule=rule_scores(packs['test'],masks['test'])
            rule_point=calibrate(cal_rule,cal_meta,protocol)
            if 'if' in algorithms:
                rule_metrics,rule_events,_=evaluate(test_rule,rule_point['threshold'],test_meta,data['events'],protocol)
                rule_key={'seed':seed,'window_hours':width,'algorithm':'rule','variant':'magnitude_times_run','mode':'rule_only'}
                all_records.append({**rule_key,'calibration':rule_point,'metrics':rule_metrics})
                all_events.extend([{**rule_key,**event} for event in rule_events])
            for variant in variants:
                scaler=TrainScaler().fit(packs['train']['X'][variant][selected])
                train_X=scaler.transform(packs['train']['X'][variant][selected])
                cal_X=scaler.transform(packs['calibration']['X'][variant][masks['calibration']])
                test_X=scaler.transform(packs['test']['X'][variant][masks['test']])
                for algorithm in algorithms:
                    key={'seed':seed,'window_hours':width,'algorithm':algorithm,'variant':variant}
                    ident=f'{algorithm}-{variant}-w{width:02d}-s{seed}'
                    wall=time.perf_counter();cpu0=time.process_time();rss0=process.memory_info().rss
                    model=build_model(algorithm,seed).fit(train_X);fit_seconds=time.perf_counter()-wall
                    t=time.perf_counter();cal_scores=model.score_samples(cal_X);cal_seconds=time.perf_counter()-t
                    operating=calibrate(cal_scores,cal_meta,protocol)
                    center,spread=_normalizer(cal_scores);rc,rs=_normalizer(cal_rule)
                    joint_cal=np.maximum((cal_scores-operating['threshold'])/spread,(cal_rule-rule_point['threshold'])/rs)
                    joint_point=calibrate(joint_cal,cal_meta,protocol,minimum_threshold=0.0)
                    # The test score call starts only after every operating point is frozen.
                    t=time.perf_counter();test_scores=model.score_samples(test_X);test_seconds=time.perf_counter()-t
                    joint_test=np.maximum((test_scores-operating['threshold'])/spread,(test_rule-rule_point['threshold'])/rs)
                    cost={'feature_seconds_all_splits':feature_seconds,'fit_seconds':fit_seconds,'calibration_score_seconds':cal_seconds,
                          'test_score_seconds':test_seconds,'cpu_seconds':time.process_time()-cpu0,'rss_before_bytes':rss0,
                          'rss_after_bytes':process.memory_info().rss,'process_peak_working_set_bytes':getattr(process.memory_info(),'peak_wset',None),
                          'train_rows':len(train_X),'calibration_rows':len(cal_X),'test_rows':len(test_X),'feature_count':train_X.shape[1]}
                    saved=model.export_state() if algorithm=='dif' else model.model
                    joblib.dump({'state':saved,'scaler':scaler,'threshold':operating,'joint_threshold':joint_point,
                                 'normalizer':(center,spread),'rule_normalizer':(rc,rs),'features':packs['train']['feature_names'][variant],
                                 'reference_sha256':reference_digest,'key':key},run_dir/'models'/f'{ident}.joblib',compress=3)
                    score_path=run_dir/'scores'/f'{ident}.npz'
                    np.savez_compressed(score_path,calibration_raw=cal_scores,test_raw=test_scores,test_rule=test_rule,
                                        test_joint=joint_test,row_id=test_meta.row_id.to_numpy(str),
                                        group=test_meta.group.to_numpy(str),endpoint_ns=pd.to_datetime(test_meta.endpoint,utc=True).dt.as_unit('ns').astype('int64').to_numpy())
                    for mode,scores,point in (('ml_only',test_scores,operating),('combined',joint_test,joint_point)):
                        metrics,event_records,_=evaluate(scores,point['threshold'],test_meta,data['events'],protocol)
                        record={**key,'mode':mode,'label':model.label,'calibration':point,'metrics':metrics,'cost':cost,
                                'row_cohort_sha256':hashlib.sha256('\n'.join(test_meta.row_id).encode()).hexdigest(),
                                'scores_sha256':sha(score_path)}
                        all_records.append(record);all_events.extend([{**key,'mode':mode,**r} for r in event_records])
                    manifest['runs'].append({**key,'cost':cost,'scores_sha256':sha(score_path),'model_sha256':sha(run_dir/'models'/f'{ident}.joblib')})
                    write_json(run_dir/'results.json',all_records);pd.DataFrame(all_events).to_csv(run_dir/'events.csv',index=False)
                    write_json(run_dir/'manifest.json',manifest)
                    print(json.dumps({'completed':len(manifest['runs']),**key,'fit_s':round(fit_seconds,3),'test_s':round(test_seconds,3)},ensure_ascii=False),flush=True)
                    del model,saved;gc.collect()
            assert hashlib.sha256(pickle.dumps(reference)).hexdigest()==reference_digest,'Frozen reference mutated'
    assert all(sha(p)==hashes[p.name] for p in paths),'Source changed during execution'
    manifest['status']='completed';manifest['fit_count']=len(manifest['runs'])
    manifest['results_sha256']=sha(run_dir/'results.json');manifest['events_sha256']=sha(run_dir/'events.csv')
    write_json(run_dir/'manifest.json',manifest)
    return run_dir

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--tag',default='full-v1')
    parser.add_argument('--seeds',type=int,nargs='+',default=[11,29,47]);parser.add_argument('--windows',type=int,nargs='+',default=list(range(1,13)))
    parser.add_argument('--algorithms',nargs='+',choices=['if','eif','dif'],default=['if','eif','dif']);parser.add_argument('--variants',nargs='+',default=['base','enhanced'])
    parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    with threadpool_limits(1):
        if args.smoke:
            args.output.mkdir(parents=True,exist_ok=True);report={'models':smoke(),'eif_equivalence':eif_equivalence(),'runner_checks':self_check()};write_json(args.output/'model-smoke.json',report);print(json.dumps(report,indent=2))
        else:print(run(args.output,args.seeds,args.windows,args.algorithms,args.variants,args.tag))
