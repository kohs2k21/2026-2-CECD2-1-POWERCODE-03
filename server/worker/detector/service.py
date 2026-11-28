"""Authenticated direct delivery claims the exact same persisted queue item as polling."""
import argparse
import hmac
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import psycopg
from detector.heartbeat import SharedLeaseHeartbeat
from detector.policy import configure_capacity
from detector.runner import batch_size, process_detection_message, run_workers, worker_count
from pg_queue import claim, fail

LOGGER = logging.getLogger(__name__)
# DETECTOR_PROCESSES(판정 프로세스 수) 기본값과 허용 범위. 1이면 지금처럼 한 프로세스다.
DEFAULT_PROCESSES = 1
MAX_PROCESSES = 8
# 여러 프로세스일 때 정지 요청(SIGTERM 등)부터 0번 작업자 join과 자식 정지를 모두 끝낼 기한. docker stop 기본 유예 10초 안에
# HTTP 서버 닫기(약 0.5초)·직접 전달 갱신 스레드 정지(최대 1초)까지 끝나도록 7초로 둔다.
STOP_SECONDS = 7.0
# 자식이 기한 - 2 × CHILD_KILL_SECONDS까지 스스로 끝나지 않으면 SIGTERM, 기한 - CHILD_KILL_SECONDS까지도 남으면 SIGKILL이다.
CHILD_KILL_SECONDS = 1.0
# 기한이 이미 지났어도 SIGTERM·SIGKILL 뒤 이만큼은 기다린다(올림이 늘 일어나고 wait로 거둔다).
STOP_FLOOR_SECONDS = 0.2


class DeliveryConnection:
    """직접 전달이 다시 쓰는 연결 1개. 호출자(serve의 capacity)가 동시에 한 요청만 use()하도록 보장한다.

    - 처음 use()할 때 연다. 닫혔거나 끊긴(broken) 연결이면 새로 연다.
    - 끝날 때는 예전 `with psycopg.connect(...)`와 같이 정상 종료면 COMMIT, 예외면 ROLLBACK해 열린 트랜잭션을 남기지 않는다
      (본문이 매번 COMMIT하므로 보통 남은 트랜잭션이 없다). 연결 닫기만 하지 않는다.
    - 연결 오류(psycopg.OperationalError·InterfaceError)·끊긴 연결이거나 COMMIT·ROLLBACK이 실패하면 연결을 닫아 다음
      요청에서 새로 연다. 본문 예외와 COMMIT 실패는 그대로 올려 응답 코드를 지금과 같게 둔다.
    """

    def __init__(self, database_url, connect=None):
        self.database_url, self.connect = database_url, connect
        self.conn = None

    def close(self):
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def use(self):
        if self.conn is None or self.conn.closed or self.conn.broken:
            self.close()
            self.conn = (self.connect or psycopg.connect)(self.database_url)
        conn = self.conn
        try:
            yield conn
        except BaseException as exc:
            if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)) or conn.closed or conn.broken:
                self.close()
            else:
                try:
                    conn.rollback()
                except Exception:
                    self.close()
            raise
        try:
            conn.commit()
        except BaseException:
            self.close()
            raise


class _DeliveryServer(ThreadingHTTPServer):
    """직접 전달 HTTP 서버. 닫을 때(server_close) 직접 전달의 임대 갱신 스레드도 멈춘다(최대 1초 대기).

    ThreadingHTTPServer는 요청 스레드가 daemon(`daemon_threads=True`)이라 server_close가 처리 중인 요청을 기다리지 않는다.
    그래서 종료 중에 처리 중이던 직접 전달 시도는 갱신 등록이 닫혀 `lost`가 되거나(저장 전 확인에서 detection_lease_lost로
    시도 실패 기록) 프로세스와 함께 끊긴다. 어느 쪽이든 판정은 저장되지 않고 그 작업은 큐 재시도(시도 실패·임대 만료)로
    다시 처리된다."""

    heartbeats = None

    def server_close(self):
        super().server_close()
        if self.heartbeats is not None:
            self.heartbeats.close(timeout=1.0)


def serve(database_url, root, token, host="127.0.0.1", port=8011):
    if len(token) < 32:
        raise ValueError("detector_delivery_token_too_short")
    capacity = threading.BoundedSemaphore(1)
    # 직접 전달 연결 1개를 POST마다 다시 쓴다(POST마다 새 연결을 열지 않음). capacity를 쥔 동안에만 쓰므로 한 번에 한 요청만
    # 쓴다. 처음 쓸 때 열고, 연결 오류·끊김이면 닫고 다음 요청에서 새로 연다. 요청이 끝나면 열린 트랜잭션을 남기지 않는다.
    delivery = DeliveryConnection(database_url)
    # 직접 전달 시도의 임대 갱신도 POST마다 스레드를 띄우지 않고 서버의 갱신 스레드 1개가 맡는다(작업자 루프와 같은 방식).
    heartbeats = SharedLeaseHeartbeat(lambda: psycopg.connect(database_url, autocommit=True),
                                      name="detector-heartbeat-delivery")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self,status,value):
            body=json.dumps(value).encode()
            self.send_response(status);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path != "/health":
                self.respond(404,{"code":"not_found"});return
            try:
                with psycopg.connect(database_url,connect_timeout=3) as conn:
                    with conn.cursor() as cur:cur.execute("SELECT 1")
                self.respond(200,{"status":"ready"})
            except Exception:self.respond(503,{"status":"database_unavailable"})
        def do_POST(self):
            if self.path != "/detect" or not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
                self.respond(401,{"code":"unauthorized"});return
            if not capacity.acquire(blocking=False):
                self.respond(202,{"status":"queued"});return
            message=None
            try:
                self.connection.settimeout(5)
                length=int(self.headers.get("Content-Length","0"))
                if not 0 < length <= 4096:
                    raise ValueError("invalid_delivery")
                payload=json.loads(self.rfile.read(length));job_id=uuid.UUID(payload["jobId"])
                with delivery.use() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT input_spec FROM run_jobs WHERE id=%s AND kind='detect'",(job_id,))
                        row=cur.fetchone()
                        if row is None or row[0].get("inputManifestHash") != payload.get("inputManifestHash"):
                            raise ValueError("invalid_delivery")
                    message=claim(conn,"detection",message_id=job_id)
                    conn.commit()
                    if message:
                        process_detection_message(conn,message,root,heartbeats=heartbeats);conn.commit()
                self.respond(202,{"status":"accepted"})
            except (ValueError,KeyError,TypeError):
                self.respond(400,{"code":"invalid_delivery"})
            except Exception:
                self.respond(503,{"code":"delivery_unavailable"})
            finally:
                capacity.release()
    try:
        server = _DeliveryServer((host,port),Handler)
    except BaseException:
        heartbeats.close()
        raise
    server.heartbeats = heartbeats
    return server


def process_count(value):
    """DETECTOR_PROCESSES 값. 없으면 DEFAULT_PROCESSES, 1~MAX_PROCESSES 정수 문자열이 아니면 시작 실패(ValueError)."""
    if value is None:
        return DEFAULT_PROCESSES
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= MAX_PROCESSES:
        raise ValueError(f"invalid_detector_processes: 1~{MAX_PROCESSES} 정수여야 한다")
    return int(value)


class ProcessFailed(RuntimeError):
    """자식 판정 프로세스 하나가 정지 요청 없이 끝났다. 나머지를 멈춘 뒤 올려 서비스를 실패로 끝낸다(컨테이너 재시작)."""


def _child_command(index):
    return [sys.executable, "-m", "detector.service", "--process-index", str(index)]


def _spawn_child(index, command=None):
    """자식 판정 프로세스 1개(같은 모듈을 --process-index로 다시 실행). 표준 입력은 부모 쪽 파이프다.

    부모가 그 파이프를 닫거나(정지 요청) 부모가 어떤 이유로든 죽으면(커널이 닫음) 자식은 파이프 끝(EOF)을 보고 스스로
    멈춘다. 표준 출력·오류는 부모 것을 그대로 쓰므로 로그가 컨테이너 로그로 간다. 환경 변수는 그대로 물려받는다."""
    worker_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(item for item in (str(worker_dir), env.get("PYTHONPATH")) if item)
    return subprocess.Popen(command or _child_command(index), stdin=subprocess.PIPE, cwd=str(worker_dir), env=env)


class _Children:
    """자식 프로세스 목록과 정지 요청(표준 입력 닫기). 여러 스레드가 불러도 파이프는 한 번만 닫는다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = []  # Popen(또는 같은 모양의 가짜)

    def add(self, child):
        with self.lock:
            self.items.append(child)

    def snapshot(self):
        with self.lock:
            return list(self.items)

    def request_stop(self):
        with self.lock:
            for child in self.items:
                stdin = getattr(child, "stdin", None)
                if stdin is None or stdin.closed:
                    continue
                try:
                    stdin.close()
                except Exception:  # noqa: BLE001 - 이미 끝난 자식의 파이프
                    pass

    def stop(self, deadline):
        """정지를 알리고(이미 알렸으면 그대로) 기한(time.monotonic) 하나 안에서 끝낸다.

        기한 - 2 × CHILD_KILL_SECONDS까지 스스로 끝나기를 기다리고, 남으면 SIGTERM 뒤 기한 - CHILD_KILL_SECONDS까지,
        그래도 남으면 SIGKILL 뒤 기한까지 기다린다. 기한이 이미 지났으면 단계마다 STOP_FLOOR_SECONDS만 기다린다(올림은 늘
        한다). 그래서 이 함수가 끝나는 때는 max(기한, 부른 때) + 2 × STOP_FLOOR_SECONDS 이하다. 모두 wait로 거둔다."""
        self.request_stop()
        children = self.snapshot()
        self._wait(children, deadline - 2 * CHILD_KILL_SECONDS)
        for action, until in (("terminate", deadline - CHILD_KILL_SECONDS), ("kill", deadline)):
            alive = [child for child in children if child.poll() is None]
            if not alive:
                return
            LOGGER.error("detector processes did not stop in time, %s: %s", action,
                         [getattr(child, "pid", None) for child in alive])
            for child in alive:
                try:
                    getattr(child, action)()
                except OSError:  # 그 사이에 끝났다
                    pass
            self._wait(alive, max(until, time.monotonic() + STOP_FLOOR_SECONDS))

    @staticmethod
    def _wait(children, until):
        for child in children:
            try:
                child.wait(max(0.0, until - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass


def run_processes(database_url, root, *, processes, workers, batch_size=1, poll_seconds=.1, stop=None,
                  join_seconds=STOP_SECONDS, spawn=_spawn_child):
    """판정 프로세스 processes개 × 작업자 workers개. 이 프로세스(0번)와 자식 processes - 1개가 같은 큐를 나눠 가진다.

    - 자식은 `python -m detector.service --process-index <번호>`(subprocess)로 띄운다. 자식마다 run_workers(작업자 workers개,
      연결 따로, 슬롯 workers개 — 직접 전달이 없어 +1이 없다, 정리 안 함)다. 0번(이 프로세스)은 run_workers(슬롯
      workers + 1, 0번 작업자가 서비스 전체의 유일한 정리 담당)다. 직접 전달 HTTP 서버는 0번에만 있다(main).
    - prepare_active는 프로세스마다 run_workers가 작업자를 띄우기 전에 한 번 한다. DB는 읽기만 하고(활성 구성·모델 행)
      산출물 파일 해시 확인·적재는 그 프로세스 메모리에만 하므로 여러 프로세스가 동시에 해도 된다.
    - 자식 하나가 정지 요청 없이 끝나면(종료 코드 무관) stop을 세워 0번 작업자를 멈추고 나머지 자식을 멈춘 뒤
      ProcessFailed를 올린다. 0번 작업자가 실패하면(WorkerFailed) 자식을 멈춘 뒤 그 예외를 올린다. stop이 밖에서
      세워지면(SIGTERM) 같은 방식으로 멈추고 정상 반환한다.
    - 정지: 기한은 하나다 — stop이 처음 선 때 + join_seconds(기본 STOP_SECONDS 7초). stop이 서는 즉시(0번 작업자에게
      멈추라고 하는 때와 같은 때) 자식에게 알리고(표준 입력 닫기), 0번 작업자 join(run_workers, 최대 join_seconds)과 자식
      정지가 같은 기한을 쓴다. 자식이 기한 - 2초까지 남으면 SIGTERM, 기한 - 1초까지 남으면 SIGKILL하고 모두 wait한다(좀비·
      고아 없음). 전체는 정지 요청 뒤 약 join_seconds + 2 × STOP_FLOOR_SECONDS 안에 끝난다. spawn은 시험이 가짜 프로세스를
      넣는 자리다.
    """
    if type(processes) is not int or not 2 <= processes <= MAX_PROCESSES:
        raise ValueError(f"invalid_detector_processes: 2~{MAX_PROCESSES} 정수여야 한다")
    stop = stop or threading.Event()
    children = _Children()
    failures = []

    def watch(index, child):
        code = child.wait()
        if not stop.is_set():
            LOGGER.error("detector process %s exited without a stop request: code %s", index, code)
            failures.append((index, code))
            stop.set()

    stop_lock, stopped_at = threading.Lock(), []

    def stop_requested_at():
        """stop이 처음 선 시각(정지 기한의 기준). 처음 부른 때를 남긴다."""
        with stop_lock:
            if not stopped_at:
                stopped_at.append(time.monotonic())
            return stopped_at[0]

    def relay():
        stop.wait()
        stop_requested_at()
        children.request_stop()

    # 자식을 띄우기 전에 둔다(띄우는 중에 stop이 서도 바로 알린다. 그 뒤에 더해진 자식은 finally의 stop이 알린다).
    threading.Thread(target=relay, name="detector-process-stop", daemon=True).start()
    try:
        for index in range(1, processes):
            if stop.is_set():
                break
            child = spawn(index)
            children.add(child)
            threading.Thread(target=watch, args=(index, child), name=f"detector-process-{index}-watch",
                             daemon=True).start()
        run_workers(database_url, root, workers=workers, poll_seconds=poll_seconds, batch_size=batch_size,
                    stop=stop, join_seconds=join_seconds, process_index=0)
    finally:
        stop.set()
        children.stop(stop_requested_at() + join_seconds)
    if failures:
        index, code = failures[0]
        raise ProcessFailed(f"detector_process_failed:{index}:{code}")


def _stop_on_signals(stop):
    """SIGTERM(docker stop)·SIGINT를 받으면 stop을 세운다(작업자는 현재 반복을 끝내고 멈춘다). 메인 스레드에서만 부른다."""
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_args: stop.set())


def _watch_parent(stop, stream=None):
    """부모가 표준 입력을 닫거나(정지 요청) 죽으면 파이프 끝(EOF)이다. 그때 stop을 세운다."""
    stream = stream or sys.stdin.buffer
    try:
        while stream.read(4096):
            pass
    except Exception:  # noqa: BLE001 - 읽을 수 없으면 부모와 이어져 있지 않은 것으로 보고 멈춘다
        pass
    stop.set()


def child_main(index):
    """자식 판정 프로세스(run_processes가 띄움). 직접 전달 서버 없이 run_workers만 돈다.

    부모와 같은 환경 변수를 다시 검사한다. 정지 요청(표준 입력 EOF)·SIGTERM이면 정상 종료(0), 작업자 실패면 WorkerFailed로
    0이 아닌 코드다(부모가 보고 서비스 전체를 멈춘다)."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    if not 1 <= index < MAX_PROCESSES:
        raise ValueError(f"invalid_detector_process_index: 1~{MAX_PROCESSES - 1}")
    workers=worker_count(os.environ.get("DETECTOR_WORKERS"))
    batch=batch_size(os.environ.get("DETECTOR_BATCH_SIZE"))
    database_url=os.environ["DATABASE_URL"]
    root=Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    stop=threading.Event()
    _stop_on_signals(stop)
    threading.Thread(target=_watch_parent,args=(stop,),name="detector-parent-watch",daemon=True).start()
    run_workers(database_url,root,workers=workers,poll_seconds=.1,batch_size=batch,stop=stop,
                capacity=workers,cleanup=False,process_index=index)


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    # 작업자 수는 HTTP 서버를 열기 전에 검사한다(잘못된 값이면 바로 실패).
    workers=worker_count(os.environ.get("DETECTOR_WORKERS"))
    # 묶음 크기(DETECTOR_BATCH_SIZE, 기본 8)도 같은 때 검사한다. 직접 전달은 늘 한 건이다.
    batch=batch_size(os.environ.get("DETECTOR_BATCH_SIZE"))
    # 프로세스 수(DETECTOR_PROCESSES, 기본 1)도 같은 때 검사한다. 1이면 지금처럼 이 프로세스 하나다.
    processes=process_count(os.environ.get("DETECTOR_PROCESSES"))
    # 직접 전달도 판정하므로 슬롯은 HTTP 서버를 열기 전에 맞춘다(run_workers의 같은 호출은 같은 값이라 그대로 둔다).
    # 슬롯은 프로세스마다 따로다. 직접 전달 서버는 이 프로세스(0번)에만 있으므로 자식은 작업자 수만큼이다(run_processes).
    configure_capacity(workers+1)
    database_url=os.environ["DATABASE_URL"]
    root=Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    server=serve(database_url,root,os.environ["DETECTOR_DELIVERY_TOKEN"],os.environ.get("DETECTOR_HOST","127.0.0.1"))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        # 작업자 하나가 죽으면 WorkerFailed가 올라와 HTTP 서버를 닫고 0이 아닌 코드로 끝난다(컨테이너 재시작).
        # 큐가 비었을 때 대기 0.1초(유휴 반복도 고정 단계 조회를 하므로 더 줄이면 빈 조회가 그만큼 는다).
        if processes == 1:
            run_workers(database_url,root,workers=workers,poll_seconds=.1,batch_size=batch)
        else:
            # 자식 하나가 끝나면 ProcessFailed, 작업자가 죽으면 WorkerFailed로 같이 0이 아닌 코드다. SIGTERM이면 자식까지
            # 멈추고 기다린 뒤 정상 종료한다.
            stop=threading.Event()
            _stop_on_signals(stop)
            run_processes(database_url,root,processes=processes,workers=workers,batch_size=batch,poll_seconds=.1,
                          stop=stop)
    finally:
        server.shutdown();server.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-index", type=int, default=None,
                        help="자식 판정 프로세스 번호(run_processes가 넘긴다). 없으면 서비스 본체(main)")
    arguments = parser.parse_args()
    if arguments.process_index is None:
        main()
    else:
        child_main(arguments.process_index)
