"""Shared input batch admission (input_store.admit_batch): same outcomes as one-by-one admission, set-based SQL."""
import json
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import input_store
from collector.input_contract import digest
from collector.storage import prepare_projection

SOURCE = uuid.UUID(int=1)
RUN = uuid.UUID(int=2)
RECEIVED = datetime(2026, 11, 15, 1, 0, 2, tzinfo=timezone.utc)
UNKNOWN = {"status": "unknown"}


def P(tx, process="p", retry=0, **extra):
    return ("P", {"transaction_id": tx, "process_id": process, "retry_count": retry, **extra})


def T(tx, **extra):
    return ("T", {"transaction_id": tx, "retry_count": 0, **extra})


def M(tx, message, process="p", **extra):
    return ("M", {"transaction_id": tx, "process_id": process, "message_id": message, "retry_count": 0, **extra})


def B(message):
    return ("B", {"message_id": message})


class BatchConnection:
    """Answers the batch statements; `latest` is the stored newest revision per execution, `conflicts` dedup keys
    that already exist (unidentified rows) with their stored tuple."""

    def __init__(self, *, latest=None, conflicts=None, basis=UNKNOWN, existing=None):
        self.latest = latest or {}
        self.conflicts = conflicts or set()
        self.existing = existing or {}
        self.basis = basis
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        rows = []
        if "SELECT clock_basis" in sql:
            rows = [(self.basis,)]
        elif "CROSS JOIN LATERAL" in sql:
            rows = [(uuid.UUID(e), *self.latest[uuid.UUID(e)]) for e in params[0] if uuid.UUID(e) in self.latest]
        elif "INSERT INTO data_observations" in sql:
            ids, dedups = params[2], params[6]
            rows = [(uuid.UUID(i),) for i, d in zip(ids, dedups) if d not in self.conflicts]
        elif "FROM data_observations o JOIN unnest" in sql:
            rows = [(layer, dedup, *self.existing[dedup]) for layer, dedup in zip(params[0], params[1]) if dedup in self.existing]
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)

    def sql(self, fragment):
        return [params for sql, params in self.statements if fragment in sql]


def execution_id(layer, raw, namespace="api"):
    from collector.input_contract import identity
    key = json.dumps([namespace, layer, identity(layer, raw)], ensure_ascii=False, separators=(",", ":"))
    return uuid.uuid5(uuid.NAMESPACE_URL, f"input-execution:{SOURCE}:{key}")


def stored_hash(layer, raw):
    return digest(raw) if layer == "B" else prepare_projection(layer, raw, None).content_hash


class Admit(unittest.TestCase):
    def admit(self, records, conn=None, *, mode="api", payloads=None, **kwargs):
        conn = conn or BatchConnection()
        kwargs.setdefault("phase", "replay" if mode == "csv" else "live")
        kwargs.setdefault("received_at", None if mode == "csv" else RECEIVED)
        calls = []

        def enqueue(_conn, ids, *, config_id=None):
            calls.append((list(ids), config_id))
            return [(payloads or {}).get(i, {"schemaVersion": 1, "eventId": "e"}) for i in ids]

        with mock.patch.object(input_store, "enqueue_observations", side_effect=enqueue):
            outcomes = input_store.admit_batch(conn, SOURCE, RUN, mode, records, namespace=mode, **kwargs)
        return conn, outcomes, calls

    def inserted(self, conn):
        params = conn.sql("INSERT INTO data_observations")[0]
        return list(zip(params[3], params[4], params[5], params[6]))  # layer, execution, revision, dedup

    def test_revisions_in_input_order_within_one_batch(self):
        a, b = P("t1", status="S"), P("t1", status="F")
        conn, outcomes, _ = self.admit([a, b, a, a, b])
        self.assertEqual([o["created"] for o in outcomes], [True, True, True, False, True])
        execution = str(execution_id(*a))
        self.assertEqual([(r[1], r[2], r[3]) for r in self.inserted(conn)],
                         [(execution, str(n), f"input:{execution}:{n}") for n in (1, 2, 3, 4)])

    def test_continues_from_stored_latest_and_skips_same_content(self):
        a, b = P("t1", status="S"), P("t1", status="F")
        stored = {execution_id(*a): (5, stored_hash(*a))}
        conn, outcomes, _ = self.admit([a, b], BatchConnection(latest=stored))
        self.assertEqual([o["created"] for o in outcomes], [False, True])
        self.assertEqual([r[2] for r in self.inserted(conn)], ["6"])
        # Executions are registered once per distinct key, in first-seen order.
        executions = conn.sql("INSERT INTO data_executions")[0]
        self.assertEqual(executions[1], [str(execution_id(*a))])

    def test_unidentified_rows_use_content_dedup_and_are_counted(self):
        bad = ("P", {"transaction_id": "t1", "process_id": "p", "retry_count": -1, "status": "S"})
        conn, outcomes, calls = self.admit([bad, bad, P("t2")])
        self.assertEqual([(o["created"], o["identified"]) for o in outcomes], [(True, False), (False, False), (True, True)])
        rows = self.inserted(conn)
        self.assertEqual(rows[0][1:3], (None, None))
        self.assertEqual(rows[0][3], f"input-unidentified:{SOURCE}:api:P:{digest(bad[1])}")
        # Only the identified new P is a detect input.
        self.assertEqual(calls, [([uuid.UUID(outcomes[2]["observationId"])], None)])
        flags = conn.sql("INSERT INTO data_observations")[0][13]
        self.assertIn("execution_identity_unverified", json.loads(flags[0]))

    def test_existing_unidentified_row_is_verified(self):
        bad = ("T", {"transaction_id": "t1", "retry_count": "x"})
        dedup = f"input-unidentified:{SOURCE}:api:T:{digest(bad[1])}"
        same = BatchConnection(conflicts={dedup}, existing={dedup: (stored_hash(*bad), None, None, None)})
        _, outcomes, _ = self.admit([bad], same)
        self.assertEqual(outcomes, [{"created": False, "identified": False}])
        other = BatchConnection(conflicts={dedup}, existing={dedup: ("different", None, None, None)})
        with self.assertRaises(ValueError):
            self.admit([bad], other)

    def test_identified_conflict_aborts(self):
        a = P("t1")
        execution = execution_id(*a)
        with self.assertRaisesRegex(ValueError, "observation_revision_conflict"):
            self.admit([a], BatchConnection(conflicts={f"input:{execution}:1"}))

    def test_body_reference_rows(self):
        conn, outcomes, calls = self.admit([M("t1", "m1"), B("m1")], mode="csv", position=7)
        self.assertTrue(all(o["created"] for o in outcomes))
        params = conn.sql("INSERT INTO data_observations")[0]
        self.assertEqual(params[3], ["M", "B"])
        # B: absent source time, raw JSON as stored values, content hash = arrival hash, body flag.
        self.assertEqual((params[8][1], params[9][1], params[10][1], params[11][1]), (None, None, "absent", '{"rule":"source-time-v1"}'))
        self.assertEqual((params[12][1], params[13][1], params[14][1]), (json.dumps(B("m1")[1]), '["body_values_not_collected"]', "[]"))
        self.assertEqual(params[7][1], digest(B("m1")[1]))
        bodies = conn.sql("INSERT INTO data_message_bodies")[0]
        self.assertEqual((bodies[0], bodies[1], bodies[2]), (SOURCE, [outcomes[1]["observationId"]], ["m1"]))
        self.assertEqual(conn.sql("INSERT INTO data_messages")[0][0], [outcomes[0]["observationId"]])
        origin = conn.sql("INSERT INTO data_input_origins")[0]
        self.assertEqual(origin[:5], (RUN, "csv", 7, "csv", "replay"))
        self.assertEqual(calls, [])
        self.assertIsNone(params[1])  # CSV api_received_at

    def test_layer_rows_and_source_time(self):
        raw = P("t1", start_time_raw="2026-11-15 09:00:00", end_time_raw="2026-11-15 10:00:00", success_count="3")
        conn, outcomes, _ = self.admit([raw, T("t1")], BatchConnection(basis={"status": "verified", "timezone": "UTC"}))
        params = conn.sql("INSERT INTO data_observations")[0]
        self.assertEqual(params[1], RECEIVED)
        self.assertEqual((params[8][0], params[9][0], params[10][0]),
                         ("2026-11-15 10:00:00", "2026-11-15T10:00:00+00:00", "verified_tz"))
        process = conn.sql("INSERT INTO data_processes")[0]
        self.assertEqual(process[0], [outcomes[0]["observationId"]])
        columns = input_store.TEXT_FIELDS["P"] + input_store.NUMERIC_FIELDS["P"]
        values = dict(zip(columns, process[1:]))
        self.assertEqual((values["transaction_id"], str(values["success_count"][0])), (["t1"], "3"))
        self.assertEqual(len(conn.sql("INSERT INTO data_transactions")), 1)
        self.assertEqual(len(conn.sql("SELECT clock_basis")), 1)

    def test_detect_jobs_only_for_payloads_with_job_id(self):
        first, second = P("t1"), P("t2")
        conn = BatchConnection()
        holder = {}

        def payloads(_conn, ids, *, config_id=None):
            holder["ids"] = list(ids)
            return [{"jobId": "j1", "inputManifestHash": "h1", "eventId": "j1"}, {"schemaVersion": 1, "eventId": "w"}]

        with mock.patch.object(input_store, "enqueue_observations", side_effect=payloads):
            outcomes = input_store.admit_batch(conn, SOURCE, RUN, "api", [first, T("t1"), second], namespace="api",
                                               config="cfg", phase="initial", received_at=RECEIVED)
        self.assertEqual(outcomes[0]["detectJob"], {"jobId": "j1", "inputManifestHash": "h1"})
        self.assertNotIn("detectJob", outcomes[1]);self.assertNotIn("detectJob", outcomes[2])
        self.assertEqual([str(i) for i in holder["ids"]], [outcomes[0]["observationId"], outcomes[2]["observationId"]])
        self.assertEqual(conn.sql("INSERT INTO data_input_origins")[0][4], "initial")

    def test_duplicates_create_nothing(self):
        a = P("t1")
        conn, outcomes, calls = self.admit([a], BatchConnection(latest={execution_id(*a): (1, stored_hash(*a))}))
        self.assertEqual(outcomes, [{"created": False, "identified": True}])
        self.assertEqual(conn.sql("INSERT INTO data_observations"), [])
        self.assertEqual(calls, [])

    def test_phase_and_receipt_must_match_mode(self):
        for mode, phase, at, code in (("api", None, RECEIVED, "invalid_ingest_phase"), ("api", "replay", RECEIVED, "invalid_ingest_phase"),
                                      ("csv", "live", None, "invalid_ingest_phase"), ("api", "live", None, "api_received_at_mode_mismatch"),
                                      ("csv", "replay", RECEIVED, "api_received_at_mode_mismatch")):
            with self.subTest(mode=mode, phase=phase), self.assertRaisesRegex(ValueError, code):
                self.admit([T("t")], mode=mode, phase=phase, received_at=at)
        with self.assertRaises(ValueError):
            self.admit([T("t")], received_at=datetime(2026, 11, 15))

    def test_single_record_wrapper(self):
        conn = BatchConnection()
        with mock.patch.object(input_store, "enqueue_observations", return_value=[{"eventId": "w"}]):
            outcome = input_store.admit(conn, SOURCE, RUN, "csv", "P", P("t1")[1], namespace="csv:x:0", position=3, phase="replay")
        self.assertTrue(outcome["created"]);self.assertNotIn("detectJob", outcome)

    def test_statement_count_does_not_grow_with_rows(self):
        records = []
        for i in range(200):
            records += [P(f"t{i}", "p1"), P(f"t{i}", "p2"), M(f"t{i}", f"m{i}"), B(f"m{i}"), T(f"t{i}")]
        conn, outcomes, calls = self.admit(records)
        self.assertEqual(sum(o["created"] for o in outcomes), 1000)
        self.assertLessEqual(len(conn.statements), 9)
        self.assertEqual(len(calls[0][0]), 400)


class AssignRevisions(unittest.TestCase):
    def plans(self, records, latest=None):
        plans = input_store.plan_records(SOURCE, "api", records)
        return input_store.assign_revisions(plans, latest or {}, source=SOURCE, namespace="api",
                                            stored_hash=lambda plan: plan["rawHash"])

    def test_a_b_a_gets_three_revisions_and_repeat_is_duplicate(self):
        a, b = P("t", status="S"), P("t", status="F")
        got = [(p["action"], p.get("revision")) for p in self.plans([a, a, b, a, b, b])]
        self.assertEqual(got, [("insert", 1), ("duplicate", None), ("insert", 2), ("insert", 3), ("insert", 4), ("duplicate", None)])

    def test_executions_are_independent_per_layer_and_identity(self):
        got = [(p["action"], p.get("revision")) for p in self.plans([P("t"), T("t"), P("t", retry=1), P("t")])]
        self.assertEqual(got, [("insert", 1), ("insert", 1), ("insert", 1), ("duplicate", None)])


if __name__ == "__main__":
    unittest.main()
