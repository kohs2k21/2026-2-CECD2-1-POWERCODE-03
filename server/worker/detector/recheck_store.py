"""관계 규칙 재판정 탐색 주기 작업의 DB 쪽(contracts/relation-rules.md 6.1~6.3, migration 024). 거르기 규칙은 recheck.py(순수).

한 번 실행 = 원본 1개·트랜잭션 1개: 잠금(81742) → 커서 → (a) 새 TRANSACTION 관측 + 겹침 → (b) 새 final 판정 이벤트 →
거래마다 도착 확정·대상 판정 → 사전 검사(현재 룰 세트) → 재판정 작업(jobs.enqueue_recheck, 잠금 81740) → 실행 행.

- 시각은 모두 DB 시계(`clock_timestamp()`): 거래 행 `persisted_at`(수집 `clock_timestamp()`)과 같은 시계로 비교한다.
- (a)·(b) 모두 거래 단위로 "그 거래의 TRANSACTION 실행(실행마다 최신 리비전) 중 가장 늦은 `persisted_at` ≤ 지금 − 30초"일
  때만 넘긴다(재판정 시도가 항상 도착 확정이 되도록). 아직이면 넘기지 않는다 — 그 늦은 행이 (a)의 새 계기가 되어 30초 뒤
  다시 본다. 남는 틈(작업 생성부터 시도 시작 사이에 새 실행이 커밋됨)은 계약의 한계로 둔다.
- 1회 보장은 `run_input_manifests UNIQUE(source_id, content_hash)`: (a)·(b)·겹침이 같은 관측을 여러 번 넘겨도 재판정
  manifest는 1개다. 재판정 manifest가 이미 있으면 사전 검사 전에 건너뛴다.
- 판정·판정 이벤트 표에는 쓰지 않는다. 같은 트랜잭션에서 81741을 잡지 않는다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Mapping
import uuid

from psycopg.pq import TransactionStatus

from detector.jobs import enqueue_recheck, recheck_manifest_exists, recheck_manifest_hash
from detector.operational_state import pin_operational_state
from detector.recheck import (OVERLAP_SECONDS, RECHECK_LOCK_CLASS, RECHECK_LOCK_KEY, SETTLE_SECONDS, WINDOW_SECONDS,
                              Precheck, awaiting_transaction, ordered_unique, precheck, within_window)
from detector.relation_graph import graph_value_absent
from detector.relation_inputs import read_relation_inputs
from detector.relation_plan import needs_relation_inputs, plan_relations

DEFAULT_BATCH_LIMIT = 500
NIL = uuid.UUID(int=0)

_LAST_CURSOR = """SELECT transaction_cursor_at, transaction_cursor_id, event_cursor FROM run_recheck_runs
WHERE source_id = %s ORDER BY seq DESC LIMIT 1"""

# (a) 커서 다음의 TRANSACTION 관측(도착 확정 지연이 지난 것만). 인덱스 data_observations(source_id, persisted_at).
_NEW_TRANSACTIONS = """SELECT o.persisted_at, o.id, t.transaction_id
FROM data_observations o JOIN data_transactions t ON t.observation_id = o.id
WHERE o.source_id = %(source)s AND o.layer = 'T' AND (o.persisted_at, o.id) > (%(at)s::timestamptz, %(id)s::uuid)
  AND o.persisted_at <= %(horizon)s
ORDER BY o.persisted_at, o.id LIMIT %(limit)s"""

# (a) 겹침: 커서 앞 60초를 다시 읽는다(커서를 지난 뒤 커밋된, persisted_at이 더 이른 행). 커서는 움직이지 않는다.
# 커서에 가까운(늦은) 행부터 읽는다 — 늦게 커밋된 행은 대개 커서 바로 앞이라 상한이 걸리면 먼 쪽이 빠진다(계약 10절 한계).
_OVERLAP_TRANSACTIONS = """SELECT o.persisted_at, o.id, t.transaction_id
FROM data_observations o JOIN data_transactions t ON t.observation_id = o.id
WHERE o.source_id = %(source)s AND o.layer = 'T'
  AND o.persisted_at >= %(at)s::timestamptz - make_interval(secs => %(overlap)s)
  AND (o.persisted_at, o.id) <= (%(at)s::timestamptz, %(id)s::uuid)
ORDER BY o.persisted_at DESC, o.id DESC LIMIT %(limit)s"""

# (b) 커서 다음의 final 판정 이벤트(이 원본). id 순서 = 커밋 순서(006 트리거가 81736 잠금 아래에서 번호를 붙임).
_EVENTS = """WITH batch AS (
  SELECT e.id AS event_id, e.decision_id
  FROM run_decision_events e
  JOIN run_process_decisions d ON d.id = e.decision_id
  JOIN data_executions x ON x.id = d.execution_id
  WHERE e.id > %(after)s AND e.event_kind = 'final' AND x.source_id = %(source)s
  ORDER BY e.id LIMIT %(limit)s
)
SELECT b.event_id, p.transaction_id,
  d.evidence->>'schemaVersion' = '4' AND NOT (d.evidence ? 'relationRecheck') AS first_v4,
  d.evidence->'relationNotes'
FROM batch b
JOIN run_process_decisions d ON d.id = b.decision_id
JOIN data_processes p ON p.observation_id = d.process_observation_id
ORDER BY b.event_id"""

# 거래 행: 같은 원본·같은 거래 ID 문자열, 실행마다 최신 리비전(relation_inputs._TRANSACTIONS와 같은 행 선택). persisted_at 순.
_TRANSACTION_ROWS = """SELECT observation_id::text, revision_no, persisted_at FROM (
  SELECT DISTINCT ON (coalesce(o.execution_id, o.id)) o.id AS observation_id, o.revision_no, o.persisted_at
  FROM data_transactions t JOIN data_observations o ON o.id = t.observation_id AND o.source_id = %(source)s
    AND o.layer = 'T'
  WHERE t.transaction_id = %(tx)s
  ORDER BY coalesce(o.execution_id, o.id), o.revision_no DESC NULLS LAST, o.persisted_at DESC, o.id
) r ORDER BY persisted_at, observation_id"""

# 같은 거래의 PROCESS(실행마다 최신 리비전)와 관측별 최신 판정(018 인덱스, decided_at DESC, id DESC). v04 첫 판정만.
_CANDIDATES = """WITH latest AS (
  SELECT DISTINCT ON (coalesce(o.execution_id, o.id)) o.id, o.revision_no, o.allowed_values
  FROM data_processes p JOIN data_observations o ON o.id = p.observation_id AND o.source_id = %(source)s
    AND o.layer = 'P'
  WHERE p.transaction_id = %(tx)s
  ORDER BY coalesce(o.execution_id, o.id), o.revision_no DESC NULLS LAST, o.persisted_at DESC, o.id
)
SELECT d.id, l.id, l.revision_no, l.allowed_values, d.decided_at, d.evidence->'relationNotes',
  d.evidence->'methodResults', d.evidence->'composite'
FROM latest l
JOIN LATERAL (SELECT id, decided_at, evidence FROM run_process_decisions
              WHERE process_observation_id = l.id ORDER BY decided_at DESC, id DESC LIMIT 1) d ON true
WHERE d.evidence->>'schemaVersion' = '4' AND NOT (d.evidence ? 'relationRecheck')
ORDER BY d.decided_at, d.id"""


@dataclass
class RecheckOutcome:
    """run_once 결과. status = skipped(잠금 못 잡음) | idle(새 행 없음, 실행 행 없음) | completed."""
    status: str
    run_id: str | None = None
    transactions: int = 0  # 이번 회차에 본 거래 수((a)·겹침·(b) 합집합)
    candidates: int = 0  # 거래 행 도착을 기다리던 판정 수
    enqueued: int = 0
    jobs: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # 사유별 수
    transaction_cursor: tuple[str, str] | None = None
    event_cursor: int | None = None


_FAILURE_CODE = re.compile(r"[a-z0-9_:]+")


def precheck_failure_code(exc: BaseException) -> str:
    """보고 사유 코드. ValueError 문구가 우리 사유 코드 모양(소문자·숫자·`_`·`:`)일 때만 그 문자열, 그 밖은 예외 이름만
    (저장값·데이터 값이 표준 출력 JSON 보고에 섞이지 않게)."""
    text = str(exc)
    if isinstance(exc, ValueError) and _FAILURE_CODE.fullmatch(text):
        return text[:60]
    return type(exc).__name__[:60]


def _cursor(conn, source: str, horizon, window_seconds: float) -> tuple[bool, object, uuid.UUID, int]:
    """(첫 실행 여부, 거래 관측 커서 시각·id, 판정 이벤트 커서). 첫 실행: 거래 관측은 기간만큼 앞에서, 판정 이벤트는 지금 끝에서
    시작한다(그 앞의 판정은 (a)가 기간 안 거래 행으로 찾는다)."""
    row = conn.execute(_LAST_CURSOR, (source,)).fetchone()
    if row is not None:
        return False, row[0], row[1], row[2]
    start = conn.execute("SELECT %s::timestamptz - make_interval(secs => %s)", (horizon, float(window_seconds))).fetchone()[0]
    last_event = conn.execute("SELECT coalesce(max(id), 0) FROM run_decision_events").fetchone()[0]
    return True, start, NIL, last_event


class _Search:
    """한 회차의 거래 처리. 운영 상태는 대상 판정이 처음 나올 때 한 번 고정한다(대상이 없으면 읽지 않음)."""

    def __init__(self, conn, source: str, horizon, window_seconds: float):
        self.conn, self.source, self.horizon, self.window = conn, source, horizon, window_seconds
        self.state = None
        self.outcome = RecheckOutcome("completed")
        self.skipped: Counter = Counter()

    def pinned(self):
        if self.state is None:
            # 사전 검사 ①~⑤는 원 판정에 고정된 리비전이 아니라 지금의 룰 세트로 계산한다(6.2). 실패하면 회차 전체가 되돌아간다.
            self.state = pin_operational_state(self.conn)
        return self.state

    def transaction(self, tx: str) -> None:
        rows = self.conn.execute(_TRANSACTION_ROWS, {"source": self.source, "tx": tx}).fetchall()
        if not rows:
            self.skipped["transaction_absent"] += 1  # (b) 거래 행 전 판정: 거래 행이 오면 (a)가 본다
            return
        if rows[-1][2] > self.horizon:
            self.skipped["transaction_settling"] += 1  # 새 실행·리비전이 30초 안: 그 행이 (a)의 새 계기가 된다
            return
        first_at = rows[0][2]
        trigger = [{"observation_id": row[0], "revision_no": row[1]} for row in rows]
        for decision, observation, revision, raw, decided_at, notes, methods, composite in self.conn.execute(
                _CANDIDATES, {"source": self.source, "tx": tx}).fetchall():
            if awaiting_transaction(notes):
                self.outcome.candidates += 1
                self.candidate(decision, observation, revision, raw, decided_at, methods, composite, first_at,
                               trigger)

    def precheck(self, state, observation, raw, methods, composite) -> Precheck | None:
        """6.2 사전 검사 1건. None이면 넘기지 않음(사유는 skipped에 이미 셈)."""
        if not isinstance(raw, Mapping):
            raise TypeError("allowed_values_not_object")
        if not needs_relation_inputs(state, raw):
            self.skipped["open_result_absent"] += 1  # 지금 룰 세트에 관계 입력이 필요한 규칙이 없다
            return None
        inputs = read_relation_inputs(self.conn, self.source, observation)
        if not inputs.transaction_confirmed:
            self.skipped["transaction_settling"] += 1
            return None
        return precheck(state, plan_relations(state, inputs, raw), methods, composite)

    def candidate(self, decision, observation, revision, raw, decided_at, methods, composite, first_at, trigger) -> None:
        if not within_window(first_at, decided_at, self.window):
            self.skipped["outside_window"] += 1
            return
        if type(revision) is not int:
            self.skipped["detect_input_unavailable"] += 1
            return
        if recheck_manifest_exists(self.conn, self.source, recheck_manifest_hash(self.source, observation, revision)):
            self.skipped["already_rechecked"] += 1
            return
        # 운영 상태 고정은 try 밖: 실패하면 회차 전체가 되돌아간다(커서 그대로, 다음 회차가 다시 읽음).
        state = self.pinned()
        try:
            checked = self.precheck(state, observation, raw, methods, composite)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            # 판정 하나의 데이터 모양 이상·저장소·그래프 불변 조건 위반(relation_focus_unavailable 등): 그 판정만 건너뛰고
            # 보고에 남긴다 — 한 판정 때문에 원본의 커서가 멈추지 않게. DB 오류(psycopg.Error)는 회차 전체를 되돌린다.
            self.skipped["precheck_failed:" + precheck_failure_code(exc)] += 1
            return
        if checked is None:
            return
        if not checked.target:
            self.skipped[checked.reason] += 1
            return
        payload = enqueue_recheck(self.conn, observation, decision, trigger)
        if payload is None:
            self.skipped["already_rechecked"] += 1
            return
        self.outcome.enqueued += 1
        self.outcome.jobs.append(payload["jobId"])


def run_once(conn, source_id, *, batch_limit: int = DEFAULT_BATCH_LIMIT,
             window_seconds: float = WINDOW_SECONDS) -> RecheckOutcome:
    """원본 1개를 한 트랜잭션으로 묶는다. conn은 열린 트랜잭션이 없어야 한다(이 함수가 BEGIN·COMMIT).

    - 잠금(81742, 0)을 못 잡으면 아무것도 읽지 않고 skipped. 새 거래 관측·판정 이벤트가 없으면 실행 행 없이 idle
      (첫 실행은 커서를 고정하려고 실행 행을 남긴다)
    - 예외면 트랜잭션 전체가 되돌아가고 커서는 그대로다(다음 회차가 같은 범위를 다시 읽음)
    """
    if type(batch_limit) is not int or batch_limit < 1:
        raise ValueError("recheck_batch_limit_invalid")
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("recheck_run_requires_idle_connection")
    source = str(uuid.UUID(str(source_id)))
    with conn.transaction():
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s, %s)",
                            (RECHECK_LOCK_CLASS, RECHECK_LOCK_KEY)).fetchone()[0]:
            return RecheckOutcome("skipped")
        conn.execute("SET LOCAL TimeZone = 'UTC'")
        started_at, horizon = conn.execute("SELECT c.at, c.at - make_interval(secs => %s) FROM clock_timestamp() AS c(at)",
                                           (float(SETTLE_SECONDS),)).fetchone()
        first, tx_at, tx_id, event_after = _cursor(conn, source, horizon, window_seconds)
        params = {"source": source, "at": tx_at, "id": tx_id, "horizon": horizon, "limit": batch_limit,
                  "overlap": float(OVERLAP_SECONDS)}
        new_rows = conn.execute(_NEW_TRANSACTIONS, params).fetchall()
        # 겹침은 늦은 행부터 상한까지 읽고, 처리는 오래 기다린 거래 먼저(persisted_at 오름차순)로 되돌린다.
        overlap_rows = conn.execute(_OVERLAP_TRANSACTIONS, params).fetchall()[::-1] if not first else []
        events = conn.execute(_EVENTS, {"after": event_after, "source": source, "limit": batch_limit}).fetchall()
        next_tx = (new_rows[-1][0], new_rows[-1][1]) if new_rows else (tx_at, tx_id)
        next_event = events[-1][0] if events else event_after
        # (b): 거래 행 도착을 기다리는 v04 첫 판정의 거래만. 겹침·(a)와 같은 거래는 한 번만 본다.
        waiting = [row[1] for row in events if row[2] and awaiting_transaction(row[3])]
        transactions = [tx for tx in ordered_unique([*(row[2] for row in overlap_rows), *(row[2] for row in new_rows),
                                                     *waiting]) if not graph_value_absent(tx)]
        search = _Search(conn, source, horizon, window_seconds)
        for tx in transactions:
            search.transaction(tx)
        outcome = search.outcome
        outcome.transactions, outcome.skipped = len(transactions), dict(sorted(search.skipped.items()))
        outcome.transaction_cursor, outcome.event_cursor = (next_tx[0].isoformat(), str(next_tx[1])), next_event
        if not (first or new_rows or events or outcome.enqueued):
            outcome.status = "idle"
            return outcome
        outcome.run_id = str(uuid.uuid4())
        conn.execute("""INSERT INTO run_recheck_runs(id, source_id, transaction_cursor_at, transaction_cursor_id,
            event_cursor, transactions, candidates, enqueued, started_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (outcome.run_id, source, next_tx[0], next_tx[1], next_event, outcome.transactions,
                      outcome.candidates, outcome.enqueued, started_at))
        return outcome
