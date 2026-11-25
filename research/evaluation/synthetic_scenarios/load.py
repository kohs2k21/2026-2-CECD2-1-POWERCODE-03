"""Open-loop LOCAL inference load. Explicitly excludes HTTP, DB, containers and GPU."""
import argparse
import itertools
import json
import queue
import threading
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psutil
from threadpoolctl import threadpool_limits

from .datasets import sha


def percentile(values, q):
    return float(np.quantile(values,q)*1000) if len(values) else None


def one_condition(model,values,rate,batch,config):
    pending=queue.Queue(maxsize=config['load_queue_capacity'])
    stop=threading.Event();sample_stop=threading.Event();results=[];errors=[];resources=[]
    process=psutil.Process();inflight=0;peak_depth=0
    def sample():
        process.cpu_percent()
        while not sample_stop.wait(.05):
            resources.append((process.cpu_percent(),process.memory_info().rss))
    def worker():
        nonlocal inflight
        while not stop.is_set():
            try:items=[pending.get(timeout=.005)]
            except queue.Empty:continue
            while len(items)<batch:
                try:items.append(pending.get_nowait())
                except queue.Empty:break
            inflight=len(items)
            started=time.perf_counter()
            try:
                # Same scalar values/model as quality evaluation, no labels in the scoring input.
                prediction=model.predict(np.array([values[i%len(values)] for i,_,_ in items]))
                finished=time.perf_counter()
                results.extend((i,scheduled,admitted,started,finished,bool(p)) for (i,scheduled,admitted),p in zip(items,prediction))
            except Exception as exc:errors.extend((item[0],time.perf_counter(),type(exc).__name__) for item in items)
            inflight=0
    worker_thread=threading.Thread(target=worker);sampler=threading.Thread(target=sample)
    worker_thread.start();sampler.start()
    duration=config['load_duration_seconds'];planned=int(duration*rate)
    baseline_rss=process.memory_info().rss
    cpu0=process.cpu_times();t0=time.perf_counter();rejected=0;admitted=0;max_schedule_lag=0.0
    for i in range(planned):
        scheduled=t0+i/rate
        remaining=scheduled-time.perf_counter()
        if remaining>0:time.sleep(remaining)
        actual=time.perf_counter();max_schedule_lag=max(max_schedule_lag,actual-scheduled)
        try:pending.put_nowait((i,scheduled,actual));admitted+=1
        except queue.Full:rejected+=1
        peak_depth=max(peak_depth,pending.qsize())
    if time.perf_counter()<t0+duration:time.sleep(t0+duration-time.perf_counter())
    production_finished=time.perf_counter()
    at_window=[r for r in results if r[4]<=production_finished];backlog_at_window=admitted-len(at_window)-len(errors)
    deadline=production_finished+config['load_drain_seconds']
    while len(results)+len(errors)<admitted and time.perf_counter()<deadline:time.sleep(.005)
    cutoff=time.perf_counter();queued_at_cutoff=pending.qsize();inflight_at_cutoff=inflight
    stop.set();worker_thread.join(timeout=2)
    sample_stop.set();sampler.join(timeout=2)
    if worker_thread.is_alive():raise RuntimeError('scoring worker did not stop')
    cpu1=process.cpu_times()
    completed=[r for r in results if r[4]<=cutoff]
    errors=[r for r in errors if r[1]<=cutoff]
    end_to_end=np.array([r[4]-r[1] for r in completed])
    after_admission=np.array([r[4]-r[2] for r in completed])
    budget=config['latency_budget_ms']/1000
    unresolved=admitted-len(completed)-len(errors)
    assert planned==rejected+len(completed)+len(errors)+unresolved
    cpu_seconds=cpu1.user+cpu1.system-cpu0.user-cpu0.system
    elapsed=cutoff-t0
    return {'target_rate':rate,'batch_max':batch,'offered':planned,'admitted':admitted,'rejected':rejected,
        'completed_by_cutoff':len(completed),'completed_during_offer_window':len(at_window),
        'queue_pending_sample_at_cutoff':queued_at_cutoff,'inflight_sample_at_cutoff':inflight_at_cutoff,
        'errors':len(errors),'unresolved_at_cutoff':unresolved,'backlog_at_offer_end':backlog_at_window,
        'queue_capacity':config['load_queue_capacity'],'queue_peak':peak_depth,'offering_seconds':production_finished-t0,
        'elapsed_including_drain_seconds':elapsed,'completed_per_offer_second':len(at_window)/duration,
        'completed_per_elapsed_second':len(completed)/elapsed,'drain_seconds':cutoff-production_finished,
        'rejected_fraction':rejected/planned,'unresolved_fraction':unresolved/planned,
        'p50_scheduled_to_result_ms':percentile(end_to_end,.5),'p95_scheduled_to_result_ms':percentile(end_to_end,.95),
        'p99_scheduled_to_result_ms':percentile(end_to_end,.99),'p95_admitted_to_result_ms':percentile(after_admission,.95),
        'within_budget_all_offered_fraction':int((end_to_end<=budget).sum())/planned,
        'max_producer_schedule_lag_ms':max_schedule_lag*1000,'cpu_seconds':cpu_seconds,
        'mean_process_cpu_percent':cpu_seconds/elapsed*100,'sampled_peak_process_cpu_percent':max([r[0] for r in resources],default=0),
        'baseline_rss_mib':baseline_rss/1048576,'sampled_peak_rss_mib':max([r[1] for r in resources],default=baseline_rss)/1048576,
        'scope':'single scorer thread plus open-loop producer and sampler; local memory queue only'}


def run(output):
    out=Path(output);config=json.loads((out/'config.json').read_text());manifest=json.loads((out/'manifest.json').read_text())
    for name,expected in manifest['source_hashes'].items():
        if sha(Path(__file__).with_name(name))!=expected:raise ValueError('experiment source changed after quality run')
    conditions=list(itertools.product(['gutentag','tsb_csv'],config['load_rates'],config['load_batches'],range(config['load_repeats'])))
    np.random.default_rng(20261018).shuffle(conditions)
    rows=[];models={};values={}
    for family in ['gutentag','tsb_csv']:
        path=out/(family+'-iforest.joblib')
        if sha(path)!=manifest['artifact_hashes'][path.name]:raise ValueError('model hash mismatch')
        models[family]=joblib.load(path) # Only own experiment-generated artifact with manifest hash.
        values[family]=np.load(out/(family+'-representative.npz'),allow_pickle=False)['changed']
        models[family].predict(values[family][:32])
    for index,(family,rate,batch,repeat) in enumerate(conditions):
        result={'family':family,'repeat':repeat,**one_condition(models[family],values[family],rate,batch,config)}
        rows.append(result);pd.DataFrame(rows).to_csv(out/'load.csv',index=False)
        print(json.dumps({'phase':'load','condition':index+1,'total':len(conditions),'family':family,'rate':rate,'batch':batch,
            'completed':result['completed_by_cutoff'],'rejected':result['rejected'],'p95_ms':result['p95_scheduled_to_result_ms']}),flush=True)
    manifest['status']='complete';manifest['load_conditions']=len(rows);manifest['load_completed_unix']=time.time()
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    with threadpool_limits(limits=1):run(parser.parse_args().output)
