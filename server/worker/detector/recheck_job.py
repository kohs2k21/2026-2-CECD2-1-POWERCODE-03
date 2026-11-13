"""관계 규칙 재판정 탐색 주기 작업 진입점(contracts/relation-rules.md 6.1). Compose 서비스 `recheck`(server/infra/compose.yaml)가
--interval 30으로 돌린다. Detector 서비스·층 C `incidents`와는 별도 프로세스다.

실행: DATABASE_URL=... python -m detector.recheck_job --once [--source <uuid>] [--batch-limit 500]
      --once 없이 돌리면 --interval 초(기본 30)마다 원본별로 run_once를 한 번씩 부른다.
- 거래 행 도착을 기다리는 판정이 없거나(v2 룰 세트 포함) 새 거래 행이 없으면 아무것도 넣지 않는다. 운영 상태(룰 세트)는
  대상 판정이 있을 때만 읽는다.
- 연결·대기 처리는 층 C 작업(incidents_job)과 같다: 원본마다 트랜잭션 1개, 한 원본이 실패해도 다음 원본을 돌고, 회차 전체가
  실패하면 대기를 2배씩 늘린다(최대 300초).
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

from detector.incidents_job import _reason, _recover, _sources, wait_seconds
from detector.recheck_store import DEFAULT_BATCH_LIMIT, run_once


def run_all(conn, connect: Callable[[], object], only: str | None = None,
            batch_limit: int = DEFAULT_BATCH_LIMIT) -> tuple[object | None, list[dict]]:
    """원본마다 run_once 1회. 돌려주는 연결은 다음 회차에 쓴다(실패 뒤 다시 열었을 수 있음, 다시 열기도 실패하면 None)."""
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
    parser.add_argument("--interval", type=float, default=30.0)
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
            stalled = bool(report) and all(line["status"] in ("failed", "not_run") for line in report)
        except Exception as exc:
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
