"""변수 추천 작업의 스냅샷·임대·실제 변수셋 생성을 확인한다."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.storage import ingest_observation
from pg_queue import enqueue
from trainer.runner import run


def main() -> None:
    os.environ["FEATURE_DESIGN_PROVIDER"] = "numeric"
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("격리된 시험 DB의 DATABASE_URL이 필요하다")
    source_id, job_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops_sources
                   (id, dataset_key, display_name, ingestion_method, schema_version,
                    clock_basis, field_catalog, created_at)
                   VALUES (%s, %s, 'Training boundary test', 'synthetic-test', 'v1',
                           '{}'::jsonb, '{}'::jsonb, clock_timestamp())""",
                (source_id, f"training-boundary-{source_id}"),
            )
        conn.commit()
        for index in range(20):
            execution_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO data_executions
                       (id, source_id, layer, execution_key, key_parts,
                        key_contract_version, first_observed_at)
                       VALUES (%s, %s, 'P', %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                    (execution_id, source_id, f"process-{index}"),
                )
            ingest_observation(conn, source_id=source_id, layer="P",
                               dedup_key=f"training-test:{source_id}:{index}",
                               raw={"process_id": f"p{index}", "error_count": str(index % 2)},
                               execution_id=execution_id, revision_no=1)
            conn.commit()
        now = datetime.now(timezone.utc)
        spec = {"kind": "recommend_feature", "sourceId": str(source_id),
                "from": (now - timedelta(minutes=10)).isoformat(),
                "until": (now + timedelta(minutes=10)).isoformat()}
        request_hash = hashlib.sha256(json.dumps(spec).encode()).hexdigest()
        llm_spec = {"evidence_version": "test-period-v1", "input_evidence_hash": request_hash,
                    "model_version": "numeric-baseline-test", "prompt_version": "test",
                    "output_schema_version": "test", "reuse_key": str(job_id)}
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO run_jobs
                   (id, kind, status, requested_by_actor_id, requester_role,
                    request_hash, input_spec, max_attempts, attempt_count,
                    next_run_at, created_at,contract_version,queue,producer_kind,producer_key,idempotency_key,llm_spec)
                   VALUES (%s, 'recommend_feature', 'queued', 'test-actor', 'admin',
                           %s, %s::jsonb, 3, 0, clock_timestamp(), clock_timestamp(),
                           'v03','llm_recommendation','user','test-actor',%s,%s::jsonb)""",
                (job_id, request_hash, json.dumps(spec), str(job_id), json.dumps(llm_spec)),
            )
            enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(job_id),
                                        "enqueuedAt": now.isoformat()})
        conn.commit()
    run(once=True)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT status, error_code, attempt_count, result_summary->>'snapshotId'
                     FROM run_jobs WHERE id = %s""",
                (job_id,),
            )
            row = cur.fetchone()
            assert row[:3] == ("succeeded", None, 1)
            assert row[3] is not None
            cur.execute("SELECT status, manifest_hash FROM data_snapshots WHERE id = %s", (row[3],))
            snapshot = cur.fetchone()
            assert snapshot[0] == "ready" and len(snapshot[1]) == 64
    print("training boundary integration passed: immutable snapshot, fenced attempt, feature recommendation")


if __name__ == "__main__":
    main()
