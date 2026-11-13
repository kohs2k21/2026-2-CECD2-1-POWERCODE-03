import { httpClient } from "@/services/api/client";
import type { Actor } from "@/features/detection/rules/ruleSetApi";

/**
 * 운영 설정·모델 교체 API(model-version.md 활성 집합·일관 읽기 절, operations-ui.md 방법별 운영 상태 절). 판단 없이 조회·요청만 한다.
 * 판정 정책(detectionPolicy)은 응답에 있어도 화면에 쓰지 않는다(사용자 11/02 Q-M10).
 */
export type ActiveModel = { configVersionId: string; modelKey: string; displayName: string | null; activatedAtRevision: number };
export type OperationalState = {
  ruleSet: { revision: number; contentHash: string; contentHashVersion: number; savedAt: string; savedBy: Actor };
  activeModelSet: { revision: number; models: ActiveModel[]; changedAt: string; changedBy: Actor; restoredFromRevision: number | null };
  readAt: string;
};
export const readOperationalState = (signal?: AbortSignal) => httpClient.get<OperationalState>("/api/operational-state", { signal });

export type MethodPeriod = "1h" | "24h";
export type MethodStatus = {
  methodId: string;
  category: "hard" | "soft" | "ml";
  modelVersionId: string | null;
  /** 끊김 없이 켜져(활성으로) 있기 시작한 시각. 거슬러 읽을 기록이 없으면 null. */
  enabledSince: string | null;
  runs: number;
  succeeded: number;
  notReady: number;
  failed: number;
  anomalous: number;
  timeouts: number;
  causes: { cause: string; count: number }[];
  lastRunAt: string | null;
  elapsedP95Ms: number | null;
};
export type MethodStatusReport = {
  period: MethodPeriod;
  from: string;
  until: string;
  firstRunWaitMs: number;
  ruleSetRevision: number;
  activeModelSetRevision: number;
  decisionCount: number;
  legacyDecisionCount: number;
  failedWithoutDecision: { count: number; byCode: { code: string; count: number }[] };
  methods: MethodStatus[];
};
export const readMethodStatus = (period: MethodPeriod, signal?: AbortSignal) =>
  httpClient.get<MethodStatusReport>("/api/operations/method-status", { params: { period }, signal });

export type ActiveChange = {
  type: "activated" | "replaced" | "retired" | "restored";
  configVersionId: string;
  modelKey: string;
  displayName: string | null;
  previousConfigVersionId?: string;
  previousDisplayName?: string | null;
  evaluationId?: string;
  acceptedRecommendation?: AcceptedRecommendation | null;
};
export type ActiveHistoryItem = {
  revision: number;
  changedAt: string;
  changedBy: Actor;
  note: string | null;
  restoredFromRevision: number | null;
  changes: ActiveChange[];
  summary: { activated: number; replaced: number; retired: number; restored: number };
  snapshot: { models: ActiveModel[] };
};
export type ActiveHistoryPage = { items: ActiveHistoryItem[]; pagination: { limit: number; hasMore: boolean; nextCursor: string | null } };
export const readActiveHistory = (cursor: string | null, signal?: AbortSignal) =>
  httpClient.get<ActiveHistoryPage>("/api/active-models/history", { params: { cursor: cursor ?? undefined }, signal });

export type AcceptedRecommendation = { comparisonJobId: string; modelKey: string; targetVersionId: string };
export type ActiveSetChanged = { revision: number; previousRevision: number; models: ActiveModel[]; changes: Omit<ActiveChange, "displayName">[]; changedAt: string };

const applications = (configVersionId: string) => `/api/configurations/${encodeURIComponent(configVersionId)}/applications`;
/** 같은 종류 운영 모델이 없으면 추가 운영(apply), 있으면 그 자리 교체(replace). */
export const requestReplace = (target: string, input: { evaluationId: string; expectedRevision: number; baseline: string | null; acceptedRecommendation: AcceptedRecommendation | null }) =>
  httpClient.post<ActiveSetChanged>(applications(target), input.baseline
    ? { operation: "replace", evaluationId: input.evaluationId, expectedRevision: input.expectedRevision, expectedActiveVersionId: input.baseline, ...(input.acceptedRecommendation ? { acceptedRecommendation: input.acceptedRecommendation } : {}) }
    : { operation: "apply", evaluationId: input.evaluationId, expectedRevision: input.expectedRevision, ...(input.acceptedRecommendation ? { acceptedRecommendation: input.acceptedRecommendation } : {}) });
export const requestRetire = (configVersionId: string, expectedRevision: number) =>
  httpClient.post<ActiveSetChanged>(applications(configVersionId), { operation: "retire", expectedRevision, confirmRetire: true });
export const requestRestore = (targetRevision: number, expectedRevision: number, confirmRetire: boolean) =>
  httpClient.post<ActiveSetChanged>("/api/active-models/restore", { operation: "restore", expectedRevision, targetRevision, confirmRetire });
