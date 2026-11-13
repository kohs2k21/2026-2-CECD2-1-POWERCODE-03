"""export_server_captures를 격리된 로컬 시험 DB(합성 행)로 끝까지 돌린다. 고객 데이터·GPU 없음.

전제: migration 001~022가 적용된 빈 시험 DB, DATABASE_URL 경로 /qa_rule_v3_export(localhost, 질의 문자열 없음).
순서: 합성 원천 수집(ingest_observation) → 라벨 → build_snapshot(persisted_at 70/15/15) → export(READ ONLY)
→ run_cases --cpu-only(validate-feature·기준선 사전 검사·replay) → judge.
persisted_at 순서와 원천 시각 순서를 반대로 둬서 export가 저장 시각이 아니라 원천 시각으로 자르는지 본다.

    DATABASE_URL=postgresql://postgres@localhost:<port>/qa_rule_v3_export \
      RV3_WORK=D:/Temp/powercode-db-test/<dir> python tests/integration_export_pg.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "server" / "worker"))
sys.path.insert(0, str(HERE))

import psycopg  # noqa: E402

from collector.storage import ingest_observation  # noqa: E402
from trainer.snapshot import build_snapshot  # noqa: E402
import capture_layout as layout  # noqa: E402

TRANSACTIONS = 120
SECRET = "SECRETTX"


def _execution(conn, source_id, layer, key):
    execution_id = uuid.uuid4()
    conn.execute("""INSERT INTO data_executions (id, source_id, layer, execution_key, key_parts, key_contract_version,
                     first_observed_at) VALUES (%s, %s, %s, %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                 (execution_id, source_id, layer, key))
    return execution_id


def _ingest(conn, source_id, layer, key, raw, persisted_at):
    observation = ingest_observation(conn, source_id=source_id, layer=layer, dedup_key=f"rv3:{source_id}:{key}",
                                     raw=raw, execution_id=_execution(conn, source_id, layer, key), revision_no=1)
    conn.execute("UPDATE data_observations SET persisted_at=%s WHERE id=%s", (persisted_at, observation.observation_id))
    return observation.observation_id


def seed(conn, source_id):
    """원천 시각은 거래 번호 순, 저장 시각은 역순. 거래 2개씩 같은 원천 시각(동률), 해석 실패·F·anomaly 행 포함."""
    stored = datetime.now(timezone.utc) - timedelta(hours=2)
    source = datetime(2026, 8, 3, 10, 0, 0)
    labelled = []
    for tx in range(TRANSACTIONS):
        persisted = stored + timedelta(seconds=TRANSACTIONS - tx)
        at = source + timedelta(seconds=20 * (tx // 2))
        tx_id = f"{SECRET}-{tx:04d}"
        _ingest(conn, source_id, "T", f"T:{tx_id}", {"transaction_id": tx_id, "interface_id": f"IFSECRET{tx % 3}",
                                                      "status": "S"}, persisted)
        for proc in range(2):
            end = at + timedelta(seconds=3 * proc)
            raw = {"transaction_id": tx_id, "process_id": f"P{proc}", "status": "S",
                   "start_time_raw": (end - timedelta(milliseconds=200 + tx)).isoformat(sep=" ", timespec="milliseconds"),
                   "end_time_raw": end.isoformat(sep=" ", timespec="milliseconds"),
                   "total_count": 10, "success_count": 10 - tx % 4, "error_count": tx % 4, "retry_count": tx % 2}
            if tx == 97 and proc == 1:
                raw.update(start_time_raw="N/A", end_time_raw="not-a-time")
            if tx == 99 and proc == 1:
                raw["status"] = "F"
            if tx == 101 and proc == 1:
                raw["total_count"] = "x12"  # 숫자 아님 → 빈 값으로 세어짐
            oid = _ingest(conn, source_id, "P", f"P:{tx_id}:{proc}", raw, persisted)
            if tx == 103 and proc == 0:
                labelled.append(oid)
    for oid in labelled:
        conn.execute("""INSERT INTO data_label_evidence (id, observation_id, revision_no, label, reason_code,
                         created_by_actor_id) VALUES (%s, %s, 1, 'anomaly', 'TEST_LABEL', 'test-admin')""",
                     (uuid.uuid4(), oid))
    conn.commit()
    return stored


def main() -> None:
    dsn = os.environ.get("DATABASE_URL", "")
    parsed = urlsplit(dsn)
    if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path != "/qa_rule_v3_export" or parsed.query:
        raise SystemExit("local isolated DATABASE_URL with path /qa_rule_v3_export required")
    work = Path(os.environ["RV3_WORK"]).resolve()
    source_id = uuid.uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute("""INSERT INTO ops_sources (id, dataset_key, display_name, ingestion_method, schema_version,
                         clock_basis, field_catalog, created_at)
                       VALUES (%s, %s, 'Rule v3 export test', 'synthetic-test', 'v1', '{}'::jsonb, '{}'::jsonb,
                               clock_timestamp())""", (source_id, f"rv3-{source_id}"))
        conn.commit()
        stored = seed(conn, source_id)
        snapshot = build_snapshot(conn, source_id=source_id, from_time=(stored - timedelta(minutes=1)).isoformat(),
                                  until_time=(stored + timedelta(hours=1)).isoformat(), actor_id="test-admin")
        conn.commit()
        train = conn.execute("SELECT count(*) FROM data_snapshot_members WHERE snapshot_id=%s AND split='train'",
                             (snapshot.id,)).fetchone()[0]
        before = conn.execute("SELECT count(*) FROM data_snapshots").fetchone()[0]

    env = {**os.environ, "PYTHONUTF8": "1"}
    inside = subprocess.run([sys.executable, str(HERE / "export_server_captures.py"), "--snapshot-id", str(snapshot.id),
                             "--output-dir", str(HERE / "tmp-export")], capture_output=True, text=True, env=env)
    assert inside.returncode != 0 and "outside the git repository" in inside.stderr, inside.stderr

    out = work / "export"
    done = subprocess.run([sys.executable, str(HERE / "export_server_captures.py"), "--snapshot-id", str(snapshot.id),
                           "--output-dir", str(out), "--per-interface-top", "2"], capture_output=True, text=True, env=env)
    print(done.stdout)
    assert done.returncode == 0, done.stderr
    printed = done.stdout + done.stderr
    assert SECRET not in printed and "IFSECRET" not in printed and "2026-08-03" not in printed, "values printed"
    again = subprocess.run([sys.executable, str(HERE / "export_server_captures.py"), "--snapshot-id", str(snapshot.id),
                            "--output-dir", str(out)], capture_output=True, text=True, env=env)
    assert again.returncode != 0 and "already exists" in again.stderr

    index = layout.read_json(out / "index.json")
    g = index["counts"]["global"]
    assert g["observations"] == train, (g["observations"], train)
    assert g["excluded_status_not_S"] >= 1 and g["excluded_anomaly_label"] == 1, g
    assert g["grouping"]["excluded_unparsed_time"] == 1, g["grouping"]
    assert g["value_parse_failures"].get("unparsed_TOTAL_COUNT") == 1, g["value_parse_failures"]
    assert g["cut"]["reference_blocks"] < g["cut"]["reference_groups"], g["cut"]  # 원천 시각 동률이 블록으로 합쳐짐
    assert len(index["cases"]) == 18 and index["cases"][6]["scope"] == "interface", len(index["cases"])
    assert "IFSECRET" not in (out / "index.json").read_text(encoding="utf-8")
    private = layout.read_json(out / "private/interface_map.json")
    assert set(private["interface_rank_to_interface_id"].values()) <= {"IFSECRET0", "IFSECRET1", "IFSECRET2"}
    # 원천 시각 분할: 저장 시각은 역순이므로 persisted_at 기준이었다면 참조가 원천상 더 늦은 거래가 된다.
    first = out / index["cases"][4]["path"]
    ref = layout.read_json(first / "reference/capture.json")["period"]
    app = layout.read_json(first / "application/capture.json")["period"]
    assert ref["end_exclusive"] <= app["start_inclusive"] and ref["start_inclusive"] < ref["end_exclusive"]
    for case in index["cases"]:
        assert case["held_window_overlap"] is False
        for rel, digest in case["files"].items():
            assert layout.sha256_file(out / case["path"] / rel) == digest, rel
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM data_snapshots").fetchone()[0] == before  # 읽기만 함

    runs = work / "runs"
    cpu = subprocess.run([sys.executable, str(HERE / "run_cases.py"), "--cases-dir", str(out), "--output-root", str(runs),
                          "--run-name", "export-cpu", "--cpu-only"], capture_output=True, text=True, env=env)
    print(cpu.stdout[-2000:])
    assert SECRET not in cpu.stdout + cpu.stderr and "IFSECRET" not in cpu.stdout + cpu.stderr
    run_dir = next(runs.glob("*-export-cpu"))
    summary = layout.read_json(run_dir / "summary.json")
    md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert SECRET not in md and "IFSECRET" not in md
    print("summary:", summary["passed"], "/", summary["total"])
    assert summary["passed"] == summary["total"] == 18, [c["reasons"] for c in summary["cases"]]
    print("integration_export_pg OK")


if __name__ == "__main__":
    main()
