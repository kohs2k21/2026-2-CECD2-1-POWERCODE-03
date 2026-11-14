import express, { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, AuthenticatedRequest } from '../middleware/auth.js';
import { UserRepository } from '../repository/userRepository.js';
import {
  ANALYSIS_CATEGORIES, ANALYSIS_COMPLETENESS, ANALYSIS_PAGE_SIZES, ANALYSIS_QUERY_MAX, ANALYSIS_SIGNALS, ANALYSIS_SORTS,
  readDecisionDetail, readDecisionFlow, readDecisionGraph, readDecisionInterims, readDecisionList, type AnalysisListFilter,
} from '../services/analysisReadModel.js';
import {
  AnalysisRequestError, FEEDBACK_KINDS, FEEDBACK_OUTCOMES, FEEDBACK_REASON_MAX, WORKFLOW_STATES,
  addFeedback, changeWorkflow, listFeedback, markRead, type FeedbackKind, type FeedbackOutcome, type WorkflowState,
} from '../services/analysisWorkflow.js';
import { registerIncidentRoutes } from './incidentRoutes.js';

/**
 * 이상 분석 API(analysis-ui.md).
 * - 읽기: user/admin 모두. 처리 상태 변경·읽음 표시: user/admin 모두(요청자 기록). 조사·조치 기록 읽기·쓰기: admin(와이어프레임 feedbackBox).
 * - Bearer 인증만 쓰므로(쿠키 없음) 별도 CSRF 토큰은 없다(기존 쓰기 API와 같음). 본문은 JSON만, 16KB 상한.
 * - 기존 GET /api/pipeline/decisions·SSE /api/pipeline/events는 그대로 둔다.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LIST_KEYS = ['category', 'q', 'signal', 'completeness', 'sort', 'page', 'pageSize'];
const single = (req: Request, key: string): string | undefined => {
  const value = req.query[key];
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new AnalysisRequestError(`invalid_${key === 'pageSize' ? 'page_size' : key}`);
  return value;
};
const option = <T extends string>(req: Request, key: string, allowed: readonly T[], code: string): T | undefined => {
  const value = single(req, key);
  if (value === undefined) return undefined;
  if (!(allowed as readonly string[]).includes(value)) throw new AnalysisRequestError(code);
  return value as T;
};

export function parseListFilter(req: Request): AnalysisListFilter {
  if (Object.keys(req.query).some((key) => !LIST_KEYS.includes(key))) throw new AnalysisRequestError('unknown_analysis_filter');
  const q = (single(req, 'q') ?? '').trim();
  // 제어 문자 거절, 길이 상한. 빈 검색어는 검색 없음.
  if (q.length > ANALYSIS_QUERY_MAX || /[\u0000-\u001f\u007f]/.test(q)) throw new AnalysisRequestError('invalid_q');
  const page = single(req, 'page') ?? '1';
  if (!/^[1-9]\d{0,5}$/.test(page)) throw new AnalysisRequestError('invalid_page');
  const pageSize = single(req, 'pageSize') ?? '10';
  if (!ANALYSIS_PAGE_SIZES.map(String).includes(pageSize)) {
    throw new AnalysisRequestError('invalid_page_size');
  }
  return {
    category: option(req, 'category', ANALYSIS_CATEGORIES, 'invalid_category') ?? 'all',
    q,
    signal: option(req, 'signal', ANALYSIS_SIGNALS, 'invalid_signal') ?? null,
    completeness: option(req, 'completeness', ANALYSIS_COMPLETENESS, 'invalid_completeness') ?? null,
    sort: option(req, 'sort', ANALYSIS_SORTS, 'invalid_sort') ?? 'newest',
    page: Number(page),
    pageSize: Number(pageSize),
  };
}
const idOf = (req: Request) => {
  if (!UUID.test(req.params.id)) throw new AnalysisRequestError('invalid_decision_id');
  if (Object.keys(req.query).length) throw new AnalysisRequestError('unknown_analysis_filter');
  return req.params.id.toLowerCase();
};
/** body-parser 오류 type → 응답 code. 목록 밖의 4xx는 invalid_request. */
const BODY_ERROR_CODES: Record<string, string> = {
  'entity.parse.failed': 'invalid_json',
  'entity.too.large': 'request_too_large',
  'charset.unsupported': 'unsupported_charset',
  'encoding.unsupported': 'unsupported_encoding',
  'request.aborted': 'request_aborted',
  'request.size.invalid': 'invalid_request',
  'entity.verify.failed': 'invalid_request',
  'stream.encoding.set': 'invalid_request',
};
const plainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
/** 본문 키 집합이 정확히 `keys`인 JSON 객체(그 밖의 키·누락·배열·비객체는 400 code). */
const bodyOf = (req: Request, keys: string[], code: string) => {
  const body = req.body;
  if (!req.is('application/json') || !plainObject(body)) throw new AnalysisRequestError(code);
  const present = Object.keys(body).sort();
  if (present.length !== keys.length || present.some((key, index) => key !== [...keys].sort()[index])) throw new AnalysisRequestError(code);
  return body;
};
const oneOf = <T extends string>(value: unknown, allowed: readonly T[], code: string): T => {
  if (typeof value !== 'string' || !(allowed as readonly string[]).includes(value)) throw new AnalysisRequestError(code);
  return value as T;
};
/** 쓰기 요청: 본문 필수(빈 본문 거절). 기록 사유는 앞뒤 공백 제거 후 1~2000자, 제어 문자는 줄바꿈·탭만 허용. */
export function parseWorkflowRequest(req: Request): { state: WorkflowState; expectedState: WorkflowState } {
  const body = bodyOf(req, ['state', 'expectedState'], 'invalid_workflow_request');
  return {
    state: oneOf(body.state, WORKFLOW_STATES, 'invalid_workflow_request'),
    expectedState: oneOf(body.expectedState, WORKFLOW_STATES, 'invalid_workflow_request'),
  };
}
/**
 * 조사·조치 기록: `{kind, reason}` 또는 확인 결과를 더한 `{kind, reason, outcome}`(029). outcome = actual-anomaly·normal·undetermined 또는 null(미입력),
 * 키 생략도 null. 이전 화면(키 2개)은 그대로 받는다.
 */
export function parseFeedbackRequest(req: Request): { kind: FeedbackKind; reason: string; outcome: FeedbackOutcome | null } {
  const withOutcome = plainObject(req.body) && Object.prototype.hasOwnProperty.call(req.body, 'outcome');
  const body = bodyOf(req, withOutcome ? ['kind', 'reason', 'outcome'] : ['kind', 'reason'], 'invalid_feedback_request');
  const kind = oneOf(body.kind, FEEDBACK_KINDS, 'invalid_feedback_request');
  const outcome = body.outcome === undefined || body.outcome === null ? null : oneOf(body.outcome, FEEDBACK_OUTCOMES, 'invalid_feedback_request');
  const reason = typeof body.reason === 'string' ? body.reason.trim() : '';
  if (!reason || [...reason].length > FEEDBACK_REASON_MAX || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(reason)) {
    throw new AnalysisRequestError('invalid_feedback_request');
  }
  return { kind, reason, outcome };
}

/** 읽음 표시: `{occurredAt}` = 화면이 본 항목 시각(목록·상세 응답의 UTC ISO, 소수 6자리까지, Z 또는 ±hh:mm). */
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
export function parseReadRequest(req: Request): string {
  const body = bodyOf(req, ['occurredAt'], 'invalid_read_request');
  if (typeof body.occurredAt !== 'string' || !ISO_TIME.test(body.occurredAt) || !Number.isFinite(Date.parse(body.occurredAt))) {
    throw new AnalysisRequestError('invalid_read_request');
  }
  return body.occurredAt;
}

export function createAnalysisRouter(database: () => Pool = getDatabasePool, users = UserRepository.getInstance()) {
  const router = Router();
  router.use(authenticateToken as any);
  // 앱 공통 express.json보다 먼저 연결한다(index.ts): 잘못된 JSON·과대 본문을 이 라우터의 400으로 돌려준다.
  router.use(express.json({ limit: '16kb', strict: true }));
  const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };
  const found = (res: Response, value: unknown) => {
    if (value === null) { res.status(404).json({ code: 'decision_not_found' }); return; }
    res.json(value);
  };

  router.get('/decisions', route(async (req, res) => {
    res.json(await readDecisionList(database(), parseListFilter(req), req.user!.id));
  }));
  router.get('/decisions/:id', route(async (req, res) => {
    found(res, await readDecisionDetail(database(), idOf(req), req.user!.id));
  }));
  router.get('/decisions/:id/interims', route(async (req, res) => {
    found(res, await readDecisionInterims(database(), idOf(req)));
  }));
  router.get('/decisions/:id/flow', route(async (req, res) => {
    found(res, await readDecisionFlow(database(), idOf(req)));
  }));
  // 거래 내부 실행 그래프(층 A, contracts/process-graph.md): Flow와 같은 조회·상한·권한.
  router.get('/decisions/:id/graph', route(async (req, res) => {
    found(res, await readDecisionGraph(database(), idOf(req)));
  }));
  router.put('/decisions/:id/workflow', route(async (req, res) => {
    const id = idOf(req);
    res.json(await changeWorkflow(database(), id, req.user!.id, parseWorkflowRequest(req)));
  }));
  router.put('/decisions/:id/read', route(async (req, res) => {
    const id = idOf(req);
    res.json(await markRead(database(), id, req.user!.id, parseReadRequest(req)));
  }));
  /** 작성자 표시: 계정 email(관리자 전용 응답, 관리자는 GET /api/users로 같은 값을 볼 수 있다). 계정이 없으면 null. */
  // 계정 조회 실패는 기록 저장 결과를 바꾸지 않는다(작성자 email만 null).
  const withActor = async <T extends { actorId: string }>(item: T) =>
    ({ ...item, actorEmail: (await users.getById(item.actorId).catch(() => null))?.email ?? null });
  router.get('/decisions/:id/feedback', requireAdmin as any, route(async (req, res) => {
    const result = await listFeedback(database(), idOf(req));
    res.json({ id: result.id, items: await Promise.all(result.items.map(withActor)) });
  }));
  router.post('/decisions/:id/feedback', requireAdmin as any, route(async (req, res) => {
    const id = idOf(req);
    const result = await addFeedback(database(), id, req.user!.id, parseFeedbackRequest(req));
    res.status(201).json({ id: result.id, item: await withActor(result.item) });
  }));

  // 사건 보기(층 C, contracts/incidents-ui.md): 읽기만, 같은 인증·오류 처리.
  registerIncidentRoutes(router, database);

  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof AnalysisRequestError) { res.status(error.status).json({ code: error.code }); return; }
    // 본문 해석 단계(body-parser)의 요청 오류는 그 4xx 상태 그대로 돌려준다(저장소 오류 503과 구분).
    const parse = error as { type?: string; status?: number; statusCode?: number };
    const status = parse?.status ?? parse?.statusCode;
    if (typeof status === 'number' && status >= 400 && status < 500) {
      res.status(status).json({ code: BODY_ERROR_CODES[parse.type ?? ''] ?? 'invalid_request' });
      return;
    }
    // 저장소 연결·조회 실패는 성공·빈 목록으로 바꾸지 않는다. 오류 원문은 노출하지 않는다.
    res.status(503).json({ code: 'analysis_storage_unavailable' });
  });
  return router;
}

export default createAnalysisRouter();
