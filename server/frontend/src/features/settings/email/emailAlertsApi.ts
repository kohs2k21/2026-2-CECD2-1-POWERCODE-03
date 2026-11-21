import { HttpError, httpClient } from "@/services/api/client";

/**
 * 메일 알림 설정 API(contracts/email-alerts.md "API" 절, 관리자만). 명시한 필드만 복사하고 머리 값이 틀리면 오류(성공·빈 값으로 바꾸지 않음).
 * 비밀번호는 쓰기 전용: 응답에는 passwordSet만 오고, 화면은 저장된 비밀번호를 받지도 보이지도 않는다.
 */
export const alertKinds = ["incident", "decision"] as const;
export const riskLevels = ["critical", "warning", "info"] as const;
export const smtpSecurities = ["tls", "starttls"] as const;
export const transportStatuses = ["not_configured", "invalid", "configured", "auth_failed", "error"] as const;
export const deliveryStatuses = ["queued", "sending", "sent", "retrying", "failed", "unknown", "suppressed"] as const;
export const deliveryKinds = ["incident", "decision", "test"] as const;
export type AlertKind = (typeof alertKinds)[number];
export type RiskLevel = (typeof riskLevels)[number];
export type SmtpSecurity = (typeof smtpSecurities)[number];
export type TransportStatus = (typeof transportStatuses)[number];
export type DeliveryStatus = (typeof deliveryStatuses)[number];
export type DeliveryKind = (typeof deliveryKinds)[number];

export type EmailAlertSettings = {
  enabled: boolean;
  rules: { kinds: AlertKind[]; minRiskLevel: RiskLevel };
  recipients: string[];
  smtp: { host: string | null; port: number | null; security: SmtpSecurity | null; username: string | null; passwordSet: boolean; fromAddress: string | null };
  publicBaseUrl: string | null;
  transport: { status: TransportStatus; code: string | null; checkedAt: string | null };
  /** 순번은 문자열(bigint). processedThroughSeq null = 아직 켠 적 없음. */
  processing: { processedThroughSeq: string | null; latestSeq: string; armedAt: string | null; lastTickAt: string | null };
  limits: { recipients: number; ports: number[]; security: SmtpSecurity[] };
  isDefault: boolean;
  updatedAt: string | null;
};
export type VerifyResult = { result: "ok" | "auth_failed" | "error" | "skipped"; code: string | null };
export type SavedEmailAlertSettings = { settings: EmailAlertSettings; verify: VerifyResult };
/** PUT 본문. password: 생략 = 저장값 유지, null = 지움, 문자열 = 새 값. */
export type EmailAlertSettingsUpdate = {
  enabled: boolean;
  rules: { kinds: AlertKind[]; minRiskLevel: RiskLevel };
  recipients: string[];
  smtp: { host: string | null; port: number | null; security: SmtpSecurity | null; username: string | null; fromAddress: string | null; password?: string | null };
  publicBaseUrl: string | null;
};
export type TestSendResult = { deliveryId: string; status: "sent" | "failed" | "unknown"; errorCode: string | null; smtpCode: number | null; finishedAt: string | null };
export type Delivery = { id: string; kind: DeliveryKind; recipient: string; status: DeliveryStatus; errorCode: string | null; createdAt: string };
export type DeliveryList = { items: Delivery[]; nextCursor: string | null };

type Obj = Record<string, unknown>;
const obj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const strOrNull = (value: unknown) => (typeof value === "string" ? value : null);
const SEQ = /^(0|[1-9]\d{0,18})$/;
const oneOf = <T extends string>(value: unknown, allowed: readonly T[]): T | null =>
  typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : null;
const fail = (what: string): never => {
  throw new Error(`${what} 응답 형식을 확인할 수 없습니다.`);
};
const WHAT = "메일 알림 설정";

export const parseEmailAlertSettings = (value: unknown): EmailAlertSettings => {
  if (!obj(value) || typeof value.enabled !== "boolean" || !obj(value.rules) || !obj(value.smtp) || !obj(value.transport) || !obj(value.processing)
    || !obj(value.limits) || !Array.isArray(value.recipients)) return fail(WHAT);
  const { rules, smtp, transport, processing, limits } = value;
  const minRiskLevel = oneOf(rules.minRiskLevel, riskLevels);
  if (!Array.isArray(rules.kinds) || !minRiskLevel) return fail(WHAT);
  const kinds = alertKinds.filter((kind) => (rules.kinds as unknown[]).includes(kind));
  const status = oneOf(transport.status, transportStatuses);
  if (!status || typeof smtp.passwordSet !== "boolean" || typeof processing.latestSeq !== "string" || !SEQ.test(processing.latestSeq)) return fail(WHAT);
  const through = processing.processedThroughSeq;
  if (through !== null && (typeof through !== "string" || !SEQ.test(through))) return fail(WHAT);
  const recipientMax = limits.recipients;
  if (!Number.isInteger(recipientMax) || (recipientMax as number) < 1 || !Array.isArray(limits.ports) || !Array.isArray(limits.security)) return fail(WHAT);
  return {
    enabled: value.enabled,
    rules: { kinds, minRiskLevel },
    recipients: value.recipients.filter((entry): entry is string => typeof entry === "string"),
    smtp: {
      host: strOrNull(smtp.host),
      port: Number.isInteger(smtp.port) ? (smtp.port as number) : null,
      security: oneOf(smtp.security, smtpSecurities),
      username: strOrNull(smtp.username),
      passwordSet: smtp.passwordSet,
      fromAddress: strOrNull(smtp.fromAddress),
    },
    publicBaseUrl: strOrNull(value.publicBaseUrl),
    transport: { status, code: strOrNull(transport.code), checkedAt: strOrNull(transport.checkedAt) },
    processing: { processedThroughSeq: through, latestSeq: processing.latestSeq, armedAt: strOrNull(processing.armedAt), lastTickAt: strOrNull(processing.lastTickAt) },
    limits: {
      recipients: recipientMax as number,
      ports: limits.ports.filter((port): port is number => Number.isInteger(port)),
      security: limits.security.filter((entry): entry is SmtpSecurity => oneOf(entry, smtpSecurities) !== null),
    },
    isDefault: value.isDefault === true,
    updatedAt: strOrNull(value.updatedAt),
  };
};

const parseVerify = (value: unknown): VerifyResult => {
  const result = obj(value) ? oneOf(value.result, ["ok", "auth_failed", "error", "skipped"] as const) : null;
  if (!obj(value) || !result) return fail(WHAT);
  return { result, code: strOrNull(value.code) };
};

export const parseTestSendResult = (value: unknown): TestSendResult => {
  const status = obj(value) ? oneOf(value.status, ["sent", "failed", "unknown"] as const) : null;
  if (!obj(value) || !status || typeof value.deliveryId !== "string") return fail("시험 발송");
  return { deliveryId: value.deliveryId, status, errorCode: strOrNull(value.errorCode), smtpCode: Number.isInteger(value.smtpCode) ? (value.smtpCode as number) : null,
    finishedAt: strOrNull(value.finishedAt) };
};

/** 발송 1행. 모르는 종류·상태·빠진 값은 목록에서 뺀다. */
const parseDelivery = (value: unknown): Delivery | null => {
  if (!obj(value) || typeof value.id !== "string" || typeof value.recipient !== "string" || typeof value.createdAt !== "string") return null;
  const kind = oneOf(value.kind, deliveryKinds);
  const status = oneOf(value.status, deliveryStatuses);
  if (!kind || !status) return null;
  return { id: value.id, kind, recipient: value.recipient, status, errorCode: strOrNull(value.errorCode), createdAt: value.createdAt };
};
export const parseDeliveryList = (value: unknown): DeliveryList => {
  if (!obj(value) || !Array.isArray(value.items)) return fail("최근 발송");
  return { items: value.items.map(parseDelivery).filter((item): item is Delivery => item !== null), nextCursor: strOrNull(value.nextCursor) };
};

/** 최근 발송 표에 보이는 행 수(API 기본값과 같음). */
export const RECENT_DELIVERY_LIMIT = 20;

export const readEmailAlertSettings = async (signal?: AbortSignal) =>
  parseEmailAlertSettings(await httpClient.get<unknown>("/api/email-alerts/settings", { signal }));
export const saveEmailAlertSettings = async (body: EmailAlertSettingsUpdate): Promise<SavedEmailAlertSettings> => {
  const saved = await httpClient.put<unknown>("/api/email-alerts/settings", body);
  return { settings: parseEmailAlertSettings(saved), verify: parseVerify(obj(saved) ? saved.verify : undefined) };
};
export const sendTestEmail = async (to: string) => parseTestSendResult(await httpClient.post<unknown>("/api/email-alerts/test", { to }));
export const readRecentDeliveries = async (signal?: AbortSignal) =>
  parseDeliveryList(await httpClient.get<unknown>("/api/email-alerts/deliveries", { signal, params: { limit: RECENT_DELIVERY_LIMIT } }));

/** 오류 코드(서버 code, 없으면 http_<상태>). 화면에는 코드만 보인다(서버 원문 없음). */
export const errorCodeOf = (error: unknown) =>
  error instanceof HttpError ? (error.code ?? `http_${error.status}`) : error instanceof Error ? error.message : "request_failed";

/** 켤 수 있는 전송 상태(contracts/email-alerts.md 5절 "켜기 전제", 아니면 409 email_transport_not_configured). */
export const transportUsable = (status: TransportStatus) => status === "configured" || status === "auth_failed" || status === "error";

/**
 * 받는 사람 주소 검사(서버 normalizeEmail과 같은 규칙, contracts/email-alerts.md 7절): ASCII local@domain, 도메인 라벨 2개 이상·끝 라벨 숫자만 아님,
 * local 64자·전체 254자 이하. 앞뒤 공백 제거·도메인 소문자. 형식이 아니면 null.
 */
const LOCAL = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/;
const LABEL = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
export const normalizeEmail = (value: string): string | null => {
  const text = value.trim();
  // eslint-disable-next-line no-control-regex
  if (!text || text.length > 254 || /[\u0000-\u001f\u007f]/.test(text)) return null;
  const at = text.lastIndexOf("@");
  if (at <= 0 || at !== text.indexOf("@")) return null;
  const local = text.slice(0, at);
  const domain = text.slice(at + 1).toLowerCase();
  const labels = domain.split(".");
  if (local.length > 64 || !LOCAL.test(local) || labels.length < 2 || !labels.every((label) => LABEL.test(label)) || /^\d+$/.test(labels.at(-1)!)) return null;
  return `${local}@${domain}`;
};
export type RecipientProblem = "format" | "duplicate" | "max";
/** 받는 사람 추가 검사: 최대 수 → 형식 → 중복(대소문자 무시). 통과하면 정규화한 주소. */
export const checkRecipient = (input: string, list: readonly string[], max: number): { address: string } | { problem: RecipientProblem } => {
  if (list.length >= max) return { problem: "max" };
  const address = normalizeEmail(input);
  if (!address) return { problem: "format" };
  if (list.some((entry) => entry.toLowerCase() === address.toLowerCase())) return { problem: "duplicate" };
  return { address };
};

/**
 * 사건 묶음 처리 대기(contracts/email-alerts.md 2절 "사건 소속 확정"): 처리 경계가 최신 판정 순번보다 뒤에 있고, 주기 작업이 그 사이 다시 돌았는데도
 * 경계가 같은 자리에 PROCESSING_LAG_MS 이상 머물면 대기로 본다. 회차는 대기가 아니면 읽은 범위를 모두 넘기므로(규칙 미통과 판정도 넘김)
 * 주기 작업이 돈 뒤에도 같은 경계 = 사건 묶음 경계를 기다림. API 응답에 "언제부터 머물렀나"가 없어 화면이 본 조회 사이에서만 잰다(주기 조회 없음).
 */
export const PROCESSING_LAG_MS = 5 * 60_000;
export type ProcessingObservation = { seq: string; since: number; tickAt: string | null };
export const observeProcessing = (
  previous: ProcessingObservation | undefined,
  settings: Pick<EmailAlertSettings, "enabled" | "processing">,
  observedAt: number,
): { observation: ProcessingObservation | undefined; lagging: boolean } => {
  const { processedThroughSeq: seq, latestSeq, lastTickAt } = settings.processing;
  if (!settings.enabled || seq === null || BigInt(seq) >= BigInt(latestSeq)) return { observation: undefined, lagging: false };
  if (!previous || previous.seq !== seq) return { observation: { seq, since: observedAt, tickAt: lastTickAt }, lagging: false };
  const ticked = lastTickAt !== null && lastTickAt !== previous.tickAt;
  return { observation: previous, lagging: ticked && observedAt - previous.since >= PROCESSING_LAG_MS };
};
