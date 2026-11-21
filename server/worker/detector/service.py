"""Authenticated direct delivery claims the exact same persisted queue item as polling."""
import hmac
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import psycopg
from detector.runner import process_detection_message, run_workers, worker_count
from pg_queue import claim, fail


def serve(database_url, root, token, host="127.0.0.1", port=8011):
    if len(token) < 32:
        raise ValueError("detector_delivery_token_too_short")
    capacity = threading.BoundedSemaphore(1)
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
                with psycopg.connect(database_url) as conn:
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
    database_url=os.environ["DATABASE_URL"]
    root=Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    server=serve(database_url,root,os.environ["DETECTOR_DELIVERY_TOKEN"],os.environ.get("DETECTOR_HOST","127.0.0.1"))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        # 작업자 하나가 죽으면 WorkerFailed가 올라와 HTTP 서버를 닫고 0이 아닌 코드로 끝난다(컨테이너 재시작).
        run_workers(database_url,root,workers=workers,poll_seconds=.25)
    finally:
        server.shutdown();server.server_close()

if __name__ == "__main__":
    main()
