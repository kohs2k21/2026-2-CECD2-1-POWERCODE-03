"""Detector 다중 프로세스(DETECTOR_PROCESSES, service.run_processes)의 DB 없는 시험.

프로세스 수 해석, 1이면 지금 경로 그대로, 정리 담당 1개, 자식 종료 시 서비스 종료와 나머지 정리를 가짜 프로세스로 본다.
정지 → SIGTERM → SIGKILL은 작은 실제 자식 프로세스로도 본다. 실제 PostgreSQL 2 × 2는 integration_detector_workers.py가 본다.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import io
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import policy, runner, service
from test_detector_workers import _FakeConn, _Harness


class ProcessCountTest(unittest.TestCase):
    def test_default_and_range(self) -> None:
        self.assertEqual(service.process_count(None), 1)
        self.assertEqual([service.process_count(v) for v in ("1", "2", "8")], [1, 2, 8])

    def test_invalid_values_fail(self) -> None:
        for value in ("0", "9", "", "abc", " 2", "2 ", "-1", "2.0", "٣"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_detector_processes"):
                service.process_count(value)

    def test_run_processes_needs_two_or_more(self) -> None:
        for value in (1, 9, "2"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_detector_processes"):
                service.run_processes("postgresql://unit-test", None, processes=value, workers=2,
                                      spawn=MagicMock(side_effect=AssertionError("spawned")))


class _FakeStdin:
    def __init__(self, child: "_FakeChild") -> None:
        self.child, self.closed = child, False

    def close(self) -> None:
        if not self.closed:
            self.closed_at = time.monotonic()
        self.closed = True
        if self.child.obeys_stop:
            self.child.exit(0)


class _FakeChild:
    """Popen 모양의 가짜. 표준 입력을 닫으면(정지 요청) obeys_stop일 때 0으로 끝난다."""

    def __init__(self, index: int, *, obeys_stop=True, obeys_terminate=True) -> None:
        self.index, self.pid = index, 1000 + index
        self.obeys_stop, self.obeys_terminate = obeys_stop, obeys_terminate
        self.done = threading.Event()
        self.returncode = None
        self.stdin = _FakeStdin(self)
        self.calls: list[str] = []

    def exit(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
        self.done.set()

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.obeys_terminate:
            self.exit(-15)

    def kill(self) -> None:
        self.calls.append("kill")
        self.exit(-9)


class RunProcessesTest(unittest.TestCase):
    def _run(self, *, processes=3, main_workers=None, children_kwargs=None, stop_after=None, join_seconds=1.0):
        children: list[_FakeChild] = []
        seen: dict = {}
        self.children, self.seen = children, seen

        def spawn(index):
            child = _FakeChild(index, **(children_kwargs or {}).get(index, {}))
            children.append(child)
            return child

        def fake_run_workers(_url, _root, **kwargs):
            seen.update(kwargs)
            if main_workers is not None:
                main_workers(kwargs["stop"])
            kwargs["stop"].wait(10)

        stop = threading.Event()
        timer = threading.Timer(stop_after, stop.set) if stop_after is not None else None
        with patch.object(service, "run_workers", fake_run_workers), patch.object(service, "CHILD_KILL_SECONDS", .2):
            if timer:
                timer.start()
            try:
                service.run_processes("postgresql://unit-test", None, processes=processes, workers=2, batch_size=8,
                                      poll_seconds=.1, stop=stop, join_seconds=join_seconds, spawn=spawn)
            finally:
                if timer:
                    timer.cancel()
        return children, seen

    def test_external_stop_stops_children_and_returns(self) -> None:
        children, seen = self._run(stop_after=.1)
        self.assertEqual([child.index for child in children], [1, 2])
        self.assertTrue(all(child.stdin.closed and child.returncode == 0 for child in children))
        self.assertTrue(all(child.calls == [] for child in children))  # SIGTERM·SIGKILL 없이 스스로 끝났다
        # 이 프로세스는 0번이고 정리 담당(cleanup 기본 True), 슬롯은 run_workers 기본(작업자 + 1)
        self.assertEqual(seen["process_index"], 0)
        self.assertNotIn("cleanup", seen)
        self.assertNotIn("capacity", seen)
        self.assertEqual((seen["workers"], seen["batch_size"], seen["poll_seconds"]), (2, 8, .1))

    def test_child_exit_stops_service_with_failure(self) -> None:
        # 0번 작업자가 도는 중에 2번 자식이 코드 3으로 끝난다.
        with self.assertRaisesRegex(service.ProcessFailed, "detector_process_failed:2:3"):
            self._run(main_workers=lambda _stop: threading.Timer(.05, lambda: self.children[1].exit(3)).start())
        first, second = self.children
        self.assertEqual(second.returncode, 3)
        self.assertTrue(first.stdin.closed)
        self.assertEqual(first.returncode, 0)  # 나머지 자식도 멈추고 기다렸다

    def test_child_exit_zero_without_stop_is_failure(self) -> None:
        with self.assertRaisesRegex(service.ProcessFailed, "detector_process_failed:1:0"):
            self._run(processes=2, main_workers=lambda _stop: threading.Timer(
                .05, lambda: self.children[0].exit(0)).start())

    def test_main_worker_failure_stops_children_and_propagates(self) -> None:
        def failing(_url, _root, **kwargs):
            raise runner.WorkerFailed("detector_worker_failed:p0-1")

        children: list[_FakeChild] = []

        def spawn(index):
            children.append(_FakeChild(index))
            return children[-1]

        with patch.object(service, "run_workers", failing):
            with self.assertRaises(runner.WorkerFailed):
                service.run_processes("postgresql://unit-test", None, processes=3, workers=2,
                                      stop=threading.Event(), join_seconds=1, spawn=spawn)
        self.assertTrue(all(child.stdin.closed and child.returncode == 0 for child in children))

    def test_spawn_failure_stops_started_children(self) -> None:
        children: list[_FakeChild] = []

        def spawn(index):
            if index == 2:
                raise OSError("spawn failed")
            children.append(_FakeChild(index))
            return children[-1]

        with patch.object(service, "run_workers", MagicMock(side_effect=AssertionError("not reached"))):
            with self.assertRaisesRegex(OSError, "spawn failed"):
                service.run_processes("postgresql://unit-test", None, processes=3, workers=2,
                                      stop=threading.Event(), join_seconds=1, spawn=spawn)
        self.assertEqual(children[0].returncode, 0)

    def test_stubborn_children_get_terminate_then_kill(self) -> None:
        started = time.monotonic()
        children, _seen = self._run(stop_after=.05, join_seconds=.2, children_kwargs={
            1: {"obeys_stop": False}, 2: {"obeys_stop": False, "obeys_terminate": False}})
        self.assertLess(time.monotonic() - started, 3)  # join 0.2 + SIGTERM 0.2 + SIGKILL 0.2 안쪽
        self.assertEqual(children[0].calls, ["terminate"])
        self.assertEqual(children[1].calls, ["terminate", "kill"])
        self.assertEqual([child.returncode for child in children], [-15, -9])


class StopDeadlineTest(unittest.TestCase):
    """정지 기한 하나: 정지 요청 때 자식에게 바로 알리고, 0번 join과 자식 정지·올림이 같은 기한 안에서 끝난다."""

    def _stop(self, *, main_join: float, budget: float, children_kwargs: dict):
        children: list[_FakeChild] = []
        seen: dict = {}

        def spawn(index):
            children.append(_FakeChild(index, **children_kwargs))
            return children[-1]

        def slow_join(_url, _root, **kwargs):
            seen.update(kwargs)
            kwargs["stop"].wait(10)
            time.sleep(main_join)  # 0번 작업자 join이 이만큼 걸린다

        stop = threading.Event()
        threading.Timer(.05, stop.set).start()
        with patch.object(service, "run_workers", slow_join), patch.object(service, "CHILD_KILL_SECONDS", .3):
            service.run_processes("postgresql://unit-test", None, processes=3, workers=2, stop=stop,
                                  join_seconds=budget, spawn=spawn)
        finished = time.monotonic()
        requested = min(child.stdin.closed_at for child in children)
        return children, seen, requested, finished

    def test_children_signalled_at_stop_and_escalated_within_deadline(self) -> None:
        # 0번 join이 기한 대부분(0.9초)을 써도 자식은 정지 요청 때 알림을 받고, 올림은 기한 안에서 한다.
        started = time.monotonic()
        children, seen, requested, finished = self._stop(
            main_join=.9, budget=1.2, children_kwargs={"obeys_stop": False, "obeys_terminate": False})
        self.assertEqual(seen["join_seconds"], 1.2)  # 0번 작업자 join도 같은 기한
        self.assertLess(requested - started, .05 + .2)  # stop이 선 직후(0번 join을 기다리지 않음)
        self.assertLess(finished - requested, 1.2 + 2 * service.STOP_FLOOR_SECONDS + .3)
        self.assertTrue(all(child.calls == ["terminate", "kill"] and child.returncode == -9 for child in children))

    def test_late_main_join_still_bounded_with_floor(self) -> None:
        # 0번 join이 기한을 넘겨도(1.5초 > 1.0초) 올림은 바닥 시간만 더 기다린다.
        children, _seen, requested, finished = self._stop(
            main_join=1.5, budget=1.0, children_kwargs={"obeys_stop": False, "obeys_terminate": False})
        self.assertLess(finished - requested, 1.5 + 2 * service.STOP_FLOOR_SECONDS + .3)
        self.assertTrue(all(child.calls == ["terminate", "kill"] for child in children))

    def test_default_budget_fits_docker_grace(self) -> None:
        # 기본 기한 + 올림 바닥 + HTTP 서버 닫기(약 0.5초) + 직접 전달 갱신 스레드 정지(최대 1초) < docker stop 기본 10초
        self.assertEqual(service.run_processes.__kwdefaults__["join_seconds"], service.STOP_SECONDS)
        self.assertLess(service.STOP_SECONDS + 2 * service.STOP_FLOOR_SECONDS + .5 + 1.0, 10.0)
        self.assertGreater(service.STOP_SECONDS - 2 * service.CHILD_KILL_SECONDS, 0)


class RealChildStopTest(unittest.TestCase):
    """실제 자식 프로세스(파이썬 한 줄)로 정지 요청(표준 입력 EOF)과 SIGTERM을 본다."""

    def _children(self, *commands):
        children = service._Children()
        for index, code in enumerate(commands, start=1):
            children.add(service._spawn_child(index, command=[sys.executable, "-c", code]))
        return children

    def test_stdin_close_stops_child_and_stubborn_child_is_terminated(self) -> None:
        children = self._children("import sys\nwhile sys.stdin.buffer.read(4096): pass",
                                  "import time\ntime.sleep(60)")
        obeying, stubborn = children.snapshot()
        with patch.object(service, "CHILD_KILL_SECONDS", 1.0):
            started = time.monotonic()
            children.stop(started + 4.0)  # 스스로 끝내기 2초 → SIGTERM 1초 → SIGKILL 1초
        self.assertLess(time.monotonic() - started, 4.0 + 2 * service.STOP_FLOOR_SECONDS + .5)
        self.assertEqual(obeying.returncode, 0)
        self.assertIsNotNone(stubborn.returncode)
        self.assertNotEqual(stubborn.returncode, 0)

    def test_spawned_child_sees_worker_dir_on_path(self) -> None:
        children = self._children("import detector.service, sys\nsys.stdin.buffer.read()")
        child, = children.snapshot()
        children.stop(time.monotonic() + 12.0)
        self.assertEqual(child.returncode, 0)


class WatchParentTest(unittest.TestCase):
    def test_eof_sets_stop(self) -> None:
        stop = threading.Event()
        service._watch_parent(stop, io.BytesIO(b"ignored"))
        self.assertTrue(stop.is_set())


class ChildMainTest(unittest.TestCase):
    def test_child_runs_workers_without_cleanup_and_capacity_workers(self) -> None:
        seen = {}
        env = {"DATABASE_URL": "postgresql://unit-test", "DETECTOR_WORKERS": "3", "DETECTOR_BATCH_SIZE": "4"}
        with patch.dict(os.environ, env), patch("dotenv.load_dotenv", lambda *_a, **_k: None), \
                patch.object(service, "_stop_on_signals", lambda _stop: None), \
                patch.object(service, "_watch_parent", lambda _stop: None), \
                patch.object(service, "run_workers", lambda *_a, **k: seen.update(k)):
            service.child_main(2)
        self.assertEqual((seen["workers"], seen["batch_size"], seen["poll_seconds"]), (3, 4, .1))
        self.assertEqual((seen["capacity"], seen["cleanup"], seen["process_index"]), (3, False, 2))

    def test_invalid_index_or_env_fails(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test"}), \
                patch("dotenv.load_dotenv", lambda *_a, **_k: None):
            for index in (0, 8):
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, "process_index"):
                    service.child_main(index)
            with patch.dict(os.environ, {"DETECTOR_WORKERS": "0"}), \
                    self.assertRaisesRegex(ValueError, "invalid_detector_workers"):
                service.child_main(1)


class ServiceMainProcessesTest(unittest.TestCase):
    def _main(self, env: dict[str, str]):
        server = MagicMock()
        calls = {"run_workers": [], "run_processes": [], "capacity": [], "signals": []}
        base = {"DATABASE_URL": "postgresql://unit-test", "DETECTOR_DELIVERY_TOKEN": "t" * 32}
        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.dict(os.environ, {**base, **env}))
            enter(patch("dotenv.load_dotenv", lambda *_a, **_k: None))
            enter(patch.object(service, "serve", MagicMock(return_value=server)))
            enter(patch.object(service, "run_workers", lambda *a, **k: calls["run_workers"].append(k)))
            enter(patch.object(service, "run_processes", lambda *a, **k: calls["run_processes"].append(k)))
            enter(patch.object(service, "configure_capacity", calls["capacity"].append))
            enter(patch.object(service, "_stop_on_signals", calls["signals"].append))
            service.main()
        return calls, server

    def test_one_process_is_unchanged(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DETECTOR_PROCESSES", None)
            calls, server = self._main({"DETECTOR_WORKERS": "3"})
        self.assertEqual(calls["run_processes"], [])
        self.assertEqual(calls["signals"], [])  # 지금처럼 신호 처리기를 두지 않는다
        self.assertEqual(calls["run_workers"], [{"workers": 3, "poll_seconds": .1, "batch_size": 8}])
        self.assertEqual(calls["capacity"], [4])
        server.server_close.assert_called_once()
        calls, _server = self._main({"DETECTOR_PROCESSES": "1"})
        self.assertEqual(calls["run_processes"], [])

    def test_several_processes_use_run_processes(self) -> None:
        calls, server = self._main({"DETECTOR_PROCESSES": "2", "DETECTOR_WORKERS": "4", "DETECTOR_BATCH_SIZE": "8"})
        self.assertEqual(calls["run_workers"], [])
        (kwargs,) = calls["run_processes"]
        self.assertEqual((kwargs["processes"], kwargs["workers"], kwargs["batch_size"], kwargs["poll_seconds"]),
                         (2, 4, 8, .1))
        self.assertIs(calls["signals"][0], kwargs["stop"])
        self.assertEqual(calls["capacity"], [5])  # 0번 프로세스: 작업자 + 직접 전달 1
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()

    def test_invalid_processes_fails_before_http_server(self) -> None:
        serve = MagicMock()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://unit-test", "DETECTOR_DELIVERY_TOKEN": "t" * 32,
                                     "DETECTOR_PROCESSES": "9"}), \
                patch("dotenv.load_dotenv", lambda *_a, **_k: None), patch.object(service, "serve", serve):
            with self.assertRaisesRegex(ValueError, "invalid_detector_processes"):
                service.main()
        serve.assert_not_called()


class RunWorkersProcessOptionsTest(unittest.TestCase):
    """run_workers의 cleanup·capacity·process_index와 작업자마다 공유 heartbeat 1개."""

    def tearDown(self) -> None:
        policy.configure_capacity(policy.DEFAULT_CAPACITY)

    def _run(self, harness: _Harness, **kwargs) -> list:
        created: list = []

        class RecordingHeartbeat:
            def __init__(self, factory, *, name):
                self.name, self.closed = name, False
                created.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self.closed = True

        stop = threading.Event()
        timer = threading.Timer(.3, stop.set)
        with ExitStack() as stack:
            harness.patches(stack)
            stack.enter_context(patch.object(runner, "psycopg",
                                             SimpleNamespace(connect=lambda *_a, **_k: nullcontext(_FakeConn()))))
            stack.enter_context(patch.object(runner, "SharedLeaseHeartbeat", RecordingHeartbeat))
            loops: list = []
            original = runner._Loop

            def loop(*args, **loop_kwargs):
                loops.append(loop_kwargs.get("heartbeats"))
                return original(*args, **loop_kwargs)

            stack.enter_context(patch.object(runner, "_Loop", loop))
            timer.start()
            try:
                runner.run_workers("postgresql://unit-test", None, workers=2, poll_seconds=.01, stop=stop,
                                   reconcile_seconds=.05, join_seconds=5, **kwargs)
            finally:
                timer.cancel()
        self.assertEqual(sorted(loops, key=lambda item: item.name), sorted(created, key=lambda item: item.name))
        return created

    def test_child_process_options(self) -> None:
        harness = _Harness()
        created = self._run(harness, capacity=2, cleanup=False, process_index=1)
        self.assertEqual(harness.names("reconcile"), [])
        self.assertEqual(harness.names("expire"), [])
        self.assertEqual(set(harness.names("claim")), {"detector-worker-p1-0", "detector-worker-p1-1"})
        self.assertEqual(policy.capacity(), 2)
        self.assertEqual(sorted(item.name for item in created), ["detector-heartbeat-p1-0", "detector-heartbeat-p1-1"])
        self.assertTrue(all(item.closed for item in created))

    def test_process_zero_keeps_single_cleanup_owner(self) -> None:
        harness = _Harness()
        created = self._run(harness, process_index=0)
        self.assertEqual(set(harness.names("reconcile")), {"detector-worker-p0-0"})
        self.assertEqual(set(harness.names("expire")), {"detector-worker-p0-0"})
        self.assertEqual(policy.capacity(), 3)
        self.assertEqual(len(created), 2)  # 작업자마다 1개(시도마다가 아님)


if __name__ == "__main__":
    unittest.main()
