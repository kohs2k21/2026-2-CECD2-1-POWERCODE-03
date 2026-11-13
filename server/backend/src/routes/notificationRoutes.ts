import express, { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth.js';
import {
  NOTIFICATION_LIST_LIMITS, NOTIFICATION_READ_ITEMS_MAX, NOTIFICATION_VIEWS, decodeCursor, isItemKey, isSeq,
  markNotificationsRead, readNotifications, readUnreadCount, type NotificationView, type ReadRequest,
} from '../services/notificationReadModel.js';
import { SettingsRequestError, loadNotificationSettings, parseSettingsUpdate, readSettings, updateSettings } from '../services/userSettings.js';

/**
 * 사용자별 설정·알림 API(contracts/notifications.md). user·admin 모두 자기 계정 것만 읽고 쓴다(요청 계정 = 인증 토큰의 계정 id).
 * - Bearer 인증만 쓰므로(쿠키 없음) 별도 CSRF 토큰은 없다(analysisRoutes와 같음). 본문은 JSON만, 16KB 상한(앱 공통 express.json보다 먼저 연결).
 * - 저장소 오류는 성공·빈 목록으로 바꾸지 않고 503 notification_storage_unavailable(원문 비노출).
 */
const BODY_ERROR_CODES: Record<string, string> = {
  'entity.parse.failed': 'invalid_json',
  'entity.too.large': 'request_too_large',
  'charset.unsupported': 'unsupported_charset',
  'encoding.unsupported': 'unsupported_encoding',
  'request.aborted': 'request_aborted',
};
const plainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const single = (req: Request, key: string, code: string): string | undefined => {
  const value = req.query[key];
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new SettingsRequestError(code);
  return value;
};
const allowOnly = (req: Request, keys: string[]) => {
  if (Object.keys(req.query).some((key) => !keys.includes(key))) throw new SettingsRequestError('unknown_notification_filter');
};

export function parseNotificationQuery(req: Request) {
  allowOnly(req, ['view', 'limit', 'cursor']);
  const view = single(req, 'view', 'invalid_view') ?? 'unread';
  if (!(NOTIFICATION_VIEWS as readonly string[]).includes(view)) throw new SettingsRequestError('invalid_view');
  const limitText = single(req, 'limit', 'invalid_limit');
  if (limitText !== undefined && (!/^[1-9]\d{0,2}$/.test(limitText) || Number(limitText) > NOTIFICATION_LIST_LIMITS.max)) throw new SettingsRequestError('invalid_limit');
  const cursorText = single(req, 'cursor', 'invalid_cursor');
  const cursor = cursorText === undefined ? null : decodeCursor(cursorText);
  if (cursorText !== undefined && !cursor) throw new SettingsRequestError('invalid_cursor');
  return { view: view as NotificationView, limit: limitText === undefined ? NOTIFICATION_LIST_LIMITS.default : Number(limitText), cursor };
}

/** 읽음 본문: {"through": "<순번>"} 또는 {"items": [{"key": "<항목 키>", "seq": "<순번>"}, …]}(1~50개, 키 중복 금지). */
export function parseReadBody(req: Request): ReadRequest {
  const body = req.body;
  if (!req.is('application/json') || !plainObject(body)) throw new SettingsRequestError('invalid_read_request');
  const keys = Object.keys(body);
  if (keys.length === 1 && keys[0] === 'through' && isSeq(body.through)) return { through: body.through as string };
  if (keys.length === 1 && keys[0] === 'items' && Array.isArray(body.items) && body.items.length >= 1 && body.items.length <= NOTIFICATION_READ_ITEMS_MAX) {
    const seen = new Set<string>();
    const items = body.items.map((item) => {
      if (!plainObject(item) || Object.keys(item).length !== 2 || !isItemKey(item.key) || !isSeq(item.seq) || seen.has(item.key as string)) {
        throw new SettingsRequestError('invalid_read_request');
      }
      seen.add(item.key as string);
      return { key: item.key as string, seq: item.seq as string };
    });
    return { items };
  }
  throw new SettingsRequestError('invalid_read_request');
}

const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
  (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };
const noQuery = (req: Request) => { if (Object.keys(req.query).length) throw new SettingsRequestError('unknown_notification_filter'); };

function errors(router: Router) {
  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof SettingsRequestError) { res.status(error.status).json({ code: error.code }); return; }
    const parse = error as { type?: string; status?: number; statusCode?: number };
    const status = parse?.status ?? parse?.statusCode;
    if (typeof status === 'number' && status >= 400 && status < 500) {
      res.status(status).json({ code: BODY_ERROR_CODES[parse.type ?? ''] ?? 'invalid_request' });
      return;
    }
    res.status(503).json({ code: 'notification_storage_unavailable' });
  });
}

/** /api/me: GET·PUT /settings. */
export function createMeRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any);
  router.use(express.json({ limit: '16kb', strict: true }));
  router.get('/settings', route(async (req, res) => {
    noQuery(req);
    res.json(await readSettings(database(), req.user!.id));
  }));
  router.put('/settings', route(async (req, res) => {
    noQuery(req);
    if (!req.is('application/json')) throw new SettingsRequestError('invalid_settings_request');
    res.json(await updateSettings(database(), req.user!.id, parseSettingsUpdate(req.body)));
  }));
  errors(router);
  return router;
}

/** /api/notifications: GET 목록, GET /unread-count, POST /read. */
export function createNotificationRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any);
  router.use(express.json({ limit: '16kb', strict: true }));
  router.get('/', route(async (req, res) => {
    const query = parseNotificationQuery(req);
    const settings = await loadNotificationSettings(database(), req.user!.id);
    res.json(await readNotifications(database(), req.user!.id, settings, query));
  }));
  router.get('/unread-count', route(async (req, res) => {
    noQuery(req);
    const settings = await loadNotificationSettings(database(), req.user!.id);
    res.json(await readUnreadCount(database(), req.user!.id, settings));
  }));
  router.post('/read', route(async (req, res) => {
    noQuery(req);
    const request = parseReadBody(req);
    const settings = await loadNotificationSettings(database(), req.user!.id);
    const result = await markNotificationsRead(database(), req.user!.id, settings, request);
    res.json({ ...result, ...(await readUnreadCount(database(), req.user!.id, settings)) });
  }));
  errors(router);
  return router;
}
