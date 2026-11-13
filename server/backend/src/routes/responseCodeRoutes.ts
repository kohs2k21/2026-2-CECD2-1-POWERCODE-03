import express, { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, AuthenticatedRequest } from '../middleware/auth.js';
import { UserRepository } from '../repository/userRepository.js';
import type { ActorResolver } from '../ruleset/ruleSetStore.js';
import { ResponseCodeError, parseCodeParam, parseListQuery, parseMessageEdit } from '../responsecodes/responseCodeContract.js';
import { editResponseCodeMessage, readResponseCodeDetail, readResponseCodeList } from '../responsecodes/responseCodeStore.js';

/**
 * 응답 코드 사전 API(contracts/response-codes-ui.md 3·4절). 읽기: 로그인 사용자, 메시지 편집: 관리자.
 * 코드별 위험도 편집은 이 API가 아니라 룰 세트 저장(PUT /api/rule-set, expectedRevision)으로 한다 — 버전·되돌리기가 룰 세트에 남는다.
 * Bearer만 쓰므로 CSRF 토큰 없음. 본문은 JSON만, 16KB 상한(앱 공통 express.json보다 먼저 연결). 저장소 오류는 503(원문 비노출).
 */
const BODY_ERROR_CODES: Record<string, string> = {
  'entity.parse.failed': 'invalid_json', 'entity.too.large': 'request_too_large',
  'charset.unsupported': 'unsupported_charset', 'encoding.unsupported': 'unsupported_encoding',
};

export function createResponseCodeRouter(database: () => Pool = getDatabasePool, users = UserRepository.getInstance()) {
  const router = Router();
  router.use(authenticateToken as any);
  router.use(express.json({ limit: '16kb', strict: true }));
  const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };
  /** 변경자 표시: 계정 email은 관리자에게만(룰 세트 API와 같은 범위). 그 밖은 계정 ID 그대로(가져오기는 system:import). */
  const actorOf = (req: AuthenticatedRequest): ActorResolver => async (actorId) => {
    const account = req.user!.userType === 'admin' ? await users.getById(actorId).catch(() => null) : null;
    return { actorId, displayName: account?.email ?? actorId };
  };

  router.get('/', route(async (req, res) => {
    res.json(await readResponseCodeList(database(), parseListQuery(req.query as Record<string, unknown>)));
  }));
  router.get('/:code', route(async (req, res) => {
    if (Object.keys(req.query).length) throw new ResponseCodeError('unknown_response_code_filter');
    res.json(await readResponseCodeDetail(database(), parseCodeParam(req.params.code), actorOf(req)));
  }));
  router.put('/:code', requireAdmin as any, route(async (req, res) => {
    const code = parseCodeParam(req.params.code);
    if (!req.is('application/json')) throw new ResponseCodeError('invalid_response_code_edit');
    await editResponseCodeMessage(database(), code, parseMessageEdit(req.body), req.user!.id);
    res.json(await readResponseCodeDetail(database(), code, actorOf(req)));
  }));

  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof ResponseCodeError) { res.status(error.status).json({ code: error.code, ...error.detail }); return; }
    const parse = error as { type?: string; status?: number; statusCode?: number };
    const status = parse?.status ?? parse?.statusCode;
    if (typeof status === 'number' && status >= 400 && status < 500) {
      res.status(status).json({ code: BODY_ERROR_CODES[parse.type ?? ''] ?? 'invalid_request' });
      return;
    }
    res.status(503).json({ code: 'response_code_storage_unavailable' });
  });
  return router;
}

export default createResponseCodeRouter();
