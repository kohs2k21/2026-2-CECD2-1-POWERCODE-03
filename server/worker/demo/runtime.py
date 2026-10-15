"""Prepare a CPU-only candidate on a dedicated demo source and replay at a paced rate."""
from __future__ import annotations
import hashlib
import json
import math
import re
import time
import uuid
from datetime import timezone,timedelta
from pathlib import Path
import psycopg
from collector.storage import ingest_observation
from demo.sources import digest,numeric_features,csv_rows,postgres_rows
from detector.jobs import enqueue_observation
from pg_queue import enqueue,claim
from trainer.catalog import register_feature_set
from trainer.lease import TrainingLeaseHeartbeat
from trainer.runner import _claim_job,_execute,_finish

KEY=re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
SCHEMA={
 '001_postgres_queues.sql':('powercode_queue.detection_logs','powercode_queue.training_jobs'),
 '002_core_erd.sql':('public.data_observations','public.data_processes','public.ops_sources'),
 '004_erd_v03_contracts.sql':('public.run_input_manifests','public.run_llm_results'),
 '004_feature_pipeline.sql':('public.data_feature_materializations','public.data_label_evidence'),
 '005_research_runtime.sql':('public.ops_window_monitors',),
 '006_service_transition.sql':('public.run_job_snapshots','public.run_decision_events','powercode_queue.llm_recommendation_jobs'),
 '007_api_collection_metrics.sql':('public.data_api_snapshots','public.ops_api_poll_samples'),
 '009_switchable_inputs.sql':('public.ops_input_controls','public.ops_input_runs','public.data_input_replays','public.data_input_replay_rows','public.data_input_origins'),
 '008_demo_replay.sql':('public.ops_demo_runs','public.data_demo_inputs','public.ops_demo_passes','public.data_demo_deliveries'),
}

def preflight(conn):
    missing={}
    with conn.cursor() as cur:
        for migration,tables in SCHEMA.items():
            absent=[]
            for table in tables:
                cur.execute('SELECT to_regclass(%s)',(table,))
                if cur.fetchone()[0] is None:absent.append(table)
            if absent:missing[migration]=absent
        cur.execute("""SELECT t.typname,e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid
            JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'""")
        enums=set(cur.fetchall())
        absent=[name+':'+value for name,value in (('source_layer','B'),('job_kind','detect'),
            ('job_kind','recommend_feature'),('job_kind','explain'),('job_status','blocked'),('artifact_kind','model_input'))
            if (name,value) not in enums]
        if absent:missing['003_erd_v03_enums.sql']=absent
        cur.execute("""SELECT EXISTS(SELECT FROM pg_trigger WHERE tgrelid=to_regclass('public.ml_rule_versions')
            AND tgname='ml_rule_versions_immutable' AND NOT tgisinternal AND tgenabled<>'D')""")
        if not cur.fetchone()[0]:missing['003_rule_versions_immutable.sql']=['ml_rule_versions_immutable trigger']
    return {'ready':not missing,'missingObjects':missing,
        'note':'Read-only object/enum/trigger check, not a migration ledger or schema-drift audit. No automatic migrations; commit 003 enums before v03.'}


def _run(conn,key):
    with conn.cursor() as cur:
        cur.execute('SELECT id,source_id,status,record_count,baseline_count,config_version_id,input_hash,train_job_id,error_code,preparation_spec FROM ops_demo_runs WHERE run_key=%s',(key,))
        row=cur.fetchone()
    if row is None:raise ValueError('demo_run_not_found')
    return dict(zip(('id','source','status','count','baselineCount','config','inputHash','trainJob','errorCode','spec'),row))


def _lock(conn,name):
    if not conn.execute('SELECT pg_try_advisory_lock(81737,hashtext(%s))',(name,)).fetchone()[0]:
        raise ValueError('demo_operation_already_running')
    conn.commit()


def _unlock(conn,name):
    conn.rollback()
    conn.execute('SELECT pg_advisory_unlock(81737,hashtext(%s))',(name,));conn.commit()


def _execution(conn,source,key):
    identifier=uuid.uuid5(uuid.NAMESPACE_URL,key)
    conn.execute("""INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
        VALUES(%s,%s,'P',%s,%s::jsonb,'demo-process-replay-v1',clock_timestamp()) ON CONFLICT DO NOTHING""",
        (identifier,source,key,json.dumps({'simulation':True,'replayIdentity':key})))
    return identifier


def _train(conn,dsn,job_id,queue_id,root):
    message=claim(conn,'training',message_id=queue_id);conn.commit()
    if message:
        owned=_claim_job(conn,message,'demo-prepare');conn.commit()
        if owned:
            job,attempt,token=owned
            heartbeat=TrainingLeaseHeartbeat(lambda:psycopg.connect(dsn,connect_timeout=10),message,job_id,attempt,token)
            heartbeat.start()
            try:
                result=_execute(conn,job,attempt,root,message,token);conn.commit()
                if not result.get('_finalized'):
                    if heartbeat.lost:raise RuntimeError('demo_training_ownership_lost')
                    _finish(conn,message,job,attempt,token,result,None,True);conn.commit()
            except Exception:
                conn.rollback()
                try:_finish(conn,message,job,attempt,token,None,'demo_training_failed',True);conn.commit()
                except Exception:conn.rollback()
                raise
            finally:heartbeat.stop()
    # Another Trainer may have claimed the exact same job; don't duplicate computation.
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        row=conn.execute('SELECT status,result_summary FROM run_jobs WHERE id=%s',(job_id,)).fetchone();conn.commit()
        if row[0]=='succeeded':return row[1]
        if row[0]=='failed':raise ValueError('demo_training_failed')
        time.sleep(.5)
    raise ValueError('demo_training_pending_use_status')


def prepare(dsn,root:Path,*,key,kind,process_csv=None,source_dsn=None,origin_source=None,
            limit=1000000,baseline_limit=5000,actor='demo-operator',deadline_ms=3000,transaction_csv=None,body_csv=None):
    if not KEY.fullmatch(key) or kind not in ('postgres','csv') or not 1000<=baseline_limit<=10000 or not 1000<=limit<=1000000 or not 100<=deadline_ms<=30000:
        raise ValueError('invalid_demo_preparation_settings')
    if not root.is_dir():raise ValueError('artifact_root_must_exist')
    run_id=uuid.uuid5(uuid.NAMESPACE_URL,'powercode-demo:'+key)
    source_id=uuid.uuid5(uuid.NAMESPACE_URL,'powercode-demo-source:'+key)
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        check=preflight(conn);conn.commit()
        if not check['ready']:raise ValueError('demo_schema_missing_run_preflight_and_apply_only_missing_migrations')
        _lock(conn,'prepare:'+key)
        try:
            old=conn.execute('SELECT status,input_kind,origin_source_id,preparation_spec FROM ops_demo_runs WHERE id=%s',(run_id,)).fetchone();conn.commit()
            if old:
                current=_run(conn,key);conn.commit()
                if current['status']=='ready':
                    expected={'processCsv':str(process_csv) if process_csv else None,'transactionCsv':str(transaction_csv) if transaction_csv else None,'bodyCsv':str(body_csv) if body_csv else None,'limit':limit,'baselineLimit':baseline_limit,'deadlineMs':deadline_ms}
                    if old[1]!=kind or str(old[2] or '')!=str(origin_source or '') or old[3].get('settings')!=expected:
                        raise ValueError('run_settings_changed_use_new_run_key')
                    return status(dsn,key)
                raise ValueError('incomplete_preparation_use_new_run_key')
            spec={'schemaVersion':1,'simulation':True,'limit':limit,'baselineLimit':baseline_limit,
                'settings':{'processCsv':str(process_csv) if process_csv else None,'transactionCsv':str(transaction_csv) if transaction_csv else None,'bodyCsv':str(body_csv) if body_csv else None,'limit':limit,'baselineLimit':baseline_limit,'deadlineMs':deadline_ms},
                'featureProfile':'observed-varying-process-counts-v1','model':'actual-cpu-iforest',
                'detectionPolicy':{'mode':'ml_only','deadlineMs':deadline_ms,'combination':'selected_path'}}
            with conn.transaction():
                conn.execute("""INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
                    VALUES(%s,%s,%s,'demo-replay','demo-process-replay-v1','{"status":"unknown","simulation":true}'::jsonb,
                    '{"layer":"P","bodyValuesExcluded":true}'::jsonb,clock_timestamp())""",(source_id,'demo-replay:'+key,'Demo: '+key))
                conn.execute("""INSERT INTO ops_demo_runs(id,run_key,source_id,input_kind,origin_source_id,status,preparation_spec)
                    VALUES(%s,%s,%s,%s,%s,'loading',%s::jsonb)""",(run_id,key,source_id,kind,origin_source,json.dumps(spec)))
            rows=csv_rows(Path(process_csv),limit) if kind=='csv' else postgres_rows(source_dsn or dsn,origin_source,limit)
            count=0;overall=hashlib.sha256();reference=[]
            batch=[]
            def save_batch():
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.executemany("""INSERT INTO data_demo_inputs(run_id,sequence,execution_group,origin_observation_id,values_json,label,content_hash)
                            VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s)""",batch)
            for row in rows:
                hashed=digest(row);overall.update((hashed+'\n').encode())
                batch.append((run_id,count,row['group'],row['originObservationId'],json.dumps(row['raw']),row['label'],hashed))
                if row['label']!='anomaly' and len(reference)<baseline_limit:reference.append(row)
                count+=1
                if len(batch)==500:save_batch();batch=[]
            if batch:save_batch()
            if len(reference)<1000:raise ValueError('demo_needs_at_least_1000_reference_process_rows')
            definitions=numeric_features(reference)
            start=conn.execute('SELECT clock_timestamp()').fetchone()[0];conn.commit()
            # Copy reference records only into the isolated source, preserving simulation execution groups.
            for index,row in enumerate(reference):
                with conn.transaction():
                    execution=_execution(conn,source_id,f'demo-baseline:{run_id}:{row["group"]}')
                    saved=ingest_observation(conn,source_id=source_id,layer='P',dedup_key=f'demo-baseline:{run_id}:{index}',
                        raw=row['raw'],execution_id=execution,revision_no=index+1)
                    if row['label']:
                        conn.execute("""INSERT INTO data_label_evidence(id,observation_id,revision_no,label,reason_code,created_by_actor_id)
                            VALUES(%s,%s,1,%s,'ORIGINAL_MANUAL_LABEL',%s)""",(uuid.uuid4(),saved.observation_id,row['label'],actor))
            if transaction_csv or body_csv:
                from demo.context import copy_csv_context
                spec['contextCounts']=copy_csv_context(conn,source_id,run_id,transaction_csv,body_csv,limit)
            end=conn.execute('SELECT clock_timestamp()').fetchone()[0]+timedelta(microseconds=1);conn.commit()
            feature_set=register_feature_set(conn,definitions,actor,definition_kind='operator_defined');conn.commit()
            job_id=uuid.uuid5(uuid.NAMESPACE_URL,f'demo-training:{run_id}')
            queue_id=uuid.uuid5(uuid.NAMESPACE_URL,f'demo-training-queue:{run_id}')
            train_spec={'kind':'train','sourceId':str(source_id),'featureSetId':str(feature_set),
                'from':start.astimezone(timezone.utc).isoformat(),'until':end.astimezone(timezone.utc).isoformat(),
                'ruleVersionIds':[],'detectionPolicy':spec['detectionPolicy'],'demoRunId':str(run_id)}
            with conn.transaction():
                conn.execute("""INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,idempotency_key,request_hash,
                    input_spec,contract_version,queue,producer_kind,producer_key,max_attempts,attempt_count,next_run_at,created_at)
                    VALUES(%s,'train','queued',%s,'admin',%s,%s,%s::jsonb,'v03','training','user',%s,3,0,clock_timestamp(),clock_timestamp())""",
                    (job_id,actor,str(job_id),digest(train_spec),json.dumps(train_spec),actor))
                enqueue(conn,'training',{'schemaVersion':1,'jobId':str(job_id)},message_id=queue_id)
                conn.execute("""UPDATE ops_demo_runs SET status='preparing',record_count=%s,baseline_count=%s,input_hash=%s,
                    feature_set_id=%s,train_job_id=%s,preparation_spec=%s::jsonb WHERE id=%s""",(count,len(reference),overall.hexdigest(),feature_set,job_id,json.dumps(spec),run_id))
            result=_train(conn,dsn,job_id,queue_id,root)
            with conn.transaction():
                conn.execute("UPDATE ops_demo_runs SET status='ready',config_version_id=%s,ready_at=clock_timestamp() WHERE id=%s",
                    (result['configVersionId'],run_id))
            return status(dsn,key)
        except BaseException as exc:
            conn.rollback()
            try:
                conn.execute("UPDATE ops_demo_runs SET status='failed',error_code=%s WHERE id=%s AND status<>'ready'",(type(exc).__name__,run_id));conn.commit()
            except Exception:conn.rollback()
            raise
        finally:_unlock(conn,'prepare:'+key)


class Pacer:
    """No catch-up bursts. The requested rate is a target, not guaranteed throughput."""
    def __init__(self,rate,clock=time.monotonic,sleep=time.sleep):
        if not math.isfinite(rate) or not .1<=rate<=10000:raise ValueError('invalid_demo_rate')
        self.interval=1/rate;self.clock=clock;self.sleep=sleep;self.next_at=clock()
    def before_send(self):
        delay=self.next_at-self.clock()
        if delay>0:self.sleep(delay)
    def after_send(self):self.next_at=self.clock()+self.interval


def play(dsn,*,key,pass_key,rate=10,repeat=1,notify=None,stop_after=None):
    if not KEY.fullmatch(key) or not KEY.fullmatch(pass_key) or type(repeat) is not int or not 1<=repeat<=100:
        raise ValueError('invalid_demo_play_settings')
    pacer=Pacer(rate)
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        current=_run(conn,key);conn.commit()
        if current['status']!='ready':raise ValueError('demo_run_not_ready')
        pass_id=uuid.uuid5(uuid.NAMESPACE_URL,f'demo-pass:{current["id"]}:{pass_key}')
        lock_name='play:'+str(pass_id);_lock(conn,lock_name)
        emitted=0;changed=False
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO ops_demo_passes(id,run_id,pass_key,rate,repeat_count,total_count,status)
                    VALUES(%s,%s,%s,%s,%s,%s,'running') ON CONFLICT DO NOTHING""",
                    (pass_id,current['id'],pass_key,rate,repeat,current['count']*repeat))
            settings=conn.execute('SELECT rate,repeat_count,next_sequence,status,total_count FROM ops_demo_passes WHERE id=%s',(pass_id,)).fetchone();conn.commit()
            if settings[0]!=rate or settings[1]!=repeat:raise ValueError('pass_settings_changed_use_new_pass_key')
            if settings[3]=='completed':return status(dsn,key,pass_key)
            sequence=settings[2]
            changed=True
            conn.execute("UPDATE ops_demo_passes SET status='running',error_code=NULL WHERE id=%s",(pass_id,));conn.commit()
            while sequence<settings[4]:
                if stop_after is not None and emitted>=stop_after:raise KeyboardInterrupt()
                slot=sequence%current['count'];cycle=sequence//current['count']
                row=conn.execute('SELECT execution_group,values_json,content_hash FROM data_demo_inputs WHERE run_id=%s AND sequence=%s',(current['id'],slot)).fetchone();conn.commit()
                if row is None:raise ValueError('frozen_demo_input_missing')
                pacer.before_send()
                with conn.transaction():
                    execution=_execution(conn,current['source'],f'demo-play:{pass_id}:{cycle}:{row[0]}')
                    observed=ingest_observation(conn,source_id=current['source'],layer='P',
                        dedup_key=f'demo-play:{pass_id}:{sequence}',raw=row[1],execution_id=execution,revision_no=sequence+1)
                    payload=enqueue_observation(conn,observed.observation_id,config_id=current['config'])
                    conn.execute("""INSERT INTO data_demo_deliveries(pass_id,sequence,input_sequence,observation_id,job_id)
                        VALUES(%s,%s,%s,%s,%s)""",(pass_id,sequence,slot,observed.observation_id,payload['jobId']))
                    conn.execute('UPDATE ops_demo_passes SET next_sequence=%s WHERE id=%s',(sequence+1,pass_id))
                sequence+=1;emitted+=1
                if notify:
                    try:notify(payload)
                    except (OSError,TimeoutError):pass # already committed; Detector polling recovers
                pacer.after_send()
                if emitted==1 or emitted%max(1,int(rate))==0:
                    print(json.dumps({'demo':True,'runKey':key,'passKey':pass_key,'sent':sequence,'total':settings[4],'targetRate':rate}),flush=True)
            with conn.transaction():conn.execute("UPDATE ops_demo_passes SET status='completed',finished_at=clock_timestamp() WHERE id=%s",(pass_id,))
        except BaseException as exc:
            conn.rollback()
            try:
                if not changed:raise exc
                conn.execute('UPDATE ops_demo_passes SET status=%s,error_code=%s WHERE id=%s AND status<>\'completed\'',
                    ('interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',type(exc).__name__,pass_id));conn.commit()
            except Exception:conn.rollback()
            if not isinstance(exc,KeyboardInterrupt):raise
        finally:_unlock(conn,lock_name)
    return status(dsn,key,pass_key)


def status(dsn,key,pass_key=None):
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        run=_run(conn,key)
        with conn.cursor() as cur:
            cur.execute("""SELECT p.pass_key,p.status,p.rate,p.total_count,p.next_sequence,
                count(d.id),count(d.id) FILTER(WHERE d.status='complete'),count(d.id) FILTER(WHERE d.evidence->>'anomalous'='true'),
                percentile_cont(.5) WITHIN GROUP(ORDER BY extract(epoch FROM d.decided_at-r.sent_at)*1000),
                percentile_cont(.95) WITHIN GROUP(ORDER BY extract(epoch FROM d.decided_at-r.sent_at)*1000),
                p.started_at,p.finished_at,
                count(j.id) FILTER(WHERE j.status='failed'),
                extract(epoch FROM max(r.sent_at)-min(r.sent_at)),count(r.job_id)
                FROM ops_demo_passes p LEFT JOIN data_demo_deliveries r ON r.pass_id=p.id
                LEFT JOIN run_process_decisions d ON d.job_id=r.job_id LEFT JOIN run_jobs j ON j.id=r.job_id
                WHERE p.run_id=%s AND (%s::text IS NULL OR p.pass_key=%s)
                GROUP BY p.id ORDER BY p.started_at""",(run['id'],pass_key,pass_key))
            rows=cur.fetchall()
    return {'demo':True,'runKey':key,'sourceId':str(run['source']),'status':run['status'],
        'inputCount':run['count'],'baselineCount':run['baselineCount'],'configVersionId':str(run['config']) if run['config'] else None,
        'inputHash':run['inputHash'],'errorCode':run['errorCode'],'contextCounts':run['spec'].get('contextCounts',{}),'sourceOccurrenceLatencyMs':None,
        'measurementScope':'historical P replay; enqueue-to-final-decision, not original ESB/API occurrence latency',
        'passes':[{'passKey':r[0],'senderStatus':r[1],'targetRate':r[2],'total':r[3],'sent':r[4],
            'decided':r[5],'complete':r[6],'anomalous':r[7],'pending':r[4]-r[5]-r[12],'failed':r[12],
            'observedSendRate':float((r[14]-1)/r[13]) if r[13] and r[14]>1 else None,
            'sendRateScope':'first-to-last committed send, including pauses',
            'enqueueDecisionP50Ms':r[8],'enqueueDecisionP95Ms':r[9],
            'startedAt':r[10].isoformat(),'finishedAt':r[11].isoformat() if r[11] else None} for r in rows]}
