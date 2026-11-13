import { httpClient } from "@/services/api/client";

/**
 * 사용자별 설정·알림 API(contracts/notifications.md) 요청·응답. 명시한 필드만 복사하고 형식이 틀린 항목은 버린다(필수 머리 값이 틀리면 오류).
 * 오류(400·401·503)는 httpClient가 HttpError로 던진다. 실패를 성공·빈 목록으로 바꾸지 않는다.
 */
export const decisionBases = ["interim", "final_any", "final_complete"] as const;
export const riskFloors = ["critical", "warning", "info"] as const;
export type DecisionBasis = (typeof decisionBases)[number];
export type RiskFloor = (typeof riskFloors)[number];
export type NotificationSettings = { decisionBasis: DecisionBasis; minRiskLevel: RiskFloor; includeUnrated: boolean };
export const notificationViews = ["unread", "all"] as const;
export type NotificationView = (typeof notificationViews)[number];

export type NotificationReason = "opened" | "risk_raised" | "withdrawn" | "grew";
export type DecisionStage = "interim" | "updated" | "final" | "superseded" | "no_final" | "cleared";
type Risk = { riskStatus: "rated" | "unrated"; riskLevel: RiskFloor | null; hasUnratedEvidence: boolean };
type Base = { key: string; seq: string; notifiedAt: string; unread: boolean };
export type DecisionNotification = Base & {
  type: "decision";
  reason: Exclude<NotificationReason, "grew">;
  decision: Risk & {
    /** 상세 주소 id(최종 판정 ID, 없으면 detect 작업 ID — analysis-ui.md). */
    detailId: string;
    decisionId: string | null;
    jobId: string | null;
    stage: DecisionStage;
    completeness: "complete" | "partial" | "unavailable" | null;
    grade: "anomaly" | "anomaly_sign" | "normal" | "undetermined" | null;
    processId: string | null;
    transactionId: string | null;
  };
};
export type IncidentNotification = Base & {
  type: "incident";
  reason: Exclude<NotificationReason, "withdrawn">;
  incident: Risk & {
    id: string;
    openedAt: string | null;
    criteria: { kind: string | null; value: string | null; longValue: boolean };
    memberDecisions: number;
  };
};
export type NotificationItem = DecisionNotification | IncidentNotification;
export type NotificationList = {
  latestSeq: string;
  readThrough: string;
  window: { events: number; fromSeq: string; truncated: boolean };
  settings: NotificationSettings;
  unreadCount: number;
  total: number;
  nextCursor: string | null;
  items: NotificationItem[];
};
export type UnreadCount = { latestSeq: string; unreadCount: number };
export type StoredLayout = { version: number; items: { i: string; x: number; y: number; w: number; h: number }[] };
export type MySettings = {
  notifications: NotificationSettings & { isDefault: boolean; updatedAt: string | null };
  widgetLayout: { layout: StoredLayout | null; updatedAt: string | null };
};

type Obj = Record<string, unknown>;
const obj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown) => (typeof value === "string" ? value : null);
const SEQ = /^(0|[1-9]\d{0,18})$/;
const oneOf = <T extends string>(value: unknown, allowed: readonly T[]): T | null =>
  typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : null;
const fail = (what: string): never => {
  throw new Error(`${what} 응답 형식을 확인할 수 없습니다.`);
};

export const parseNotificationSettings = (value: unknown, what = "알림 설정"): NotificationSettings => {
  if (!obj(value)) return fail(what);
  const decisionBasis = oneOf(value.decisionBasis, decisionBases);
  const minRiskLevel = oneOf(value.minRiskLevel, riskFloors);
  if (!decisionBasis || !minRiskLevel || typeof value.includeUnrated !== "boolean") return fail(what);
  return { decisionBasis, minRiskLevel, includeUnrated: value.includeUnrated };
};

const parseRisk = (value: Obj): Risk | null => {
  const riskStatus = oneOf(value.riskStatus, ["rated", "unrated"] as const);
  if (!riskStatus) return null;
  const riskLevel = oneOf(value.riskLevel, riskFloors);
  if (riskStatus === "rated" && !riskLevel) return null;
  return { riskStatus, riskLevel: riskStatus === "rated" ? riskLevel : null, hasUnratedEvidence: value.hasUnratedEvidence === true };
};

/** 항목 1개. 키·순번·사유·대상 id가 맞지 않으면 null(목록에서 뺌). */
export const parseNotificationItem = (value: unknown): NotificationItem | null => {
  if (!obj(value) || typeof value.key !== "string" || !SEQ.test(String(value.seq)) || typeof value.notifiedAt !== "string") return null;
  const base = { key: value.key, seq: String(value.seq), notifiedAt: value.notifiedAt, unread: value.unread === true };
  if (value.type === "decision" && obj(value.decision) && /^(job|decision):/.test(value.key)) {
    const d = value.decision;
    const reason = oneOf(value.reason, ["opened", "risk_raised", "withdrawn"] as const);
    const stage = oneOf(d.stage, ["interim", "updated", "final", "superseded", "no_final", "cleared"] as const);
    const risk = parseRisk(d);
    if (!reason || !stage || !risk || typeof d.detailId !== "string" || !d.detailId) return null;
    return { ...base, type: "decision", reason, decision: { ...risk, detailId: d.detailId, decisionId: str(d.decisionId), jobId: str(d.jobId), stage,
      completeness: oneOf(d.completeness, ["complete", "partial", "unavailable"] as const),
      grade: oneOf(d.grade, ["anomaly", "anomaly_sign", "normal", "undetermined"] as const), processId: str(d.processId), transactionId: str(d.transactionId) } };
  }
  if (value.type === "incident" && obj(value.incident) && value.key.startsWith("incident:")) {
    const i = value.incident;
    const reason = oneOf(value.reason, ["opened", "risk_raised", "grew"] as const);
    const risk = parseRisk(i);
    const criteria = obj(i.criteria) ? i.criteria : {};
    if (!reason || !risk || typeof i.id !== "string" || !i.id || !Number.isInteger(i.memberDecisions) || (i.memberDecisions as number) < 1) return null;
    return { ...base, type: "incident", reason, incident: { ...risk, id: i.id, openedAt: str(i.openedAt), memberDecisions: i.memberDecisions as number,
      criteria: { kind: str(criteria.kind), value: str(criteria.value), longValue: criteria.longValue === true } } };
  }
  return null;
};

export const parseNotificationList = (value: unknown): NotificationList => {
  if (!obj(value) || !SEQ.test(String(value.latestSeq)) || !SEQ.test(String(value.readThrough)) || !Array.isArray(value.items)
    || !Number.isInteger(value.unreadCount) || !Number.isInteger(value.total) || !obj(value.window)) return fail("알림 목록");
  const w = value.window;
  return {
    latestSeq: String(value.latestSeq),
    readThrough: String(value.readThrough),
    window: { events: Number(w.events) || 0, fromSeq: String(w.fromSeq ?? "1"), truncated: w.truncated === true },
    settings: parseNotificationSettings(value.settings, "알림 목록"),
    unreadCount: value.unreadCount as number,
    total: value.total as number,
    nextCursor: typeof value.nextCursor === "string" && /^[A-Za-z0-9_-]+$/.test(value.nextCursor) ? value.nextCursor : null,
    items: value.items.map(parseNotificationItem).filter((item): item is NotificationItem => item !== null),
  };
};

export const parseUnreadCount = (value: unknown): UnreadCount => {
  if (!obj(value) || !SEQ.test(String(value.latestSeq)) || !Number.isInteger(value.unreadCount) || (value.unreadCount as number) < 0) return fail("알림 수");
  return { latestSeq: String(value.latestSeq), unreadCount: value.unreadCount as number };
};

const parseLayout = (value: unknown): StoredLayout | null => {
  if (!obj(value) || !Number.isInteger(value.version) || !Array.isArray(value.items)) return null;
  return { version: value.version as number, items: value.items.filter(obj).map((item) => ({ i: String(item.i), x: Number(item.x), y: Number(item.y), w: Number(item.w), h: Number(item.h) })) };
};
export const parseMySettings = (value: unknown): MySettings => {
  if (!obj(value) || !obj(value.notifications) || !obj(value.widgetLayout)) return fail("설정");
  const n = value.notifications;
  return {
    notifications: { ...parseNotificationSettings(n, "설정"), isDefault: n.isDefault === true, updatedAt: str(n.updatedAt) },
    widgetLayout: { layout: parseLayout(value.widgetLayout.layout), updatedAt: str(value.widgetLayout.updatedAt) },
  };
};

export const readNotificationList = async (params: { view: NotificationView; cursor: string | null; limit?: number }, signal?: AbortSignal) =>
  parseNotificationList(await httpClient.get<unknown>("/api/notifications", {
    params: { view: params.view, limit: params.limit ?? 20, cursor: params.cursor ?? undefined }, signal }));
export const readUnreadCount = async (signal?: AbortSignal) => parseUnreadCount(await httpClient.get<unknown>("/api/notifications/unread-count", { signal }));
export type ReadRequest = { through: string } | { items: { key: string; seq: string }[] };
export const markNotificationsRead = async (body: ReadRequest) => {
  const value = await httpClient.post<unknown>("/api/notifications/read", body);
  const count = parseUnreadCount(value);
  return { ...count, readThrough: obj(value) && SEQ.test(String(value.readThrough)) ? String(value.readThrough) : "0" };
};
export const readMySettings = async (signal?: AbortSignal) => parseMySettings(await httpClient.get<unknown>("/api/me/settings", { signal }));
export const saveMySettings = async (body: { notifications?: NotificationSettings; widgetLayout?: StoredLayout }) =>
  parseMySettings(await httpClient.put<unknown>("/api/me/settings", body));
