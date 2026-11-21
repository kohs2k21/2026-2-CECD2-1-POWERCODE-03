"""Detector 묶음 claim·묶음 저장(DETECTOR_BATCH_SIZE)의 DB 없는 시험.

실제 PostgreSQL 동시 처리·묶음 실패 뒤 한 건씩 저장은 integration_detector_workers.py가 본다.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_queue
from detector import attempt_v04, finish, runner, service
from pg_queue import ClaimedMessage, acknowledge_statement


def _message(created: int = 0) -> ClaimedMessage:
    return ClaimedMessage(uuid.uuid4(), {"schemaVersion": 1, "jobId": str(uuid.uuid4())}, 1, uuid.uuid4(), created,
                          "detection")


# ---------------------------------------------------------------- claim_many

class _ClaimCursor:
    def __init__(self, sink: list, rows: list) -> None:
        self.sink, self.rows, self.rowcount = sink, rows, 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=()):
        self.sink.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class ClaimManyTest(unittest.TestCase):
    def _run(self, rows, **kwargs):
        sink: list = []
        conn = SimpleNamespace(cursor=lambda: _ClaimCursor(sink, rows))
        return pg_queue.claim_many(conn, "detection", job_kinds=("detect",), **kwargs), sink

    def test_one_statement_limit_and_per_row_token(self) -> None:
        messages, sink = self._run([], limit=8, expire_exhausted=False)
        self.assertEqual(messages, [])
        (sql, params), = sink  # 한 문장, 비어도 다시 묻지 않는다(채우려고 기다리지 않음)
        self.assertIn("FOR UPDATE SKIP LOCKED LIMIT %s", sql)
        self.assertIn("lease_token = gen_random_uuid()", sql)
        self.assertIn("lease_until <= clock_timestamp() AND attempts < %s", sql)
        self.assertIn("ORDER BY available_at, created_at", sql)
        self.assertEqual((params[0], params[-2], params[-1]), (3, 8, 60))

    def test_returns_at_most_limit_in_claim_order(self) -> None:
        t = datetime(2026, 11, 22, tzinfo=timezone.utc)
        ids = [uuid.UUID(int=value) for value in (3, 1, 2)]
        # RETURNING 순서는 보장되지 않으므로 (available_at, created_at, id)로 다시 정렬한다.
        rows = [(ids[0], {}, 1, uuid.uuid4(), t, t), (ids[1], {}, 1, uuid.uuid4(), t, t),
                (ids[2], {}, 1, uuid.uuid4(), t.replace(year=2025), t)]
        messages, _sink = self._run(rows, limit=3)
        self.assertEqual([message.id for message in messages], [ids[2], ids[1], ids[0]])
        self.assertTrue(all(message.queue == "detection" for message in messages))

    def test_expire_default_and_limit_range(self) -> None:
        _messages, sink = self._run([], limit=2)
        self.assertEqual(len(sink), 2)
        self.assertIn("SET status = 'dead'", sink[0][0])
        for limit in (0, 65, True, 2.0):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self._run([], limit=limit)

    def test_single_claim_statement_unchanged(self) -> None:
        sink: list = []
        conn = SimpleNamespace(cursor=lambda: _ClaimCursor(sink, []))
        self.assertIsNone(pg_queue.claim(conn, "detection", job_kinds=("detect",), expire_exhausted=False))
        (sql, params), = sink
        self.assertIn("FOR UPDATE SKIP LOCKED LIMIT 1 )", sql)
        self.assertIn("lease_token = %s", sql)
        self.assertNotIn("gen_random_uuid", sql)
        self.assertEqual(len(params), 4)  # max_attempts, job_kinds, token, lease_seconds
        self.assertIsInstance(params[2], uuid.UUID)


# ---------------------------------------------------------------- DETECTOR_BATCH_SIZE

class BatchSizeTest(unittest.TestCase):
    def test_default_and_range(self) -> None:
        self.assertEqual(runner.batch_size(None), 8)
        self.assertEqual([runner.batch_size(v) for v in ("1", "8", "64")], [1, 8, 64])

    def test_invalid_values_fail(self) -> None:
        for value in ("0", "65", "", "abc", " 8", "8 ", "-1", "2.0", "٣"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_detector_batch_size"):
                runner.batch_size(value)

    def test_run_workers_and_loop_reject_out_of_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_detector_batch_size"):
            runner.run_workers("postgresql://unit-test", None, workers=1, batch_size=0)
        with self.assertRaisesRegex(ValueError, "invalid_detector_batch_size"):
            runner._Loop(object(), None, "w", batch_size=65)

    def _main(self, env: dict[str, str]):
        seen, serve = {}, MagicMock(return_value=MagicMock())
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test",
                                          "DETECTOR_DELIVERY_TOKEN": "t" * 32, **env}))
            if "DETECTOR_BATCH_SIZE" not in env:
                os.environ.pop("DETECTOR_BATCH_SIZE", None)
            enter(patch("dotenv.load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(service, "serve", serve))
            enter(patch.object(service, "run_workers", lambda *_a, **k: seen.update(k)))
            enter(patch.object(service, "configure_capacity", lambda _n: None))
            service.main()
        return seen, serve

    def test_service_passes_batch_size(self) -> None:
        self.assertEqual(self._main({})[0]["batch_size"], 8)
        self.assertEqual(self._main({"DETECTOR_BATCH_SIZE": "1"})[0]["batch_size"], 1)

    def test_invalid_batch_size_fails_before_http_server(self) -> None:
        serve = MagicMock()
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test",
                                          "DETECTOR_DELIVERY_TOKEN": "t" * 32, "DETECTOR_BATCH_SIZE": "0"}))
            enter(patch("dotenv.load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(service, "serve", serve))
            with self.assertRaisesRegex(ValueError, "invalid_detector_batch_size"):
                service.main()
        serve.assert_not_called()


# ---------------------------------------------------------------- _Loop 묶음 claim

class _FakeConn:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class LoopBatchClaimTest(unittest.TestCase):
    def _iterate(self, batch_size: int, available: list):
        calls: list = []

        def claim_many(_conn, queue, *, limit, **kwargs):
            calls.append(("claim_many", limit, kwargs))
            taken = available[:limit]
            del available[:limit]
            return taken

        def claim(_conn, queue, **kwargs):
            calls.append(("claim", kwargs))
            return available.pop(0) if available and "job_kinds" in kwargs else None

        loop = runner._Loop(_FakeConn(), None, "w", reconcile=False, expire_in_claim=False,
                            fixed_refresh_seconds=1.0, batch_size=batch_size)
        loop.source_id, loop.last_fixed, loop.busy = "src", 0.0, True
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(runner, "claim_many", claim_many))
            enter(patch.object(runner, "claim", claim))
            enter(patch("detector.jobs.claim_waiting_input", lambda _conn: calls.append(("waiting",))))
            enter(patch.object(runner, "process_detection_batch",
                               lambda _conn, messages, _root: calls.append(("batch", list(messages)))))
            enter(patch.object(runner, "process_detection_message",
                               lambda _conn, message, _root: calls.append(("single", message))))
            enter(patch.object(runner, "time", SimpleNamespace(monotonic=lambda: 0.5)))
            processed = loop.iterate()
        return processed, calls

    def test_takes_what_is_available_without_waiting(self) -> None:
        processed, calls = self._iterate(8, ["m1", "m2", "m3"])
        self.assertTrue(processed)
        self.assertEqual(calls[0][:2], ("claim_many", 8))
        self.assertEqual(calls[0][2], {"job_kinds": ("detect",), "expire_exhausted": False})
        self.assertEqual(calls[1:], [("batch", ["m1", "m2", "m3"])])  # 한 번만 묻고 바로 처리

    def test_one_message_takes_single_path(self) -> None:
        _processed, calls = self._iterate(8, ["m1"])
        self.assertEqual([call[0] for call in calls], ["claim_many", "single"])

    def test_empty_falls_back_to_single_claims(self) -> None:
        processed, calls = self._iterate(8, [])
        self.assertFalse(processed)
        self.assertEqual([call[0] for call in calls], ["claim_many", "claim", "waiting"])
        self.assertEqual(calls[1][1], {"source_id": "src", "expire_exhausted": False})  # 원천 범위는 한 건

    def test_batch_size_one_uses_claim(self) -> None:
        _processed, calls = self._iterate(1, ["m1", "m2"])
        self.assertEqual([call[0] for call in calls], ["claim", "single"])


# ---------------------------------------------------------------- finish_detect_batch·save_batch

class _Cursor:
    def __init__(self, conn: "_PipelineConn") -> None:
        self.conn, self.rowcount, self.result = conn, -1, None

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self.conn.log.append((sql, params, self.conn.in_pipeline, self))
        self.conn.stages.append(self.conn.pipelines)

    def fetchone(self):
        assert not self.conn.in_pipeline, "pipeline 안에서 결과를 읽지 않는다"
        return self.result

    def close(self) -> None:
        pass


class _PipelineConn:
    """pipeline 블록을 나올 때(Sync) 결과가 도착하는 것을 흉내 낸다. owners_missing·ack_zero는 몇 번째 건을 어긋나게 할지."""

    def __init__(self, *, owners_missing=(), ack_zero=(), sync_error=None) -> None:
        self.owners_missing, self.ack_zero, self.sync_error = set(owners_missing), set(ack_zero), sync_error
        self.in_pipeline, self.pipelines = False, 0
        self.log: list[tuple] = []
        self.stages: list[int] = []  # 문장마다 보낸 pipeline 번호(1부터)
        self.events: list[str] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def pipeline(self):
        self.in_pipeline, self.pipelines = True, self.pipelines + 1
        try:
            yield self
        finally:
            self.in_pipeline = False
        if self.sync_error is not None:
            raise self.sync_error
        owner = ack = 0
        for sql, _params, _piped, cursor in self.log:
            cursor.rowcount = 1
            if sql.lstrip().startswith("SELECT 1"):
                cursor.result = None if owner in self.owners_missing else (1,)
                owner += 1
            elif "SET status = 'done'" in sql:
                cursor.rowcount = 0 if ack in self.ack_zero else 1
                ack += 1

    @contextmanager
    def transaction(self):
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        self.events.append("commit")

    def commit(self) -> None:
        self.events.append("commit()")

    def rollback(self) -> None:
        self.events.append("rollback()")

    def execute(self, sql, params=None) -> None:
        self.log.append((sql, params, self.in_pipeline, None))
        self.stages.append(self.pipelines)


def _batch_item(index: int) -> finish.BatchItem:
    message = _message()
    return finish.BatchItem(message, {"id": uuid.uuid4()}, uuid.uuid4(), uuid.uuid4(),
                            [(f"INSERT feature {index}", (index,))] if index == 0 else [],
                            (f"INSERT measurement {index}", (index,)), (f"INSERT decision {index}", (index,)),
                            {"status": "complete", "executionCost": {"i": index}, "checkers": {}})


class FinishDetectBatchTest(unittest.TestCase):
    def test_statement_order_two_pipelines(self) -> None:
        items = [_batch_item(index) for index in range(3)]
        conn = _PipelineConn()
        finish.finish_detect_batch(conn, items)
        self.assertEqual(conn.pipelines, 2)
        self.assertTrue(all(piped for _sql, _params, piped, _cursor in conn.log), "모든 문장이 pipeline 안")
        # 첫 묶음 = 소유권 3 + 캐시 1 + 측정 3(잠금 81736 전), 둘째 묶음 = 판정·시도·작업·ack 건마다 4
        self.assertEqual(conn.stages, [1] * 7 + [2] * 12)
        statements = [(" ".join(sql.split()), params) for sql, params, _piped, _cursor in conn.log]
        owner = " ".join(finish.OWNERSHIP_LOCK.format(queue_table="powercode_queue.detection_logs").split())
        expected = [(owner, (item.message.id, item.job["id"], item.attempt_id, item.attempt_token,
                             item.message.lease_token)) for item in items]
        expected += [("INSERT feature 0", (0,))]
        expected += [(f"INSERT measurement {index}", (index,)) for index in range(3)]
        for index, item in enumerate(items):
            ack_sql, ack_params = acknowledge_statement("detection", item.message)
            expected += [(f"INSERT decision {index}", (index,)),
                         (" ".join(finish.ATTEMPT_SUCCEEDED.split()), ("succeeded", None, item.attempt_id)),
                         (" ".join(finish.JOB_SUCCEEDED.split()), (json.dumps(item.result), None, None, item.job["id"])),
                         (" ".join(ack_sql.split()), ack_params)]
        self.assertEqual(statements, expected)

    def test_ownership_statement_matches_lease(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "trainer" / "lease.py").read_text(encoding="utf-8")
        squash = lambda text: " ".join(text.split())  # noqa: E731
        self.assertIn(squash(finish.OWNERSHIP_LOCK.format(queue_table="{_table(message.queue)}")), squash(source))

    def test_missing_owner_rejects_before_any_decision_insert(self) -> None:
        conn = _PipelineConn(owners_missing={1})
        with self.assertRaisesRegex(finish.BatchRejected, "detection_lease_lost"):
            finish.finish_detect_batch(conn, [_batch_item(index) for index in range(3)])
        self.assertEqual(conn.pipelines, 1)  # 둘째 묶음(판정 INSERT)을 보내지 않았다
        sent = [sql for sql, _params, _piped, _cursor in conn.log]
        self.assertFalse(any("decision" in sql or "SET status = 'done'" in sql or sql == finish.JOB_SUCCEEDED
                             for sql in sent), sent)

    def test_zero_ack_rejects_after_second_pipeline(self) -> None:
        conn = _PipelineConn(ack_zero={2})
        with self.assertRaisesRegex(finish.BatchRejected, "training_queue_lease_lost"):
            finish.finish_detect_batch(conn, [_batch_item(index) for index in range(3)])
        self.assertEqual(conn.pipelines, 2)


def _pending(index: int, *, recheck=None) -> attempt_v04.PendingSave:
    observation = SimpleNamespace(observation_id=uuid.uuid4(), execution_id=uuid.uuid4(), source_id=uuid.uuid4(),
                                  revision_no=1)
    fixed = ({}, None, uuid.uuid4(), f"hash-{index}", datetime(2026, 11, 22, tzinfo=timezone.utc))
    telemetry = {"status": "measured", "details": {"batchSize": 1}}
    evidence = {"checkers": {"hr": {}}, "telemetry": telemetry}
    columns = {"status": "complete", "rule_status": None, "ml_status": None, "decision_code": None,
               "anomaly_score": None}
    return attempt_v04.PendingSave(_message(), {"id": uuid.uuid4()}, uuid.uuid4(), uuid.uuid4(), observation, fixed,
                                   0.0, SimpleNamespace(measured={}), "content", recheck, telemetry, evidence, columns,
                                   MagicMock(lost=False))


class SaveBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(attempt_v04, "decision_source_time", lambda *_a, **_k: None))
        self.stores = stack.enter_context(patch.object(attempt_v04, "record_judgment_stores",
                                                       MagicMock(return_value=True)))

    def test_one_transaction_one_commit_and_batch_size_recorded(self) -> None:
        pendings = [_pending(index) for index in range(3)]
        conn = _PipelineConn()
        self.assertTrue(attempt_v04.save_batch(conn, pendings))
        self.assertEqual(conn.events, ["begin", "commit", "commit()"])
        self.assertEqual(conn.pipelines, 2)  # 소유권·캐시·측정 / 판정·완료·ack
        decisions = [params for sql, params, _p, _c in conn.log if "INSERT INTO run_process_decisions" in sql]
        self.assertEqual([params[3] for params in decisions], ["hash-0", "hash-1", "hash-2"])  # 임대 순서
        for params in decisions:
            evidence = json.loads(params[10])
            self.assertEqual(evidence["telemetry"]["details"][attempt_v04.STORE_BATCH_KEY], 3)
            self.assertEqual(evidence["telemetry"]["details"]["batchSize"], 1)  # 처리량 행 수는 그대로
        jobs = [json.loads(params[0]) for sql, params, _p, _c in conn.log if sql == finish.JOB_SUCCEEDED]
        self.assertEqual([job["executionCost"]["details"][attempt_v04.STORE_BATCH_KEY] for job in jobs], [3, 3, 3])
        (args, _kwargs), = self.stores.call_args_list
        self.assertEqual(args[1], [item.observation.observation_id for item in pendings])
        self.assertGreaterEqual(args[2], 0)

    def test_failure_rolls_back_everything_and_returns_false(self) -> None:
        for conn in (_PipelineConn(sync_error=psycopg.errors.RaiseException("rejected")),
                     _PipelineConn(owners_missing={0}), _PipelineConn(ack_zero={1})):
            with self.subTest(conn=conn):
                with self.assertLogs(attempt_v04.LOGGER, "WARNING") as logs:
                    self.assertFalse(attempt_v04.save_batch(conn, [_pending(index) for index in range(2)]))
                self.assertEqual(conn.events, ["begin", "rollback", "rollback()"])
                # DB 오류·확인 실패는 경고 한 줄(traceback 없음)
                self.assertEqual([(record.levelname, record.exc_info) for record in logs.records], [("WARNING", None)])
        self.stores.assert_not_called()

    def test_unexpected_exception_is_logged_with_traceback_and_falls_back(self) -> None:
        conn, bug = _PipelineConn(), TypeError("bug in batch path")
        with patch.object(attempt_v04, "finish_detect_batch", MagicMock(side_effect=bug)), \
                self.assertLogs(attempt_v04.LOGGER, "WARNING") as logs:
            self.assertFalse(attempt_v04.save_batch(conn, [_pending(index) for index in range(2)]))
        (record,) = logs.records
        self.assertEqual(record.levelname, "ERROR")
        self.assertIs(record.exc_info[1], bug)
        self.assertEqual(conn.events, ["begin", "rollback", "rollback()"])
        self.stores.assert_not_called()

    def test_single_save_drops_batch_key(self) -> None:
        pending = _pending(0)
        pending.telemetry["details"][attempt_v04.STORE_BATCH_KEY] = 2  # 묶음 실패 뒤 한 건 저장
        captured = {}
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(attempt_v04, "lock_training_ownership", lambda *_a: True))
            enter(patch.object(attempt_v04, "finish_detect_success",
                               lambda _c, _m, _j, _a, result, before: captured.update(result=result, before=before)))
            enter(patch.object(attempt_v04, "record_judgment_store", MagicMock(return_value=True)))
            attempt_v04.save_attempt(_PipelineConn(), pending)
        self.assertNotIn(attempt_v04.STORE_BATCH_KEY, captured["result"]["executionCost"]["details"])
        evidence = json.loads(captured["before"][1][1][10])
        self.assertNotIn(attempt_v04.STORE_BATCH_KEY, evidence["telemetry"]["details"])

    def test_rejects_recheck_or_single(self) -> None:
        with self.assertRaises(ValueError):
            attempt_v04.save_batch(_PipelineConn(), [_pending(0)])
        with self.assertRaises(ValueError):
            attempt_v04.save_batch(_PipelineConn(), [_pending(0), _pending(1, recheck={"x": 1})])


class _MeasureConn:
    def __init__(self, fail=None) -> None:
        self.fail, self.statements = fail, []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None) -> None:
        if self.fail is not None and "INSERT" in sql:
            raise self.fail
        self.statements.append((" ".join(sql.split()), params))


class RecordJudgmentStoresTest(unittest.TestCase):
    def test_one_async_transaction_same_duration(self) -> None:
        conn, ids = _MeasureConn(), [uuid.uuid4(), uuid.uuid4()]
        self.assertTrue(attempt_v04.record_judgment_stores(conn, ids, 7.5))
        (setting, _), (insert, params) = conn.statements
        self.assertEqual(setting, "SET LOCAL synchronous_commit = off")
        self.assertIn("'judgment_store',NULL,%s", insert)
        self.assertEqual((params[0], params[2]), (7.5, ids))
        self.assertEqual(len(set(params[1])), 2)

    def test_db_error_is_logged_only(self) -> None:
        with self.assertLogs(attempt_v04.LOGGER, "WARNING"):
            self.assertFalse(attempt_v04.record_judgment_stores(_MeasureConn(psycopg.errors.CheckViolation("x")),
                                                                [uuid.uuid4()], 1.0))
        with self.assertRaises(TypeError):
            attempt_v04.record_judgment_stores(_MeasureConn(TypeError("bug")), [uuid.uuid4()], 1.0)


# ---------------------------------------------------------------- process_detection_batch

class ProcessBatchTest(unittest.TestCase):
    """_process_message는 가짜로 바꿔 묶음에 넣을 건·한 건 경로로 끝낼 건·실패할 건을 정한다."""

    def _run(self, plan: dict, *, batch_ok=True, save_errors=(), save_batch_error=None, renew=None, lost=()):
        messages = [_message(index) for index in range(len(plan))]
        kinds = dict(zip((message.id for message in messages), plan.values()))
        names = dict(zip((message.id for message in messages), plan.keys()))
        log: list = []
        pendings: dict = {}

        def process(_conn, message, _root, pending):
            kind = kinds[message.id]
            log.append(("process", names[message.id]))
            if kind == "fail":
                raise ValueError("rule_set_hash_mismatch")
            if kind == "defer":
                name = names[message.id]
                heartbeat = MagicMock(lost=name in lost)
                heartbeat.stop.side_effect = lambda name=name: log.append(("stop", name))
                item = SimpleNamespace(message=message, job={"id": uuid.uuid4(), "contractVersion": "v04"},
                                       attempt_id=uuid.uuid4(), attempt_token=uuid.uuid4(), name=name,
                                       heartbeat=heartbeat)
                pendings[names[message.id]] = item
                pending.append(item)

        def save_batch(_conn, items):
            log.append(("save_batch", [item.name for item in items]))
            if save_batch_error is not None:
                raise save_batch_error
            return batch_ok

        def save_attempt(_conn, item):
            log.append(("save_attempt", item.name))
            if item.name in save_errors:
                raise psycopg.errors.RaiseException("rejected")

        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(runner, "_process_message", process))
            enter(patch.object(attempt_v04, "save_batch", save_batch))
            enter(patch.object(attempt_v04, "save_attempt", save_attempt))
            enter(patch.object(runner, "_record_attempt_failure",
                               lambda _c, message, *_a: log.append(("attempt_failed", names[message.id], type(_a[-1]).__name__))))
            enter(patch.object(runner, "fail", lambda _c, _q, message, reason: log.append(("fail", names[message.id], reason))))
            if renew is not None:
                enter(patch.object(runner, "renew_lease", lambda _c, _q, message: names[message.id] not in renew))
            kwargs = {"renew_seconds": 0.0} if renew is not None else {}
            conn = SimpleNamespace(commit=lambda: None, rollback=lambda: log.append(("rollback",)))
            try:
                runner.process_detection_batch(conn, messages, None, **kwargs)
            finally:
                self.pendings, self.last_log = pendings, log
        return log

    def _stopped(self) -> list[str]:
        return [name for name, item in self.pendings.items() if item.heartbeat.stop.call_count >= 1]

    @staticmethod
    def _steps(log: list) -> list:
        """heartbeat 정지·ROLLBACK을 뺀 처리 순서."""
        return [entry for entry in log if entry[0] not in ("stop", "rollback")]

    def test_batch_success(self) -> None:
        log = self._run({"a": "defer", "b": "defer", "c": "defer"})
        self.assertEqual(log, [("process", "a"), ("process", "b"), ("process", "c"), ("save_batch", ["a", "b", "c"]),
                               ("stop", "a"), ("stop", "b"), ("stop", "c")])
        self.assertEqual(self._stopped(), ["a", "b", "c"])

    def test_non_eligible_and_failures_stay_single(self) -> None:
        # "single" = 한 건 경로로 끝난 건(v03·재판정·옛 메시지): 묶음에 없다. "fail" = 저장 전 실패: 그 건만 큐 fail.
        log = self._steps(self._run({"a": "defer", "b": "single", "c": "fail", "d": "defer"}))
        self.assertEqual(log, [("process", "a"), ("process", "b"), ("process", "c"),
                               ("fail", "c", "rule_set_hash_mismatch"), ("process", "d"), ("save_batch", ["a", "d"])])
        self.assertEqual(self._stopped(), ["a", "d"])

    def test_one_pending_uses_single_save(self) -> None:
        log = self._steps(self._run({"a": "defer", "b": "single"}))
        self.assertEqual(log[-1], ("save_attempt", "a"))
        self.assertNotIn("save_batch", [entry[0] for entry in log])
        self.assertEqual(self._stopped(), ["a"])

    def test_batch_failure_saves_one_by_one_and_fails_only_bad_item(self) -> None:
        log = self._run({"a": "defer", "b": "defer", "c": "defer"}, batch_ok=False, save_errors={"b"})
        # 실패한 건은 한 건 경로와 같은 순서: 시도 실패 기록 → 그 건의 heartbeat 정지 → (ROLLBACK →) 큐 fail
        self.assertEqual(log[3:], [("save_batch", ["a", "b", "c"]), ("save_attempt", "a"), ("save_attempt", "b"),
                                   ("attempt_failed", "b", "RaiseException"), ("stop", "b"), ("rollback",),
                                   ("fail", "b", "detection_failed"), ("save_attempt", "c"),
                                   ("stop", "a"), ("stop", "b"), ("stop", "c")])
        self.assertEqual(self._stopped(), ["a", "b", "c"])

    def test_lost_heartbeat_fails_before_batch(self) -> None:
        log = self._steps(self._run({"a": "defer", "b": "defer", "c": "defer"}, lost={"b"}))
        self.assertEqual(log[3:], [("attempt_failed", "b", "RuntimeError"), ("fail", "b", "detection_failed"),
                                   ("save_batch", ["a", "c"])])
        self.assertEqual(self._stopped(), ["a", "b", "c"])

    def test_heartbeats_stopped_when_save_raises(self) -> None:
        # 예외로 나갈 때 행 잠금을 먼저 풀고(ROLLBACK) heartbeat를 멈춘다(정지 join이 그 행 잠금을 기다리지 않게).
        with self.assertRaises(KeyboardInterrupt):
            self._run({"a": "defer", "b": "defer"}, save_batch_error=KeyboardInterrupt())
        self.assertEqual(self._stopped(), ["a", "b"])
        tail = [entry for entry in self.last_log if entry[0] in ("rollback", "stop")]
        self.assertEqual(tail, [("rollback",), ("stop", "a"), ("stop", "b")])

    def test_waiting_leases_renewed_and_lost_ones_dropped(self) -> None:
        log = self._steps(self._run({"a": "defer", "b": "defer", "c": "defer"}, renew={"b"}))
        # 처음 건부터 renew_seconds(0)마다 남은 건을 연장한다. b는 연장 실패라 시작하지 않는다(시도 없음 — 할 일 없음).
        self.assertEqual(log, [("process", "a"), ("process", "c"), ("save_batch", ["a", "c"])])


# ---------------------------------------------------------------- _process_message 자격

class _FixedCursor:
    def __init__(self, row) -> None:
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, *_args) -> None:
        return None

    def fetchone(self):
        return self.row


class ProcessMessageEligibilityTest(unittest.TestCase):
    def _run(self, *, contract: str, recheck=None, pending: list | None):
        message = _message()
        payload = message.payload
        fixed = ({"sourceId": None, "observationId": None, "configVersionId": None, "inputManifestHash": None,
                  "inputManifestId": None}, None, uuid.uuid4(), "h", datetime(2026, 11, 22, tzinfo=timezone.utc), None)
        job = {"id": uuid.UUID(payload["jobId"]), "contractVersion": contract}
        heartbeat = MagicMock(lost=False)
        prepared = SimpleNamespace(recheck=recheck)
        calls = MagicMock()
        conn = SimpleNamespace(cursor=lambda: _FixedCursor(fixed), commit=lambda: None, rollback=lambda: None)
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(runner, "load_process_observation", lambda *_a: SimpleNamespace(source_id=None)))
            enter(patch("trainer.runner._claim_job", lambda *_a: (job, uuid.uuid4(), uuid.uuid4())))
            enter(patch("trainer.runner._finish", calls.finish))
            enter(patch.object(runner, "queue_wait_ms", lambda *_a: 0.0))
            enter(patch("trainer.lease.TrainingLeaseHeartbeat", lambda *_a, **_k: heartbeat))
            enter(patch.object(attempt_v04, "run_attempt", calls.run_attempt))
            enter(patch.object(attempt_v04, "prepare_attempt", MagicMock(return_value=prepared)))
            enter(patch.object(attempt_v04, "save_attempt", calls.save_attempt))
            enter(patch("detector.computation.load_context", MagicMock(side_effect=ValueError("v03_path"))))
            try:
                runner._process_message(conn, message, None, pending)
            except ValueError as exc:
                calls.raised(str(exc))
        return calls, heartbeat, prepared

    def test_v04_first_decision_joins_batch_and_keeps_heartbeat(self) -> None:
        pending: list = []
        calls, heartbeat, prepared = self._run(contract="v04", pending=pending)
        self.assertEqual(pending, [prepared])
        heartbeat.stop.assert_not_called()
        calls.save_attempt.assert_not_called()
        calls.run_attempt.assert_not_called()

    def test_v04_recheck_saves_alone(self) -> None:
        pending: list = []
        calls, heartbeat, prepared = self._run(contract="v04", recheck={"recheckedDecisionId": "x"}, pending=pending)
        self.assertEqual(pending, [])
        calls.save_attempt.assert_called_once()
        heartbeat.stop.assert_called_once()

    def test_single_path_unchanged(self) -> None:
        calls, heartbeat, _prepared = self._run(contract="v04", pending=None)
        calls.run_attempt.assert_called_once()
        heartbeat.stop.assert_called_once()

    def test_v03_never_joins_batch(self) -> None:
        pending: list = []
        calls, heartbeat, _prepared = self._run(contract="v03", pending=pending)
        self.assertEqual(pending, [])
        calls.raised.assert_called_once_with("v03_path")  # v03 경로로 갔다(load_context)
        calls.finish.assert_called_once()  # 시도 실패 기록은 한 건 경로와 같다
        self.assertEqual(calls.finish.call_args.args[6], "detection_processing_failed")
        heartbeat.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
