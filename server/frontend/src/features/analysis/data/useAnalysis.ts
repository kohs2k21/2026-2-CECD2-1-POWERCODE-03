import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { randomUuid } from "@/lib/uuid";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { DECISION_STREAM_RETRY_MS, subscribeDecisionStream } from "@/services/api/decision-stream-hub";
import { createPipelineJob } from "@/services/api/pipeline.api";
import {
  changeDecisionWorkflow,
  editDecisionFeedback,
  listApiParams,
  markDecisionRead,
  readDecisionDetail,
  readDecisionFeedback,
  readDecisionFlow,
  readDecisionGraph,
  readDecisionInterims,
  readDecisionList,
  readDecisionPolished,
  saveDecisionFeedback,
  type DecisionDetail,
  type DecisionPolish,
  type FeedbackKind,
  type FeedbackOutcome,
  type FeedbackRecord,
  type ListParams,
  type WorkflowState,
} from "./analysisApi";
import { readEvidence } from "./evidence";

export const listQueryName = "analysis-decisions";
export const detailQueryName = "analysis-decision";
export const interimsQueryName = "analysis-decision-interims";
export const polishedQueryName = "analysis-decision-polished";
export const flowQueryName = "analysis-decision-flow";
export const graphQueryName = "analysis-decision-graph";
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
/** 실행 그래프(GET …/graph, contracts/process-graph.md). 그래프 보기와 트리 보기("연결 모호" 배지) 모두 조회한다(같은 캐시). */
export const useDecisionGraph = (id: string, enabled: boolean) =>
  useAccountQuery({ name: graphQueryName, params: [id], read: (signal) => readDecisionGraph(id, signal), enabled: Boolean(id) && enabled });
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
    mutationFn: ({ kind, reason, outcome = null }: { kind: FeedbackKind; reason: string; outcome?: FeedbackOutcome | null }) =>
      saveDecisionFeedback(id, kind, reason, outcome),
    onSuccess: () => void cache.invalidateQueries({ queryKey: accountQueryKey(feedbackQueryName, user?.id) }),
  });
};

/**
 * 조사·조치 기록 수정(관리자·최초 작성자, 032). 성공·실패 모두 기록 목록을 다시 읽고(충돌 409면 다른 판본이 보이게),
 * 성공하면 목록(확인 결과별 완료 건수)도 다시 조회한다. 실패는 오류 그대로.
 */
export const useFeedbackEdit = (id: string) => {
  const { user } = useAuthSession();
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({ record, kind, reason, outcome }: { record: FeedbackRecord; kind: FeedbackKind; reason: string; outcome: FeedbackOutcome | null }) =>
      editDecisionFeedback(id, record, kind, reason, outcome),
    onSuccess: () => void cache.invalidateQueries({ queryKey: accountQueryKey(listQueryName, user?.id) }),
    onSettled: () => void cache.invalidateQueries({ queryKey: accountQueryKey(feedbackQueryName, user?.id) }),
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
export const LIVE_RETRY_MS = DECISION_STREAM_RETRY_MS;
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
    const token = getStoredToken();
    let stopped = false;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const invalidate = () => {
      refresh = undefined;
      if (stopped || token !== getStoredToken()) return;
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
    setStatus((current) => (current === "idle" ? "connecting" : current));
    // 알림 배지와 같은 연결을 나눠 쓴다(decision-stream-hub). 재연결(3초)·커서는 연결 쪽이 맡는다.
    const unsubscribe = subscribeDecisionStream({
      after: cursorRef.current!,
      onEvent: () => {
        if (token !== getStoredToken()) return;
        setReceived((count) => count + 1);
        refresh ??= setTimeout(invalidate, LIVE_REFRESH_MS);
      },
      onOpen: () => {
        setStatus("open");
        setError("");
      },
      onDisconnect: (reason) => {
        if (reason !== null) setError(reason);
        setStatus("retrying");
      },
    });
    return () => {
      stopped = true;
      unsubscribe();
      if (refresh) clearTimeout(refresh);
      setStatus("idle");
    };
  }, [cache, ready, userId]);
  return { status, error, received };
};

/**
 * AI 판정 근거 요약 블록 2 다듬기(analysis-ui.md): 버튼 없이 자동으로 요청하고, 다듬은 문단이 오면 그 문단을 돌려준다.
 * - 요청: 서버가 지금 요청해도 된다고 알린(polishRequestable) 이상 신호 최종 판정이고 시연 판정이 아닐 때만, 이 판정에 한 번(새 Idempotency-Key).
 *   요청 실패(503·409 등)는 조용히 넘긴다(다시 요청하지 않음, 코드 문단 그대로)
 * - 읽기: 상세가 pending이거나 방금 요청했으면 GET …/polished를 pending인 동안 5초마다. done·none이 되면 멈춘다. 작업(GET /api/jobs/:id)은 읽지 않는다
 * - 반환: 다듬은 문단(source=llm)이면 그 글자, 아니면 null(코드 문단 그대로)
 */
export const useEvidencePolish = (detail: DecisionDetail): string | null => {
  const demo = Boolean(readEvidence(detail.evidence).demo);
  const eligible = detail.kind === "final" && detail.signal === "yes" && !demo && Boolean(detail.decisionId && detail.sourceId);
  const cache = useQueryClient();
  const requested = useRef<string | null>(null);
  const [sentFor, setSentFor] = useState<string | null>(null);
  const shouldRequest = eligible && detail.polishRequestable && detail.polishState === "none";
  const { id, decisionId, sourceId } = detail;
  // 캐시된 판정 상세(같은 판정, 주소가 작업 id여도)에 다듬기 상태를 반영한다 — 다시 들어왔을 때 다시 요청하거나 코드 문단이 잠깐 보이지 않게.
  const patchDetail = useCallback((patch: Partial<DecisionDetail>) =>
    cache.setQueriesData<DecisionDetail>(
      // predicate로 같은 판정의 쿼리만 고른다(판정 id·작업 id 주소). 다른 판정 상세의 신선도·무효화 표시를 건드리지 않는다
      { queryKey: [detailQueryName], predicate: (query) => (query.state.data as DecisionDetail | undefined)?.id === id },
      (old) => (old ? { ...old, ...patch } : old)), [cache, id]);
  useEffect(() => {
    if (!shouldRequest || requested.current === id) return;
    requested.current = id;
    createPipelineJob({ kind: "explain", sourceId, decisionId }, randomUuid()).then(() => {
      setSentFor(id);
      patchDetail({ polishRequestable: false });
    }, () => patchDetail({ polishRequestable: false }));
  }, [shouldRequest, id, decisionId, sourceId, patchDetail]);
  const watching = eligible && (detail.polishState === "pending" || sentFor === id);
  const polish = useAccountQuery({
    name: polishedQueryName,
    params: [id],
    read: (signal) => readDecisionPolished(id, signal),
    enabled: watching,
    staleTime: 0,
    isRunning: (data: DecisionPolish | undefined) => data?.polishState === "pending",
  });
  const polledState = polish.data?.polishState;
  const polledPolished = polish.data?.polished ?? null;
  useEffect(() => {
    if (polledState === "done" || polledState === "none") patchDetail({ polishState: polledState, polished: polledPolished, polishRequestable: false });
  }, [polledState, polledPolished, patchDetail]);
  const polished = (watching ? polish.data?.polished : null) ?? detail.polished;
  return polished?.source === "llm" ? polished.text : null;
};
