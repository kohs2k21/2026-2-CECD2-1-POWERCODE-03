"""Collector 운영 계측(contracts/collector-metrics.md): 원천 시각 채우기·입력 구간·API 수신 시각·clock-basis CLI·자원 표본기."""
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import input_cli, input_runner, resource_sampler as rs, source_clock, storage
from detector.incidents import occurred_time
from trainer.source_time import process_source_time

UNKNOWN = {"status": "unknown"}
VERIFIED_UTC = {"status": "verified", "timezone": "UTC", "method": "persist_lag_check"}


class SourceTimeFill(unittest.TestCase):
    def resolve(self, start, end, basis=UNKNOWN):
        return source_clock.resolve({"start_time_raw": start, "end_time_raw": end}, basis)

    def test_offset_in_string(self):
        got = self.resolve("2026-11-15 09:00:00", "2026-11-15T10:00:00+09:00")
        self.assertEqual((got.occurred_at, got.raw, got.clock_status),
                         (datetime(2026, 11, 15, 1, tzinfo=timezone.utc), "2026-11-15T10:00:00+09:00", "offset"))
        self.assertEqual(got.parsed_times, {"rule": "source-time-v1", "field": "end", "basis": "offset"})

    def test_verified_timezone(self):
        got = self.resolve("", "2026-11-15 10:00:00.250", VERIFIED_UTC)
        self.assertEqual((got.occurred_at, got.clock_status),
                         (datetime(2026, 11, 15, 10, 0, 0, 250000, tzinfo=timezone.utc), "verified_tz"))

    def test_assumed_timezone_when_not_verified(self):
        for basis in (UNKNOWN, None, "not-an-object", {"status": "pending", "timezone": "UTC"}):
            with self.subTest(basis=basis):
                got = self.resolve("", "2026-11-15 10:00:00", basis)
                self.assertEqual((got.occurred_at, got.clock_status), (datetime(2026, 11, 15, 1, tzinfo=timezone.utc), "assumed_tz"))

    def test_end_before_start_and_start_fallback(self):
        both = self.resolve("2026-11-15 09:00:00", "2026-11-15 09:30:00")
        self.assertEqual((both.parsed_times["field"], both.raw), ("end", "2026-11-15 09:30:00"))
        for end in ("", "\\N", "  ", None, "not-a-time"):
            with self.subTest(end=end):
                got = self.resolve("2026-11-15 09:00:00", end)
                self.assertEqual((got.parsed_times["field"], got.raw, got.clock_status), ("start", "2026-11-15 09:00:00", "assumed_tz"))

    def test_unparsed(self):
        cases = [("x", "y", UNKNOWN, "y"), ("", "2026-13-01 00:00:00", UNKNOWN, "2026-13-01 00:00:00"),
                 # verified without a usable zone name: naive values are not given the assumed zone
                 ("", "2026-11-15 10:00:00", {"status": "verified"}, "2026-11-15 10:00:00"),
                 ("", "2026-11-15 10:00:00", {"status": "verified", "timezone": "No/Such_Zone"}, "2026-11-15 10:00:00"),
                 (5, None, UNKNOWN, None)]
        for start, end, basis, raw in cases:
            with self.subTest(start=start, end=end, basis=basis):
                got = self.resolve(start, end, basis)
                self.assertEqual((got.occurred_at, got.raw, got.clock_status, got.parsed_times),
                                 (None, raw, "unparsed", {"rule": "source-time-v1"}))
        offset = self.resolve("", "2026-11-15T10:00:00Z", {"status": "verified"})
        self.assertEqual(offset.clock_status, "offset")

    def test_absent(self):
        for start, end in ((None, None), ("", "\\N"), (" ", "")):
            with self.subTest(start=start, end=end):
                got = self.resolve(start, end)
                self.assertEqual((got.occurred_at, got.raw, got.clock_status), (None, None, "absent"))
        self.assertEqual(source_clock.absent().parsed_json(), '{"rule":"source-time-v1"}')

    def test_process_datetime_is_not_used(self):
        got = source_clock.resolve({"process_datetime_raw": "2026-11-15 10:00:00"}, UNKNOWN)
        self.assertEqual(got.clock_status, "absent")

    def test_same_rule_as_trainer_and_detector(self):
        rows = [("2026-11-15 09:00:00", "2026-11-15 10:00:00"), ("2026-11-15T09:00:00+09:00", ""), ("bad", "")]
        for start, end in rows:
            for zone, basis in ((None, UNKNOWN), ("UTC", VERIFIED_UTC)):
                with self.subTest(start=start, end=end, zone=zone):
                    got = self.resolve(start, end, basis)
                    expected, kind, field = process_source_time(start, end, zone)
                    self.assertEqual(got.occurred_at, expected)
                    if expected is not None:
                        self.assertEqual((got.clock_status, got.parsed_times["field"]), (kind, field))
                        detector = occurred_time(SimpleNamespace(start_time_raw=start, end_time_raw=end,
                                                                 decided_at=datetime(2000, 1, 1, tzinfo=timezone.utc)), zone)
                        self.assertEqual(detector[0], expected)


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        if "SELECT clock_basis" in sql:
            self.result = (self.conn.basis,)
        elif "INSERT INTO data_observations" in sql:
            self.result = (params[0],)
        else:
            self.result = None

    def fetchone(self):
        return self.result


class Connection:
    def __init__(self, basis=UNKNOWN):
        self.basis = basis
        self.statements = []

    @contextmanager
    def transaction(self):
        yield

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return SimpleNamespace(fetchone=lambda: None)


class IngestColumns(unittest.TestCase):
    def ingest(self, conn, **kwargs):
        raw = {"transaction_id": "t", "process_id": "p", "start_time_raw": "2026-11-15 09:00:00", "end_time_raw": "2026-11-15 10:00:00"}
        storage.ingest_observation(conn, source_id=uuid.uuid4(), layer="P", dedup_key="k", raw=raw, **kwargs)
        sql, params = next(item for item in conn.statements if "INSERT INTO data_observations" in item[0])
        return sql, params

    def test_source_time_columns_and_null_receipt_for_csv(self):
        sql, params = self.ingest(Connection())
        self.assertNotIn("'unknown'", sql)
        self.assertEqual(params[8:13], ("2026-11-15 10:00:00", datetime(2026, 11, 15, 1, tzinfo=timezone.utc), None, "assumed_tz",
                                       '{"basis":"assumed_tz","field":"end","rule":"source-time-v1"}'))

    def test_verified_basis_read_in_the_same_transaction(self):
        _, params = self.ingest(Connection(VERIFIED_UTC))
        self.assertEqual((params[9], params[11]), (datetime(2026, 11, 15, 10, tzinfo=timezone.utc), "verified_tz"))

    def test_api_receipt_time_is_stored(self):
        received = datetime(2026, 11, 15, 1, 0, 5, tzinfo=timezone.utc)
        _, params = self.ingest(Connection(), api_received_at=received)
        self.assertIs(params[10], received)
        with self.assertRaises(ValueError):
            self.ingest(Connection(), api_received_at=datetime(2026, 11, 15))


class IngestPhase(unittest.TestCase):
    T0 = datetime(2026, 11, 15, 1, tzinfo=timezone.utc)

    def test_assignment(self):
        cases = [({"mode": "csv", "success": None, "switched": self.T0}, "replay"),
                 ({"mode": "csv", "success": self.T0, "switched": self.T0}, "replay"),
                 ({"mode": "api", "success": None, "switched": self.T0}, "initial"),
                 # paused -> resumed: the run is reused, its last success predates the switch
                 ({"mode": "api", "success": self.T0, "switched": self.T0 + timedelta(seconds=5)}, "initial"),
                 ({"mode": "api", "success": self.T0 + timedelta(seconds=5), "switched": self.T0}, "live")]
        for old, expected in cases:
            with self.subTest(old=old):
                self.assertEqual(input_runner.ingest_phase(old), expected)


class TickReceipt(unittest.TestCase):
    def test_api_tick_passes_receipt_finished_at_and_phase(self):
        finished = datetime(2026, 11, 15, 1, 0, 2, tzinfo=timezone.utc)
        receipt = {"started_at": finished - timedelta(seconds=1), "finished_at": finished, "duration_ms": 1000.0, "http_status": 200,
                   "response_code": "0000", "response_bytes": 10, "error_code": None, "received_monotonic": 0.0}
        api = SimpleNamespace(in_progress=lambda: [{}], last_receipt=receipt)
        old = {"mode": "api", "revision": 1, "run": uuid.uuid4(), "config": None, "settings": {"pollSeconds": 2}, "position": 4,
               "status": "running", "attempt": None, "success": finished - timedelta(minutes=1), "replay": None, "count": None,
               "switched": finished - timedelta(hours=1)}
        calls = []
        with mock.patch.object(input_runner, "read_state", return_value=old), mock.patch.object(input_runner, "fence", return_value=True), \
                mock.patch.object(input_runner, "mapped_api_records", return_value=[("P", {"transaction_id": "t"})]), \
                mock.patch.object(input_runner, "admit_batch", side_effect=lambda *a, **k: calls.append(k) or [{"created": True, "identified": True}]), \
                mock.patch.object(input_runner, "store_snapshot", return_value=(uuid.uuid4(), True)), \
                mock.patch.object(input_runner, "store_sample"):
            result = input_runner.tick(Connection(), uuid.uuid4(), api_factory=lambda: api, collect_catalog=False)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual((calls[0]["received_at"], calls[0]["phase"]), (finished, "live"))


class ClockBasisCli(unittest.TestCase):
    def test_timezone_validation(self):
        for name in ("Asia/Seoul", "UTC", "America/Argentina/Buenos_Aires", "Etc/GMT+9"):
            self.assertEqual(source_clock.validate_timezone(name), name)
        for name in ("", "No/Such_Zone", "../etc/passwd", "/etc/localtime", "Asia/../Seoul", "Asia/Seoul\n", None, 9, "a" * 65):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid_timezone"):
                source_clock.validate_timezone(name)
        for actor in ("", "  ", "a\nb", "x" * 129, None):
            with self.subTest(actor=actor), self.assertRaisesRegex(ValueError, "invalid_actor"):
                source_clock.validate_actor(actor)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["input_cli", *argv]), mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                mock.patch.object(input_cli.psycopg, "connect") as connect, redirect_stdout(out), redirect_stderr(err):
            code = input_cli.main()
        return code, out.getvalue(), err.getvalue(), connect

    def test_invalid_input_never_connects(self):
        for argv in (("clock-basis", "verify", "--timezone", "Mars/Base", "--actor", "op"),
                     ("clock-basis", "verify", "--timezone", "Asia/Seoul", "--actor", " "),
                     ("clock-basis", "reset", "--actor", "")):
            with self.subTest(argv=argv):
                code, _, err, connect = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn(json.loads(err)["errorCode"], ("invalid_timezone", "invalid_actor"))
                connect.assert_not_called()

    def test_unknown_source_is_rejected_not_created(self):
        class Conn(Connection):
            def __init__(self, found):
                super().__init__();self.found=found;self.committed=False
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def commit(self):self.committed=True
            def execute(self, sql, params=None):
                self.statements.append((sql, params))
                return SimpleNamespace(fetchone=lambda: self.found if 'dataset_key' in sql else ({"status":"unknown"},))
        for found, code in ((None, 2), ((uuid.UUID(int=5),), 0)):
            with self.subTest(found=found):
                conn = Conn(found)
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(sys, "argv", ["input_cli", "--source-key", "typo-key", "clock-basis", "reset", "--actor", "op"]), \
                        mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                        mock.patch.object(input_cli.psycopg, "connect", return_value=conn), \
                        mock.patch.object(input_cli, "ensure_source") as ensure, redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(input_cli.main(), code)
                ensure.assert_not_called()
                self.assertFalse(any("INSERT" in sql for sql, _ in conn.statements))
                if found is None:
                    self.assertEqual(json.loads(err.getvalue())["errorCode"], "input_source_not_initialized")
                    self.assertEqual(len(conn.statements), 1)
                    self.assertFalse(conn.committed)
                else:
                    self.assertEqual(json.loads(out.getvalue())["sourceId"], str(uuid.UUID(int=5)))
                    self.assertTrue(conn.committed)

    def test_verify_and_reset_statements(self):
        class Conn(Connection):
            def execute(self, sql, params=None):
                self.statements.append((sql, params))
                value = {"status": "verified"} if "RETURNING" in sql else UNKNOWN
                return SimpleNamespace(fetchone=lambda: (value,))
        conn = Conn()
        out = source_clock.set_clock_basis(conn, "sid", verify_timezone="Asia/Seoul", actor="op-1")
        self.assertEqual((out["previous"], out["clockBasis"]), (UNKNOWN, {"status": "verified"}))
        self.assertIn("FOR UPDATE", conn.statements[0][0])
        self.assertEqual(conn.statements[1][1], ("Asia/Seoul", "persist_lag_check", "op-1", "sid"))
        self.assertIn("'verifiedAt'", conn.statements[1][0])
        source_clock.set_clock_basis(conn, "sid", verify_timezone=None, actor="op-1")
        self.assertIn("'status','unknown'", conn.statements[3][0])

    def test_collector_runtime_never_sets_clock_basis(self):
        root = Path(input_runner.__file__).parent
        for name in ("input_runner.py", "input_store.py", "storage.py", "api_runner.py", "replay_csv.py", "resource_sampler.py"):
            self.assertNotIn("set_clock_basis", (root / name).read_text(encoding="utf-8"), name)


PROC_STAT_1 = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0 0\nintr 1\n"
PROC_STAT_2 = "cpu  160 0 70 880 90 0 0 0 0 0\ncpu0 160 0 70 880 90 0 0 0 0 0\n"
MEMINFO = "MemTotal:       16000000 kB\nMemFree:         2000000 kB\nMemAvailable:    4000000 kB\n"


class ResourceSampler(unittest.TestCase):
    def test_cpu_parsing_and_percent(self):
        first, second = rs.parse_cpu(PROC_STAT_1), rs.parse_cpu(PROC_STAT_2)
        self.assertEqual((first.total, first.idle), (1000, 850))
        # delta total 200, delta idle(idle+iowait) 120 -> 40 % busy
        self.assertEqual(rs.cpu_percent(first, second), (40.0, None))
        self.assertEqual(rs.cpu_percent(None, second), (None, "cpu_first_sample"))
        self.assertEqual(rs.cpu_percent(second, first), (None, "cpu_counter_reset"))
        self.assertEqual(rs.cpu_percent(first, None), (None, "proc_stat_unavailable"))
        for text in ("", "intr 1\n", "cpu  1 2 3\n", "cpu  a b c d e\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                rs.parse_cpu(text)

    def test_memory_parsing(self):
        self.assertEqual(rs.parse_memory(MEMINFO), (12000000 * 1024, 16000000 * 1024))
        for text in ("MemTotal: 10 kB\n", "MemTotal: 10 kB\nMemAvailable: 11 kB\n", "MemTotal: 0 kB\nMemAvailable: 0 kB\n",
                     "MemTotal: 10 MB\nMemAvailable: 1 MB\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                rs.parse_memory(text)

    def test_resource_rows_from_proc_fixtures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "stat").write_text(PROC_STAT_1, encoding="ascii")
            (root / "meminfo").write_text(MEMINFO, encoding="ascii")
            ticks = iter([0.0, 60.0])
            sampler = rs.Sampler(proc_root=root, clock=lambda: next(ticks))
            first = sampler.resource_row()
            self.assertEqual((first["cpu_status"], first["cpu_percent"], first["cpu_reason_code"], first["memory_status"]),
                             ("unavailable", None, "cpu_first_sample", "measured"))
            (root / "stat").write_text(PROC_STAT_2, encoding="ascii")
            second = sampler.resource_row()
            self.assertEqual((second["cpu_status"], second["cpu_percent"], second["sample_interval_ms"], second["scope"]),
                             ("measured", 40.0, 60000.0, "host_proc_unverified"))
            self.assertEqual((second["memory_used_bytes"], second["memory_total_bytes"]), (12000000 * 1024, 16000000 * 1024))

    def test_missing_proc_is_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            row = rs.Sampler(proc_root=Path(temp) / "absent").resource_row()
        self.assertEqual((row["cpu_status"], row["cpu_percent"], row["cpu_reason_code"]), ("unavailable", None, "proc_stat_unavailable"))
        self.assertEqual((row["memory_status"], row["memory_used_bytes"], row["memory_reason_code"]), ("unavailable", None, "meminfo_unavailable"))

    def test_disk_unavailable_paths(self):
        for path, mounted in ((None, True), ("", True), ("/measure/database_data", False)):
            with self.subTest(path=path):
                row = rs.measure_disk("database_data", path, ismount=lambda p: mounted)
                self.assertEqual((row["status"], row["reason_code"], row["capacity_bytes"], row["warning_state"]),
                                 ("unavailable", "mount_not_visible", None, "unknown"))
        def broken(path):
            raise OSError("denied")
        row = rs.measure_disk("artifacts", "/m", ismount=lambda p: True, statvfs=broken)
        self.assertEqual((row["status"], row["reason_code"]), ("unavailable", "statvfs_failed"))

    def test_disk_measured_and_warning_states(self):
        def vfs(free_blocks, avail_blocks):
            return lambda path: SimpleNamespace(f_blocks=1000, f_bfree=free_blocks, f_bavail=avail_blocks, f_frsize=4096)
        stat = lambda path: SimpleNamespace(st_dev=2049)
        for free, avail, state in ((500, 450, "normal"), (200, 150, "warning"), (90, 50, "critical")):
            with self.subTest(state=state):
                row = rs.measure_disk("database_data", "/m", ismount=lambda p: True, statvfs=vfs(free, avail), stat=stat)
                self.assertEqual((row["status"], row["warning_state"], row["filesystem_key"], row["reason_code"]),
                                 ("measured", state, "dev:2049", None))
                self.assertEqual((row["capacity_bytes"], row["used_bytes"], row["available_bytes"]),
                                 (1000 * 4096, (1000 - free) * 4096, avail * 4096))

    def test_store_statements(self):
        conn = Connection()
        resource = rs.Sampler(proc_root=Path("/nonexistent-proc")).resource_row()
        disks = [rs.measure_disk("database_data", None)]
        rs.store(conn, resource, disks)
        sqls = [sql for sql, _ in conn.statements]
        self.assertTrue(sqls[0].lstrip().startswith("INSERT INTO ops_resource_measurements"))
        self.assertIn("INSERT INTO ops_storage_measurements", sqls[1])
        self.assertEqual(conn.statements[1][1][1:3], ("database_data", None))
        self.assertTrue(all("DELETE" in sql for sql in sqls[2:]) and len(sqls) == 4)

    def test_configuration_rejected(self):
        with self.assertRaises(ValueError):
            rs.Sampler(scope="container")
        with self.assertRaises(ValueError):
            rs.Sampler(disks={"other": "/x"})


if __name__ == "__main__":
    unittest.main()
