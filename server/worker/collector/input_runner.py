"""One Collector, manually switchable API/CSV adapters, one shared admission path."""
import argparse
import json
import os
import time
from datetime import datetime,timezone
from pathlib import Path
import psycopg
from psycopg.pq import TransactionStatus
from dotenv import load_dotenv
from collector.api_client import from_environment,SourceUnavailable
from collector.api_runner import catalog_page
from collector.input_contract import mapped_api_records
from collector.input_store import ensure_source,admit_batch
from collector.telemetry import store_sample,store_snapshot,monitor_preview


def read_state(conn,source):
    row=conn.execute("""SELECT c.mode,c.revision,c.run_id,c.config_version_id,r.settings,r.next_position,r.status,
        r.last_attempt_at,r.last_success_at,r.replay_id,p.record_count,c.updated_at
        FROM ops_input_controls c LEFT JOIN ops_input_runs r ON r.id=c.run_id
        LEFT JOIN data_input_replays p ON p.id=r.replay_id WHERE c.source_id=%s""",(source,)).fetchone()
    conn.commit()
    if row is None:raise ValueError('input_source_not_initialized')
    return dict(zip(('mode','revision','run','config','settings','position','status','attempt','success','replay','count','switched'),row))


def ingest_phase(old):
    """data_input_origins.ingest_phase for this tick (contracts/collector-metrics.md 3.1).

    csv -> 'replay'. api -> 'initial' when the run has no committed successful poll yet, or the last one is older than
    the latest mode switch (pause -> resume reuses the run): the response then holds transactions that were already
    running before collection (re)started. Otherwise 'live'. The fence re-checks revision and next_position under
    lock in the admission transaction, so `success`/`switched` read here are the values that transaction sees.
    """
    if old['mode']=='csv':return 'replay'
    if old['success'] is None or (old['switched'] is not None and old['success']<old['switched']):return 'initial'
    return 'live'


def fence(conn,source,old):
    current=conn.execute('SELECT mode,revision,run_id,config_version_id FROM ops_input_controls WHERE source_id=%s FOR UPDATE',(source,)).fetchone()
    if current[:3]!=(old['mode'],old['revision'],old['run']):return False
    owned=conn.execute('SELECT next_position,status FROM ops_input_runs WHERE id=%s FOR UPDATE',(old['run'],)).fetchone()
    return owned==(old['position'],'running')


def remaining_wait(old,interval,schedule,*,now=None,monotonic=None):
    """Seconds until the next tick is due (<=0: due now).

    csv: end-to-end pacing from the last committed row (`last_success_at`), unchanged.
    api: start-to-start. `schedule` (kept by the runner process) holds the monotonic start of this run's last poll,
    so the next poll is due at that start + pollSeconds; an overrun poll is followed immediately by one poll, never a
    catch-up burst. Without an in-process start (new process, other run) the stored `last_attempt_at` is used.
    """
    now=now or datetime.now(timezone.utc)
    if old['mode']=='api' and schedule is not None and schedule.get('run')==old['run'] and schedule.get('started') is not None:
        return schedule['started']+interval-(time.monotonic() if monotonic is None else monotonic)
    last=old['success'] if old['mode']=='csv' else old['attempt']
    return interval-(now-last).total_seconds() if last else 0


def tick(conn,source,*,api_factory=from_environment,mapping=None,collect_catalog=True,schedule=None):
    old=read_state(conn,source)
    if old['mode']=='paused' or old['status']!='running':return {'idle':True,'wait':.5}
    interval=1/old['settings']['rate'] if old['mode']=='csv' else old['settings']['pollSeconds']
    remaining=remaining_wait(old,interval,schedule)
    if remaining>0:return {'idle':True,'wait':min(.5,remaining)}
    api=None;receipt=None;values=None
    try:
        if old['mode']=='api':
            if schedule is not None:schedule.update(run=old['run'],started=time.monotonic())
            api=api_factory()
            if collect_catalog:
                try:
                    metadata=catalog_page(conn,api,source);conn.commit()
                    cr=api.last_receipt
                    if cr:
                        store_sample(conn,source,'interfaces',cr,(time.monotonic()-cr['received_monotonic'])*1000,
                            interval,snapshot_id=metadata['snapshotId'],snapshot_created=metadata['snapshotCreated'],item_count=metadata['itemCount'])
                        conn.commit()
                except Exception:
                    conn.rollback()
                    if api.last_receipt:
                        store_sample(conn,source,'interfaces',api.last_receipt,None,interval,error_code='catalog_collection_failed');conn.commit()
            values=api.in_progress();receipt=api.last_receipt
            # Parse the whole response before admission; a contract error writes no partial log batch.
            records=list(mapped_api_records(values,mapping))
        else:
            total=old['count']*old['settings']['repeat']
            if old['position']>=total:
                with conn.transaction():
                    if fence(conn,source,old):conn.execute("UPDATE ops_input_runs SET status='completed' WHERE id=%s",(old['run'],))
                return {'completed':True,'wait':.5}
            stored=conn.execute('SELECT layer,values_json FROM data_input_replay_rows WHERE replay_id=%s AND position=%s',
                (old['replay'],old['position']%old['count'])).fetchone();conn.commit()
            if stored is None:raise ValueError('frozen_csv_row_missing')
            records=[stored]
        with conn.transaction():
            if not fence(conn,source,old):return {'discarded':'input_mode_changed','wait':0}
            namespace='api' if old['mode']=='api' else f'csv:{old["run"]}:{old["position"]//old["count"]}'
            phase=ingest_phase(old);received=receipt['finished_at'] if old['mode']=='api' else None
            outcomes=admit_batch(conn,source,old['run'],old['mode'],records,namespace=namespace,
                position=old['position'] if old['mode']=='csv' else None,config=old['config'],
                phase=phase,received_at=received)
            if old['mode']=='api':
                snapshot,created=store_snapshot(conn,source,'in-progress',monitor_preview(values),
                    'mapped-arrival-snapshot-v1',receipt['finished_at'])
                store_sample(conn,source,'in-progress',receipt,(time.monotonic()-receipt['received_monotonic'])*1000,
                    interval,snapshot_id=snapshot,snapshot_created=created,item_count=len(values))
                conn.execute("""INSERT INTO ops_api_poll_state(source_id,endpoint,checkpoint,last_attempt_at,last_success_at)
                    VALUES(%s,'in-progress',%s::jsonb,clock_timestamp(),clock_timestamp()) ON CONFLICT(source_id,endpoint)
                    DO UPDATE SET checkpoint=excluded.checkpoint,last_attempt_at=excluded.last_attempt_at,
                    last_success_at=excluded.last_success_at,error_code=NULL""",
                    (source,json.dumps({'logIngestionReady':True,'mapping':'configured' if mapping else 'canonical-tagged-v1',
                        'normalizedRecords':len(records),'arrivalCoverage':'in-progress-only'})))
            conn.execute("""UPDATE ops_input_runs SET next_position=next_position+1,accepted_count=accepted_count+%s,
                duplicate_count=duplicate_count+%s,unidentified_count=unidentified_count+%s,last_error=NULL,
                last_attempt_at=clock_timestamp(),last_success_at=clock_timestamp() WHERE id=%s""",
                (sum(o['created'] for o in outcomes),sum(not o['created'] for o in outcomes),
                 sum(not o['identified'] for o in outcomes),old['run']))
            conn.execute('UPDATE ops_input_controls SET worker_heartbeat_at=clock_timestamp() WHERE source_id=%s',(source,))
        wait=min(.5,interval)
        if old['mode']=='api' and schedule is not None:wait=max(0,min(wait,remaining_wait(old,interval,schedule)))
        return {'accepted':sum(o['created'] for o in outcomes),'duplicates':sum(not o['created'] for o in outcomes),
            'detectJobs':[o['detectJob'] for o in outcomes if o.get('detectJob')],'wait':wait}
    except Exception as exc:
        conn.rollback()
        code=str(exc) if isinstance(exc,(ValueError,SourceUnavailable)) and str(exc).replace('_','').isalnum() else type(exc).__name__
        with conn.transaction():
            if fence(conn,source,old):
                conn.execute('UPDATE ops_input_runs SET last_error=%s,last_attempt_at=clock_timestamp() WHERE id=%s',(code,old['run']))
                conn.execute('UPDATE ops_input_controls SET worker_heartbeat_at=clock_timestamp() WHERE source_id=%s',(source,))
                if old['mode']=='api' and api and api.last_receipt:
                    snapshot=None;created=None
                    if values is not None:
                        snapshot,created=store_snapshot(conn,source,'in-progress',monitor_preview(values),'monitoring_contract_required',api.last_receipt['finished_at'])
                        conn.execute("""INSERT INTO ops_api_poll_state(source_id,endpoint,checkpoint,last_attempt_at,error_code)
                            VALUES(%s,'in-progress','{"logIngestionReady":false}',clock_timestamp(),%s) ON CONFLICT(source_id,endpoint)
                            DO UPDATE SET checkpoint=excluded.checkpoint,last_attempt_at=excluded.last_attempt_at,error_code=excluded.error_code""",(source,code))
                    store_sample(conn,source,'in-progress',api.last_receipt,None,interval,error_code=code,
                        snapshot_id=snapshot,snapshot_created=created,item_count=len(values) if values is not None else None)
                # CSV format/DB failures retain the exact position; operator can fix/retry or switch mode.
        return {'errorCode':code,'wait':min(5,max(.5,interval))}


# Pending direct-delivery notices held in memory. Detector's /detect serves one job at a time and answers 202
# "queued" at once when busy, so notices beyond what it takes are cheap no-ops; the bound only caps memory and how
# stale a notice can get. Overflow is dropped: those jobs are already committed and Detector queue polling claims them.
DELIVERY_CAPACITY=256
DELIVERY_TIMEOUT=2
DELIVERY_HOSTS=frozenset({'detector','127.0.0.1','localhost'})


def delivery_target():
    """(url, token) for direct delivery, or None when DETECTOR_DELIVERY_URL is unset. Rejected settings raise."""
    from urllib.parse import urlsplit
    url=os.environ.get('DETECTOR_DELIVERY_URL','')
    token=os.environ.get('DETECTOR_DELIVERY_TOKEN','')
    if not url:return None
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or parsed.hostname not in DELIVERY_HOSTS or parsed.username or parsed.password or parsed.query or parsed.fragment or len(token)<32:
        raise ValueError('detector_delivery_configuration_rejected')
    return url.rstrip('/')+'/detect',token


class Delivery:
    """After-commit optimization only: tell Detector about detect jobs a tick has committed, off the poll loop.

    Reads no database queue. `offer` never blocks: jobs that do not fit stay in the durable queue, which Detector
    polling claims, as do failed or slow POSTs. One background sender posts {jobId, inputManifestHash} in order.
    """
    def __init__(self,url,token,*,capacity=DELIVERY_CAPACITY,timeout=DELIVERY_TIMEOUT):
        import queue
        import threading
        self.url,self.token,self.timeout=url,token,timeout
        self.pending=queue.Queue(maxsize=capacity)
        self.offered=0;self.dropped=0;self.sent=0
        self.thread=threading.Thread(target=self._send_forever,name='detector-delivery',daemon=True)
        self.thread.start()

    @classmethod
    def from_environment(cls,**kwargs):
        target=delivery_target()
        return cls(*target,**kwargs) if target else None

    def offer(self,jobs):
        """Queue committed jobs for delivery without waiting. Returns how many were queued."""
        import queue
        queued=0
        for job in jobs:
            try:self.pending.put_nowait({'jobId':job['jobId'],'inputManifestHash':job['inputManifestHash']})
            except queue.Full:
                self.dropped+=1;continue
            queued+=1
        self.offered+=queued
        return queued

    def post(self,job):
        import urllib.request
        from generation_client import NoRedirect
        request=urllib.request.Request(self.url,data=json.dumps(job).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=self.timeout) as response:
                response.read(4096)
        except (OSError,TimeoutError,ValueError):
            pass # durable queue polling remains authoritative

    def _send_forever(self):
        while True:
            job=self.pending.get()
            try:
                self.post(job);self.sent+=1
            except Exception:
                pass
            finally:
                self.pending.task_done()

    def drain(self,timeout):
        """Wait up to `timeout` seconds for queued notices to be sent (used by --once)."""
        deadline=time.monotonic()+timeout
        with self.pending.all_tasks_done:
            while self.pending.unfinished_tasks:
                left=deadline-time.monotonic()
                if left<=0:return False
                self.pending.all_tasks_done.wait(left)
        return True


def run(once=False):
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    dsn=os.environ['DATABASE_URL'];key=os.environ['COLLECTOR_SOURCE_KEY']
    mapping_path=os.environ.get('COLLECTOR_LOG_MAPPING_FILE')
    mapping=json.loads(Path(mapping_path).read_text()) if mapping_path else None
    try:notifier=Delivery.from_environment()
    except ValueError as exc:
        # Collection continues; Detector queue polling still claims every committed job.
        print(json.dumps({'deliveryError':str(exc)}),flush=True);notifier=None
    schedule={}
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        source=ensure_source(conn,key);conn.commit()
        if not conn.execute('SELECT pg_try_advisory_lock(81735,hashtext(%s))',(str(source),)).fetchone()[0]:
            raise RuntimeError('collector_source_already_running')
        conn.commit()
        while True:
            result=tick(conn,source,mapping=mapping,schedule=schedule)
            if result.get('errorCode'):print(json.dumps({'inputError':result['errorCode']}),flush=True)
            # tick commits its admission transaction before returning; never announce work that is not yet durable.
            if notifier and result.get('detectJobs') and conn.info.transaction_status==TransactionStatus.IDLE:
                notifier.offer(result['detectJobs'])
            if once:
                if notifier:notifier.drain(DELIVERY_TIMEOUT+1)
                return
            wait=result.get('wait',.5)
            if wait>0:time.sleep(wait)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true')
    run(parser.parse_args().once)
