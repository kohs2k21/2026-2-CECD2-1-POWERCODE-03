import type { RelationRuleRequest, RuleSetRequest, RuleSetState, SoftRuleRequest } from "../ruleSetApi";

/**
 * 규칙 추천 승인(rule-candidates-ui.md 7절): 현재 룰 세트 전체 + 새 규칙 1개를 저장 요청으로. "승인됨" 판단은 현재 룰 세트에 같은 조건이 있는지
 * (relation-rules.md 2절 같은 규칙의 기준 = rule-set.md v3.3 정규 객체의 kind·scope·from·to·match, 소프트룰은 field·scope — 표시용 interfaceId 제외).
 */
type Json = Record<string, unknown>;
const object = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** rule-set.md v3.3: required_step은 {interfaceId}, 그 밖은 adapterType만(없으면 null). */
const relationScopeKey = (kind: unknown, scope: unknown) => {
  if (!object(scope)) return null;
  if (kind === "required_step") return { interfaceId: scope.interfaceId };
  return scope.adapterType === undefined ? null : { adapterType: scope.adapterType };
};
const processOf = (value: unknown) => (object(value) ? value.processId ?? null : null);
const matchOf = (value: unknown) => (object(value) ? [value.from, value.to, value.key] : null);
const relationKey = (rule: Json) =>
  JSON.stringify([rule.kind, relationScopeKey(rule.kind, rule.scope), processOf(rule.from), processOf(rule.to), matchOf(rule.match)]);
/** 소프트룰 scope 판정 키(processId·adapterType·processHubId)만, 없으면 null. */
const softScopeKey = (scope: unknown) => {
  if (!object(scope)) return null;
  const kept = ["processId", "adapterType", "processHubId"].filter((key) => scope[key] !== undefined).map((key) => [key, scope[key]]);
  return kept.length ? kept : null;
};

export const approvedRelation = (state: RuleSetState, draft: Json) =>
  (state.relationRules ?? []).some((rule) => relationKey(rule as unknown as Json) === relationKey(draft));
export const approvedThreshold = (state: RuleSetState, draft: Json) =>
  state.softRules.some((rule) => rule.field === draft.field && JSON.stringify(softScopeKey(rule.scope)) === JSON.stringify(softScopeKey(draft.scope)));

/** 저장 요청에 새 규칙을 더한다. 관계 규칙 source는 Trainer 출처(서버가 최초 값 유지, hash 입력 아님) — 없으면 보내지 않는다. */
export const approvalRequest = (request: RuleSetRequest, add: { relation?: RelationRuleRequest; soft?: SoftRuleRequest }): RuleSetRequest => {
  const relation = add.relation && (({ source, ...rest }) => (source === null || source === undefined ? rest : { ...rest, source }))(add.relation);
  return {
    ...request,
    relationRules: relation ? [...request.relationRules, relation] : request.relationRules,
    softRules: add.soft ? [...request.softRules, add.soft] : request.softRules,
  };
};
