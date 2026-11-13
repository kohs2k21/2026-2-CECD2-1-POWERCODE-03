import type { Pool } from 'pg';
import { RuleSetError, pathKey } from './ruleSetContract.js';
import { RELATION_CONTRACT_VERSION, latestRelationJobs, relationJobOf, transactionOfRef } from './ruleCandidates.js';

/**
 * 프로토콜 설정 [관측 경로로 초안 만들기] — 가장 최근 성공한 Trainer `recommend_relations` 결과의 `protocolPathDrafts`를 읽는다
 * (protocol-rules.md 9절 Trainer 출력, contracts/protocol-settings-ui.md 2절). 새 표 없음, 결과를 고치지 않는다.
 * - 결과가 없거나 그 결과에 `protocolPathDrafts` 키가 없으면(초안 집계 전 Trainer가 만든 결과) `state: "protocol_drafts_unavailable"` + 사유.
 *   빈 초안·가짜 초안으로 바꾸지 않는다.
 * - 키가 있는데 모양이 계약과 다르면 503 `protocol_drafts_invalid`(해석하지 않음). 초안 버전이 다르면 503 `protocol_drafts_version_unsupported`.
 */
type Queryable = Pick<Pool, 'query'>;
type Json = Record<string, unknown>;
export const PROTOCOL_DRAFT_VERSION = 1;
const STATUSES = ['draft', 'insufficient_evidence'] as const;
const INTERFACE_MAX = 512;

const object = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value);
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const text = (value: unknown): value is string => typeof value === 'string';
const invalid = () => new RuleSetError('protocol_drafts_invalid', 503);

/** 사유별 건수: 키는 결과 `parameters.reasonOrder` 안, 값은 0 이상 정수. 순서는 reasonOrder 순으로 다시 쓴다. */
const reasonCounts = (value: unknown, order: readonly string[]) => {
  if (!object(value) || !Object.entries(value).every(([key, n]) => order.includes(key) && count(n))) throw invalid();
  return Object.fromEntries(order.filter((key) => key in value).map((key) => [key, value[key] as number]));
};

function draftPath(raw: unknown, maxSteps: number) {
  if (!object(raw) || !Array.isArray(raw.channels) || raw.channels.length === 0 || raw.channels.length > maxSteps || !raw.channels.every(text)) throw invalid();
  const channels = raw.channels as string[];
  // pathKey는 쪼개 읽지 않는다. 채널에서 다시 만든 값과 같은지만 본다(정의가 다른 결과를 걸러낸다).
  if (raw.pathKey !== pathKey(channels) || !count(raw.transactions) || !count(raw.fromBranching) || typeof raw.rare !== 'boolean'
    || typeof raw.ratio !== 'number' || !Number.isFinite(raw.ratio) || raw.ratio < 0 || raw.ratio > 1 || !Array.isArray(raw.exampleRefs)) throw invalid();
  const examples = raw.exampleRefs.map((example) => {
    if (!object(example) || !Array.isArray(example.refs) || !example.refs.every(text) || !Array.isArray(example.steps) || !example.steps.every(text)) throw invalid();
    return transactionOfRef(example.refs[0]);
  });
  return {
    channels, pathKey: raw.pathKey as string, transactions: raw.transactions as number, ratio: raw.ratio, rare: raw.rare,
    fromBranching: raw.fromBranching as number,
    /** 예시 거래 id(첫 참조 `transaction:<id>`를 되돌린 값, 형식이 다르면 null). 원천 행은 Trainer가 복사하지 않는다. */
    exampleTransactionIds: examples,
  };
}

function draftInterface(raw: unknown, order: readonly string[], limits: { maxSteps: number; maxPaths: number }) {
  if (!object(raw) || !text(raw.interfaceId) || !(STATUSES as readonly unknown[]).includes(raw.status)) throw invalid();
  const { endpoints, omittedPaths } = raw;
  if (![raw.transactions, raw.pathTransactions, raw.branchingTransactions, raw.distinctPaths].every(count)
    || !object(endpoints) || ![endpoints.compared, endpoints.mismatch, endpoints.notCompared].every(count)
    || !object(omittedPaths) || ![omittedPaths.paths, omittedPaths.transactions].every(count)
    || !Array.isArray(raw.paths) || raw.paths.length > limits.maxPaths) throw invalid();
  const paths = raw.paths.map((path) => draftPath(path, limits.maxSteps));
  if (new Set(paths.map((path) => path.pathKey)).size !== paths.length) throw invalid();
  return {
    interfaceId: raw.interfaceId,
    status: raw.status as (typeof STATUSES)[number],
    transactions: raw.transactions as number,
    pathTransactions: raw.pathTransactions as number,
    branchingTransactions: raw.branchingTransactions as number,
    distinctPaths: raw.distinctPaths as number,
    excludedReasons: reasonCounts(raw.excludedReasons, order),
    endpoints: { compared: endpoints.compared as number, mismatch: endpoints.mismatch as number, notCompared: endpoints.notCompared as number },
    omittedPaths: { paths: omittedPaths.paths as number, transactions: omittedPaths.transactions as number },
    paths,
  };
}

/** `protocolPathDrafts` 전체 검사(Trainer protocol_paths.py `ProtocolPathDrafts.result` 모양). */
export function parseProtocolDrafts(raw: unknown) {
  if (!object(raw)) throw invalid();
  if (raw.version !== PROTOCOL_DRAFT_VERSION) throw new RuleSetError('protocol_drafts_version_unsupported', 503);
  const parameters = raw.parameters;
  if (!object(parameters) || !Array.isArray(parameters.reasonOrder) || !parameters.reasonOrder.every(text)
    || ![parameters.minTransactions, parameters.rarePercent, parameters.maxPathSteps, parameters.maxRoutes, parameters.maxProcessRows, parameters.maxOutputPaths].every(count)
    || !Array.isArray(raw.interfaces) || !count(raw.transactionIdMissingRows) || !count(raw.endpointMismatch)) throw invalid();
  const order = parameters.reasonOrder as string[];
  const limits = { maxSteps: parameters.maxPathSteps as number, maxPaths: parameters.maxOutputPaths as number };
  const interfaces = raw.interfaces.map((item) => draftInterface(item, order, limits));
  if (new Set(interfaces.map((item) => item.interfaceId)).size !== interfaces.length) throw invalid();
  return {
    split: text(raw.split) ? raw.split : null,
    parameters: {
      minTransactions: parameters.minTransactions as number, rarePercent: parameters.rarePercent as number,
      maxPathSteps: limits.maxSteps, maxRoutes: parameters.maxRoutes as number, maxProcessRows: parameters.maxProcessRows as number,
      maxOutputPaths: limits.maxPaths, reasonOrder: order,
    },
    excludedReasons: reasonCounts(raw.excludedReasons, order),
    transactionIdMissingRows: raw.transactionIdMissingRows as number,
    endpointMismatch: raw.endpointMismatch as number,
    interfaces,
  };
}

export function parseProtocolDraftQuery(query: Record<string, unknown>): string | null {
  if (Object.keys(query).some((key) => key !== 'interfaceId')) throw new RuleSetError('unknown_protocol_draft_filter');
  const value = query.interfaceId;
  if (value === undefined) return null;
  if (!text(value) || !value || [...value].length > INTERFACE_MAX) throw new RuleSetError('invalid_interface_id');
  return value;
}

/**
 * GET /api/rule-set/protocol-drafts[?interfaceId=] — 목록은 인터페이스 요약(경로 제외), interfaceId가 있으면 그 인터페이스의 경로까지.
 * 그 인터페이스가 결과에 없으면 `interface: null`(학습 기간에 관측되지 않음 — 오류 아님).
 */
export async function readProtocolDrafts(db: Queryable, interfaceId: string | null) {
  const { latest, succeeded } = await latestRelationJobs(db);
  const latestJob = latest && relationJobOf(latest);
  if (!succeeded) return { state: 'protocol_drafts_unavailable' as const, reason: 'no_result' as const, latestJob };
  const result = succeeded.result;
  if (!object(result)) throw invalid();
  if (result.relationContractVersion !== RELATION_CONTRACT_VERSION) throw new RuleSetError('rule_candidate_contract_unsupported', 503);
  if (!('protocolPathDrafts' in result)) return { state: 'protocol_drafts_unavailable' as const, reason: 'not_in_result' as const, latestJob, job: relationJobOf(succeeded) };
  const drafts = parseProtocolDrafts(result.protocolPathDrafts);
  const period = object(result.period) ? result.period : {};
  const selected = interfaceId === null ? undefined : drafts.interfaces.find((item) => item.interfaceId === interfaceId) ?? null;
  return {
    state: 'available' as const,
    latestJob,
    job: relationJobOf(succeeded),
    snapshotId: text(result.snapshotId) ? result.snapshotId : null,
    sourceId: text(result.sourceId) ? result.sourceId : null,
    period: { from: text(period.from) ? period.from : null, until: text(period.until) ? period.until : null },
    split: drafts.split,
    parameters: drafts.parameters,
    excludedReasons: drafts.excludedReasons,
    transactionIdMissingRows: drafts.transactionIdMissingRows,
    endpointMismatch: drafts.endpointMismatch,
    interfaces: drafts.interfaces.map(({ paths, ...summary }) => ({ ...summary, listedPaths: paths.length })),
    ...(selected === undefined ? {} : { interface: selected }),
  };
}
