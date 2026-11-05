import { createPipelineJob } from "@/services/api/pipeline.api";
import type { DraftSoftRule, RuleDraft } from "./ruleDraft";
import type { RuleSetState, SoftRuleOperator } from "./ruleSetApi";

/** AI 규칙 추천 작업(llm-recommendation-job.md): POST /api/jobs kind=recommend_thresholds → GET /api/jobs/:id resultSummary. */
export type RecommendationStatus = "proposed" | "request_evidence" | "deferred" | "no_applicable" | "rejected";
export type RecommendationItem = {
  field: string;
  softRuleIds: string[];
  currentThreshold: number | null;
  operator: SoftRuleOperator | null;
  threshold: string | null;
  displayThreshold: string | null;
  unit: string;
  source: "data_p95" | "ai_proposal";
  status: RecommendationStatus;
  rejectedReason?: string;
  evidence: { n: number; min: number | null; max: number | null; p95: number | null; [key: string]: unknown };
  rationale: string | null;
  [key: string]: unknown;
};
export type RecommendationResult = { recommendationContractVersion: string; items: RecommendationItem[]; [key: string]: unknown };

const RECOMMENDATION_MONTHS = 3;
/**
 * 와이어프레임에 기간 입력이 없어 고정한다: 최근 3개월, 수식 추천과 같은 저장 시각 기준(rules-ui.md).
 * 대상은 저장된 버전의 켜진 규칙 전체(`scope.softRuleIds: null`).
 */
export const recommendationRequest = (sourceId: string, revision: number, now = new Date()) => {
  const from = new Date(now);
  from.setMonth(from.getMonth() - RECOMMENDATION_MONTHS);
  return {
    kind: "recommend_thresholds", sourceId, ruleSetRevision: revision, scope: { softRuleIds: null },
    data: { snapshotId: null, from: from.toISOString(), until: now.toISOString(), timeBasis: "persisted_at" },
    mode: "data_p95_with_llm_rationale",
  };
};

const STATUSES: readonly string[] = ["proposed", "request_evidence", "deferred", "no_applicable", "rejected"];
const SOURCES: readonly string[] = ["data_p95", "ai_proposal"];
const OPERATORS: readonly string[] = ["gte", "gt", "lte", "lt"];
const DECIMAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const numberOrNull = (value: unknown) => value === null || (typeof value === "number" && Number.isFinite(value));
const decimalText = (value: unknown) => typeof value === "string" && DECIMAL.test(value);
const validItem = (value: unknown) => {
  if (!record(value) || !record(value.evidence)) return false;
  const { field, softRuleIds, unit, status, source, operator, threshold, displayThreshold, rationale, rejectedReason, evidence } = value;
  if (typeof field !== "string" || typeof unit !== "string" || !Array.isArray(softRuleIds) || !softRuleIds.every((id) => typeof id === "string") ||
      !STATUSES.includes(status as string) || !SOURCES.includes(source as string) || !(rationale === null || typeof rationale === "string") ||
      !(rejectedReason === undefined || typeof rejectedReason === "string")) return false;
  if (typeof evidence.n !== "number" || !Number.isFinite(evidence.n) || !numberOrNull(evidence.min) || !numberOrNull(evidence.max) || !numberOrNull(evidence.p95)) return false;
  return status === "proposed"
    ? OPERATORS.includes(operator as string) && decimalText(threshold) && decimalText(displayThreshold)
    : operator === null && threshold === null && displayThreshold === null;
};
/**
 * 작업 결과 검증. 항목 하나라도 계약과 다르면 null(화면은 성공으로 보이지 않게 실패로 표시). 대상 0건은 접수 단계 오류라 빈 결과도 null.
 * 대상은 저장된 켜진 규칙이므로 `softRuleIds`가 비었거나 그 밖의 id가 있으면 저장 상태 기준으로 null이다(편집 중 초안과 무관).
 */
export const parseRecommendation = (value: unknown, state: RuleSetState): RecommendationResult | null => {
  if (!record(value) || value.recommendationContractVersion !== "threshold-recommendation-v1") return null;
  const { items } = value;
  if (!Array.isArray(items) || items.length === 0 || !items.every(validItem)) return null;
  const enabled = new Set(state.softRules.filter((rule) => rule.enabled).map((rule) => rule.id));
  const matched = (items as RecommendationItem[]).every((item) => item.softRuleIds.length > 0 && item.softRuleIds.every((id) => enabled.has(id)));
  return matched ? (value as RecommendationResult) : null;
};

export const startRecommendation = async (body: ReturnType<typeof recommendationRequest>, idempotencyKey: string): Promise<string> =>
  (await createPipelineJob(body, idempotencyKey)).id;

export type RecommendationRow = { rule: DraftSoftRule; item: RecommendationItem };
/**
 * 표 행 = 저장된 켜진 규칙 중 결과의 `softRuleIds`에 든 규칙(초안에서 지운 규칙 제외). 같은 필드라도 목록에 없는 규칙에는 값이 들어가지 않는다.
 */
export const recommendationRows = (result: RecommendationResult, state: RuleSetState, draft: RuleDraft): RecommendationRow[] =>
  state.softRules.filter((rule) => rule.enabled).flatMap((saved) => {
    const rule = draft.soft.find((item) => item.id === saved.id);
    const item = result.items.find((entry) => entry.softRuleIds.includes(saved.id));
    return rule && item ? [{ rule, item }] : [];
  });
export const selectable = (row: RecommendationRow) => row.item.status === "proposed";
