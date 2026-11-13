"""층 C 사건 묶음 주기 작업(detector/incidents_store.py)의 실제 PostgreSQL 시험. 합성 값만 쓰고 localhost 새 DB에서만 돈다.

실행: INCIDENTS_TEST_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres
      python server/worker/tests/integration_incidents.py
- 새 DB qa_incidents를 만들고(이미 있으면 실패, DROP 없음) prepare-runtime-db.py와 같은 순서(001~021)로 migration을
  적용한 뒤 scripts/database-roles.sql을 적용한다. 준비 데이터는 소유자(postgres)로 넣고, 주기 작업은
  powercode_detector 권한만 받은 로그인 역할로 돌린다(권한 확인). 로그인 역할은 이 클러스터에만 만든다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from pathlib import Path
import re
import sys
from unittest.mock import patch
import os
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.storage import ingest_observation
from detector import incidents_store
from detector.incidents import CRITERIA_VERSION, INCIDENT_LOCK_CLASS, incident_id
from detector.incidents_store import run_once

INFRA = Path(__file__).resolve().parents[2] / "infra"
DATABASE = "qa_incidents"
LOGIN = "qa_incident_detector"
LOGIN_PASSWORD = secrets.token_hex(16)  # 이 시험 클러스터 전용 1회용 값(CI는 비밀번호 인증)
KST = timezone(timedelta(hours=9))
DECIDED = datetime(2000, 1, 2, 0, 0, tzinfo=timezone.utc)  # 원문 시각과 멀리 떨어진 판정 시각(대체 확인용)
CHECKS: list[str] = []


def check(name: str, condition: bool, detail=None) -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail!r}")
    CHECKS.append(name)


def migration_files() -> list[str]:
    """prepare-runtime-db.py의 files 튜플을 그대로 읽어 순서가 어긋나지 않게 한다."""
    text = (INFRA / "tests" / "prepare-runtime-db.py").read_text(encoding="utf-8")
    block = re.search(r"files=\((.*?)\)\n", text, re.S).group(1)
    files = re.findall(r"'([0-9]{3}_[a-z0-9_]+\.sql)'", block)
    order = ["018_decision_observation_index.sql", "019_incidents.sql", "020_rule_set_hash_v3.sql", "021_recommend_relations.sql"]
    missing = [name for name in order if name not in files]
    assert not missing, (missing, files[-4:])
    positions = [files.index(name) for name in order]
    assert positions == sorted(positions), (order, files[-4:])
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
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (LOGIN,)).fetchone():
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(LOGIN)))
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(LOGIN), sql.Literal(LOGIN_PASSWORD)))
        conn.execute(sql.SQL("GRANT powercode_detector TO {}").format(sql.Identifier(LOGIN)))
    return url


class Fixture:
    """합성 원본·관측·판정. 판정은 작업 없는 legacy 행(구성 필수)으로 넣는다 — 트리거가 final 이벤트를 만든다."""

    def __init__(self, conn, clock: dict):
        self.conn = conn
        self.source = uuid.uuid4()
        conn.execute("""INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,
            field_catalog,created_at) VALUES (%s,%s,'incident fixture','synthetic-test','v1',%s::jsonb,'{}',clock_timestamp())""",
                     (self.source, f"incident-{self.source}", json.dumps(clock)))
        self.config = uuid.uuid4()
        conn.execute("""INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,
            created_by_actor_id,created_at) VALUES (%s,%s,'{}','{}','synthetic','integration',clock_timestamp())""",
                     (self.config, self.source))
        conn.commit()
        self.n = 0

    def execution(self, layer: str) -> uuid.UUID:
        self.n += 1
        execution = uuid.uuid4()
        self.conn.execute("""INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,
            first_observed_at) VALUES (%s,%s,%s,%s,'{}','test-v1',clock_timestamp())""",
                          (execution, self.source, layer, f"{layer}-{self.n}"))
        return execution

    def transaction(self, tx: str, code: str, execution: uuid.UUID | None = None, revision: int = 1):
        execution = execution or self.execution("T")
        ingest_observation(self.conn, source_id=self.source, layer="T", dedup_key=f"T-{execution}-{revision}",
                           raw={"transaction_id": tx, "response_code": code, "status": "F"},
                           execution_id=execution, revision_no=revision)
        self.conn.commit()
        return execution

    def decision(self, tx: str | None, channel: str | None, end: str | None, grade: str | None = "anomaly", *,
                 schema: int = 4, execution: uuid.UUID | None = None, revision: int = 1, start: str | None = None):
        """PROCESS 관측 1건 + 판정 1건. end·start = KST 원문(시간대 없음)."""
        execution = execution or self.execution("P")
        raw = {"transaction_id": tx, "process_id": f"P-{self.n}", "channel_id": channel, "end_time_raw": end,
               "start_time_raw": start, "status": "F"}
        observed = ingest_observation(self.conn, source_id=self.source, layer="P",
                                      dedup_key=f"P-{execution}-{revision}", raw=raw,
                                      execution_id=execution, revision_no=revision)
        evidence = {"schemaVersion": schema, "composite": {"grade": grade, "anomalous": grade != "normal"}}
        decision = uuid.uuid4()
        self.conn.execute("""INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,
            input_manifest_hash,status,rule_status,ml_status,evidence,decided_at,latency_status)
            VALUES (%s,%s,%s,%s,'synthetic','complete','succeeded','skipped',%s::jsonb,%s,'unavailable')""",
                          (decision, execution, observed.observation_id, self.config, json.dumps(evidence), DECIDED))
        self.conn.commit()
        return str(decision), execution


def kst(minute: int) -> str:
    return (datetime(2000, 1, 1, tzinfo=KST) + timedelta(minutes=minute)).strftime("%Y-%m-%d %H:%M:%S")


def utc(minute: int) -> datetime:
    return (datetime(2000, 1, 1, tzinfo=KST) + timedelta(minutes=minute)).astimezone(timezone.utc)


def counts(conn) -> tuple[int, int, int, int]:
    row = conn.execute("""SELECT (SELECT count(*) FROM run_incidents), (SELECT count(*) FROM run_incident_members),
        (SELECT count(*) FROM run_incident_events), (SELECT count(*) FROM run_incident_runs)""").fetchone()
    conn.commit()
    return tuple(row)


def incident_of(conn, decision: str) -> uuid.UUID:
    row = conn.execute("SELECT incident_id FROM run_incident_members WHERE decision_id = %s", (decision,)).fetchone()
    conn.commit()
    return row[0] if row else None


def main() -> None:
    url = prepare(os.environ["INCIDENTS_TEST_ADMIN_URL"])
    detector_url = urlunsplit(urlsplit(url)._replace(netloc=f"{LOGIN}:{LOGIN_PASSWORD}@{urlsplit(url).netloc.split('@')[-1]}"))
    with psycopg.connect(url) as owner, psycopg.connect(detector_url) as job, psycopg.connect(url) as other:
        check("job_role_not_superuser", not job.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
              .fetchone()[0])
        job.commit()
        fx = Fixture(owner, {"status": "verified", "timezone": "Asia/Seoul"})
        side = Fixture(owner, {"status": "unknown"})
        source = str(fx.source)

        # 1회차 입력: 응답코드 키·채널 대체·자격 제외·최신 아님·시각 대체·다른 원본
        fx.transaction("TX-1", "E901")
        fx.transaction("TX-2", "E901")
        fx.transaction("TX-3", "0000")
        fx.transaction("TX-9", "E902")
        fx.transaction("TX-9", "E902")  # 실행 2개 → 응답코드 키 없음 → channel
        fx.transaction("TX-7", "E901")
        d1, _ = fx.decision("TX-1", "CHA", kst(0))
        d2, _ = fx.decision("TX-2", "CHA", kst(3))
        d3, _ = fx.decision("TX-3", "CHA", kst(1))  # 성공 코드 → channel_id:CHA
        d4, _ = fx.decision("TX-4", "CHA", kst(2))  # 거래 행 없음 → channel_id:CHA
        d5, _ = fx.decision("TX-5", "CHA", kst(2), grade="normal")
        d6, _ = fx.decision("TX-6", "CHA", kst(2), grade=None, schema=3)
        d7, _ = fx.decision("TX-7", "CHA", kst(20))
        old, execution = fx.decision("TX-8", "CHA", kst(2))
        newer, _ = fx.decision("TX-8", "CHA", kst(2), grade="normal", execution=execution, revision=2)
        d9, _ = fx.decision("TX-9", "CHB", kst(5))
        d13, _ = fx.decision("TX-13", "CHC", "bad", start=" \\N ")  # 시각 해석 불가 → decided_at
        side.decision("TX-1", "CHA", kst(0))  # 다른 원본: 읽지 않음
        first_events = owner.execute("""SELECT max(e.id) FROM run_decision_events e JOIN run_process_decisions d
            ON d.id = e.decision_id JOIN data_executions x ON x.id = d.execution_id WHERE x.source_id = %s""",
                                     (fx.source,)).fetchone()[0]
        owner.commit()

        out = run_once(job, source)
        check("run1_completed", out.status == "completed", out)
        check("run1_cursor", (out.cursor_from, out.cursor_to) == (0, first_events), out)
        check("run1_counts", (out.events, out.opened_incidents, out.added_members, out.merged_incidents) ==
              (11, 5, 7, 0), out)
        check("run1_excluded", out.excluded == {"grade_not_eligible": 3, "not_latest": 1}, out.excluded)
        check("run1_fallback", out.fallback_members == 1, out)
        check("run1_rows", counts(owner) == (5, 7, 0, 1), counts(owner))
        a = incident_of(owner, d1)
        check("same_code_same_incident", incident_of(owner, d2) == a)
        check("channel_fallback_incident", incident_of(owner, d3) == incident_of(owner, d4) != a)
        check("gap_splits", incident_of(owner, d7) not in (None, a))
        check("not_eligible_absent", all(incident_of(owner, d) is None for d in (d5, d6, old, newer)))
        rows = dict(owner.execute("""SELECT m.decision_id::text, i.group_key FROM run_incident_members m
            JOIN run_incidents i ON i.id = m.incident_id""").fetchall())
        owner.commit()
        check("group_keys", (rows[d1], rows[d3], rows[d9], rows[d13]) ==
              ("response_code:E901", "channel_id:CHA", "channel_id:CHB", "channel_id:CHC"), rows)
        expected_a = incident_id(source, CRITERIA_VERSION, "response_code:E901", utc(0))
        check("uuid5_incident_id", str(a) == expected_a, (a, expected_a))
        basis = dict(owner.execute("SELECT decision_id::text, occurred_basis FROM run_incident_members").fetchall())
        stored = owner.execute("""SELECT opened_at, time_basis, criteria->>'version', added_by_run FROM run_incidents
            WHERE id = %s""", (a,)).fetchone()
        owner.commit()
        check("occurred_basis", basis[d1] == "process_time_raw" and basis[d13] == "decided_at", basis)
        assumed = owner.execute("SELECT criteria->>'assumedTimezone' FROM run_incidents WHERE id = %s", (a,)).fetchone()[0]
        owner.commit()
        check("criteria_assumed_timezone", assumed == "Asia/Seoul", assumed)
        check("incident_row", stored[:3] == (utc(0), "process_time_raw", CRITERIA_VERSION)
              and str(stored[3]) == out.run_id, stored)

        # 2회차: 새 이벤트 없음 → 실행 행 없이 idle
        again = run_once(job, source)
        check("run2_idle", (again.status, again.cursor_from) == ("idle", first_events), again)
        check("run2_no_rows", counts(owner) == (5, 7, 0, 1), counts(owner))
        # 같은 범위 재처리(after=0): 모든 판정이 이미 구성원 → 새 사건·구성원·이벤트 없음, 실행 행만 1개
        replay = run_once(job, source, after=0)
        check("replay_no_actions", (replay.opened_incidents, replay.added_members, replay.merged_incidents) == (0, 0, 0),
              replay)
        check("replay_already_member", replay.excluded.get("already_member") == 7, replay.excluded)
        check("replay_rows", counts(owner) == (5, 7, 0, 2), counts(owner))

        # 잠금: 다른 연결이 (81741, 0)을 잡고 있으면 skipped, 아무것도 쓰지 않음
        fx.decision("TX-1", "CHA", kst(4))
        other.execute("SELECT pg_advisory_xact_lock(%s, 0)", (INCIDENT_LOCK_CLASS,))
        locked = run_once(job, source)
        other.rollback()
        check("lock_skipped", locked.status == "skipped", locked)
        check("lock_no_rows", counts(owner) == (5, 7, 0, 2), counts(owner))

        # 실패: 쓰기 전 검사에서 예외 → 전체 되돌림, 커서 그대로
        with patch.object(incidents_store, "check_result_scope", side_effect=ValueError("injected")):
            try:
                run_once(job, source)
                raise AssertionError("expected failure")
            except ValueError as exc:
                check("failure_raised", str(exc) == "injected", exc)
        check("failure_rolled_back", counts(owner) == (5, 7, 0, 2) and
              job.info.transaction_status == psycopg.pq.TransactionStatus.IDLE, counts(owner))
        # 구성원 수 불일치: 이미 구성원인 판정을 모르는 척하면(구성원 여부 읽기 대체) ON CONFLICT로 빠진 행이 생겨 전체 되돌림
        with patch.object(incidents_store, "_assigned", return_value=set()):
            try:
                run_once(job, source, after=0, batch_limit=2)
                raise AssertionError("expected member conflict")
            except ValueError as exc:
                check("member_conflict_raised", str(exc) == "incident_member_conflict", exc)
        check("member_conflict_rolled_back", counts(owner) == (5, 7, 0, 2), counts(owner))

        # 3회차(작은 배치 반복): 병합·이어짐. d10 00:16은 B(00:20) 시작 직전 → 새 사건 C, B → C 병합.
        # d11 00:07은 A(00:00~00:03)에 참여. CHA 00:05~01:00(5분 간격)은 CHA 사건, 01:04는 구간(60분) 초과 → 이어짐
        fx.transaction("TX-10", "E901")
        fx.transaction("TX-11", "E901")
        d10, _ = fx.decision("TX-10", "CHA", kst(16))
        d11, _ = fx.decision("TX-11", "CHA", kst(7))
        chain = [fx.decision(f"TX-C{m}", "CHA", kst(m))[0] for m in range(5, 61, 5)]
        tail, _ = fx.decision("TX-CT", "CHA", kst(64))
        before = counts(owner)
        runs, previous = [], replay.cursor_to
        while True:
            step = run_once(job, source, batch_limit=5)
            if step.status == "idle":
                break
            check("cursor_contiguous", step.status == "completed" and step.cursor_from == previous, (step, previous))
            previous = step.cursor_to
            runs.append(step)
        check("small_batches", len(runs) == 4 and all(r.events <= 5 for r in runs), [r.events for r in runs])
        added = tuple(x - y for x, y in zip(counts(owner), before))
        # 구성원: 잠금 시험 때 넣은 TX-1 00:04(A 참여) + d10 + d11 + chain 12 + tail = 16
        check("run3_rows", added == (2, 16, 1, 4), added)
        b, c = incident_of(owner, d7), incident_of(owner, d10)
        merge = owner.execute("SELECT incident_id, target_incident_id FROM run_incident_events").fetchall()
        owner.commit()
        check("merge_row", merge == [(b, c)], merge)
        check("late_join", incident_of(owner, d11) == a)
        cha = incident_of(owner, d3)
        check("chain_joined", {incident_of(owner, d) for d in chain} == {cha})
        cont = owner.execute("SELECT continues_from, opened_at FROM run_incidents WHERE id = %s",
                             (incident_of(owner, tail),)).fetchone()
        owner.commit()
        check("continuation_row", cont == (cha, utc(64)), cont)
        # 순수 로직의 병합 대상 규칙: C는 새 사건이고 B는 병합됨 → B를 대상으로 하는 병합 없음
        check("merged_once", owner.execute("""SELECT count(*) FROM run_incident_events GROUP BY incident_id
            HAVING count(*) > 1""").fetchall() == [])
        owner.commit()
        last = owner.execute("""SELECT max(cursor_to) FROM run_incident_runs WHERE source_id = %s AND criteria_version = %s""",
                             (fx.source, CRITERIA_VERSION)).fetchone()[0]
        owner.commit()
        check("cursor_advanced", last == previous, (last, previous))

        # 상태 읽기 닫힘(계약 6.1): 창에 이어짐 사건만 있어도 앞 구간을, 병합 대상만 있어도 병합된 사건을 함께 읽는다
        states, _ = incidents_store._state(job, source, CRITERIA_VERSION, {"channel_id:CHA": (utc(64), utc(64))})
        job.commit()
        check("closure_continuation", {s.incident_id for s in states} == {str(incident_of(owner, tail)), str(cha)},
              states)
        states, _ = incidents_store._state(job, source, CRITERIA_VERSION, {"response_code:E901": (utc(16), utc(16))})
        job.commit()
        by_id = {s.incident_id: s for s in states}
        check("closure_merge", set(by_id) == {str(b), str(c)} and by_id[str(b)].merged_into == str(c)
              and by_id[str(b)].last_member_at == utc(20), states)

        # 다른 원본은 그 원본의 커서·사건만 쓴다(side는 clock unknown → 시간대 없는 원문에 Asia/Seoul 가정, 대체 없음)
        side_out = run_once(job, str(side.source))
        check("side_source", (side_out.status, side_out.events, side_out.opened_incidents, side_out.fallback_members) ==
              ("completed", 1, 1, 0), side_out)
        side_member = owner.execute("""SELECT m.occurred_at, m.occurred_basis, i.time_basis FROM run_incident_members m
            JOIN run_incidents i ON i.id = m.incident_id WHERE i.source_id = %s""", (side.source,)).fetchall()
        owner.commit()
        check("side_assumed_seoul", side_member == [(utc(0), "process_time_raw", "process_time_raw")], side_member)

        # 검증된 원본 시간대가 서울이 아니면 그 시간대를 쓴다(가정 시간대로 덮지 않음)
        utc_source = Fixture(owner, {"status": "verified", "timezone": "UTC"})
        utc_decision, _ = utc_source.decision("TX-U", "CHU", kst(0))
        utc_out = run_once(job, str(utc_source.source))
        utc_member = owner.execute("SELECT occurred_at, occurred_basis FROM run_incident_members WHERE decision_id = %s",
                                   (utc_decision,)).fetchone()
        owner.commit()
        check("verified_zone_kept", (utc_out.status, utc_out.fallback_members) == ("completed", 0)
              and tuple(utc_member) == (utc(0) + timedelta(hours=9), "process_time_raw"), (utc_out, utc_member))

        # 시각 범위 끝: 9999-12-31T23:58Z(유효, 창 +gap이 범위를 넘음)와 0001-01-01T00:00+09:00(UTC 변환 넘침 → decided_at).
        # 판정이 섞여도 실행이 끝나고 커서가 넘어간다. 다음 판정은 범위 끝 사건을 상태로 다시 읽는다(세션 시간대 UTC 고정)
        top, _ = fx.decision("TX-1", "CHA", "9999-12-31T23:58:00+00:00")
        bottom, _ = fx.decision("TX-14", "CHD", "0001-01-01T00:00:00+09:00")
        edge = run_once(job, source)
        check("edge_completed", (edge.status, edge.events, edge.cursor_from) == ("completed", 2, previous), edge)
        basis = dict(owner.execute("SELECT decision_id::text, occurred_basis FROM run_incident_members").fetchall())
        owner.commit()
        check("edge_basis", (basis[top], basis[bottom]) == ("process_time_raw", "decided_at"), basis)
        check("edge_cursor_advanced", run_once(job, source).status == "idle")
        later, _ = fx.decision("TX-1", "CHA", "9999-12-31T23:59:30+00:00")
        near = run_once(job, source)
        check("edge_state_reread", (near.status, near.added_members, near.opened_incidents) == ("completed", 1, 0)
              and incident_of(owner, later) == incident_of(owner, top), near)

        # 긴 키 값: 압축이 잘 안 되는 6002자 채널은 group_key를 SHA-256으로 바꿔 UNIQUE btree 한도에 걸리지 않는다
        long_channel = "CH" + secrets.token_hex(3000)
        long_decision, _ = fx.decision("TL-1", long_channel, kst(0))
        long_run = run_once(job, source)
        stored_key = owner.execute("""SELECT i.group_key FROM run_incident_members m JOIN run_incidents i
            ON i.id = m.incident_id WHERE m.decision_id = %s""", (long_decision,)).fetchone()
        owner.commit()
        expected_key = "channel_id:sha256:" + hashlib.sha256(long_channel.encode("utf-8")).hexdigest()
        check("long_key_completed", (long_run.status, long_run.events, long_run.opened_incidents) ==
              ("completed", 1, 1) and stored_key == (expected_key,), (long_run, stored_key))
        check("long_key_cursor_advanced", run_once(job, source).status == "idle")

        # 권한: 작업 역할은 사건 4표에 UPDATE·DELETE·TRUNCATE 권한이 없다(불변 트리거와 별개)
        denied = job.execute("""SELECT t, p FROM unnest(ARRAY['run_incident_runs','run_incidents','run_incident_members',
            'run_incident_events']) t, unnest(ARRAY['UPDATE','DELETE','TRUNCATE']) p WHERE has_table_privilege(t, p)""").fetchall()
        job.commit()
        check("no_change_grants", denied == [], denied)
    print(f"integration_incidents: {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
