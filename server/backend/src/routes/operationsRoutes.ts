import { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken } from '../middleware/auth.js';
import { isLayer, readCollection, readDecisionTrend, readLatency, readOverview, readSourceFields,
  readStorage } from '../services/operationsReadModel.js';
import { isMethodStatusPeriod, readMethodStatus } from '../services/methodStatusReadModel.js';

/**
 * 운영 현황 읽기 API(operations-ui.md). user/admin 모두 조회 가능한 집계만 반환한다.
 * 변경(입력 모드 전환)은 관리자 전용 /api/inputs를 그대로 쓴다.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
class OperationsRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}
const allowOnly = (req: Request, keys: string[]) => {
  if (Object.keys(req.query).some((key) => !keys.includes(key))) throw new OperationsRequestError('unknown_operations_filter');
};
const sourceIdOf = (req: Request) => {
  const value = req.query.sourceId;
  if (typeof value !== 'string' || !UUID.test(value)) throw new OperationsRequestError('invalid_source_id');
  return value;
};

export function createOperationsRouter(database: () => Pool = getDatabasePool) {
  const router = Router();
  router.use(authenticateToken as any);
  const route = (handler: (req: Request, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) => { void handler(req, res).catch(next); };
  const found = (res: Response, value: unknown) => {
    if (value === null) { res.status(404).json({ code: 'source_not_found' }); return; }
    res.json(value);
  };

  router.get('/overview', route(async (req, res) => {
    allowOnly(req, []);
    res.json(await readOverview(database()));
  }));
  router.get('/collection', route(async (req, res) => {
    allowOnly(req, ['sourceId']);
    found(res, await readCollection(database(), sourceIdOf(req)));
  }));
  router.get('/latency', route(async (req, res) => {
    allowOnly(req, ['sourceId']);
    found(res, await readLatency(database(), sourceIdOf(req)));
  }));
  router.get('/source-fields', route(async (req, res) => {
    allowOnly(req, ['sourceId', 'layer']);
    const sourceId = sourceIdOf(req);
    if (!isLayer(req.query.layer)) throw new OperationsRequestError('invalid_layer');
    found(res, await readSourceFields(database(), sourceId, req.query.layer));
  }));
  router.get('/storage', route(async (req, res) => {
    allowOnly(req, []);
    res.json(await readStorage(database()));
  }));
  router.get('/decision-trend', route(async (req, res) => {
    allowOnly(req, ['days']);
    const value = req.query.days ?? '3';
    if (typeof value !== 'string' || !/^(?:[1-9]|1[0-4])$/.test(value)) throw new OperationsRequestError('invalid_days');
    res.json(await readDecisionTrend(database(), Number(value)));
  }));

  router.get('/method-status', route(async (req, res) => {
    allowOnly(req, ['period']);
    const period = req.query.period ?? '1h';
    if (!isMethodStatusPeriod(period)) throw new OperationsRequestError('invalid_period');
    res.json(await readMethodStatus(database(), period));
  }));

  router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof OperationsRequestError) { res.status(error.status).json({ code: error.code }); return; }
    // 저장소 연결·조회 실패는 성공·빈 값으로 바꾸지 않는다. 오류 원문은 노출하지 않는다.
    res.status(503).json({ code: 'operations_storage_unavailable' });
  });
  return router;
}

export default createOperationsRouter();
