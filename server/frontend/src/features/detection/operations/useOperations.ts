import { useState } from "react";
import { useAccountQuery } from "@/lib/query";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { readComparison, readComparisonPage, type ComparisonDetail, type ComparisonItem } from "@/features/detection/evaluations/evaluationsApi";
import { readActiveHistory, readMethodStatus, readOperationalState, type ActiveHistoryItem, type MethodPeriod } from "./operationsApi";

export const OPERATIONAL_STATE_QUERY = "operational-state";
export const METHOD_STATUS_QUERY = "method-status";
export const ACTIVE_HISTORY_QUERY = "active-model-history";

export const useOperationalState = () => useAccountQuery({ name: OPERATIONAL_STATE_QUERY, read: readOperationalState, adminOnly: true, staleTime: 0 });
export const useMethodStatus = (period: MethodPeriod) =>
  useAccountQuery({ name: METHOD_STATUS_QUERY, params: [period], read: (signal) => readMethodStatus(period, signal), adminOnly: true, staleTime: 0, keepPrevious: true });

/** 모델 교체 이력: 최신순 커서. 활성 집합 버전이 바뀌면 첫 쪽부터 다시 읽는다. */
export const useActiveHistory = (revision: number | undefined) => {
  const first = useAccountQuery({ name: ACTIVE_HISTORY_QUERY, params: [revision], read: (signal) => readActiveHistory(null, signal), adminOnly: true, staleTime: 0, enabled: revision !== undefined });
  const key = `${revision}:${first.dataUpdatedAt}`;
  const [more, setMore] = useState<{ key: string; items: ActiveHistoryItem[]; cursor?: string | null; loading: boolean; error: string | null }>({ key, items: [], loading: false, error: null });
  const current = more.key === key ? more : { key, items: [], cursor: undefined, loading: false, error: null };
  const cursor = current.cursor === undefined ? (first.data?.pagination.nextCursor ?? null) : current.cursor;
  const loadMore = async () => {
    if (!cursor) return;
    setMore({ ...current, loading: true, error: null });
    try {
      const page = await readActiveHistory(cursor);
      setMore((value) => (value.key === key ? { key, items: [...value.items, ...page.items], cursor: page.pagination.nextCursor, loading: false, error: null } : value));
    } catch (error) {
      setMore((value) => (value.key === key ? { ...value, loading: false, error: errorCode(error) } : value));
    }
  };
  const items = first.data ? [...first.data.items, ...current.items] : [];
  return { first, items, cursor, loading: current.loading, moreError: current.error, loadMore };
};

/** 조회할 비교 평가 상세 수 상한(최신순). 교체 근거는 최근 비교에서 찾는다. */
const COMPARISON_SCAN = 10;
const measured = (item: ComparisonItem | undefined) => item?.status === "succeeded" && item.categoryResults?.ml.measurementStatus === "measured";

/** 최근 완료 비교 평가 상세들(최신순). 교체 추천·교체 근거가 함께 쓴다. */
export const useRecentComparisons = () =>
  useAccountQuery({
    name: "recent-comparisons", adminOnly: true, staleTime: 0,
    read: async () => {
      const page = await readComparisonPage("all", null);
      const done = page.items.filter((item) => item.status === "succeeded").slice(0, COMPARISON_SCAN);
      const details: ComparisonDetail[] = [];
      for (const item of done) details.push(await readComparison(item.comparisonId));
      return details;
    },
  });

/** 교체 근거(와이어프레임 commonRun): 현재 운영 버전과 선택 버전이 모두 측정된 가장 최근 비교. 같은 종류 운영 모델이 없으면 선택 버전이 측정된 가장 최근 비교. */
export const evidenceFor = (details: ComparisonDetail[] | undefined, baseline: string | null, target: string | null) => {
  if (!details || !target) return null;
  return details.find((detail) => {
    const items = detail.items ?? [];
    const has = (id: string) => measured(items.find((item) => item.configVersionId === id));
    return has(target) && (baseline === null || has(baseline));
  }) ?? null;
};
