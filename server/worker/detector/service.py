"""Authenticated direct delivery claims the exact same persisted queue item as polling."""
import hmac
import json
import os
import threading
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import psycopg
from detector.policy import configure_capacity
from detector.runner import process_detection_message, run_workers, worker_count
from pg_queue import claim, fail


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


def serve(database_url, root, token, host="127.0.0.1", port=8011):
    if len(token) < 32:
        raise ValueError("detector_delivery_token_too_short")
    capacity = threading.BoundedSemaphore(1)
    # 직접 전달 연결 1개를 POST마다 다시 쓴다(POST마다 새 연결을 열지 않음). capacity를 쥔 동안에만 쓰므로 한 번에 한 요청만
    # 쓴다. 처음 쓸 때 열고, 연결 오류·끊김이면 닫고 다음 요청에서 새로 연다. 요청이 끝나면 열린 트랜잭션을 남기지 않는다.
    delivery = DeliveryConnection(database_url)
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
                        process_detection_message(conn,message,root);conn.commit()
                self.respond(202,{"status":"accepted"})
            except (ValueError,KeyError,TypeError):
                self.respond(400,{"code":"invalid_delivery"})
            except Exception:
                self.respond(503,{"code":"delivery_unavailable"})
            finally:
                capacity.release()
    return ThreadingHTTPServer((host,port),Handler)


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/".env")
    # 작업자 수는 HTTP 서버를 열기 전에 검사한다(잘못된 값이면 바로 실패).
    workers=worker_count(os.environ.get("DETECTOR_WORKERS"))
    # 직접 전달도 판정하므로 슬롯은 HTTP 서버를 열기 전에 맞춘다(run_workers의 같은 호출은 같은 값이라 그대로 둔다).
    configure_capacity(workers+1)
    database_url=os.environ["DATABASE_URL"]
    root=Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    server=serve(database_url,root,os.environ["DETECTOR_DELIVERY_TOKEN"],os.environ.get("DETECTOR_HOST","127.0.0.1"))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        # 작업자 하나가 죽으면 WorkerFailed가 올라와 HTTP 서버를 닫고 0이 아닌 코드로 끝난다(컨테이너 재시작).
        # 큐가 비었을 때 대기 0.1초(유휴 반복도 고정 단계 조회를 하므로 더 줄이면 빈 조회가 그만큼 는다).
        run_workers(database_url,root,workers=workers,poll_seconds=.1)
    finally:
        server.shutdown();server.server_close()

if __name__ == "__main__":
    main()
