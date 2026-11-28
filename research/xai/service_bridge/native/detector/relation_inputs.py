"""관계 입력 읽기(relation-rules.md 3.2): 판정 PROCESS와 같은 원본·같은 거래 ID 문자열의 TRANSACTION·PROCESS·MESSAGE 행.

- 시도 시작 때(운영 상태 고정과 같은 트랜잭션) 한 번 읽고 그 시도 안에서는 다시 읽지 않는다. 읽은 행은
  evidence `relationInputs`에 고정한다. 첫 판정 manifest(`process-input-v1`)는 바꾸지 않는다.
- 행 선택은 그래프 층 A(process-graph.md "입력 행", 백엔드 `analysisReadModel.ts` `loadFlowRows`)와 같다: 실행마다
  최신 리비전 1건, 판정 PROCESS 실행은 판정에 쓴 관측(focus) 그대로, 상한 PROCESS 200·MESSAGE 500, 같은 정렬.
- 거래 대표 행은 relation-rules.md 7.2(= 그래프 "거래 노드 대표 행", `pickTransactionRow`). FK 행으로 대신하지 않는다.
- 판정 관측의 거래 ID가 없음(`graph_value_absent`)이면 다른 행을 읽지 않는다(같은 문자열의 무관한 행을 모으지 않음).
- PROCESS 행의 시작·종료 시각 원래 문자열(`startTimeRaw`·`endTimeRaw`)은 required_step 붙이는 판정(가장 늦게 끝난
  PROCESS, relation-rules.md 3.1)에만 쓴다. evidence에는 관측 참조만 남는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any, Mapping

from detector.relation_graph import build_instance_graph, graph_value_absent, pick_transaction_row

PROCESS_LIMIT = 200
# 거래 행 "도착 확정" 최소 지연(초). 재판정 탐색(relation-rules.md 6.1, 10절 7단계)의 30초와 같은 값이고 7단계도
# 이 상수를 쓴다. CSV 재생은 거래 행을 PROCESS보다 먼저 커밋하므로(collector input_store 정렬 T·P·M·B) 거래 행이
# 있다는 것만으로 "그 거래의 PROCESS가 다 기록됨"(3.1 구조 전제)을 믿지 않는다.
TRANSACTION_SETTLE_SECONDS = 30
MESSAGE_LIMIT = 500

_PROCESSES = """WITH candidates AS (
      SELECT p.observation_id FROM data_processes p WHERE %(tx)s::text IS NOT NULL AND p.transaction_id=%(tx)s
      UNION SELECT %(focus)s::uuid
    ), latest AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) p.observation_id,o.execution_id,o.revision_no,p.transaction_id,
        p.transaction_observation_id,p.process_id,p.depend_process_id,p.status,p.response_code,p.total_count,
        p.retry_count,p.channel_id,p.adapter_type,p.process_hub_id,p.start_time_raw,p.end_time_raw,
        (p.observation_id=%(focus)s::uuid) AS focus
      FROM candidates c JOIN data_processes p ON p.observation_id=c.observation_id
      JOIN data_observations o ON o.id=p.observation_id AND o.source_id=%(source)s::uuid AND o.layer='P'
      ORDER BY coalesce(o.execution_id,o.id),(p.observation_id=%(focus)s::uuid) DESC,o.revision_no DESC NULLS LAST,
        o.persisted_at DESC,o.id
    )
    SELECT observation_id::text,execution_id::text,revision_no::text,transaction_id,transaction_observation_id::text,
      process_id,depend_process_id,status,response_code,total_count::text,retry_count::text,channel_id,adapter_type,
      process_hub_id,focus,start_time_raw,end_time_raw,count(*) OVER ()
    FROM latest ORDER BY start_time_raw NULLS LAST,process_id NULLS LAST,observation_id LIMIT %(limit)s"""
_PROCESS_KEYS = ("observationId", "executionId", "revisionNo", "transactionId", "transactionObservationId",
                 "processId", "dependsOn", "status", "responseCode", "totalCount", "retryCount", "channelId",
                 "adapterType", "processHubId", "focus", "startTimeRaw", "endTimeRaw")

_MESSAGES = """WITH latest AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) m.observation_id,o.execution_id,o.revision_no,
        m.process_observation_id,m.transaction_id,m.process_id,m.message_id,m.direction,m.status,m.data_size,
        m.message_index
      FROM data_messages m JOIN data_observations o ON o.id=m.observation_id AND o.source_id=%(source)s::uuid AND o.layer='M'
      WHERE m.transaction_id=%(tx)s
      ORDER BY coalesce(o.execution_id,o.id),o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT observation_id::text,execution_id::text,revision_no::text,process_observation_id::text,transaction_id,
      process_id,message_id,direction,status,data_size::text,message_index::text,count(*) OVER ()
    FROM latest ORDER BY process_id NULLS LAST,message_index NULLS LAST,message_id NULLS LAST,observation_id
    LIMIT %(limit)s"""
_MESSAGE_KEYS = ("observationId", "executionId", "revisionNo", "processObservationId", "transactionId", "processId",
                 "messageId", "direction", "status", "dataSize", "messageIndex")

# 재시도 번호 큰 순(번호 없음 먼저) 앞 2건 + 실행 수·실행 id 없음 수·재시도 번호 없음 수(재시도가 많아도 2행).
_TRANSACTIONS = """WITH r AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) t.observation_id,o.execution_id,o.revision_no,t.retry_count,
        t.transaction_id,t.interface_id,t.start_channel_id,t.end_channel_id,t.status,t.response_code,o.persisted_at
      FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id AND o.source_id=%(source)s::uuid
        AND o.layer='T'
      WHERE t.transaction_id=%(tx)s
      ORDER BY coalesce(o.execution_id,o.id),o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT observation_id::text,revision_no::text,retry_count::text,transaction_id,interface_id,start_channel_id,
      end_channel_id,status,response_code,
      max(persisted_at) OVER () <= %(read_at)s::timestamptz - make_interval(secs => %(settle)s),
      count(*) OVER (),count(*) FILTER (WHERE execution_id IS NULL) OVER (),
      count(*) FILTER (WHERE retry_count IS NULL) OVER (),
      count(*) FILTER (WHERE interface_id IS NULL OR btrim(interface_id,%(trim)s) IN ('',%(null_mark)s)) OVER (),
      min(interface_id COLLATE "C") OVER (),max(interface_id COLLATE "C") OVER ()
    FROM r ORDER BY retry_count DESC NULLS FIRST,observation_id LIMIT 2"""
_TRANSACTION_KEYS = ("observationId", "revisionNo", "retryCount", "transactionId", "interfaceId", "channelId",
                     "endChannelId", "status", "responseCode")
# 끝 세 열은 프로토콜 인터페이스 정하기(protocol-rules.md 3.1a, 대표 행이 모호할 때): interface_id 없음 실행 수
# (relation_graph.graph_value_absent와 같은 기준 — NULL, 또는 ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 ""·"\N")와
# 바이트 순(COLLATE "C") 최소·최대. 없음이 0건이고 최소 = 최대면 모든 실행의 값이 같다.
_ABSENT_TRIM, _ABSENT_MARK = " \t\r\n", "\\N"
# 행 값 뒤에 오는 창 집계 열(위 SELECT 순서와 같다). 열 수가 다르면 저장소·코드 불일치로 시도 실패.
_TRANSACTION_SUMMARY_KEYS = ("confirmed", "executions", "missingExecution", "missingRetry", "absentInterface",
                             "minInterface", "maxInterface")


@dataclass(frozen=True)
class RelationInputs:
    """시도 시작 때 읽어 고정한 관계 입력. 행 값은 원래 문자열(숫자 열은 SQL text)이다."""
    read_at: datetime
    source_id: str
    focus_observation_id: str
    focus_transaction_id: str | None
    transaction_executions: int
    transaction: Mapping[str, Any] | None
    transaction_ambiguous: bool
    # 거래 행 도착 확정: 실행 1건 이상이고 모든 실행(최신 리비전)의 persisted_at ≤ readAt − TRANSACTION_SETTLE_SECONDS.
    # 둘 다 DB 시계(persisted_at은 수집 clock_timestamp(), readAt은 이 시도의 clock_timestamp()).
    transaction_confirmed: bool
    processes: tuple[Mapping[str, Any], ...]
    messages: tuple[Mapping[str, Any], ...]
    processes_truncated: bool
    messages_truncated: bool
    # 거래 행 모든 실행(최신 리비전)의 interface_id가 없음 없이 모두 같으면 그 값, 아니면 None(protocol-rules.md 3.1a).
    common_interface_id: str | None = None

    @property
    def transaction_keyed(self) -> bool:
        """판정 관측의 거래 ID가 있음(없으면 그래프 `unkeyed`, 관계 판정은 `transaction_id_missing`)."""
        return not graph_value_absent(self.focus_transaction_id)

    @cached_property
    def graph(self) -> dict[str, Any]:
        """그래프 층 A. 거래 노드는 대표 행(모호면 대표 값 없이 거래 ID만, 재시도 목록 없음)."""
        transaction = None
        if self.transaction is not None:
            transaction = {**self.transaction, "processTimeMs": None, "processCount": None, "retries": None}
        elif self.transaction_ambiguous:
            transaction = {"observationId": None, "revisionNo": None, "transactionId": self.focus_transaction_id,
                           "interfaceId": None, "channelId": None, "status": None, "responseCode": None,
                           "processTimeMs": None, "processCount": None, "retries": None, "ambiguous": True}
        return build_instance_graph(source_id=self.source_id, transaction=transaction, processes=self.processes,
                                    messages=self.messages, processes_truncated=self.processes_truncated,
                                    messages_truncated=self.messages_truncated, generated_at=self.read_at.isoformat())

    def evidence(self) -> dict[str, Any]:
        """evidence `relationInputs`. 원천 값은 복사하지 않고 관측 참조만 남긴다(detection-decision.md 11.5)."""
        def ref(row: Mapping[str, Any]) -> dict[str, Any]:
            # revisionNo는 bigint 열의 SQL ::text(ASCII 숫자)라 int()의 유니코드 숫자 허용과 무관하다.
            return {"observationId": row["observationId"],
                    "revisionNo": None if row["revisionNo"] is None else int(row["revisionNo"])}

        transaction = None
        if self.transaction_executions:
            picked = self.transaction
            transaction = {"executions": self.transaction_executions, "ambiguous": self.transaction_ambiguous,
                           "arrivalConfirmed": self.transaction_confirmed,
                           **(ref(picked) if picked else {"observationId": None, "revisionNo": None})}
        return {"readAt": self.read_at.isoformat(), "transaction": transaction,
                "processes": [ref(row) for row in self.processes], "messages": [ref(row) for row in self.messages],
                "truncated": self.processes_truncated, "messagesTruncated": self.messages_truncated}


def read_relation_inputs(conn: Any, source_id: Any, observation_id: Any) -> RelationInputs:
    """같은 연결의 열린 트랜잭션에서 읽는다(호출자가 운영 상태 고정과 함께 커밋). 시각은 DB 시계."""
    with conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp(),transaction_id FROM data_processes WHERE observation_id=%s",
                    (observation_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("relation_focus_unavailable")
        read_at, focus_tx = row
        keyed = not graph_value_absent(focus_tx)
        params = {"tx": focus_tx if keyed else None, "focus": observation_id, "source": source_id}
        cur.execute(_PROCESSES, {**params, "limit": PROCESS_LIMIT + 1})
        process_rows = cur.fetchall()
        message_rows, top = [], []
        if keyed:
            cur.execute(_MESSAGES, {**params, "limit": MESSAGE_LIMIT + 1})
            message_rows = cur.fetchall()
            cur.execute(_TRANSACTIONS, {**params, "read_at": read_at, "settle": TRANSACTION_SETTLE_SECONDS,
                                        "trim": _ABSENT_TRIM, "null_mark": _ABSENT_MARK})
            top = cur.fetchall()
    processes = tuple(dict(zip(_PROCESS_KEYS, values[:len(_PROCESS_KEYS)])) for values in process_rows)
    messages = tuple(dict(zip(_MESSAGE_KEYS, values[:len(_MESSAGE_KEYS)])) for values in message_rows)
    if len(processes) <= PROCESS_LIMIT and not any(row["focus"] is True for row in processes):
        # 판정 PROCESS 관측은 후보에 항상 들어간다(상한에 걸린 경우만 잘릴 수 있다). 없으면 원본·층이 맞지 않는 저장소 이상이다.
        raise ValueError("relation_focus_unavailable")
    width = len(_TRANSACTION_KEYS) + len(_TRANSACTION_SUMMARY_KEYS)
    if any(len(values) != width for values in top):
        raise ValueError("relation_transaction_columns_mismatch")
    summary = dict(zip(_TRANSACTION_SUMMARY_KEYS, top[0][len(_TRANSACTION_KEYS):])) if top else {
        "confirmed": False, "executions": 0, "missingExecution": 0, "missingRetry": 0, "absentInterface": 0,
        "minInterface": None, "maxInterface": None}
    confirmed, executions = summary["confirmed"], summary["executions"]
    missing_execution, missing_retry = summary["missingExecution"], summary["missingRetry"]
    absent_interface, low, high = summary["absentInterface"], summary["minInterface"], summary["maxInterface"]
    candidates = [dict(zip(_TRANSACTION_KEYS, values[:len(_TRANSACTION_KEYS)])) for values in top]
    picked, ambiguous = pick_transaction_row(executions, missing_execution, missing_retry, candidates)
    return RelationInputs(read_at=read_at, source_id=str(source_id), focus_observation_id=str(observation_id),
                          focus_transaction_id=focus_tx, transaction_executions=executions, transaction=picked,
                          transaction_ambiguous=ambiguous, transaction_confirmed=executions >= 1 and confirmed is True,
                          processes=processes[:PROCESS_LIMIT],
                          messages=messages[:MESSAGE_LIMIT], processes_truncated=len(processes) > PROCESS_LIMIT,
                          messages_truncated=len(messages) > MESSAGE_LIMIT,
                          common_interface_id=low if executions >= 1 and absent_interface == 0 and low == high
                          else None)
