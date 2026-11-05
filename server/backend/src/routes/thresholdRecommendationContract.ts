import crypto from 'node:crypto';
import type { SoftRule } from '../ruleset/ruleSetContract.js';
import { SOFT_RULE, softRuleField } from '../ruleset/methodCatalog.js';

/**
 * 소프트룰 임계값 추천 접수(llm-recommendation-job.md `recommend_thresholds`)의 순수 규칙: 요청 형식, 대상 고정, llm_spec.
 * DB를 읽지 않는다. 호출자가 현재 룰 세트 리비전을 넘긴다.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const SOFT_RULE_ID = new RegExp(SOFT_RULE.instanceIdPattern);
const PERIOD_MS_MAX = 366 * 86400000;
const MODE_DATA_P95 = 'data_p95_with_llm_rationale';
const MODE_AI_PROPOSAL = 'ai_proposal';
const TIME_BASIS_SNAPSHOT = 'persisted_at';
const TIME_BASES: readonly unknown[] = [TIME_BASIS_SNAPSHOT, 'esb_occurred_at'];
/** 워커가 `recommend_rule`과 같은 번들의 rule 프롬프트를 쓴다. API는 번들을 읽지 않아 그 고정 이름을 기록한다. */
export const THRESHOLD_PROMPT_VERSION = 'feature-rule-design-v0.2';

export class ThresholdRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}
const malformed = () => new ThresholdRequestError('invalid_job_request');

export type ThresholdRequest = {
  kind: 'recommend_thresholds'; sourceId: string; ruleSetRevision: number; scope: { softRuleIds: string[] | null };
  data: { snapshotId: string | null; from: string | null; until: string | null; timeBasis: string | null };
  mode: string;
};
export type ThresholdFixedInput = {
  ruleSetContentHash: string; ruleSetContentHashVersion: number;
  targets: { id: string; field: string; threshold: number }[];
};
export type CurrentRuleSet = { revision: number; contentHash: string; contentHashVersion: number; softRules: SoftRule[] };

const plainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const keysWithin = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every((key) => keys.includes(key));
/** Date.parse는 2월 30일을 3월로 넘긴다. 그런 값은 워커의 timestamptz 변환에서 터지므로 접수에서 막는다. */
const instant = (value: unknown): value is string =>
  typeof value === 'string' && UTC_TIME.test(value) && Number.isFinite(Date.parse(value)) &&
  new Date(value).toISOString().slice(0, 10) === value.slice(0, 10);
/** 기존 작업 종류와 같은 기간 규칙: UTC 표기, from < until, 366일 이내. */
const validPeriod = (from: unknown, until: unknown) =>
  instant(from) && instant(until) && Date.parse(from) < Date.parse(until) && Date.parse(until) - Date.parse(from) <= PERIOD_MS_MAX;

function parseScope(value: unknown): ThresholdRequest['scope'] {
  if (!plainObject(value) || !('softRuleIds' in value) || !keysWithin(value, ['softRuleIds'])) throw malformed();
  const { softRuleIds } = value;
  if (softRuleIds === null) return { softRuleIds: null };
  if (!Array.isArray(softRuleIds) || softRuleIds.length > SOFT_RULE.maxRules || new Set(softRuleIds).size !== softRuleIds.length ||
      !softRuleIds.every((id) => typeof id === 'string' && SOFT_RULE_ID.test(id))) throw malformed();
  return { softRuleIds };
}

function parseData(value: unknown): ThresholdRequest['data'] {
  if (!plainObject(value) || !keysWithin(value, ['snapshotId', 'from', 'until', 'timeBasis'])) throw malformed();
  const { snapshotId = null, from = null, until = null, timeBasis = null } = value;
  if (timeBasis !== null && !TIME_BASES.includes(timeBasis)) throw malformed();
  if (snapshotId !== null && (typeof snapshotId !== 'string' || !UUID.test(snapshotId))) throw new ThresholdRequestError('invalid_snapshot_reference');
  // snapshot이 있으면 기간은 없어도 되고, 함께 보내면 그 snapshot의 기간과 일치해야 한다(접수 시 확인).
  const periodOmitted = snapshotId !== null && from === null && until === null;
  if (!periodOmitted && !validPeriod(from, until)) throw new ThresholdRequestError('invalid_time_range');
  // 기간으로 만드는 snapshot은 persisted_at만 지원한다. 다른 기준을 조용히 바꿔 계산하지 않는다.
  if (snapshotId === null && timeBasis !== null && timeBasis !== TIME_BASIS_SNAPSHOT) {
    throw new ThresholdRequestError('recommendation_time_basis_unsupported', 422);
  }
  return {
    snapshotId: snapshotId as string | null, from: from as string | null, until: until as string | null,
    timeBasis: snapshotId === null ? TIME_BASIS_SNAPSHOT : timeBasis as string | null,
  };
}

/** `kind`·`sourceId`를 검사한 본문을 받는다. 형식 위반 400, 예약 모드 422. */
export function parseThresholdRequest(body: Record<string, unknown>, sourceId: string): ThresholdRequest {
  if (!keysWithin(body, ['kind', 'sourceId', 'ruleSetRevision', 'scope', 'data', 'mode'])) throw malformed();
  const { ruleSetRevision, mode = null } = body;
  if (!Number.isSafeInteger(ruleSetRevision) || (ruleSetRevision as number) < 0) throw malformed();
  if (mode !== null && mode !== MODE_DATA_P95 && mode !== MODE_AI_PROPOSAL) throw malformed();
  const scope = parseScope(body.scope);
  const data = parseData(body.data);
  if (mode === MODE_AI_PROPOSAL) throw new ThresholdRequestError('recommendation_mode_not_available', 422);
  return { kind: 'recommend_thresholds', sourceId, ruleSetRevision: ruleSetRevision as number, scope, data, mode: MODE_DATA_P95 };
}

/**
 * 요청 리비전을 현재 리비전과 대조하고 그 리비전의 켜진 소프트룰을 대상으로 고정한다.
 * hash와 버전은 리비전 행의 값 그대로다(재계산 없음). 없는 리비전도 현재와 다르므로 같은 409다.
 */
export function fixThresholdTargets(request: ThresholdRequest, current: CurrentRuleSet): ThresholdFixedInput {
  if (request.ruleSetRevision !== current.revision) throw new ThresholdRequestError('rule_set_revision_conflict', 409);
  const selected = request.scope.softRuleIds;
  const rules = current.softRules.filter((rule) => rule.enabled && (selected === null || selected.includes(rule.id)));
  if (!rules.length) throw new ThresholdRequestError('soft_rules_empty');
  const unknown = rules.find((rule) => !softRuleField(rule.field));
  if (unknown) throw new ThresholdRequestError(`soft_rule_field_unknown:${unknown.field}`, 422);
  return {
    ruleSetContentHash: current.contentHash, ruleSetContentHashVersion: current.contentHashVersion,
    targets: rules.map(({ id, field, threshold }) => ({ id, field, threshold })),
  };
}

/**
 * `input`은 대상까지 고정한 `input_spec` 전체다. 그래서 리비전·hash·기간·대상이 하나라도 다르면 `reuse_key`가 달라진다.
 * 멱등키도 들어 있어 재사용은 같은 실행의 재전송에만 일어난다(데이터는 시간이 지나면 바뀌므로 새 실행은 새 작업이다).
 */
export const thresholdLlmSpec = (input: ThresholdRequest & ThresholdFixedInput, requestHash: string, actorId: string, idempotencyKey: string) => ({
  evidence_version: 'soft-rule-threshold-v1', input_evidence_hash: requestHash, model_version: 'rule_v2',
  prompt_version: THRESHOLD_PROMPT_VERSION, output_schema_version: 'threshold-recommendation-v1',
  reuse_key: crypto.createHash('sha256').update(JSON.stringify([actorId, input, idempotencyKey, THRESHOLD_PROMPT_VERSION])).digest('hex'),
});
