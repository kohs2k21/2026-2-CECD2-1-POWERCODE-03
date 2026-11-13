/**
 * 거래 내부 실행 그래프(층 A, `graph.layer = "instance"`) — contracts/process-graph.md.
 * - 새 저장·조회 없음: readDecisionFlow가 이미 읽은 TRANSACTION·PROCESS·MESSAGE 행(실행마다 가장 큰 리비전 1건)으로 메모리에서 만든다.
 * - 묶음은 기존 Process Flow와 같은 화면 표시용이다. 부모 연결을 새로 만들거나 저장하지 않는다.
 * - DB가 검증한 연결은 T→P(`transaction_observation_id`)·P→M(`process_observation_id`) FK뿐. 선행·인계는 문자열 일치 결과다.
 * - 깊이·분기 수를 가정하지 않는다(간선 목록). 순환이 있어도 그대로 내보낸다.
 */

export type Verification = 'not_checked' | 'unmatched' | 'verified' | 'ambiguous' | 'failed';
export type HandoffResult = 'match' | 'mismatch' | 'undetermined';
export type HandoffReason = 'no_out_message' | 'no_in_message' | 'multiple_messages' | 'missing_message_id'
  | 'excluded_transaction_suffix' | 'messages_truncated' | 'precedes_not_verified';

/**
 * 거래 노드 원천: 대표 TRANSACTION 행(pickTransactionRow). 대표가 모호하면 observationId·revisionNo·상태 등 대표 값은 null이고 ambiguous=true.
 */
export type GraphTransactionRow = {
  observationId: string | null; revisionNo: string | null; transactionId: string | null; interfaceId: string | null;
  channelId: string | null; status: string | null; responseCode: string | null; processTimeMs: string | null;
  processCount: string | null; retries: { retryCount: string | null; status: string | null; observationId: string }[] | null;
  /** 재시도 목록이 상한(GRAPH_RETRY_LIMIT)에 걸려 잘렸음. */
  retriesTruncated?: boolean;
  /** 대표 TRANSACTION 행을 고를 수 없음(재시도 번호 동률·없음, 실행 id 없음). */
  ambiguous?: boolean;
};
/** 거래 노드 `attrs.retries[]` 상한(재시도 번호 순 앞쪽부터). 넘으면 `attrs.retriesTruncated=true`. */
export const GRAPH_RETRY_LIMIT = 50;
export type GraphProcessRow = {
  observationId: string; executionId: string | null; revisionNo: string | null; transactionId: string | null;
  transactionObservationId: string | null; processId: string | null; dependsOn: string | null; status: string | null;
  responseCode: string | null; totalCount: string | null; retryCount: string | null; focus: boolean;
  decisionId: string | null; grade: string | null;
};
export type GraphMessageRow = {
  observationId: string; executionId: string | null; revisionNo: string | null; processObservationId: string | null;
  transactionId: string | null; processId: string | null; messageId: string | null; direction: string | null;
  status: string | null; dataSize: string | null; messageIndex: string | null;
};
export type GraphNode = { id: string; kind: 'transaction' | 'process' | 'message' | 'missing'; attrs: Record<string, unknown> };
export type GraphEdge = {
  id: string; kind: 'contains' | 'precedes' | 'hands_off'; from: string; to: string;
  verification: Verification | null; evidence: Record<string, unknown> | null;
};
export type GraphHeader = {
  layer: 'instance' | 'pattern' | 'incident'; id: string | null; version: string | null; sourceId: string | null;
  snapshotId: string | null; period: { from: string; to: string } | null; ruleSetRevision: number | null; generatedAt: string;
};
export type ProcessGraph = { graph: GraphHeader; nodes: GraphNode[]; edges: GraphEdge[] };

/** 그래프 없음 판정에서 앞뒤로 지우는 문자: ASCII 공백·탭·CR·LF만. JS `trim()`·Python `strip()`과 다르다(U+FEFF·U+0085·U+001C 등은 값). */
const GRAPH_TRIM_CHARS = new Set([' ', '\t', '\r', '\n']);
const graphTrim = (value: string) => {
  let start = 0, end = value.length;
  while (start < end && GRAPH_TRIM_CHARS.has(value[start])) start += 1;
  while (end > start && GRAPH_TRIM_CHARS.has(value[end - 1])) end -= 1;
  return value.slice(start, end);
};
/** 없음 판정 대상 원천 열. `DEPEND_PROCESS_ID`만 시작 단계 표기 `NONE`을 없음으로 본다. */
export type GraphField = 'TRANSACTION_ID' | 'PROCESS_ID' | 'DEPEND_PROCESS_ID' | 'MESSAGE_ID' | 'EXECUTION_ID' | 'RETRY_COUNT';
/**
 * 그래프 계약 함수 `graph_value_absent`(contracts/process-graph.md "없음 판정"). Detector `source_absent`·worker/derived_expression.py와 별개다.
 * 없음: NULL, 또는 ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 "" 또는 "\N". `DEPEND_PROCESS_ID`만 추가로 뺀 값이 정확히 "NONE"(대소문자 구분)이면 없음.
 * 없음이 아닌 값은 원래 값(앞뒤 문자 포함) 그대로 id·비교에 쓴다.
 */
export const graphValueAbsent = (value: string | null | undefined, field?: GraphField): boolean => {
  if (value === null || value === undefined) return true;
  const trimmed = graphTrim(value);
  return trimmed === '' || trimmed === '\\N' || (field === 'DEPEND_PROCESS_ID' && trimmed === 'NONE');
};
/** graphValueAbsent의 SQL 쪽(DEPEND_PROCESS_ID의 NONE 제외): NULL이거나 ASCII 공백·탭·CR·LF만 지운 값이 ''·'\N'. */
export const GRAPH_ABSENT_SQL = (column: string) => `(${column} IS NULL OR btrim(${column},E' \\t\\r\\n') IN ('','\\N'))`;
const present = (value: string | null | undefined, field?: GraphField): value is string => !graphValueAbsent(value, field);

/** id 구성 요소 이스케이프: '%' → '%25' 다음 ':' → '%3A'. 이것이 유일한 변환이다(trim·대소문자 변환 없음). */
export const escapeIdComponent = (value: string) => value.replace(/%/g, '%25').replace(/:/g, '%3A');
const esc = escapeIdComponent;
/** id 규칙(contracts/process-graph.md "id", 공용 시험 벡터 process-graph-id-vectors.json). 소비자는 id를 쪼개 읽지 않는다. */
export const graphIds = {
  transaction: (transactionId: string) => `transaction:${esc(transactionId)}`,
  process: (transactionId: string, processId: string) => `process:${esc(transactionId)}:${esc(processId)}`,
  message: (executionId: string) => `message:${esc(executionId)}`,
  unkeyed: (observationId: string) => `unkeyed:${esc(observationId)}`,
  missing: (referencingNodeId: string, processId: string) => `missing:${referencingNodeId}:${esc(processId)}`,
  edge: (kind: 'contains' | 'precedes', from: string, to: string) => `${kind}:${from}:${to}`,
  handsOff: (from: string, to: string, messageNodeId: string) => `hands_off:${from}:${to}:${messageNodeId}`,
};

/**
 * 층 B(인터페이스별 정상 패턴, `graph.layer = "pattern"`) id 예약(contracts/process-graph.md "층 B id 예약").
 * 지금은 공용 시험 벡터 확인용이며 API 응답에 쓰지 않는다.
 * - 노드 kind마다 구성 요소 수 고정: `pattern_step:<interfaceId>:<processId>:<adapterType>`. adapterType이 없음이면 마지막 칸은 빈 문자열
 * - interfaceId·processId 중 하나라도 없음이면 노드를 만들지 않는다(null, 미결로 센다)
 * - 간선은 `<kind>:<from>:<to>`. 층 B `hands_off`에는 메시지 노드가 없어 메시지 구성 요소가 없다(층 A는 4칸)
 */
export const patternGraphIds = {
  patternStep: (interfaceId: string | null | undefined, processId: string | null | undefined,
    adapterType: string | null | undefined): string | null => {
    if (!present(interfaceId) || !present(processId)) return null;
    return `pattern_step:${esc(interfaceId)}:${esc(processId)}:${present(adapterType) ? esc(adapterType) : ''}`;
  },
  edge: (kind: 'precedes' | 'branches_to' | 'hands_off', from: string, to: string) => `${kind}:${from}:${to}`,
};

/** 0채움 2자리 끝자리 거래 ID(`-01`·`-02`…): MESSAGE_ID 인계 기준선 산정 제외 구간(분석 결과 기반 임시 기준, ESB 업체 확인 전 — contracts/process-graph.md hands_off). */
export const EXCLUDED_TRANSACTION_SUFFIX = /-0\d$/;

type ProcessNodeState = {
  id: string; keyed: boolean; transactionId: string | null; processId: string | null; attempts: GraphProcessRow[];
  ambiguous: boolean; dependsOnValues: (string | null)[];
};

const retryNumber = (value: string | null) => {
  if (!present(value)) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
const byRetry = <T extends { retryCount: string | null; observationId: string }>(a: T, b: T) =>
  (retryNumber(a.retryCount) ?? Infinity) - (retryNumber(b.retryCount) ?? Infinity) || a.observationId.localeCompare(b.observationId);

/**
 * 거래 노드 대표 TRANSACTION 행(contracts/process-graph.md "거래 노드 대표 행", Detector relation-rules.md 7.2와 같은 규칙).
 * 입력: 같은 원본·같은 거래 ID의 TRANSACTION 실행(실행마다 최신 리비전) 수와 결측 수, 재시도 번호 큰 순 앞 2건(`top`, 번호 없음이 맨 앞).
 * - 실행 0건 → 고를 행 없음(row null, 모호 아님) / 1건 → 그 행
 * - 2건 이상: 재시도 번호가 없는(NULL·없음) 행 또는 실행 id가 없는 행이 하나라도 있거나, 가장 큰 재시도 번호가 2건 이상 → 모호(대표 없음)
 * - 그 밖 → 재시도 번호가 가장 큰 실행
 */
export function pickTransactionRow<T extends { retryCount: string | null }>(input: {
  executions: number; missingExecutionId: number; missingRetryCount: number; top: T[];
}): { row: T | null; ambiguous: boolean } {
  if (input.executions <= 0 || input.top.length === 0) return { row: null, ambiguous: false };
  if (input.executions === 1) return { row: input.top[0], ambiguous: false };
  const [first, second] = input.top.map((row) => retryNumber(row.retryCount));
  if (input.missingExecutionId > 0 || input.missingRetryCount > 0 || first === null || first === undefined
    || second === null || second === undefined || first === second) return { row: null, ambiguous: true };
  return { row: input.top[0], ambiguous: false };
}

export function buildInstanceGraph(input: {
  sourceId: string | null; transaction: GraphTransactionRow | null; processes: GraphProcessRow[]; messages: GraphMessageRow[];
  processesTruncated: boolean; messagesTruncated: boolean; generatedAt: string;
}): ProcessGraph {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const tx = input.transaction;
  const txNodeId = !tx ? null : present(tx.transactionId) ? graphIds.transaction(tx.transactionId)
    : tx.observationId !== null ? graphIds.unkeyed(tx.observationId) : null;
  if (tx && txNodeId) {
    nodes.push({ id: txNodeId, kind: 'transaction', attrs: {
      observationId: tx.observationId, revisionNo: tx.revisionNo, transactionId: tx.transactionId, interfaceId: tx.interfaceId,
      channelId: tx.channelId, status: tx.status, responseCode: tx.responseCode, processTimeMs: tx.processTimeMs,
      processCount: tx.processCount, unkeyed: !present(tx.transactionId), retries: [...(tx.retries ?? [])].sort(byRetry),
      retriesTruncated: tx.retriesTruncated === true, verification: tx.ambiguous ? 'ambiguous' : null,
    } });
  }

  // PROCESS: (거래 ID, Process ID)마다 노드 1개(재시도 묶음). 구성 요소가 없음이면 행마다 unkeyed 노드(파생 간선 없음).
  const groups = new Map<string, ProcessNodeState>();
  for (const row of input.processes) {
    const keyed = present(row.transactionId) && present(row.processId);
    const id = keyed ? graphIds.process(row.transactionId!, row.processId!) : graphIds.unkeyed(row.observationId);
    const group = groups.get(id) ?? { id, keyed, transactionId: row.transactionId, processId: row.processId, attempts: [],
      ambiguous: false, dependsOnValues: [] };
    group.attempts.push(row);
    groups.set(id, group);
  }
  const attemptOwner = new Map<string, ProcessNodeState>();
  for (const group of groups.values()) {
    group.attempts.sort(byRetry);
    const retries = group.attempts.map((a) => retryNumber(a.retryCount));
    // 실행마다 최신 리비전으로 줄인 뒤에도 같은 재시도 번호(또는 번호 없음)가 2건 이상이면 대표를 고르지 않는다.
    group.ambiguous = group.attempts.length > 1 && (retries.some((r) => r === null) || new Set(retries).size !== retries.length);
    // 없음 값(NULL·""·\N, 이 열만 NONE도)은 하나로 본다 = 시작 단계.
    group.dependsOnValues = [...new Set(group.attempts.map((a) => (present(a.dependsOn, 'DEPEND_PROCESS_ID') ? a.dependsOn : null)))];
    for (const attempt of group.attempts) attemptOwner.set(attempt.observationId, group);
  }

  // 선행: 후행 노드의 DEPEND_PROCESS_ID → 같은 거래의 선행 노드. from = 선행, to = 후행(원천 열과 화살표 방향이 반대).
  const missing = new Map<string, GraphNode>();
  const addMissing = (referencingNodeId: string, processId: string) => {
    const id = graphIds.missing(referencingNodeId, processId);
    if (!missing.has(id)) {
      missing.set(id, { id, kind: 'missing', attrs: { processId, reason: input.processesTruncated ? 'not_loaded' : 'not_logged' } });
    }
    return id;
  };
  const precedes: { edge: GraphEdge; from: ProcessNodeState | null; to: ProcessNodeState }[] = [];
  const selfDependency = new Set<string>();
  for (const successor of groups.values()) {
    if (!successor.keyed) continue;
    const dependsDisagree = successor.dependsOnValues.length > 1;
    for (const dependsOn of successor.dependsOnValues) {
      if (dependsOn === null) continue;
      if (dependsOn === successor.processId) { selfDependency.add(successor.id); continue; }
      const predecessor = groups.get(graphIds.process(successor.transactionId!, dependsOn));
      if (predecessor) {
        const verification: Verification = dependsDisagree || predecessor.ambiguous || successor.ambiguous ? 'ambiguous' : 'verified';
        precedes.push({ from: predecessor, to: successor, edge: { id: graphIds.edge('precedes', predecessor.id, successor.id),
          kind: 'precedes', from: predecessor.id, to: successor.id, verification, evidence: null } });
      } else {
        const missingId = addMissing(successor.id, dependsOn);
        precedes.push({ from: null, to: successor, edge: { id: graphIds.edge('precedes', missingId, successor.id), kind: 'precedes',
          from: missingId, to: successor.id, verification: dependsDisagree || successor.ambiguous ? 'ambiguous' : 'unmatched',
          evidence: null } });
      }
    }
  }
  const outgoing = new Map<string, Set<string>>();
  for (const { from, to } of precedes) if (from) outgoing.set(from.id, (outgoing.get(from.id) ?? new Set()).add(to.id));

  for (const group of groups.values()) {
    const last = group.attempts[group.attempts.length - 1];
    nodes.push({ id: group.id, kind: 'process', attrs: {
      transactionId: group.transactionId, processId: group.processId,
      dependsOn: group.dependsOnValues.length === 1 ? group.dependsOnValues[0] : null,
      status: group.ambiguous ? null : last.status, responseCode: group.ambiguous ? null : last.responseCode,
      focus: group.attempts.some((a) => a.focus), verification: group.ambiguous ? 'ambiguous' : null, unkeyed: !group.keyed,
      branch: (outgoing.get(group.id)?.size ?? 0) >= 2, selfDependency: selfDependency.has(group.id),
      attempts: group.attempts.map((a) => ({ observationId: a.observationId, executionId: a.executionId, revisionNo: a.revisionNo,
        retryCount: a.retryCount, status: a.status, responseCode: a.responseCode, dependsOn: a.dependsOn, totalCount: a.totalCount,
        focus: a.focus, decisionId: a.decisionId, grade: a.grade })),
    } });
  }
  if (tx && txNodeId) {
    for (const group of groups.values()) {
      // 대표 행이 없으면(모호) 어느 시도도 검증된 연결로 보지 않는다.
      const verified = tx.observationId !== null && group.attempts.every((a) => a.transactionObservationId === tx.observationId);
      edges.push({ id: graphIds.edge('contains', txNodeId, group.id), kind: 'contains', from: txNodeId, to: group.id,
        verification: verified ? 'verified' : 'not_checked', evidence: null });
    }
  }
  edges.push(...precedes.map((p) => p.edge));

  // MESSAGE: 실행 ID로 노드 1개. 포함 간선은 FK가 그래프 안 PROCESS 시도를 가리키면 그 노드(verified),
  // 아니면 같은 거래의 Process ID 문자열(not_checked), 그 Process가 없으면 missing 노드(unmatched).
  const messageNodes: GraphNode[] = [];
  const containsMessages: GraphEdge[] = [];
  const messagesOf = new Map<string, { nodeId: string; row: GraphMessageRow }[]>();
  for (const row of input.messages) {
    const id = present(row.executionId) ? graphIds.message(row.executionId) : graphIds.unkeyed(row.observationId);
    messageNodes.push({ id, kind: 'message', attrs: { observationId: row.observationId, executionId: row.executionId,
      revisionNo: row.revisionNo, messageId: row.messageId, processId: row.processId, direction: row.direction, status: row.status,
      dataSize: row.dataSize, messageIndex: row.messageIndex, unkeyed: !present(row.executionId) } });
    let owner: ProcessNodeState | undefined;
    let verification: Verification;
    if (row.processObservationId !== null && attemptOwner.has(row.processObservationId)) {
      owner = attemptOwner.get(row.processObservationId);
      verification = 'verified';
    } else if (present(row.processId) && present(row.transactionId)) {
      owner = groups.get(graphIds.process(row.transactionId, row.processId));
      verification = 'not_checked';
    } else continue; // 묶을 값 없음 → 포함 간선 없음
    if (owner) {
      containsMessages.push({ id: graphIds.edge('contains', owner.id, id), kind: 'contains', from: owner.id, to: id, verification, evidence: null });
      messagesOf.set(owner.id, [...(messagesOf.get(owner.id) ?? []), { nodeId: id, row }]);
    } else {
      const missingId = addMissing(id, row.processId!);
      containsMessages.push({ id: graphIds.edge('contains', missingId, id), kind: 'contains', from: missingId, to: id,
        verification: 'unmatched', evidence: null });
    }
  }
  nodes.push(...missing.values(), ...messageNodes);
  edges.push(...containsMessages);

  // 그래프 거래 ID: TRANSACTION 행 값, 그 행이 없거나 값이 없음이면 판정 PROCESS 값. 끝자리 제외 판단도 이 값으로 한다.
  const graphId = [tx?.transactionId, input.processes.find((p) => p.focus)?.transactionId].find((v) => present(v)) ?? null;
  // 메시지 인계: verified 선행 간선마다 선행 OUT × 후행 IN을 MESSAGE_ID 문자열로 비교(같은 거래). 요약은 선행 간선 evidence.
  const excluded = graphId !== null && EXCLUDED_TRANSACTION_SUFFIX.test(graphId);
  for (const { edge, from, to } of precedes) {
    if (edge.verification !== 'verified' || !from) {
      edge.evidence = { handoff: 'undetermined', reason: 'precedes_not_verified' };
      continue;
    }
    if (input.messagesTruncated) { edge.evidence = { handoff: 'undetermined', reason: 'messages_truncated' }; continue; }
    const outs = (messagesOf.get(from.id) ?? []).filter((m) => m.row.direction === 'OUT');
    const ins = (messagesOf.get(to.id) ?? []).filter((m) => m.row.direction === 'IN');
    if (!outs.length) { edge.evidence = { handoff: 'undetermined', reason: 'no_out_message' }; continue; }
    const results: { result: HandoffResult; reason: HandoffReason | null }[] = [];
    for (const out of outs) {
      let result: HandoffResult, reason: HandoffReason | null = null, inMessages: string[];
      const matched = present(out.row.messageId) ? ins.filter((i) => i.row.messageId === out.row.messageId) : [];
      if (!ins.length) { result = 'undetermined'; reason = 'no_in_message'; inMessages = []; }
      else if (!present(out.row.messageId)) { result = 'undetermined'; reason = 'missing_message_id'; inMessages = ins.map((i) => i.nodeId); }
      else if (matched.length) { result = 'match'; inMessages = matched.map((i) => i.nodeId); }
      else if (outs.length === 1 && ins.length === 1) {
        inMessages = [ins[0].nodeId];
        if (!present(ins[0].row.messageId)) { result = 'undetermined'; reason = 'missing_message_id'; }
        else if (excluded) { result = 'undetermined'; reason = 'excluded_transaction_suffix'; }
        else result = 'mismatch';
      } else { result = 'undetermined'; reason = 'multiple_messages'; inMessages = ins.map((i) => i.nodeId); }
      results.push({ result, reason });
      edges.push({ id: graphIds.handsOff(from.id, to.id, out.nodeId), kind: 'hands_off', from: from.id, to: to.id,
        verification: edge.verification, evidence: { result, reason, outMessage: out.nodeId, inMessages } });
    }
    const summary = results.find((r) => r.result === 'match') ?? results.find((r) => r.result === 'mismatch') ?? results[0];
    edge.evidence = { handoff: summary.result, reason: summary.reason };
  }

  return {
    graph: { layer: 'instance', id: graphId, version: null, sourceId: input.sourceId, snapshotId: null, period: null,
      ruleSetRevision: null, generatedAt: input.generatedAt },
    nodes, edges,
  };
}
