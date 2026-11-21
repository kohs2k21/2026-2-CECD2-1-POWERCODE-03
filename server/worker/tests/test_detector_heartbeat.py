"""작업자 루프 공유 임대 갱신(detector.heartbeat.SharedLeaseHeartbeat)의 DB 없는 시험.

갱신 함수(renew_training_ownership)와 연결은 가짜다. 간격은 짧은 실제 시간(0.05초)으로 본다.
실제 PostgreSQL에서 긴 시도의 갱신은 integration_detector_workers.py가 본다.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector import heartbeat as heartbeat_module, runner
from detector.heartbeat import SharedLeaseHeartbeat

INTERVAL = .05


class _Renewals:
    """가짜 갱신 함수. 시도 id별 갱신 시각을 남기고, results에 넣은 값(False·예외)을 차례로 돌려준다."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.at: dict[uuid.UUID, list[float]] = {}
        self.results: dict[uuid.UUID, list] = {}
        self.connections: list = []
        self.gates: dict[uuid.UUID, threading.Event] = {}
        self.entered = threading.Event()

    def connect(self):
        conn = SimpleNamespace(closed=False, broken=False)
        conn.close = lambda conn=conn: setattr(conn, "closed", True)
        self.connections.append(conn)
        return conn

    def __call__(self, conn, message, job_id, attempt_id, attempt_token, *, lease_seconds):
        assert not conn.closed and lease_seconds == 1
        gate = self.gates.get(attempt_id)
        if gate is not None:
            self.entered.set()
            gate.wait(5)
        with self.lock:
            self.at.setdefault(attempt_id, []).append(time.monotonic())
            planned = self.results.get(attempt_id)
            value = planned.pop(0) if planned else True
        if isinstance(value, BaseException):
            raise value
        return value

    def count(self, attempt_id) -> int:
        with self.lock:
            return len(self.at.get(attempt_id, []))


class SharedHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renewals = _Renewals()
        self.heartbeats = SharedLeaseHeartbeat(self.renewals.connect, lease_seconds=1, interval_seconds=INTERVAL,
                                               renew=self.renewals)
        self.addCleanup(self.heartbeats.close)

    def lease(self):
        item = self.heartbeats.lease(object(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        item.start()
        return item

    def test_renews_every_interval_after_start_and_reuses_connection(self) -> None:
        started = time.monotonic()
        a = self.lease()
        time.sleep(INTERVAL * 4.5)
        b = self.lease()
        b_started = time.monotonic()
        time.sleep(INTERVAL * 2.5)
        a.stop(), b.stop()
        at_a, at_b = self.renewals.at[a.attempt_id], self.renewals.at[b.attempt_id]
        self.assertGreaterEqual(at_a[0] - started, INTERVAL * .9)  # 첫 갱신은 등록 뒤 간격이 지나서
        self.assertGreaterEqual(len(at_a), 3)
        # 다음 갱신은 앞 갱신이 끝난 때부터 다시 간격 뒤(TrainingLeaseHeartbeat의 wait → 갱신 반복)
        self.assertTrue(all(later - earlier >= INTERVAL * .9 for earlier, later in zip(at_a, at_a[1:])), at_a)
        self.assertGreaterEqual(at_b[0] - b_started, INTERVAL * .9)
        self.assertEqual(len(self.renewals.connections), 1)  # 연결 1개를 다시 쓴다
        self.assertFalse(a.lost or b.lost)

    def test_short_attempt_is_never_renewed(self) -> None:
        for _ in range(20):
            self.lease().stop()
        time.sleep(INTERVAL * 2)
        self.assertEqual(self.renewals.at, {})
        self.assertEqual(self.renewals.connections, [])  # 갱신이 없으면 연결도 열지 않는다

    def test_false_marks_only_that_attempt_lost_and_stops_its_renewal(self) -> None:
        a, b = self.lease(), self.lease()
        self.renewals.results[a.attempt_id] = [False]
        time.sleep(INTERVAL * 4)
        self.assertTrue(a.lost)
        self.assertFalse(b.lost)
        self.assertEqual(self.renewals.count(a.attempt_id), 1)
        self.assertGreaterEqual(self.renewals.count(b.attempt_id), 2)
        a.stop(), b.stop()

    def test_exception_marks_lost_and_reconnects(self) -> None:
        a = self.lease()
        self.renewals.results[a.attempt_id] = [OSError("connection reset")]
        time.sleep(INTERVAL * 2.5)
        self.assertTrue(a.lost)
        self.assertTrue(self.renewals.connections[0].closed)  # 오류 뒤 연결을 닫는다
        b = self.lease()
        time.sleep(INTERVAL * 2.5)
        b.stop()
        self.assertFalse(b.lost)
        self.assertEqual(len(self.renewals.connections), 2)  # 다음 갱신은 새 연결
        self.assertEqual(self.renewals.count(a.attempt_id), 1)

    def test_broken_connection_is_replaced_before_use(self) -> None:
        a = self.lease()
        time.sleep(INTERVAL * 1.5)
        self.renewals.connections[0].broken = True
        time.sleep(INTERVAL * 2)
        a.stop()
        self.assertGreaterEqual(len(self.renewals.connections), 2)
        self.assertFalse(a.lost)

    def _until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)

    def test_reused_connection_error_retries_once_on_fresh_connection(self) -> None:
        # 첫 갱신이 연 연결(쉬는 동안 끊김)에서 연결 오류 → 그 연결을 닫고 새 연결로 그 갱신을 다시 해 성공: 상실 아님.
        a = self.lease()
        self.renewals.results[a.attempt_id] = [True, psycopg.OperationalError("terminating connection"), True]
        self._until(lambda: self.renewals.count(a.attempt_id) >= 3)
        a.stop()
        self.assertFalse(a.lost)
        self.assertEqual(len(self.renewals.connections), 2)
        self.assertTrue(self.renewals.connections[0].closed)
        self.assertFalse(self.renewals.connections[1].closed)

    def test_reused_connection_error_then_fresh_failure_is_lost(self) -> None:
        a = self.lease()
        self.renewals.results[a.attempt_id] = [True, psycopg.InterfaceError("connection closed"),
                                               psycopg.OperationalError("server down")]
        self._until(lambda: a.lost)
        self.assertEqual(self.renewals.count(a.attempt_id), 3)  # 첫 갱신 + 끊긴 연결 + 새 연결 한 번
        self.assertEqual(len(self.renewals.connections), 2)
        self.assertTrue(all(conn.closed for conn in self.renewals.connections))
        time.sleep(INTERVAL * 2)
        self.assertEqual(self.renewals.count(a.attempt_id), 3)
        a.stop()

    def test_false_on_reused_connection_is_not_retried(self) -> None:
        a = self.lease()
        self.renewals.results[a.attempt_id] = [True, False]
        self._until(lambda: a.lost)
        time.sleep(INTERVAL * 2)
        self.assertEqual(self.renewals.count(a.attempt_id), 2)
        self.assertEqual(len(self.renewals.connections), 1)  # 소유권 상실은 연결 문제가 아니므로 연결도 그대로
        a.stop()

    def test_non_connection_error_on_reused_connection_is_not_retried(self) -> None:
        a = self.lease()
        self.renewals.results[a.attempt_id] = [True, ValueError("unexpected")]
        self._until(lambda: a.lost)
        time.sleep(INTERVAL * 2)
        self.assertEqual(self.renewals.count(a.attempt_id), 2)
        self.assertEqual(len(self.renewals.connections), 1)
        self.assertTrue(self.renewals.connections[0].closed)  # 오류 뒤 연결은 닫는다
        a.stop()

    def test_connection_closed_when_thread_exits(self) -> None:
        a = self.lease()
        self._until(lambda: self.renewals.count(a.attempt_id) >= 1)
        a.stop()
        conn = self.renewals.connections[0]
        self.assertFalse(conn.closed)
        self.heartbeats.close()
        self.assertTrue(conn.closed)

    def test_connection_closed_when_thread_exits_after_close_timeout(self) -> None:
        a = self.heartbeats.lease(object(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        self.renewals.results[a.attempt_id] = [True]
        a.start()
        self._until(lambda: self.renewals.count(a.attempt_id) >= 1)
        gate = self.renewals.gates[a.attempt_id] = threading.Event()  # 둘째 갱신이 DB에서 막힌 것처럼
        self.assertTrue(self.renewals.entered.wait(2))
        conn = self.renewals.connections[0]
        self.heartbeats.close(timeout=.05)  # join이 timeout으로 끝난다
        self.assertTrue(self.heartbeats._thread.is_alive())
        self.assertFalse(conn.closed)
        gate.set()
        self.heartbeats._thread.join(2)
        self.assertFalse(self.heartbeats._thread.is_alive())
        self.assertTrue(conn.closed)  # 스레드가 끝날 때 닫는다
        a.stop()

    def test_stop_waits_for_its_own_renewal_in_progress_only(self) -> None:
        a = self.heartbeats.lease(object(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        gate = self.renewals.gates[a.attempt_id] = threading.Event()
        a.start()
        self.assertTrue(self.renewals.entered.wait(2))
        # a의 갱신이 막힌 동안 등록·해제한 다른 시도는 a의 갱신을 기다리지 않는다.
        b = self.lease()
        before = time.monotonic()
        b.stop()
        self.assertLess(time.monotonic() - before, INTERVAL)
        returned = threading.Event()
        stopper = threading.Thread(target=lambda: (a.stop(), returned.set()))
        stopper.start()
        self.assertFalse(returned.wait(INTERVAL * 3))  # 자기 갱신이 끝날 때까지 돌아오지 않는다
        gate.set()
        self.assertTrue(returned.wait(2))
        stopper.join(2)
        count = self.renewals.count(a.attempt_id)
        time.sleep(INTERVAL * 3)
        self.assertEqual(self.renewals.count(a.attempt_id), count)  # 해제 뒤 갱신 없음
        self.assertEqual(self.renewals.count(b.attempt_id), 0)

    def test_no_thread_per_attempt(self) -> None:
        started: list[str] = []
        original = threading.Thread.start

        def counted(thread):
            started.append(thread.name)
            return original(thread)

        with patch.object(threading.Thread, "start", counted):
            for _ in range(100):
                item = self.lease()
                self.assertFalse(item.lost)
                item.stop()
        self.assertEqual(started, [])

    def test_close_marks_remaining_lost_and_later_start_is_lost(self) -> None:
        a = self.lease()
        self.heartbeats.close()
        self.assertTrue(a.lost)
        late = self.heartbeats.lease(object(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        late.start()
        self.assertTrue(late.lost)
        late.stop()
        self.assertFalse(self.heartbeats._thread.is_alive())

    def test_double_start_rejected_and_stop_before_start_is_noop(self) -> None:
        item = self.heartbeats.lease(object(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        item.stop()
        item.start()
        with self.assertRaisesRegex(RuntimeError, "already_started"):
            item.start()
        item.stop()
        item.stop()

    def test_interval_must_be_shorter_than_lease(self) -> None:
        for lease, interval in ((0, .5), (1, 0), (1, 1), (60, 61)):
            with self.subTest(lease=lease, interval=interval), self.assertRaises(ValueError):
                SharedLeaseHeartbeat(self.renewals.connect, lease_seconds=lease, interval_seconds=interval)

    def test_defaults_match_training_heartbeat(self) -> None:
        heartbeats = SharedLeaseHeartbeat(self.renewals.connect)
        self.addCleanup(heartbeats.close)
        self.assertEqual((heartbeats.lease_seconds, heartbeats.interval_seconds), (60, 20.0))
        from trainer.lease import renew_training_ownership
        self.assertIs(heartbeats._renew, renew_training_ownership)
        with patch.object(heartbeat_module, "INTERVAL_SECONDS", .5):
            shorter = SharedLeaseHeartbeat(self.renewals.connect)
            self.addCleanup(shorter.close)
        self.assertEqual(shorter.interval_seconds, .5)


class ProcessMessageUsesSharedHeartbeatTest(unittest.TestCase):
    """heartbeats가 있으면 시도마다 TrainingLeaseHeartbeat를 만들지 않고 거기에 등록한다."""

    def test_registers_lease_instead_of_thread(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_detector_batch import _FixedCursor, _message
        from detector import attempt_v04
        from datetime import datetime, timezone
        message = _message()
        payload = message.payload
        fixed = ({"sourceId": None, "observationId": None, "configVersionId": None, "inputManifestHash": None,
                  "inputManifestId": None}, None, uuid.uuid4(), "h", datetime(2026, 11, 22, tzinfo=timezone.utc), None)
        job = {"id": uuid.UUID(payload["jobId"]), "contractVersion": "v04"}
        attempt_id, token = uuid.uuid4(), uuid.uuid4()
        lease = MagicMock(lost=False)
        heartbeats = MagicMock()
        heartbeats.lease.return_value = lease
        conn = SimpleNamespace(cursor=lambda: _FixedCursor(fixed), commit=lambda: None, rollback=lambda: None)
        never = MagicMock(side_effect=AssertionError("per-attempt heartbeat thread"))
        with patch.object(runner, "load_process_observation", lambda *_a: SimpleNamespace(source_id=None)), \
                patch("trainer.runner._claim_job", lambda *_a: (job, attempt_id, token)), \
                patch.object(runner, "queue_wait_ms", lambda *_a: 0.0), \
                patch("trainer.lease.TrainingLeaseHeartbeat", never), \
                patch.object(attempt_v04, "run_attempt", MagicMock()) as run_attempt:
            runner.process_detection_message(conn, message, None, heartbeats=heartbeats)
        heartbeats.lease.assert_called_once_with(message, job["id"], attempt_id, token)
        lease.start.assert_called_once()
        lease.stop.assert_called_once()
        self.assertIs(run_attempt.call_args.kwargs["heartbeat"], lease)
        never.assert_not_called()


if __name__ == "__main__":
    unittest.main()
