import crypto from 'node:crypto';
import type { Pool } from 'pg';
import { protocolReviewListedSql } from '../services/notificationReadModel.js';

/**
 * 프로토콜 경로 설명 접수(contracts/protocol-explanation-job.md 1절 요청·llm_spec, 10절 접수 검증).
 * - 형식(`parseProtocolExplanationRequest`)은 DB를 읽지 않는다. 워커 `protocol_explanation_job.parse_input`과 같은 키·값 범위다.
 * - 대상·사실(`protocolExplanationProblem`)은 워커 `build_facts`(관측 경로 분기)·`rules_by_interface`와 같은 조건으로 행을 읽어 판정한다.
 *   알림 대상의 근거 판별은 알림 목록과 같은 식(`protocolReviewListedSql`)을 쓴다. 판정 근거 룰 세트 버전의 행·항목은 보지 않는다(워커 방어에서만).
 */
export const PROTOCOL_EXPLANATION_KIND = 'explain_protocol';
export const INVALID_PROTOCOL_EXPLANATION_REQUEST = 'invalid_protocol_explanation_request';
export const PROTOCOL_EXPLANATION_TARGET_NOT_FOUND = 'protocol_explanation_target_not_found';
export const PROTOCOL_EXPLANATION_FACTS_UNAVAILABLE = 'protocol_explanation_facts_unavailable';
/** 1절 llm_spec 고정 값. 워커가 `evidence_version`·`prompt_version`·`output_schema_version`을 대조한다. */
export const PROTOCOL_EXPLANATION_LLM_SPEC = {
  evidence_version: 'protocol-explain-facts-v1',
  model_version: 'qwen-base-6a9e13bd',
  prompt_version: 'protocol-explain-prompt-v5',
  output_schema_version: 'protocol-explanation-v1',
} as const;
/** 워커 INTERFACE_ID_MAX(문자 수 = 코드 포인트 수, Python len과 같음). */
const INTERFACE_ID_MAX = 512;
// 워커보다 엄격한 곳(정상 데이터에서는 차이 없음): UUID는 기존 /api/jobs 정규식(워커 uuid.UUID는 버전·변형 자리를 보지 않음), 초안 version은 숫자 1만(워커는 Python `True == 1`로 JSON true도 받음).
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
/** jsonb에 담을 수 없는 문자(NUL·짝 없는 대리 문자). 저장 오류(503) 대신 형식 오류로 거절한다. */
const NOT_STORABLE = /\u0000|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

export type ProtocolExplanationTarget =
  | { kind: 'observed_paths'; jobId: string; interfaceId: string; ruleSetRevision: number }
  | { kind: 'alert'; decisionId: string };
export type ProtocolExplanationInput = { kind: typeof PROTOCOL_EXPLANATION_KIND; sourceId: string; target: ProtocolExplanationTarget };

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const sameKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const uuid = (value: unknown): value is string => typeof value === 'string' && UUID.test(value);

/** 1절 키 규칙. 위반은 null(호출자가 400 `invalid_protocol_explanation_request`). 값은 받은 그대로, 키 순서만 계약 순서로 고정한다. */
export function parseProtocolExplanationRequest(body: Record<string, unknown>): ProtocolExplanationInput | null {
  if (!sameKeys(body, ['kind', 'sourceId', 'target']) || body.kind !== PROTOCOL_EXPLANATION_KIND || !uuid(body.sourceId)) return null;
  const target = body.target;
  if (!isObject(target)) return null;
  if (target.kind === 'observed_paths') {
    const { jobId, interfaceId, ruleSetRevision } = target;
    if (!sameKeys(target, ['kind', 'jobId', 'interfaceId', 'ruleSetRevision']) || !uuid(jobId)
        || typeof interfaceId !== 'string' || interfaceId === '' || [...interfaceId].length > INTERFACE_ID_MAX || NOT_STORABLE.test(interfaceId)
        || typeof ruleSetRevision !== 'number' || !Number.isSafeInteger(ruleSetRevision) || ruleSetRevision < 0) return null;
    return { kind: PROTOCOL_EXPLANATION_KIND, sourceId: body.sourceId, target: { kind: 'observed_paths', jobId, interfaceId, ruleSetRevision } };
  }
  if (target.kind === 'alert') {
    if (!sameKeys(target, ['kind', 'decisionId']) || !uuid(target.decisionId)) return null;
    return { kind: PROTOCOL_EXPLANATION_KIND, sourceId: body.sourceId, target: { kind: 'alert', decisionId: target.decisionId } };
  }
  return null;
}

/** 작업 `input_decision_id`: 알림 대상은 `target.decisionId`, 관측 경로 대상은 NULL(10절 — 결과 판정 = 작업 판정을 DB가 강제). */
export const protocolExplanationDecisionId = (input: ProtocolExplanationInput) =>
  input.target.kind === 'alert' ? input.target.decisionId : null;

/** 1절 llm_spec. `input_evidence_hash` = request_hash(= input_spec sha256), `reuse_key`는 explain과 같은 꼴. */
export function protocolExplanationLlmSpec(input: ProtocolExplanationInput, requestHash: string, actorId: string) {
  return {
    evidence_version: PROTOCOL_EXPLANATION_LLM_SPEC.evidence_version,
    input_evidence_hash: requestHash,
    model_version: PROTOCOL_EXPLANATION_LLM_SPEC.model_version,
    prompt_version: PROTOCOL_EXPLANATION_LLM_SPEC.prompt_version,
    output_schema_version: PROTOCOL_EXPLANATION_LLM_SPEC.output_schema_version,
    reuse_key: crypto.createHash('sha256')
      .update(JSON.stringify([actorId, input, null, PROTOCOL_EXPLANATION_LLM_SPEC.prompt_version])).digest('hex'),
  };
}

/** 워커 `_same_source`: 결과의 `sourceId` 문자열이 같은 UUID인가(대소문자·하이픈 표기 차이는 같은 값, Python uuid.UUID와 같은 정규화). */
function sameSource(value: unknown, sourceId: string): boolean {
  if (typeof value !== 'string') return false;
  const hex = value.replaceAll('urn:', '').replaceAll('uuid:', '').replace(/^[{}]+|[{}]+$/g, '').replaceAll('-', '');
  return /^[0-9a-f]{32}$/i.test(hex) && hex.toLowerCase() === sourceId.replaceAll('-', '').toLowerCase();
}

/** 워커 `rules_by_interface`에 그 인터페이스가 있는가(목록이 아니면 빈 목록, 객체이고 interfaceId가 문자열인 항목만). */
const registered = (rules: unknown, interfaceId: string) =>
  Array.isArray(rules) && rules.some((item) => isObject(item) && typeof item.interfaceId === 'string' && item.interfaceId === interfaceId);

/**
 * 10절 접수 검증 — 대상·사실. 문제가 있으면 400 코드, 없으면 null. 호출자의 트랜잭션 안에서 읽기만 한다.
 * 관측 경로: (1) 작업 recommend_relations·succeeded·결과 sourceId 일치 (2) 리비전 행 → 아니면 대상 없음,
 *   (3) protocolPathDrafts version 1·interfaces 목록 (4) 초안 interfaces[] 또는 그 리비전 protocolRules[]에 interfaceId(정확 일치) → 아니면 사실 없음.
 * 알림: (1) 판정·관측 sourceId 일치 → 아니면 대상 없음 (2) evidence.protocolReview가 알림 목록 판별 통과 → 아니면 사실 없음.
 */
export async function protocolExplanationProblem(db: Pick<Pool, 'query'>, input: ProtocolExplanationInput): Promise<string | null> {
  const { target } = input;
  if (target.kind === 'alert') {
    const found = await db.query<{ listed: boolean }>(
      `SELECT coalesce(${protocolReviewListedSql(`d.evidence->'protocolReview'`)}, false) AS listed
         FROM run_process_decisions AS d JOIN data_observations AS o ON o.id = d.process_observation_id
        WHERE d.id = $1 AND o.source_id = $2`, [target.decisionId, input.sourceId]);
    if (!found.rowCount) return PROTOCOL_EXPLANATION_TARGET_NOT_FOUND;
    return found.rows[0].listed ? null : PROTOCOL_EXPLANATION_FACTS_UNAVAILABLE;
  }
  const job = await db.query<{ kind: string; status: string; drafts: unknown; source: string | null }>(
    `SELECT kind::text AS kind, status::text AS status, result_summary->'protocolPathDrafts' AS drafts,
            result_summary->>'sourceId' AS source
       FROM run_jobs WHERE id = $1`, [target.jobId]);
  const revision = await db.query<{ rules: unknown }>(
    `SELECT snapshot->'protocolRules' AS rules FROM ops_rule_set_revisions WHERE revision = $1::bigint`, [target.ruleSetRevision]);
  const row = job.rows[0];
  if (!row || row.kind !== 'recommend_relations' || row.status !== 'succeeded' || !sameSource(row.source, input.sourceId)) {
    return PROTOCOL_EXPLANATION_TARGET_NOT_FOUND;
  }
  if (!revision.rowCount) return PROTOCOL_EXPLANATION_TARGET_NOT_FOUND;
  const drafts = row.drafts;
  if (!isObject(drafts) || drafts.version !== 1 || !Array.isArray(drafts.interfaces)) return PROTOCOL_EXPLANATION_FACTS_UNAVAILABLE;
  const observed = drafts.interfaces.some((item) => isObject(item) && item.interfaceId === target.interfaceId);
  if (!observed && !registered(revision.rows[0].rules, target.interfaceId)) return PROTOCOL_EXPLANATION_FACTS_UNAVAILABLE;
  return null;
}
