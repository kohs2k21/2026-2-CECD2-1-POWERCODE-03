import { HttpError } from "@/services/api/client";
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
/** 작업 결과 형식 확인. 항목 하나라도 계약과 다르면 null(화면은 성공으로 보이지 않게 실패로 표시). 대상 0건은 접수 단계 오류라 빈 결과도 null. */
export const parseRecommendation = (value: unknown): RecommendationResult | null => {
  if (!record(value) || value.recommendationContractVersion !== "threshold-recommendation-v1") return null;
  const { items } = value;
  return Array.isArray(items) && items.length > 0 && items.every(validItem) ? (value as RecommendationResult) : null;
};

/**
 * 개발 모드에서 서버가 이 작업 종류를 아직 거절할 때만 계약 예시를 돌려준다. 운영 빌드에서는 분기와 예시 파일이 사라지고 오류가 그대로 표시된다.
 * 서버가 recommend_thresholds를 받으면 이 함수와 ruleAiDevSample.ts를 지운다.
 */
const devSampleAfter = async (error: unknown, state: RuleSetState) => {
  if (import.meta.env?.DEV && error instanceof HttpError && error.code === "invalid_job_request") {
    // 예시의 규칙 id는 실제 규칙과 달라 같은 필드의 켜진 규칙 id로 맞춘다. 운영 경로에는 필드 대체가 없다.
    return (await import("./ruleAiDevSample")).devRecommendationFor(state);
  }
  return null;
};

export const startRecommendation = async (body: ReturnType<typeof recommendationRequest>, idempotencyKey: string, state: RuleSetState): Promise<{ jobId: string } | { result: RecommendationResult }> => {
  try {
    return { jobId: (await createPipelineJob(body, idempotencyKey)).id };
  } catch (error) {
    const result = await devSampleAfter(error, state);
    if (result) return { result };
    throw error;
  }
};

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
