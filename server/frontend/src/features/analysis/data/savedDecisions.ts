import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { consumeDecisionStream } from "@/services/api/decision-stream.api";
import {
  createPipelineJob,
  fetchPipelineJob,
  fetchProcessDecisions,
  type PipelineJob,
  type ProcessDecision,
} from "@/services/api/pipeline.api";

export const savedDecisionsQueryName = "process-decisions";
export const explainJobQueryName = "explain-job";
export const SAVED_DECISION_LIMIT = 50;
const STREAM_RETRY_MS = 3_000;

/** 새 판정을 앞에 넣고 같은 ID는 하나만 유지(최대 50건). */
export const mergeDecision = (rows: ProcessDecision[] | undefined, value: ProcessDecision) =>
  [value, ...(rows ?? []).filter((item) => item.id !== value.id)].slice(0, SAVED_DECISION_LIMIT);

/**
 * 목록 응답 반영: 목록 조회가 끝나기 전에 SSE로 받은 판정(streamed, 최신순)을 보존한다.
 * 같은 ID는 SSE 쪽(더 최신)을 쓰고, 최대 50건.
 */
export const mergeDecisionLists = (listed: ProcessDecision[], streamed: ProcessDecision[]) => {
  const ids = new Set(streamed.map((item) => item.id));
  return [...streamed, ...listed.filter((item) => !ids.has(item.id))].slice(0, SAVED_DECISION_LIMIT);
};

export const jobPending = (job: PipelineJob | undefined) => job?.status === "queued" || job?.status === "running";

/**
 * 저장된 판정: 최초 목록은 Query, 이후 새 판정은 SSE(/api/pipeline/events)로 받아 같은 캐시에 병합.
 * 스트림이 끊기면 3초 뒤 재연결(주기 조회 아님), 세션이 바뀌면 중단한다.
 */
export const useSavedDecisions = () => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  // 이번 연결에서 SSE로 받은 판정. 늦게 도착한 목록 응답이 이 항목을 덮어쓰지 않게 병합한다.
  const streamed = useRef<ProcessDecision[]>([]);
  const query = useAccountQuery({
    name: savedDecisionsQueryName,
    read: async (signal) => mergeDecisionLists(await fetchProcessDecisions(signal), streamed.current),
  });
  const [streamError, setStreamError] = useState("");
  const key = query.queryKey;
  const userId = user?.id;
  useEffect(() => {
    if (!userId) return;
    streamed.current = [];
    const controller = new AbortController();
    const token = getStoredToken();
    let after = "0";
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = async () => {
      try {
        await consumeDecisionStream(controller.signal, after, (value, id) => {
          if (token !== getStoredToken()) return controller.abort();
          after = id;
          streamed.current = mergeDecision(streamed.current, value);
          cache.setQueryData<ProcessDecision[]>(key, (rows) => mergeDecision(rows, value));
        });
        if (!controller.signal.aborted) setStreamError("");
      } catch (reason) {
        if (!controller.signal.aborted) setStreamError(reason instanceof Error ? reason.message : String(reason));
      }
      if (!controller.signal.aborted && token === getStoredToken()) retry = setTimeout(() => void connect(), STREAM_RETRY_MS);
    };
    void connect();
    return () => {
      controller.abort();
      if (retry) clearTimeout(retry);
    };
    // key는 계정 ID(userId)로만 바뀐다.
  }, [cache, userId]);
  return { query, streamError };
};

/** 판정 설명 작업: 요청은 Mutation, 결과 조회는 진행 중(queued·running)일 때만 5초 주기. */
export const useExplainJob = (decision: ProcessDecision) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useAccountQuery({
    name: explainJobQueryName,
    params: [decision.id, jobId],
    read: (signal) => fetchPipelineJob(jobId!, signal),
    enabled: Boolean(jobId),
    staleTime: 0,
    isRunning: jobPending,
  });
  const request = useMutation({
    mutationFn: async () => {
      const token = getStoredToken();
      const created = await createPipelineJob({
        kind: "explain",
        sourceId: decision.sourceId,
        decisionId: decision.id,
      });
      return { created, token };
    },
    onSuccess: ({ created, token }) => {
      if (token !== getStoredToken()) return;
      cache.setQueryData(accountQueryKey(explainJobQueryName, user?.id, decision.id, created.id), created);
      setJobId(created.id);
    },
  });
  return { job, request };
};
