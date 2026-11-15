import { createPipelineJob } from "@/services/api/pipeline.api";
import type { ThresholdCandidate } from "./candidates/candidateApi";
import type { DraftSoftRule, RuleDraft } from "./ruleDraft";
import type { RuleSetState, SoftRuleOperator } from "./ruleSetApi";

/** AI 규칙 추천 작업(llm-recommendation-job.md): POST /api/jobs kind=recommend_thresholds → GET /api/jobs/:id resultSummary. */
export type RecommendationStatus = "proposed" | "request_evidence" | "deferred" | "no_applicable" | "rejected";
export type RecommendationItem = {
  field: string;
  softRuleIds: string[];
  /** (10차) 묶음에 든 처리 시간 후보 id. 옛 결과는 키가 없다(= 없음). */
  candidateIds?: string[];
  /** (10차) 묶음의 적용 범위 정규형, null = 필드 전체. 옛 결과는 키가 없다. */
  scope?: Record<string, string> | null;
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
  /** (7차) 임계값 p95 표본 코드: `train_p95`(rule_v2) / `train_reference_80_p95`(rule_v3). 모르는 값은 표시하지 않는다. */
  basis?: string;
  [key: string]: unknown;
};

/** 화면 기준 문구(사용자 11/14 승인). `train_p95`·basis 없음·모르는 값은 기준을 표시하지 않는다. */
export const BASIS_LABELS: Record<string, string> = { train_reference_80_p95: "기준 · 학습 기간 앞 80% p95" };
export const basisLabel = (basis: unknown) => (typeof basis === "string" && Object.hasOwn(BASIS_LABELS, basis) ? BASIS_LABELS[basis] : null);
/**
 * 처리 시간 후보의 Trainer 제안 임계값 기준(llm-recommendation-job.md 10차 결정 1·5: 후보 snapshot train 분할 전체 p95).
 * AI 값(앞 80%)과 나란히 둘 때 기준 표시 없이 두지 않는다. 문구는 AI 기준 문구 계열을 따른 새 문구(사용자 확인 전).
 */
export const CANDIDATE_BASIS = "기준 · 학습 기간 전체 p95";

/**
 * 실행 중 진행률(llm-recommendation-job.md 7차 '진행률' 절): running 동안 `resultSummary.progress{processed,total}`.
 * 재시도 대기·새 임대·실패면 비어 있다. 형식이 맞지 않으면(정수 아님, total ≤ 0, processed 범위 밖) null — 화면은 값 없는 진행 표시를 유지한다.
 */
export type RecommendationProgress = { processed: number; total: number };
export const recommendationProgress = (summary: unknown): RecommendationProgress | null => {
  if (!record(summary) || !record(summary.progress)) return null;
  const { processed, total } = summary.progress;
  if (!Number.isSafeInteger(processed) || !Number.isSafeInteger(total)) return null;
  const [done, all] = [processed as number, total as number];
  return all > 0 && done >= 0 && done <= all ? { processed: done, total: all } : null;
};
/** 진행률 숫자 문구(사용자 11/14 승인): `처리 n/전체`. 진행률이 유효할 때만 쓴다. */
export const progressText = ({ processed, total }: RecommendationProgress) => `처리 ${processed}/${total}`;
/**
 * Rule v3 보류 창 회피로 뺀 행 수(llm-recommendation-job.md 결과 추가 필드 최상위 `heldWindowExclusion`).
 * 객체이고 `excludedRows`가 0 이상 정수일 때만 값, null·없음·형식이 다르면 null(표시하지 않음).
 */
export const heldWindowExcludedRows = (result: unknown): number | null => {
  if (!record(result) || !record(result.heldWindowExclusion)) return null;
  const rows = result.heldWindowExclusion.excludedRows;
  return Number.isSafeInteger(rows) && (rows as number) >= 0 ? (rows as number) : null;
};
export type RecommendationResult = { recommendationContractVersion: string; items: RecommendationItem[]; [key: string]: unknown };

/**
 * (10차) 함께 보낼 처리 시간 후보: 규칙 추천 화면에 지금 보이는 후보 결과(선택한 인터페이스)의 후보 중 `candidate` 상태이고 제안 임계값이 있는 것.
 * 후보 결과의 snapshot으로 계산해야 하므로(접수 409 `rule_candidate_snapshot_mismatch`) snapshot이 있고, 고른 출처와 같은 출처의 결과일 때만 쓴다.
 */
export type CandidateTargets = { snapshotId: string; items: ThresholdCandidate[] };
export const candidateTargets = (
  result: { snapshotId: string | null; sourceId: string | null; thresholdCandidates: ThresholdCandidate[] } | null | undefined, sourceId: string,
): CandidateTargets | null => {
  if (!result?.snapshotId || !result.sourceId || !sourceId || result.sourceId.toLowerCase() !== sourceId.toLowerCase()) return null;
  const items = result.thresholdCandidates.filter((item) => item.status === "candidate" && item.threshold !== null && item.operator !== null);
  return items.length ? { snapshotId: result.snapshotId, items } : null;
};

const RECOMMENDATION_MONTHS = 3;
/**
 * 와이어프레임에 기간 입력이 없어 고정한다: 최근 3개월, 수식 추천과 같은 저장 시각 기준(rules-ui.md).
 * 대상은 저장된 버전의 켜진 규칙 전체(`scope.softRuleIds: null`). (10차) 처리 시간 후보가 있으면 `scope.candidateIds`를 더하고
 * 기간 대신 후보 결과의 snapshot으로 계산한다(같은 요청의 규칙도 그 snapshot). 후보가 없으면 요청은 10차 전과 같다.
 */
export const recommendationRequest = (sourceId: string, revision: number, candidates: CandidateTargets | null = null, now = new Date()) => {
  if (candidates) {
    return {
      kind: "recommend_thresholds", sourceId, ruleSetRevision: revision,
      scope: { softRuleIds: null, candidateIds: candidates.items.map((item) => item.candidateId) },
      data: { snapshotId: candidates.snapshotId, from: null, until: null, timeBasis: null },
      mode: "data_p95_with_llm_rationale",
    };
  }
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
const strings = (value: unknown) => Array.isArray(value) && value.every((id) => typeof id === "string");
const validItem = (value: unknown) => {
  if (!record(value) || !record(value.evidence)) return false;
  const { field, softRuleIds, candidateIds, scope, unit, status, source, operator, threshold, displayThreshold, rationale, rejectedReason, evidence } = value;
  if (typeof field !== "string" || typeof unit !== "string" || !strings(softRuleIds) ||
      !STATUSES.includes(status as string) || !SOURCES.includes(source as string) || !(rationale === null || typeof rationale === "string") ||
      !(rejectedReason === undefined || typeof rejectedReason === "string")) return false;
  // (10차) 추가 키는 없어도 되고(옛 결과), 있으면 형식을 본다.
  if (!(candidateIds === undefined || strings(candidateIds)) ||
      !(scope === undefined || scope === null || (record(scope) && Object.values(scope).every((item) => typeof item === "string")))) return false;
  if (typeof evidence.n !== "number" || !Number.isFinite(evidence.n) || !numberOrNull(evidence.min) || !numberOrNull(evidence.max) || !numberOrNull(evidence.p95)) return false;
  return status === "proposed"
    ? OPERATORS.includes(operator as string) && decimalText(threshold) && decimalText(displayThreshold)
    : operator === null && threshold === null && displayThreshold === null;
};
export const itemCandidates = (item: RecommendationItem) => item.candidateIds ?? [];
/**
 * 작업 결과 검증. 항목 하나라도 계약과 다르면 null(화면은 성공으로 보이지 않게 실패로 표시). 대상 0건은 접수 단계 오류라 빈 결과도 null.
 * 대상은 저장된 켜진 규칙 + 요청한 처리 시간 후보다: `softRuleIds`는 저장 상태 기준 켜진 규칙만, `candidateIds`는 요청한 후보만 있어야 하고
 * 둘 다 빈 항목은 null이다(편집 중 초안과 무관). 후보만 있는 묶음은 `softRuleIds`가 비어 있다(10차).
 */
export const parseRecommendation = (value: unknown, state: RuleSetState, requested: readonly string[] = []): RecommendationResult | null => {
  if (!record(value) || value.recommendationContractVersion !== "threshold-recommendation-v1") return null;
  const { items } = value;
  if (!Array.isArray(items) || items.length === 0 || !items.every(validItem)) return null;
  const enabled = new Set(state.softRules.filter((rule) => rule.enabled).map((rule) => rule.id));
  const candidates = new Set(requested);
  const matched = (items as RecommendationItem[]).every((item) => (item.softRuleIds.length > 0 || itemCandidates(item).length > 0) &&
    item.softRuleIds.every((id) => enabled.has(id)) && itemCandidates(item).every((id) => candidates.has(id)));
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

export type CandidateRow = { candidate: ThresholdCandidate; item: RecommendationItem };
/** (10차) 후보 행 = 요청에 넣은 후보(요청 순서) 중 결과의 `candidateIds`에 든 후보. 같은 범위의 규칙과 한 묶음이면 그 규칙 행과 같은 항목을 본다. */
export const candidateRows = (result: RecommendationResult, candidates: readonly ThresholdCandidate[]): CandidateRow[] =>
  candidates.flatMap((candidate) => {
    const item = result.items.find((entry) => itemCandidates(entry).includes(candidate.candidateId));
    return item ? [{ candidate, item }] : [];
  });
/**
 * (10차 결정 5) 후보 승인 값: AI가 `proposed`면 AI 값(학습 기간 앞 80% p95), 아니면 후보의 Trainer 값(학습 기간 전체 p95 — 지금 후보 승인 흐름과 같음).
 * `ai`가 null이면 승인 흐름이 후보 상세의 저장 초안(`ruleDraft`) 값을 쓴다.
 */
export type CandidateApprovalValue = {
  /** 승인 입력창에 값과 함께 보일 기준. AI 값인데 `basis`를 모르면 null(기준을 지어내지 않음). */
  basis: string | null;
  ai: { threshold: number; operator: SoftRuleOperator; displayThreshold: string } | null;
};
export const candidateApprovalValue = ({ item }: CandidateRow): CandidateApprovalValue =>
  item.status === "proposed" && item.threshold !== null && item.operator !== null && item.displayThreshold !== null
    ? { basis: basisLabel(item.basis), ai: { threshold: Number(item.threshold), operator: item.operator, displayThreshold: item.displayThreshold } }
    : { basis: CANDIDATE_BASIS, ai: null };
