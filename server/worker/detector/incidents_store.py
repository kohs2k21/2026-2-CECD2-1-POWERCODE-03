"""층 C 사건 묶음 주기 작업의 DB 쪽(contracts/incidents.md 2·5·6절, migration 019). 묶음 규칙은 incidents.py(순수)에 있다.

한 번 실행 = 트랜잭션 1개: 잠금 → 커서 → 새 final 판정 → 구성원 여부 → 열린 사건 상태 → group_decisions → INSERT → 실행 행.
판정·판정 이벤트 표에는 쓰지 않는다. 같은 트랜잭션에서 81736·81739·81740 잠금을 잡지 않는다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import uuid

from psycopg.pq import TransactionStatus

from detector.incidents import (DEFAULT_CRITERIA, INCIDENT_LOCK_CLASS, INCIDENT_LOCK_KEY, Criteria,
                                DecisionCandidate, OpenIncident, check_result_scope, graph_value_absent,
                                group_decisions, resolve_transaction_response_code, state_windows)

DEFAULT_BATCH_LIMIT = 1000

# 커서 다음의 final 이벤트를 원본으로 걸러 id 순서로 읽고, 실행마다 지금 최신 판정인지 함께 본다(계약 2.1·8절 정렬).
# 등급은 v4 근거에서만 읽는다(v3는 NULL). 근거 jsonb 전체는 가져오지 않는다.
_BATCH_SQL = """
WITH batch AS (
  SELECT e.id AS event_id, d.id AS decision_id, d.execution_id
  FROM run_decision_events e
  JOIN run_process_decisions d ON d.id = e.decision_id
  JOIN data_executions x ON x.id = d.execution_id
  WHERE e.id > %(after)s AND e.event_kind = 'final' AND x.source_id = %(source)s
  ORDER BY e.id
  LIMIT %(limit)s
), latest AS (
  SELECT DISTINCT ON (d.execution_id) d.execution_id, d.id AS decision_id
  FROM run_process_decisions d
  JOIN data_observations o ON o.id = d.process_observation_id
  WHERE d.execution_id IN (SELECT execution_id FROM batch)
  ORDER BY d.execution_id, o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC
)
SELECT b.event_id, b.decision_id, b.execution_id, l.decision_id = b.decision_id AS is_latest,
       CASE WHEN d.evidence->>'schemaVersion' = '4' THEN d.evidence->'composite'->>'grade' END AS grade,
       d.decided_at, p.transaction_id, p.channel_id, p.start_time_raw, p.end_time_raw
FROM batch b
JOIN run_process_decisions d ON d.id = b.decision_id
JOIN data_processes p ON p.observation_id = d.process_observation_id
JOIN latest l ON l.execution_id = b.execution_id
ORDER BY b.event_id
"""

# 같은 원본의 TRANSACTION 행을 거래 ID 문자열로 찾는다(P→T FK는 채워지지 않음, 계약 2.3).
_TRANSACTION_SQL = """
SELECT t.transaction_id, o.execution_id, o.revision_no, t.response_code
FROM data_transactions t
JOIN data_observations o ON o.id = t.observation_id
WHERE o.source_id = %(source)s AND o.layer = 'T' AND t.transaction_id = ANY(%(ids)s)
"""

# 계약 6.1: 키별 시간 창 안의 사건에서 시작해 병합 간선(양방향)과 앞 구간 간선(continues_from)을 닫힘까지 따라간다.
# 간선 표는 원본으로 거르지 않는다: 다른 원본·기준 버전으로 닿으면 그대로 읽어 check_result_scope가 거부한다.
_STATE_SQL = """
WITH RECURSIVE seed AS (
  SELECT i.id
  FROM run_incidents i
  JOIN unnest(%(keys)s::text[], %(lows)s::timestamptz[], %(highs)s::timestamptz[]) AS w(group_key, low, high)
    ON i.group_key = w.group_key AND i.opened_at BETWEEN w.low AND w.high
  WHERE i.source_id = %(source)s AND i.criteria_version = %(version)s
), edges AS (
  SELECT incident_id AS a, target_incident_id AS b FROM run_incident_events WHERE kind = 'merged_into'
  UNION ALL
  SELECT target_incident_id, incident_id FROM run_incident_events WHERE kind = 'merged_into'
  UNION ALL
  SELECT id, continues_from FROM run_incidents WHERE continues_from IS NOT NULL
), reach(id) AS (
  SELECT id FROM seed
  UNION
  SELECT e.b FROM reach r JOIN edges e ON e.a = r.id
)
SELECT i.id, i.source_id, i.criteria_version, i.group_key, i.opened_at, i.continues_from,
       (SELECT max(m.occurred_at) FROM run_incident_members m WHERE m.incident_id = i.id) AS last_member_at,
       EXISTS (SELECT 1 FROM run_incidents n WHERE n.continues_from = i.id) AS has_continuation,
       (SELECT v.target_incident_id FROM run_incident_events v
         WHERE v.incident_id = i.id AND v.kind = 'merged_into') AS merged_into
FROM reach r
JOIN run_incidents i ON i.id = r.id
ORDER BY i.opened_at, i.id
"""


@dataclass
class RunOutcome:
    """run_once 결과. status = skipped(잠금 못 잡음) | idle(새 이벤트 없음, 실행 행 없음) | completed."""
    status: str
    run_id: str | None = None
    cursor_from: int | None = None
    cursor_to: int | None = None
    events: int = 0
    added_members: int = 0
    opened_incidents: int = 0
    merged_incidents: int = 0
    fallback_members: int = 0
    excluded: dict[str, int] = field(default_factory=dict)  # not_latest + 순수 로직 Skipped 사유별 수


def _text(value) -> str | None:
    return None if value is None else str(value)


def _source_timezone(conn, source_id) -> str | None:
    """계약 2.2: clock_basis.status = verified일 때만 timezone을 쓴다. 원본 설정은 읽기만 한다.

    - 검증되지 않은 원본(status != verified, clock_basis가 객체가 아님) → None. 순수 로직이 가정 시간대(Asia/Seoul)를 붙인다
    - 검증됐지만 timezone이 문자열이 아님(없음 포함) → "" (시간대 없음 → decided_at 대체, 기존 동작). 검증된 다른
      시간대 주장을 가정으로 덮지 않는다
    """
    row = conn.execute("SELECT clock_basis FROM ops_sources WHERE id = %s", (source_id,)).fetchone()
    if row is None:
        raise ValueError("incident_source_not_found")
    basis = row[0] if isinstance(row[0], dict) else {}
    if basis.get("status") != "verified":
        return None
    zone = basis.get("timezone")
    return zone if isinstance(zone, str) else ""


def _last_cursor(conn, source_id, version: str) -> int | None:
    row = conn.execute("""SELECT cursor_to FROM run_incident_runs WHERE source_id = %s AND criteria_version = %s
        ORDER BY cursor_to DESC LIMIT 1""", (source_id, version)).fetchone()
    return None if row is None else row[0]


def _response_codes(conn, source_id, transaction_ids) -> dict[str, str | None]:
    wanted = sorted({value for value in transaction_ids if not graph_value_absent(value)})
    if not wanted:
        return {}
    rows: dict[str, list] = {value: [] for value in wanted}
    for transaction, execution, revision, code in conn.execute(_TRANSACTION_SQL, {"source": source_id, "ids": wanted}):
        rows[transaction].append((_text(execution), revision, code))
    return {value: resolve_transaction_response_code(found) for value, found in rows.items()}


def _assigned(conn, decision_ids) -> set[str]:
    """이미 어느 사건의 구성원인 판정. PK가 decision_id 하나라 기준 버전과 무관하게 본다(계약 열린 항목 10)."""
    if not decision_ids:
        return set()
    return {str(row[0]) for row in conn.execute(
        "SELECT decision_id FROM run_incident_members WHERE decision_id = ANY(%s::uuid[])", (list(decision_ids),))}


def _state(conn, source_id, version: str, windows) -> tuple[list[OpenIncident], dict[str, tuple[str, str]]]:
    if not windows:
        return [], {}
    keys = list(windows)
    rows = conn.execute(_STATE_SQL, {"keys": keys, "lows": [windows[k][0] for k in keys],
                                     "highs": [windows[k][1] for k in keys], "source": source_id,
                                     "version": version}).fetchall()
    states, scope = [], {}
    for incident, source, row_version, key, opened, continues, last, has_next, merged in rows:
        if last is None:
            # 구성원 없는 사건은 이 작업이 만들지 않는다(구성원이 하나라도 안 들어가면 되돌림, 계약 7절).
            raise ValueError("incident_without_members")
        scope[str(incident)] = (str(source), row_version)
        states.append(OpenIncident(str(incident), key, opened, last, _text(continues), bool(has_next), _text(merged)))
    return states, scope


def _insert(conn, run_id: str, source_id: str, version: str, result, started_at, cursor_from: int,
            cursor_to: int) -> None:
    """019 순서: 사건 → 구성원 → 병합 이벤트 → 실행 행(added_by_run FK는 지연 검사라 COMMIT 때 본다)."""
    for new in result.incidents:
        conn.execute("""INSERT INTO run_incidents(id, source_id, criteria, criteria_version, group_key, time_basis,
            opened_at, continues_from, added_by_run) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)""",
                     (new.incident_id, new.source_id, json.dumps(new.criteria, sort_keys=True), new.criteria_version,
                      new.group_key, new.time_basis, new.opened_at, new.continues_from, run_id))
    if result.members:
        inserted = conn.execute("""INSERT INTO run_incident_members(decision_id, incident_id, occurred_at, occurred_basis,
                added_by_run)
            SELECT m.decision_id, m.incident_id, m.occurred_at, m.occurred_basis, %s
            FROM unnest(%s::uuid[], %s::uuid[], %s::timestamptz[], %s::text[])
              AS m(decision_id, incident_id, occurred_at, occurred_basis)
            ON CONFLICT (decision_id) DO NOTHING RETURNING decision_id""",
                                (run_id, [m.decision_id for m in result.members],
                                 [m.incident_id for m in result.members], [m.occurred_at for m in result.members],
                                 [m.occurred_basis for m in result.members])).fetchall()
        if len(inserted) != len(result.members):
            # 잠금 안에서 구성원 여부를 읽었으므로 정상 경로에 없다. 빈 사건이 남지 않게 전체를 되돌린다(계약 7절).
            raise ValueError("incident_member_conflict")
    for merge in result.merges:
        conn.execute("""INSERT INTO run_incident_events(incident_id, kind, target_incident_id, added_by_run)
            VALUES (%s, 'merged_into', %s, %s)""", (merge.incident_id, merge.target_incident_id, run_id))
    conn.execute("""INSERT INTO run_incident_runs(id, source_id, criteria_version, cursor_from, cursor_to, added_members,
        opened_incidents, merged_incidents, started_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                 (run_id, source_id, version, cursor_from, cursor_to, len(result.members), len(result.incidents),
                  len(result.merges), started_at))


def run_once(conn, source_id, *, criteria: Criteria = DEFAULT_CRITERIA, batch_limit: int = DEFAULT_BATCH_LIMIT,
             after: int | None = None) -> RunOutcome:
    """원본 1개·기준 버전 1개를 한 트랜잭션으로 묶는다. conn은 열린 트랜잭션이 없어야 한다(이 함수가 BEGIN·COMMIT).

    - after: 커서 시작점(배타). 없으면 같은 (source_id, criteria_version) 실행 행의 최대 cursor_to, 그것도 없으면 0
      (과거 판정 전체, 계약 열린 항목 8). 같은 범위 재처리 시험·복구용으로만 준다
    - 잠금(81741, 0)을 못 잡으면 아무것도 읽지 않고 skipped. 새 final 이벤트가 없으면 실행 행 없이 idle
    - 예외면 트랜잭션 전체가 되돌아가고 커서는 그대로다(다음 회차가 같은 범위를 다시 읽음)
    """
    if type(batch_limit) is not int or batch_limit < 1:
        raise ValueError("incident_batch_limit_invalid")
    if after is not None and (type(after) is not int or after < 0):
        raise ValueError("incident_cursor_invalid")
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("incident_run_requires_idle_connection")
    source = str(uuid.UUID(str(source_id)))
    version = criteria.version
    with conn.transaction():
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s, %s)",
                            (INCIDENT_LOCK_CLASS, INCIDENT_LOCK_KEY)).fetchone()[0]:
            return RunOutcome("skipped")
        # 시각을 UTC로 받는다. 세션 시간대가 UTC가 아니면 9999-12-31 근처 값을 Python datetime으로 바꿀 때 범위를 넘는다.
        conn.execute("SET LOCAL TimeZone = 'UTC'")
        started_at = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        timezone_name = _source_timezone(conn, source)
        if after is None:
            after = _last_cursor(conn, source, version) or 0
        rows = conn.execute(_BATCH_SQL, {"after": after, "source": source, "limit": batch_limit}).fetchall()
        if not rows:
            return RunOutcome("idle", cursor_from=after, cursor_to=after)
        cursor_to = max(row[0] for row in rows)
        excluded = Counter()
        latest_rows = []
        for row in rows:
            if row[3]:
                latest_rows.append(row)
            else:
                excluded["not_latest"] += 1
        codes = _response_codes(conn, source, [row[6] for row in latest_rows])
        candidates = [DecisionCandidate(
            decision_id=str(decision), event_id=event, execution_id=str(execution), source_id=source, grade=grade,
            decided_at=decided_at, end_time_raw=end_raw, start_time_raw=start_raw, transaction_id=transaction,
            transaction_response_code=None if graph_value_absent(transaction) else codes.get(transaction),
            channel_id=channel)
            for event, decision, execution, _, grade, decided_at, transaction, channel, start_raw, end_raw
            in latest_rows]
        assigned = _assigned(conn, [c.decision_id for c in candidates])
        windows = state_windows(candidates, assigned, criteria, source_timezone=timezone_name)
        states, scope = _state(conn, source, version, windows)
        result = group_decisions(source, candidates, states, assigned, criteria, source_timezone=timezone_name)
        check_result_scope(result, source, version, scope)
        run_id = str(uuid.uuid4())
        _insert(conn, run_id, source, version, result, started_at, after, cursor_to)
        excluded.update(skip.reason for skip in result.skipped)
        return RunOutcome("completed", run_id, after, cursor_to, len(rows), len(result.members),
                          len(result.incidents), len(result.merges), result.fallback_members, dict(sorted(excluded.items())))
