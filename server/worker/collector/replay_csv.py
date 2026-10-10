"""CSV의 T·P·BODY 행을 일정 속도로 PostgreSQL 저장 경로에 재생한다."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import islice
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

import psycopg
from dotenv import load_dotenv

from collector.storage import IngestResult, Layer, ingest_observation, project_observation

PROFILE = "csv-replay-v1"
InputKind = Literal["T", "P", "BODY"]
INPUT_ORDER: tuple[InputKind, ...] = ("T", "P", "BODY")
FIELD_ALIASES = {
    "start_time": "start_time_raw",
    "end_time": "end_time_raw",
    "process_time": "reported_process_time_ms",
    "process_datetime": "process_datetime_raw",
}


class ReplayError(ValueError):
    """원천값이나 접속 정보를 포함하지 않는 CSV 재생 오류."""


@dataclass(frozen=True)
class CsvInput:
    kind: InputKind
    path: Path

    @property
    def layer(self) -> Layer:
        return "M" if self.kind == "BODY" else self.kind


@dataclass(frozen=True)
class ReplayRow:
    kind: InputKind
    layer: Layer
    row_number: int
    allowed: dict[str, Any]
    identity: tuple[str, ...] | None


@dataclass
class ReplayCounts:
    read: int = 0
    eligible_process: int = 0
    stored: int = 0
    duplicate: int = 0
    queued: int = 0
    unverified_process: int = 0


def _canonical_header(header: str) -> str:
    # BODY, MESSAGE_IN, MESSAGE_OUT 등 허용 목록 밖의 헤더는 값에 접근하지 않는다.
    name = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", header.strip()).lower()
    return FIELD_ALIASES.get(name, name)


def _header_mapping(kind: InputKind, headers: list[str] | None) -> dict[str, str]:
    if not headers or any(not header or not header.strip() for header in headers):
        raise ReplayError("CSV 헤더가 없거나 빈 열 이름이 있다")
    allowed_headers = project_observation("M" if kind == "BODY" else kind, {})[1]
    mapping: dict[str, str] = {}
    seen: set[str] = set()
    for header in headers:
        canonical = _canonical_header(header)
        if canonical in seen:
            raise ReplayError("같은 의미의 CSV 헤더가 중복됐다")
        seen.add(canonical)
        if canonical in allowed_headers:
            mapping[header] = canonical
    if not mapping:
        raise ReplayError("CSV에 저장 가능한 T/P/M 필드가 없다")
    return mapping


def _required_text(allowed: Mapping[str, Any], field: str) -> str | None:
    value = allowed.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def execution_identity(kind: InputKind, allowed: Mapping[str, Any]) -> tuple[str, ...] | None:
    """원천 키 필드가 모두 있는 경우에만 CSV 재생용 실행 식별을 만든다."""
    transaction_id = _required_text(allowed, "transaction_id")
    if kind == "T":
        return (transaction_id,) if transaction_id else None
    process_id = _required_text(allowed, "process_id")
    if kind == "BODY":
        message_id = _required_text(allowed, "message_id")
        return (transaction_id, process_id, message_id) if all((transaction_id, process_id, message_id)) else None
    retry_raw = allowed.get("retry_count")
    try:
        retry = Decimal(str(retry_raw))
        if not retry.is_finite() or retry < 0 or retry != retry.to_integral_value():
            return None
    except (InvalidOperation, ValueError, TypeError):
        return None
    return (transaction_id, process_id, str(int(retry))) if all((transaction_id, process_id)) else None


def iter_csv_rows(source: CsvInput) -> Iterator[ReplayRow]:
    """한 행씩 허용 필드를 투영한다. 원본 행과 제외 필드는 저장하지 않는다."""
    with source.path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        mapping = _header_mapping(source.kind, reader.fieldnames)
        for row_number, raw in enumerate(reader, start=1):
            if None in raw or any(value is None for value in raw.values()):
                raise ReplayError(f"{source.kind} CSV {row_number}번째 행의 열 개수가 헤더와 다르다")
            selected = {canonical: raw[header] for header, canonical in mapping.items()}
            allowed, _ = project_observation(source.layer, selected)
            yield ReplayRow(source.kind, source.layer, row_number, allowed,
                            execution_identity(source.kind, allowed))


def _check_headers(inputs: list[CsvInput]) -> None:
    """DB에 쓰기 전에 지정한 모든 파일의 존재와 헤더를 검사한다."""
    for source in inputs:
        with source.path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            _header_mapping(source.kind, next(reader, None))


def _ensure_source(conn: psycopg.Connection[Any], dataset_key: str) -> uuid.UUID:
    source_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ops_sources
               (id, dataset_key, display_name, ingestion_method, schema_version,
                clock_basis, field_catalog, created_at)
               VALUES (%s, %s, %s, 'csv-replay', %s,
                       %s::jsonb, %s::jsonb, clock_timestamp())
               ON CONFLICT (dataset_key) DO NOTHING""",
            (source_id, dataset_key, dataset_key, PROFILE,
             json.dumps({"status": "unknown", "reason": "csv_clock_not_verified"}),
             json.dumps({"profile": PROFILE, "layers": ["T", "P", "M"],
                         "body_values_excluded": True})),
        )
        cur.execute(
            "SELECT id, ingestion_method, schema_version FROM ops_sources WHERE dataset_key = %s",
            (dataset_key,),
        )
        row = cur.fetchone()
    if row is None or row[1:] != ("csv-replay", PROFILE):
        raise ReplayError("같은 dataset_key가 다른 수집 계약으로 이미 등록됐다")
    return row[0]


def _ensure_execution(
    conn: psycopg.Connection[Any], source_id: uuid.UUID,
    replay_key: str, row: ReplayRow,
) -> uuid.UUID | None:
    if row.identity is None:
        return None
    execution_id = uuid.uuid4()
    # 파일 단위로 실행 namespace를 분리한다. 원천의 최종 수정 순서를 주장하지 않는다.
    key_parts = {"replay_key": replay_key, "identity": list(row.identity)}
    key = json.dumps(key_parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO data_executions
               (id, source_id, layer, execution_key, key_parts,
                key_contract_version, first_observed_at)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, clock_timestamp())
               ON CONFLICT (source_id, layer, execution_key) DO NOTHING""",
            (execution_id, source_id, row.layer, key, json.dumps(key_parts), PROFILE),
        )
        cur.execute(
            """SELECT id, key_parts, key_contract_version FROM data_executions
                 WHERE source_id = %s AND layer = %s AND execution_key = %s""",
            (source_id, row.layer, key),
        )
        existing = cur.fetchone()
    if existing is None or existing[1:] != (key_parts, PROFILE):
        raise ReplayError("CSV 실행 키가 기존 계약과 충돌한다")
    return existing[0]


def replay(
    inputs: list[CsvInput], *, dataset_key: str, replay_key: str,
    rate: float, apply: bool, dsn: str | None = None, max_rows: int | None = None,
) -> ReplayCounts:
    """기본값은 검증만 수행한다. 적용 시 각 행의 관측값과 큐를 원자적으로 커밋한다."""
    if not inputs or len({item.kind for item in inputs}) != len(inputs):
        raise ReplayError("T/P/BODY 입력은 각각 최대 한 파일씩 지정해야 한다")
    if not dataset_key.strip() or not replay_key.strip() or not math.isfinite(rate) or rate < 0:
        raise ReplayError("dataset_key와 replay_key는 필수이며 rate는 0 이상이어야 한다")
    if max_rows is not None and max_rows < 1:
        raise ReplayError("max_rows는 양수여야 한다")
    if apply and not dsn:
        raise ReplayError("적용하려면 DATABASE_URL이 필요하다")
    _check_headers(inputs)

    counts = ReplayCounts()
    conn = psycopg.connect(dsn) if apply else None
    started = time.monotonic()
    try:
        source_id = None
        if conn is not None:
            with conn.transaction():
                source_id = _ensure_source(conn, dataset_key)
        for kind in INPUT_ORDER:
            source = next((item for item in inputs if item.kind == kind), None)
            if source is None:
                continue
            remaining = None if max_rows is None else max_rows - counts.read
            if remaining == 0:
                return counts
            rows = iter_csv_rows(source)
            if remaining is not None:
                rows = islice(rows, remaining)
            for row in rows:
                counts.read += 1
                if row.kind == "P" and row.identity is None:
                    counts.unverified_process += 1
                if row.kind == "P" and row.identity is not None:
                    counts.eligible_process += 1
                if conn is not None:
                    with conn.transaction():
                        execution_id = _ensure_execution(conn, source_id, replay_key, row)
                        result: IngestResult = ingest_observation(
                            conn, source_id=source_id, layer=row.layer,
                            dedup_key=f"{PROFILE}:{replay_key}:{row.layer}:{row.row_number}",
                            raw=row.allowed, execution_id=execution_id,
                            revision_no=row.row_number if execution_id else None,
                            detection_eligible=row.kind == "P" and execution_id is not None,
                        )
                    counts.stored += int(result.created)
                    counts.duplicate += int(not result.created)
                    counts.queued += int(result.queued_for_detection)
                    if rate:
                        wait = started + counts.read / rate - time.monotonic()
                        if wait > 0:
                            time.sleep(wait)
        return counts
    finally:
        if conn is not None:
            conn.close()


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transaction-csv", type=Path)
    parser.add_argument("--process-csv", type=Path)
    parser.add_argument("--body-csv", type=Path)
    parser.add_argument("--dataset-key", required=True)
    parser.add_argument("--replay-key", required=True, help="같은 CSV 재실행에는 같은 키를 사용")
    parser.add_argument("--rate", type=float, default=1.0, help="초당 행 수. 0이면 대기 없이 재생")
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--apply", action="store_true", help="지정하면 PostgreSQL에 실제 저장")
    args = parser.parse_args()
    inputs = [CsvInput(kind, path) for kind, path in (
        ("T", args.transaction_csv), ("P", args.process_csv), ("BODY", args.body_csv),
    ) if path is not None]
    try:
        counts = replay(inputs, dataset_key=args.dataset_key, replay_key=args.replay_key,
                        rate=args.rate, apply=args.apply,
                        dsn=os.environ.get("DATABASE_URL"), max_rows=args.max_rows)
    except ReplayError as exc:
        parser.exit(1, f"CSV 재생 실패: {exc}\n")
    except (OSError, ValueError, csv.Error, psycopg.Error) as exc:
        # DB 연결 예외에는 접속 정보가 포함될 수 있으므로 원문을 출력하지 않는다.
        parser.exit(1, f"CSV 재생 실패: {type(exc).__name__}\n")
    print(f"mode={'apply' if args.apply else 'dry-run'} read={counts.read} "
          f"stored={counts.stored} duplicate={counts.duplicate} "
          f"eligible_process={counts.eligible_process} queued={counts.queued} "
          f"unverified_process={counts.unverified_process}")


if __name__ == "__main__":
    main()
