import { httpClient } from "@/services/api/client";
import type { EvaluationItem } from "@/services/api/pipeline.api";
import { readCapabilities, readVersionPage } from "@/features/detection/models/modelsApi";

/**
 * 평가 결과 화면의 데이터 접근 한곳(config-comparison-evaluation.md v1, evaluations-ui.md). 서버 API에 연결되어 있고 예시 데이터는 쓰지 않는다.
 * 실패는 그대로 오류로 둔다(빈 성공으로 바꾸지 않음).
 */
export type SyntheticSpec = {
  profileId: string;
  profileVersion: string;
  scenarios: { scenario: string; severity: number }[];
  seed: number;
  repetitions: number;
  targetRows: number;
};
export type EvaluationSet = {
  id: string;
  sourceId: string;
  snapshotId: string;
  purpose: "validation" | "test";
  displayName: string;
  status: "queued" | "ready" | "failed";
  /** 합성 없는 세트(원본 분할만)는 null. v1은 항상 null이다. */
  synthetic: SyntheticSpec | null;
  /** 생성 전·실패는 null. */
  generation: { rowCount: number; originalRowCount: number; syntheticRowCount: number; truthCount: number; contentHash: string; artifactRef: string | null } | null;
  createdAt: string;
  readyAt: string | null;
  /** 상세에만 있다. */
  errorCode?: string | null;
};

export type ComparisonStatus = "queued" | "running" | "succeeded" | "failed";
export type RecommendationState = "current" | "applied" | "stale" | "none";
export type ComparisonListItem = {
  /** 비교 묶음은 완료 때 생긴다. 완료 전은 null이고 화면은 작업 ID로 연다. */
  comparisonId: string | null;
  jobId: string;
  evaluationSetId: string;
  baselines: { modelKey: string; configVersionId: string }[];
  activeModelSetRevision: number;
  status: ComparisonStatus;
  errorCode?: string | null;
  itemCount: number;
  succeededCount: number;
  failedCount: number;
  completedAt: string | null;
  /** 교체 추천. 없으면 null이고 완료 뒤에는 `recommendationsReason`(v1 `not_implemented_v1`)이 있다. */
  recommendations: { modelKey: string; targetVersionId: string | null; state: RecommendationState }[] | null;
  recommendationsReason?: string | null;
};

/** 측정값 하나. 값이 null이면 `reason`이 있다(미측정은 null+사유). */
export type Rate = { value: number | null; unit?: string; numerator?: number | null; denominator?: number | null; coverage?: number | null; reason?: string | null };
export type Confusion = { tp: number | null; fp: number | null; tn: number | null; fn: number | null };
export type BasisRates = { recall: Rate; precision: Rate; falsePositiveRate: Rate; unavailableCount?: number };
export type ModelCategoryResult = {
  measurementStatus: "measured" | "unavailable" | "skipped";
  methodCount?: number;
  confusion: Confusion;
  unavailableCount: number;
  completeOnly: BasisRates;
  decidable?: BasisRates;
};
export type CompositeResult = {
  labelCounts: { anomalyLabelCount: number; normalLabelCount: number; unlabeledCount: number };
  completeOnly: BasisRates & { confusion: Confusion; hitCount: number };
};
export type EvaluationPolicy = { source: "accepted" | "configuration"; detectionPolicy: { mode?: string; deadlineMs?: number } | null; ruleCount: number };
export type ComparisonItem = {
  configVersionId: string;
  modelKey: string | null;
  role: "active" | "candidate";
  /** 완료 전 상세의 접수 항목은 `pending`. */
  status: "succeeded" | "failed" | "pending";
  errorCode?: string | null;
  evaluationId?: string | null;
  reasonCodes?: string[];
  /** 그 항목 평가에 실제 쓴 정책: `accepted` = 접수 때 고정한 운영 정책(v3), `configuration` = 구성에 저장된 정책·룰(v1·v2). */
  evaluationPolicy?: EvaluationPolicy;
  composite?: CompositeResult | null;
  categoryResults?: { ml: ModelCategoryResult };
  evaluationEnvelope?: { rule_ml: EvaluationItem; execution_cost: EvaluationItem; operational: EvaluationItem };
};
export type Recommendation = {
  modelKey: string;
  comparisonJobId: string;
  baselineConfigVersionId: string | null;
  targetVersionId: string | null;
  targetEvaluationId: string | null;
  /** 서버가 만든 한글 1줄. 추천 없음이면 사유 문구다. */
  summary: string;
  noRecommendationReason: string | null;
  generatedAt?: string;
  recommendationState: RecommendationState;
};
/** `GET /api/comparisons/:id`(비교 ID 또는 작업 ID). 완료 전에는 접수 때 고정한 값과 접수 항목만 있다. */
export type ComparisonDetail = {
  comparisonId: string | null;
  jobId: string;
  status: ComparisonStatus;
  errorCode?: string | null;
  completedAt?: string | null;
  evaluationSet?: { id: string; contentHash?: string; purpose?: string; rowCount?: number; originalRowCount?: number; syntheticRowCount?: number; synthetic?: boolean };
  baselines?: { modelKey: string; configVersionId: string }[];
  ruleSet?: { revision: number; contentHash: string | null };
  /** 추천 없음은 null. 그 사유는 `replacementRecommendationsReason`(v1 `not_implemented_v1`, 완료 전 null). */
  replacementRecommendations?: Recommendation[] | null;
  replacementRecommendationsReason?: string | null;
  items?: ComparisonItem[];
};

/** 평가 대상 모델 버전(모델 있는 구성만). `modelName`은 방법 카탈로그 표시 이름이다. */
export type CompareVersion = {
  id: string;
  displayName: string | null;
  modelKey: string;
  modelName: string;
  active: boolean;
  executable: boolean;
  latestEvaluation: { evaluationId: string } | null;
};

export type ComparisonTab = "current" | "all";
export type ComparisonPage = { items: ComparisonListItem[]; pagination: { limit: number; hasMore: boolean; nextCursor: string | null } };
/** 평가 세트의 기준 스냅샷 = 모델 버전의 학습 스냅샷(`GET /api/evaluation-sets/snapshots`). 세트는 그 검증 분할이다. */
export type EvaluationSnapshot = { id: string; sourceId: string; createdAt: string; from: string | null; until: string | null; configVersionIds: string[] };
export type ComparisonRequest = { kind: "compare"; sourceId: string; evaluationSetId: string; configVersionIds: string[] };
export type EvaluationSetRequest = { sourceId: string; snapshotId: string; purpose: "validation"; displayName: string; synthetic: null };

/** 요청 기능 연결 여부. 합성 세트 생성은 v1 서버가 받지 않는다(400 synthetic_set_unsupported). */
export const evaluationCapabilities = { createComparison: true, createEvaluationSet: true, syntheticSets: false };
export const SYNTHETIC_UNSUPPORTED = "synthetic_set_unsupported";
export const RECOMMENDATIONS_NOT_IMPLEMENTED = "not_implemented_v1";

const PAGE_LIMIT = 20;
const SET_LIMIT = 100;
/** 비교 대상 후보로 읽는 모델 버전 수(최신순 한 쪽). */
const VERSION_LIMIT = 100;

/** 비교 행·상세를 여는 ID: 완료 뒤는 비교 ID, 완료 전은 작업 ID(같은 상세 API가 둘 다 받는다). */
export const comparisonKey = (item: Pick<ComparisonListItem, "comparisonId" | "jobId">) => item.comparisonId ?? item.jobId;

export const readComparisonPage = (tab: ComparisonTab, cursor: string | null, signal?: AbortSignal): Promise<ComparisonPage> =>
  httpClient.get<ComparisonPage>("/api/comparisons", { params: { scope: tab, limit: PAGE_LIMIT, cursor: cursor ?? undefined }, signal });
export const readComparison = (id: string, signal?: AbortSignal): Promise<ComparisonDetail> =>
  httpClient.get<ComparisonDetail>(`/api/comparisons/${encodeURIComponent(id)}`, { signal });
export const readEvaluationSets = (signal?: AbortSignal): Promise<{ items: EvaluationSet[] }> =>
  httpClient.get<{ items: EvaluationSet[] }>("/api/evaluation-sets", { params: { limit: SET_LIMIT }, signal });
export const readEvaluationSet = (id: string, signal?: AbortSignal): Promise<EvaluationSet> =>
  httpClient.get<EvaluationSet>(`/api/evaluation-sets/${encodeURIComponent(id)}`, { signal });
export const readSnapshots = async (signal?: AbortSignal): Promise<EvaluationSnapshot[]> =>
  (await httpClient.get<{ items: EvaluationSnapshot[] }>("/api/evaluation-sets/snapshots", { signal })).items;

/** 모델 버전 목록(`GET /api/configurations`) + 방법 카탈로그 이름. 룰 전용 구성(`modelKey` null)은 비교 대상이 아니다. */
export const readCompareVersions = async (signal?: AbortSignal): Promise<CompareVersion[]> => {
  const [page, capabilities] = await Promise.all([readVersionPage("all", null, signal, VERSION_LIMIT), readCapabilities(signal)]);
  return page.items.filter((version) => version.modelKey).map((version) => ({
    id: version.id, displayName: version.displayName, modelKey: version.modelKey!, modelName: capabilities.methodCatalog.methods.find((method) => method.methodId.slice(method.methodId.indexOf(":") + 1) === version.modelKey)?.displayName ?? version.modelKey!,
    active: version.active, executable: version.executable, latestEvaluation: version.latestEvaluation ? { evaluationId: version.latestEvaluation.evaluationId } : null,
  }));
};
/** 현재 운영 룰 세트 버전. 비교 작업은 접수 시점 이 버전을 고정한다. */
export const readRuleRevision = async (signal?: AbortSignal): Promise<number> =>
  (await httpClient.get<{ ruleSet: { revision: number } }>("/api/operational-state", { signal })).ruleSet.revision;

/** `POST /api/jobs kind=compare`. 응답은 작업(`id`)이다. */
export const requestComparison = (request: ComparisonRequest): Promise<{ id: string }> => httpClient.post<{ id: string }>("/api/jobs", request);
/** `POST /api/evaluation-sets`(원본 분할만). 세트는 대기 상태로 접수되고 Trainer가 고정한다. */
export const requestEvaluationSet = (request: EvaluationSetRequest): Promise<{ id: string; jobId: string; status: EvaluationSet["status"] }> =>
  httpClient.post("/api/evaluation-sets", request);
