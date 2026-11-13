import crypto from 'node:crypto';
import type { Pool } from 'pg';
import { softRuleField } from './methodCatalog.js';
import { CONTENT_HASH_VERSION, RuleSetError, THRESHOLD_MAX_LENGTH, thresholdFixedDecimal } from './ruleSetContract.js';

/**
 * 규칙 추천(층 B 관계 규칙·처리 시간 후보) 읽기·작업 요청 — contracts/rule-candidates-ui.md.
 * 원천: Trainer `recommend_relations` 작업 결과 JSON(run_jobs.result_summary, Trainer
 * relation_candidates.py `relationContractVersion` "relation-candidates-experimental-2"). 새 표 없음.
 * 결과를 바꾸지 않고 화면에 필요한 값만 골라 낸다. 모양이 계약과 다르면 빈 목록으로 바꾸지 않고 503으로 멈춘다.
 */
type Queryable = Pick<Pool, 'query'>;

export const RELATION_JOB_KIND = 'recommend_relations';
export const RELATION_CONTRACT_VERSION = 'relation-candidates-experimental-2';
/** 관계 규칙 kind(Trainer 후보 → Detector relation-rules.md 2절). allowed_branch는 후보가 아니라 relationNotes. */
const RELATION_KINDS = ['required_step', 'precedes', 'message_handoff'] as const;
const RELATION_STATUSES = ['candidate', 'review_needed', 'insufficient_evidence', 'not_candidate'] as const;
const THRESHOLD_STATUSES = ['candidate', 'insufficient_evidence', 'no_variation', 'rejected'] as const;
/** 후보 id = `<작업 id>.r<번호>`(관계) · `<작업 id>.t<번호>`(처리 시간). 번호는 그 작업 결과 배열 위치(결과는 바뀌지 않는다). */
export const CANDIDATE_ID = /^([0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.([rt])(0|[1-9]\d{0,5})$/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const INTERFACE_MAX = 512;
/** 작업 입력 상한 — Trainer parse_input·compile_suffix_patterns와 같은 값(패턴 10개·100자). 제외 단계 목록은 API 상한. */
const SUFFIX_PATTERNS_MAX = 10;
const SUFFIX_PATTERN_LENGTH_MAX = 100;
const EXCLUDE_MAX = 200;
const EXCLUDE_LENGTH_MAX = 200;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

/**
 * 승인 가능 여부. 승인 = 룰 세트 저장(PUT /api/rule-set)에 relationRules·softRules.scope를 싣는 것이고,
 * 그 두 필드는 hash v3에서만 저장된다(rule-set.md v3.7). CONTENT_HASH_VERSION이 3이면 승인 단추를 연다.
 * 2로 되돌린 배포(첫 v3 행 전에만 가능)에서는 단추를 끄고 사유를 보인다.
 */
export const approvalState = (version = CONTENT_HASH_VERSION) =>
  version >= 3 ? { available: true, reason: null, contentHashVersion: version }
    : { available: false, reason: 'rule_set_hash_v3_pending', contentHashVersion: version };

type Json = Record<string, unknown>;
const object = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value);
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const text = (value: unknown): value is string => typeof value === 'string';
const nullableText = (value: unknown) => value === null || text(value);
const invalid = () => new RuleSetError('rule_candidate_result_invalid', 503);

type Counts = { applicable: number; satisfied: number; violated: number; undetermined: number };
const countsOf = (value: unknown): Counts => {
  if (!object(value)) throw invalid();
  const { applicable, satisfied, violated, undetermined } = value;
  if (![applicable, satisfied, violated, undetermined].every(count)) throw invalid();
  return { applicable, satisfied, violated, undetermined } as Counts;
};
const rateOf = (value: unknown) => {
  if (value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) throw invalid();
  return value;
};
const step = (value: unknown) => {
  if (value === null) return null;
  if (!object(value) || !text(value.processId)) throw invalid();
  return { processId: value.processId };
};
const tally = (value: unknown): Record<string, number> => {
  if (value === undefined || value === null) return {};
  if (!object(value) || !Object.values(value).every(count)) throw invalid();
  return value as Record<string, number>;
};

type RawResult = Json & {
  candidates: Json[]; thresholdCandidates: Json[]; graph: { graph: Json; nodes: Json[]; edges: Json[] };
  data: { interfaceTransactions: Json }; parameters: Json;
};
/** 결과 머리 검사: 계약 버전·배열 모양. 버전이 다르면 해석하지 않는다. */
export function checkResult(value: unknown): RawResult {
  if (!object(value)) throw invalid();
  if (value.relationContractVersion !== RELATION_CONTRACT_VERSION) throw new RuleSetError('rule_candidate_contract_unsupported', 503);
  const graph = value.graph;
  if (!Array.isArray(value.candidates) || !Array.isArray(value.thresholdCandidates) || !object(graph) || !object(graph.graph)
    || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !object(value.data) || !object(value.data.interfaceTransactions)
    || !object(value.parameters)) throw invalid();
  return value as RawResult;
}

/** 관계 후보 1건(목록 열). 승인 가능·짝 승인 가능은 Trainer 값 그대로(계산하지 않음). */
export function relationItem(jobId: string, index: number, raw: unknown) {
  if (!object(raw) || !(RELATION_KINDS as readonly unknown[]).includes(raw.kind) || !(RELATION_STATUSES as readonly unknown[]).includes(raw.status)
    || typeof raw.approvable !== 'boolean' || !object(raw.display) || !text(raw.display.interfaceId)) throw invalid();
  if (raw.scope !== null && !object(raw.scope)) throw invalid();
  if (raw.pairApprovable !== undefined && typeof raw.pairApprovable !== 'boolean') throw invalid();
  const to = step(raw.to);
  if ((raw.kind === 'required_step') !== (to === null)) throw invalid();
  return {
    candidateId: `${jobId}.r${index}`,
    kind: raw.kind as (typeof RELATION_KINDS)[number],
    status: raw.status as (typeof RELATION_STATUSES)[number],
    approvable: raw.approvable,
    listedForPairing: raw.listedForPairing === true,
    pairApprovable: raw.kind === 'message_handoff' ? (raw.pairApprovable ?? null) : null,
    interfaceId: raw.display.interfaceId,
    scope: raw.scope as Json | null,
    from: step(raw.from)!,
    to,
    match: object(raw.match) ? raw.match : null,
    counts: countsOf(raw.counts),
    satisfactionRate: rateOf(raw.satisfactionRate),
    evaluatedAt: text(raw.evaluatedAt) ? raw.evaluatedAt : null,
    patternEdgeId: text(raw.patternEdgeId) ? raw.patternEdgeId : null,
    nonStandardProcessId: object(raw.attrs) && raw.attrs.nonStandardProcessId === true,
  };
}
type RelationItem = ReturnType<typeof relationItem>;

/**
 * Trainer 제안 임계값 → number|null. Trainer는 방법 카탈로그 `thresholdSerialization: "fixed-decimal-string"`대로 고정소수 문자열("704")을 낸다
 * (worker threshold_values.fixed_threshold). 문자열은 저장 검증과 같은 규칙(40자·DSL 숫자·유효숫자 15자리·정수 필드 정수·number 왕복 불변,
 * threshold_values.threshold_valid)을 통과해야 하고, 왕복 문자열이 원문과 같아야 한다("1e3"·"+1"·"704.0"·" 704"·"" 거부).
 * 옛 결과의 finite number도 읽는다. 그 밖은 undefined(호출자가 rule_candidate_result_invalid).
 */
export function candidateThreshold(value: unknown, valueType: 'integer' | 'number'): number | null | undefined {
  if (value === null) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value !== 'string' || value.length > THRESHOLD_MAX_LENGTH) return undefined;
  const parsed = Number(value);
  return thresholdFixedDecimal(parsed, valueType) === value ? parsed : undefined;
}

/** 처리 시간(이상 징후) 후보 1건. 제안 임계값은 Trainer 값(data_p95 = 코드 계산 p95)이고 LLM 제안이 아니다. */
export function thresholdItem(jobId: string, index: number, raw: unknown) {
  if (!object(raw) || raw.field !== 'elapsed_ms' || !(THRESHOLD_STATUSES as readonly unknown[]).includes(raw.status) || !object(raw.scope)
    || !text(raw.scope.processId) || !object(raw.display) || !Array.isArray(raw.display.interfaceIds) || !raw.display.interfaceIds.every(text)
    || !object(raw.evidence)) throw invalid();
  const { evidence } = raw;
  if (![evidence.rows, evidence.referenceRows, evidence.n, evidence.endTimeMissingReferenceRows, evidence.unavailableReferenceRows].every(count)) throw invalid();
  const threshold = candidateThreshold(raw.threshold, softRuleField(raw.field)?.valueType ?? 'number');
  if (threshold === undefined) throw invalid();
  if (!nullableText(raw.displayThreshold) || !nullableText(raw.operator) || !nullableText(raw.reason) || !text(raw.unit)) throw invalid();
  const adapterType = raw.scope.adapterType;
  if (adapterType !== undefined && !text(adapterType)) throw invalid();
  const finite = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
  return {
    candidateId: `${jobId}.t${index}`,
    field: 'elapsed_ms' as const,
    status: raw.status as (typeof THRESHOLD_STATUSES)[number],
    reason: raw.reason as string | null,
    scope: { processId: raw.scope.processId, ...(adapterType === undefined ? {} : { adapterType }) } as { processId: string; adapterType?: string },
    interfaceIds: raw.display.interfaceIds as string[],
    operator: raw.operator as string | null,
    threshold,
    displayThreshold: raw.displayThreshold as string | null,
    unit: raw.unit,
    source: text(raw.source) ? raw.source : null,
    evidence: {
      n: evidence.n as number, rows: evidence.rows as number, referenceRows: evidence.referenceRows as number,
      endTimeMissingReferenceRows: evidence.endTimeMissingReferenceRows as number, unavailableReferenceRows: evidence.unavailableReferenceRows as number,
      p95: finite(evidence.p95), min: finite(evidence.min), max: finite(evidence.max),
      quantileMethod: text(evidence.quantileMethod) ? evidence.quantileMethod : null,
    },
    limitation: text(raw.limitation) ? raw.limitation : null,
    nonStandardProcessId: raw.nonStandardProcessId === true,
  };
}
type ThresholdItem = ReturnType<typeof thresholdItem>;

export type JobRow = { id: string; status: string; errorCode: string | null; createdAt: string; finishedAt: string | null; inputSpec: Json; result?: unknown };
export const relationJobOf = (row: JobRow) => ({ id: row.id, status: row.status, errorCode: row.errorCode, createdAt: row.createdAt, finishedAt: row.finishedAt,
  snapshotId: text(row.inputSpec?.snapshotId) ? row.inputSpec.snapshotId : null });
const JOB_COLUMNS = `id::text AS id, status::text AS status, error_code AS "errorCode", ${ISO('created_at')} AS "createdAt",
  ${ISO('finished_at')} AS "finishedAt", input_spec AS "inputSpec"`;
// kind를 text로 비교한다: job_kind enum에 값이 아직 없을 때(DB migration 전) 'recommend_relations' 리터럴이 enum 변환 오류를 내지 않게.
const KIND = `kind::text = '${RELATION_JOB_KIND}'`;

const LATEST_SUCCEEDED = `${KIND} AND status = 'succeeded' ORDER BY finished_at DESC NULLS LAST, created_at DESC, id DESC LIMIT 1`;
/** 가장 최근 작업(상태 무관)과 가장 최근 성공 작업(결과 포함). 프로토콜 초안도 같은 결과를 읽는다(protocolDrafts.ts). */
export async function latestRelationJobs(db: Queryable) {
  const [{ rows: [latest] }, { rows: [succeeded] }] = await Promise.all([
    db.query<JobRow>(`SELECT ${JOB_COLUMNS} FROM run_jobs WHERE ${KIND} ORDER BY created_at DESC, id DESC LIMIT 1`),
    db.query<JobRow>(`SELECT ${JOB_COLUMNS}, result_summary AS result FROM run_jobs WHERE ${LATEST_SUCCEEDED}`),
  ]);
  return { latest: latest ?? null, succeeded: succeeded ?? null };
}
/** 상세의 isLatest용: 가장 최근 성공 작업 id만(결과 JSON은 다시 읽지 않는다). */
async function latestSucceededId(db: Queryable) {
  const { rows: [row] } = await db.query<{ id: string }>(`SELECT id::text AS id FROM run_jobs WHERE ${LATEST_SUCCEEDED}`);
  return row?.id ?? null;
}

const resultHead = (result: RawResult, job: JobRow) => {
  const parameters = result.parameters as Json;
  const period = object(result.period) ? result.period : {};
  return {
    job: relationJobOf(job),
    snapshotId: text(result.snapshotId) ? result.snapshotId : null,
    sourceId: text(result.sourceId) ? result.sourceId : null,
    period: { from: text(period.from) ? period.from : null, until: text(period.until) ? period.until : null, timeBasis: text(period.timeBasis) ? period.timeBasis : null },
    generatedAt: text(result.graph.graph.generatedAt) ? result.graph.graph.generatedAt : null,
    experimental: result.experimental === true,
    parameters: {
      minApplicable: parameters.minApplicable, candidatePercent: parameters.candidatePercent, reviewPercent: parameters.reviewPercent,
      maxCounterexamples: parameters.maxCounterexamples,
      excludeProcessIds: Array.isArray(parameters.excludeProcessIds) ? parameters.excludeProcessIds : [],
      handoffMismatchSuffixPatterns: Array.isArray(parameters.handoffMismatchSuffixPatterns) ? parameters.handoffMismatchSuffixPatterns : [],
    },
  };
};

/** 인터페이스 목록: 승인 가능 후보 많은 순 → 거래 많은 순 → id. 첫 항목이 기본 선택. */
const interfacesOf = (result: RawResult, relations: RelationItem[], thresholds: ThresholdItem[]) => {
  const transactions = result.data.interfaceTransactions as Json;
  const ids = new Set<string>([...Object.keys(transactions), ...relations.map((item) => item.interfaceId)]);
  return [...ids].map((interfaceId) => ({
    interfaceId,
    transactions: count(transactions[interfaceId]) ? transactions[interfaceId] as number : 0,
    relationCandidates: relations.filter((item) => item.interfaceId === interfaceId).length,
    approvable: relations.filter((item) => item.interfaceId === interfaceId && item.approvable).length,
    thresholdCandidates: thresholds.filter((item) => item.interfaceIds.includes(interfaceId)).length,
  })).sort((a, b) => b.approvable - a.approvable || b.transactions - a.transactions || (a.interfaceId < b.interfaceId ? -1 : a.interfaceId > b.interfaceId ? 1 : 0));
};

/** 층 B 그래프 중 한 인터페이스: pattern_step 노드(attrs.interfaceId 일치)와 양 끝이 그 안에 있는 간선. 간선 evidence는 화면에 쓰는 값만. */
const graphOf = (result: RawResult, interfaceId: string) => {
  const nodes = result.graph.nodes.filter((node) => object(node) && node.kind === 'pattern_step' && object(node.attrs) && node.attrs.interfaceId === interfaceId)
    .map((node) => {
      const attrs = node.attrs as Json;
      return { id: String(node.id), kind: 'pattern_step', attrs: { interfaceId, processId: attrs.processId ?? null, adapterType: attrs.adapterType ?? null,
        transactions: count(attrs.transactions) ? attrs.transactions : null, nonStandardProcessId: attrs.nonStandardProcessId === true } };
    });
  const ids = new Set(nodes.map((node) => node.id));
  const edges = result.graph.edges.filter((edge) => object(edge) && ['precedes', 'hands_off', 'branches_to'].includes(edge.kind as string)
    && ids.has(edge.from as string) && ids.has(edge.to as string))
    .map((edge) => {
      const evidence = object(edge.evidence) ? edge.evidence : {};
      const pick = (key: string) => (evidence[key] === undefined ? null : evidence[key]);
      return { id: String(edge.id), kind: edge.kind as string, from: edge.from as string, to: edge.to as string,
        evidence: { status: pick('status'), satisfactionRate: pick('satisfactionRate'), applicable: pick('applicable'), satisfied: pick('satisfied'),
          violated: pick('violated'), undetermined: pick('undetermined'), observedTransactions: pick('observedTransactions') } };
    });
  return { graph: { ...result.graph.graph, layer: 'pattern' }, nodes, edges };
};

/** GET /api/rule-set/candidates — 가장 최근 성공한 후보 작업 결과(인터페이스 하나의 그래프·후보) + 가장 최근 작업 상태. */
export async function readCandidateList(db: Queryable, interfaceId: string | null) {
  const { latest, succeeded } = await latestRelationJobs(db);
  const approval = approvalState();
  if (!succeeded) return { latestJob: latest && relationJobOf(latest), approval, result: null };
  const result = checkResult(succeeded.result);
  const relations = result.candidates.map((raw, index) => relationItem(succeeded.id, index, raw));
  const thresholds = result.thresholdCandidates.map((raw, index) => thresholdItem(succeeded.id, index, raw));
  const interfaces = interfacesOf(result, relations, thresholds);
  if (interfaceId !== null && !interfaces.some((item) => item.interfaceId === interfaceId)) throw new RuleSetError('rule_candidate_interface_not_found', 404);
  const selected = interfaceId ?? interfaces[0]?.interfaceId ?? null;
  const limitations = [...new Set(thresholds.map((item) => item.limitation).filter((value): value is string => value !== null))];
  return {
    latestJob: latest && relationJobOf(latest),
    approval,
    result: {
      ...resultHead(result, succeeded),
      interfaces,
      selectedInterfaceId: selected,
      graph: selected === null ? null : graphOf(result, selected),
      relationCandidates: relations.filter((item) => item.interfaceId === selected),
      thresholdCandidates: thresholds.filter((item) => selected !== null && item.interfaceIds.includes(selected)),
      thresholdsWithoutInterface: thresholds.filter((item) => !item.interfaceIds.length).length,
      thresholdLimitations: limitations,
      totals: { relationCandidates: relations.length, approvable: relations.filter((item) => item.approvable).length, thresholdCandidates: thresholds.length,
        thresholdApprovable: thresholds.filter((item) => item.status === 'candidate').length },
    },
  };
}

/** 반례 참조의 거래 id. 첫 참조가 `transaction:<id>`(Trainer relation_candidates.py transaction_ref, 구성요소 이스케이프 `%`→`%25`·`:`→`%3A`). */
export const transactionOfRef = (ref: unknown): string | null => {
  if (!text(ref) || !ref.startsWith('transaction:')) return null;
  const body = ref.slice('transaction:'.length);
  if (!body || body.includes(':') || /%(?!25|3A)/i.test(body)) return null;
  return body.replace(/%3A/gi, ':').replace(/%25/g, '%');
};

/**
 * 승인 시 저장할 규칙 초안(Detector relation-rules.md 2절 relationRules 항목, 4절 softRules.scope). id·severity·name은 승인 때 정한다.
 * precedes·message_handoff의 scope.interfaceId는 표시·출처용(hash 제외), required_step만 판정 키.
 */
export const relationRuleDraft = (item: RelationItem, source: unknown) => ({
  kind: item.kind,
  scope: item.kind === 'required_step' ? { interfaceId: item.interfaceId }
    : { interfaceId: item.interfaceId, ...(object(item.scope) && text(item.scope.adapterType) ? { adapterType: item.scope.adapterType } : {}) },
  from: item.from, to: item.to,
  match: item.kind === 'message_handoff' ? { from: 'OUT', to: 'IN', key: 'message_id' } : null,
  enabled: true,
  source: object(source) ? source : null,
});
export const softRuleDraft = (item: ThresholdItem) => item.threshold === null || item.operator === null ? null : ({
  field: item.field, operator: item.operator, threshold: item.threshold, unit: item.unit, enabled: true,
  scope: { ...item.scope, ...(item.interfaceIds.length === 1 ? { interfaceId: item.interfaceIds[0] } : {}) },
});

type DecisionRow = { transactionId: string; decisionId: string };
/**
 * 반례 거래마다 판정 상세로 갈 판정 1건: 같은 원본·거래의 PROCESS 판정 중 대상 단계가 있으면 그 단계, 없으면 아무 단계에서 가장 최근
 * (decided_at, id 내림차순 — 분석 화면 최신 판정 순서와 같음). 거래마다 DISTINCT ON으로 1건만 골라, 판정이 많은 거래가 다른 거래를 밀어내지 않는다.
 */
async function decisionsOf(db: Queryable, sourceId: string | null, transactionIds: string[], processId: string | null) {
  if (!sourceId || !transactionIds.length) return new Map<string, string>();
  const { rows } = await db.query<DecisionRow>(`SELECT DISTINCT ON (p.transaction_id) p.transaction_id AS "transactionId", d.id::text AS "decisionId"
    FROM data_processes p JOIN data_observations o ON o.id = p.observation_id
    JOIN run_process_decisions d ON d.process_observation_id = o.id
    WHERE o.source_id = $1 AND p.transaction_id = ANY($2::text[])
    ORDER BY p.transaction_id, (p.process_id = $3::text) IS TRUE DESC, d.decided_at DESC, d.id DESC`,
  [sourceId, [...new Set(transactionIds)], processId]);
  return new Map(rows.map((row) => [row.transactionId, row.decisionId]));
}

const sameScope = (left: unknown, right: unknown) => JSON.stringify(left ?? null) === JSON.stringify(right ?? null);

/** GET /api/rule-set/candidates/:candidateId — 후보 1건 + 반례(판정 상세 연결) + 짝 선행 후보 + 저장 초안. */
export async function readCandidateDetail(db: Queryable, candidateId: string) {
  const match = CANDIDATE_ID.exec(candidateId);
  if (!match) throw new RuleSetError('invalid_candidate_id');
  const [, jobId, type, position] = match;
  const { rows: [row] } = await db.query<JobRow>(`SELECT ${JOB_COLUMNS}, result_summary AS result FROM run_jobs WHERE id = $1 AND ${KIND} AND status = 'succeeded'`, [jobId]);
  if (!row) throw new RuleSetError('rule_candidate_not_found', 404);
  const result = checkResult(row.result);
  const head = { ...resultHead(result, row), isLatest: (await latestSucceededId(db)) === row.id, approval: approvalState() };
  const index = Number(position);
  if (type.toLowerCase() === 't') {
    const raw = result.thresholdCandidates[index];
    if (raw === undefined) throw new RuleSetError('rule_candidate_not_found', 404);
    const item = thresholdItem(row.id, index, raw);
    return { ...head, type: 'threshold' as const, threshold: item, ruleDraft: softRuleDraft(item) };
  }
  const raw = result.candidates[index];
  if (raw === undefined) throw new RuleSetError('rule_candidate_not_found', 404);
  const item = relationItem(row.id, index, raw);
  const full = raw as Json;
  const refs = Array.isArray(full.counterexampleRefs) ? full.counterexampleRefs.filter(object) : [];
  const transactions = refs.map((ref) => (Array.isArray(ref.refs) ? transactionOfRef(ref.refs[0]) : null));
  const decisions = await decisionsOf(db, head.sourceId, transactions.filter((value): value is string => value !== null), item.to?.processId ?? null);
  const counterexamples = refs.map((ref, position) => {
    const transactionId = transactions[position];
    return { transactionId, reason: text(ref.reason) ? ref.reason : null, transactionStatus: text(ref.transactionStatus) ? ref.transactionStatus : null,
      decisionId: transactionId === null ? null : decisions.get(transactionId) ?? null };
  });
  let pairedPrecedes = null;
  if (item.kind === 'message_handoff') {
    // Trainer가 짝 간선 id(pairedPrecedesEdgeId = 짝 precedes의 patternEdgeId)를 주면 그것으로만 잇는다(못 찾으면 null).
    // 필드가 없거나 null인 결과(옛 결과)만 같은 인터페이스·from·to·scope로 찾는다. 문자열도 null도 아닌 값은 추측하지 않고 짝 없음.
    const pairedEdge = full.pairedPrecedesEdgeId;
    const byTuple = pairedEdge === undefined || pairedEdge === null;
    const pairIndex = !byTuple && !text(pairedEdge) ? -1 : result.candidates.findIndex((other) => object(other) && other.kind === 'precedes' && (!byTuple
      ? other.patternEdgeId === pairedEdge
      : object(other.display) && other.display.interfaceId === item.interfaceId && object(other.from) && other.from.processId === item.from.processId
        && object(other.to) && other.to.processId === item.to?.processId && sameScope(other.scope, item.scope)));
    if (pairIndex >= 0) {
      const pair = relationItem(row.id, pairIndex, result.candidates[pairIndex]);
      pairedPrecedes = { candidateId: pair.candidateId, status: pair.status, approvable: pair.approvable, counts: pair.counts, satisfactionRate: pair.satisfactionRate };
    }
  }
  return {
    ...head, type: 'relation' as const,
    relation: {
      ...item,
      violationReasons: tally(full.violationReasons), undeterminedReasons: tally(full.undeterminedReasons),
      violationsByTransactionStatus: tally(full.violationsByTransactionStatus),
      fromAdapterTypes: Array.isArray(full.fromAdapterTypes) ? full.fromAdapterTypes : [],
      bySuffixClass: object(full.bySuffixClass) ? full.bySuffixClass : null,
    },
    counterexamples,
    pairedPrecedes,
    ruleDraft: relationRuleDraft(item, full.source),
  };
}

export function parseCandidateListQuery(query: Record<string, unknown>): string | null {
  if (Object.keys(query).some((key) => key !== 'interfaceId')) throw new RuleSetError('unknown_candidate_filter');
  const value = query.interfaceId;
  if (value === undefined) return null;
  if (!text(value) || !value || [...value].length > INTERFACE_MAX) throw new RuleSetError('invalid_interface_id');
  return value;
}

export type CandidateJobRequest = { snapshotId: string; excludeProcessIds?: string[]; handoffMismatchSuffixPatterns?: string[] };
/** POST /api/rule-set/candidates/jobs 본문. 키·형식만 본다(정규식 컴파일은 Trainer — Python 문법이라 JS로 검사하지 않음). */
export function parseCandidateJobRequest(body: unknown): CandidateJobRequest {
  if (!object(body) || Object.keys(body).some((key) => !['snapshotId', 'excludeProcessIds', 'handoffMismatchSuffixPatterns'].includes(key))
    || !text(body.snapshotId) || !UUID.test(body.snapshotId)) throw new RuleSetError('invalid_candidate_job_request');
  const strings = (value: unknown, max: number, length: number) => Array.isArray(value) && value.length <= max
    && value.every((item) => text(item) && item.length > 0 && [...item].length <= length) && new Set(value).size === value.length;
  const { excludeProcessIds, handoffMismatchSuffixPatterns } = body;
  if (excludeProcessIds !== undefined && !strings(excludeProcessIds, EXCLUDE_MAX, EXCLUDE_LENGTH_MAX)) throw new RuleSetError('invalid_candidate_job_request');
  if (handoffMismatchSuffixPatterns !== undefined && !strings(handoffMismatchSuffixPatterns, SUFFIX_PATTERNS_MAX, SUFFIX_PATTERN_LENGTH_MAX)) {
    throw new RuleSetError('invalid_candidate_job_request');
  }
  return { snapshotId: body.snapshotId.toLowerCase(),
    ...(excludeProcessIds === undefined ? {} : { excludeProcessIds: excludeProcessIds as string[] }),
    ...(handoffMismatchSuffixPatterns === undefined ? {} : { handoffMismatchSuffixPatterns: handoffMismatchSuffixPatterns as string[] }) };
}

type PgError = { code?: string; constraint?: string; message?: string; routine?: string; dataType?: string };
/**
 * DB 준비 전 실패만 503으로 바꾼다: 22P02는 enum 입력 함수(PG routine `enum_in`) 오류이고 그 enum이 job_kind일 때만,
 * 23514는 run_jobs_v03_contract CHECK일 때만. enum 이름은 PG가 dataType 필드로 주면 그 값, 없으면(enum_in은 주지 않음) 메시지 안의
 * 형식 이름 `job_kind`(식별자라 서버 언어 설정과 무관)로 본다. 같은 SQLSTATE라도 다른 원인(다른 CHECK·다른 enum·형식 오류)은 null → 원래 오류를 그대로 던진다.
 */
export const relationJobUnavailable = (error: unknown) => {
  const { code, constraint, message, routine, dataType } = (object(error) ? error : {}) as PgError;
  const jobKind = typeof dataType === 'string' ? dataType === 'job_kind' : typeof message === 'string' && /(^|[^\w])job_kind([^\w]|$)/.test(message);
  if (code === '22P02' && routine === 'enum_in' && jobKind) return new RuleSetError('relation_job_kind_unavailable', 503);
  if (code === '23514' && constraint === 'run_jobs_v03_contract') return new RuleSetError('relation_job_contract_unavailable', 503);
  return null;
};
const optionsOf = (request: CandidateJobRequest) => ({ ...(request.excludeProcessIds ? { excludeProcessIds: request.excludeProcessIds } : {}),
  ...(request.handoffMismatchSuffixPatterns ? { handoffMismatchSuffixPatterns: request.handoffMismatchSuffixPatterns } : {}) });
const sha256 = (value: unknown) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
/** 지금 request_hash: 요청 본문(kind·snapshotId·선택 키)만. */
export const candidateRequestHash = (request: CandidateJobRequest) => sha256({ kind: RELATION_JOB_KIND, snapshotId: request.snapshotId, ...optionsOf(request) });
/**
 * 옛 request_hash(hash에서 sourceId를 빼기 전 구현): 입력 전체(kind·snapshotId·sourceId·선택 키, 이 순서). sourceId는 그 작업 input_spec에 저장된 값.
 * 저장된 sourceId가 없으면 null(옛 hash와 비교하지 않음).
 */
export const legacyRequestHash = (request: CandidateJobRequest, inputSpec: unknown) =>
  object(inputSpec) && text(inputSpec.sourceId)
    ? sha256({ kind: RELATION_JOB_KIND, snapshotId: request.snapshotId, sourceId: inputSpec.sourceId, ...optionsOf(request) }) : null;
const JOB_RETURN = `id::text AS id, status::text AS status, kind::text AS kind, input_spec AS "inputSpec"`;
/**
 * 후보 작업 요청(관리자). 작업 표 행은 기존 학습 작업과 같은 v03 training 모양(jobRoutes.ts)이고 입력은 Trainer parse_input 키 그대로.
 * 멱등: 같은 요청자·키의 작업을 스냅샷 확인보다 먼저 찾는다 → 재요청은 그 사이 스냅샷 상태가 바뀌어도 같은 작업 200.
 * request_hash는 요청 본문(kind·snapshotId·선택 키)만 — 스냅샷에서 읽는 sourceId는 넣지 않는다(재요청 때 스냅샷을 다시 읽지 않으므로).
 * 그 전 구현으로 만든 작업은 sourceId를 넣은 hash(legacyRequestHash)라, 재요청 비교는 두 hash 중 하나와 같으면 같은 본문으로 본다.
 * DB 준비 전 실패는 원인별 503(relationJobUnavailable).
 */
export async function enqueueCandidateJob(pool: Pool, request: CandidateJobRequest, actorId: string, idempotencyKey: string | null) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const options = optionsOf(request);
    const requestHash = candidateRequestHash(request);
    const key = idempotencyKey ?? crypto.randomUUID();
    const prior = async () => {
      const { rows: [existing] } = await client.query(`SELECT ${JOB_RETURN}, request_hash FROM run_jobs
        WHERE requested_by_actor_id = $1 AND ${KIND} AND idempotency_key = $2`, [actorId, key]);
      if (!existing) return null;
      if (existing.request_hash !== requestHash && existing.request_hash !== legacyRequestHash(request, existing.inputSpec)) {
        throw new RuleSetError('idempotency_payload_conflict', 409);
      }
      const { request_hash: _hash, ...rest } = existing;
      return rest;
    };
    if (idempotencyKey !== null) {
      const existing = await prior();
      if (existing) {
        await client.query('COMMIT');
        return { created: false, job: existing };
      }
    }
    const { rows: [snapshot] } = await client.query<{ sourceId: string }>(`SELECT source_id::text AS "sourceId" FROM data_snapshots
      WHERE id = $1 AND status = 'ready' AND schema_version = 'process-v1'`, [request.snapshotId]);
    if (!snapshot) throw new RuleSetError('snapshot_not_ready', 409);
    const input = { kind: RELATION_JOB_KIND, snapshotId: request.snapshotId, sourceId: snapshot.sourceId, ...options };
    const id = crypto.randomUUID();
    let inserted;
    try {
      inserted = await client.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash,
          input_snapshot_id, input_spec, max_attempts, attempt_count, next_run_at, created_at, contract_version, queue, producer_kind, producer_key)
        VALUES ($1, $2, 'queued', $3, 'admin', $4, $5, $6, $7::jsonb, 3, 0, clock_timestamp(), clock_timestamp(), 'v03', 'training', 'user', $3)
        ON CONFLICT (requested_by_actor_id, kind, idempotency_key) DO NOTHING
        RETURNING ${JOB_RETURN}`,
      [id, RELATION_JOB_KIND, actorId, key, requestHash, request.snapshotId, JSON.stringify(input)]);
    } catch (error) {
      throw relationJobUnavailable(error) ?? error;
    }
    let row = inserted.rows[0];
    if (!row) {
      // 같은 키의 동시 요청이 먼저 넣은 경우.
      row = await prior();
      if (!row) throw new RuleSetError('idempotency_payload_conflict', 409);
    } else {
      await client.query(`INSERT INTO powercode_queue.training_jobs (id, payload) VALUES ($1, $2::jsonb)`,
        [crypto.randomUUID(), JSON.stringify({ schemaVersion: 1, jobId: id, enqueuedAt: new Date().toISOString() })]);
    }
    await client.query('COMMIT');
    return { created: Boolean(inserted.rowCount), job: row };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}
