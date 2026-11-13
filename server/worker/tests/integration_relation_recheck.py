"""룰 세트 v3 판정 7단계(재판정 탐색, contracts/relation-rules.md 6절)의 실제 PostgreSQL 시험. 합성 값만 쓰고 localhost 새 DB에서만 돈다.

실행: RECHECK_TEST_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres
      python server/worker/tests/integration_relation_recheck.py
- 새 DB qa_relation_recheck를 만들고(이미 있으면 실패, DROP 없음) prepare-runtime-db.py와 같은 순서로 migration(024 포함)과
  scripts/database-roles.sql을 적용한다. 준비 데이터는 소유자로 넣고, 판정 시도와 탐색 주기 작업은 powercode_detector 역할로
  돌린다(권한 확인).
- CSV 재생 순서(거래 행 T가 PROCESS보다 먼저 커밋)를 그대로 만든다: 첫 판정은 도착 확정 전이라 보류·deferred_to_transaction,
  거래 행 persisted_at을 1분 앞으로 옮겨 최소 지연 30초가 지난 상태를 만든 뒤 탐색이 재판정 작업을 1회만 넣는지 본다.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import integration_detection_decision as base  # noqa: E402
from integration_research_pipeline import source  # noqa: E402
from detector import attempt_v04  # noqa: E402
from detector.jobs import RECHECK_MANIFEST, enqueue_recheck  # noqa: E402
from detector.recheck_store import run_once  # noqa: E402

INFRA = Path(__file__).resolve().parents[2] / "infra"
DATABASE = "qa_relation_recheck"
PRECEDES = {"id": "hard:rel-0000000a", "kind": "precedes", "scope": None, "from": {"processId": "IF_A_PR001"},
            "to": {"processId": "IF_A_PR002"}, "match": None, "severity": "critical", "enabled": True}
REQUIRED = {"id": "hard:rel-0000000b", "kind": "required_step", "scope": {"interfaceId": "IF_A"},
            "from": {"processId": "IF_A_PR009"}, "to": None, "match": None, "severity": "warning", "enabled": True}
DEFERRED = {"ruleId": None, "kind": "required_step", "result": "deferred_to_transaction", "reason": None,
            "waitingFor": {"layer": "T"}}
HELD = {"ruleId": PRECEDES["id"], "kind": "precedes", "result": "held", "reason": "collection_incomplete",
        "waitingFor": {"processId": "IF_A_PR001"}}


def migration_files() -> list[str]:
    text = (INFRA / "tests" / "prepare-runtime-db.py").read_text(encoding="utf-8")
    block = re.search(r"files=\((.*?)\)\n", text, re.S).group(1)
    files = re.findall(r"'([0-9]{3}_[a-z0-9_]+\.sql)'", block)
    assert files[-1] == "024_relation_recheck_runs.sql", files[-3:]
    return files


def prepare(admin_url: str) -> str:
    parsed = urlsplit(admin_url)
    if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path != "/postgres":
        raise SystemExit("only a local postgres admin URL is permitted")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
    url = urlunsplit(parsed._replace(path="/" + DATABASE))
    with psycopg.connect(url, autocommit=True) as conn:
        for name in migration_files():
            conn.execute((INFRA / "migrations" / name).read_text(encoding="utf-8"))
        conn.execute((INFRA / "scripts" / "database-roles.sql").read_text(encoding="utf-8"))
    return url


def as_detector(conn, run):
    conn.execute("SET ROLE powercode_detector")
    conn.commit()
    try:
        return run()
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.commit()


def search(conn, sid):
    return as_detector(conn, lambda: run_once(conn, sid))


def judge(conn, sid, raw):
    """첫 판정(실제 Detector 권한). 반환: (관측 id, 판정 id, evidence)."""
    seen, payload = base.v04_job(conn, sid, "rc", {**raw, "start_time_raw": "2026-01-01 00:00:00"})
    failure = as_detector(conn, lambda: base.attempt(conn, payload))
    assert failure is None, repr(failure)
    decision_id, evidence = conn.execute("SELECT id,evidence FROM run_process_decisions WHERE job_id=%s",
                                         (payload["jobId"],)).fetchone()
    conn.commit()
    return seen.observation_id, decision_id, evidence


def decisions(conn, observation_id):
    rows = conn.execute("""SELECT id,evidence,decision_code,job_id FROM run_process_decisions
        WHERE process_observation_id=%s ORDER BY decided_at,id""", (observation_id,)).fetchall()
    conn.commit()
    return rows


def transaction_row(conn, sid, tx, interface):
    return base.observe_layer(conn, sid, "T", {"transaction_id": tx, "status": "S", "response_code": "0000",
                                               "retry_count": "0", "interface_id": interface}).observation_id


def settle(conn, observation_ids, minutes):
    """거래 행 도착 확정(최소 지연 30초)이 지난 상태: persisted_at을 DB 시계 기준 앞으로 옮긴다(소유자 권한)."""
    conn.execute("UPDATE data_observations SET persisted_at=clock_timestamp()-make_interval(mins => %s) WHERE id=ANY(%s)",
                 (minutes, list(observation_ids)))
    conn.commit()


def step(tx, process, depends, end):
    return {"transaction_id": tx, "process_id": process, "depend_process_id": depends, "end_time_raw": end}


def main():
    admin = os.environ.get("RECHECK_TEST_ADMIN_URL")
    if not admin:
        raise SystemExit("RECHECK_TEST_ADMIN_URL이 필요하다")
    url = prepare(admin)
    os.environ["DATABASE_URL"] = url
    with tempfile.TemporaryDirectory(prefix="powercode-recheck-") as directory, psycopg.connect(url) as conn:
        base.ROOT = Path(directory)
        conn.execute("INSERT INTO ops_system_state(id,revision,detector_state,changed_at) VALUES (1,0,'{}',clock_timestamp())")
        sid = source(conn, "재판정 탐색 시험")

        first = search(conn, sid)
        again = search(conn, sid)
        assert (first.status, first.enqueued, again.status) == ("completed", 0, "idle"), (first, again)
        print("PASS 첫 실행은 커서만 고정(실행 행 1개), 새 행이 없으면 idle — v2 룰 세트·보류 기록 없음에서 아무것도 넣지 않음")

        base.save_rule_set(conn, hard=base.hard_rules(), version=3,
                           extra={"relationRules": [PRECEDES, REQUIRED], "protocolRules": []})
        # CSV 재생 순서: 거래마다 T → P1 → P2. A(IF_A)는 필수 단계 PR009 없음, B(IF_B)는 선행 PR001 없음, C(IF_B)는 정상.
        tx = {name: f"TX{name}-{uuid.uuid4()}" for name in "ABC"}
        t_rows = {"A": transaction_row(conn, sid, tx["A"], "IF_A")}
        a1, a1_decision, a1_evidence = judge(conn, sid, step(tx["A"], "IF_A_PR001", "NONE", "2026-01-01 00:00:01"))
        a2, a2_decision, a2_evidence = judge(conn, sid, step(tx["A"], "IF_A_PR002", "IF_A_PR001", "2026-01-01 00:00:05"))
        t_rows["B"] = transaction_row(conn, sid, tx["B"], "IF_B")
        b2, b2_decision, b2_evidence = judge(conn, sid, step(tx["B"], "IF_A_PR002", "IF_A_PR001", "2026-01-01 00:00:05"))
        t_rows["C"] = transaction_row(conn, sid, tx["C"], "IF_B")
        c1, _, _ = judge(conn, sid, step(tx["C"], "IF_A_PR001", "NONE", "2026-01-01 00:00:01"))
        c2, _, c2_evidence = judge(conn, sid, step(tx["C"], "IF_A_PR002", "IF_A_PR001", "2026-01-01 00:00:05"))
        assert a1_evidence["relationNotes"] == [DEFERRED] and a2_evidence["relationNotes"] == [DEFERRED], a2_evidence
        assert b2_evidence["relationNotes"] == [HELD, DEFERRED], b2_evidence["relationNotes"]
        assert c2_evidence["relationNotes"] == [DEFERRED], c2_evidence["relationNotes"]
        for evidence in (a1_evidence, a2_evidence, b2_evidence, c2_evidence):
            assert evidence["relationInputs"]["transaction"]["arrivalConfirmed"] is False, evidence["relationInputs"]
            assert evidence["composite"]["grade"] == "normal", evidence["composite"]
        print("PASS CSV 재생 순서(T 먼저) 첫 판정: 도착 확정 전이라 선행 없음 held, 필수 단계 deferred_to_transaction, 종합 정상")

        early = search(conn, sid)
        assert (early.enqueued, early.skipped) == (0, {"transaction_settling": 3}), early
        print("PASS 거래 행 30초 안: (b) 판정 이벤트로 거래를 보지만 도착 확정 전이라 넘기지 않음")

        settle(conn, t_rows.values(), 1)
        found = search(conn, sid)
        assert (found.transactions, found.candidates, found.enqueued) == (3, 5, 2), found
        assert found.skipped == {"open_result_absent": 3}, found.skipped
        repeat = search(conn, sid)
        assert (repeat.enqueued, repeat.skipped) == (0, {"already_rechecked": 2, "open_result_absent": 3}), repeat
        manifests = conn.execute("""SELECT process_observation_id,readiness,transaction_observation_id,
            message_observation_refs,contract_version FROM run_input_manifests WHERE readiness ? 'relation'
            ORDER BY created_at""").fetchall()
        conn.commit()
        assert sorted(str(row[0]) for row in manifests) == sorted([str(a2), str(b2)]), manifests
        relation = next(row[1]["relation"] for row in manifests if row[0] == a2)
        assert relation == {"recheck": 1, "recheckedDecisionId": str(a2_decision),
                            "rows": [{"layer": "T", "observation_id": str(t_rows["A"]), "revision_no": 1}]}, relation
        assert all(row[2] is None and row[3] == [] and row[4] == "v03" for row in manifests), manifests
        assert as_detector(conn, lambda: enqueue_recheck(conn, a2, a2_decision, [
            {"observation_id": t_rows["A"], "revision_no": 1}])) is None
        print("PASS 30초 뒤 탐색: 종합이 바뀌는 판정 2건(A 마지막 PROCESS, B 선행 없음)만 재판정 작업 1회, "
              "정상으로 풀리는 3건은 넘기지 않음, 두 번째 탐색·직접 재삽입은 무시(UNIQUE(source_id, content_hash))")

        for job in found.jobs:
            failure = as_detector(conn, lambda job=job: base.attempt(conn, {"jobId": job}))
            assert failure is None, repr(failure)
        a_rows, b_rows = decisions(conn, a2), decisions(conn, b2)
        assert len(a_rows) == 2 and len(b_rows) == 2 and len(decisions(conn, a1)) == 1, (a_rows, b_rows)
        assert a_rows[0][1] == a2_evidence and b_rows[0][1] == b2_evidence  # 옛 판정 행은 그대로(append-only)
        a_new, b_new = a_rows[1][1], b_rows[1][1]
        assert a_rows[1][2] == "REL_REQUIRED_STEP" and a_new["composite"]["grade"] == "anomaly", a_new["composite"]
        assert a_new["relationRecheck"] == {"recheckedDecisionId": str(a2_decision),
                                            "transactionObservationId": str(t_rows["A"])}, a_new["relationRecheck"]
        assert base.method(a_new, PRECEDES["id"])["anomalous"] is False and a_new["relationNotes"] == [], a_new
        assert a_new["relationInputs"]["transaction"]["arrivalConfirmed"] is True, a_new["relationInputs"]
        precedes = base.method(b_new, PRECEDES["id"])
        assert (b_rows[1][2], precedes["anomalous"], precedes["evidence"][0]["violation"]) == (
            "REL_PRECEDES", True, "predecessor_missing"), precedes
        assert b_new["composite"]["basisMethods"] == [PRECEDES["id"]], b_new["composite"]
        measured = conn.execute("SELECT count(*) FROM ops_pipeline_measurements WHERE observation_id=ANY(%s)",
                                ([a2, b2],)).fetchone()[0]
        conn.commit()
        assert measured == 2, measured  # 첫 판정 측정 행만(재판정은 측정 행 없음)
        print("PASS 재판정 시도: 새 판정 행 추가(옛 행 그대로) — A 필수 단계 없음 → REL_REQUIRED_STEP, "
              "B 선행 없음 → REL_PRECEDES(predecessor_missing), relationRecheck 기록, 측정 행 없음")

        after = search(conn, sid)
        assert (after.enqueued, after.skipped) == (0, {"open_result_absent": 3}), after
        print("PASS 재판정 판정은 다시 대상이 되지 않음(1회)")

        # (b) 판정 이벤트 커서: (a) 커서·겹침을 이미 지난 거래 행(5분 전)의 판정이, 시도가 거래 행을 보기 전에 입력을 읽은 경우.
        tx["D"] = f"TXD-{uuid.uuid4()}"
        t_rows["D"] = transaction_row(conn, sid, tx["D"], "IF_B")
        settle(conn, [t_rows["D"]], 5)
        real = attempt_v04.read_relation_inputs

        def before_transaction(*args):
            return dataclasses.replace(real(*args), transaction_executions=0, transaction=None,
                                       transaction_ambiguous=False, transaction_confirmed=False,
                                       common_interface_id=None)

        with patch.object(attempt_v04, "read_relation_inputs", before_transaction):
            d2, d2_decision, d2_evidence = judge(conn, sid, step(tx["D"], "IF_A_PR002", "IF_A_PR001",
                                                                 "2026-01-01 00:00:05"))
        assert d2_evidence["relationNotes"] == [HELD, DEFERRED], d2_evidence["relationNotes"]
        late = search(conn, sid)
        assert (late.transactions, late.candidates, late.enqueued) >= (1, 1, 1) and late.enqueued == 1, late
        relation = conn.execute("SELECT readiness->'relation' FROM run_input_manifests WHERE process_observation_id=%s "
                                "AND readiness ? 'relation'", (d2,)).fetchone()[0]
        conn.commit()
        assert relation["recheckedDecisionId"] == str(d2_decision), relation
        print("PASS (b) 판정 이벤트 커서: 거래 행이 커서를 지난 뒤 커밋된 보류 판정도 1회 재판정")

        quiet = search(conn, sid)
        assert quiet.enqueued == 0, quiet
        runs, cursor = conn.execute("""SELECT count(*), max(event_cursor) FROM run_recheck_runs WHERE source_id=%s""",
                                    (sid,)).fetchone()
        hashes = conn.execute("SELECT count(*) FROM run_input_manifests WHERE readiness ? 'relation'").fetchone()[0]
        conn.commit()
        assert hashes == 3 and runs >= 5 and cursor > 0, (hashes, runs, cursor)
        try:
            conn.execute("UPDATE run_recheck_runs SET enqueued=0")
            raise AssertionError("run_recheck_runs must be immutable")
        except psycopg.Error:
            conn.rollback()
        unique = conn.execute("""SELECT count(DISTINCT content_hash) FROM run_input_manifests
            WHERE readiness ? 'relation'""").fetchone()[0]
        conn.commit()
        assert unique == 3 and RECHECK_MANIFEST == "process-input-v2-recheck"
        print("PASS 마지막 탐색 새 작업 없음, 실행 행 불변(024 트리거), 재판정 manifest 3개")
    print("PASS integration_relation_recheck")


if __name__ == "__main__":
    main()
