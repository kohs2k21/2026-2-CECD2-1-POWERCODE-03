"""One Collector, manually switchable API/CSV adapters, one shared admission path."""
import argparse
import json
import os
import time
from datetime import datetime,timezone
from pathlib import Path
import psycopg
from dotenv import load_dotenv
from collector.api_client import from_environment,SourceUnavailable
from collector.api_runner import catalog_page,delivery
from collector.input_contract import mapped_api_records
from collector.input_store import ensure_source,admit
from collector.telemetry import store_sample,store_snapshot,monitor_preview


def read_state(conn,source):
    row=conn.execute("""SELECT c.mode,c.revision,c.run_id,c.config_version_id,r.settings,r.next_position,r.status,
        r.last_attempt_at,r.last_success_at,r.replay_id,p.record_count
        FROM ops_input_controls c LEFT JOIN ops_input_runs r ON r.id=c.run_id
        LEFT JOIN data_input_replays p ON p.id=r.replay_id WHERE c.source_id=%s""",(source,)).fetchone()
    conn.commit()
    if row is None:raise ValueError('input_source_not_initialized')
    return dict(zip(('mode','revision','run','config','settings','position','status','attempt','success','replay','count'),row))


def fence(conn,source,old):
    current=conn.execute('SELECT mode,revision,run_id,config_version_id FROM ops_input_controls WHERE source_id=%s FOR UPDATE',(source,)).fetchone()
    if current[:3]!=(old['mode'],old['revision'],old['run']):return False
    owned=conn.execute('SELECT next_position,status FROM ops_input_runs WHERE id=%s FOR UPDATE',(old['run'],)).fetchone()
    return owned==(old['position'],'running')


def tick(conn,source,*,api_factory=from_environment,mapping=None,collect_catalog=True):
    old=read_state(conn,source)
    if old['mode']=='paused' or old['status']!='running':return {'idle':True,'wait':.5}
    interval=1/old['settings']['rate'] if old['mode']=='csv' else old['settings']['pollSeconds']
    last=old['success'] if old['mode']=='csv' else old['attempt']
    if last:
        remaining=interval-(datetime.now(timezone.utc)-last).total_seconds()
        if remaining>0:return {'idle':True,'wait':min(.5,remaining)}
    api=None;receipt=None;values=None
    try:
        if old['mode']=='api':
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
            outcomes=[admit(conn,source,old['run'],old['mode'],layer,raw,namespace=namespace,
                position=old['position'] if old['mode']=='csv' else None,config=old['config']) for layer,raw in records]
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
        return {'accepted':sum(o['created'] for o in outcomes),'duplicates':sum(not o['created'] for o in outcomes),'wait':min(.5,interval)}
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


def run(once=False):
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    dsn=os.environ['DATABASE_URL'];key=os.environ['COLLECTOR_SOURCE_KEY']
    mapping_path=os.environ.get('COLLECTOR_LOG_MAPPING_FILE')
    mapping=json.loads(Path(mapping_path).read_text()) if mapping_path else None
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        source=ensure_source(conn,key);conn.commit()
        if not conn.execute('SELECT pg_try_advisory_lock(81735,hashtext(%s))',(str(source),)).fetchone()[0]:
            raise RuntimeError('collector_source_already_running')
        conn.commit()
        while True:
            result=tick(conn,source,mapping=mapping)
            if result.get('errorCode'):print(json.dumps({'inputError':result['errorCode']}),flush=True)
            if result.get('accepted'):
                try:delivery(conn,source)
                except Exception:conn.rollback() # durable queue polling remains authoritative
            if once:return
            time.sleep(result.get('wait',.5))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true')
    run(parser.parse_args().once)
