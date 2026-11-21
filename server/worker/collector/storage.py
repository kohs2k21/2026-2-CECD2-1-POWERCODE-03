"""허용된 T·P·M 관측값을 저장하고 판정 가능한 P를 큐에 등록한다."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

from collector import source_clock


Layer = Literal["T", "P", "M"]

# 원천 BODY·messageBody·인증정보는 이 목록에 없으므로 투영 전에 버려진다.
TEXT_FIELDS: dict[Layer, tuple[str, ...]] = {
    "T": ("transaction_id", "interface_id", "process_hub_id", "start_channel_id",
          "end_channel_id", "status", "response_code", "response_message",
          "interface_type", "category_name", "start_time_raw", "end_time_raw"),
    "P": ("transaction_id", "process_id", "depend_process_id", "process_hub_id",
          "adapter_type", "channel_id", "status", "start_time_raw", "end_time_raw",
          "response_code", "response_message"),
    "M": ("transaction_id", "process_id", "message_id", "process_channel_id",
          "data_type", "data_name", "status", "response_code", "response_message",
          "start_time_raw", "end_time_raw", "direction", "process_datetime_raw"),
}
NUMERIC_FIELDS: dict[Layer, tuple[str, ...]] = {
    "T": ("process_count", "reported_process_time_ms", "retry_count"),
    "P": ("success_count", "error_count", "total_count", "retry_count"),
    "M": ("success_count", "error_count", "data_size", "message_index", "retry_count"),
}
TABLES: dict[Layer, str] = {
    "T": "data_transactions",
    "P": "data_processes",
    "M": "data_messages",
}
_FIELDS: dict[Layer, tuple[str, ...]] = {layer: TEXT_FIELDS[layer] + NUMERIC_FIELDS[layer] for layer in TABLES}


@dataclass(frozen=True)
class IngestResult:
    observation_id: uuid.UUID
    created: bool
    queued_for_detection: bool


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise ValueError("허용 필드 값은 유한한 scalar 또는 NULL이어야 한다")


def project_observation(layer: Layer, raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """허용 필드만 새 객체로 복사하고 미제공 필드를 기록한다."""
    # type(...) is dict first: the ABC isinstance check is slow and every JSON object is a dict.
    if layer not in TABLES or not (type(raw) is dict or isinstance(raw, Mapping)):
        raise ValueError("유효한 T/P/M 계층과 객체 형태의 원천값이 필요하다")
    fields = _FIELDS[layer]
    allowed = {field: _scalar(raw[field]) for field in fields if field in raw}
    missing = [field for field in fields if field not in raw]
    return allowed, missing


def _typed_values(layer: Layer, allowed: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    typed: dict[str, Any] = {}
    flags: list[str] = []
    for field in TEXT_FIELDS[layer]:
        value = allowed.get(field)
        typed[field] = value if isinstance(value, str) else None
        if value is not None and not isinstance(value, str):
            flags.append(f"invalid_text:{field}")
    for field in NUMERIC_FIELDS[layer]:
        value = allowed.get(field)
        if value is None or isinstance(value, bool):
            typed[field] = None
            if isinstance(value, bool):
                flags.append(f"invalid_numeric:{field}")
            continue
        try:
            number = Decimal(str(value))
            if not number.is_finite():
                raise InvalidOperation()
            typed[field] = number
        except (InvalidOperation, ValueError):
            typed[field] = None
            flags.append(f"invalid_numeric:{field}")
    return typed, flags


# json.dumps(allowed, sort_keys=True, separators=(",", ":"), ensure_ascii=False) with one reused encoder (same text).
_ALLOWED_JSON = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Projection:
    """한 관측의 저장 값. `ingest_observation`과 공유 입력 일괄 입고(`input_store.admit_batch`)가 같은 값을 쓴다."""
    allowed: dict[str, Any]
    missing: list[str]
    typed: dict[str, Any]
    flags: list[str]
    allowed_json: str
    content_hash: str


def prepare_projection(layer: Layer, raw: Mapping[str, Any], execution_id: uuid.UUID | None) -> Projection:
    """허용 필드 투영·열 값·품질 표시·content_hash(allowed_values JSON의 sha256)."""
    allowed, missing = project_observation(layer, raw)
    typed, flags = _typed_values(layer, allowed)
    if execution_id is None:
        flags.append("execution_identity_unverified")
    allowed_json = _ALLOWED_JSON.encode(allowed)
    content_hash = hashlib.sha256(allowed_json.encode("utf-8")).hexdigest()
    return Projection(allowed, missing, typed, flags, allowed_json, content_hash)


def check_received_at(api_received_at: datetime | None) -> None:
    if api_received_at is not None and (not isinstance(api_received_at, datetime) or api_received_at.utcoffset() is None):
        raise ValueError("api_received_at은 시간대 있는 datetime이어야 한다")


def ingest_observation(
    conn: Any,
    *,
    source_id: uuid.UUID,
    layer: Layer,
    dedup_key: str,
    raw: Mapping[str, Any],
    execution_id: uuid.UUID | None = None,
    revision_no: int | None = None,
    detection_eligible: bool = False,
    source_revision_token: str | None = None,
    benchmark_run_id: str | None = None,
    api_received_at: datetime | None = None,
) -> IngestResult:
    """투영 관측값·계층별 행·변수 계산 메시지를 하나의 트랜잭션에 기록한다.

    호출자는 검증된 source, 버전이 반영된 dedup_key, 실행 식별과 revision을 제공한다.
    실행 식별이 불명확하면 관측값은 보존하되 변수 계산 큐에는 넣지 않는다.
    원천 시각 열(`source_log_occurred_at`·`source_log_time_raw`·`clock_status`·`parsed_times`)은 같은 트랜잭션에서
    원천 `clock_basis`를 읽어 채운다(contracts/collector-metrics.md 1.1). `api_received_at`은 API 조회 응답 수신 시각
    (api_client receipt `finished_at`)이고 CSV·재생 경로는 None(NULL)이다.
    """
    if not isinstance(source_id, uuid.UUID) or not isinstance(dedup_key, str) or not dedup_key.strip():
        raise ValueError("source_id와 비어 있지 않은 dedup_key가 필요하다")
    if (execution_id is None) != (revision_no is None):
        raise ValueError("execution_id와 revision_no는 함께 제공해야 한다")
    if revision_no is not None and (type(revision_no) is not int or revision_no < 1):
        raise ValueError("revision_no는 양의 정수여야 한다")
    if execution_id is not None and not isinstance(execution_id, uuid.UUID):
        raise ValueError("execution_id는 UUID여야 한다")
    if detection_eligible and (layer != "P" or execution_id is None):
        raise ValueError("검증된 실행 식별이 있는 PROCESS만 탐지 큐에 등록할 수 있다")
    if benchmark_run_id is not None and (not isinstance(benchmark_run_id, str) or not benchmark_run_id):
        raise ValueError("benchmark_run_id는 비어 있지 않은 문자열이어야 한다")
    check_received_at(api_received_at)

    projected = prepare_projection(layer, raw, execution_id)
    allowed, missing, typed, flags = projected.allowed, projected.missing, projected.typed, projected.flags
    allowed_json, content_hash = projected.allowed_json, projected.content_hash
    observation_id = uuid.uuid4()

    with conn.transaction():
        with conn.cursor() as cur:
            # 운영자 `clock-basis verify`는 다음 INSERT부터 반영된다(같은 트랜잭션의 원천 행 값).
            cur.execute("SELECT clock_basis FROM ops_sources WHERE id = %s", (source_id,))
            basis_row = cur.fetchone()
            source_time = source_clock.resolve(allowed, basis_row[0] if basis_row else None)
            cur.execute(
                """INSERT INTO data_observations
                     (id, source_id, layer, execution_id, revision_no, dedup_key, content_hash,
                      source_revision_token, source_log_time_raw, source_log_occurred_at,
                      api_received_at, persisted_at, clock_status,
                      parsed_times, allowed_values, quality_flags, missing_fields)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, clock_timestamp(), %s, %s::jsonb,
                           %s::jsonb, %s::jsonb, %s::jsonb)
                   ON CONFLICT (source_id, layer, dedup_key) DO NOTHING
                   RETURNING id""",
                (observation_id, source_id, layer, execution_id, revision_no,
                 dedup_key, content_hash, source_revision_token,
                 source_time.raw, source_time.occurred_at, api_received_at,
                 source_time.clock_status, source_time.parsed_json(), allowed_json,
                 json.dumps(flags), json.dumps(missing)),
            )
            inserted = cur.fetchone()
            if inserted is None:
                cur.execute(
                    """SELECT id, content_hash, execution_id, revision_no, source_revision_token
                         FROM data_observations
                         WHERE source_id = %s AND layer = %s AND dedup_key = %s""",
                    (source_id, layer, dedup_key),
                )
                existing = cur.fetchone()
                if existing is None or existing[1:] != (
                    content_hash, execution_id, revision_no, source_revision_token,
                ):
                    raise ValueError("같은 dedup_key에 다른 내용·실행 식별·revision이 있다")
                return IngestResult(existing[0], False, False)

            columns = tuple(typed)
            placeholders = ", ".join("%s" for _ in columns)
            cur.execute(
                f"INSERT INTO {TABLES[layer]} (observation_id, {', '.join(columns)}) "
                f"VALUES (%s, {placeholders})",
                (observation_id, *(typed[field] for field in columns)),
            )
        if detection_eligible:
            from detector.jobs import enqueue_observation
            enqueue_observation(conn, observation_id, benchmark_run_id=benchmark_run_id)
    return IngestResult(observation_id, True, detection_eligible)
