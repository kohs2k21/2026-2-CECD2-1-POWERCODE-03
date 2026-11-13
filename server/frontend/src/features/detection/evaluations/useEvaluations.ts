import { useRef, useState } from "react";
import { useAccountQuery } from "@/lib/query";
import { comparisonKey, readCompareVersions, readComparison, readComparisonPage, readEvaluationSet, readEvaluationSets, readRuleRevision, readSnapshots, type ComparisonListItem, type ComparisonStatus, type ComparisonTab } from "./evaluationsApi";

export const EVALUATION_SETS_QUERY = "evaluation-sets";
const inProgress = (status: ComparisonStatus | undefined) => status === "queued" || status === "running";

/** 앞쪽부터 `pages`쪽을 읽어 합친다. 쪽 사이에 새 평가가 들어와 밀린 행은 한 번만 둔다. */
const readLoadedPages = async (tab: ComparisonTab, pages: number) => {
  const rows = new Map<string, ComparisonListItem>();
  let page = await readComparisonPage(tab, null);
  page.items.forEach((item) => rows.set(comparisonKey(item), item));
  let read = 1;
  while (read < pages && page.pagination.nextCursor) {
    page = await readComparisonPage(tab, page.pagination.nextCursor);
    page.items.forEach((item) => rows.set(comparisonKey(item), item));
    read += 1;
  }
  return { items: [...rows.values()], nextCursor: page.pagination.nextCursor, pages: read };
};

/**
 * 비교 평가 목록: 최신순 커서. 재조회도 지금까지 읽은 쪽 수만큼 다시 읽는다. 그래서 `더 보기`로 읽은 행이 주기 조회에 사라지지 않고,
 * 뒤쪽 쪽의 진행 중 항목도 상태가 갱신된다. 읽은 행 중 대기·실행 중이 있을 때만 5초마다 다시 읽는다.
 */
export const useComparisons = (tab: ComparisonTab) => {
  const pages = useRef<Record<ComparisonTab, number>>({ current: 1, all: 1 });
  const [loading, setLoading] = useState(false);
  const first = useAccountQuery({
    name: "comparisons", params: [tab], adminOnly: true, staleTime: 0,
    read: () => readLoadedPages(tab, pages.current[tab]),
    isRunning: (list) => Boolean(list?.items.some((item) => inProgress(item.status))),
  });
  const loadMore = async () => {
    // 지금 읽어 둔 쪽 기준이다. 앞선 `더 보기`가 실패했으면 그 쪽부터 다시 읽는다.
    pages.current[tab] = (first.data?.pages ?? 1) + 1;
    setLoading(true);
    await first.refetch();
    setLoading(false);
  };
  return { first, items: first.data?.items ?? [], cursor: first.data?.nextCursor ?? null, loading, loadMore };
};
export const useComparison = (id: string) =>
  useAccountQuery({ name: "comparison", params: [id], adminOnly: true, staleTime: 0, read: (signal) => readComparison(id, signal), isRunning: (detail) => inProgress(detail?.status) });

/** 평가 세트 목록. 생성 대기 세트가 있으면 5초마다 다시 읽는다(Trainer가 고정하면 고를 수 있게 된다). */
export const useEvaluationSets = () =>
  useAccountQuery({ name: EVALUATION_SETS_QUERY, adminOnly: true, staleTime: 0, read: readEvaluationSets, isRunning: (sets) => Boolean(sets?.items.some((set) => set.status === "queued")) });
export const useEvaluationSet = (id: string | undefined) =>
  useAccountQuery({ name: "evaluation-set", params: [id], adminOnly: true, enabled: Boolean(id), read: (signal) => readEvaluationSet(id!, signal) });
export const useCompareVersions = () => useAccountQuery({ name: "compare-versions", adminOnly: true, staleTime: 0, read: readCompareVersions });
export const useRuleRevision = () => useAccountQuery({ name: "rule-revision", adminOnly: true, staleTime: 0, read: readRuleRevision });
export const useSnapshots = () => useAccountQuery({ name: "evaluation-snapshots", adminOnly: true, staleTime: 0, read: readSnapshots });
