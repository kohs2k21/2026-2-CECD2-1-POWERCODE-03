"""Detector 다중 작업자(runner.run_workers)의 실제 PostgreSQL 시험. 합성 값만 쓰고 localhost 새 DB에서만 돈다.

실행: WORKERS_TEST_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres
      python server/worker/tests/integration_detector_workers.py
- 새 DB qa_detector_workers를 만들고(이미 있으면 실패, DROP 없음) prepare-runtime-db.py와 같은 순서로 migration과
  scripts/database-roles.sql을 적용한다. 준비 데이터는 소유자로 넣고, 작업자 연결은 powercode_detector 역할로 연다.
- v04 detect 작업 50건을 작업자 4개가 처리한다. 작업마다 판정 1건·성공 시도 1번·큐 done, 판정 이벤트(final) 수 = 판정 수,
  중복 없음, 처리한 작업자가 둘 이상인지 본다. 시도를 다 쓴 채 임대가 끝난 큐 행 1개는 0번 작업자의 주기 정리가
  dead로 바꾸고 그 작업을 failed·queue_exhausted로 바꾸는지 본다(claim 안의 dead 처리를 건너뛰는 경로).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import integration_detection_decision as base  # noqa: E402
from integration_relation_recheck import migration_files  # noqa: E402
from integration_research_pipeline import source  # noqa: E402
from detector import runner  # noqa: E402

INFRA = Path(__file__).resolve().parents[2] / "infra"
DATABASE = "qa_detector_workers"
JOBS = 50
WORKERS = 4


def prepare(admin_url: str) -> str:
    parsed = urlsplit(admin_url)
    if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path != "/postgres":
        raise SystemExit("only a local postgres admin URL is permitted")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
    url = urlunsplit(parsed._replace(path="/" + DATABASE))
    with psycopg.connect(url, autocommit=True) as conn:
        for name in migration_files():
            conn.execute((INFRA / "migrations" / name).read_text(encoding="utf-8"))
        conn.execute((INFRA / "scripts" / "database-roles.sql").read_text(encoding="utf-8"))
    return url


def detector_url(url: str) -> str:
    """작업자 연결은 Detector 실행 역할로 연다(연결 시작 옵션 role)."""
    parsed = urlsplit(url)
    query = "options=-c%20role%3Dpowercode_detector"
    return urlunsplit(parsed._replace(query=f"{parsed.query}&{query}" if parsed.query else query))


def exhausted_job(conn, sid) -> uuid.UUID:
    """시도를 다 쓴 채 임대가 끝난 큐 행(크래시가 세 번 난 작업과 같은 상태)."""
    _seen, payload = base.v04_job(conn, sid, "exhausted")
    identifier = uuid.UUID(payload["jobId"])
    conn.execute("""UPDATE powercode_queue.detection_logs SET status='leased', attempts=3, lease_token=%s,
        lease_until=clock_timestamp()-interval '1 minute' WHERE id=%s""", (uuid.uuid4(), identifier))
    conn.commit()
    return identifier


def main():
    admin = os.environ.get("WORKERS_TEST_ADMIN_URL")
    if not admin:
        raise SystemExit("WORKERS_TEST_ADMIN_URL이 필요하다")
    url = prepare(admin)
    os.environ["DATABASE_URL"] = detector_url(url)
    with tempfile.TemporaryDirectory(prefix="powercode-workers-") as directory, psycopg.connect(url) as conn:
        base.ROOT = Path(directory)
        conn.execute("INSERT INTO ops_system_state(id,revision,detector_state,changed_at) VALUES (1,0,'{}',clock_timestamp())")
        conn.commit()
        sid = source(conn, "다중 작업자 시험")
        jobs = [uuid.UUID(base.v04_job(conn, sid, f"w{index}")[1]["jobId"]) for index in range(JOBS)]
        dead = exhausted_job(conn, sid)
        conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=ANY(%s)", (jobs,))
        conn.commit()
        print(f"PASS 준비: v04 detect 작업 {JOBS}건 + 시도를 다 쓴 큐 행 1건")

        seen: list[str] = []
        original = runner.process_detection_message

        def recorded(*args, **kwargs):
            seen.append(threading.current_thread().name)
            return original(*args, **kwargs)

        runner.process_detection_message = recorded
        stop, failure = threading.Event(), []

        def serve():
            try:
                runner.run_workers(detector_url(url), Path(directory), workers=WORKERS, poll_seconds=.05, stop=stop)
            except BaseException as exc:  # noqa: BLE001
                failure.append(exc)
                stop.set()

        started = time.monotonic()
        thread = threading.Thread(target=serve, name="run-workers")
        thread.start()
        try:
            limit = started + 180
            while not stop.is_set():
                done, exhausted = conn.execute("""SELECT count(*) FILTER (WHERE id=ANY(%s) AND status='done'),
                    count(*) FILTER (WHERE id=%s AND status='dead') FROM powercode_queue.detection_logs""",
                                               (jobs, dead)).fetchone()
                failed = conn.execute("SELECT status::text FROM run_jobs WHERE id=%s", (dead,)).fetchone()[0]
                conn.commit()
                if done == JOBS and exhausted == 1 and failed == "failed":
                    break
                assert time.monotonic() < limit, (done, exhausted, failed)
                time.sleep(.1)
        finally:
            stop.set()
            thread.join(30)
            runner.process_detection_message = original
        assert not thread.is_alive() and not failure, failure
        elapsed = time.monotonic() - started
        print(f"PASS 작업자 {WORKERS}개가 {JOBS}건을 {elapsed:.1f}초에 처리하고 정상 종료(stop)")

        rows = conn.execute("""SELECT count(*), count(DISTINCT job_id), count(DISTINCT process_observation_id)
            FROM run_process_decisions WHERE job_id=ANY(%s)""", (jobs,)).fetchone()
        assert rows == (JOBS, JOBS, JOBS), rows
        states = conn.execute("""SELECT status::text, attempt_count, count(*) FROM run_jobs WHERE id=ANY(%s)
            GROUP BY 1,2""", (jobs,)).fetchall()
        assert states == [("succeeded", 1, JOBS)], states
        attempts = conn.execute("""SELECT status::text, count(*), count(DISTINCT job_id) FROM run_job_attempts
            WHERE job_id=ANY(%s) GROUP BY 1""", (jobs,)).fetchall()
        assert attempts == [("succeeded", JOBS, JOBS)], attempts
        queue = conn.execute("""SELECT status, attempts, count(*) FROM powercode_queue.detection_logs WHERE id=ANY(%s)
            GROUP BY 1,2""", (jobs,)).fetchall()
        assert queue == [("done", 1, JOBS)], queue
        events = conn.execute("""SELECT count(*), count(DISTINCT e.decision_id) FROM run_decision_events e
            JOIN run_process_decisions d ON d.id=e.decision_id WHERE e.event_kind='final' AND d.job_id=ANY(%s)""",
                              (jobs,)).fetchone()
        assert events == (JOBS, JOBS), events
        conn.commit()
        assert len(seen) == JOBS, len(seen)
        assert len(set(seen)) > 1, set(seen)
        print(f"PASS 작업마다 판정 1건·성공 시도 1번·큐 done(시도 1), 판정 이벤트 {JOBS}건, 중복 없음, "
              f"처리 작업자 {len(set(seen))}개")

        dead_row = conn.execute("""SELECT q.status, q.last_error, j.status::text, j.error_code
            FROM powercode_queue.detection_logs q JOIN run_jobs j ON j.id=q.id WHERE q.id=%s""", (dead,)).fetchone()
        decided = conn.execute("SELECT count(*) FROM run_process_decisions WHERE job_id=%s", (dead,)).fetchone()[0]
        conn.commit()
        assert dead_row == ("dead", "lease_expired_max_attempts", "failed", "queue_exhausted"), dead_row
        assert decided == 0, decided
        print("PASS 시도를 다 쓴 큐 행: claim이 가져가지 않고, 0번 작업자 주기 정리가 dead → 작업 failed·queue_exhausted")
    print("PASS integration_detector_workers")


if __name__ == "__main__":
    main()
