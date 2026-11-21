"""v04 detect 시도 성공 저장의 끝: 판정·측정 INSERT와 시도·작업 완료·큐 ack를 pipeline 한 묶음으로 보낸다.

판정 INSERT 트리거(publish_decision_event)가 전역 advisory lock 81736을 잡고 COMMIT까지 쥔다(012). 그 뒤 문장마다
클라이언트 왕복(과 GIL 재획득)을 기다리면 잠금 점유가 길어지므로, 잠금 뒤 문장을 Sync 한 번으로 묶는다. 잠금 구간 왕복은
묶음 1번 + COMMIT 1번이다.

trainer/runner.py `_finish`의 detect 성공 분기와 같은 값을 쓴다. 다른 점은 둘이다.
- 소유권 재확인(lock_training_ownership)과 중첩 savepoint를 하지 않는다. 같은 트랜잭션 첫머리의 확인
  (attempt_v04.run_attempt)이 작업·시도·큐 행을 FOR UPDATE OF j, a, q로 잠가 COMMIT까지 쥐므로 다른 연결은 그 행을
  바꾸지 못한다. 그 뒤 남는 변화는 시간에 따른 임대 만료뿐이고, 이것은 판정 INSERT 트리거(시도 임대, 016
  erd_v03_validate_result), 작업 완료 트리거(시도 임대, 027 "completion requires current unexpired attempt"),
  ack 조건(큐 임대 lease_until)이 각각 다시 본다.
- 문장을 pipeline으로 보낸다. 실패 경로(시도 실패·재시도)와 v03 경로는 지금처럼 trainer `_finish`를 쓴다.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping
import uuid

from pg_queue import acknowledge_statement

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
