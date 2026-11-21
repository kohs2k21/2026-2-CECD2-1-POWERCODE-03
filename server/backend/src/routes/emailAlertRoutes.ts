import express, { Router, type NextFunction, type Request, type Response } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, type AuthenticatedRequest } from '../middleware/auth.js';
import { EmailSettingsError, normalizeEmail, parseSettingsUpdate } from '../services/emailAlerts/config.js';
import { TestSendError, sendTest, verifyTransport, type SendDeps } from '../services/emailAlerts/outbox.js';
import { defaultKeySource } from '../services/emailAlerts/secretBox.js';
import { readEmailSettings, updateEmailSettings } from '../services/emailAlerts/settingsStore.js';
import { createNodemailerFactory } from '../services/emailAlerts/transport.js';

/**
 * 메일 알림 API(contracts/email-alerts.md "API" 절). 모두 관리자만(authenticateToken → requireAdmin, 일반 사용자 403 admin_required).
 * - 본문은 JSON만, 16KB 상한(앱 공통 express.json보다 먼저 연결). Bearer 인증만 써서(쿠키 없음) CSRF 토큰은 없다(notificationRoutes와 같음).
 * - 비밀번호는 응답·오류·로그에 넣지 않는다(오류 본문은 {code[, index]}만). 저장소 오류는 503 email_alert_storage_unavailable(원문 비노출, 성공 대체 없음).
 */
const BODY_ERROR_CODES: Record<string, string> = {
  'entity.parse.failed': 'invalid_json',
  'entity.too.large': 'request_too_large',
  'charset.unsupported': 'unsupported_charset',
  'encoding.unsupported': 'unsupported_encoding',
  'request.aborted': 'request_aborted',
};
export const DELIVERY_LIST_LIMITS = { default: 20, max: 50 } as const;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const AT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;

const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
  (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };
const noQuery = (req: Request) => { if (Object.keys(req.query).length) throw new EmailSettingsError('unknown_email_alert_filter'); };
const plainObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

/** 발송 목록 커서 = base64url JSON [만든 시각(UTC 마이크로초 6자리), 발송 id]. */
export const encodeDeliveryCursor = (at: string, id: string) => Buffer.from(JSON.stringify([at, id]), 'utf8').toString('base64url');
export function decodeDeliveryCursor(value: string): { at: string; id: string } | null {
  if (value.length > 200 || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const parsed = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
    if (Array.isArray(parsed) && parsed.length === 2 && typeof parsed[0] === 'string' && AT.test(parsed[0]) && typeof parsed[1] === 'string' && UUID.test(parsed[1])) {
      return { at: parsed[0], id: parsed[1] };
    }
  } catch { /* 아래 null */ }
  return null;
}

/** GET /api/email-alerts/deliveries: 최근 발송(만든 시각 → id 내림차순 키셋). payload 전체는 내지 않고 대상 id·위험도만. */
async function readDeliveries(db: Pick<Pool, 'query'>, limit: number, cursor: { at: string; id: string } | null) {
  const { rows } = await db.query(`SELECT d.id,d.kind,d.reason,a.alert_key,coalesce(d.payload->>'incidentId',d.payload->>'decisionId') AS target_id,
      d.payload->>'riskLevel' AS risk_level,d.recipient,d.status,d.attempts,d.error_code,d.smtp_code,
      ${ISO('d.created_at')} AS created_at,${ISO('d.sent_at')} AS sent_at,${ISO('d.finished_at')} AS finished_at,${ISO('d.next_attempt_at')} AS next_attempt_at
    FROM ops_email_deliveries d LEFT JOIN ops_email_alerts a ON a.id=d.alert_id
    WHERE $1::timestamptz IS NULL OR (d.created_at,d.id)<($1::timestamptz,$2::uuid)
    ORDER BY d.created_at DESC,d.id DESC LIMIT $3`, [cursor?.at ?? null, cursor?.id ?? null, limit + 1]);
  const page = rows.slice(0, limit);
  const last = page.at(-1);
  return {
    items: page.map((row) => ({ id: row.id, kind: row.kind, reason: row.reason, alertKey: row.alert_key ?? null, targetId: row.target_id ?? null,
      riskLevel: row.risk_level ?? null, recipient: row.recipient, status: row.status, attempts: row.attempts, errorCode: row.error_code ?? null,
      smtpCode: row.smtp_code ?? null, createdAt: row.created_at, sentAt: row.sent_at ?? null, finishedAt: row.finished_at ?? null,
      nextAttemptAt: row.status === 'retrying' ? row.next_attempt_at : null })),
    nextCursor: rows.length > limit && last ? encodeDeliveryCursor(last.created_at, last.id) : null,
  };
}

export const defaultEmailDeps = (): SendDeps => ({
  factory: createNodemailerFactory(),
  keys: defaultKeySource((code) => console.error(`[email-alerts] ${code}`)),
});

/** /api/email-alerts: GET·PUT /settings, POST /test, GET /deliveries. */
export function createEmailAlertRouter({ database = getDatabasePool, deps = defaultEmailDeps() }: { database?: () => Pool; deps?: SendDeps } = {}) {
  const router = Router();
  router.use(authenticateToken as any, requireAdmin as any);
  router.use(express.json({ limit: '16kb', strict: true }));
  router.get('/settings', route(async (req, res) => {
    noQuery(req);
    res.json(await readEmailSettings(database(), deps.keys));
  }));
  router.put('/settings', route(async (req, res) => {
    noQuery(req);
    if (!req.is('application/json')) throw new EmailSettingsError('invalid_email_settings');
    const update = parseSettingsUpdate(req.body);
    const { smtpChanged } = await updateEmailSettings(database(), req.user!.id, update, deps.keys);
    // 저장 뒤 연결 확인(SMTP 값이 바뀌었을 때만). 실패해도 저장값은 그대로 두고 결과를 응답과 전송 상태에 보인다.
    const verify = smtpChanged ? await verifyTransport(database(), deps) : { result: 'skipped' as const, code: null };
    res.json({ ...(await readEmailSettings(database(), deps.keys)), verify });
  }));
  router.post('/test', route(async (req, res) => {
    noQuery(req);
    const body = req.body;
    if (!req.is('application/json') || !plainObject(body) || Object.keys(body).join() !== 'to') throw new EmailSettingsError('invalid_test_request');
    const to = normalizeEmail(body.to);
    if (!to) throw new EmailSettingsError('invalid_test_request');
    res.json(await sendTest(database(), req.user!.id, to, deps));
  }));
  router.get('/deliveries', route(async (req, res) => {
    if (Object.keys(req.query).some((key) => key !== 'limit' && key !== 'cursor')) throw new EmailSettingsError('unknown_email_alert_filter');
    const limitText = req.query.limit;
    if (limitText !== undefined && (typeof limitText !== 'string' || !/^[1-9]\d{0,1}$/.test(limitText) || Number(limitText) > DELIVERY_LIST_LIMITS.max)) {
      throw new EmailSettingsError('invalid_limit');
    }
    const cursorText = req.query.cursor;
    if (cursorText !== undefined && typeof cursorText !== 'string') throw new EmailSettingsError('invalid_cursor');
    const cursor = cursorText === undefined ? null : decodeDeliveryCursor(cursorText);
    if (cursorText !== undefined && !cursor) throw new EmailSettingsError('invalid_cursor');
    res.json(await readDeliveries(database(), limitText === undefined ? DELIVERY_LIST_LIMITS.default : Number(limitText), cursor));
  }));
  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof EmailSettingsError) { res.status(error.status).json(error.index === undefined ? { code: error.code } : { code: error.code, index: error.index }); return; }
    if (error instanceof TestSendError) { res.status(error.status).json({ code: error.code }); return; }
    const parse = error as { type?: string; status?: number; statusCode?: number };
    const status = parse?.status ?? parse?.statusCode;
    if (typeof status === 'number' && status >= 400 && status < 500) {
      res.status(status).json({ code: BODY_ERROR_CODES[parse.type ?? ''] ?? 'invalid_request' });
      return;
    }
    res.status(503).json({ code: 'email_alert_storage_unavailable' });
  });
  return router;
}
