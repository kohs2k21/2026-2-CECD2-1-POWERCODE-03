import { httpClient } from "@/services/api/client";

/**
 * 사용자별 설정·알림 API(contracts/notifications.md) 요청·응답. 명시한 필드만 복사하고 형식이 틀린 항목은 버린다(필수 머리 값이 틀리면 오류).
 * 오류(400·401·503)는 httpClient가 HttpError로 던진다. 실패를 성공·빈 목록으로 바꾸지 않는다.
 */
export const decisionBases = ["interim", "final_any", "final_complete"] as const;
export const riskFloors = ["critical", "warning", "info"] as const;
export type DecisionBasis = (typeof decisionBases)[number];
export type RiskFloor = (typeof riskFloors)[number];
/** 알림 조회 기간(일): 기본 7, 1~30 정수(contracts/notifications.md "API" 절). */
export const NOTIFICATION_WINDOW_DAYS = { default: 7, min: 1, max: 30 } as const;
export const isWindowDays = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= NOTIFICATION_WINDOW_DAYS.min && value <= NOTIFICATION_WINDOW_DAYS.max;
/** includeJobs: 작업 알림(학습·비교 평가 종료) 켜기. 서버는 관리자에게만 작업 알림을 만든다(설정 화면도 관리자만 보인다). */
export type NotificationSettings = { decisionBasis: DecisionBasis; minRiskLevel: RiskFloor; includeUnrated: boolean; windowDays: number; includeJobs: boolean };
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
    /** 마지막 최종 판정의 이상 신호(analysis-ui signal). 최종이 없으면 null. */
    finalSignal: "yes" | "no" | "unknown" | null;
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
/** 프로토콜 알림(contracts/protocol-settings-ui.md 5절): (인터페이스, 경로)마다 1개, 처음 나온 판정 1번. pathKey null = 경로 없는 미등록 인터페이스 알림. */
export type ProtocolNotification = Base & {
  type: "protocol";
  reason: "first_seen";
  protocol: {
    kind: "path_unmatched" | "interface_unregistered";
    interfaceId: string;
    pathKey: string | null;
    channels: string[] | null;
    routesReason: string | null;
    protocolRuleId: string | null;
    /** 가장 최근 판정(판정 상세 주소). */
    decisionId: string;
    decisions: number;
    /** kept = [이상 유지], added = 현재 룰 세트에 있음(옛 알림 "추가됨"), null = 처리 전. */
    resolution: "kept" | "added" | null;
    keptAt: string | null;
    processId: string | null;
    transactionId: string | null;
  };
};
/** 작업 알림(contracts/notifications.md "작업 알림" 절): 학습·비교 평가가 완료·실패로 끝난 작업 1건. 관리자만. */
export type JobNotification = Base & {
  type: "job";
  reason: "finished";
  job: {
    id: string;
    kind: "train" | "compare";
    status: "succeeded" | "failed";
    errorCode: string | null;
    finishedAt: string;
    /** 학습 결과 모델 버전(학습 완료만). */
    configVersionId: string | null;
    /** 비교 평가 실패 항목 수(비교 평가만, 없으면 null). */
    failedCount: number | null;
    /** 비교 평가 완료에 교체 추천 대상이 있음. */
    replacementRecommended: boolean;
  };
};
export type NotificationItem = DecisionNotification | IncidentNotification | ProtocolNotification | JobNotification;
export type NotificationList = {
  latestSeq: string;
  /** 작업 알림 대상 중 가장 늦은 종료 시각(모두 읽음 jobsThrough로 보냄). 작업 알림이 없거나 꺼졌으면 null. */
  latestJobAt: string | null;
  readThrough: string;
  /** truncatedBy: 범위를 정한 쪽 — days(조회 기간) / events(최근 이벤트 수 상한) / null(잘림 없음). */
  window: { events: number; days: number; fromSeq: string; truncated: boolean; truncatedBy: "days" | "events" | null };
  settings: NotificationSettings;
  unreadCount: number;
  total: number;
  nextCursor: string | null;
  items: NotificationItem[];
};
/** 종류별 미확인 수(합 = unreadCount). 상단 메뉴 이상 분석 배지 = decision + incident, 탐지 설정 배지 = job. 일반 사용자는 protocol·job 0. */
export type UnreadByType = { decision: number; incident: number; protocol: number; job: number };
export type UnreadCount = { latestSeq: string; latestJobAt: string | null; unreadCount: number; unreadByType: UnreadByType };
export type StoredLayout = { version: number; items: { i: string; x: number; y: number; w: number; h: number }[] };
export type MySettings = {
  notifications: NotificationSettings & { isDefault: boolean; updatedAt: string | null };
  widgetLayout: { layout: StoredLayout | null; updatedAt: string | null };
};

type Obj = Record<string, unknown>;
const obj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown) => (typeof value === "string" ? value : null);
const SEQ = /^(0|[1-9]\d{0,18})$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** 작업 알림 시각(서버 형식 그대로: UTC 마이크로초 6자리). 형식이 다르면 null(모두 읽음에 보내지 않음). */
const jobAt = (value: unknown) => (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(value) ? value : null);
const oneOf = <T extends string>(value: unknown, allowed: readonly T[]): T | null =>
  typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : null;
const count = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;
const fail = (what: string): never => {
  throw new Error(`${what} 응답 형식을 확인할 수 없습니다.`);
};

export const parseNotificationSettings = (value: unknown, what = "알림 설정"): NotificationSettings => {
  if (!obj(value)) return fail(what);
  const decisionBasis = oneOf(value.decisionBasis, decisionBases);
  const minRiskLevel = oneOf(value.minRiskLevel, riskFloors);
  if (!decisionBasis || !minRiskLevel || typeof value.includeUnrated !== "boolean" || !isWindowDays(value.windowDays) || typeof value.includeJobs !== "boolean") return fail(what);
  return { decisionBasis, minRiskLevel, includeUnrated: value.includeUnrated, windowDays: value.windowDays, includeJobs: value.includeJobs };
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
      finalSignal: oneOf(d.finalSignal, ["yes", "no", "unknown"] as const),
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
  if (value.type === "protocol" && obj(value.protocol) && /^protocol:[0-9a-f]{64}$/.test(value.key) && value.reason === "first_seen") {
    const p = value.protocol;
    const kind = oneOf(p.kind, ["path_unmatched", "interface_unregistered"] as const);
    const channels = Array.isArray(p.channels) && p.channels.length > 0 && p.channels.every((item) => typeof item === "string") ? (p.channels as string[]) : null;
    const pathKey = str(p.pathKey);
    if (!kind || typeof p.interfaceId !== "string" || !p.interfaceId || typeof p.decisionId !== "string" || !p.decisionId
      || (pathKey === null) !== (channels === null) || !Number.isInteger(p.decisions) || (p.decisions as number) < 1) return null;
    return { ...base, type: "protocol", reason: "first_seen", protocol: { kind, interfaceId: p.interfaceId, pathKey, channels, routesReason: str(p.routesReason),
      protocolRuleId: str(p.protocolRuleId), decisionId: p.decisionId, decisions: p.decisions as number,
      resolution: oneOf(p.resolution, ["kept", "added"] as const), keptAt: str(p.keptAt), processId: str(p.processId), transactionId: str(p.transactionId) } };
  }
  if (value.type === "job" && obj(value.job) && value.reason === "finished" && /^work:/.test(value.key)) {
    const j = value.job;
    const kind = oneOf(j.kind, ["train", "compare"] as const);
    const status = oneOf(j.status, ["succeeded", "failed"] as const);
    if (!kind || !status || typeof j.id !== "string" || !UUID.test(j.id) || value.key !== `work:${j.id}`) return null;
    return { ...base, type: "job", reason: "finished", job: { id: j.id, kind, status, errorCode: str(j.errorCode), finishedAt: str(j.finishedAt) ?? value.notifiedAt,
      configVersionId: str(j.configVersionId), failedCount: count(j.failedCount) ? j.failedCount : null, replacementRecommended: j.replacementRecommended === true } };
  }
  return null;
};

export const parseNotificationList = (value: unknown): NotificationList => {
  if (!obj(value) || !SEQ.test(String(value.latestSeq)) || !SEQ.test(String(value.readThrough)) || !Array.isArray(value.items)
    || !Number.isInteger(value.unreadCount) || !Number.isInteger(value.total) || !obj(value.window)) return fail("알림 목록");
  const w = value.window;
  return {
    latestSeq: String(value.latestSeq),
    latestJobAt: jobAt(value.latestJobAt),
    readThrough: String(value.readThrough),
    window: { events: Number(w.events) || 0, days: Number(w.days) || 0, fromSeq: String(w.fromSeq ?? "1"), truncated: w.truncated === true,
      truncatedBy: oneOf(w.truncatedBy, ["days", "events"] as const) },
    settings: parseNotificationSettings(value.settings, "알림 목록"),
    unreadCount: value.unreadCount as number,
    total: value.total as number,
    nextCursor: typeof value.nextCursor === "string" && /^[A-Za-z0-9_-]+$/.test(value.nextCursor) ? value.nextCursor : null,
    items: value.items.map(parseNotificationItem).filter((item): item is NotificationItem => item !== null),
  };
};

/** 배지 응답(미확인 수 조회·읽음·이상 유지 응답 공통). 종류별 수가 없거나 합이 맞지 않으면 형식 오류(배지 확인 불가). */
export const parseUnreadCount = (value: unknown): UnreadCount => {
  if (!obj(value) || !SEQ.test(String(value.latestSeq)) || !count(value.unreadCount) || !obj(value.unreadByType)) return fail("알림 수");
  const { decision, incident, protocol, job } = value.unreadByType;
  if (!count(decision) || !count(incident) || !count(protocol) || !count(job) || decision + incident + protocol + job !== value.unreadCount) return fail("알림 수");
  return { latestSeq: String(value.latestSeq), latestJobAt: jobAt(value.latestJobAt), unreadCount: value.unreadCount, unreadByType: { decision, incident, protocol, job } };
};
/** 상단 메뉴 이상 분석 배지 수: 판정·사건 알림 미확인(프로토콜 알림은 넣지 않음, 와이어프레임 badgeAnalysis = 판정 알림). */
export const analysisUnread = (value: UnreadCount) => value.unreadByType.decision + value.unreadByType.incident;
/** 상단 메뉴 탐지 설정 배지 수: 작업 알림 미확인(와이어프레임 badgeDetect, 관리자만 — 일반 사용자는 서버가 0). */
export const detectionUnread = (value: UnreadCount) => value.unreadByType.job;
/** 모두 읽음 본문: 판정 순번과(있으면) 작업 알림 경계를 같은 배지 응답에서 보낸다(본 것까지만 읽음). */
export const readAllRequest = (value: Pick<UnreadCount, "latestSeq" | "latestJobAt">): ReadRequest =>
  value.latestJobAt ? { through: value.latestSeq, jobsThrough: value.latestJobAt } : { through: value.latestSeq };

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
export type ReadRequest = { through: string; jobsThrough?: string } | { items: { key: string; seq: string }[] };
export const markNotificationsRead = async (body: ReadRequest) => {
  const value = await httpClient.post<unknown>("/api/notifications/read", body);
  const count = parseUnreadCount(value);
  return { ...count, readThrough: obj(value) && SEQ.test(String(value.readThrough)) ? String(value.readThrough) : "0" };
};
/** [이상 유지](관리자): 룰 세트·판정은 바꾸지 않고 같은 (인터페이스, 경로)를 다시 알리지 않는다. */
export const keepProtocolAlert = async (body: { interfaceId: string; pathKey: string | null }) => {
  const value = await httpClient.post<unknown>("/api/notifications/protocol-alerts/keep", body);
  return { ...parseUnreadCount(value), alreadyKept: obj(value) && value.alreadyKept === true };
};
export const readMySettings = async (signal?: AbortSignal) => parseMySettings(await httpClient.get<unknown>("/api/me/settings", { signal }));
export const saveMySettings = async (body: { notifications?: NotificationSettings; widgetLayout?: StoredLayout }) =>
  parseMySettings(await httpClient.put<unknown>("/api/me/settings", body));
