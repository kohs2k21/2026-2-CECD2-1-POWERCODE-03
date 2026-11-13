import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg.pq import TransactionStatus

from detector import incidents_job
from detector.incidents_store import RunOutcome


class FakeConn:
    def __init__(self, status=TransactionStatus.IDLE, rollback_error=None):
        self.closed = False
        self.info = SimpleNamespace(transaction_status=status)
        self.rollback_error = rollback_error
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error:
            raise self.rollback_error
        self.info.transaction_status = TransactionStatus.IDLE

    def close(self):
        self.closed = True


class RunAllTest(unittest.TestCase):
    def run_all(self, conn, effects, connect):
        calls = []

        def fake_run_once(used, source, batch_limit):
            calls.append((used, source))
            effect = effects[source]
            if callable(effect):
                effect = effect(used)
            if isinstance(effect, BaseException):
                raise effect
            return effect

        with patch.object(incidents_job, "_sources", return_value=list(effects)), \
                patch.object(incidents_job, "run_once", side_effect=fake_run_once):
            return incidents_job.run_all(conn, connect, None, 10), calls

    def test_any_exception_is_isolated_per_source(self):
        conn = FakeConn()
        (used, report), calls = self.run_all(conn, {"s1": OverflowError("date value out of range"),
                                                   "s2": RunOutcome("idle", cursor_from=3, cursor_to=3),
                                                   "s3": RuntimeError("boom"),
                                                   "s4": ValueError("incident_member_conflict")},
                                             connect=lambda: self.fail("reconnect not expected"))
        self.assertIs(used, conn)
        self.assertEqual([(line["sourceId"], line["status"]) for line in report],
                         [("s1", "failed"), ("s2", "idle"), ("s3", "failed"), ("s4", "failed")])
        self.assertEqual([line.get("reason") for line in report],
                         ["OverflowError", None, "RuntimeError", "incident_member_conflict"])
        self.assertEqual([source for _, source in calls], ["s1", "s2", "s3", "s4"])

    def test_broken_connection_is_reopened(self):
        broken, fresh = FakeConn(), FakeConn()

        def lose(used):
            used.info.transaction_status = TransactionStatus.UNKNOWN
            return psycopg.OperationalError("server closed the connection")
        (used, report), calls = self.run_all(broken, {"s1": lose, "s2": RunOutcome("idle")}, connect=lambda: fresh)
        self.assertTrue(broken.closed)
        self.assertIs(used, fresh)
        self.assertIs(calls[1][0], fresh)
        self.assertEqual([line["status"] for line in report], ["failed", "idle"])
        self.assertEqual(report[0]["reason"], "OperationalError")

    def test_failed_rollback_reopens(self):
        conn = FakeConn(TransactionStatus.INERROR, rollback_error=psycopg.OperationalError("gone"))
        fresh = FakeConn()
        (used, _), calls = self.run_all(conn, {"s1": ValueError("x"), "s2": RunOutcome("idle")}, connect=lambda: fresh)
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)
        self.assertIs(used, fresh)
        self.assertIs(calls[1][0], fresh)

    def test_rollback_in_open_transaction_keeps_connection(self):
        conn = FakeConn()

        def fail_in_transaction(used):
            used.info.transaction_status = TransactionStatus.INERROR
            return ValueError("incident_state_scope_mismatch")
        (used, _), calls = self.run_all(conn, {"s1": fail_in_transaction, "s2": RunOutcome("idle")},
                                        connect=lambda: self.fail("reconnect not expected"))
        self.assertIs(used, conn)
        self.assertEqual(conn.rollbacks, 1)
        self.assertIs(calls[1][0], conn)

    def test_reconnect_failure_keeps_finished_lines_and_defers_rest(self):
        broken = FakeConn()

        def lose(used):
            used.info.transaction_status = TransactionStatus.UNKNOWN
            return psycopg.OperationalError("server closed the connection")

        def refuse():
            raise psycopg.OperationalError("connection refused")
        (used, report), calls = self.run_all(broken, {"s1": RunOutcome("idle"), "s2": lose,
                                                      "s3": RunOutcome("idle"), "s4": RunOutcome("idle")},
                                             connect=refuse)
        self.assertIsNone(used)
        self.assertTrue(broken.closed)
        self.assertEqual([source for _, source in calls], ["s1", "s2"])
        self.assertEqual([(line.get("sourceId"), line["status"], line.get("reason")) for line in report],
                         [("s1", "idle", None), ("s2", "failed", "OperationalError"),
                          (None, "failed", "reconnect:OperationalError"),
                          ("s3", "not_run", "connection_unavailable"), ("s4", "not_run", "connection_unavailable")])


class BackoffTest(unittest.TestCase):
    def test_doubles_up_to_cap(self):
        self.assertEqual([incidents_job.wait_seconds(60, n) for n in range(5)], [60, 120, 240, 300, 300])
        self.assertEqual(incidents_job.wait_seconds(600, 3), 600)  # 간격이 상한보다 길면 간격 그대로
        self.assertEqual(incidents_job.wait_seconds(1, 10_000), 300)


if __name__ == "__main__":
    unittest.main()
