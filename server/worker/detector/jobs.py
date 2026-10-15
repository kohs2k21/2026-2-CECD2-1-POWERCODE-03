"""Immutable input + job + durable queue committed with source observations."""
import hashlib
from datetime import timezone
import json
import uuid
from pg_queue import enqueue
from feature_worker import active_config


def input_config(conn, source_id):
    if conn.execute("SELECT to_regclass('public.ops_input_controls')").fetchone()[0]:
        row=conn.execute('SELECT config_version_id FROM ops_input_controls WHERE source_id=%s',(source_id,)).fetchone()
        if row and row[0]:return row[0]
    return active_config(conn)


def claim_waiting_input(conn):
    if not conn.execute("SELECT to_regclass('public.ops_input_controls')").fetchone()[0]:return None
    row=conn.execute("""SELECT q.id FROM powercode_queue.detection_logs q JOIN ops_input_controls c
        ON c.source_id::text=q.payload->>'sourceId' WHERE c.config_version_id IS NOT NULL
        AND NOT (q.payload ? 'jobId') AND (q.status='pending' AND q.available_at<=clock_timestamp()
          OR q.status='leased' AND q.lease_until<=clock_timestamp())
        ORDER BY q.available_at,q.created_at LIMIT 1""").fetchone()
    if row:
        from pg_queue import claim
        return claim(conn,'detection',message_id=row[0])
    return None


def enqueue_observation(conn, observation_id, *, config_id=None, benchmark_run_id=None):
    with conn.cursor() as cur:
        cur.execute("SELECT source_id,execution_id,content_hash,persisted_at FROM data_observations WHERE id=%s AND layer='P'", (observation_id,))
        observation = cur.fetchone()
        if observation is None or observation[1] is None:
            raise ValueError("detect_input_unavailable")
        source, execution, content_hash, as_of = observation
        config_id = config_id or input_config(conn, source)
        if config_id is not None:
            cur.execute("SELECT source_id FROM ml_config_versions WHERE id=%s", (config_id,))
            config = cur.fetchone()
            if config is None or config[0] != source:
                config_id = None
        if config_id is None:
            # Recovery binds a config only when this source has an approved active config.
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"waiting-config:{observation_id}")
            payload = {"schemaVersion": 1, "eventId": str(event_id), "sourceId": str(source),
                       "observationId": str(observation_id)}
        else:
            manifest_hash = hashlib.sha256(json.dumps(["process-input-v1", str(source), str(observation_id),
                str(execution), content_hash, as_of.astimezone(timezone.utc).isoformat()], separators=(",", ":")).encode()).hexdigest()
            manifest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"manifest:{manifest_hash}")
            cur.execute("""INSERT INTO run_input_manifests(id,source_id,execution_id,process_observation_id,
                message_observation_refs,readiness,input_as_of,contract_version,content_hash,created_at)
                VALUES(%s,%s,%s,%s,'[]'::jsonb,%s::jsonb,%s,'v03',%s,clock_timestamp()) ON CONFLICT DO NOTHING""",
                (manifest_id,source,execution,observation_id,json.dumps({"process": "ready", "transaction": "unknown", "messages": "unknown", "body": "not_collected"}),as_of,manifest_hash))
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"detect:{manifest_id}:{config_id}")
            payload = {"schemaVersion": 1, "eventId": str(event_id), "jobId": str(event_id),
                       "sourceId": str(source), "observationId": str(observation_id),
                       "configVersionId": str(config_id), "inputManifestHash": manifest_hash,
                       "inputManifestId": str(manifest_id)}
            if conn.execute("SELECT to_regclass('public.data_input_origins')").fetchone()[0]:
                origin=conn.execute('SELECT mode,run_id FROM data_input_origins WHERE observation_id=%s',(observation_id,)).fetchone()
                if origin:
                    payload['inputOrigin']={'mode':origin[0],'runId':str(origin[1]),
                        'configurationScope':'approved-active' if config_id==active_config(conn) else 'input-validation'}
            cur.execute("""INSERT INTO run_jobs(id,kind,status,request_hash,input_spec,input_config_version_id,
                input_manifest_id,contract_version,queue,producer_kind,producer_key,idempotency_key,
                max_attempts,attempt_count,next_run_at,created_at)
                VALUES(%s,'detect','queued',%s,%s::jsonb,%s,%s,'v03','detection','system',%s,%s,3,0,
                clock_timestamp(),clock_timestamp()) ON CONFLICT DO NOTHING""",
                (event_id,manifest_hash,json.dumps(payload),config_id,manifest_id,str(source),str(event_id)))
    if benchmark_run_id:
        payload["benchmarkRunId"] = benchmark_run_id
    enqueue(conn, "detection", payload, message_id=event_id)
    return payload
