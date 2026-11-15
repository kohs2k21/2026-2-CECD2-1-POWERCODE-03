import type { Pool } from 'pg';
import { parseProtocolDrafts } from './protocolDrafts.js';
import {
  RELATION_CONTRACT_VERSION, latestRelationJobParts, relationItem, relationJobOf, relationRuleDraft, thresholdItem,
} from './ruleCandidates.js';
import { RuleSetError, canonicalJson, canonicalSoftScope, relationConditionKey } from './ruleSetContract.js';
import { readCurrentRules } from './ruleSetStore.js';

/**
 * GET /api/rule-set/candidates/summary — 룰 설정 카드·프로토콜 설정 목록·규칙 추천 탭의 "추천 n건" 건수(rule-candidates-ui.md 8절).
 * 가장 최근 성공한 후보 작업 결과 중 아직 현재 룰 세트에 없는 것만 센다(사용자 11/15 결정 1). 결과 전체(그래프 등)는 읽지 않고
 * 후보 배열·관측 경로 초안만 꺼내며, 항목 해석·503 코드는 목록·초안과 같은 함수(relationItem·thresholdItem·parseProtocolDrafts)를 쓴다.
 * - 이상 징후: 처리 시간 후보 중 `status: candidate`이고 같은 `field` + scope 판정 키(rule-candidates-ui.md 7절)의 소프트룰이 없는 것
 * - 관계 규칙: 승인 가능(`approvable`)이고 같은 조건(rule-set.md v3.3 정규 조건, 서버 `relation_rule_condition_duplicate` 기준)의 관계 규칙이 없는 것
 * - 프로토콜: 관측 경로 초안 인터페이스 중 근거 부족이 아니고(`status: draft`) 아직 프로토콜 항목으로 등록되지 않은 것(꺼진 항목도 등록으로 본다)
 * 결과 없음·초안 키 없음(옛 결과)은 0. 결과 모양이 다르면 0으로 바꾸지 않고 목록과 같은 503.
 */
type Queryable = Pick<Pool, 'query'>;

export async function readCandidateSummary(db: Queryable) {
  const [{ latest, succeeded }, rules] = await Promise.all([latestRelationJobParts(db), readCurrentRules(db)]);
  const head = { latestJob: latest && relationJobOf(latest), ruleSetRevision: rules.revision };
  if (!succeeded) return { ...head, job: null, counts: { anomaly: 0, relation: 0, protocol: 0 } };
  if (succeeded.resultType !== 'object') throw new RuleSetError('rule_candidate_result_invalid', 503);
  if (succeeded.contractVersion !== RELATION_CONTRACT_VERSION) throw new RuleSetError('rule_candidate_contract_unsupported', 503);
  const { candidates, thresholdCandidates } = succeeded;
  if (!Array.isArray(candidates) || !Array.isArray(thresholdCandidates)) throw new RuleSetError('rule_candidate_result_invalid', 503);
  const relations = candidates.map((raw, index) => relationItem(succeeded.id, index, raw));
  const thresholds = thresholdCandidates.map((raw, index) => thresholdItem(succeeded.id, index, raw));
  const softKey = (field: string, scope: unknown) => canonicalJson([field, canonicalSoftScope(scope)]);
  const softKeys = new Set(rules.softRules.map((rule) => softKey(rule.field, rule.scope)));
  const relationKeys = new Set(rules.relationRules.map(relationConditionKey));
  const registered = new Set(rules.protocolRules.map((rule) => rule.interfaceId));
  const drafts = succeeded.hasProtocolDrafts ? parseProtocolDrafts(succeeded.protocolPathDrafts).interfaces : [];
  return {
    ...head,
    job: relationJobOf(succeeded),
    counts: {
      anomaly: thresholds.filter((item) => item.status === 'candidate' && !softKeys.has(softKey(item.field, item.scope))).length,
      relation: relations.filter((item) => item.approvable && !relationKeys.has(relationConditionKey(relationRuleDraft(item, null)))).length,
      protocol: drafts.filter((item) => item.status === 'draft' && !registered.has(item.interfaceId)).length,
    },
  };
}
