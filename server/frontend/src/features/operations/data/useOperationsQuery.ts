import { useAccountQuery } from "@/lib/query";
import { fetchProcessDecisions } from "@/services/api/pipeline.api";
import { readOperationsOverview } from "./operations";
import {
  readCollection,
  readLatency,
  readSourceFields,
  readStorage,
  readTrend,
  type CollectionView,
  type Layer,
} from "./operationsReads";

/**
 * 운영 현황 조회: 계정별 key·AbortSignal·세션 검증(useAccountQuery). 자동 재조회 없음(수동 새로고침).
 * 수집 현황만 입력 실행이 running일 때 5초 주기 조회(RUNNING_POLL_MS, 진행 중 작업 규칙).
 */
export const operationsQueryName = "operations-overview";
export const useOperationsQuery = () => useAccountQuery({ name: operationsQueryName, read: readOperationsOverview });

export const collectionRunning = (data: CollectionView | undefined) => data?.input?.status === "running";
export const useCollectionQuery = (sourceId: string) =>
  useAccountQuery({
    name: "operations-collection",
    params: [sourceId],
    read: (signal) => readCollection(sourceId, signal),
    enabled: Boolean(sourceId),
    staleTime: 0,
    isRunning: collectionRunning,
  });
export const useLatencyQuery = (sourceId: string) =>
  useAccountQuery({ name: "operations-latency", params: [sourceId], read: (signal) => readLatency(sourceId, signal), enabled: Boolean(sourceId) });
export const useSourceFieldsQuery = (sourceId: string, layer: Layer) =>
  useAccountQuery({
    name: "operations-source-fields",
    params: [sourceId, layer],
    read: (signal) => readSourceFields(sourceId, layer, signal),
    enabled: Boolean(sourceId),
  });
export const useStorageQuery = (enabled = true) => useAccountQuery({ name: "operations-storage", read: readStorage, enabled });
export const useTrendQuery = (enabled = true) => useAccountQuery({ name: "operations-decision-trend", read: readTrend, enabled });
/** 최근 판정: 기존 저장 판정 목록 API(/api/pipeline/decisions) 재사용, 앞 4건만 표시. 판정 화면 캐시(SSE 병합)와 key를 나눈다. */
export const RECENT_DECISION_COUNT = 4;
export const useRecentDecisionsQuery = (enabled = true) =>
  useAccountQuery({
    name: "operations-recent-decisions",
    read: (signal) => fetchProcessDecisions(signal, RECENT_DECISION_COUNT),
    enabled,
  });
