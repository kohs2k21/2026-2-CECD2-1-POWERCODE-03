import crypto from 'node:crypto';
import express, { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, AuthenticatedRequest } from '../middleware/auth.js';
import { UserRepository } from '../repository/userRepository.js';
import { RuleSetError, parseRuleSetRequest } from '../ruleset/ruleSetContract.js';
import {
  parseHistoryQuery, previewRuleSet, readHistory, readHistoryItem, readRuleSet, saveRuleSet, type ActorResolver,
} from '../ruleset/ruleSetStore.js';
import { computeThresholdStats, parseThresholdStatsRequest } from '../ruleset/thresholdStats.js';
import {
  enqueueCandidateJob, parseCandidateJobRequest, parseCandidateListQuery, readCandidateDetail, readCandidateList,
} from '../ruleset/ruleCandidates.js';

/**
 * 룰 세트 API(rule-set.md)·규칙별 수식 추천(llm-recommendation-job.md).
 * 읽기·이력: 로그인 사용자. 저장·미리보기·수식 추천: 관리자. Bearer만 쓰므로 CSRF 토큰 없음. 본문은 JSON만, 64KB 상한.
 * 규칙 추천(rule-candidates-ui.md): 후보 읽기·후보 작업 요청 모두 관리자(탐지 설정 화면, 작업 결과는 관리자만 읽음).
 */
const BODY_ERROR_CODES: Record<string, string> = {
  'entity.parse.failed': 'invalid_json', 'entity.too.large': 'request_too_large',
  'charset.unsupported': 'unsupported_charset', 'encoding.unsupported': 'unsupported_encoding',
};
const IDEMPOTENCY_KEY = /^[a-zA-Z0-9_.:-]{1,128}$/;
const IDEMPOTENCY_MAX = 200;
type Replay = { requestHash: string; response: unknown };

export function createRuleSetRouter(database: () => Pool = getDatabasePool, users = UserRepository.getInstance()) {
  const router = Router();
  router.use(authenticateToken as any);
  // 앱 공통 express.json보다 먼저 연결한다(index.ts): 잘못된 JSON·과대 본문을 이 라우터 오류 형식으로 돌려준다.
  router.use(express.json({ limit: '64kb', strict: true }));
  const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };
  const admin = requireAdmin as any;
  const json = (req: Request) => {
    if (!req.is('application/json')) throw new RuleSetError('invalid_rule_set_request');
    return req.body;
  };
  /** 변경자 표시: 계정 email은 관리자에게만 보인다(GET /api/users와 같은 범위). 일반 사용자·계정 없는 값(r0의 system)은 계정 ID 그대로. */
  const actorOf = (req: AuthenticatedRequest): ActorResolver => async (actorId) => {
    const account = req.user!.userType === 'admin' ? await users.getById(actorId).catch(() => null) : null;
    return { actorId, displayName: account?.email ?? actorId };
  };

  // 저장 표에 멱등키 열이 없어 응답 재전송은 이 프로세스 메모리로만 보장한다. 재시작 뒤 같은 키 재요청은 expectedRevision이 막아 409로 끝난다(이중 적용 없음).
  const replays = new Map<string, Replay>();
  const replayKey = (req: AuthenticatedRequest) => {
    const header = req.headers['idempotency-key'];
    if (header === undefined) return null;
    if (typeof header !== 'string' || !IDEMPOTENCY_KEY.test(header)) throw new RuleSetError('invalid_idempotency_key');
    return `${req.user!.id}:${header}`;
  };
  const remember = (key: string, value: Replay) => {
    replays.set(key, value);
    if (replays.size > IDEMPOTENCY_MAX) replays.delete(replays.keys().next().value as string);
  };

  router.get('/', route(async (req, res) => { res.json(await readRuleSet(database(), actorOf(req))); }));
  router.put('/', admin, route(async (req, res) => {
    const key = replayKey(req);
    const request = parseRuleSetRequest(json(req));
    const requestHash = crypto.createHash('sha256').update(JSON.stringify(request)).digest('hex');
    const prior = key ? replays.get(key) : undefined;
    if (prior) {
      if (prior.requestHash !== requestHash) throw new RuleSetError('idempotency_payload_conflict', 409);
      res.json(prior.response);
      return;
    }
    const response = await saveRuleSet(database(), request, req.user!.id, actorOf(req));
    if (key) remember(key, { requestHash, response });
    res.json(response);
  }));
  router.post('/preview', admin, route(async (req, res) => {
    res.json(await previewRuleSet(database(), parseRuleSetRequest(json(req)), actorOf(req)));
  }));
  router.get('/history', route(async (req, res) => {
    res.json(await readHistory(database(), parseHistoryQuery(req.query), actorOf(req)));
  }));
  router.get('/history/:revision', route(async (req, res) => {
    if (!/^(0|[1-9]\d{0,14})$/.test(req.params.revision)) throw new RuleSetError('invalid_revision');
    res.json(await readHistoryItem(database(), Number(req.params.revision), actorOf(req)));
  }));
  router.post('/threshold-stats', admin, route(async (req, res) => {
    if (!req.is('application/json')) throw new RuleSetError('invalid_threshold_stats_request');
    res.json(await computeThresholdStats(database(), parseThresholdStatsRequest(req.body)));
  }));

  router.get('/candidates', admin, route(async (req, res) => {
    res.json(await readCandidateList(database(), parseCandidateListQuery(req.query as Record<string, unknown>)));
  }));
  router.get('/candidates/:candidateId', admin, route(async (req, res) => {
    if (Object.keys(req.query).length) throw new RuleSetError('unknown_candidate_filter');
    res.json(await readCandidateDetail(database(), req.params.candidateId));
  }));
  router.post('/candidates/jobs', admin, route(async (req, res) => {
    const header = req.headers['idempotency-key'];
    if (header !== undefined && (typeof header !== 'string' || !IDEMPOTENCY_KEY.test(header))) throw new RuleSetError('invalid_idempotency_key');
    if (!req.is('application/json')) throw new RuleSetError('invalid_candidate_job_request');
    const { created, job } = await enqueueCandidateJob(database(), parseCandidateJobRequest(req.body), req.user!.id, header ?? null);
    res.status(created ? 201 : 200).json(job);
  }));

  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof RuleSetError) { res.status(error.status).json({ code: error.code, ...error.detail }); return; }
    const parse = error as { type?: string; status?: number; statusCode?: number };
    const status = parse?.status ?? parse?.statusCode;
    if (typeof status === 'number' && status >= 400 && status < 500) {
      res.status(status).json({ code: BODY_ERROR_CODES[parse.type ?? ''] ?? 'invalid_request' });
      return;
    }
    // 저장소 연결·조회 실패는 빈 룰·성공으로 바꾸지 않는다. 오류 원문은 노출하지 않는다.
    res.status(503).json({ code: 'rule_set_storage_unavailable' });
  });
  return router;
}

export default createRuleSetRouter();
