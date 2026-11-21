"""Local-only: shared input admission on a fresh PostgreSQL database (input_store.admit_batch through input_runner.tick).

Default: one API scenario (v04 and v03 waiting-config sources) and one CSV replay with fixed expectations.
--baseline-worker-dir DIR: also run the same scenario with another worker tree (e.g. the per-row admission before the
batch path) on its own fresh database and require the same normalized rows (ids/timestamps replaced by order labels).
--measure-rows N: admission cost of one synthetic API response of about N rows (2 P + 3 M + 1 T per transaction).

Needs BATCH_TEST_ADMIN_URL=postgresql://...@localhost:5432/postgres (localhost only). Creates new databases
qa_batch_admission_<random>; never touches an existing one.
"""
import argparse
import csv
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve()
RECEIVED = datetime(2026, 11, 21, 1, 0, 0, 123456, tzinfo=timezone.utc)


def fresh_database(admin_url):
    import psycopg
    from psycopg import sql
    parsed = urlsplit(admin_url)
    if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path != "/postgres":
        raise SystemExit("only a local postgres admin URL is permitted")
    spec = importlib.util.spec_from_file_location("prepare_runtime_db", HERE.parents[2] / "infra/tests/prepare-runtime-db.py")
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    name = "qa_batch_admission_" + uuid.uuid4().hex[:10]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = urlunsplit(parsed._replace(path="/" + name))
    with psycopg.connect(url, autocommit=True) as conn:
        for file in prepare.migration_names():
            conn.execute((HERE.parents[2] / "infra/migrations" / file).read_text(encoding="utf-8"))
    return url


def tagged(layer, **values):
    return {"layer": layer, "values": values}


def process(tx, pid, status="S", retry=0, start="2026-11-21 10:00:00.100", end="2026-11-21 10:00:00.200", **extra):
    return tagged("P", TRANSACTION_ID=tx, PROCESS_ID=pid, STATUS=status, RETRY_COUNT=retry, START_TIME=start, END_TIME=end,
                  SUCCESS_COUNT=1 if status == "S" else 0, ERROR_COUNT=0 if status == "S" else 1, TOTAL_COUNT=1, **extra)


def polls():
    """Three API responses that exercise every admission rule."""
    first = [
        process("TX1", "P1"), process("TX1", "P1"),                              # exact repeat in one response
        process("TX1", "P2", end="2026-11-21T10:00:00.300+09:00"),               # offset time
        tagged("M", TRANSACTION_ID="TX1", PROCESS_ID="P1", MESSAGE_ID="MX1", RETRY_COUNT=0, PROCESS_DT="2026-11-21 10:00:00",
               DATA_SIZE=512, START_TIME="2026-11-21 10:00:00.100", END_TIME=""),
        tagged("B", MESSAGE_ID="MX1", MESSAGE_IN="dropped", MESSAGE_BODY="dropped"),
        tagged("T", TRANSACTION_ID="TX1", STATUS="S", RETRY_COUNT=0, PROCESS_COUNT=2, START_TIME="bad-time"),
        process("TX2", "P1", "S"), process("TX2", "P1", "F"), process("TX2", "P1", "S"),   # A, B, A
        process("TX3", "P1", retry=-1), process("TX3", "P1", retry=-1),          # unidentified, repeated
        tagged("T", TRANSACTION_ID="TX3", RETRY_COUNT="x"),                      # unidentified T
        tagged("B", MESSAGE_ID="MX-ORPHAN"),
        tagged("P", TRANSACTION_ID="TX4", PROCESS_ID="P1", RETRY_COUNT=None, TOTAL_COUNT="1.50"),  # retry unknown
    ]
    second = first + [tagged("T", TRANSACTION_ID="TX1", STATUS="F", RETRY_COUNT=0, PROCESS_COUNT=2)]
    return [first, second, []]


class FakeAPI:
    def __init__(self, response, at):
        self.response, self.at, self.last_receipt = response, at, None

    def in_progress(self):
        self.last_receipt = {"started_at": self.at - timedelta(milliseconds=5), "finished_at": self.at, "http_status": 200,
                             "response_code": "0000", "response_bytes": 1, "error_code": None, "duration_ms": 5.0,
                             "received_monotonic": time.monotonic()}
        return self.response


def prime(input_runner, conn, source, schedule=None):
    """Make the next tick due now (the runner waits pollSeconds after the previous poll)."""
    conn.execute("UPDATE ops_input_runs SET last_attempt_at=NULL WHERE id=(SELECT run_id FROM ops_input_controls WHERE source_id=%s)", (source,))
    conn.commit()
    if schedule is not None:
        schedule.clear()


def drive_api(modules, conn, key, contract, responses):
    input_store, input_runner = modules
    os.environ["DETECT_JOB_CONTRACT"] = contract
    source = input_store.ensure_source(conn, key); conn.commit()
    revision = conn.execute("SELECT revision FROM ops_input_controls WHERE source_id=%s", (source,)).fetchone()[0]
    input_store.switch(conn, source, mode="api", expected_revision=revision, actor="batch-test", settings={"pollSeconds": 1}, config=None)
    conn.commit()
    results = []
    for index, response in enumerate(responses):
        prime(input_runner, conn, source)
        at = RECEIVED + timedelta(seconds=index)
        result = input_runner.tick(conn, source, api_factory=lambda: FakeAPI(json.loads(json.dumps(response)), at), collect_catalog=False)
        assert "errorCode" not in result, result
        results.append({"accepted": result["accepted"], "duplicates": result["duplicates"], "detectJobs": len(result["detectJobs"])})
    return source, results


def drive_csv(modules, conn, key, folder):
    input_store, input_runner = modules
    os.environ["DETECT_JOB_CONTRACT"] = "v04"
    source = input_store.ensure_source(conn, key); conn.commit()
    files = {}
    specs = [("T", ["TRANSACTION_ID", "STATUS", "RETRY_COUNT"], [["C1", "S", "0"]]),
             ("P", ["TRANSACTION_ID", "PROCESS_ID", "RETRY_COUNT", "STATUS", "END_TIME"],
              [["C1", "P1", "0", "S", "2026-11-21 10:00:00"], ["C1", "P1", "0", "S", "2026-11-21 10:00:00"], ["C1", "P2", "\\N", "F", ""]]),
             ("M", ["TRANSACTION_ID", "PROCESS_ID", "MESSAGE_ID", "PROCESS_DT"], [["C1", "P1", "CM1", "2026-11-21 10:00:00"]]),
             ("B", ["MESSAGE_ID", "MESSAGE_IN", "MESSAGE_OUT", "MESSAGE_BODY"], [["CM1", "x", "y", "z"]])]
    for layer, headers, rows in specs:
        files[layer] = Path(folder) / f"{key}-{layer}.csv"
        with files[layer].open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream); writer.writerow(headers); writer.writerows(rows)
    replay = input_store.stage_csv(conn, source, "batch-ref", files); conn.commit()
    revision = conn.execute("SELECT revision FROM ops_input_controls WHERE source_id=%s", (source,)).fetchone()[0]
    input_store.switch(conn, source, mode="csv", expected_revision=revision, actor="batch-test",
                       settings={"replayId": str(replay), "rate": 10000, "repeat": 2}, config=None)
    conn.commit()
    results = []
    for _ in range(20):
        conn.execute("UPDATE ops_input_runs SET last_success_at=NULL WHERE id=(SELECT run_id FROM ops_input_controls WHERE source_id=%s)", (source,))
        conn.commit()
        result = input_runner.tick(conn, source)
        assert "errorCode" not in result, result
        if result.get("completed"):
            break
        results.append({"accepted": result["accepted"], "duplicates": result["duplicates"], "detectJobs": len(result["detectJobs"])})
    return source, results


def dump(conn, source):
    """Normalized rows of one source: random ids and DB clock values become order labels."""
    observations = conn.execute("""SELECT o.id,o.layer::text,e.execution_key,o.revision_no,o.dedup_key,o.content_hash,o.source_revision_token,
          o.source_log_time_raw,o.source_log_occurred_at,o.api_received_at,o.clock_status,o.parsed_times,o.allowed_values,
          o.quality_flags,o.missing_fields,o.execution_id
        FROM data_observations o LEFT JOIN data_executions e ON e.id=o.execution_id WHERE o.source_id=%s
        ORDER BY o.persisted_at,o.id""", (source,)).fetchall()
    label = {row[0]: f"o{i}" for i, row in enumerate(observations)}
    runs = {row[0]: f"r{i}" for i, row in enumerate(conn.execute(
        "SELECT id FROM ops_input_runs WHERE source_id=%s ORDER BY created_at", (source,)).fetchall())}

    def plain(value):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, uuid.UUID):
            return label.get(value) or runs.get(value) or str(value)
        return value if isinstance(value, (str, int, float, bool, type(None), list, dict)) else str(value)

    rows = []
    for row in observations:
        oid, layer = row[0], row[1]
        item = {"label": label[oid], "values": [plain(v) for v in row[1:]]}
        table = {"T": "data_transactions", "P": "data_processes", "M": "data_messages", "B": "data_message_bodies"}[layer]
        detail = conn.execute(f"SELECT to_jsonb(x) - 'observation_id' FROM {table} x WHERE observation_id=%s", (oid,)).fetchone()
        item["detail"] = detail[0] if detail else None
        origin = conn.execute("SELECT run_id,mode,position,namespace,ingest_phase FROM data_input_origins WHERE observation_id=%s", (oid,)).fetchone()
        item["origin"] = [plain(v) for v in origin] if origin else None
        manifest = conn.execute("""SELECT execution_id,transaction_observation_id,message_observation_refs,readiness,contract_version,
              input_as_of = (SELECT persisted_at FROM data_observations WHERE id=%s)
            FROM run_input_manifests WHERE process_observation_id=%s""", (oid, oid)).fetchall()
        item["manifests"] = [[plain(v) for v in m] for m in manifest]
        jobs = conn.execute("""SELECT j.kind::text,j.status::text,j.contract_version,j.queue::text,j.producer_kind::text,j.input_config_version_id,
              j.input_spec,q.status,q.payload = j.input_spec,j.request_hash = m.content_hash
            FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
            LEFT JOIN powercode_queue.detection_logs q ON q.id=j.id WHERE m.process_observation_id=%s""", (oid,)).fetchall()
        normalized = []
        for job in jobs:
            spec = dict(job[6])
            spec = {k: (label.get(uuid.UUID(v)) if k == "observationId" else
                        {**v, "runId": runs.get(uuid.UUID(v["runId"]))} if k == "inputOrigin" else
                        "<id>" if k in ("eventId", "jobId", "inputManifestId", "inputManifestHash") else v) for k, v in spec.items()}
            normalized.append([plain(v) for v in job[:6]] + [spec, job[7], job[8], job[9]])
        item["jobs"] = normalized
        waiting = conn.execute("""SELECT payload,status FROM powercode_queue.detection_logs
            WHERE payload->>'observationId'=%s AND NOT (payload ? 'jobId')""", (str(oid),)).fetchall()
        item["waiting"] = [[{k: (label.get(uuid.UUID(v)) if k == "observationId" else "<id>" if k == "eventId" else v)
                             for k, v in p.items()}, s] for p, s in waiting]
        rows.append(item)
    counters = conn.execute("""SELECT r.mode,r.status,r.next_position,r.accepted_count,r.duplicate_count,r.unidentified_count,r.last_error
        FROM ops_input_runs r WHERE r.source_id=%s ORDER BY r.created_at""", (source,)).fetchall()
    output = {"observations": rows, "runs": [list(c) for c in counters]}
    # CSV namespaces carry the random run id, so CSV execution ids (uuid5 of the key) differ per database too.
    # Replace every run/execution/observation id inside any string by its order label.
    executions = [row[15] for row in observations if row[15] is not None]
    names = {str(run): name for run, name in runs.items()}
    names.update({str(oid): name for oid, name in label.items()})
    for execution in executions:
        names.setdefault(str(execution), f"e{len(names)}")
    text = json.dumps(output, default=str, sort_keys=True)
    for value, name in names.items():
        text = text.replace(value, name)
    return json.loads(text)


def queue_identity(conn):
    """Detection queue ids: a job message id is its run_jobs id and jobId (input_spec and payload); a waiting-config
    message id is uuid5(NAMESPACE_URL, "waiting-config:<observationId>") as detector/jobs.py builds it."""
    jobs = conn.execute("""SELECT count(*), count(*) FILTER (WHERE j.id IS NULL OR j.input_spec->>'jobId' IS DISTINCT FROM j.id::text
            OR q.payload->>'jobId' IS DISTINCT FROM j.id::text OR q.payload->>'eventId' IS DISTINCT FROM q.id::text)
        FROM powercode_queue.detection_logs q LEFT JOIN run_jobs j ON j.id=q.id WHERE q.payload ? 'jobId'""").fetchone()
    waiting = conn.execute("""SELECT q.id, q.payload FROM powercode_queue.detection_logs q WHERE NOT (q.payload ? 'jobId')""").fetchall()
    wrong = [str(qid) for qid, payload in waiting
             if qid != uuid.uuid5(uuid.NAMESPACE_URL, f"waiting-config:{payload['observationId']}") or payload["eventId"] != str(qid)]
    orphan = conn.execute("""SELECT count(*) FROM run_jobs j WHERE j.kind='detect' AND NOT EXISTS
        (SELECT 1 FROM powercode_queue.detection_logs q WHERE q.id=j.id)""").fetchone()[0]
    conn.commit()
    return {"jobMessages": jobs[0], "jobIdMismatches": jobs[1], "jobsWithoutMessage": orphan,
            "waitingMessages": len(waiting), "waitingIdMismatches": wrong}


def scenario(worker_dir, dsn):
    sys.path.insert(0, str(worker_dir))
    import psycopg
    input_store = importlib.import_module("collector.input_store")
    input_runner = importlib.import_module("collector.input_runner")
    modules = (input_store, input_runner)
    output = {}
    with psycopg.connect(dsn) as conn, tempfile.TemporaryDirectory() as folder:
        for key, contract in (("batch-api-v04", "v04"), ("batch-api-v03", "v03")):
            source, ticks = drive_api(modules, conn, key, contract, polls())
            output[key] = {"ticks": ticks, **dump(conn, source)}
        source, ticks = drive_csv(modules, conn, "batch-csv", folder)
        output["batch-csv"] = {"ticks": ticks, **dump(conn, source)}
        output["queueIdentity"] = queue_identity(conn)
    return output


def check_expectations(result):
    """Counts follow from admitting the polls above one record at a time, in order (arrival revisions)."""
    api = result["batch-api-v04"]
    # poll 1 (14 records): the TX1/P1 repeat and the second unidentified TX3 row are not stored; A,B,A on TX2 is 3 revisions
    assert api["ticks"][0] == {"accepted": 12, "duplicates": 2, "detectJobs": 6}, api["ticks"]
    # poll 2 (same + changed TX1 T): TX2 newest is A, so B then A become revisions 4 and 5; TX1 T becomes revision 2;
    # stored unidentified rows are verified and skipped
    assert api["ticks"][1] == {"accepted": 3, "duplicates": 12, "detectJobs": 2}, api["ticks"]
    assert api["ticks"][2] == {"accepted": 0, "duplicates": 0, "detectJobs": 0}, api["ticks"]
    revisions = {}
    for item in api["observations"]:
        layer, key, revision = item["values"][0], item["values"][1], item["values"][2]
        if key:
            revisions.setdefault((layer, key), []).append(revision)
    assert [v for (layer, key), v in revisions.items() if layer == "P" and '"TX2"' in key] == [[1, 2, 3, 4, 5]], revisions
    assert [v for (layer, key), v in revisions.items() if layer == "T" and '"TX1"' in key] == [[1, 2]], revisions
    assert all(v == list(range(1, len(v) + 1)) for v in revisions.values()), revisions
    unidentified = [item for item in api["observations"] if item["values"][1] is None]
    assert [item["values"][0] for item in unidentified] == ["P", "T"], unidentified
    assert all("execution_identity_unverified" in item["values"][12] for item in unidentified)
    bodies = [item for item in api["observations"] if item["values"][0] == "B"]
    assert len(bodies) == 2 and all(item["values"][9] == "absent" and item["detail"]["collection_status"] == "not_collected"
                                    and item["detail"]["message_in"] is None for item in bodies), bodies
    assert [item["origin"][4] for item in api["observations"]] == ["initial"] * 12 + ["live"] * 3
    assert all(item["values"][8] == RECEIVED.isoformat() for item in api["observations"][:12])
    statuses = {item["values"][0] + ":" + str(item["values"][9]) for item in api["observations"]}
    assert {"P:assumed_tz", "P:offset", "T:unparsed", "M:assumed_tz", "B:absent"} <= statuses, statuses
    processes = [item for item in api["observations"] if item["values"][0] == "P" and item["values"][1]]
    assert len(processes) == 8 and all(len(item["jobs"]) == 1 and item["jobs"][0][2] == "v04" and item["jobs"][0][7] == "pending"
                                       and item["jobs"][0][8] and item["jobs"][0][9] and item["manifests"][0][5] for item in processes)
    assert api["runs"][0][3:6] == [15, 14, 6], api["runs"]
    waiting = result["batch-api-v03"]
    assert all(not item["jobs"] for item in waiting["observations"])
    assert sum(len(item["waiting"]) for item in waiting["observations"]) == 8
    replay = result["batch-csv"]
    # 6 frozen rows (P,P,P,M,B,T) x repeat 2, one row per tick; each cycle is its own namespace; the repeated P is skipped
    assert len(replay["ticks"]) == 12 and sum(t["accepted"] for t in replay["ticks"]) == 10, replay["ticks"]
    assert [item["origin"][2] for item in replay["observations"]] == [0, 2, 3, 4, 5, 6, 8, 9, 10, 11], replay["observations"]
    assert replay["runs"][0][1:6] == ["completed", 12, 10, 2, 0], replay["runs"]
    # 8 API v04 jobs + 4 CSV jobs (2 identified P per cycle x 2 cycles); 8 waiting-config messages from the v03 source
    assert result["queueIdentity"] == {"jobMessages": 12, "jobIdMismatches": 0, "jobsWithoutMessage": 0,
                                       "waitingMessages": 8, "waitingIdMismatches": []}, result["queueIdentity"]


def measure(worker_dir, dsn, rows):
    sys.path.insert(0, str(worker_dir))
    import psycopg
    input_store = importlib.import_module("collector.input_store")
    input_runner = importlib.import_module("collector.input_runner")
    os.environ["DETECT_JOB_CONTRACT"] = "v04"
    response = []
    for i in range((rows + 5) // 6):
        tx = f"MEASURE{i:07d}"
        response += [process(tx, "P1", end="2026-11-21 10:00:00.200"), process(tx, "P2", end="2026-11-21 10:00:00.250")]
        response += [tagged("M", TRANSACTION_ID=tx, PROCESS_ID="P1" if j < 2 else "P2", MESSAGE_ID=f"{tx}-M{j}", RETRY_COUNT=0,
                            STATUS="S", DATA_SIZE=512, END_TIME="2026-11-21 10:00:00.200") for j in range(3)]
        response += [tagged("T", TRANSACTION_ID=tx, STATUS="S", RETRY_COUNT=0, PROCESS_COUNT=2, END_TIME="2026-11-21 10:00:00.250")]
    name = "admit_batch" if hasattr(input_runner, "admit_batch") else "admit"
    original = getattr(input_runner, name)
    spent = [0.0]

    def timed(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            spent[0] += time.perf_counter() - started

    setattr(input_runner, name, timed)
    with psycopg.connect(dsn) as conn:
        source = input_store.ensure_source(conn, "batch-measure"); conn.commit()
        revision = conn.execute("SELECT revision FROM ops_input_controls WHERE source_id=%s", (source,)).fetchone()[0]
        input_store.switch(conn, source, mode="api", expected_revision=revision, actor="batch-test", settings={"pollSeconds": 1}, config=None)
        conn.commit()
        started = time.perf_counter()
        result = input_runner.tick(conn, source, api_factory=lambda: FakeAPI(response, RECEIVED), collect_catalog=False)
        total = time.perf_counter() - started
        assert "errorCode" not in result, result
    count = len(response)
    return {"path": name, "rows": count, "accepted": result["accepted"], "detectJobs": len(result["detectJobs"]),
            "admissionSeconds": round(spent[0], 3), "admissionMsPerRow": round(spent[0] * 1000 / count, 4),
            "admissionRowsPerSecond": round(count / spent[0]), "tickSeconds": round(total, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-dir", default=str(HERE.parents[1]))
    parser.add_argument("--baseline-worker-dir")
    parser.add_argument("--measure-rows", type=int)
    parser.add_argument("--child", choices=["scenario", "measure"])
    parser.add_argument("--dsn")
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.child:
        value = scenario(args.worker_dir, args.dsn) if args.child == "scenario" else measure(args.worker_dir, args.dsn, args.measure_rows)
        Path(args.out).write_text(json.dumps(value, default=str, sort_keys=True), encoding="utf-8")
        return

    admin = os.environ["BATCH_TEST_ADMIN_URL"]

    def child(kind, worker_dir, extra=()):
        dsn = fresh_database(admin)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            out = handle.name
        # A separate process per tree so two `collector` packages never share one interpreter.
        subprocess.run([sys.executable, str(HERE), "--child", kind, "--worker-dir", str(worker_dir), "--dsn", dsn, "--out", out, *extra],
                       check=True, env={**os.environ, "PYTHONPATH": str(worker_dir)})
        value = json.loads(Path(out).read_text(encoding="utf-8")); os.unlink(out)
        return value

    if args.measure_rows:
        rows = ["--measure-rows", str(args.measure_rows)]
        report = {"current": child("measure", args.worker_dir, rows)}
        if args.baseline_worker_dir:
            report["baseline"] = child("measure", args.baseline_worker_dir, rows)
        print(json.dumps(report, indent=1))
        return
    current = child("scenario", args.worker_dir)
    check_expectations(current)
    print("batch admission expectations: ok")
    if args.baseline_worker_dir:
        baseline = child("scenario", args.baseline_worker_dir)
        if baseline != current:
            for key in current:
                if baseline.get(key) != current[key]:
                    print(json.dumps({"source": key, "baseline": baseline.get(key), "current": current[key]}, default=str)[:20000])
            raise SystemExit("batch admission differs from baseline")
        print("batch admission matches baseline: ok")


if __name__ == "__main__":
    main()
