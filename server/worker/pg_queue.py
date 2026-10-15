"""서로 다른 테이블을 사용하는 PostgreSQL 기반 최소 1회 전달 큐."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

QueueName = Literal["feature", "detection", "training", "llm_recommendation", "llm_explanation"]
TABLES = {
    "feature": "powercode_queue.feature_observations",
    "detection": "powercode_queue.detection_logs",
    "training": "powercode_queue.training_jobs",
    "llm_recommendation": "powercode_queue.llm_recommendation_jobs",
    "llm_explanation": "powercode_queue.llm_explanation_jobs",
}


@dataclass(frozen=True)
class ClaimedMessage:
    id: uuid.UUID
    payload: dict[str, Any]
    attempts: int
    lease_token: uuid.UUID
    created_at: Any
    queue: QueueName = "training"


def _table(queue: QueueName) -> str:
    try:
        return TABLES[queue]
    except KeyError as exc:
        raise ValueError(f"unknown queue: {queue}") from exc


def enqueue(conn: Any, queue: QueueName, payload: dict[str, Any], *, message_id: uuid.UUID | None = None) -> uuid.UUID:
    """호출자의 트랜잭션에서 등록해 도메인 데이터와 큐 메시지를 함께 커밋한다."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    message_id = message_id or uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {_table(queue)} (id, payload) VALUES (%s, %s::jsonb) ON CONFLICT (id) DO NOTHING",
            (message_id, json.dumps(payload)),
        )
    return message_id


def claim(conn: Any, queue: QueueName, *, lease_seconds: int = 60,
          max_attempts: int = 3, benchmark_run_id: str | None = None,
          source_id: str | None = None, job_kinds: tuple[str, ...] | None = None,
          message_id: uuid.UUID | None = None) -> ClaimedMessage | None:
    """메시지 하나를 임대하고 다른 소비자가 잠근 행은 건너뛴다."""
    if lease_seconds < 1 or max_attempts < 1:
        raise ValueError("lease_seconds and max_attempts must be positive")
    if benchmark_run_id is not None and not benchmark_run_id:
        raise ValueError("benchmark_run_id must not be empty")
    if source_id is not None and (queue not in ("feature", "detection") or not source_id):
        raise ValueError("source_id scope is only valid for observation queues")
    scope_sql = ""
    scope_params: tuple[str, ...] = ()
    if benchmark_run_id is not None:
        scope_sql += " AND payload->>'benchmarkRunId' = %s"
        scope_params += (benchmark_run_id,)
    if source_id is not None:
        if queue == "feature":
            scope_sql += " AND payload->>'sourceId' = %s"
            scope_params += (source_id,)
        else:
            # 이전 CSV 재생 메시지에는 sourceId가 없으므로 관측 참조로 범위를 확인한다.
            scope_sql += """ AND (payload->>'sourceId' = %s OR
                (NOT (payload ? 'sourceId') AND EXISTS (
                    SELECT 1 FROM data_observations AS scoped_observation
                     WHERE scoped_observation.id::text = payload->>'observationId'
                       AND scoped_observation.source_id::text = %s)))"""
            scope_params += (source_id, source_id)
    if job_kinds is not None:
        if not job_kinds:
            raise ValueError("job_kinds must not be empty")
        scope_sql += " AND EXISTS (SELECT 1 FROM run_jobs j WHERE j.id::text = payload->>'jobId' AND j.kind::text = ANY(%s))"
        scope_params += (list(job_kinds),)
    if message_id is not None:
        scope_sql += " AND id = %s"
        scope_params += (message_id,)
    token = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {_table(queue)}
                   SET status = 'dead', lease_until = NULL, lease_token = NULL,
                       last_error = 'lease_expired_max_attempts'
                 WHERE status = 'leased' AND lease_until <= clock_timestamp()
                   AND attempts >= %s{scope_sql}""",
            (max_attempts, *scope_params),
        )
        cur.execute(
            f"""
            WITH picked AS (
              SELECT id FROM {_table(queue)}
              WHERE ((status = 'pending' AND available_at <= clock_timestamp())
                 OR (status = 'leased' AND lease_until <= clock_timestamp() AND attempts < %s))
                {scope_sql}
              ORDER BY available_at, created_at
              FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE {_table(queue)} AS q
               SET status = 'leased', attempts = attempts + 1,
                   lease_token = %s, lease_until = clock_timestamp() + (%s * interval '1 second')
              FROM picked WHERE q.id = picked.id
            RETURNING q.id, q.payload, q.attempts, q.lease_token, q.created_at
            """,
            (max_attempts, *scope_params, token, lease_seconds),
        )
        row = cur.fetchone()
    return ClaimedMessage(*row, queue=queue) if row else None


def renew_lease(conn: Any, queue: QueueName, message: ClaimedMessage, *, lease_seconds: int = 60) -> bool:
    """현재 소비자의 유효한 임대만 연장한다. 호출자가 변경을 커밋해야 한다."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {_table(queue)}
                   SET lease_until = clock_timestamp() + (%s * interval '1 second')
                 WHERE id = %s AND status = 'leased' AND lease_token = %s
                   AND lease_until > clock_timestamp()""",
            (lease_seconds, message.id, message.lease_token),
        )
        return cur.rowcount == 1


def acknowledge(conn: Any, queue: QueueName, message: ClaimedMessage) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {_table(queue)} SET status = 'done', completed_at = clock_timestamp(),
                lease_until = NULL, lease_token = NULL
                WHERE id = %s AND status = 'leased' AND lease_token = %s
                  AND lease_until > clock_timestamp()""",
            (message.id, message.lease_token),
        )
        return cur.rowcount == 1


def fail(conn: Any, queue: QueueName, message: ClaimedMessage, *, max_attempts: int = 3,
         retry_seconds: int = 5, reason: str = "processing_failed") -> bool:
    if max_attempts < 1 or retry_seconds < 0:
        raise ValueError("invalid retry settings")
    dead = message.attempts >= max_attempts
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {_table(queue)} SET status = %s,
                available_at = clock_timestamp() + (%s * interval '1 second'),
                lease_until = NULL, lease_token = NULL, last_error = %s
                WHERE id = %s AND status = 'leased' AND lease_token = %s
                  AND lease_until > clock_timestamp()""",
            ("dead" if dead else "pending", retry_seconds, reason[:200], message.id, message.lease_token),
        )
        return cur.rowcount == 1


def reconcile_dead_jobs(conn: Any, queue: QueueName) -> None:
    """Expire exhausted crashed attempts; a dead queue item must not look running forever."""
    if queue == "feature":
        return
    with conn.cursor() as cur:
        cur.execute(f"""UPDATE run_job_attempts a SET status='expired',finished_at=clock_timestamp(),error_code='queue_exhausted'
            FROM run_jobs j WHERE a.id=j.current_attempt_id AND a.job_id=j.id AND a.status='running'
              AND a.lease_until<=clock_timestamp() AND EXISTS(SELECT 1 FROM {_table(queue)} q
              WHERE q.status='dead' AND q.payload->>'jobId'=j.id::text)""")
        cur.execute(f"""UPDATE run_jobs j SET status='failed',error_code='queue_exhausted',finished_at=clock_timestamp()
            WHERE j.status IN ('queued','running') AND EXISTS(SELECT 1 FROM {_table(queue)} q
              WHERE q.status='dead' AND q.payload->>'jobId'=j.id::text)
              AND NOT EXISTS(SELECT 1 FROM run_job_attempts a WHERE a.id=j.current_attempt_id
                AND a.status='running' AND a.lease_until>clock_timestamp())""")
