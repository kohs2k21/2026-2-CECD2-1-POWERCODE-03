import { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, AuthenticatedRequest } from '../middleware/auth.js';
import {
  ComparisonError, createEvaluationSet, parseComparisonListQuery, parseEvaluationSetRequest, parseSetListQuery, readComparison, readComparisons,
  readEvaluationSet, readEvaluationSets, readSetSnapshots,
} from '../services/comparisonEvaluation.js';

/**
 * 평가 세트·비교 평가(config-comparison-evaluation.md v1, evaluations-ui.md). 관리자 전용(평가 결과 화면이 관리자 화면).
 * - POST/GET /api/evaluation-sets(+/:id, /snapshots), GET /api/comparisons(+/:id)
 * - 비교 작업 접수는 기존 `POST /api/jobs kind=compare`(jobRoutes가 comparisonEvaluation으로 넘김)
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDEMPOTENCY_KEY = /^[a-zA-Z0-9_.:-]{1,128}$/;

const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
  (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };

export const idempotencyKeyOf = (req: Request) => {
  const header = req.headers['idempotency-key'];
  if (header === undefined) return null;
  if (typeof header !== 'string' || !IDEMPOTENCY_KEY.test(header)) throw new ComparisonError('invalid_idempotency_key');
  return header;
};
const idParam = (value: string, code: string) => {
  if (!UUID.test(value)) throw new ComparisonError(code);
  return value.toLowerCase();
};
const errors = (storageCode: string) => (error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof ComparisonError) { res.status(error.status).json({ code: error.code }); return; }
  console.error('comparison request failed:', error);
  res.status(503).json({ code: storageCode });
};

export function createEvaluationSetRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any, requireAdmin as any);
  router.post('/', route(async (req, res) => {
    const key = idempotencyKeyOf(req);
    const { created, body } = await createEvaluationSet(database(), parseEvaluationSetRequest(req.body), req.user!.id, key);
    res.status(created ? 201 : 200).json(body);
  }));
  router.get('/', route(async (req, res) => {
    res.json(await readEvaluationSets(database(), parseSetListQuery(req.query as Record<string, unknown>)));
  }));
  router.get('/snapshots', route(async (req, res) => {
    const query = req.query as Record<string, unknown>;
    if (Object.keys(query).some((key) => key !== 'sourceId')) throw new ComparisonError('unknown_evaluation_set_filter');
    if (query.sourceId !== undefined && (typeof query.sourceId !== 'string' || !UUID.test(query.sourceId))) throw new ComparisonError('invalid_source_id');
    res.json(await readSetSnapshots(database(), (query.sourceId as string | undefined)?.toLowerCase() ?? null));
  }));
  router.get('/:id', route(async (req, res) => {
    res.json(await readEvaluationSet(database(), idParam(req.params.id, 'invalid_evaluation_set_id')));
  }));
  router.use(errors('evaluation_set_storage_unavailable'));
  return router;
}

export function createComparisonRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any, requireAdmin as any);
  router.get('/', route(async (req, res) => {
    res.json(await readComparisons(database(), parseComparisonListQuery(req.query as Record<string, unknown>)));
  }));
  router.get('/:id', route(async (req, res) => {
    res.json(await readComparison(database(), idParam(req.params.id, 'invalid_comparison_id')));
  }));
  router.use(errors('comparison_storage_unavailable'));
  return router;
}
