"""탐지 큐의 관측 ID로 저장된 PROCESS 관측값을 읽는다."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pg_queue import ClaimedMessage


@dataclass(frozen=True)
class ProcessObservation:
    observation_id: uuid.UUID
    source_id: uuid.UUID
    execution_id: uuid.UUID
    revision_no: int
    allowed_values: dict[str, Any]
    quality_flags: list[str]
    clock_status: str


def load_process_observation(conn: Any, message: ClaimedMessage) -> ProcessObservation:
    """큐 ID를 검증하고 저장된 P 관측값을 조회한다. 판정·완료 처리는 호출자 책임이다."""
    payload = message.payload
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("observationId"), str):
        raise ValueError("탐지 큐 메시지 계약이 올바르지 않다")
    try:
        observation_id = uuid.UUID(payload["observationId"])
    except ValueError as exc:
        raise ValueError("관측 ID 형식이 올바르지 않다") from exc
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.id, o.source_id, o.execution_id, o.revision_no,
                      o.allowed_values, o.quality_flags, o.clock_status
                 FROM data_observations AS o
                 JOIN data_processes AS p ON p.observation_id = o.id
                WHERE o.id = %s AND o.layer = 'P' AND o.execution_id IS NOT NULL""",
            (observation_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("탐지 가능한 PROCESS 관측값이 없다")
    return ProcessObservation(*row)
