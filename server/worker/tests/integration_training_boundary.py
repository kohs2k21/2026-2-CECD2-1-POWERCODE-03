"""폐지 종류의 남은 작업 회귀 시험: 옛 training 큐의 `recommend_feature`는 실행 없이 `job_kind_abolished`로 끝난다.

재시도 없는 failed, attempt 1개, 스냅샷·LLM 결과 없음, 큐 메시지는 done(llm-recommendation-job.md "기존 추천과의 관계").
"""

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
            message_id = enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(job_id),
                                                    "enqueuedAt": now.isoformat()})
        conn.commit()
    run(once=True,role="llm")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, error_code, attempt_count, result_summary FROM run_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            assert row == ("failed", "job_kind_abolished", 1, None), row
            cur.execute("SELECT status, error_code FROM run_job_attempts WHERE job_id = %s", (job_id,))
            assert cur.fetchall() == [("failed", "job_kind_abolished")]
            cur.execute("SELECT count(*) FROM run_job_snapshots WHERE job_id = %s", (job_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM data_snapshots WHERE source_id = %s", (source_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM run_llm_results WHERE job_id = %s", (job_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT status FROM powercode_queue.training_jobs WHERE id = %s", (message_id,))
            assert cur.fetchone()[0] == "done"
    print("training boundary integration passed: abolished recommend_feature ends as job_kind_abolished without snapshot")


if __name__ == "__main__":
    main()
