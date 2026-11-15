import crypto from 'node:crypto';
import { canonicalSoftScope, type SoftRule } from '../ruleset/ruleSetContract.js';
import { SOFT_RULE, softRuleField } from '../ruleset/methodCatalog.js';
import { CANDIDATE_ID } from '../ruleset/ruleCandidates.js';

/**
 * 소프트룰 임계값 추천 접수(llm-recommendation-job.md `recommend_thresholds`)의 순수 규칙: 요청 형식, 대상 고정, llm_spec.
 * DB를 읽지 않는다. 호출자가 현재 룰 세트 리비전과 접수 때 확인한 처리 시간 후보를 넘긴다.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const SOFT_RULE_ID = new RegExp(SOFT_RULE.instanceIdPattern);
const PERIOD_MS_MAX = 366 * 86400000;
const MODE_DATA_P95 = 'data_p95_with_llm_rationale';
const MODE_AI_PROPOSAL = 'ai_proposal';
const TIME_BASIS_SNAPSHOT = 'persisted_at';
const TIME_BASES: readonly unknown[] = [TIME_BASIS_SNAPSHOT, 'esb_occurred_at'];
/** (10차) 상한: 후보 id 1~32개, 결과 묶음(필드 × 적용 범위) ≤ 32. 대상은 소프트룰 32 + 후보 32라 64를 넘지 않는다. */
const CANDIDATES_MAX = 32;
const GROUPS_MAX = 32;
/**
 * Rule v3 경로(llm-recommendation-job.md 7차 'Rule v3 경로' 절). 워커는 `(model_version, evidence_version)`으로 경로를 고르고
 * rule_v2 조합도 그대로 받으므로 이 세 값만 바꾸면 전환·되돌림이 된다. API는 번들을 읽지 않아 프롬프트는 고정 이름을 기록한다.
 * (10차) `targets[]`를 새 모양 `{kind, id, field, scope, threshold}`로 기록하므로 evidence_version은 v3다 — 새 모양을 모르는 옛 워커는
 * 조용히 틀리지 않고 `recommendation_llm_spec_invalid`로 실패한다. rule_v2로 되돌리면 짝은 `soft-rule-threshold-v1`이다.
 */
export const THRESHOLD_MODEL_VERSION = 'rule_v3';
export const THRESHOLD_EVIDENCE_VERSION = 'soft-rule-threshold-v3';
export const THRESHOLD_PROMPT_VERSION = 'feature-rule-spec-rule-v3-ko';

export class ThresholdRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}
const malformed = () => new ThresholdRequestError('invalid_job_request');

export type ThresholdRequest = {
  kind: 'recommend_thresholds'; sourceId: string; ruleSetRevision: number;
  /** 대상 선택. `candidateIds` 생략·null = 후보 없음(10차 전과 같음). input_spec에는 늘 두 키가 함께 있다. */
  scope: { softRuleIds: string[] | null; candidateIds: string[] | null };
  data: { snapshotId: string | null; from: string | null; until: string | null; timeBasis: string | null };
  mode: string;
};
/** 적용 범위 정규형(판정 키 중 값 있는 키만) 또는 null(필드 전체). 요청 `scope`(대상 선택)와 이름만 같고 뜻이 다르다. */
export type TargetScope = Record<string, string> | null;
export type ThresholdTarget = { kind: 'soft_rule' | 'candidate'; id: string; field: string; scope: TargetScope; threshold: number | null };
export type ThresholdFixedInput = {
  ruleSetContentHash: string; ruleSetContentHashVersion: number;
  targets: ThresholdTarget[];
};
/** 접수 때 확인한 처리 시간 후보(ruleCandidates.readRecommendationCandidates). 값은 워커가 다시 계산하므로 id·필드·범위만 쓴다. */
export type CandidateTarget = { candidateId: string; field: string; scope: { processId: string; adapterType?: string } };
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
/** 처리 시간 후보 id `<작업 id>.t<번호>`만. 관계 후보 `.r<번호>`는 대상이 아니다(400). */
const thresholdCandidateId = (id: unknown): id is string => typeof id === 'string' && CANDIDATE_ID.exec(id)?.[2].toLowerCase() === 't';

function parseScope(value: unknown): ThresholdRequest['scope'] {
  if (!plainObject(value) || !('softRuleIds' in value) || !keysWithin(value, ['softRuleIds', 'candidateIds'])) throw malformed();
  const { softRuleIds, candidateIds = null } = value;
  if (softRuleIds !== null && (!Array.isArray(softRuleIds) || softRuleIds.length > SOFT_RULE.maxRules || new Set(softRuleIds).size !== softRuleIds.length ||
      !softRuleIds.every((id) => typeof id === 'string' && SOFT_RULE_ID.test(id)))) throw malformed();
  // (10차) 후보는 선택 키. 배열이면 1~32개, 중복 없음(대소문자 무시 — 같은 후보를 두 번 세지 않게).
  if (candidateIds !== null && (!Array.isArray(candidateIds) || !candidateIds.length || candidateIds.length > CANDIDATES_MAX ||
      !candidateIds.every(thresholdCandidateId) || new Set(candidateIds.map((id) => id.toLowerCase())).size !== candidateIds.length)) throw malformed();
  return { softRuleIds: softRuleIds as string[] | null, candidateIds: candidateIds as string[] | null };
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

/** 요청 리비전 = 현재 리비전. 없는 리비전도 현재와 다르므로 같은 409다. 후보 조회보다 먼저 본다. */
export function checkThresholdRevision(request: ThresholdRequest, current: CurrentRuleSet): void {
  if (request.ruleSetRevision !== current.revision) throw new ThresholdRequestError('rule_set_revision_conflict', 409);
}

/**
 * (10차) 후보가 있으면 `data.snapshotId` = 후보 결과 `snapshotId`여야 한다(같은 요청의 소프트룰도 이 snapshot으로 계산). 기간만 보낸 요청도 409.
 * snapshot id는 UUID라 대소문자를 구분하지 않는다.
 */
export function checkCandidateSnapshot(request: ThresholdRequest, candidateSnapshotId: string | null): void {
  const requested = request.data.snapshotId;
  if (!requested || !candidateSnapshotId || requested.toLowerCase() !== candidateSnapshotId.toLowerCase()) {
    throw new ThresholdRequestError('rule_candidate_snapshot_mismatch', 409);
  }
}

/** 범위 정규형: 판정 키(processId·adapterType·processHubId) 중 값 있는 키만, 이 순서. 표시용 interfaceId는 뺀다. 없으면 null. */
const targetScope = (scope: unknown): TargetScope => canonicalSoftScope(scope) as TargetScope;
const groupKey = ({ field, scope }: Pick<ThresholdTarget, 'field' | 'scope'>) => JSON.stringify([field, scope]);

/**
 * 그 리비전의 켜진 소프트룰과 접수 때 확인한 후보를 대상으로 고정한다(llm-recommendation-job.md 10차 `input_spec.targets[]` 표).
 * 순서: 소프트룰(저장 순서) → 후보(요청 순서). 소프트룰 `threshold`는 현재 값, 후보는 null(워커가 다시 계산).
 * hash와 버전은 리비전 행의 값 그대로다(재계산 없음). 묶음 = 같은 (필드, 범위 정규형) — 결과 항목 1건이고 32개까지(자르지 않고 400).
 * `modelVersion`이 rule_v3가 아니면(rule_v2 되돌림) 후보·범위 있는 대상은 400 — 되돌린 경로가 필드 전체 p95로 조용히 틀리지 않게.
 */
export function fixThresholdTargets(request: ThresholdRequest, current: CurrentRuleSet, candidates: CandidateTarget[] = [],
  modelVersion: string = THRESHOLD_MODEL_VERSION): ThresholdFixedInput {
  checkThresholdRevision(request, current);
  const selected = request.scope.softRuleIds;
  const rules = current.softRules.filter((rule) => rule.enabled && (selected === null || selected.includes(rule.id)));
  if (!rules.length && !candidates.length) throw new ThresholdRequestError('soft_rules_empty');
  const unknown = rules.find((rule) => !softRuleField(rule.field));
  if (unknown) throw new ThresholdRequestError(`soft_rule_field_unknown:${unknown.field}`, 422);
  const targets: ThresholdTarget[] = [
    ...rules.map(({ id, field, scope, threshold }): ThresholdTarget => ({ kind: 'soft_rule', id, field, scope: targetScope(scope), threshold })),
    ...candidates.map(({ candidateId, field, scope }): ThresholdTarget => ({ kind: 'candidate', id: candidateId, field, scope: targetScope(scope), threshold: null })),
  ];
  if (modelVersion !== 'rule_v3' && targets.some((target) => target.kind === 'candidate' || target.scope !== null)) {
    throw new ThresholdRequestError('recommendation_targets_require_rule_v3');
  }
  if (new Set(targets.map(groupKey)).size > GROUPS_MAX) throw new ThresholdRequestError('soft_rule_limit_exceeded');
  return { ruleSetContentHash: current.contentHash, ruleSetContentHashVersion: current.contentHashVersion, targets };
}

/**
 * `input`은 대상까지 고정한 `input_spec` 전체다. 그래서 리비전·hash·기간·대상이 하나라도 다르면 `reuse_key`가 달라진다.
 * 멱등키도 들어 있어 재사용은 같은 실행의 재전송에만 일어난다(데이터는 시간이 지나면 바뀌므로 새 실행은 새 작업이다).
 */
export const thresholdLlmSpec = (input: ThresholdRequest & ThresholdFixedInput, requestHash: string, actorId: string, idempotencyKey: string) => ({
  evidence_version: THRESHOLD_EVIDENCE_VERSION, input_evidence_hash: requestHash, model_version: THRESHOLD_MODEL_VERSION,
  prompt_version: THRESHOLD_PROMPT_VERSION, output_schema_version: 'threshold-recommendation-v1',
  reuse_key: crypto.createHash('sha256').update(JSON.stringify([actorId, input, idempotencyKey, THRESHOLD_PROMPT_VERSION])).digest('hex'),
});
