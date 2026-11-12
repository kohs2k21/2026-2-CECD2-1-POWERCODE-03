"""층 C 사건 묶음 주기 작업 진입점. Compose 서비스 `incidents`(server/infra/compose.yaml)가 --interval 60으로 돌린다. Detector 서비스와는 별도 프로세스다.

실행: DATABASE_URL=... python -m detector.incidents_job --once [--source <uuid>] [--batch-limit 1000]
      --once 없이 돌리면 --interval 초(기본 60)마다 원본별로 run_once를 한 번씩 부른다.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable

import psycopg
from psycopg.pq import TransactionStatus

from detector.incidents_store import DEFAULT_BATCH_LIMIT, run_once


def _sources(conn, only: str | None) -> list[str]:
    if only:
        return [only]
    rows = conn.execute("SELECT id FROM ops_sources ORDER BY id").fetchall()
    conn.commit()
    return [str(row[0]) for row in rows]


def _reason(exc: BaseException) -> str:
    """로그 사유. ValueError는 사유 코드 문자열, 그 밖은 예외 이름만 남긴다(DB 오류 문구에 값이 섞일 수 있음)."""
    return (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)[:100]


def _recover(conn, connect: Callable[[], object]):
    """실패한 원본 다음에 쓸 연결. 되돌릴 수 있으면 되돌려 그대로 쓰고, 끊겼거나(UNKNOWN·BAD) 되돌리기도 실패하면 닫고 새로 연다."""
    try:
        if not conn.closed and conn.info.transaction_status in (TransactionStatus.INTRANS, TransactionStatus.INERROR):
            conn.rollback()
        if not conn.closed and conn.info.transaction_status == TransactionStatus.IDLE:
            return conn
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass
    return connect()


# 연속 실패 회차의 대기 상한(초). 실패가 이어지면 간격을 2배씩 늘린다.
MAX_BACKOFF_SECONDS = 300.0


def wait_seconds(interval: float, failures: int) -> float:
    """다음 회차까지 대기. 실패 없음 = interval, 연속 실패 n회 = interval × 2ⁿ(최대 MAX_BACKOFF_SECONDS, interval보다 짧지 않음)."""
    if failures <= 0:
        return interval
    return max(interval, min(MAX_BACKOFF_SECONDS, interval * 2 ** min(failures, 32)))


def run_all(conn, connect: Callable[[], object], only: str | None = None,
            batch_limit: int = DEFAULT_BATCH_LIMIT) -> tuple[object | None, list[dict]]:
    """원본마다 트랜잭션 1개. 한 원본이 어떤 예외로 실패해도 그 원본만 되돌리고 다음 원본을 돈다.

    돌려주는 연결은 다음 회차에 쓴다(실패 뒤 다시 열었을 수 있음). 다시 열기도 실패하면 그 사유를 보고에 남기고
    남은 원본은 이번 회차에 건너뛴다(not_run). 이때 돌려주는 연결은 None이고 다음 회차에 새로 연다.
    이미 끝난 원본의 보고 줄은 그대로 돌려준다.
    """
    report = []
    sources = _sources(conn, only)
    for index, source in enumerate(sources):
        try:
            outcome = run_once(conn, source, batch_limit=batch_limit)
            report.append({"sourceId": source, **outcome.__dict__})
            continue
        except Exception as exc:
            report.append({"sourceId": source, "status": "failed", "reason": _reason(exc)})
        try:
            conn = _recover(conn, connect)
        except Exception as exc:
            report.append({"status": "failed", "reason": "reconnect:" + _reason(exc)})
            report.extend({"sourceId": rest, "status": "not_run", "reason": "connection_unavailable"}
                          for rest in sources[index + 1:])
            return None, report
    return conn, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    parser.add_argument("--interval", type=float, default=60.0)
    arguments = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")

    def connect():
        return psycopg.connect(database_url)

    conn = None
    status = 0
    failures = 0
    while True:
        try:
            conn = conn if conn is not None and not conn.closed else connect()
            conn, report = run_all(conn, connect, arguments.source, arguments.batch_limit)
            for line in report:
                print(json.dumps(line, ensure_ascii=False, default=str), flush=True)
            status = 1 if any(line["status"] in ("failed", "not_run") for line in report) else 0
            # 대기를 늘리는 실패 = 회차 전체 실패(성공한 원본 없음). 한 원본만 계속 실패하면 다른 원본은 늦추지 않는다
            stalled = bool(report) and all(line["status"] in ("failed", "not_run") for line in report)
        except Exception as exc:
            # 원본 목록 읽기·연결 실패. 반복 모드는 다음 회차에 새 연결로 다시 시도한다.
            print(json.dumps({"status": "failed", "reason": _reason(exc)}, ensure_ascii=False), flush=True)
            status = 1
            stalled = True
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
        if arguments.once:
            if conn is not None:
                conn.close()
            return status
        failures = failures + 1 if stalled else 0
        time.sleep(wait_seconds(arguments.interval, failures))


if __name__ == "__main__":
    sys.exit(main())
