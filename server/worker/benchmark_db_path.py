"""Collector 저장 → PostgreSQL 큐 → Detector 조회 구간의 합성 부하 측정."""

from __future__ import annotations

import argparse
import os
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from collector.storage import ingest_observation
from detector.input_reader import load_process_observation
from pg_queue import acknowledge, claim


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


def run(dsn: str, *, count: int, consumers: int, rate: float, timeout: float) -> None:
    run_id = str(uuid.uuid4())
    source_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    latencies: list[float] = []
    progress_lock = threading.Lock()
    producer_done = threading.Event()
    completed = 0
    start = time.monotonic()
    deadline = start + timeout + (count / rate if rate else 0)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops_sources
                   (id, dataset_key, display_name, ingestion_method, schema_version,
                    clock_basis, field_catalog, created_at)
                   VALUES (%s, %s, 'Queue benchmark', 'synthetic-benchmark', 'benchmark-v1',
                           '{}'::jsonb, '{}'::jsonb, clock_timestamp())""",
                (source_id, f"benchmark-{run_id}"),
            )
            cur.execute(
                """INSERT INTO data_executions
                   (id, source_id, layer, execution_key, key_parts,
                    key_contract_version, first_observed_at)
                   VALUES (%s, %s, 'P', %s, '{}'::jsonb,
                           'synthetic-benchmark-v1', clock_timestamp())""",
                (execution_id, source_id, f"benchmark-process-{run_id}"),
            )
        conn.commit()

    def consume() -> None:
        nonlocal completed
        with psycopg.connect(dsn) as conn:
            while time.monotonic() < deadline:
                with progress_lock:
                    if completed >= count:
                        return
                message = claim(conn, "detection", benchmark_run_id=run_id)
                if message is not None and message.payload.get("benchmarkRunId") != run_id:
                    conn.rollback()
                    raise RuntimeError("benchmark scope mismatch")
                conn.commit()
                if message is None:
                    if producer_done.is_set():
                        with progress_lock:
                            if completed >= count:
                                return
                    time.sleep(0.01)
                    continue

                observation = load_process_observation(conn, message)
                if observation.source_id != source_id or observation.allowed_values.get("process_id") != "benchmark":
                    conn.rollback()
                    raise RuntimeError("queued observation is missing or invalid")
                if not acknowledge(conn, "detection", message):
                    conn.rollback()
                    raise RuntimeError("queue lease expired before acknowledgement")
                conn.commit()
                with progress_lock:
                    completed += 1
                    enqueued_at = datetime.fromisoformat(message.payload["enqueuedAt"])
                    latencies.append((datetime.now(timezone.utc) - enqueued_at).total_seconds() * 1000)

    def produce() -> None:
        with psycopg.connect(dsn) as conn:
            for sequence in range(count):
                ingest_observation(
                    conn,
                    source_id=source_id,
                    layer="P",
                    dedup_key=f"synthetic-v1:{run_id}:{sequence}",
                    raw={"transaction_id": run_id, "process_id": "benchmark",
                         "status": "synthetic", "retry_count": "0"},
                    execution_id=execution_id,
                    revision_no=sequence + 1,
                    detection_eligible=True,
                    benchmark_run_id=run_id,
                )
                conn.commit()
                if rate:
                    next_at = start + (sequence + 1) / rate
                    if next_at > time.monotonic():
                        time.sleep(next_at - time.monotonic())
        producer_done.set()

    with ThreadPoolExecutor(max_workers=consumers + 1) as pool:
        workers = [pool.submit(consume) for _ in range(consumers)]
        producer = pool.submit(produce)
        producer.result()
        for worker in workers:
            worker.result()

    elapsed = time.monotonic() - start
    latencies.sort()
    print(f"path=observation+postgres-queue+read+ack sent={count} completed={completed} consumers={consumers} rate={rate}")
    print(f"elapsed_seconds={elapsed:.3f} throughput_per_second={completed / max(elapsed, 0.001):.1f}")
    print(f"latency_ms_p50={statistics.median(latencies) if latencies else 0:.1f} latency_ms_p95={percentile(latencies, .95):.1f} latency_ms_p99={percentile(latencies, .99):.1f}")
    if completed != count:
        raise SystemExit("benchmark incomplete; inspect test database rows")


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--consumers", type=int, default=4)
    parser.add_argument("--rate", type=float, default=0, help="초당 입력 수. 0이면 가능한 한 빠르게 입력")
    parser.add_argument("--timeout", type=float, default=30, help="마지막 입력 후 소비 완료 대기 상한(초)")
    args = parser.parse_args()
    if args.count < 1 or args.consumers < 1 or args.rate < 0 or args.timeout <= 0:
        parser.error("count와 consumers와 timeout은 양수, rate는 0 이상이어야 한다")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL이 필요하다. 접속 정보는 채팅이나 로그에 출력하지 않는다")
    run(database_url, count=args.count, consumers=args.consumers,
        rate=args.rate, timeout=args.timeout)
