import { HttpError, httpClient } from "@/services/api/client";
import { finite, operationsReadUnavailable, record, timestamp } from "./operations";

/**
 * 운영 현황 읽기 API(operations-ui.md) 응답 파서. 명시한 필드만 복사하고, 잘못된 값은 버린다(null = 측정 불가).
 * 오류 응답(401·403·404·503)은 httpClient가 HttpError로 던진다. 성공·빈 값으로 바꾸지 않는다.
 */
/** 404 source_not_found(원천 없음)는 미연결(404 경로 없음·503)과 구분한다. */
export const sourceMissing = (error: unknown) => error instanceof HttpError && error.status === 404 && error.code === "source_not_found";
export const readErrorTitle = (error: unknown, what: string) =>
  sourceMissing(error)
    ? `${what} · 원천 없음`
    : operationsReadUnavailable(error)
      ? `${what} 미연결`
      : error instanceof Error && error.message
        ? error.message
        : `${what} 조회 실패`;
const fail = (what: string): never => {
  throw new Error(`${what} 응답을 확인할 수 없습니다.`);
};
const count = (v: unknown): number | null => (finite(v) && Number.isInteger(v) ? v : null);
const ms = (v: unknown): number | null => (finite(v) ? v : null);
const time = (v: unknown): string | null => (timestamp(v) ? v : null);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export const layers = ["T", "P", "M", "B"] as const;
export type Layer = (typeof layers)[number];
export type InputModeName = "api" | "csv" | "paused";
const inputModes: readonly InputModeName[] = ["api", "csv", "paused"];

export type CollectionView = {
  observedAt: string;
  source: { id: string; datasetKey: string; displayName: string };
  input: null | {
    mode: InputModeName;
    revision: number | null;
    status: string | null;
    csv: null | { replayKey: string | null; recordCount: number | null; rate: number | null; repeat: number | null; position: number | null; total: number | null };
    pollSeconds: number | null;
  };
  layers: { layer: Layer; storedCount: number | null; lastMinuteCount: number | null; lastPersistedAt: string | null; revisionArrivalCount: number | null }[];
  hourly: { hourStart: string; count: number }[];
  history: { id: string; createdAt: string; mode: "api" | "csv"; replayKey: string | null; status: string | null }[];
};

export const parseCollection = (value: unknown): CollectionView => {
  if (!record(value) || !timestamp(value.observedAt) || !record(value.source) || typeof value.source.id !== "string") fail("수집 현황");
  const v = value as Record<string, any>;
  const input: Record<string, any> | null =
    record(v.input) && inputModes.includes(v.input.mode as InputModeName) ? (v.input as Record<string, any>) : null;
  return {
    observedAt: v.observedAt,
    source: { id: v.source.id, datasetKey: text(v.source.datasetKey) ?? "", displayName: text(v.source.displayName) ?? "" },
    input: input && {
      mode: input.mode,
      revision: count(input.revision),
      status: text(input.status),
      csv: record(input.csv)
        ? { replayKey: text(input.csv.replayKey), recordCount: count(input.csv.recordCount), rate: ms(input.csv.rate),
            repeat: count(input.csv.repeat), position: count(input.csv.position), total: count(input.csv.total) }
        : null,
      pollSeconds: record(input.api) ? ms(input.api.pollSeconds) : null,
    },
    layers: layers.map((layer) => {
      const row = Array.isArray(v.layers) ? v.layers.find((item: unknown) => record(item) && item.layer === layer) : undefined;
      return { layer, storedCount: count(row?.storedCount), lastMinuteCount: count(row?.lastMinuteCount),
        lastPersistedAt: time(row?.lastPersistedAt), revisionArrivalCount: count(row?.revisionArrivalCount) };
    }),
    hourly: Array.isArray(v.hourly?.items)
      ? v.hourly.items.flatMap((item: unknown) =>
          record(item) && timestamp(item.hourStart) && count(item.count) !== null ? [{ hourStart: item.hourStart, count: item.count as number }] : [])
      : [],
    history: Array.isArray(v.history)
      ? v.history.flatMap((item: unknown) =>
          record(item) && typeof item.id === "string" && timestamp(item.createdAt) && (item.mode === "api" || item.mode === "csv")
            ? [{ id: item.id, createdAt: item.createdAt, mode: item.mode, replayKey: text(item.replayKey), status: text(item.status) }]
            : [])
      : [],
  };
};

export const latencyStageIds = ["collector", "persist", "detectionQueue", "detector", "judgmentStore"] as const;
export type LatencyStageId = (typeof latencyStageIds)[number];
export type LatencyStage = { id: LatencyStageId; samples: number; p50Ms: number | null; p95Ms: number | null;
  queuePending: number | null; oldestWaitMs: number | null; recentFailures: number | null };
export type LatencyView = { observedAt: string; window: string; stages: LatencyStage[]; endToEndP50Ms: number | null };

export const parseLatency = (value: unknown): LatencyView => {
  if (!record(value) || !timestamp(value.observedAt) || !Array.isArray(value.stages)) fail("단계별 지연");
  const v = value as Record<string, any>;
  return {
    observedAt: v.observedAt,
    window: text(v.window) ?? "",
    stages: latencyStageIds.map((id) => {
      const row = v.stages.find((item: unknown) => record(item) && item.id === id) ?? {};
      return { id, samples: count(row.samples) ?? 0, p50Ms: ms(row.p50Ms), p95Ms: ms(row.p95Ms), queuePending: count(row.queuePending),
        oldestWaitMs: ms(row.oldestWaitMs), recentFailures: count(row.recentFailures) };
    }),
    endToEndP50Ms: record(v.endToEnd) ? ms(v.endToEnd.p50Ms) : null,
  };
};

export type SourceFieldsView = {
  layer: Layer;
  sampleSize: number;
  sampleLimit: number | null;
  fields: { name: string; presentCount: number; nullCount: number; missingCount: number }[];
  qualityFlags: { flag: string; count: number }[];
};
export const parseSourceFields = (value: unknown): SourceFieldsView => {
  if (!record(value) || !layers.includes(value.layer as Layer) || count(value.sampleSize) === null) fail("원본 필드");
  const v = value as Record<string, any>;
  return {
    layer: v.layer,
    sampleSize: v.sampleSize,
    sampleLimit: count(v.sampleLimit),
    fields: Array.isArray(v.fields)
      ? v.fields.flatMap((item: unknown) =>
          record(item) && typeof item.name === "string" && count(item.presentCount) !== null && count(item.nullCount) !== null && count(item.missingCount) !== null
            ? [{ name: item.name, presentCount: item.presentCount as number, nullCount: item.nullCount as number, missingCount: item.missingCount as number }]
            : [])
      : [],
    qualityFlags: Array.isArray(v.qualityFlags)
      ? v.qualityFlags.flatMap((item: unknown) =>
          record(item) && typeof item.flag === "string" && count(item.count) !== null ? [{ flag: item.flag, count: item.count as number }] : [])
      : [],
  };
};

export type StorageView = { measuredAt: string; databaseBytes: number | null;
  disk: null | { capacityBytes: number | null; usedBytes: number | null; availableBytes: number | null; measuredAt: string | null } };
export const parseStorage = (value: unknown): StorageView => {
  if (!record(value) || !timestamp(value.measuredAt)) fail("저장공간");
  const v = value as Record<string, any>;
  return {
    measuredAt: v.measuredAt,
    databaseBytes: ms(v.databaseBytes),
    disk: record(v.disk)
      ? { capacityBytes: ms(v.disk.capacityBytes), usedBytes: ms(v.disk.usedBytes), availableBytes: ms(v.disk.availableBytes), measuredAt: time(v.disk.measuredAt) }
      : null,
  };
};

export type TrendView = { configVersionId: string | null; timezone: string;
  items: { day: string; decisionCount: number; anomalySignalRatio: number | null; unavailableRatio: number | null }[] };
const ratio = (v: unknown) => (finite(v) && v <= 1 ? v : null);
export const parseTrend = (value: unknown): TrendView => {
  if (!record(value) || !Array.isArray(value.items)) fail("판정 추이");
  const v = value as Record<string, any>;
  return {
    configVersionId: text(v.configVersionId),
    timezone: text(v.timezone) ?? "",
    items: v.items.flatMap((item: unknown) =>
      record(item) && typeof item.day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(item.day) && count(item.decisionCount) !== null
        ? [{ day: item.day, decisionCount: item.decisionCount as number, anomalySignalRatio: ratio(item.anomalySignalRatio), unavailableRatio: ratio(item.unavailableRatio) }]
        : []),
  };
};

export const readCollection = async (sourceId: string, signal?: AbortSignal) =>
  parseCollection(await httpClient.get<unknown>("/api/operations/collection", { params: { sourceId }, signal }));
export const readLatency = async (sourceId: string, signal?: AbortSignal) =>
  parseLatency(await httpClient.get<unknown>("/api/operations/latency", { params: { sourceId }, signal }));
export const readSourceFields = async (sourceId: string, layer: Layer, signal?: AbortSignal) =>
  parseSourceFields(await httpClient.get<unknown>("/api/operations/source-fields", { params: { sourceId, layer }, signal }));
export const readStorage = async (signal?: AbortSignal) => parseStorage(await httpClient.get<unknown>("/api/operations/storage", { signal }));
export const readTrend = async (signal?: AbortSignal) =>
  parseTrend(await httpClient.get<unknown>("/api/operations/decision-trend", { params: { days: "3" }, signal }));
