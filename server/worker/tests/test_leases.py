"""큐 임대 검증과 학습 갱신 동작의 회귀 검사."""

import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pg_queue import ClaimedMessage, acknowledge, claim, fail, renew_lease
from trainer.lease import TrainingLeaseHeartbeat


class Cursor:
    def __init__(self):
        self.rowcount = 1
        self.sql = ""
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.statements.append((sql, params))

    def fetchone(self):
        return None


class Connection:
    def __init__(self):
        self.last_cursor = Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        self.last_cursor = Cursor()
        return self.last_cursor


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = Connection()
        self.message = ClaimedMessage(uuid.uuid4(), {}, 1, uuid.uuid4(), None)

    def test_completion_failure_and_renewal_require_unexpired_token(self):
        operations = (
            lambda: acknowledge(self.conn, "training", self.message),
            lambda: fail(self.conn, "training", self.message),
            lambda: renew_lease(self.conn, "training", self.message),
        )
        for operation in operations:
            self.assertTrue(operation())
            sql = self.conn.last_cursor.sql
            self.assertIn("lease_token = %s", sql)
            self.assertIn("lease_until > clock_timestamp()", sql)

    def test_heartbeat_reports_lost_ownership(self):
        with patch("trainer.lease.renew_training_ownership", return_value=False):
            heartbeat = TrainingLeaseHeartbeat(
                Connection, self.message, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
                lease_seconds=1, interval_seconds=0.01,
            )
            heartbeat.start()
            deadline = time.monotonic() + 1
            while not heartbeat.lost and time.monotonic() < deadline:
                time.sleep(0.01)
            heartbeat.stop()
        self.assertTrue(heartbeat.lost)

    def test_benchmark_claim_scopes_both_updates_to_its_run(self):
        self.assertIsNone(claim(self.conn, "training", benchmark_run_id="test-run"))
        statements = self.conn.last_cursor.statements
        self.assertEqual(len(statements), 2)
        for sql, params in statements:
            self.assertIn("payload->>'benchmarkRunId' = %s", sql)
            self.assertIn("test-run", params)

    def test_job_and_observation_scopes_compare_on_uuid_side(self):
        # uuid 열을 text로 바꾸면 PK 색인을 못 쓰므로 payload 값을 안전하게 uuid로 바꿔 비교한다.
        # 작업 종류 확인은 최상위 EXISTS가 join으로 바뀌지 않도록 스칼라 부질의로 둔다.
        for kwargs, column, shape in (
                ({"job_kinds": ("detect",)}, "j.id", "AND (SELECT j.kind::text FROM run_jobs j WHERE j.id = (CASE WHEN"),
                ({"source_id": "s"}, "scoped_observation.id", "OR (NOT (payload ? 'sourceId') AND EXISTS (")):
            self.assertIsNone(claim(self.conn, "detection", **kwargs))
            for sql, _params in self.conn.last_cursor.statements:
                sql = " ".join(sql.split())
                self.assertNotIn(f"{column}::text", sql)
                self.assertIn(shape, sql)
                self.assertIn(f"{column} = (CASE WHEN payload->>", sql)


if __name__ == "__main__":
    unittest.main()
