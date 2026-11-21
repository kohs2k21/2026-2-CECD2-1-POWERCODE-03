"""Detector 판정 루프의 dead 큐 정리 주기: 첫 반복 1회, 간격 안 0회, 간격 경과 뒤 다시 1회."""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_queue
from detector import applications, jobs, runner


class _StopLoop(Exception):
    pass


class _TooManyIterations(AssertionError):
    """반복 시각을 읽지 않는 회귀에서 무한 루프 대신 실패한다."""


class _FakeConn:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class ReconcileIntervalTest(unittest.TestCase):
    def _run(self, *, clock: list[float], once: bool) -> list[float]:
        """메시지 없는 반복을 clock 길이만큼 돌리고, 정리가 불린 시각 목록을 돌려준다."""
        ticks = iter(clock)
        now = {"t": None}
        calls: list[float] = []
        sleeps = {"n": 0}

        def monotonic() -> float:
            now["t"] = next(ticks)
            return now["t"]

        def sleep(_seconds: float) -> None:
            sleeps["n"] += 1
            if sleeps["n"] > len(clock):
                raise _TooManyIterations(f"sleep {sleeps['n']}회 > 시계 틱 {len(clock)}개")
            if now["t"] == clock[-1]:
                raise _StopLoop

        def reconcile(_conn: object, queue: str) -> None:
            self.assertEqual(queue, "detection")
            calls.append(now["t"])

        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test"}))
            enter(patch.object(runner, "load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(runner, "psycopg", SimpleNamespace(connect=lambda _url: nullcontext(_FakeConn()))))
            enter(patch.object(runner, "time", SimpleNamespace(monotonic=monotonic, sleep=sleep)))
            enter(patch.object(runner, "active_config", lambda _conn: None))
            enter(patch.object(runner, "claim", lambda *_a, **_k: None))
            enter(patch.object(applications, "prepare_active", lambda *_a: None))
            enter(patch.object(applications, "process_application_once", lambda *_a: None))
            enter(patch.object(jobs, "claim_waiting_input", lambda _conn: None))
            enter(patch.object(pg_queue, "reconcile_dead_jobs", reconcile))
            if once:
                runner.run(once=True)
            else:
                with self.assertRaises(_StopLoop):
                    runner.run()
        return calls

    def test_first_iteration_then_only_after_interval(self) -> None:
        calls = self._run(clock=[100.0, 110.0, 129.9, 130.0, 145.0, 159.9, 160.0], once=False)
        self.assertEqual(calls, [100.0, 130.0, 160.0])

    def test_once_still_reconciles(self) -> None:
        self.assertEqual(self._run(clock=[5.0], once=True), [5.0])

    def test_default_interval_is_module_constant(self) -> None:
        self.assertEqual(runner.RECONCILE_SECONDS, 30.0)


if __name__ == "__main__":
    unittest.main()
