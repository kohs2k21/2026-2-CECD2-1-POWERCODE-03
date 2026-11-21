"""Detector 다중 작업자(runner.run_workers)·묶음 저장(DETECTOR_BATCH_SIZE)의 실제 PostgreSQL 시험. 합성 값만 쓰고 localhost 새 DB에서만 돈다.

실행: WORKERS_TEST_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres
      python server/worker/tests/integration_detector_workers.py
- 새 DB qa_detector_workers를 만들고(이미 있으면 실패, DROP 없음) prepare-runtime-db.py와 같은 순서로 migration과
  scripts/database-roles.sql을 적용한다. 준비 데이터는 소유자로 넣고, 작업자 연결은 powercode_detector 역할로 연다.
- v04 detect 작업 50건을 작업자 4개·묶음 크기 8이 처리한다. 작업마다 판정 1건·성공 시도 1번·큐 done, 판정 이벤트(final) 수 = 판정 수,
  중복 없음, 처리한 작업자가 둘 이상인지, 방법 결과에 슬롯 부족(capacity)이 없는지 본다. 묶음 저장이 실제로 여러 건을 한
  트랜잭션(판정 행 xmin 하나)으로 저장했는지, 묶음 안 판정 이벤트 순번이 임대 순서인지, judgment_store 행(묶음 안 같은 값)과
  evidence `telemetry.details.judgmentStoreBatchSize`를 본다. 시도를 다 쓴 채 임대가 끝난 큐 행 1개는 0번 작업자의 주기 정리가
  dead로 바꾸고 그 작업을 failed·queue_exhausted로 바꾸는지 본다(claim 안의 dead 처리를 건너뛰는 경로).
- 묶음 안 한 건이 저장에서 실패(시험 트리거가 그 관측의 판정 INSERT를 거부): 묶음 전체 ROLLBACK 뒤 한 건씩 저장으로 나머지는
  저장되고 그 건만 실패하며, 작업·시도·큐 상태가 묶음 크기 1(한 건 경로)에서 같은 실패를 냈을 때와 같은지 본다.
- 저장 트랜잭션 수: 작업자 1개가 16건을 묶음 크기 1과 8로 처리할 때 판정 행 xmin 수(= 판정 저장 COMMIT·잠금 81736 획득 수)가 16과 2인지 본다.
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
from detector import attempt_v04, policy, runner  # noqa: E402

INFRA = Path(__file__).resolve().parents[2] / "infra"
DATABASE = "qa_detector_workers"
JOBS = 50
WORKERS = 4
BATCH = 8


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


def new_jobs(conn, sid, prefix: str, count: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(작업 id, 관측 id) 목록. 지금 바로 임대할 수 있게 available_at을 당긴다."""
    made = []
    for index in range(count):
        seen, payload = base.v04_job(conn, sid, f"{prefix}{index}")
        made.append((uuid.UUID(payload["jobId"]), seen.observation_id))
    conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=ANY(%s)",
                 ([job for job, _observation in made],))
    conn.commit()
    return made


class BatchRecorder:
    """attempt_v04.save_batch를 감싸 묶음마다 (작업 id 목록(저장 순서), 성공 여부)를 남긴다."""

    def __init__(self) -> None:
        self.lock, self.batches = threading.Lock(), []
        self.original = attempt_v04.save_batch

    def __enter__(self):
        def recorded(conn, pendings):
            saved = self.original(conn, pendings)
            with self.lock:
                self.batches.append(([item.job["id"] for item in pendings], saved))
            return saved

        attempt_v04.save_batch = recorded
        return self

    def __exit__(self, *_exc) -> None:
        attempt_v04.save_batch = self.original


def loop_until_empty(url: str, root: Path, batch_size: int) -> None:
    """작업자 1개(run_workers 작업자와 같은 설정)를 큐가 빌 때까지 돌린다."""
    with psycopg.connect(detector_url(url)) as worker:
        loop = runner._Loop(worker, root, "detector-batch-test", reconcile=False, expire_in_claim=False,
                            fixed_refresh_seconds=1.0, batch_size=batch_size)
        while loop.iterate():
            pass


def state_of(conn, job: uuid.UUID) -> tuple:
    """(작업 상태·원인, 시도 상태·원인, 큐 상태·last_error, 판정 수)."""
    row = conn.execute("""SELECT j.status::text, j.error_code, a.status::text, a.error_code, q.status, q.last_error,
            (SELECT count(*) FROM run_process_decisions d WHERE d.job_id=j.id)
        FROM run_jobs j JOIN run_job_attempts a ON a.job_id=j.id JOIN powercode_queue.detection_logs q ON q.id=j.id
        WHERE j.id=%s""", (job,)).fetchone()
    conn.commit()
    return row


def workers_with_batches(conn, url: str, directory: str, sid) -> None:
    jobs = [uuid.UUID(base.v04_job(conn, sid, f"w{index}")[1]["jobId"]) for index in range(JOBS)]
    dead = exhausted_job(conn, sid)
    conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=ANY(%s)", (jobs,))
    conn.commit()
    print(f"PASS 준비: v04 detect 작업 {JOBS}건 + 시도를 다 쓴 큐 행 1건")

    seen: list[str] = []
    original = runner._process_message

    def recorded(*args, **kwargs):
        seen.append(threading.current_thread().name)
        return original(*args, **kwargs)

    runner._process_message = recorded
    stop, failure = threading.Event(), []

    def serve():
        try:
            runner.run_workers(detector_url(url), Path(directory), workers=WORKERS, poll_seconds=.05, stop=stop,
                               batch_size=BATCH)
        except BaseException as exc:  # noqa: BLE001
            failure.append(exc)
            stop.set()

    started = time.monotonic()
    thread = threading.Thread(target=serve, name="run-workers")
    with BatchRecorder() as recorder:
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
            runner._process_message = original
    assert not thread.is_alive() and not failure, failure
    elapsed = time.monotonic() - started
    print(f"PASS 작업자 {WORKERS}개·묶음 {BATCH}가 {JOBS}건을 {elapsed:.1f}초에 처리하고 정상 종료(stop)")

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
    causes = conn.execute("""SELECT m->>'cause', count(*) FROM run_process_decisions d,
        jsonb_array_elements(d.evidence->'methodResults') m WHERE d.job_id=ANY(%s) GROUP BY 1""", (jobs,)).fetchall()
    conn.commit()
    # 슬롯 = 작업자 수 + 1이라 겹친 판정도 슬롯 부족(capacity) 미판정이 없다(detection-decision.md D20).
    assert causes and all(cause != "capacity" for cause, _count in causes), causes
    assert policy.capacity() == WORKERS + 1, policy.capacity()
    assert len(seen) == JOBS, len(seen)
    assert len(set(seen)) > 1, set(seen)
    print(f"PASS 작업마다 판정 1건·성공 시도 1번·큐 done(시도 1), 판정 이벤트 {JOBS}건, 중복 없음, "
          f"처리 작업자 {len(set(seen))}개")

    # 판정 행 xmin = 저장 트랜잭션. 묶음마다 하나이고, 그 안 판정 이벤트 순번은 임대(저장) 순서다.
    saved = [batch for batch, ok in recorder.batches if ok]
    assert saved and max(len(batch) for batch in saved) > 1, recorder.batches
    assert all(ok for _batch, ok in recorder.batches), recorder.batches
    for batch in saved:
        found = conn.execute("""SELECT count(DISTINCT d.xmin::text), array_agg(d.job_id ORDER BY e.id),
                array_agg(DISTINCT (d.evidence #>> '{telemetry,details,judgmentStoreBatchSize}')),
                count(DISTINCT m.duration_ms), count(m.id)
            FROM run_process_decisions d JOIN run_decision_events e ON e.decision_id=d.id AND e.event_kind='final'
            LEFT JOIN ops_pipeline_measurements m ON m.observation_id=d.process_observation_id AND m.stage='judgment_store'
            WHERE d.job_id=ANY(%s)""", (batch,)).fetchone()
        assert found == (1, batch, [str(len(batch))], 1, len(batch)), (found, batch)
    transactions = conn.execute("SELECT count(DISTINCT xmin::text) FROM run_process_decisions WHERE job_id=ANY(%s)",
                                (jobs,)).fetchone()[0]
    stores = conn.execute("""SELECT count(*), count(DISTINCT observation_id) FROM ops_pipeline_measurements
        WHERE stage='judgment_store' AND observation_id IN
          (SELECT process_observation_id FROM run_process_decisions WHERE job_id=ANY(%s))""", (jobs,)).fetchone()
    single = conn.execute("""SELECT count(*) FROM run_process_decisions WHERE job_id=ANY(%s)
        AND NOT (evidence #> '{telemetry,details}' ? 'judgmentStoreBatchSize')""", (jobs,)).fetchone()[0]
    conn.commit()
    assert stores == (JOBS, JOBS), stores
    assert single == JOBS - sum(len(batch) for batch in saved), (single, saved)
    print(f"PASS 묶음 저장 {len(saved)}번(최대 {max(len(batch) for batch in saved)}건), 판정 저장 트랜잭션 {transactions}개/{JOBS}건, "
          f"묶음마다 xmin 1개·이벤트 순번 = 임대 순서·judgment_store 같은 값·judgmentStoreBatchSize = 묶음 크기, "
          f"judgment_store {JOBS}행")

    dead_row = conn.execute("""SELECT q.status, q.last_error, j.status::text, j.error_code
        FROM powercode_queue.detection_logs q JOIN run_jobs j ON j.id=q.id WHERE q.id=%s""", (dead,)).fetchone()
    decided = conn.execute("SELECT count(*) FROM run_process_decisions WHERE job_id=%s", (dead,)).fetchone()[0]
    conn.commit()
    assert dead_row == ("dead", "lease_expired_max_attempts", "failed", "queue_exhausted"), dead_row
    assert decided == 0, decided
    print("PASS 시도를 다 쓴 큐 행: claim이 가져가지 않고, 0번 작업자 주기 정리가 dead → 작업 failed·queue_exhausted")


REJECT_TRIGGER = """
CREATE FUNCTION qa_reject_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.process_observation_id = ANY ({rejected}::uuid[]) THEN
    RAISE EXCEPTION 'qa_rejected_observation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER qa_reject_decision BEFORE INSERT ON run_process_decisions FOR EACH ROW EXECUTE FUNCTION qa_reject_decision();
"""


def item_failure_in_batch(conn, url: str, root: Path, sid) -> None:
    """묶음 안 한 건의 판정 INSERT를 시험 트리거가 거부한다. 나머지는 한 건씩 저장으로 저장되고 그 건만 한 건 경로와 같이 실패한다."""
    made = new_jobs(conn, sid, "reject", 5)
    bad_job, bad_observation = made[2]
    single_job, single_observation = new_jobs(conn, sid, "reject-single", 1)[0]
    conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp()+interval '1 hour' WHERE id=%s",
                 (single_job,))
    conn.execute(sql.SQL(REJECT_TRIGGER).format(rejected=sql.Literal([str(bad_observation), str(single_observation)])))
    conn.commit()
    try:
        with BatchRecorder() as recorder:
            loop_until_empty(url, root, BATCH)
        assert recorder.batches == [([job for job, _observation in made], False)], recorder.batches
        good = [job for job, _observation in made if job != bad_job]
        found = conn.execute("""SELECT count(*), count(DISTINCT xmin::text),
                count(*) FILTER (WHERE evidence #> '{telemetry,details}' ? 'judgmentStoreBatchSize')
            FROM run_process_decisions WHERE job_id=ANY(%s)""", (good,)).fetchone()
        stores = conn.execute("""SELECT count(*) FROM ops_pipeline_measurements WHERE stage='judgment_store'
            AND observation_id=ANY(%s)""", ([observation for job, observation in made if job != bad_job],)).fetchone()[0]
        conn.commit()
        # 묶음은 되돌려졌고 나머지 4건은 한 건씩(트랜잭션 4개, 묶음 크기 키 없음) 저장됐다.
        assert found == (4, 4, 0), found
        assert stores == 4, stores
        assert all(state_of(conn, job)[:2] == ("succeeded", None) for job in good)
        batch_failure = state_of(conn, bad_job)
        # 같은 거부를 묶음 크기 1(한 건 경로)에서 낸다. 실패한 묶음 건의 재시도(5초 뒤)는 끼지 않게 미룬다.
        conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp()+interval '1 day' WHERE id=%s",
                     (bad_job,))
        conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=%s", (single_job,))
        conn.commit()
        loop_until_empty(url, root, 1)
        single_failure = state_of(conn, single_job)
        assert batch_failure == single_failure == ("queued", "detection_processing_failed", "failed",
                                                   "detection_processing_failed", "pending",
                                                   "detection_processing_failed", 0), (batch_failure, single_failure)
    finally:
        conn.rollback()
        conn.execute("DROP TRIGGER qa_reject_decision ON run_process_decisions")
        conn.execute("DROP FUNCTION qa_reject_decision()")
        # 재시도 대기 중인 두 작업이 뒤 시험에 끼지 않게 큐에서 뺀다.
        conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp()+interval '1 day' WHERE id=ANY(%s)",
                     ([bad_job, single_job],))
        conn.commit()
    print(f"PASS 묶음 안 한 건 저장 실패: 묶음 ROLLBACK → 나머지 4건 한 건씩 저장, 실패 건 상태 = 한 건 경로 {batch_failure[:6]}")


def store_transactions(conn, url: str, root: Path, sid) -> None:
    """작업자 1개가 16건을 묶음 크기 1·8로 처리할 때 판정 저장 트랜잭션(xmin) 수."""
    counts = {}
    for size in (1, BATCH):
        made = new_jobs(conn, sid, f"tx{size}-", 16)
        loop_until_empty(url, root, size)
        counts[size] = conn.execute("""SELECT count(*), count(DISTINCT xmin::text) FROM run_process_decisions
            WHERE job_id=ANY(%s)""", ([job for job, _observation in made],)).fetchone()
        conn.commit()
    assert counts == {1: (16, 16), BATCH: (16, 2)}, counts
    print(f"PASS 판정 저장 트랜잭션(= COMMIT·잠금 81736 획득) 수: 16건에 묶음 1 → {counts[1][1]}, 묶음 {BATCH} → {counts[BATCH][1]}")


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
        workers_with_batches(conn, url, directory, sid)
        item_failure_in_batch(conn, url, Path(directory), sid)
        store_transactions(conn, url, Path(directory), sid)
    print("PASS integration_detector_workers")


if __name__ == "__main__":
    main()
