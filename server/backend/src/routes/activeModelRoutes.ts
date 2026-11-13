import crypto from 'node:crypto';
import { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, AuthenticatedRequest } from '../middleware/auth.js';
import { UserRepository } from '../repository/userRepository.js';
import type { ActorResolver } from '../ruleset/ruleSetStore.js';
import {
  ActiveSetError, changeActiveModelSet, parseActiveHistoryQuery, parseActiveSetRequest, readActiveHistory, readActiveHistoryItem, readOperationalState,
} from '../services/activeModelSet.js';

/**
 * 활성 모델 집합(model-version.md 활성 집합 절)과 운영 상태 일관 읽기.
 * - 조작: POST /api/configurations/:id/applications(apply·replace·retire·restore, configRoutes가 이 파일로 넘김), POST /api/active-models/restore
 * - 이력: GET /api/active-models/history(+/:revision), 운영 상태: GET /api/operational-state — 로그인 사용자
 */
const IDEMPOTENCY_KEY = /^[a-zA-Z0-9_.:-]{1,128}$/;
const IDEMPOTENCY_MAX = 200;
type Replay = { requestHash: string; response: unknown };
const users = UserRepository.getInstance();

/** 변경자 표시: 계정 email은 관리자에게만(룰 세트와 같은 범위). 일반 사용자·계정 없는 값(r0의 system)은 계정 ID 그대로. */
const actorOf = (req: AuthenticatedRequest): ActorResolver => async (actorId) => {
  const account = req.user!.userType === 'admin' ? await users.getById(actorId).catch(() => null) : null;
  return { actorId, displayName: account?.email ?? actorId };
};

// 저장 표에 멱등키 열이 없어 응답 재전송은 이 프로세스 메모리로만 보장한다(룰 세트 저장과 같은 방식).
// 재시작 뒤 같은 키 재요청은 expectedRevision이 막아 409로 끝난다(이중 적용 없음).
const replays = new Map<string, Replay>();
const replayKey = (req: AuthenticatedRequest) => {
  const header = req.headers['idempotency-key'];
  if (header === undefined) return null;
  if (typeof header !== 'string' || !IDEMPOTENCY_KEY.test(header)) throw new ActiveSetError('invalid_idempotency_key');
  return `${req.user!.id}:${header}`;
};

export async function handleActiveSetChange(req: AuthenticatedRequest, res: Response, configVersionId: string | null, database: () => Pool = getDatabasePool) {
  const key = replayKey(req);
  const request = parseActiveSetRequest(req.body);
  if (configVersionId === null && request.operation !== 'restore') throw new ActiveSetError('invalid_application_request');
  const requestHash = crypto.createHash('sha256').update(JSON.stringify({ configVersionId: request.operation === 'restore' ? null : configVersionId, request })).digest('hex');
  const prior = key ? replays.get(key) : undefined;
  if (prior) {
    if (prior.requestHash !== requestHash) throw new ActiveSetError('idempotency_payload_conflict', 409);
    res.json(prior.response);
    return;
  }
  // 복원은 경로의 :id를 쓰지 않는다(model-version.md Q-M9).
  const response = await changeActiveModelSet(database(), request.operation === 'restore' ? null : configVersionId, request, req.user!.id, actorOf(req));
  if (key) {
    replays.set(key, { requestHash, response });
    if (replays.size > IDEMPOTENCY_MAX) replays.delete(replays.keys().next().value as string);
  }
  res.json(response);
}

const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
  (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };

const errors = (error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof ActiveSetError) { res.status(error.status).json({ code: error.code, ...error.detail }); return; }
  console.error('active model set request failed:', error);
  res.status(503).json({ code: 'active_model_set_storage_unavailable' });
};

export function createActiveModelRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any);
  router.get('/history', route(async (req, res) => {
    res.json(await readActiveHistory(database(), parseActiveHistoryQuery(req.query as Record<string, unknown>), actorOf(req)));
  }));
  router.get('/history/:revision', route(async (req, res) => {
    if (!/^(0|[1-9]\d{0,14})$/.test(req.params.revision)) throw new ActiveSetError('invalid_revision');
    res.json(await readActiveHistoryItem(database(), Number(req.params.revision), actorOf(req)));
  }));
  router.post('/restore', requireAdmin as any, route(async (req, res) => {
    if (!req.is('application/json')) throw new ActiveSetError('invalid_application_request');
    await handleActiveSetChange(req, res, null, database);
  }));
  router.use(errors);
  return router;
}

export function createOperationalStateRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any);
  router.get('/', route(async (req, res) => {
    if (Object.keys(req.query).length) throw new ActiveSetError('unknown_operational_state_filter');
    res.json(await readOperationalState(database(), actorOf(req)));
  }));
  router.use(errors);
  return router;
}
