"""Shared logical source, frozen CSV stream and append-only observation admission."""
import json
import uuid
from pathlib import Path
from collector.input_contract import csv_records,digest,identity
from collector import source_clock
from collector.storage import NUMERIC_FIELDS,TABLES,TEXT_FIELDS,check_received_at,prepare_projection
from detector.jobs import enqueue_observations

# Replay order inside one transaction group. The source writes the TRANSACTION row only after
# all of its PROCESS rows, and Detector treats T arrival as "transaction complete", so T goes last.
# B follows M because a body reference is grouped through its M row's message_id.
REPLAY_LAYER_RANK={'P':0,'M':1,'B':2,'T':3}


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
    # Files are read T first so group order stays the T file's transaction order.
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
            rows.append((groups[group],REPLAY_LAYER_RANK[layer],index,layer,raw))
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
        found=conn.execute("SELECT configuration_spec->'schemaVersion' FROM ml_config_versions WHERE id=%s AND source_id=%s",(chosen,source)).fetchone()
        if not found:raise ValueError('input_config_source_mismatch')
        # The effective config (explicit or kept) that will run must be a legacy single config (JSON number 1|2); v3 runs
        # only through the active model set. Same rule as the API: only a pause that keeps the existing pin is not checked.
        if not (mode=='paused' and config=='keep') and (type(found[0]) not in (int,float) or found[0] not in (1,2)):
            raise ValueError('config_schema_not_supported_yet')
    if row[1]:conn.execute("UPDATE ops_input_runs SET status='paused' WHERE id=%s AND status='running'",(row[1],))
    run_id=row[1]
    if mode!='paused':
        settings=settings or {}
        if mode=='api':
            interval=settings.get('pollSeconds',1)
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


INGEST_PHASES=('live','initial','replay')
BODY_QUALITY_FLAGS='["body_values_not_collected"]'
SAME_DEDUP_CONFLICT='같은 dedup_key에 다른 내용·실행 식별·revision이 있다'


def admit(conn,source,run_id,mode,layer,raw,*,namespace,position=None,config=None,phase=None,received_at=None):
    """One record through the shared batch path (`admit_batch`)."""
    return admit_batch(conn,source,run_id,mode,[(layer,raw)],namespace=namespace,position=position,config=config,
        phase=phase,received_at=received_at)[0]


# Same text as json.dumps(value, ensure_ascii=False, separators=(',',':')), one reused encoder.
_KEY_JSON=json.JSONEncoder(ensure_ascii=False,separators=(',',':'))


def plan_records(source,namespace,records):
    """Pure first pass: identity, arrival hash and execution key of every record, in input order."""
    plans=[]
    for layer,raw in records:
        parts=identity(layer,raw)
        plan={'layer':layer,'raw':raw,'parts':parts,'rawHash':digest(raw),'execution':None}
        if parts:
            key=_KEY_JSON.encode([namespace,layer,parts])
            plan.update(execution=uuid.uuid5(uuid.NAMESPACE_URL,f'input-execution:{source}:{key}'),executionKey=key,
                keyParts=json.dumps({'namespace':namespace,'identity':parts,'revisionBasis':'local_arrival'}))
        plans.append(plan)
    return plans


def assign_revisions(plans,latest,*,source,namespace,stored_hash):
    """Pure second pass, in input order: what admitting the records one at a time would decide.

    latest: {execution: (revision_no, content_hash)} of the newest stored observation before this batch.
    stored_hash(plan): the content_hash the row would be stored with. A row is a duplicate when the newest
    revision of its execution (including rows planned earlier in this batch) has its arrival hash; otherwise it gets
    the next revision. Unidentified rows use the content-hash dedup key; a repeat inside the batch is not inserted twice.
    Sets plan['action'] to 'duplicate' | 'repeat' | 'insert' and plan['revision'], plan['dedup'], plan['contentHash'].
    """
    state=dict(latest);seen={}
    for plan in plans:
        execution=plan['execution']
        if execution:
            newest=state.get(execution)
            if newest and newest[1]==plan['rawHash']:
                plan['action']='duplicate';continue
            plan['revision']=(newest[0] if newest else 0)+1
            plan['dedup']=f'input:{execution}:{plan["revision"]}'
        else:
            plan['revision']=None
            plan['dedup']=f'input-unidentified:{source}:{namespace}:{plan["layer"]}:{plan["rawHash"]}'
        plan['contentHash']=stored_hash(plan)
        key=(plan['layer'],plan['dedup'])
        if key in seen:
            if plan['layer']!='B' and seen[key]!=(plan['contentHash'],execution,plan['revision']):raise ValueError(SAME_DEDUP_CONFLICT)
            plan['action']='repeat';continue
        seen[key]=(plan['contentHash'],execution,plan['revision'])
        plan['action']='insert'
        if execution:state[execution]=(plan['revision'],plan['contentHash'])
    return plans


def _project(plan,basis):
    """Stored column values. B is a body reference only (absent time, raw JSON); T/P/M use the storage projection."""
    if plan['layer']=='B':
        no_time=source_clock.absent()
        plan.update(values=json.dumps(plan['raw']),flags=BODY_QUALITY_FLAGS,missing='[]',time=no_time)
        return plan['rawHash']
    projected=prepare_projection(plan['layer'],plan['raw'],plan['execution'])
    plan.update(projection=projected,values=projected.allowed_json,flags=json.dumps(projected.flags),
        missing=json.dumps(projected.missing),time=source_clock.resolve(projected.allowed,basis))
    return projected.content_hash


# Array parameters below are sent in binary format (%b): the same element values and casts as text arrays, without
# psycopg quoting every element in Python. Numbers stay text (str(Decimal)) and are cast on the server, as before.
def _text(values):
    return [None if value is None else str(value) for value in values]


def admit_batch(conn,source,run_id,mode,records,*,namespace,position=None,config=None,phase=None,received_at=None):
    """Called with the control locked. Arrival revisions are not source update versions.

    Same outcomes as admitting `records` one by one in order (revisions, duplicates, dedup keys, unidentified rows,
    B references, source time, origins, detect jobs for new identified P), with a fixed number of statements per call.
    phase: data_input_origins.ingest_phase (csv='replay'; api='initial' for a run's first successful poll after
    start/resume, otherwise 'live'). received_at: API response receipt finished_at; CSV passes None (NULL).
    Returns one outcome per record: {'created','identified'} plus 'observationId' and 'detectJob' when created.
    """
    records=list(records)
    if not records:return []
    if phase not in INGEST_PHASES or (phase=='replay')!=(mode=='csv'):raise ValueError('invalid_ingest_phase')
    if (received_at is None)!=(mode=='csv'):raise ValueError('api_received_at_mode_mismatch')
    plans=plan_records(source,namespace,records)
    basis=None
    if any(plan['layer']!='B' for plan in plans):
        check_received_at(received_at)
        # Operator `clock-basis verify` applies from the next admission transaction (contracts/collector-metrics.md 1.1).
        row=conn.execute('SELECT clock_basis FROM ops_sources WHERE id=%s',(source,)).fetchone()
        basis=row[0] if row else None
    executions={}
    for plan in plans:
        if plan['execution'] and plan['execution'] not in executions:executions[plan['execution']]=plan
    latest={}
    if executions:
        chosen=list(executions.values())
        conn.execute("""INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
            SELECT u.id,%s,u.layer::source_layer,u.execution_key,u.key_parts::jsonb,'arrival-snapshot-v1',clock_timestamp()
            FROM unnest(%b::uuid[],%b::text[],%b::text[],%b::text[]) AS u(id,layer,execution_key,key_parts) ON CONFLICT DO NOTHING""",
            (source,_text(executions),[p['layer'] for p in chosen],[p['executionKey'] for p in chosen],[p['keyParts'] for p in chosen]))
        rows=conn.execute("""SELECT e.id,l.revision_no,l.content_hash FROM unnest(%b::uuid[]) AS e(id)
            CROSS JOIN LATERAL (SELECT o.revision_no,o.content_hash FROM data_observations o WHERE o.execution_id=e.id
              ORDER BY o.revision_no DESC LIMIT 1) l""",(_text(executions),)).fetchall()
        latest={row[0]:(row[1],row[2]) for row in rows}
    assign_revisions(plans,latest,source=source,namespace=namespace,stored_hash=lambda plan:_project(plan,basis))
    planned=[plan for plan in plans if plan['action']=='insert']
    for plan in planned:plan['id']=uuid.uuid4()
    inserted=set()
    if planned:
        times=[plan['time'] for plan in planned]
        rows=conn.execute("""INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
            source_revision_token,source_log_time_raw,source_log_occurred_at,api_received_at,persisted_at,clock_status,
            parsed_times,allowed_values,quality_flags,missing_fields)
            SELECT u.id,%s,u.layer::source_layer,u.execution_id,u.revision_no,u.dedup_key,u.content_hash,NULL,u.time_raw,
              u.occurred_at::timestamptz,%s::timestamptz,clock_timestamp(),u.clock_status,u.parsed_times::jsonb,
              u.allowed_values::jsonb,u.quality_flags::jsonb,u.missing_fields::jsonb
            FROM unnest(%b::uuid[],%b::text[],%b::uuid[],%b::bigint[],%b::text[],%b::text[],%b::text[],%b::text[],%b::text[],
              %b::text[],%b::text[],%b::text[],%b::text[])
              AS u(id,layer,execution_id,revision_no,dedup_key,content_hash,time_raw,occurred_at,clock_status,parsed_times,
                   allowed_values,quality_flags,missing_fields)
            ON CONFLICT DO NOTHING RETURNING id""",
            (source,received_at,_text(p['id'] for p in planned),[p['layer'] for p in planned],_text(p['execution'] for p in planned),
             _text(p['revision'] for p in planned),[p['dedup'] for p in planned],[p['contentHash'] for p in planned],
             [t.raw for t in times],[None if t.occurred_at is None else t.occurred_at.isoformat() for t in times],
             [t.clock_status for t in times],[t.parsed_json() for t in times],[p['values'] for p in planned],
             [p['flags'] for p in planned],[p['missing'] for p in planned])).fetchall()
        inserted={row[0] for row in rows}
    check=[]
    for plan in planned:
        if plan['id'] in inserted:continue
        # A planned revision is the next one under the control lock, so it cannot already exist.
        if plan['execution']:raise ValueError('observation_revision_conflict')
        plan['action']='existing'
        if plan['layer']!='B':check.append(plan)
    if check:
        rows=conn.execute("""SELECT o.layer::text,o.dedup_key,o.content_hash,o.execution_id,o.revision_no,o.source_revision_token
            FROM data_observations o JOIN unnest(%b::text[],%b::text[]) AS u(layer,dedup_key)
              ON o.source_id=%s AND o.layer::text=u.layer AND o.dedup_key=u.dedup_key""",
            ([p['layer'] for p in check],[p['dedup'] for p in check],source)).fetchall()
        existing={(row[0],row[1]):row[2:] for row in rows}
        for plan in check:
            if existing.get((plan['layer'],plan['dedup']))!=(plan['contentHash'],None,None,None):raise ValueError(SAME_DEDUP_CONFLICT)
    created=[plan for plan in planned if plan['id'] in inserted]
    for layer,table in TABLES.items():
        subset=[plan for plan in created if plan['layer']==layer]
        if not subset:continue
        columns=TEXT_FIELDS[layer]+NUMERIC_FIELDS[layer]
        # Numbers go as text and are cast on the server, exactly as the text array form did (numeric input of str(Decimal)).
        casts=['%b::uuid[]']+['%b::text[]']*len(TEXT_FIELDS[layer])+['%b::text[]::numeric[]']*len(NUMERIC_FIELDS[layer])
        conn.execute(f"INSERT INTO {table} (observation_id, {', '.join(columns)}) SELECT * FROM unnest({', '.join(casts)})",
            (_text(p['id'] for p in subset),*([p['projection'].typed[field] for p in subset] for field in TEXT_FIELDS[layer]),
             *(_text(p['projection'].typed[field] for p in subset) for field in NUMERIC_FIELDS[layer])))
    bodies=[plan for plan in created if plan['layer']=='B']
    if bodies:
        conn.execute("""INSERT INTO data_message_bodies(observation_id,source_id,message_id,collection_status,field_states,
            representation,access_policy_version,retention_policy_version)
            SELECT u.id,%s,u.message_id,'not_collected'::body_collection_status,
              '{"message_in":"unknown","message_out":"unknown","message_body":"unknown"}'::jsonb,
              'unknown'::body_representation,'metadata-only-v1','metadata-history-v1'
            FROM unnest(%b::uuid[],%b::text[]) AS u(id,message_id)""",
            (source,_text(p['id'] for p in bodies),[p['raw']['message_id'] for p in bodies]))
    if created:
        conn.execute("""INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace,ingest_phase)
            SELECT u.id,%s,%s,%s::bigint,%s,%s FROM unnest(%b::uuid[]) AS u(id)""",
            (run_id,mode,position,namespace,phase,_text(p['id'] for p in created)))
    detect=[plan for plan in created if plan['layer']=='P' and plan['execution']]
    for plan,payload in zip(detect,enqueue_observations(conn,[plan['id'] for plan in detect],config_id=config) if detect else []):
        # Only a payload with jobId is a registered detect job; waiting-config items are left to recovery polling.
        if payload.get('jobId'):
            plan['detectJob']={'jobId':payload['jobId'],'inputManifestHash':payload['inputManifestHash']}
    outcomes=[]
    for plan in plans:
        outcome={'created':plan['action']=='insert','identified':plan['parts'] is not None}
        if outcome['created']:
            outcome['observationId']=str(plan['id'])
            if 'detectJob' in plan:outcome['detectJob']=plan['detectJob']
        outcomes.append(outcome)
    return outcomes
