import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { consumeDecisionStream } from "@/services/api/decision-stream.api";
import { createPipelineJob, fetchPipelineJob, type PipelineJob } from "@/services/api/pipeline.api";
import {
  changeDecisionWorkflow,
  listApiParams,
  markDecisionRead,
  readDecisionDetail,
  readDecisionFeedback,
  readDecisionFlow,
  readDecisionInterims,
  readDecisionList,
  saveDecisionFeedback,
  type DecisionDetail,
  type FeedbackKind,
  type ListParams,
  type WorkflowState,
} from "./analysisApi";

export const listQueryName = "analysis-decisions";
export const detailQueryName = "analysis-decision";
export const interimsQueryName = "analysis-decision-interims";
export const explainJobQueryName = "analysis-explain-job";
export const flowQueryName = "analysis-decision-flow";
export const feedbackQueryName = "analysis-decision-feedback";

/** 판정 목록(분류 건수 포함). 페이지·조건을 바꾸는 동안 직전 결과를 유지한다. */
export const useDecisionList = (params: ListParams) =>
  useAccountQuery({
    name: listQueryName,
    params: [listApiParams(params)],
    read: (signal) => readDecisionList(params, signal),
    keepPrevious: true,
  });
export const useDecisionDetail = (id: string) =>
  useAccountQuery({ name: detailQueryName, params: [id], read: (signal) => readDecisionDetail(id, signal), enabled: Boolean(id) });
export const useDecisionInterims = (id: string) =>
  useAccountQuery({ name: interimsQueryName, params: [id], read: (signal) => readDecisionInterims(id, signal), enabled: Boolean(id) });

/** Process Flow(조회 시점 저장값). 실시간 재조회 대상 아님(관측 행은 판정 이벤트와 별개로 늘어난다 — 다시 시도로 재조회). */
export const useDecisionFlow = (id: string) =>
  useAccountQuery({ name: flowQueryName, params: [id], read: (signal) => readDecisionFlow(id, signal), enabled: Boolean(id) });
/** 조사·조치 기록: 관리자만 조회(일반 사용자는 요청하지 않는다). */
export const useDecisionFeedback = (id: string, enabled: boolean) =>
  useAccountQuery({ name: feedbackQueryName, params: [id], read: (signal) => readDecisionFeedback(id, signal), enabled: Boolean(id) && enabled });

/** 처리 상태 변경(보류·완료·원래 상태로 복원). 성공하면 상세·목록(분류함 건수)을 다시 조회한다. 실패는 오류 그대로(409 등). */
export const useWorkflowChange = (detail: DecisionDetail) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async (state: WorkflowState) => {
      const token = getStoredToken();
      return { state: await changeDecisionWorkflow(detail.id, state, detail.workflow.state), token };
    },
    onSettled: (result) => {
      if (result && result.token !== getStoredToken()) return;
      void cache.invalidateQueries({ queryKey: accountQueryKey(detailQueryName, user?.id) });
      void cache.invalidateQueries({ queryKey: accountQueryKey(listQueryName, user?.id) });
    },
  });
};

/** 조사·조치 기록 저장(관리자). 성공하면 기록 목록을 다시 조회한다. */
export const useFeedbackSave = (id: string) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, reason }: { kind: FeedbackKind; reason: string }) => saveDecisionFeedback(id, kind, reason),
    onSuccess: () => void cache.invalidateQueries({ queryKey: accountQueryKey(feedbackQueryName, user?.id) }),
  });
};

/**
 * 상세를 열면 요청자 기준 확인 처리(PUT …/read). 같은 항목·같은 항목 시각에는 한 번만 보낸다(새 중간 판정·최종 판정이 생기면 다시).
 * 성공하면 목록의 미확인 표시가 바뀌도록 목록을 다시 조회한다. 실패는 화면을 막지 않는다(미확인 표시만 남음).
 */
export const useMarkRead = (detail: DecisionDetail | undefined) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const sent = useRef<string | null>(null);
  const key = detail?.occurredAt ? `${detail.id}@${detail.occurredAt}` : null;
  const mutation = useMutation({
    mutationFn: async ({ id, occurredAt }: { id: string; occurredAt: string }) => {
      const token = getStoredToken();
      await markDecisionRead(id, occurredAt);
      return token;
    },
    onSuccess: (token) => {
      if (token !== getStoredToken()) return;
      void cache.invalidateQueries({ queryKey: accountQueryKey(listQueryName, user?.id) });
    },
  });
  const { mutate } = mutation;
  useEffect(() => {
    if (!detail?.occurredAt || !key || sent.current === key) return;
    sent.current = key;
    mutate({ id: detail.id, occurredAt: detail.occurredAt });
  }, [detail, key, mutate]);
  return mutation;
};

export type LiveStatus = "idle" | "connecting" | "open" | "retrying";
export const LIVE_RETRY_MS = 3_000;
/** 새 판정 이벤트가 몰려도 목록 재조회는 이 간격에 한 번(이벤트가 있을 때만, 주기 조회 아님). */
export const LIVE_REFRESH_MS = 2_000;

/**
 * 새 판정 실시간 반영: 기존 SSE(/api/pipeline/events?include=interims)를 목록 응답의 `latestEventId` 다음부터 받는다(과거 이벤트 재생 없음).
 * 최종 판정·중간 판정 기록 이벤트가 오면 목록(건수 포함)과 최종 판정이 아닌 상세·판정 기록을 무효화해 다시 조회한다. 주기 조회 없음.
 * 끊기면 3초 뒤 같은 커서로 재연결, 세션이 바뀌면 중단한다.
 */
export const useDecisionLiveUpdates = (cursor: string | null) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("idle");
  const [error, setError] = useState("");
  const [received, setReceived] = useState(0);
  const cursorRef = useRef(cursor);
  cursorRef.current = cursorRef.current ?? cursor;
  const ready = cursorRef.current !== null;
  const userId = user?.id;
  useEffect(() => {
    if (!userId || !ready) return;
    const controller = new AbortController();
    const token = getStoredToken();
    let after = cursorRef.current!;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const invalidate = () => {
      refresh = undefined;
      if (controller.signal.aborted || token !== getStoredToken()) return;
      void cache.invalidateQueries({ queryKey: accountQueryKey(listQueryName, userId) });
      void cache.invalidateQueries({
        queryKey: accountQueryKey(detailQueryName, userId),
        predicate: (query) => (query.state.data as DecisionDetail | undefined)?.kind !== "final",
      });
      void cache.invalidateQueries({
        queryKey: accountQueryKey(interimsQueryName, userId),
        predicate: (query) =>
          (cache.getQueryData<DecisionDetail>(accountQueryKey(detailQueryName, userId, query.queryKey[2]))?.kind ?? "pending") !== "final",
      });
    };
    const connect = async () => {
      setStatus((current) => (current === "idle" ? "connecting" : current));
      try {
        const receive = (id: string) => {
          if (token !== getStoredToken()) return controller.abort();
          after = id;
          setReceived((count) => count + 1);
          refresh ??= setTimeout(invalidate, LIVE_REFRESH_MS);
        };
        await consumeDecisionStream(
          controller.signal,
          after,
          (_value, id) => receive(id),
          () => {
            setStatus("open");
            setError("");
          },
          (_event, id) => receive(id),
        );
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      }
      if (!controller.signal.aborted && token === getStoredToken()) {
        setStatus("retrying");
        retry = setTimeout(() => void connect(), LIVE_RETRY_MS);
      }
    };
    void connect();
    return () => {
      controller.abort();
      if (retry) clearTimeout(retry);
      if (refresh) clearTimeout(refresh);
      setStatus("idle");
    };
  }, [cache, ready, userId]);
  return { status, error, received };
};

/** 진행 중(주기 조회·요청 중 표시) = queued·running만. blocked 등 그 밖의 상태는 주기 조회 없이 상태 그대로 보인다. */
export const jobPending = (job: PipelineJob | undefined) => job?.status === "queued" || job?.status === "running";

/**
 * 판정 설명(POST /api/jobs {kind:"explain"}): 최종 판정만. 결과 조회는 진행 중(queued·running)일 때만 5초 주기.
 * 상세 응답의 `explanation`(요청자 본인의 최근 설명 작업)이 있으면 그 작업을 이어서 보여 준다.
 */
export const useExplainJob = (detail: DecisionDetail) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const [requested, setRequested] = useState<string | null>(null);
  const jobId = requested ?? detail.explanation?.jobId ?? null;
  const job = useAccountQuery({
    name: explainJobQueryName,
    params: [jobId],
    read: (signal) => fetchPipelineJob(jobId!, signal),
    enabled: Boolean(jobId),
    staleTime: 0,
    isRunning: jobPending,
  });
  const request = useMutation({
    mutationFn: async () => {
      const token = getStoredToken();
      if (!detail.decisionId || !detail.sourceId) throw new Error("최종 판정 후 요청할 수 있습니다.");
      const created = await createPipelineJob({ kind: "explain", sourceId: detail.sourceId, decisionId: detail.decisionId });
      return { created, token };
    },
    onSuccess: ({ created, token }) => {
      if (token !== getStoredToken()) return;
      cache.setQueryData(accountQueryKey(explainJobQueryName, user?.id, created.id), created);
      setRequested(created.id);
      // 설명 작업 연결은 상세 응답에도 반영되도록 다시 조회한다.
      void cache.invalidateQueries({ queryKey: accountQueryKey(detailQueryName, user?.id, detail.id) });
    },
  });
  return { jobId, job, request };
};
