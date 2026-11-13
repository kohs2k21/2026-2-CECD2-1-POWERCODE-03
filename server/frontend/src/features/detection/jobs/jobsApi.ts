import { httpClient } from "@/services/api/client";

/** 작업 목록·작업 상세 API(operations-ui.md '작업 목록·작업 상세'). 값은 서버 저장값 그대로다. */
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "blocked";
export type JobResultLinks = {
  configVersionId: string | null;
  evaluationId: string | null;
  evaluationSetId: string | null;
  comparisonId: string | null;
  itemCount: number | null;
  failedCount: number | null;
};
export type JobListItem = {
  id: string;
  kind: string;
  status: JobStatus;
  inputSpec: Record<string, unknown>;
  attemptCount: number;
  maxAttempts: number;
  nextRunAt: string | null;
  errorCode: string | null;
  resultConfigVersionId: string | null;
  createdAt: string;
  finishedAt: string | null;
  /** 결과 화면 연결 값. 결과가 없으면 null. */
  result: JobResultLinks | null;
};
export type JobAttempt = {
  attemptNo: number;
  status: "running" | "succeeded" | "failed" | "expired";
  startedAt: string;
  heartbeatAt: string | null;
  finishedAt: string | null;
  errorCode: string | null;
};
export type JobDetail = Omit<JobListItem, "result"> & { resultSummary: Record<string, unknown> | null; attempts: JobAttempt[] };
export type JobPage = { items: JobListItem[]; pagination: { limit: number; hasMore: boolean; nextCursor: string | null } };

export const JOB_PAGE_LIMIT = 20;

export const readJobPage = (filter: { kinds: readonly string[]; statuses: readonly string[] | null }, cursor: string | null, signal?: AbortSignal) =>
  httpClient.get<JobPage>("/api/jobs", {
    params: { kind: filter.kinds.join(","), status: filter.statuses?.join(","), limit: JOB_PAGE_LIMIT, cursor: cursor ?? undefined },
    signal,
  });
export const readJob = (id: string, signal?: AbortSignal) => httpClient.get<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`, { signal });
