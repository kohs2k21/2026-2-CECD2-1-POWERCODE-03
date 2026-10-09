"""합성 메시지로 큐 전송만 측정한다. 모델 추론과 도메인 데이터 저장은 제외한다."""

from __future__ import annotations

import argparse
import os
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
from dotenv import load_dotenv
from pathlib import Path

from pg_queue import acknowledge, claim, enqueue


def run(queue: str, count: int, consumers: int, dsn: str, timeout: float) -> None:
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    with psycopg.connect(dsn) as conn:
        for sequence in range(count):
            ref = str(uuid.uuid4())
            payload = (
                {"schemaVersion": 1, "eventId": ref, "observationId": ref, "benchmarkRunId": run_id}
                if queue == "detection" else
                {"schemaVersion": 1, "jobId": ref, "benchmarkRunId": run_id}
            )
            enqueue(conn, queue, payload)
        conn.commit()
    produced_at = time.monotonic()
    progress_lock = threading.Lock()
    completed_count = 0

    def consume() -> tuple[int, list[float]]:
        nonlocal completed_count
        processed = 0
        latencies = []
        deadline = time.monotonic() + timeout
        with psycopg.connect(dsn) as conn:
            while time.monotonic() < deadline:
                with progress_lock:
                    if completed_count >= count:
                        break
                message = claim(conn, queue, benchmark_run_id=run_id)
                if message is not None and message.payload.get("benchmarkRunId") != run_id:
                    conn.rollback()
                    raise RuntimeError("benchmark scope mismatch")
                conn.commit()
                if message is None:
                    time.sleep(0.02)
                    continue
                if not acknowledge(conn, queue, message):
                    raise RuntimeError("lease lost before acknowledgement")
                conn.commit()
                processed += 1
                with progress_lock:
                    completed_count += 1
                latencies.append((time.time() - message.created_at.timestamp()) * 1000)
        return processed, latencies

    with ThreadPoolExecutor(max_workers=consumers) as pool:
        results = list(pool.map(lambda _: consume(), range(consumers)))
    completed = time.monotonic()
    total = sum(n for n, _ in results)
    latencies = sorted(value for _, sample in results for value in sample)
    percentile = lambda p: latencies[min(len(latencies) - 1, int((len(latencies) - 1) * p))] if latencies else 0
    print(f"queue={queue} requested={count} acknowledged={total} consumers={consumers}")
    print(f"enqueue_seconds={produced_at - started:.3f} total_seconds={completed - started:.3f} throughput_per_second={total / max(completed - started, 0.001):.1f}")
    print(f"latency_ms_p50={statistics.median(latencies) if latencies else 0:.1f} latency_ms_p95={percentile(.95):.1f} latency_ms_p99={percentile(.99):.1f}")
    if total != count:
        raise SystemExit("benchmark incomplete; inspect queue rows before repeating")


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", choices=("detection", "training"), required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--consumers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.count < 1 or args.consumers < 1 or args.timeout <= 0:
        parser.error("count, consumers and timeout must be positive")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set in the environment; do not paste it into chat")
    run(args.queue, args.count, args.consumers, database_url, args.timeout)
