"""격리된 PostgreSQL에서 T·P·M 저장과 PROCESS 큐 등록을 검증한다."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.storage import ingest_observation
from detector.input_reader import load_process_observation
from pg_queue import acknowledge, claim


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("격리된 시험 DB의 DATABASE_URL이 필요하다")
    source_id = uuid.uuid4()
    test_run_id = str(uuid.uuid4())
    executions = {layer: uuid.uuid4() for layer in ("T", "P", "M")}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops_sources
                   (id, dataset_key, display_name, ingestion_method, schema_version,
                    clock_basis, field_catalog, created_at)
                   VALUES (%s, %s, 'Integration test', 'synthetic-test', 'test-v1',
                           '{}'::jsonb, '{}'::jsonb, clock_timestamp())""",
                (source_id, f"storage-test-{source_id}"),
            )
            for layer, execution_id in executions.items():
                cur.execute(
                    """INSERT INTO data_executions
                       (id, source_id, layer, execution_key, key_parts,
                        key_contract_version, first_observed_at)
                       VALUES (%s, %s, %s, %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                    (execution_id, source_id, layer, f"test-{layer}-{source_id}"),
                )
        conn.commit()

        cases = {
            "T": {"transaction_id": "tx-1", "BODY": "must-not-persist"},
            "P": {"transaction_id": "tx-1", "process_id": "proc-1",
                  "retry_count": "not-a-number", "Authorization": "must-not-persist"},
            "M": {"transaction_id": "tx-1", "process_id": "proc-1",
                  "message_id": "msg-1", "messageBody": "must-not-persist"},
        }
        results = {}
        for layer, raw in cases.items():
            results[layer] = ingest_observation(
                conn, source_id=source_id, layer=layer,
                dedup_key=f"test-v1:{layer}:{source_id}", raw=raw,
                execution_id=executions[layer], revision_no=1,
                detection_eligible=(layer == "P"),
                benchmark_run_id=test_run_id if layer == "P" else None,
            )
            conn.commit()

        assert all(result.created for result in results.values())
        assert results["P"].queued_for_detection
        assert not results["T"].queued_for_detection and not results["M"].queued_for_detection
        with conn.cursor() as cur:
            cur.execute(
                """SELECT layer, allowed_values, quality_flags FROM data_observations
                     WHERE source_id = %s ORDER BY layer""",
                (source_id,),
            )
            rows = cur.fetchall()
            assert len(rows) == 3
            assert all("must-not-persist" not in str(row) for row in rows)
            assert any("invalid_numeric:retry_count" in row[2] for row in rows)
            cur.execute(
                """SELECT count(*) FROM powercode_queue.detection_logs
                     WHERE payload->>'observationId' = %s""",
                (str(results["P"].observation_id),),
            )
            assert cur.fetchone()[0] == 1
        conn.commit()

        duplicate = ingest_observation(
            conn, source_id=source_id, layer="P", dedup_key=f"test-v1:P:{source_id}",
            raw=cases["P"], execution_id=executions["P"], revision_no=1,
            detection_eligible=True,
            benchmark_run_id=test_run_id,
        )
        assert not duplicate.created and not duplicate.queued_for_detection
        try:
            ingest_observation(
                conn, source_id=source_id, layer="P", dedup_key=f"test-v1:P:{source_id}",
                raw={"process_id": "different"}, execution_id=executions["P"],
                revision_no=1, detection_eligible=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("다른 내용의 중복 키가 거부되지 않았다")
        conn.commit()

        rejected_key = f"test-v1:invalid:{source_id}"
        try:
            ingest_observation(
                conn, source_id=source_id, layer="P", dedup_key=rejected_key,
                raw={"process_id": "orphan"}, execution_id=uuid.uuid4(),
                revision_no=1, detection_eligible=True,
            )
        except psycopg.errors.ForeignKeyViolation:
            pass
        else:
            raise AssertionError("유효하지 않은 실행 ID가 거부되지 않았다")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM data_observations
                     WHERE source_id = %s AND dedup_key = %s""",
                (source_id, rejected_key),
            )
            assert cur.fetchone()[0] == 0
        conn.commit()

        message = claim(conn, "detection", benchmark_run_id=test_run_id)
        assert message is not None
        conn.commit()
        observation = load_process_observation(conn, message)
        assert observation.observation_id == results["P"].observation_id
        assert observation.allowed_values["process_id"] == "proc-1"
        assert acknowledge(conn, "detection", message)
        conn.commit()
    print("storage integration passed: T/P/M projection, atomic P enqueue, duplicate handling, detector read")


if __name__ == "__main__":
    main()
