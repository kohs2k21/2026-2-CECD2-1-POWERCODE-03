"""Shared logical source, frozen CSV stream and append-only observation admission."""
import json
import uuid
from pathlib import Path
from collector.input_contract import csv_records,digest,identity
from collector.storage import ingest_observation
from detector.jobs import enqueue_observation


def ensure_source(conn,key):
    if not isinstance(key,str) or not key.strip() or len(key)>128:raise ValueError('invalid_input_source_key')
    sid=uuid.uuid5(uuid.NAMESPACE_URL,'pipeline-input:'+key)
    conn.execute("""INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
        VALUES(%s,%s,%s,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}',
        '{"layers":["T","P","M","B"],"bodyValuesCollected":false}',clock_timestamp()) ON CONFLICT(dataset_key) DO NOTHING""",(sid,key,key))
    sid,method=conn.execute('SELECT id,ingestion_method FROM ops_sources WHERE dataset_key=%s',(key,)).fetchone()
    if method not in ('switchable-input','customer-api'):raise ValueError('source_key_belongs_to_another_contract')
    conn.execute("INSERT INTO ops_input_controls(source_id,updated_by_actor_id) VALUES(%s,'input-bootstrap') ON CONFLICT DO NOTHING",(sid,))
    return sid


def stage_csv(conn,source,key,files,limit=1000000):
    if not isinstance(key,str) or not key.strip() or len(key)>64 or not 1<=limit<=1000000:
        raise ValueError('invalid_replay_settings')
    if not files or any(layer not in ('T','P','M','B') for layer in files):raise ValueError('csv_files_required')
    # Every referenced file must be readable before any registration writes.
    rows=[];groups={};message_groups={}
    for layer in ('T','P','M','B'):
        if layer not in files:continue
        path=Path(files[layer]);initial=path.stat()
        for index,raw in enumerate(csv_records(layer,path)):
            if index>=limit:break
            tx=raw.get('transaction_id')
            if layer=='B':tx=message_groups.get(raw.get('message_id'))
            group=tx if isinstance(tx,str) and tx else f'unmatched:{layer}:{index}'
            if group not in groups:groups[group]=len(groups)
            if layer=='M' and isinstance(raw.get('message_id'),str):
                mid=raw['message_id']
                if mid in message_groups and message_groups[mid]!=group:message_groups[mid]=None
                else:message_groups[mid]=group
            rows.append((groups[group],('T','P','M','B').index(layer),index,layer,raw))
            if len(rows)>1000000:raise ValueError('csv_total_record_limit_exceeded')
        final=path.stat()
        if (initial.st_ino,initial.st_size,initial.st_mtime_ns)!=(final.st_ino,final.st_size,final.st_mtime_ns):
            raise ValueError('csv_changed_during_capture')
    if not rows or not any(r[3]=='P' for r in rows):raise ValueError('csv_process_rows_required')
    rows.sort(key=lambda item:item[:3])
    hashes=[digest([r[3],r[4]]) for r in rows];manifest=digest(hashes)
    rid=uuid.uuid5(uuid.NAMESPACE_URL,f'input-replay:{source}:{key}')
    conn.execute('SELECT pg_advisory_xact_lock(81738,hashtext(%s))',(str(rid),))
    old=conn.execute('SELECT status,manifest_hash FROM data_input_replays WHERE id=%s',(rid,)).fetchone()
    if old:
        if old!=('ready',manifest):raise ValueError('replay_key_conflict_use_new_key')
        return rid
    counts={layer:sum(r[3]==layer for r in rows) for layer in ('T','P','M','B')}
    conn.execute("INSERT INTO data_input_replays(id,source_id,replay_key,status) VALUES(%s,%s,%s,'loading')",(rid,source,key))
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO data_input_replay_rows(replay_id,position,layer,values_json,content_hash) VALUES(%s,%s,%s,%s::jsonb,%s)',
            [(rid,i,r[3],json.dumps(r[4],ensure_ascii=False),hashes[i]) for i,r in enumerate(rows)])
    conn.execute("UPDATE data_input_replays SET status='ready',record_count=%s,layer_counts=%s::jsonb,manifest_hash=%s WHERE id=%s",
        (len(rows),json.dumps(counts),manifest,rid))
    return rid


def switch(conn,source,*,mode,expected_revision,actor,settings=None,config='keep',restart=False):
    """Caller commits. Same control-row lock is used by the worker admission fence."""
    if mode not in ('paused','api','csv'):raise ValueError('invalid_input_mode')
    row=conn.execute('SELECT revision,run_id,config_version_id FROM ops_input_controls WHERE source_id=%s FOR UPDATE',(source,)).fetchone()
    if row is None:raise ValueError('input_source_not_initialized')
    if row[0]!=expected_revision:raise ValueError('input_revision_conflict')
    chosen=row[2] if config=='keep' else config
    if chosen:
        if not conn.execute('SELECT 1 FROM ml_config_versions WHERE id=%s AND source_id=%s',(chosen,source)).fetchone():
            raise ValueError('input_config_source_mismatch')
    if row[1]:conn.execute("UPDATE ops_input_runs SET status='paused' WHERE id=%s AND status='running'",(row[1],))
    run_id=row[1]
    if mode!='paused':
        settings=settings or {}
        if mode=='api':
            interval=settings.get('pollSeconds',2)
            if type(interval) not in (int,float) or not .1<=interval<=3600:raise ValueError('invalid_api_poll_seconds')
            settings={'pollSeconds':interval};replay=None
        else:
            replay=uuid.UUID(str(settings.get('replayId')))
            rate=settings.get('rate',10);repeat=settings.get('repeat',1)
            if type(rate) not in (int,float) or not .1<=rate<=10000 or type(repeat) is not int or not 1<=repeat<=100:
                raise ValueError('invalid_csv_rate_or_repeat')
            if not conn.execute("SELECT 1 FROM data_input_replays WHERE id=%s AND source_id=%s AND status='ready'",(replay,source)).fetchone():
                raise ValueError('csv_replay_not_ready')
            settings={'replayId':str(replay),'rate':rate,'repeat':repeat}
        previous=None if restart else conn.execute("""SELECT id,settings FROM ops_input_runs WHERE source_id=%s AND mode=%s
            AND replay_id IS NOT DISTINCT FROM %s AND status<>'failed' ORDER BY created_at DESC LIMIT 1""",(source,mode,replay)).fetchone()
        if previous:
            if previous[1]!=settings:raise ValueError('input_settings_changed_restart_required')
            run_id=previous[0]
            conn.execute("UPDATE ops_input_runs SET status=CASE WHEN status='completed' THEN status ELSE 'running' END,last_error=NULL WHERE id=%s",(run_id,))
        else:
            run_id=uuid.uuid4()
            conn.execute("""INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status)
                VALUES(%s,%s,%s,%s,%s::jsonb,'running')""",(run_id,source,mode,replay,json.dumps(settings)))
    conn.execute("""UPDATE ops_input_controls SET mode=%s,run_id=%s,revision=revision+1,config_version_id=%s,
        updated_by_actor_id=%s,updated_at=clock_timestamp() WHERE source_id=%s""",(mode,run_id,chosen,actor,source))
    return {'sourceId':str(source),'mode':mode,'runId':str(run_id) if run_id else None,'revision':expected_revision+1,
        'configVersionId':str(chosen) if chosen else None}


def admit(conn,source,run_id,mode,layer,raw,*,namespace,position=None,config=None):
    """Called with the control locked. Arrival revisions are not source update versions."""
    parts=identity(layer,raw);raw_hash=digest(raw)
    execution=None;revision=None
    if parts:
        key=json.dumps([namespace,layer,parts],ensure_ascii=False,separators=(',',':'))
        execution=uuid.uuid5(uuid.NAMESPACE_URL,f'input-execution:{source}:{key}')
        conn.execute("""INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
            VALUES(%s,%s,%s,%s,%s::jsonb,'arrival-snapshot-v1',clock_timestamp()) ON CONFLICT DO NOTHING""",
            (execution,source,layer,key,json.dumps({'namespace':namespace,'identity':parts,'revisionBasis':'local_arrival'})))
        latest=conn.execute('SELECT id,revision_no,content_hash FROM data_observations WHERE execution_id=%s ORDER BY revision_no DESC LIMIT 1',(execution,)).fetchone()
        if latest and latest[2]==raw_hash:return {'created':False,'identified':True}
        revision=(latest[1] if latest else 0)+1
        dedup=f'input:{execution}:{revision}'
    else:dedup=f'input-unidentified:{source}:{namespace}:{layer}:{raw_hash}'
    if layer=='B':
        # BODY reference only. It is never misclassified as an M monitoring row.
        oid=uuid.uuid4()
        inserted=conn.execute("""INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
            api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
            VALUES(%s,%s,'B',%s,%s,%s,%s,clock_timestamp(),clock_timestamp(),'unknown','{}',%s::jsonb,
            '["body_values_not_collected"]','[]') ON CONFLICT DO NOTHING RETURNING id""",
            (oid,source,execution,revision,dedup,raw_hash,json.dumps(raw))).fetchone()
        if not inserted:return {'created':False,'identified':parts is not None}
        conn.execute("""INSERT INTO data_message_bodies(observation_id,source_id,message_id,collection_status,field_states,
            representation,access_policy_version,retention_policy_version)
            VALUES(%s,%s,%s,'not_collected','{"message_in":"unknown","message_out":"unknown","message_body":"unknown"}',
            'unknown','metadata-only-v1','metadata-history-v1')""",(oid,source,raw['message_id']))
    else:
        saved=ingest_observation(conn,source_id=source,layer=layer,dedup_key=dedup,raw=raw,execution_id=execution,revision_no=revision)
        oid=saved.observation_id
        if not saved.created:return {'created':False,'identified':parts is not None}
    conn.execute('INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace) VALUES(%s,%s,%s,%s,%s)',
        (oid,run_id,mode,position,namespace))
    if layer=='P' and execution:
        enqueue_observation(conn,oid,config_id=config)
    return {'created':True,'identified':parts is not None,'observationId':str(oid)}
