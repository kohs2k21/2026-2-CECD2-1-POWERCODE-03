"""학습 작업의 큐 임대와 실행 시도 임대를 함께 갱신한다."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from pg_queue import ClaimedMessage, renew_lease


class _OwnershipLost(Exception):
    pass


def renew_training_ownership(
    conn: Any,
    message: ClaimedMessage,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    attempt_token: uuid.UUID,
    *,
    lease_seconds: int = 60,
) -> bool:
    """현재 실행 시도와 큐 임대를 한 트랜잭션에서 연장한다."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM run_jobs
                         WHERE id = %s AND status = 'running' AND current_attempt_id = %s
                         FOR UPDATE""",
                    (job_id, attempt_id),
                )
                if cur.fetchone() is None:
                    raise _OwnershipLost()
                cur.execute(
                    """UPDATE run_job_attempts
                          SET lease_until = clock_timestamp() + (%s * interval '1 second'),
                              heartbeat_at = clock_timestamp()
                        WHERE id = %s AND job_id = %s AND lease_token = %s
                          AND status = 'running' AND lease_until > clock_timestamp()""",
                    (lease_seconds, attempt_id, job_id, attempt_token),
                )
                if cur.rowcount != 1 or not renew_lease(conn, "training", message, lease_seconds=lease_seconds):
                    raise _OwnershipLost()
    except _OwnershipLost:
        return False
    return True


def lock_training_ownership(
    conn: Any,
    message: ClaimedMessage,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    attempt_token: uuid.UUID,
) -> bool:
    """최종 결과 저장 트랜잭션에서 현재 소유권을 행 잠금으로 확인한다."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1
                 FROM run_jobs AS j
                 JOIN run_job_attempts AS a ON a.id = j.current_attempt_id AND a.job_id = j.id
                 JOIN powercode_queue.training_jobs AS q ON q.id = %s
                WHERE j.id = %s AND j.status = 'running'
                  AND a.id = %s AND a.status = 'running' AND a.lease_token = %s
                  AND a.lease_until > clock_timestamp()
                  AND q.status = 'leased' AND q.lease_token = %s
                  AND q.lease_until > clock_timestamp()
                FOR UPDATE OF j, a, q""",
            (message.id, job_id, attempt_id, attempt_token, message.lease_token),
        )
        return cur.fetchone() is not None


class TrainingLeaseHeartbeat:
    """학습 중 별도 DB 연결로 임대를 갱신하고 소유권 상실을 알린다."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        message: ClaimedMessage,
        job_id: uuid.UUID,
        attempt_id: uuid.UUID,
        attempt_token: uuid.UUID,
        *,
        lease_seconds: int = 60,
        interval_seconds: float = 20,
    ) -> None:
        if lease_seconds < 1 or interval_seconds <= 0 or interval_seconds >= lease_seconds:
            raise ValueError("갱신 간격은 임대 시간보다 짧은 양수여야 한다")
        self.connection_factory = connection_factory
        self.message = message
        self.job_id = job_id
        self.attempt_id = attempt_id
        self.attempt_token = attempt_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self.connection_factory() as conn:
                    renewed = renew_training_ownership(
                        conn, self.message, self.job_id, self.attempt_id,
                        self.attempt_token, lease_seconds=self.lease_seconds,
                    )
                if not renewed:
                    self._lost.set()
                    return
            except Exception:
                # 연결 오류도 소유권 상실로 취급해 결과 채택을 중단한다.
                self._lost.set()
                return
