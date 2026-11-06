import { useState } from "react";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { useAccountQuery } from "@/lib/query";
import { fetchPipelineJob } from "@/services/api/pipeline.api";
import { readCapabilities, readVersionPage, type ModelVersion } from "./modelsApi";

export const VERSIONS_QUERY = "model-versions";
export const useCapabilities = () => useAccountQuery({ name: "trainer-capabilities", read: readCapabilities, adminOnly: true });

/** 학습 작업 상태. 대기·실행 중에만 5초마다 다시 읽는다. */
export const useTrainJob = (jobId: string | null | undefined) =>
  useAccountQuery({
    name: "train-job", params: [jobId], adminOnly: true, enabled: Boolean(jobId), staleTime: 0,
    read: (signal) => fetchPipelineJob(jobId!, signal),
    isRunning: (job) => job?.status === "queued" || job?.status === "running",
  });

type More = { key: string; items: ModelVersion[]; cursor?: string | null; error: string | null; loading: boolean };

/** 버전 목록: 두 탭 모두 최신순 커서(현재 운영 버전은 서버 `active=true`). */
export const useVersions = (tab: "active" | "all") => {
  const first = useAccountQuery({
    name: VERSIONS_QUERY, params: [tab], adminOnly: true, staleTime: 0,
    read: (signal) => readVersionPage(tab, null, signal),
  });
  // 뒤쪽 결과는 첫 쪽(탭·조회 시점)에 묶는다. 첫 쪽이 바뀌면 옛 커서 기준이라 쓰지 않고, 늦은 응답은 다른 탭이 읽은 쪽을 덮지 않는다.
  const pageKey = `${tab}:${first.dataUpdatedAt}`;
  const [stored, setStored] = useState<More>({ key: pageKey, items: [], error: null, loading: false });
  const more = stored.key === pageKey ? stored : { key: pageKey, items: [], error: null, loading: false, cursor: undefined };
  // 더 읽은 뒤에는 그 쪽의 커서만 쓴다. 마지막 쪽의 null은 끝이다.
  const cursor = more.cursor === undefined ? (first.data?.pagination.nextCursor ?? null) : more.cursor;
  const loadMore = async () => {
    if (!cursor) return;
    const key = pageKey;
    setStored({ ...more, error: null, loading: true });
    try {
      const page = await readVersionPage(tab, cursor);
      setStored((current) => (current.key === key ? { key, items: [...current.items, ...page.items], cursor: page.pagination.nextCursor, error: null, loading: false } : current));
    } catch (error) {
      setStored((current) => (current.key === key ? { ...current, error: errorCode(error), loading: false } : current));
    }
  };
  // 룰 전용 구성(v1·v2)은 모델이 없어 모델 버전이 아니다.
  const items = first.data ? [...first.data.items, ...more.items].filter((item) => item.modelKey !== null) : [];
  return { first, items, cursor, loading: more.loading, moreError: more.error, loadMore };
};
