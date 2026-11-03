import { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth.js';
import {
  ANALYSIS_CATEGORIES, ANALYSIS_COMPLETENESS, ANALYSIS_PAGE_SIZES, ANALYSIS_QUERY_MAX, ANALYSIS_SIGNALS, ANALYSIS_SORTS,
  readDecisionDetail, readDecisionInterims, readDecisionList, type AnalysisListFilter,
} from '../services/analysisReadModel.js';

/**
 * 이상 분석 읽기 API(analysis-ui.md). user/admin 모두 조회만 한다(쓰기 경로 없음).
 * 기존 GET /api/pipeline/decisions·SSE /api/pipeline/events는 그대로 둔다.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LIST_KEYS = ['category', 'q', 'signal', 'completeness', 'sort', 'page', 'pageSize'];
class AnalysisRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}
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

export function createAnalysisRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any);
  const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };
  const found = (res: Response, value: unknown) => {
    if (value === null) { res.status(404).json({ code: 'decision_not_found' }); return; }
    res.json(value);
  };

  router.get('/decisions', route(async (req, res) => {
    res.json(await readDecisionList(database(), parseListFilter(req)));
  }));
  router.get('/decisions/:id', route(async (req, res) => {
    found(res, await readDecisionDetail(database(), idOf(req), req.user!.id));
  }));
  router.get('/decisions/:id/interims', route(async (req, res) => {
    found(res, await readDecisionInterims(database(), idOf(req)));
  }));

  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof AnalysisRequestError) { res.status(error.status).json({ code: error.code }); return; }
    // 저장소 연결·조회 실패는 성공·빈 목록으로 바꾸지 않는다. 오류 원문은 노출하지 않는다.
    res.status(503).json({ code: 'analysis_storage_unavailable' });
  });
  return router;
}

export default createAnalysisRouter();
