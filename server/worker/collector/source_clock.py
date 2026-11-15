"""관측 INSERT 때 채우는 원천 발생 시각(contracts/collector-metrics.md 1.1).

해석 규칙은 새로 만들지 않는다. Trainer 원천 시각 분할(`trainer.source_time.process_source_time`)과 Detector 사건 묶음이
쓰는 규칙을 그대로 부른다: END_TIME → START_TIME 순, 오프셋 있으면 그대로(`offset`), 없으면
`ops_sources.clock_basis.status='verified'`의 시간대(`verified_tz`), 그 밖은 Asia/Seoul 가정(`assumed_tz`).
시간대 읽기도 Trainer와 같은 함수(`rule_v3_capture.source_timezone`)다.

이 모듈은 값을 고르기만 한다. `persisted_at`·`api_received_at` 등 다른 시각으로 대신하지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from derived_expression import source_absent
from rule_v3_capture import source_timezone
from trainer.source_time import process_source_time

RULE = "source-time-v1"
UNPARSED = "unparsed"
ABSENT = "absent"
# 지연 지표 표본 근거(계약 3.2·3.3, 사용자 결정 11/15: assumed_tz 포함).
LATENCY_STATUSES = ("offset", "verified_tz", "assumed_tz")
CLOCK_STATUSES = ("offset", "verified_tz", "assumed_tz", UNPARSED, ABSENT)
VERIFY_METHOD = "persist_lag_check"
_ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*")


@dataclass(frozen=True)
class SourceTime:
    occurred_at: datetime | None  # UTC aware
    raw: str | None
    clock_status: str
    parsed_times: dict[str, str]

    def parsed_json(self) -> str:
        return json.dumps(self.parsed_times, sort_keys=True, separators=(",", ":"))


def resolve(values: Mapping[str, Any], clock_basis: Any) -> SourceTime:
    """T·P·M 허용 값(`start_time_raw`·`end_time_raw`)과 원천 `clock_basis`로 원천 시각을 고른다.

    M의 `process_datetime_raw`(PROCESS_DT)는 뜻이 확인되지 않아 쓰지 않는다(계약 열린 항목).
    """
    start, end = values.get("start_time_raw"), values.get("end_time_raw")
    occurred, basis, field = process_source_time(start, end, source_timezone(clock_basis))
    if occurred is not None:
        raw = end if field == "end" else start
        return SourceTime(occurred, raw, basis, {"rule": RULE, "field": field, "basis": basis})
    if source_absent(start) and source_absent(end):
        return absent()
    # 값은 있으나 해석 실패(형식 불일치·검증된 시간대 이름 없음 등). 원문은 END 먼저, 문자열만 남긴다.
    raw = next((value for value in (end, start) if not source_absent(value) and isinstance(value, str)), None)
    return SourceTime(None, raw, UNPARSED, {"rule": RULE})


def absent() -> SourceTime:
    """시각 열이 없는 행(B 참조 행 포함)."""
    return SourceTime(None, None, ABSENT, {"rule": RULE})


def validate_timezone(name: Any) -> str:
    """운영자가 지정하는 IANA 시간대 이름. 경로·빈 값·zoneinfo가 모르는 이름은 거절한다."""
    if not isinstance(name, str) or not 0 < len(name) <= 64 or not _ZONE_NAME.fullmatch(name):
        raise ValueError("invalid_timezone")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("invalid_timezone") from None
    if zone.key != name:
        raise ValueError("invalid_timezone")
    return name


def validate_actor(actor: Any) -> str:
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in actor):
        raise ValueError("invalid_actor")
    return actor.strip()


def set_clock_basis(conn: Any, source_id: Any, *, verify_timezone: str | None, actor: str) -> dict[str, Any]:
    """`clock-basis verify|reset`(운영자 CLI). 호출자가 커밋한다. Collector 실행 경로는 이 함수를 부르지 않는다.

    verify: `{"status":"verified","timezone":<IANA>,"method":"persist_lag_check","verifiedAt":<DB 시각>,"verifiedBy":<actor>}`
    reset: `{"status":"unknown","resetAt":<DB 시각>,"resetBy":<actor>}`. 이미 저장된 관측은 바꾸지 않는다(다음 INSERT부터).
    """
    actor = validate_actor(actor)
    zone = None if verify_timezone is None else validate_timezone(verify_timezone)
    row = conn.execute("SELECT clock_basis FROM ops_sources WHERE id=%s FOR UPDATE", (source_id,)).fetchone()
    if row is None:
        raise ValueError("input_source_not_initialized")
    stamp = "to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
    if zone is not None:
        updated = conn.execute(
            f"""UPDATE ops_sources SET clock_basis=jsonb_build_object('status','verified','timezone',%s::text,
                'method',%s::text,'verifiedAt',{stamp},'verifiedBy',%s::text) WHERE id=%s RETURNING clock_basis""",
            (zone, VERIFY_METHOD, actor, source_id)).fetchone()
    else:
        updated = conn.execute(
            f"""UPDATE ops_sources SET clock_basis=jsonb_build_object('status','unknown','resetAt',{stamp},'resetBy',%s::text)
                WHERE id=%s RETURNING clock_basis""", (actor, source_id)).fetchone()
    return {"sourceId": str(source_id), "previous": row[0], "clockBasis": updated[0]}
