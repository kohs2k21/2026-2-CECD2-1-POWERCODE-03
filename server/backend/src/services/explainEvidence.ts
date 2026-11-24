/**
 * 설명 작업(`explain`) 접수 입력의 저장 판정 근거(`savedEvidence`) 조립. 판정 행 값을 옮기기만 하고 다시 계산하지 않는다.
 * - 기존 키(순서 고정): decisionId·configVersionId·status·ruleStatus·mlStatus·decisionCode·anomalyScore·inputManifestHash·rules·models
 * - `methodResults`(추가): 판정 근거 `methodResults` 중 `category === "ml"`이고 `attribution` 키가 있는 항목만
 *   `{methodId, category, score, attribution}`(저장값 그대로, 항목에 없는 `methodId`·`score` 키는 넣지 않음).
 *   그런 항목이 없으면 키 자체를 넣지 않는다 — 기여도가 없는 판정의 `savedEvidenceJson`·입력 hash·재사용 키는 이전과 바이트 단위로 같다.
 *   `methodResults`가 배열이 아니거나 항목이 객체가 아니면 그 항목은 건너뛴다(접수를 막지 않음)
 */
export type SavedDecisionRow = {
  id: string;
  config_version_id: string | null;
  status: unknown;
  rule_status: unknown;
  ml_status: unknown;
  decision_code: unknown;
  anomaly_score: unknown;
  evidence: any;
  input_manifest_hash: unknown;
};

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

/** 기여도가 있는 ML 방법 결과(설명 입력용 네 키). 없으면 []. */
export function attributionMethodResults(evidence: unknown): Record<string, unknown>[] {
  const methods = isObject(evidence) ? evidence.methodResults : undefined;
  if (!Array.isArray(methods)) return [];
  return methods.flatMap((method) => {
    if (!isObject(method) || method.category !== 'ml' || !Object.prototype.hasOwnProperty.call(method, 'attribution')) return [];
    const item: Record<string, unknown> = {};
    if (Object.prototype.hasOwnProperty.call(method, 'methodId')) item.methodId = method.methodId;
    item.category = method.category;
    if (Object.prototype.hasOwnProperty.call(method, 'score')) item.score = method.score;
    item.attribution = method.attribution;
    return [item];
  });
}

export function buildSavedEvidence(decision: SavedDecisionRow): Record<string, unknown> {
  const saved: Record<string, unknown> = {decisionId: decision.id, configVersionId: decision.config_version_id,
    status: decision.status, ruleStatus: decision.rule_status, mlStatus: decision.ml_status,
    decisionCode: decision.decision_code, anomalyScore: decision.anomaly_score === null ? null : String(decision.anomaly_score),
    inputManifestHash: decision.input_manifest_hash,
    rules: decision.evidence.rules ?? [], models: decision.evidence.models ?? []};
  const methodResults = attributionMethodResults(decision.evidence);
  if (methodResults.length) saved.methodResults = methodResults;
  return saved;
}
