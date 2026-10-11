import { HttpError, httpClient } from "../../../services/api/client";
import { getStoredToken } from "../../../services/auth/session";

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
};
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const timestamp = (v: unknown): v is string => {
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
  for (const field of ["endToEndLatencyMs", "collectorEps", "cpuPercent", "memoryPercent", "diskPercent", "alertDeliverySuccessPercent"] as const) {
    if (finite(value[field]) && (!field.endsWith("Percent") || value[field] <= 100)) result[field] = value[field];
  }
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
