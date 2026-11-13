import { HttpError, httpClient } from "@/services/api/client";
import { getStoredToken } from "@/services/auth/session";

export const operationStageIds = ["collector", "redaction", "observationQueue", "detector", "judgmentStore"] as const;
export type OperationStageId = typeof operationStageIds[number];
export type OperationStage = {
  state?: "healthy" | "degraded" | "stopped" | "unknown";
  pendingCount?: number;
  oldestWaitMs?: number;
  lastProcessedAt?: string;
};
export type OperationsOverview = {
  observedAt: string;
  maxAgeMs: number;
  stages: Partial<Record<OperationStageId, OperationStage>>;
  activeConfigurationVersion?: string;
  endToEndLatencyMs?: number;
  collectorEps?: number;
  cpuPercent?: number;
  memoryPercent?: number;
  diskPercent?: number;
  alertDeliverySuccessPercent?: number;
  /** 전체 원천 판정 큐 대기(작업 생성 → 첫 시도) 최근 1시간 p95 ms. */
  queueWaitP95Ms?: number;
  /** 현재 운영 구성 요약(선택). 없으면 운영 구성 미적용 또는 확인 불가. */
  activeConfiguration?: ActiveConfiguration | null;
  /** 구성 없이(v04) 운영되는 룰 세트·활성 모델 집합 버전(선택). null = 확인 불가. */
  operationalState?: OperationalState | null;
};
export type OperationalState = {
  ruleSetRevision?: number;
  activeModelSetRevision?: number;
  models: { modelKey: string }[];
};
export type ActiveConfiguration = {
  configVersionId: string;
  sourceId?: string;
  revision?: number;
  changedAt?: string;
  detectionMode?: string;
  ruleCount?: number;
  ruleSeverityCounts: Partial<Record<"info" | "warning" | "critical", number>>;
  models: { modelKey: string; adapterName?: string }[];
};
export const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
export const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
export const timestamp = (v: unknown): v is string => {
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(v) || !Number.isFinite(Date.parse(v))) return false;
  // Date.parse normalizes impossible calendar dates such as February 30.
  const calendar = new Date(v.slice(0, 10) + "T00:00:00Z");
  return calendar.toISOString().slice(0, 10) === v.slice(0, 10);
};
export const parseOperationsOverview = (value: unknown): OperationsOverview => {
  if (!record(value) || !timestamp(value.observedAt) || !finite(value.maxAgeMs) || value.maxAgeMs === 0) {
    throw new Error("운영 관측 응답을 확인할 수 없습니다.");
  }
  const result: OperationsOverview = { observedAt: value.observedAt, maxAgeMs: value.maxAgeMs, stages: {} };
  if (typeof value.activeConfigurationVersion === "string") result.activeConfigurationVersion = value.activeConfigurationVersion;
  for (const field of ["endToEndLatencyMs", "queueWaitP95Ms", "collectorEps", "cpuPercent", "memoryPercent", "diskPercent", "alertDeliverySuccessPercent"] as const) {
    if (finite(value[field]) && (!field.endsWith("Percent") || value[field] <= 100)) result[field] = value[field];
  }
  if (value.activeConfiguration === null) result.activeConfiguration = null;
  else if (record(value.activeConfiguration)) result.activeConfiguration = parseActiveConfiguration(value.activeConfiguration);
  if (value.operationalState === null) result.operationalState = null;
  else if (record(value.operationalState)) result.operationalState = parseOperationalState(value.operationalState);
  if (record(value.stages)) for (const id of operationStageIds) {
    const stage = value.stages[id];
    if (!record(stage)) continue;
    const safe: OperationStage = {};
    if (typeof stage.state === "string" && ["healthy", "degraded", "stopped", "unknown"].includes(stage.state)) safe.state = stage.state as OperationStage["state"];
    if (finite(stage.pendingCount) && Number.isInteger(stage.pendingCount)) safe.pendingCount = stage.pendingCount;
    if (finite(stage.oldestWaitMs)) safe.oldestWaitMs = stage.oldestWaitMs;
    if (timestamp(stage.lastProcessedAt)) safe.lastProcessedAt = stage.lastProcessedAt;
    result.stages[id] = safe;
  }
  return result;
};
const integer = (v: unknown): v is number => finite(v) && Number.isInteger(v);
const parseActiveConfiguration = (value: Record<string, unknown>): ActiveConfiguration | undefined => {
  if (typeof value.configVersionId !== "string" || !value.configVersionId) return undefined;
  const result: ActiveConfiguration = { configVersionId: value.configVersionId, ruleSeverityCounts: {}, models: [] };
  if (integer(value.revision)) result.revision = value.revision;
  if (timestamp(value.changedAt)) result.changedAt = value.changedAt;
  if (typeof value.sourceId === "string" && value.sourceId) result.sourceId = value.sourceId;
  if (typeof value.detectionMode === "string") result.detectionMode = value.detectionMode;
  if (integer(value.ruleCount)) result.ruleCount = value.ruleCount;
  if (record(value.ruleSeverityCounts)) for (const level of ["info", "warning", "critical"] as const) {
    const count = value.ruleSeverityCounts[level];
    if (integer(count)) result.ruleSeverityCounts[level] = count;
  }
  if (Array.isArray(value.models)) for (const model of value.models) {
    if (!record(model) || typeof model.modelKey !== "string" || !model.modelKey) continue;
    result.models.push(typeof model.adapterName === "string" ? { modelKey: model.modelKey, adapterName: model.adapterName } : { modelKey: model.modelKey });
  }
  return result;
};
const parseOperationalState = (value: Record<string, unknown>): OperationalState => {
  const result: OperationalState = { models: [] };
  if (integer(value.ruleSetRevision)) result.ruleSetRevision = value.ruleSetRevision;
  if (integer(value.activeModelSetRevision)) result.activeModelSetRevision = value.activeModelSetRevision;
  if (Array.isArray(value.models)) for (const model of value.models) {
    if (record(model) && typeof model.modelKey === "string" && model.modelKey) result.models.push({ modelKey: model.modelKey });
  }
  return result;
};
export const operationsFreshness = (data: OperationsOverview | undefined, now = Date.now()) => {
  if (!data) return "unknown";
  const age = now - Date.parse(data.observedAt);
  return age < 0 ? "unknown" : age > data.maxAgeMs ? "stale" : "fresh";
};
export const readOperationsOverview = async (signal?: AbortSignal) => {
  const token = getStoredToken();
  const value = await httpClient.get<unknown>("/api/operations/overview", { signal });
  if (signal?.aborted || token !== getStoredToken()) throw new DOMException("Session changed", "AbortError");
  return parseOperationsOverview(value);
};
export const operationsReadUnavailable = (error: unknown) => error instanceof HttpError && [404, 503].includes(error.status);
