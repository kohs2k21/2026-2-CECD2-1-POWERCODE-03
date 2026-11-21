"""v04 성공 저장 묶음(detector/finish.py)·측정 행 순서·직접 전달 연결 재사용의 DB 없는 시험."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector import attempt_v04, finish, service
from pg_queue import ClaimedMessage, acknowledge_statement


class _Cursor:
    def __init__(self, conn: "_PipelineConn") -> None:
        self.conn, self.rowcount = conn, -1

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self.conn.log.append((sql, params, self.conn.in_pipeline, self))


class _PipelineConn:
    """pipeline 블록을 나올 때(Sync) 결과가 도착하는 것을 흉내 낸다. 그 전 rowcount는 psycopg처럼 -1이다."""

    def __init__(self, ack_rows: int = 1, sync_error: BaseException | None = None) -> None:
        self.ack_rows, self.sync_error = ack_rows, sync_error
        self.in_pipeline, self.pipelines = False, 0
        self.log: list[tuple] = []
        self.cursors: list[_Cursor] = []

    def cursor(self) -> _Cursor:
        cursor = _Cursor(self)
        self.cursors.append(cursor)
        return cursor

    @contextmanager
    def pipeline(self):
        self.in_pipeline, self.pipelines = True, self.pipelines + 1
        try:
            yield self
        finally:
            self.in_pipeline = False
        if self.sync_error is not None:
            raise self.sync_error
        for _sql, _params, _piped, cursor in self.log:
            cursor.rowcount = 1
        self.log[-1][3].rowcount = self.ack_rows


def _message() -> ClaimedMessage:
    return ClaimedMessage(uuid.uuid4(), {}, 1, uuid.uuid4(), None, "detection")


class FinishDetectSuccess(unittest.TestCase):
    def setUp(self) -> None:
        self.message, self.job, self.attempt = _message(), {"id": uuid.uuid4(), "kind": "detect"}, uuid.uuid4()
        self.result = {"status": "complete", "executionCost": {"x": 1}, "checkers": {}}
        self.before = [("INSERT measurement", (1,)), ("INSERT decision", (2,))]

    def test_one_pipeline_in_order_with_trainer_finish_values(self) -> None:
        conn = _PipelineConn()
        finish.finish_detect_success(conn, self.message, self.job, self.attempt, self.result, self.before)
        self.assertEqual(conn.pipelines, 1)
        self.assertTrue(all(piped for _sql, _params, piped, _cursor in conn.log), "모든 문장이 pipeline 안")
        statements = [(sql, params) for sql, params, _piped, _cursor in conn.log]
        ack = acknowledge_statement("detection", self.message)
        self.assertEqual(statements, [
            *self.before,
            (finish.ATTEMPT_SUCCEEDED, ("succeeded", None, self.attempt)),
            (finish.JOB_SUCCEEDED, (json.dumps(self.result), None, None, self.job["id"])),
            ack,
        ])
        # 두 번째 소유권 확인·savepoint가 없다
        self.assertFalse(any("FOR UPDATE" in sql or "SAVEPOINT" in sql for sql, _params in statements))
        self.assertIn("lease_until > clock_timestamp()", ack[0])

    def test_same_statements_as_trainer_finish_success(self) -> None:
        """trainer `_finish` 성공 분기의 시도·작업 UPDATE 문장과 같은 텍스트(공백 제외)."""
        source = (Path(__file__).resolve().parents[1] / "trainer" / "runner.py").read_text(encoding="utf-8")
        squash = lambda text: " ".join(text.split())  # noqa: E731
        trainer = squash(source)
        self.assertIn(squash(finish.ATTEMPT_SUCCEEDED), trainer)
        self.assertIn(squash(finish.JOB_SUCCEEDED), trainer)

    def test_ack_zero_rows_raises_after_sync(self) -> None:
        conn = _PipelineConn(ack_rows=0)
        with self.assertRaisesRegex(RuntimeError, "^training_queue_lease_lost$"):
            finish.finish_detect_success(conn, self.message, self.job, self.attempt, self.result, self.before)
        self.assertEqual(conn.pipelines, 1)

    def test_pipeline_error_propagates(self) -> None:
        error = psycopg.errors.RaiseException("result requires current owner and unexpired lease")
        conn = _PipelineConn(sync_error=error)
        with self.assertRaises(psycopg.errors.RaiseException) as raised:
            finish.finish_detect_success(conn, self.message, self.job, self.attempt, self.result, self.before)
        self.assertIs(raised.exception, error)


class _PlainCursor:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, *_args) -> None:
        return None

    def fetchone(self):
        return self.rows.pop(0)


class _AttemptConn:
    def __init__(self) -> None:
        self.rows = [("content-hash",), ("polling", "dataset")]
        self.commits = 0

    def cursor(self) -> _PlainCursor:
        return _PlainCursor(self.rows)

    def transaction(self):
        return nullcontext()

    def commit(self) -> None:
        self.commits += 1


class MeasurementBeforeDecision(unittest.TestCase):
    """S1: detector 측정 행 INSERT가 판정 INSERT(잠금 81736)보다 먼저, 같은 묶음으로 간다."""

    def _run(self, recheck):
        captured = {}

        def fake_finish(conn, message, job, attempt_id, result, before):
            captured.update(result=result, before=list(before))

        state = SimpleNamespace(policy=None, revisions=None, hard_rules=None)
        relation = SimpleNamespace(hard_ids=(), evidence_fields=lambda: {})
        execution = SimpleNamespace(methods=[], measured={})
        columns = {key: None for key in ("rule_status", "ml_status", "decision_code", "anomaly_score")}
        columns["status"] = "complete"
        observation = SimpleNamespace(observation_id=uuid.uuid4(), execution_id=uuid.uuid4(), source_id=uuid.uuid4(),
                                      allowed_values={}, revision_no=1)
        fixed = ({}, None, uuid.uuid4(), "manifest-hash", datetime(2026, 11, 22, tzinfo=timezone.utc))
        patches = {
            "recheck_reference": lambda *_a: recheck, "pin_operational_state": lambda *_a: state,
            "load_models": lambda *_a: [], "needs_relation_inputs": lambda *_a: False,
            "plan_relations": lambda *_a: relation, "InterimRecorder": MagicMock(),
            "judge_row": lambda *_a, **_k: SimpleNamespace(execution=execution),
            "_telemetry": lambda *_a, **_k: {"status": "measured"},
            "build_evidence": lambda *_a, **_k: {"checkers": {"hr": {}}},
            "decision_columns": lambda *_a: columns, "lock_training_ownership": lambda *_a: True,
            "decision_source_time": lambda *_a, **_k: None, "finish_detect_success": fake_finish,
            "record_judgment_store": MagicMock(return_value=True),
        }
        with patch.multiple(attempt_v04, **patches):
            attempt_v04.run_attempt(_AttemptConn(), _message(), {"id": uuid.uuid4()}, uuid.uuid4(), uuid.uuid4(),
                                    observation, fixed, None, started=0.0, queue_wait=0.0,
                                    heartbeat=SimpleNamespace(lost=False))
            judgment = patches["record_judgment_store"]
        return captured, judgment

    def test_first_decision_measurement_first(self) -> None:
        captured, judgment = self._run(None)
        sqls = [sql for sql, _params in captured["before"]]
        self.assertEqual(len(sqls), 2)
        self.assertIn("INSERT INTO ops_pipeline_measurements", sqls[0])
        self.assertIn("'detector'", sqls[0])
        self.assertIn("INSERT INTO run_process_decisions", sqls[1])
        self.assertGreaterEqual(captured["before"][0][1][2], 0)
        self.assertEqual(captured["result"], {"status": "complete", "executionCost": {"status": "measured"},
                                              "checkers": {"hr": {}}})
        judgment.assert_called_once()

    def test_recheck_has_no_measurement(self) -> None:
        captured, judgment = self._run({"recheckedDecisionId": str(uuid.uuid4())})
        sqls = [sql for sql, _params in captured["before"]]
        self.assertEqual(len(sqls), 1)
        self.assertIn("INSERT INTO run_process_decisions", sqls[0])
        judgment.assert_not_called()


class _DeliveryCursor:
    def __init__(self, conn: "_DeliveryConn") -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, *_args) -> None:
        if self.conn.fail is not None:
            self.conn.broken = isinstance(self.conn.fail, psycopg.OperationalError)
            raise self.conn.fail

    def fetchone(self):
        return ({"inputManifestHash": "h"},)


class _DeliveryConn:
    def __init__(self) -> None:
        self.closed = self.broken = False
        self.fail: BaseException | None = None
        self.commits = self.rollbacks = 0

    def cursor(self) -> _DeliveryCursor:
        return _DeliveryCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class DeliveryConnectionReuse(unittest.TestCase):
    def setUp(self) -> None:
        self.opened: list[_DeliveryConn] = []

        def connect(_url):
            conn = _DeliveryConn()
            self.opened.append(conn)
            return conn

        self.delivery = service.DeliveryConnection("postgresql://unit-test", connect=connect)

    def test_lazy_and_reused(self) -> None:
        self.assertEqual(self.opened, [])
        for _ in range(3):
            with self.delivery.use() as conn:
                conn.cursor().execute("SELECT 1")
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0].commits, 3)  # 예전 connect 블록처럼 정상 종료는 COMMIT, 열린 트랜잭션 없음
        self.assertFalse(self.opened[0].closed)

    def test_value_error_rolls_back_and_keeps_connection(self) -> None:
        with self.assertRaises(ValueError):
            with self.delivery.use():
                raise ValueError("invalid_delivery")
        with self.delivery.use():
            pass
        self.assertEqual(len(self.opened), 1)
        self.assertEqual((self.opened[0].rollbacks, self.opened[0].commits), (1, 1))

    def test_operational_error_reconnects_next_time(self) -> None:
        with self.assertRaises(psycopg.OperationalError):
            with self.delivery.use() as conn:
                conn.fail = psycopg.OperationalError("server closed the connection unexpectedly")
                conn.cursor().execute("SELECT 1")
        self.assertTrue(self.opened[0].closed)
        with self.delivery.use() as conn:
            conn.cursor().execute("SELECT 1")
        self.assertEqual(len(self.opened), 2)
        self.assertFalse(self.opened[1].closed)

    def test_broken_connection_is_replaced(self) -> None:
        with self.delivery.use():
            pass
        self.opened[0].broken = True
        with self.delivery.use():
            pass
        self.assertEqual(len(self.opened), 2)
        self.assertTrue(self.opened[0].closed)

    def test_rollback_failure_closes(self) -> None:
        def broken_rollback():
            raise psycopg.InterfaceError("connection already closed")

        with self.assertRaises(KeyError):
            with self.delivery.use() as conn:
                conn.rollback = broken_rollback
                raise KeyError("jobId")
        self.assertTrue(self.opened[0].closed)
        with self.delivery.use():
            pass
        self.assertEqual(len(self.opened), 2)


TOKEN = "t" * 32


class DeliveryHttp(unittest.TestCase):
    """POST /detect 응답 코드는 그대로이고 연결은 요청마다 새로 열지 않는다."""

    def setUp(self) -> None:
        self.opened: list[_DeliveryConn] = []

        def connect(*_args, **_kwargs):
            conn = _DeliveryConn()
            self.opened.append(conn)
            return conn

        self.claimed: list = []
        stack = [patch.object(service.psycopg, "connect", connect),
                 patch.object(service, "claim", lambda *_a, **_k: self.claimed.pop() if self.claimed else None),
                 patch.object(service, "process_detection_message", MagicMock())]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)
        self.server = service.serve("postgresql://unit-test", None, TOKEN, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/detect"

    def post(self, body: dict) -> tuple[int, dict]:
        data = json.dumps(body).encode()
        request = urllib.request.Request(self.url, data=data, method="POST",
                                         headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_responses_and_reuse(self) -> None:
        body = {"jobId": str(uuid.uuid4()), "inputManifestHash": "h"}
        # 가져갈 것이 없는 POST(대부분) 두 번 + 가져간 POST 한 번: 연결 1개
        self.assertEqual(self.post(body), (202, {"status": "accepted"}))
        self.assertEqual(self.post(body), (202, {"status": "accepted"}))
        self.claimed.append(object())
        self.assertEqual(self.post(body), (202, {"status": "accepted"}))
        service.process_detection_message.assert_called_once()
        self.assertEqual(self.post({**body, "inputManifestHash": "other"}), (400, {"code": "invalid_delivery"}))
        self.assertEqual(len(self.opened), 1)
        # 연결 오류는 503, 다음 요청은 새 연결로 처리
        self.opened[0].fail = psycopg.OperationalError("terminating connection")
        self.assertEqual(self.post(body), (503, {"code": "delivery_unavailable"}))
        self.assertEqual(self.post(body), (202, {"status": "accepted"}))
        self.assertEqual(len(self.opened), 2)
        self.assertTrue(self.opened[0].closed)


if __name__ == "__main__":
    unittest.main()
