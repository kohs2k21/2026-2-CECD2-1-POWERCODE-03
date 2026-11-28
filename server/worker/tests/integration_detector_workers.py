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
- 판정당 새 스레드 수: threading.Thread.start를 세어 한 건 경로·묶음 8 경로를 시도마다 heartbeat 스레드(전, heartbeats=None)와
  작업자 공유 heartbeat(후)로 각각 8건씩, 직접 전달(POST /detect) 4건을 잰다. 후에는 작업자 루프가 판정마다 스레드를 만들지 않는다.
- 다중 프로세스(service.run_processes, DETECTOR_PROCESSES 2 × DETECTOR_WORKERS 2): 실제 자식 프로세스 1개(같은 모듈
  `--process-index 1`, 기록용 감싸기만 더함)와 이 프로세스(0번)가 v04 작업 50건을 나눠 처리한다. 작업마다 판정 1건·중복 없음,
  두 프로세스 모두 처리, 정리(dead 처리·대조)는 0번 프로세스 0번 작업자만, 긴 시도(프로세스마다 첫 시도 1.5초, 갱신 간격 0.3초)의
  임대 갱신(heartbeat_at), 정상 정지(자식 종료 코드 0·남은 프로세스 없음)를 본다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import integration_detection_decision as base  # noqa: E402
from integration_relation_recheck import migration_files  # noqa: E402
from integration_research_pipeline import source  # noqa: E402
from detector import attempt_v04, heartbeat, policy, runner, service  # noqa: E402
import pg_queue  # noqa: E402

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


class ThreadStarts:
    """threading.Thread.start 호출(새 스레드) 이름을 센다."""

    def __init__(self) -> None:
        self.lock, self.names = threading.Lock(), []
        self.original = threading.Thread.start

    def __enter__(self):
        recorder = self

        def start(thread):
            with recorder.lock:
                recorder.names.append(thread.name)
            return recorder.original(thread)

        self.patcher = patch.object(threading.Thread, "start", start)
        self.patcher.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.patcher.stop()


def loop_threads(conn, url: str, root: Path, sid, batch_size: int, shared: bool, count: int) -> list[str]:
    """작업자 1개가 count건을 처리하는 동안 새로 시작한 스레드 이름. 공유 heartbeat는 재기 전에 만든다(작업자마다 1번)."""
    made = new_jobs(conn, sid, f"threads-{batch_size}-{shared}-", count)
    with psycopg.connect(detector_url(url)) as worker, \
            heartbeat.SharedLeaseHeartbeat(lambda: psycopg.connect(detector_url(url), autocommit=True)) as heartbeats:
        loop = runner._Loop(worker, root, "detector-thread-test", reconcile=False, expire_in_claim=False,
                            fixed_refresh_seconds=1.0, batch_size=batch_size, heartbeats=heartbeats if shared else None)
        with ThreadStarts() as starts:
            while loop.iterate():
                pass
    decided = conn.execute("SELECT count(*) FROM run_process_decisions WHERE job_id=ANY(%s)",
                           ([job for job, _observation in made],)).fetchone()[0]
    conn.commit()
    assert decided == count, (decided, count)
    return starts.names


def threads_per_decision(conn, url: str, root: Path, sid) -> None:
    """판정당 새 스레드 수(전·후). 방법 실행 풀(policy._POOLS)은 처음 몇 건에서 늘고 그 뒤 재사용되므로 먼저 데운다."""
    loop_threads(conn, url, root, sid, BATCH, True, 4)
    loop_threads(conn, url, root, sid, 1, True, 4)
    counts, names = {}, {}
    for label, size, shared in (("한 건·전", 1, False), ("한 건·후", 1, True), ("묶음 8·전", BATCH, False),
                                ("묶음 8·후", BATCH, True)):
        started = loop_threads(conn, url, root, sid, size, shared, 8)
        counts[label], names[label] = len(started), started
    # 전: 시도마다 TrainingLeaseHeartbeat 스레드 1개. 후: 작업자 루프는 판정마다 스레드를 만들지 않는다.
    # 방법 실행 풀(detection-rule_*·detection-ml_*)은 쉬는 스레드가 없을 때만 슬롯 수까지 늘고 프로세스 동안 재사용되므로
    # 판정당 수에서 빼고 따로 센다(늘 수 있는 최대 = 2 × 슬롯).
    pooled = {label: sum(1 for name in starts if name.startswith("detection-")) for label, starts in names.items()}
    assert counts["한 건·전"] - pooled["한 건·전"] >= 8 and counts["묶음 8·전"] - pooled["묶음 8·전"] >= 8, (counts, names)
    assert counts["한 건·후"] == pooled["한 건·후"] and counts["묶음 8·후"] == pooled["묶음 8·후"], (counts, names)

    token = "integration-delivery-token-32-characters"
    server = service.serve(detector_url(url), root, token, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    payloads = [base.v04_job(conn, sid, f"delivery-threads-{index}")[1] for index in range(4)]
    delivered = [uuid.UUID(payload["jobId"]) for payload in payloads]
    conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=ANY(%s)", (delivered,))
    conn.commit()
    try:
        with ThreadStarts() as starts:
            for payload in payloads:
                body = json.dumps({"jobId": payload["jobId"], "inputManifestHash": payload["inputManifestHash"]}).encode()
                request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/detect", data=body,
                                                 method="POST", headers={"Authorization": "Bearer " + token})
                with urllib.request.urlopen(request, timeout=30) as response:
                    assert response.status == 202, response.status
    finally:
        server.shutdown()
        server.server_close()
    decided = conn.execute("SELECT count(*) FROM run_process_decisions WHERE job_id=ANY(%s)", (delivered,)).fetchone()[0]
    conn.commit()
    assert decided == 4, decided
    counts["직접 전달·후"], names["직접 전달·후"] = len(starts.names), starts.names
    # 직접 전달은 HTTP 서버가 요청마다 스레드 1개(ThreadingHTTPServer)이고 heartbeat 스레드는 더 만들지 않는다.
    assert counts["직접 전달·후"] - sum(1 for name in starts.names if name.startswith("detection-")) == 4, (counts, names)
    print("PASS 판정당 새 스레드(8건, 직접 전달 4건): " + ", ".join(f"{label} {counts[label]}" for label in counts)
          + f" — 이름 { {label: sorted(set(items)) for label, items in names.items()} }")


# 자식 프로세스 기록용 감싸기. 실제 명령(python -m detector.service --process-index 1)과 같은 모듈을 __main__으로 돌리기 전에
# 처리한 작업·첫 claim·정리 호출·긴 첫 시도를 기록 파일에 남긴다. 인자: 번호, 기록 파일, 첫 시도 지연(초), 갱신 간격(초).
PROBE = r"""
import json, os, runpy, sys, threading, time
from detector import attempt_v04, heartbeat, runner
import pg_queue
log_path, hold, interval = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
heartbeat.INTERVAL_SECONDS = interval
log = open(log_path, "a", buffering=1)
lock = threading.Lock()
claimed = set()
def note(*item):
    with lock:
        log.write(json.dumps([os.getpid(), threading.current_thread().name, *item]) + "\n")
process = runner._process_message
def recorded(conn, message, *args, **kwargs):
    note("process", message.payload.get("jobId"))
    return process(conn, message, *args, **kwargs)
runner._process_message = recorded
claim = runner._Loop._claim
def first_claim(self):
    if self.worker_id not in claimed:
        claimed.add(self.worker_id)
        note("claim")
    return claim(self)
runner._Loop._claim = first_claim
for name in ("expire_exhausted_leases", "reconcile_dead_jobs"):
    def wrapped(*args, _name=name, _original=getattr(pg_queue, name), **kwargs):
        note(_name)
        return _original(*args, **kwargs)
    setattr(pg_queue, name, wrapped)
telemetry = attempt_v04._telemetry
slow = []
def held(execution, observation, **kwargs):
    with lock:
        first = not slow
        slow.append(1)
    if first:
        note("slow", str(observation.observation_id))
        time.sleep(hold)
    return telemetry(execution, observation, **kwargs)
attempt_v04._telemetry = held
sys.argv = ["detector.service", "--process-index", sys.argv[1]]
runpy.run_module("detector.service", run_name="__main__", alter_sys=True)
"""
PROCESS_JOBS = 50
PROCESS_WORKERS = 2
HOLD_SECONDS = 1.5
RENEW_SECONDS = .3


def processes_with_workers(conn, url: str, directory: str, sid) -> None:
    """DETECTOR_PROCESSES 2 × DETECTOR_WORKERS 2. 0번은 이 프로세스(run_processes), 1번은 실제 자식 프로세스다."""
    log_path = Path(directory) / "probe-1.log"
    jobs = [job for job, _observation in new_jobs(conn, sid, "proc", PROCESS_JOBS)]
    # 자식 작업자가 claim을 시작할 때까지 묶어 둔다(자식 기동 중에 0번이 다 가져가지 않게).
    conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp()+interval '1 hour' WHERE id=ANY(%s)",
                 (jobs,))
    conn.commit()
    dead = exhausted_job(conn, sid)

    # 0번 프로세스(이 프로세스)에도 자식과 같은 기록·긴 첫 시도를 둔다.
    lock, main_log, slow = threading.Lock(), [], []
    originals = {"process": runner._process_message, "telemetry": attempt_v04._telemetry,
                 "expire_exhausted_leases": pg_queue.expire_exhausted_leases,
                 "reconcile_dead_jobs": pg_queue.reconcile_dead_jobs}

    def note(*item):
        with lock:
            main_log.append((os.getpid(), threading.current_thread().name, *item))

    def recorded(conn_, message, *args, **kwargs):
        note("process", message.payload.get("jobId"))
        return originals["process"](conn_, message, *args, **kwargs)

    def held(execution, observation, **kwargs):
        with lock:
            first = not slow
            slow.append(1)
        if first:
            note("slow", str(observation.observation_id))
            time.sleep(HOLD_SECONDS)
        return originals["telemetry"](execution, observation, **kwargs)

    def maintenance(name):
        def wrapped(*args, **kwargs):
            note(name)
            return originals[name](*args, **kwargs)
        return wrapped

    children: list = []

    def spawn(index):
        command = [sys.executable, "-c", PROBE, str(index), str(log_path), str(HOLD_SECONDS), str(RENEW_SECONDS)]
        children.append(service._spawn_child(index, command=command))
        return children[-1]

    stop, failure = threading.Event(), []

    def serve():
        try:
            service.run_processes(detector_url(url), Path(directory), processes=2, workers=PROCESS_WORKERS,
                                  batch_size=BATCH, poll_seconds=.05, stop=stop, spawn=spawn)
        except BaseException as exc:  # noqa: BLE001
            failure.append(exc)
            stop.set()

    environment = {"DETECTOR_WORKERS": str(PROCESS_WORKERS), "DETECTOR_BATCH_SIZE": str(BATCH),
                   "ARTIFACT_ROOT": directory, "DATABASE_URL": detector_url(url)}
    thread = threading.Thread(target=serve, name="run-processes")
    with patch.dict(os.environ, environment), patch.object(heartbeat, "INTERVAL_SECONDS", RENEW_SECONDS), \
            patch.object(runner, "_process_message", recorded), patch.object(attempt_v04, "_telemetry", held), \
            patch.object(pg_queue, "expire_exhausted_leases", maintenance("expire_exhausted_leases")), \
            patch.object(pg_queue, "reconcile_dead_jobs", maintenance("reconcile_dead_jobs")):
        started = time.monotonic()
        thread.start()
        try:
            limit = started + 120
            while True:
                lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
                if sum(1 for line in lines if json.loads(line)[2] == "claim") >= PROCESS_WORKERS:
                    break
                assert not stop.is_set() and time.monotonic() < limit, (failure, lines)
                time.sleep(.1)
            conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=ANY(%s)",
                         (jobs,))
            conn.commit()
            limit = time.monotonic() + 180
            while not stop.is_set():
                done, exhausted = conn.execute("""SELECT count(*) FILTER (WHERE id=ANY(%s) AND status='done'),
                    count(*) FILTER (WHERE id=%s AND status='dead') FROM powercode_queue.detection_logs""",
                                               (jobs, dead)).fetchone()
                conn.commit()
                if done == PROCESS_JOBS and exhausted == 1:
                    break
                assert time.monotonic() < limit, (done, exhausted)
                time.sleep(.1)
        finally:
            stop.set()
            thread.join(60)
    elapsed = time.monotonic() - started
    assert not thread.is_alive() and not failure, failure
    assert len(children) == 1 and [child.poll() for child in children] == [0], [child.poll() for child in children]
    print(f"PASS 프로세스 2 × 작업자 {PROCESS_WORKERS}가 {PROCESS_JOBS}건을 처리하고 정상 정지(자식 종료 코드 0, 남은 "
          f"프로세스 없음), {elapsed:.1f}초")

    entries = main_log + [tuple(json.loads(line)) for line in log_path.read_text(encoding="utf-8").splitlines()]
    wanted = {str(job) for job in jobs}
    processed = [(entry[0], entry[3]) for entry in entries if entry[2] == "process" and entry[3] in wanted]
    assert sorted(job for _pid, job in processed) == sorted(wanted), len(processed)
    by_process: dict = {}
    for pid, _job in processed:
        by_process[pid] = by_process.get(pid, 0) + 1
    assert len(by_process) == 2, by_process
    rows = conn.execute("""SELECT count(*), count(DISTINCT job_id), count(DISTINCT process_observation_id)
        FROM run_process_decisions WHERE job_id=ANY(%s)""", (jobs,)).fetchone()
    states = conn.execute("SELECT status::text, attempt_count, count(*) FROM run_jobs WHERE id=ANY(%s) GROUP BY 1,2",
                          (jobs,)).fetchall()
    causes = conn.execute("""SELECT m->>'cause', count(*) FROM run_process_decisions d,
        jsonb_array_elements(d.evidence->'methodResults') m WHERE d.job_id=ANY(%s) GROUP BY 1""", (jobs,)).fetchall()
    conn.commit()
    assert rows == (PROCESS_JOBS,) * 3, rows
    assert states == [("succeeded", 1, PROCESS_JOBS)], states
    # 슬롯은 프로세스마다(0번 작업자 + 1, 자식 작업자 수). 겹친 판정도 capacity가 없다(D20).
    assert causes and all(cause != "capacity" for cause, _count in causes), causes
    print(f"PASS 작업마다 판정 1건·성공 시도 1번·중복 없음, 처리 프로세스 2개 {sorted(by_process.values())}건, capacity 없음")

    owners = {(entry[0], entry[1]) for entry in entries if entry[2] in ("expire_exhausted_leases", "reconcile_dead_jobs")}
    assert owners == {(os.getpid(), "detector-worker-p0-0")}, owners
    dead_row = conn.execute("""SELECT q.status, j.status::text, j.error_code FROM powercode_queue.detection_logs q
        JOIN run_jobs j ON j.id=q.id WHERE q.id=%s""", (dead,)).fetchone()
    conn.commit()
    assert dead_row == ("dead", "failed", "queue_exhausted"), dead_row
    print("PASS 정리(dead 처리·대조)는 0번 프로세스 0번 작업자만, 시도를 다 쓴 큐 행은 dead → 작업 failed·queue_exhausted")

    slow_observations = [entry[3] for entry in entries if entry[2] == "slow"]
    renewed = conn.execute("""SELECT count(*) FILTER (WHERE a.heartbeat_at IS NOT NULL), count(*)
        FROM run_jobs j JOIN run_job_attempts a ON a.job_id=j.id
        WHERE j.id=ANY(%s) AND (j.input_spec->>'observationId')=ANY(%s)""", (jobs, slow_observations)).fetchone()
    conn.commit()
    assert len(slow_observations) == 2 and renewed == (2, 2), (slow_observations, renewed)
    print(f"PASS 긴 시도(프로세스마다 첫 시도 {HOLD_SECONDS}초, 갱신 간격 {RENEW_SECONDS}초) 2건 모두 임대 갱신(heartbeat_at)")


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
        threads_per_decision(conn, url, Path(directory), sid)
        processes_with_workers(conn, url, directory, sid)
    print("PASS integration_detector_workers")


if __name__ == "__main__":
    main()
