"""v04 detect 시도 성공 저장의 끝: 판정·측정 INSERT와 시도·작업 완료·큐 ack를 pipeline 한 묶음으로 보낸다.

판정 INSERT 트리거(publish_decision_event)가 전역 advisory lock 81736을 잡고 COMMIT까지 쥔다(012). 그 뒤 문장마다
클라이언트 왕복(과 GIL 재획득)을 기다리면 잠금 점유가 길어지므로, 잠금 뒤 문장을 Sync 한 번으로 묶는다. 잠금 구간 왕복은
묶음 1번 + COMMIT 1번이다.

trainer/runner.py `_finish`의 detect 성공 분기와 같은 값을 쓴다. 다른 점은 둘이다.
- 소유권 재확인(lock_training_ownership)과 중첩 savepoint를 하지 않는다. 같은 트랜잭션 첫머리의 확인
  (attempt_v04.save_attempt)이 작업·시도·큐 행을 FOR UPDATE OF j, a, q로 잠가 COMMIT까지 쥐므로 다른 연결은 그 행을
  바꾸지 못한다. 그 뒤 남는 변화는 시간에 따른 임대 만료뿐이고, 이것은 판정 INSERT 트리거(시도 임대, 016
  erd_v03_validate_result), 작업 완료 트리거(시도 임대, 027 "completion requires current unexpired attempt"),
  ack 조건(큐 임대 lease_until)이 각각 다시 본다.
- 문장을 pipeline으로 보낸다. 실패 경로(시도 실패·재시도)와 v03 경로는 지금처럼 trainer `_finish`를 쓴다.

finish_detect_batch는 첫 판정 여러 건을 한 트랜잭션·pipeline 두 묶음으로 보낸다(runner 다중 작업자의 묶음 저장). 잠금 81736
획득과 COMMIT이 묶음마다 한 번이다. 소유권 확인은 첫 묶음(잠금 전)에 넣고 그 결과를 판정 INSERT 전에 본다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
import uuid

from pg_queue import _table, acknowledge_statement

# trainer/runner.py `_finish` 성공 분기와 같은 문장(결과가 있으면 시도 succeeded, error_code NULL).
ATTEMPT_SUCCEEDED = """UPDATE run_job_attempts SET status = %s, finished_at = clock_timestamp(),
                          error_code = %s WHERE id = %s"""
JOB_SUCCEEDED = """UPDATE run_jobs SET status = 'succeeded', result_summary = %s::jsonb,
                              result_config_version_id = %s, result_artifact_id = %s,
                              finished_at = clock_timestamp(), error_code = NULL WHERE id = %s"""


def finish_detect_success(conn: Any, message: Any, job: Mapping[str, Any], attempt_id: uuid.UUID,
                          result: Mapping[str, Any], before: Iterable[tuple[str, tuple[Any, ...]]] = ()) -> None:
    """호출자가 연 판정 저장 트랜잭션 안에서 부른다. COMMIT은 호출자 몫이다.

    before: 완료 문장 앞에 같은 묶음으로 보낼 (sql, 인자) — 측정 INSERT, 판정 INSERT 순서. 묶음 순서는 before →
    시도 UPDATE → 작업 UPDATE(완료 트리거가 판정 행과 시도 임대를 본다) → 큐 ack다.

    pipeline 블록을 나올 때 Sync 한 번으로 모든 결과를 받는다. 앞 문장이 트리거 예외·제약 위반으로 실패하면 뒤 문장은
    서버가 실행하지 않고(pipeline aborted) 그 첫 오류가 여기서 예외로 올라와 호출자 트랜잭션이 ROLLBACK된다.
    ack가 맞은 행이 1개가 아니면(큐 임대 만료) RuntimeError("training_queue_lease_lost")로 같은 처리를 한다(`_finish`와
    같은 원인 문자열). rowcount는 pipeline 결과를 받은 뒤(블록을 나온 뒤)에만 읽는다.
    """
    ack_sql, ack_params = acknowledge_statement(message.queue, message)
    with conn.cursor() as cur, conn.cursor() as ack:
        with conn.pipeline():
            for sql, params in before:
                cur.execute(sql, params)
            cur.execute(ATTEMPT_SUCCEEDED, ("succeeded", None, attempt_id))
            cur.execute(JOB_SUCCEEDED, (json.dumps(result), result.get("configVersionId"), result.get("artifactId"),
                                        job["id"]))
            ack.execute(ack_sql, ack_params)
        if ack.rowcount != 1:
            raise RuntimeError("training_queue_lease_lost")


# trainer/lease.py `lock_training_ownership`과 같은 문장(공백 제외). pipeline에 넣으려고 문장만 따로 둔다(그 함수는 바로
# fetchone해 Sync를 강제한다). 인자 = (큐 id, 작업 id, 시도 id, 시도 토큰, 큐 토큰), 큐 표 이름은 {queue_table}.
OWNERSHIP_LOCK = """SELECT 1
                 FROM run_jobs AS j
                 JOIN run_job_attempts AS a ON a.id = j.current_attempt_id AND a.job_id = j.id
                 JOIN {queue_table} AS q ON q.id = %s
                WHERE j.id = %s AND j.status = 'running'
                  AND a.id = %s AND a.status = 'running' AND a.lease_token = %s
                  AND a.lease_until > clock_timestamp()
                  AND q.status = 'leased' AND q.lease_token = %s
                  AND q.lease_until > clock_timestamp()
                FOR UPDATE OF j, a, q"""


@dataclass(frozen=True)
class BatchItem:
    """묶음 저장 1건의 문장. features = 변수 캐시 INSERT들, measurement = detector 측정 INSERT(첫 판정이라 늘 있음),
    decision = 판정 INSERT, result = 작업 result_summary."""
    message: Any
    job: Mapping[str, Any]
    attempt_id: uuid.UUID
    attempt_token: uuid.UUID
    features: list[tuple[str, tuple[Any, ...]]]
    measurement: tuple[str, tuple[Any, ...]] | None
    decision: tuple[str, tuple[Any, ...]]
    result: Mapping[str, Any]


class BatchRejected(RuntimeError):
    """묶음 저장의 Sync 뒤 확인 실패(소유권 행 없음 또는 ack가 1행이 아님). 호출자가 묶음 전체를 ROLLBACK한다."""


def _ownership_params(item: BatchItem) -> tuple[Any, ...]:
    return (item.message.id, item.job["id"], item.attempt_id, item.attempt_token, item.message.lease_token)


def finish_detect_batch(conn: Any, items: list[BatchItem]) -> None:
    """호출자가 연 트랜잭션 안에서 부른다. COMMIT·ROLLBACK은 호출자 몫이다. 문장을 pipeline 두 묶음(Sync 2번)으로 보낸다.

    순서(items 순서 = 임대 순서를 단계마다 지킨다):
    - 첫 묶음(잠금 81736 전)
      1. 모든 건의 소유권 확인(OWNERSHIP_LOCK, 작업·시도·큐 행 FOR UPDATE) — 한 건 저장의 첫 확인과 같은 문장
      2. 모든 건의 변수 캐시 INSERT
      3. 모든 건의 detector 측정 INSERT
      → Sync. 소유권 확인이 하나라도 0행이면 여기서 BatchRejected(판정 INSERT를 보내지 않는다)
    - 둘째 묶음: 건마다 판정 INSERT → 시도 succeeded → 작업 succeeded → 큐 ack(finish_detect_success와 같은 문장·순서)
      → Sync. ack가 하나라도 1행이 아니면 BatchRejected
    한 건 저장(소유권 → 캐시 → 측정 → 판정 → 완료 → ack)과 건마다의 순서는 같다. 판정 INSERT가 items 순서라 판정 이벤트
    순번도 그 순서다.

    소유권 결과를 판정 INSERT 전에 보는 이유: 소유권을 잃은 건의 판정 INSERT가 81736을 쥔 채 돌면, 그 INSERT 트리거
    (decision_no_final_guard)가 다른 저장자가 잠근 작업 행을 FOR UPDATE로 기다리고 그 저장자는 81736을 기다려 교착이 되고,
    PostgreSQL이 교착을 알아챌 때까지(deadlock_timeout) 모든 판정 저장이 멈춘다. 추가 왕복 1번은 잠금 전이다. 소유권 확인·
    캐시·측정을 첫 묶음에 모은 것은 81736을 잡은 뒤에는 판정·완료 문장만 서버에서 돌게 하려는 것이기도 하다(임대 갱신
    heartbeat가 같은 작업 행을 잠근 경우의 행 잠금 대기도 잠금 전에 끝난다).

    묶음 안 문장이 오류면 뒤 문장은 서버가 실행하지 않고(pipeline aborted) 그 첫 오류가 예외로 올라온다. 어느 경우든 아직
    COMMIT 전이다.
    """
    with conn.cursor() as cur:
        owners = [conn.cursor() for _ in items]
        acks = [conn.cursor() for _ in items]
        try:
            with conn.pipeline():
                for item, owner in zip(items, owners):
                    owner.execute(OWNERSHIP_LOCK.format(queue_table=_table(item.message.queue)), _ownership_params(item))
                for item in items:
                    for sql, params in item.features:
                        cur.execute(sql, params)
                for item in items:
                    if item.measurement is not None:
                        cur.execute(*item.measurement)
            # fetchone은 pipeline 결과를 받은 뒤(블록을 나온 뒤)에만 읽는다.
            if any(owner.fetchone() is None for owner in owners):
                raise BatchRejected("detection_lease_lost")
            with conn.pipeline():
                for item, ack in zip(items, acks):
                    cur.execute(*item.decision)
                    cur.execute(ATTEMPT_SUCCEEDED, ("succeeded", None, item.attempt_id))
                    cur.execute(JOB_SUCCEEDED, (json.dumps(item.result), item.result.get("configVersionId"),
                                                item.result.get("artifactId"), item.job["id"]))
                    ack.execute(*acknowledge_statement(item.message.queue, item.message))
            # rowcount도 pipeline 결과를 받은 뒤에만 읽는다.
            if any(ack.rowcount != 1 for ack in acks):
                raise BatchRejected("training_queue_lease_lost")
        finally:
            for cursor in (*owners, *acks):
                cursor.close()
