import { HttpError } from "@/services/api/client";
import type { EvaluationItem } from "@/services/api/pipeline.api";

/**
 * 평가 결과 화면의 데이터 접근 한곳(config-comparison-evaluation.md 조회 API 형식). 지금은 API에 연결하지 않았다:
 * 개발 모드만 예시 데이터를 읽고 운영 빌드는 `service_unavailable`로 실패한다(detection-ui.md 개발 조회). 연결할 때 이 파일만 바꾼다.
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
  /** 합성 없는 세트(원본 분할만)는 null. */
  synthetic: SyntheticSpec | null;
  /** 생성 전·실패는 null. */
  generation: { rowCount: number; originalRowCount: number; syntheticRowCount: number; truthCount: number; contentHash: string; artifactRef: string | null } | null;
  createdAt: string;
  readyAt: string | null;
};

export type ComparisonStatus = "queued" | "running" | "succeeded" | "failed";
export type RecommendationState = "current" | "applied" | "stale" | "none";
export type ComparisonListItem = {
  comparisonId: string;
  jobId: string;
  evaluationSetId: string;
  baselines: { modelKey: string; configVersionId: string }[];
  activeModelSetRevision: number;
  status: ComparisonStatus;
  itemCount: number;
  succeededCount: number;
  failedCount: number;
  completedAt: string | null;
  recommendations: { modelKey: string; targetVersionId: string | null; state: RecommendationState }[];
};

/** 측정값 하나. 값이 null이면 `reason`이 있다(미측정은 null+사유). */
export type Rate = { value: number | null; unit?: string; numerator?: number | null; denominator?: number | null; coverage?: number | null; reason?: string | null };
export type Confusion = { tp: number | null; fp: number | null; tn: number | null; fn: number | null };
export type BasisRates = { recall: Rate; precision: Rate; falsePositiveRate: Rate; unavailableCount?: number };
export type ModelCategoryResult = {
  measurementStatus: "measured" | "unavailable" | "skipped";
  methodCount: number;
  confusion: Confusion;
  unavailableCount: number;
  completeOnly: BasisRates;
  decidable?: BasisRates;
};
export type CompositeResult = {
  labelCounts: { anomalyLabelCount: number; normalLabelCount: number; unlabeledCount: number };
  completeOnly: BasisRates & { confusion: Confusion; hitCount: number };
};
export type ComparisonItem = {
  configVersionId: string;
  modelKey: string;
  role: "active" | "candidate";
  status: "succeeded" | "failed";
  errorCode: string | null;
  evaluationId: string | null;
  reasonCodes?: string[];
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
  generatedAt: string;
  recommendationState: RecommendationState;
};
/** `GET /api/comparisons/:id`. 완료 전에는 상태만 있다(계약: status·접수 항목만). */
export type ComparisonDetail = {
  comparisonId: string;
  jobId: string;
  status: ComparisonStatus;
  errorCode?: string | null;
  evaluationSet?: { id: string; contentHash: string; purpose: string; rowCount: number; originalRowCount: number; syntheticRowCount: number; synthetic: boolean };
  baselines?: { modelKey: string; configVersionId: string }[];
  ruleSet?: { revision: number; contentHash: string };
  replacementRecommendations?: Recommendation[];
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
/** 평가 세트의 기준 스냅샷(`POST /api/evaluation-sets`의 `snapshotId`). 목록 API가 계약에 없어 형식은 화면에 필요한 값만 둔다. */
export type EvaluationSnapshot = { id: string; displayName: string };
export type ComparisonRequest = { kind: "compare"; sourceId: string; evaluationSetId: string; configVersionIds: string[] };

/** 요청 기능 연결 여부(detection-ui.md 기본 false). 연결 전 실행 버튼은 비활성이다. */
export const evaluationCapabilities = { createComparison: false, createEvaluationSet: false };
export const NOT_CONNECTED = "service_unavailable";
const notConnected = () => new HttpError(NOT_CONNECTED, 503, NOT_CONNECTED);

const fromSample = async <T>(read: (sample: typeof import("./evaluationsSample")) => T): Promise<T> => {
  if (import.meta.env?.DEV) return read(await import("./evaluationsSample"));
  throw notConnected();
};
const found = <T>(value: T | undefined) => {
  if (value === undefined) throw new HttpError("not_found", 404);
  return value;
};

export const readComparisonPage = (tab: ComparisonTab, cursor: string | null): Promise<ComparisonPage> =>
  fromSample((sample) => sample.comparisonPage(tab, cursor));
export const readComparison = (id: string): Promise<ComparisonDetail> => fromSample((sample) => found(sample.comparisonDetail(id)));
export const readEvaluationSets = (): Promise<{ items: EvaluationSet[] }> => fromSample((sample) => ({ items: sample.evaluationSets }));
export const readEvaluationSet = (id: string): Promise<EvaluationSet> => fromSample((sample) => found(sample.evaluationSets.find((set) => set.id === id)));
export const readCompareVersions = (): Promise<CompareVersion[]> => fromSample((sample) => sample.compareVersions);
export const readSnapshots = (): Promise<EvaluationSnapshot[]> => fromSample((sample) => sample.snapshots);
/** 현재 운영 룰 세트 버전. 비교 작업은 접수 시점 이 버전을 고정한다. */
export const readRuleRevision = (): Promise<number> => fromSample((sample) => sample.ruleRevision);

/** `POST /api/jobs kind=compare`. 연결 전이라 항상 실패한다(성공 시뮬레이션 없음). */
export const requestComparison = async (_request: ComparisonRequest): Promise<{ id: string }> => {
  throw notConnected();
};
