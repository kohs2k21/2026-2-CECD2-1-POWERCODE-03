"""Detector 다중 작업자(run_workers): 작업자 수 해석, 0번만 정리, 적체 중 고정 단계 건너뛰기, 작업자 사망 시 종료.

DB 없이 가짜 연결로 본다. 실제 PostgreSQL 동시 처리는 integration_detector_workers.py가 본다.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_queue
from detector import applications, jobs, runner, service


class _FakeConn:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class WorkerCountTest(unittest.TestCase):
    def test_default_and_range(self) -> None:
        self.assertEqual(runner.worker_count(None), 4)
        self.assertEqual([runner.worker_count(v) for v in ("1", "4", "8")], [1, 4, 8])

    def test_invalid_values_fail(self) -> None:
        for value in ("0", "9", "", "abc", " 4", "4 ", "-1", "2.0", "٣"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_detector_workers"):
                runner.worker_count(value)

    def test_run_workers_rejects_out_of_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_detector_workers"):
            runner.run_workers("postgresql://unit-test", None, workers=0)


class _Harness:
    """claim·고정 단계·정리 호출을 기록한다. messages는 detect claim이 차례로 돌려줄 값(다 쓰면 None)."""

    def __init__(self, messages=()):
        self.lock = threading.Lock()
        self.messages = list(messages)
        self.calls: list[tuple] = []

    def record(self, *item) -> None:
        with self.lock:
            self.calls.append((threading.current_thread().name, *item))

    def claim(self, _conn, queue, **kwargs):
        self.record("claim", queue, kwargs.get("expire_exhausted", True), "job_kinds" in kwargs)
        if queue == "detection" and "job_kinds" in kwargs:
            with self.lock:
                return self.messages.pop(0) if self.messages else None
        return None

    def patches(self, stack: ExitStack) -> None:
        enter = stack.enter_context
        enter(patch.object(runner, "claim", self.claim))
        enter(patch.object(runner, "active_config", lambda _conn: self.record("active_config")))
        enter(patch.object(runner, "process_detection_message",
                           lambda _conn, message, _root: self.record("process", message)))
        enter(patch.object(applications, "prepare_active", lambda *_a: self.record("prepare")))
        enter(patch.object(applications, "process_application_once", lambda *_a: self.record("application")))
        enter(patch.object(jobs, "claim_waiting_input", lambda _conn: None))
        enter(patch.object(pg_queue, "reconcile_dead_jobs", lambda _conn, queue: self.record("reconcile", queue)))
        enter(patch.object(pg_queue, "expire_exhausted_leases", lambda _conn, queue: self.record("expire", queue)))

    def names(self, kind: str) -> list[str]:
        return [call[0] for call in self.calls if call[1] == kind]


class SkipFixedStepsTest(unittest.TestCase):
    def _iterate(self, loop: runner._Loop, harness: _Harness, clock: list[float]) -> list[float]:
        """clock 시각마다 반복 1회. 고정 단계(신청 claim)가 돈 시각 목록을 돌려준다."""
        fixed: list[float] = []
        for now in clock:
            before = len(harness.names("application"))
            with patch.object(runner, "time", SimpleNamespace(monotonic=lambda now=now: now)):
                loop.iterate()
            if len(harness.names("application")) > before:
                fixed.append(now)
        return fixed

    def test_busy_skips_until_interval_then_empty_runs_every_time(self) -> None:
        harness = _Harness(messages=["m1", "m2", "m3", "m4", "m5"])
        with ExitStack() as stack:
            harness.patches(stack)
            loop = runner._Loop(_FakeConn(), None, "w", reconcile=False, expire_in_claim=False,
                                fixed_refresh_seconds=1.0)
            # 메시지 5건(0.0~1.2) → 빈 반복 2번(1.3·1.4): 빈 반복 다음은 간격과 무관하게 매번.
            fixed = self._iterate(loop, harness, [0.0, 0.3, 0.6, 1.0, 1.2, 1.3, 1.4])
        self.assertEqual(fixed, [0.0, 1.0, 1.4])
        self.assertEqual(len(harness.names("process")), 5)
        self.assertEqual(len(harness.names("active_config")), 3)

    def test_single_loop_mode_runs_fixed_steps_every_iteration(self) -> None:
        harness = _Harness(messages=["m1", "m2", "m3"])
        with ExitStack() as stack:
            harness.patches(stack)
            loop = runner._Loop(_FakeConn(), None, "w")
            fixed = self._iterate(loop, harness, [0.0, 0.1, 0.2, 0.3])
        self.assertEqual(fixed, [0.0, 0.1, 0.2, 0.3])
        # run() 모드는 claim 안의 dead 처리를 그대로 한다.
        self.assertTrue(all(call[3] for call in harness.calls if call[1] == "claim"))


class RunWorkersTest(unittest.TestCase):
    def _run(self, harness: _Harness, *, workers: int, stop_after: float = .3, **kwargs) -> None:
        stop = threading.Event()
        timer = threading.Timer(stop_after, stop.set)
        with ExitStack() as stack:
            harness.patches(stack)
            stack.enter_context(patch.object(runner, "psycopg",
                                             SimpleNamespace(connect=lambda _url: nullcontext(_FakeConn()))))
            timer.start()
            try:
                runner.run_workers("postgresql://unit-test", None, workers=workers, poll_seconds=.01,
                                   stop=stop, join_seconds=5, **kwargs)
            finally:
                timer.cancel()

    def test_only_worker_zero_expires_and_reconciles(self) -> None:
        harness = _Harness()
        self._run(harness, workers=3, reconcile_seconds=.05)
        self.assertEqual(harness.names("prepare"), ["MainThread"])
        self.assertEqual(set(harness.names("reconcile")), {"detector-worker-0"})
        self.assertEqual(set(harness.names("expire")), {"detector-worker-0"})
        self.assertGreaterEqual(len(harness.names("reconcile")), 2)
        # 정리 직전마다 dead 처리
        maintenance = [call[1] for call in harness.calls if call[1] in ("expire", "reconcile")]
        self.assertEqual(maintenance[::2], ["expire"] * len(maintenance[::2]))
        self.assertEqual(maintenance[1::2], ["reconcile"] * len(maintenance[1::2]))
        self.assertEqual(set(harness.names("claim")),
                         {"detector-worker-0", "detector-worker-1", "detector-worker-2"})
        # detection claim은 모든 작업자가 dead 처리 UPDATE를 건너뛴다.
        detection = [call for call in harness.calls if call[1] == "claim" and call[2] == "detection"]
        self.assertTrue(detection and all(call[3] is False for call in detection))

    def test_messages_are_processed_by_several_workers(self) -> None:
        harness = _Harness(messages=[f"m{index}" for index in range(40)])
        original = harness.claim

        def slow_claim(*args, **kwargs):
            threading.Event().wait(.002)
            return original(*args, **kwargs)

        harness.claim = slow_claim
        self._run(harness, workers=4, stop_after=.5)
        processed = [call[2] for call in harness.calls if call[1] == "process"]
        self.assertEqual(sorted(processed), sorted(f"m{index}" for index in range(40)))
        self.assertGreater(len(set(harness.names("process"))), 1)

    def test_worker_exception_stops_all_and_raises(self) -> None:
        harness = _Harness()
        boom = RuntimeError("database_gone")

        def claim(_conn, queue, **kwargs):
            harness.record("claim", queue)
            if threading.current_thread().name == "detector-worker-1":
                raise boom
            return None

        harness.claim = claim
        with self.assertRaises(runner.WorkerFailed) as raised:
            self._run(harness, workers=3, stop_after=30)
        self.assertIs(raised.exception.__cause__, boom)
        self.assertIn("detector_worker_failed:1", str(raised.exception))
        alive = [thread.name for thread in threading.enumerate() if thread.name.startswith("detector-worker-")]
        self.assertEqual(alive, [])


class ServiceMainTest(unittest.TestCase):
    def _main(self, env: dict[str, str], run_workers) -> MagicMock:
        server = MagicMock()
        base = {"DATABASE_URL": "postgresql://unit-test", "DETECTOR_DELIVERY_TOKEN": "t" * 32}
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {**base, **env}, clear=False))
            enter(patch("dotenv.load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(service, "serve", MagicMock(return_value=server)))
            enter(patch.object(service, "run_workers", run_workers))
            service.main()
        return server

    def test_worker_failure_closes_http_server_and_propagates(self) -> None:
        server = MagicMock()

        def failing(*_a, **_k):
            raise runner.WorkerFailed("detector_worker_failed:0")

        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test",
                                          "DETECTOR_DELIVERY_TOKEN": "t" * 32, "DETECTOR_WORKERS": "2"}))
            enter(patch("dotenv.load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(service, "serve", MagicMock(return_value=server)))
            enter(patch.object(service, "run_workers", failing))
            with self.assertRaises(runner.WorkerFailed):
                service.main()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()

    def test_workers_env_is_passed(self) -> None:
        seen = {}
        self._main({"DETECTOR_WORKERS": "3"}, lambda *_a, **k: seen.update(k))
        self.assertEqual(seen["workers"], 3)

    def test_invalid_workers_fails_before_http_server(self) -> None:
        serve = MagicMock()
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test",
                                          "DETECTOR_DELIVERY_TOKEN": "t" * 32, "DETECTOR_WORKERS": "12"}))
            enter(patch("dotenv.load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(service, "serve", serve))
            with self.assertRaisesRegex(ValueError, "invalid_detector_workers"):
                service.main()
        serve.assert_not_called()


class ClaimExpireOptionTest(unittest.TestCase):
    class _Cursor:
        def __init__(self, sink):
            self.sink, self.rowcount = sink, 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=()):
            self.sink.append((" ".join(sql.split()), params))

        def fetchone(self):
            return None

    def _sql(self, **kwargs) -> list[str]:
        sink: list = []
        conn = SimpleNamespace(cursor=lambda: self._Cursor(sink))
        pg_queue.claim(conn, "detection", job_kinds=("detect",), **kwargs)
        return [sql for sql, _ in sink]

    def test_default_keeps_dead_update(self) -> None:
        statements = self._sql()
        self.assertEqual(len(statements), 2)
        self.assertIn("SET status = 'dead'", statements[0])

    def test_skip_dead_update_keeps_picked_guard(self) -> None:
        statements = self._sql(expire_exhausted=False)
        self.assertEqual(len(statements), 1)
        self.assertNotIn("'dead'", statements[0])
        # 시도를 다 쓴 행은 picked 조건(attempts < max)에서 이미 빠진다.
        self.assertIn("lease_until <= clock_timestamp() AND attempts < %s", statements[0])

    def test_expire_exhausted_leases_is_unscoped_same_update(self) -> None:
        sink: list = []
        conn = SimpleNamespace(cursor=lambda: self._Cursor(sink))
        pg_queue.expire_exhausted_leases(conn, "detection")
        (sql, params), = sink
        self.assertIn("UPDATE powercode_queue.detection_logs SET status = 'dead'", sql)
        self.assertTrue(sql.endswith("AND attempts >= %s"))
        self.assertEqual(params, (3,))


if __name__ == "__main__":
    unittest.main()
