import { httpClient } from "@/services/api/client";
import type { FeatureSet } from "@/services/api/pipeline.api";

/** 머신러닝 모델 화면 API(model-version.md 조회, trainer-service.md 발견·거래량 집계). 판단 없이 조회만 한다. */
export type ParameterRule = {
  type?: "integer" | "number" | "string" | "boolean";
  const?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
};
export type CatalogMethod = {
  methodId: string;
  displayName: string;
  status: string;
  methodVersion: string;
  executable: { process: boolean; window: boolean };
  executableReason?: { process?: string; window?: string };
  parameterSchema: { required?: string[]; properties: Record<string, ParameterRule> };
  parameterSchemaStatus: string;
  defaults: Record<string, unknown> | null;
  inputRequirements: { unit?: string; featureSet?: string; maxFeatures?: number; minTrainRows?: number; numericOnly?: boolean };
};
export type TimeBasis = "esb_occurred_at" | "persisted_at";
export type Capabilities = {
  methodCatalog: { catalogVersion: string; methods: CatalogMethod[] };
  snapshot: { timeBasis: TimeBasis };
  /** 값이 없으면 지원하지 않는 것으로 본다. */
  training?: { excludedIntervals?: boolean };
};

/** 방법 카탈로그가 없는 응답은 빈 목록으로 보이지 않게 실패로 둔다. */
export const readCapabilities = async (signal?: AbortSignal): Promise<Capabilities> => {
  const value = await httpClient.get<Partial<Capabilities>>("/api/jobs/capabilities", { signal });
  if (!Array.isArray(value.methodCatalog?.methods) || !value.snapshot?.timeBasis) throw new Error("method_catalog_unavailable");
  return value as Capabilities;
};

export type TrainingPeriod = { from: string; until: string; timeBasis: TimeBasis };
export type VersionMethod = {
  methodId: string;
  enabled: boolean;
  status: string;
  executable: { process: boolean; window: boolean };
  executableReason: string | null;
};
export type ModelVersion = {
  id: string;
  sourceId: string;
  schemaVersion: number | null;
  displayName: string | null;
  contentHash: string;
  createdAt: string;
  modelKey: string | null;
  active: boolean;
  activeSince: { revision: number; at: string } | null;
  createdFrom: { kind: string; trainJobId?: string | null };
  executable: boolean;
  methods: VersionMethod[];
  trainingSelection: {
    policyVersion?: string;
    populationCount?: number;
    selectedCount?: number;
    excludedCount?: number;
    trainingPeriod: TrainingPeriod | null;
    excludedIntervalCount: number;
  };
  /** 모델 없는 구성은 null. 안의 값도 저장 기록이 없으면 null이다. */
  modelSummary: { featureCount: number | null; parameters: Record<string, unknown> | null; thresholdValue: string | null } | null;
  latestEvaluation: { evaluationId: string; applicability: string; completedAt: string } | null;
  /** 운영 적용 gate(model-version.md 조회 절): 적격 평가 1건 또는 사유. */
  applicationEligibility?: { eligible: boolean; evaluationId: string | null; reason: string | null };
};
export type ExcludedInterval = { from: string; until: string; reasonCode?: string; note?: string | null };
export type ModelVersionDetail = ModelVersion & {
  methods: (VersionMethod & { methodVersion?: string | null; parameters: Record<string, unknown> | null; artifacts?: { modelArtifactId?: string | null; fitArtifactId?: string | null } })[];
  models: {
    modelKey: string;
    featureSetId: string;
    modelArtifactId: string | null;
    fitArtifactId: string | null;
    trainingSettings: { featureNames?: string[]; excludedIntervals?: ExcludedInterval[]; thresholdPolicy?: string };
    thresholdSpec: Record<string, unknown> | null;
  }[];
};
export type VersionPage = { items: ModelVersion[]; pagination: { limit: number; hasMore: boolean; nextCursor: string | null } };

const PAGE_LIMIT = 50;
export const readVersionPage = (tab: "active" | "all", cursor: string | null, signal?: AbortSignal) =>
  httpClient.get<VersionPage>("/api/configurations", { params: { limit: PAGE_LIMIT, cursor: cursor ?? undefined, active: tab === "active" ? true : undefined }, signal });

export const readVersion = (id: string, signal?: AbortSignal) =>
  httpClient.get<ModelVersionDetail>(`/api/configurations/${encodeURIComponent(id)}`, { signal });

export type VolumeUnit = "month" | "day" | "hour" | "minute";
export type VolumeBucket = { bucketStart: string; processCount: number; statusCounts: { S: number; F: number; P: number; N: number; other: number } };
export type VolumeReport = { from: string; until: string; bucket: VolumeUnit; timeBasis: TimeBasis; totalCount: number; timeUnknownCount: number; buckets: VolumeBucket[] };
export const readVolume = (params: { sourceId: string; from: string; until: string; bucket: VolumeUnit; timeBasis: TimeBasis; timezone: string }, signal?: AbortSignal) =>
  httpClient.get<VolumeReport>("/api/pipeline/reports/volume", { params, signal });

export const readFeatureSets = (sourceId: string, signal?: AbortSignal) =>
  httpClient.get<{ items: FeatureSet[]; hasMore: boolean }>("/api/jobs/feature-sets", { params: { sourceId }, signal });
