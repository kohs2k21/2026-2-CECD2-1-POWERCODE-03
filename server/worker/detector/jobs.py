"""Immutable input + job + durable queue committed with source observations."""
import hashlib
from datetime import timezone
import json
import os
import uuid
from pg_queue import enqueue, enqueue_many
from feature_worker import active_config

JOB_CONTRACTS = ('v03', 'v04')
# 같은 관측을 두 작업 생성 주체가 동시에 넣을 때 v03·v04 작업이 함께 생기지 않게 manifest별로 줄을 세운다.
MANIFEST_LOCK_CLASS = 81740


def job_contract_setting():
    """새 detect 작업의 계약 버전. 기본은 v03이고 전환 절차는 README에 있다."""
    value = os.environ.get('DETECT_JOB_CONTRACT', 'v03')
    if value not in JOB_CONTRACTS:
        raise ValueError('invalid_detect_job_contract')
    return value


def _controlled_config(conn, source_id):
    if conn.execute("SELECT to_regclass('public.ops_input_controls')").fetchone()[0]:
        row=conn.execute('SELECT config_version_id FROM ops_input_controls WHERE source_id=%s',(source_id,)).fetchone()
        if row and row[0]:return row[0]
    return None


def input_config(conn, source_id):
    return _controlled_config(conn, source_id) or active_config(conn)


def detect_contract(conn, source_id, config_id=None):
    """승인 운영 경로만 v04로 간다. 승인 구성이 아닌 구성을 고정한 입력 검증·데모는 그 구성을 써야 하므로 v03이다."""
    if job_contract_setting() == 'v03':
        return 'v03'
    pinned = config_id or _controlled_config(conn, source_id)
    if pinned is None:
        return 'v04'
    active = active_config(conn)
    return 'v04' if active is not None and str(pinned) == str(active) else 'v03'


def claim_waiting_input(conn):
    if job_contract_setting() == 'v04':
        # v04는 구성 없이 판정하므로 구성을 기다리던 입력 전부가 대상이다.
        row=conn.execute("""SELECT q.id FROM powercode_queue.detection_logs q
            WHERE NOT (q.payload ? 'jobId') AND (q.status='pending' AND q.available_at<=clock_timestamp()
              OR q.status='leased' AND q.lease_until<=clock_timestamp())
            ORDER BY q.available_at,q.created_at LIMIT 1""").fetchone()
        if row:
            from pg_queue import claim
            return claim(conn,'detection',message_id=row[0])
        return None
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


def _input_origins(conn, observation_ids):
    """입력 출처 일괄 조회 {observation_id(UUID): (mode, run_id)}. 출처 표가 없거나 조회할 관측이 없으면 {}."""
    if not observation_ids or not conn.execute("SELECT to_regclass('public.data_input_origins')").fetchone()[0]:
        return {}
    rows=conn.execute('SELECT observation_id,mode,run_id FROM data_input_origins WHERE observation_id = ANY(%b::uuid[])',
        ([str(value) for value in observation_ids],)).fetchall()
    return {row[0]:(row[1],row[2]) for row in rows}


def _input_origin(conn, observation_id, approved):
    """입력 출처(data_input_origins). approved는 출처가 있을 때만 부르는 함수(활성 구성 조회를 그때만 한다)."""
    if not conn.execute("SELECT to_regclass('public.data_input_origins')").fetchone()[0]:
        return None
    origin=conn.execute('SELECT mode,run_id FROM data_input_origins WHERE observation_id=%s',(observation_id,)).fetchone()
    if not origin:
        return None
    return {'mode':origin[0],'runId':str(origin[1]),'configurationScope':'approved-active' if approved() else 'input-validation'}


def enqueue_observation(conn, observation_id, *, config_id=None, benchmark_run_id=None):
    return enqueue_observations(conn, [observation_id], config_id=config_id, benchmark_run_id=benchmark_run_id)[0]


def enqueue_observations(conn, observation_ids, *, config_id=None, benchmark_run_id=None):
    """`enqueue_observation`의 일괄 형태. 입력 순서대로 관측마다 payload 하나를 돌려준다.

    관측 하나씩 부를 때와 같은 행·payload를 만든다(계약·구성 결정, manifest, 잠금, 반대 계약 작업 재사용, 출처, 작업, 큐 메시지).
    문장 수만 관측 수와 무관하게 줄인다. 계약·구성은 원천마다 한 번 정한다(같은 호출자 트랜잭션 안).
    배열 인자는 binary 형식(%b)이다(값·형 변환은 text 배열과 같다).
    관측 하나라도 판정 입력이 아니면(P가 아니거나 실행 식별 없음) 아무것도 넣지 않고 detect_input_unavailable.
    """
    ids = list(observation_ids)
    if not ids:
        return []
    keys = [uuid.UUID(str(value)) for value in ids]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_detect_input")
    with conn.cursor() as cur:
        cur.execute("SELECT id,source_id,execution_id,content_hash,persisted_at FROM data_observations WHERE id = ANY(%b::uuid[]) AND layer='P'",
                    ([str(key) for key in keys],))
        found = {row[0]: row[1:] for row in cur.fetchall()}
        observations = [found.get(key) for key in keys]
        if any(observation is None or observation[1] is None for observation in observations):
            raise ValueError("detect_input_unavailable")
        decided = {}

        def decide(source):
            # 관측 하나씩 부를 때 매번 하던 결정과 같다. 호출자의 config_id는 원천마다 그대로 다시 쓴다.
            if source not in decided:
                contract = detect_contract(conn, source, config_id)
                chosen = None if contract == 'v04' else config_id or input_config(conn, source)
                if chosen is not None:
                    cur.execute("SELECT source_id FROM ml_config_versions WHERE id=%s", (chosen,))
                    config = cur.fetchone()
                    if config is None or config[0] != source:
                        chosen = None
                decided[source] = (contract, chosen)
            return decided[source]

        plans = []
        for observation_id, (source, execution, content_hash, as_of) in zip(ids, observations):
            contract, chosen = decide(source)
            if contract == 'v03' and chosen is None:
                # Recovery binds a config only when this source has an approved active config.
                event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"waiting-config:{observation_id}")
                plans.append({"eventId": event_id, "payload": {"schemaVersion": 1, "eventId": str(event_id), "sourceId": str(source),
                                                               "observationId": str(observation_id)}})
                continue
            manifest_hash = hashlib.sha256(json.dumps(["process-input-v1", str(source), str(observation_id),
                str(execution), content_hash, as_of.astimezone(timezone.utc).isoformat()], separators=(",", ":")).encode()).hexdigest()
            manifest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"manifest:{manifest_hash}")
            plans.append({"observationId": observation_id, "source": source, "execution": execution, "asOf": as_of,
                          "contract": contract, "config": chosen, "manifestHash": manifest_hash, "manifestId": manifest_id})
        made = [plan for plan in plans if "manifestId" in plan]
        if made:
            cur.execute("""INSERT INTO run_input_manifests(id,source_id,execution_id,process_observation_id,
                message_observation_refs,readiness,input_as_of,contract_version,content_hash,created_at)
                SELECT u.id,u.source_id,u.execution_id,u.observation_id,'[]'::jsonb,%s::jsonb,u.as_of,'v03',u.content_hash,clock_timestamp()
                FROM unnest(%b::uuid[],%b::uuid[],%b::uuid[],%b::uuid[],%b::timestamptz[],%b::text[])
                  AS u(id,source_id,execution_id,observation_id,as_of,content_hash)
                ON CONFLICT DO NOTHING""",
                (json.dumps({"process": "ready", "transaction": "unknown", "messages": "unknown", "body": "not_collected"}),
                 [str(p["manifestId"]) for p in made], [str(p["source"]) for p in made], [str(p["execution"]) for p in made],
                 [str(p["observationId"]) for p in made], [p["asOf"] for p in made], [p["manifestHash"] for p in made]))
            # Same per-manifest lock as one call at a time, taken in input order.
            cur.execute("SELECT pg_advisory_xact_lock(%s,hashtext(m.id)) FROM unnest(%b::text[]) WITH ORDINALITY AS m(id,n) ORDER BY m.n",
                        (MANIFEST_LOCK_CLASS, [str(p["manifestId"]) for p in made]))
            cur.execute("""SELECT input_manifest_id,contract_version,id,input_spec FROM run_jobs WHERE kind='detect'
                AND input_manifest_id = ANY(%b::uuid[])""", ([str(p["manifestId"]) for p in made],))
            jobs = {}
            for manifest_id, version, job_id, spec in cur.fetchall():
                jobs.setdefault((manifest_id, version), (job_id, spec))
            new = []
            for plan in made:
                existing = jobs.get((plan["manifestId"], 'v04' if plan["contract"] == 'v03' else 'v03'))
                if existing:
                    # 전환 전후로 같은 입력이 다시 들어와도 판정은 먼저 만든 작업 하나다.
                    plan["eventId"], plan["payload"] = existing
                    continue
                event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"detect:{plan['manifestId']}:{plan['config'] or plan['contract']}")
                payload = {"schemaVersion": 1, "eventId": str(event_id), "jobId": str(event_id),
                           "sourceId": str(plan["source"]), "observationId": str(plan["observationId"]),
                           "inputManifestHash": plan["manifestHash"], "inputManifestId": str(plan["manifestId"])}
                if plan["contract"] == 'v03':
                    payload["configVersionId"] = str(plan["config"])
                plan["eventId"], plan["payload"] = event_id, payload
                new.append(plan)
            origins = _input_origins(conn, [plan["observationId"] for plan in new])
            approved = {}
            for plan in new:
                origin = origins.get(uuid.UUID(str(plan["observationId"])))
                if origin:
                    if plan["source"] not in approved:
                        approved[plan["source"]] = plan["contract"] == 'v04' or plan["config"] == active_config(conn)
                    plan["payload"]['inputOrigin'] = {'mode': origin[0], 'runId': str(origin[1]),
                        'configurationScope': 'approved-active' if approved[plan["source"]] else 'input-validation'}
            if new:
                cur.execute("""INSERT INTO run_jobs(id,kind,status,request_hash,input_spec,input_config_version_id,
                    input_manifest_id,contract_version,queue,producer_kind,producer_key,idempotency_key,
                    max_attempts,attempt_count,next_run_at,created_at)
                    SELECT u.id,'detect','queued',u.request_hash,u.input_spec::jsonb,u.config_id,u.manifest_id,u.contract,
                      'detection','system',u.producer_key,u.idempotency_key,3,0,clock_timestamp(),clock_timestamp()
                    FROM unnest(%b::uuid[],%b::text[],%b::text[],%b::uuid[],%b::uuid[],%b::text[],%b::text[],%b::text[])
                      AS u(id,request_hash,input_spec,config_id,manifest_id,contract,producer_key,idempotency_key)
                    ON CONFLICT DO NOTHING""",
                    ([str(p["eventId"]) for p in new], [p["manifestHash"] for p in new], [json.dumps(p["payload"]) for p in new],
                     [None if p["config"] is None else str(p["config"]) for p in new], [str(p["manifestId"]) for p in new],
                     [p["contract"] for p in new], [str(p["source"]) for p in new], [str(p["eventId"]) for p in new]))
    payloads = [plan["payload"] for plan in plans]
    if benchmark_run_id:
        for payload in payloads:
            payload["benchmarkRunId"] = benchmark_run_id
    enqueue_many(conn, "detection", [(plan["payload"], plan["eventId"]) for plan in plans])
    return payloads


# 재판정(relation-rules.md 6.3): manifest hash 입력에 관련 행을 넣지 않는다. 1회 키 = PROCESS 관측(같은 관측이면 hash가 같아
# run_input_manifests UNIQUE(source_id, content_hash)가 DB에서 1회를 강제). 첫 판정 process-input-v1 입력은 그대로다.
RECHECK_MANIFEST = "process-input-v2-recheck"
RECHECK_KEY = "recheck:1"


def recheck_manifest_hash(source_id, observation_id, revision_no) -> str:
    """["process-input-v2-recheck", source, observation, revision_no, "recheck:1"]. revision_no는 JSON 정수."""
    if type(revision_no) is not int:
        raise ValueError("detect_input_unavailable")
    return hashlib.sha256(json.dumps([RECHECK_MANIFEST, str(source_id), str(observation_id), revision_no, RECHECK_KEY],
                                     separators=(",", ":")).encode()).hexdigest()


def recheck_manifest_exists(conn, source_id, content_hash) -> bool:
    return conn.execute("SELECT 1 FROM run_input_manifests WHERE source_id=%s AND content_hash=%s",
                        (source_id, content_hash)).fetchone() is not None


def enqueue_recheck(conn, observation_id, rechecked_decision_id, transaction_rows):
    """재판정 v04 작업 1개(relation-rules.md 6.3). 같은 관측의 재판정 manifest가 이미 있으면 아무것도 넣지 않고 None.

    - manifest: message_observation_refs [], transaction_observation_id NULL(검증된 P→T FK가 없음), contract_version 'v03'
      (manifest 표 CHECK), input_as_of = 작업 생성 시각(DB 시계), readiness.relation = {recheck: 1, recheckedDecisionId,
      rows: [{layer, observation_id, revision_no}]}(계기 거래 행, snake_case). DB는 readiness 안을 검사하지 않으므로
      재판정 시도가 시작 때 직접 확인한다(attempt_v04 `recheck_reference`).
    - 작업: 항상 v04(관계 규칙·재판정은 v04 판정에만 있다). 옛 backlog 연결 경로(runner `jobId` 없음)는 쓰지 않는다.
    - 호출자 트랜잭션 안에서 넣는다(커밋은 호출자). 잠금 순서는 호출자 81742 → 여기 81740.
    """
    rows = [{"layer": "T", "observation_id": str(row["observation_id"]), "revision_no": row["revision_no"]}
            for row in transaction_rows]
    if not rows:
        raise ValueError("recheck_transaction_rows_required")
    with conn.cursor() as cur:
        cur.execute("SELECT source_id,execution_id,revision_no FROM data_observations WHERE id=%s AND layer='P'",
                    (observation_id,))
        observation = cur.fetchone()
        if observation is None or observation[1] is None:
            raise ValueError("detect_input_unavailable")
        source, execution, revision = observation
        manifest_hash = recheck_manifest_hash(source, observation_id, revision)
        manifest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"manifest:{manifest_hash}")
        readiness = {"process": "ready", "transaction": "unknown", "messages": "unknown", "body": "not_collected",
                     "relation": {"recheck": 1, "recheckedDecisionId": str(rechecked_decision_id), "rows": rows}}
        cur.execute("""INSERT INTO run_input_manifests(id,source_id,execution_id,process_observation_id,
            message_observation_refs,readiness,input_as_of,contract_version,content_hash,created_at)
            VALUES(%s,%s,%s,%s,'[]'::jsonb,%s::jsonb,clock_timestamp(),'v03',%s,clock_timestamp())
            ON CONFLICT DO NOTHING RETURNING id""",
            (manifest_id,source,execution,observation_id,json.dumps(readiness),manifest_hash))
        if cur.fetchone() is None:
            return None
        cur.execute("SELECT pg_advisory_xact_lock(%s,hashtext(%s))", (MANIFEST_LOCK_CLASS, str(manifest_id)))
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"detect:{manifest_id}:v04")
        payload = {"schemaVersion": 1, "eventId": str(event_id), "jobId": str(event_id), "sourceId": str(source),
                   "observationId": str(observation_id), "inputManifestHash": manifest_hash,
                   "inputManifestId": str(manifest_id)}
        origin = _input_origin(conn, observation_id, lambda: True)
        if origin:
            payload["inputOrigin"] = origin
        cur.execute("""INSERT INTO run_jobs(id,kind,status,request_hash,input_spec,input_config_version_id,
            input_manifest_id,contract_version,queue,producer_kind,producer_key,idempotency_key,
            max_attempts,attempt_count,next_run_at,created_at)
            VALUES(%s,'detect','queued',%s,%s::jsonb,NULL,%s,'v04','detection','system',%s,%s,3,0,
            clock_timestamp(),clock_timestamp())""",
            (event_id,manifest_hash,json.dumps(payload),manifest_id,str(source),str(event_id)))
    enqueue(conn, "detection", payload, message_id=event_id)
    return payload
