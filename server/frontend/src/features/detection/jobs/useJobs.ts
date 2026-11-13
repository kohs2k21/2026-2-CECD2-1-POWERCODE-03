import { useRef, useState } from "react";
import { labelOf } from "@/features/detection/evaluations/evaluationFormat";
import { useCompareVersions, useEvaluationSets } from "@/features/detection/evaluations/useEvaluations";
import { useCapabilities } from "@/features/detection/models/useModels";
import { useAccountQuery } from "@/lib/query";
import { JOB_KINDS, JOB_TAB_STATUSES, jobInProgress, type JobNames, type JobTab } from "./jobFormat";
import { readJob, readJobPage, type JobListItem } from "./jobsApi";

export const JOBS_QUERY = "jobs";
export type JobFilter = { tab: JobTab; kind: string };

const filterOf = ({ tab, kind }: JobFilter) => ({ kinds: kind === "all" ? JOB_KINDS : [kind], statuses: JOB_TAB_STATUSES[tab] });

/** 앞쪽부터 `pages`쪽을 읽어 합친다. 쪽 사이에 새 작업이 들어와 밀린 행은 한 번만 둔다. */
const readLoadedPages = async (filter: JobFilter, pages: number, signal?: AbortSignal) => {
  const rows = new Map<string, JobListItem>();
  let page = await readJobPage(filterOf(filter), null, signal);
  page.items.forEach((item) => rows.set(item.id, item));
  let read = 1;
  while (read < pages && page.pagination.nextCursor) {
    page = await readJobPage(filterOf(filter), page.pagination.nextCursor, signal);
    page.items.forEach((item) => rows.set(item.id, item));
    read += 1;
  }
  return { items: [...rows.values()], nextCursor: page.pagination.nextCursor, pages: read };
};

/**
 * 작업 목록: 최신순 커서(평가 결과 비교 목록과 같은 방식). 재조회도 지금까지 읽은 쪽 수만큼 다시 읽어 `더 보기`로 읽은 행이 사라지지 않고,
 * 읽은 행 중 대기·실행 중이 있을 때만 5초마다 다시 읽는다.
 */
export const useJobs = (filter: JobFilter) => {
  const key = `${filter.tab}:${filter.kind}`;
  const pages = useRef<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const first = useAccountQuery({
    name: JOBS_QUERY, params: [filter.tab, filter.kind], adminOnly: true, staleTime: 0,
    read: (signal) => readLoadedPages(filter, pages.current[key] ?? 1, signal),
    isRunning: (list) => Boolean(list?.items.some((item) => jobInProgress(item.status))),
  });
  const loadMore = async () => {
    // 지금 읽어 둔 쪽 기준이다. 앞선 `더 보기`가 실패했으면 그 쪽부터 다시 읽는다.
    pages.current[key] = (first.data?.pages ?? 1) + 1;
    setLoading(true);
    await first.refetch();
    setLoading(false);
  };
  return { first, items: first.data?.items ?? [], cursor: first.data?.nextCursor ?? null, loading, loadMore };
};

/** 작업 1건. 대기·실행 중에만 5초마다 다시 읽는다. */
export const useJob = (id: string) =>
  useAccountQuery({ name: "job", params: [id], adminOnly: true, staleTime: 0, read: (signal) => readJob(id, signal), isRunning: (job) => jobInProgress(job?.status) });

/** 이름 조회(모델·평가 세트·모델 버전)는 표시만 돕는다. 실패하면 id 앞 8자리로 보인다. */
export const useJobNames = (): JobNames => {
  const capabilities = useCapabilities();
  const sets = useEvaluationSets();
  const versions = useCompareVersions();
  return {
    methods: capabilities.data?.methodCatalog.methods,
    setName: (id) => sets.data?.items.find((set) => set.id === id)?.displayName ?? null,
    versionLabel: (id) => labelOf(versions.data, id),
  };
};
