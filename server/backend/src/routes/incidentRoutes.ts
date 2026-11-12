import type { NextFunction, Request, Response, Router } from 'express';
import type { Pool } from 'pg';
import { AnalysisRequestError } from '../services/analysisWorkflow.js';
import {
  INCIDENT_CRITERIA_KINDS, INCIDENT_LIST_LIMITS, INCIDENT_MEMBER_LIMITS, INCIDENT_MIN_TRANSACTIONS,
  readIncidentDetail, readIncidentList, readIncidentMembers, type IncidentListFilter, type MemberCursor,
} from '../services/incidentReadModel.js';

/**
 * 사건 보기 읽기 API(contracts/incidents-ui.md). 이상 분석 라우터 안에 둔다(같은 인증·오류 형식·503 처리).
 * 읽기만 있다(019 표는 API 역할 SELECT만). user·admin 모두 읽는다(다른 이상 분석 읽기와 같음).
 */
/** analysisRoutes.ts와 같은 UUID 형식(Detector 사건 id는 uuid5). */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
/** 응답이 내는 시각 형식(UTC, 마이크로초 6자리). 커서 안 시각은 이 형식만 받는다. */
const UTC_MICROS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;
const CURSOR_MAX = 512;

const single = (req: Request, key: string, code: string): string | undefined => {
  const value = req.query[key];
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new AnalysisRequestError(code);
  return value;
};
const allowOnly = (req: Request, keys: string[]) => {
  if (Object.keys(req.query).some((key) => !keys.includes(key))) throw new AnalysisRequestError('unknown_incident_filter');
};
const timeOf = (req: Request, key: 'from' | 'to') => {
  const value = single(req, key, `invalid_${key}`);
  if (value === undefined) return null;
  if (!ISO_TIME.test(value) || !Number.isFinite(Date.parse(value))) throw new AnalysisRequestError(`invalid_${key}`);
  return new Date(Date.parse(value)).toISOString();
};
const limitOf = (req: Request, limits: { default: number; max: number }) => {
  const value = single(req, 'limit', 'invalid_limit');
  if (value === undefined) return limits.default;
  if (!/^[1-9]\d{0,3}$/.test(value) || Number(value) > limits.max) throw new AnalysisRequestError('invalid_limit');
  return Number(value);
};
/** 커서 = 응답 nextCursor 그대로(base64url JSON [시각, 문자열]). 형식이 다르면 400 invalid_cursor. */
const cursorOf = (req: Request): [string, string] | null => {
  const value = single(req, 'cursor', 'invalid_cursor');
  if (value === undefined) return null;
  if (value.length > CURSOR_MAX || !/^[A-Za-z0-9_-]+$/.test(value)) throw new AnalysisRequestError('invalid_cursor');
  try {
    const parsed = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
    if (Array.isArray(parsed) && parsed.length === 2 && typeof parsed[0] === 'string' && UTC_MICROS.test(parsed[0])
      && Number.isFinite(Date.parse(parsed[0])) && typeof parsed[1] === 'string' && parsed[1].length > 0) return [parsed[0], parsed[1]];
  } catch { /* 아래에서 거절 */ }
  throw new AnalysisRequestError('invalid_cursor');
};
const incidentIdOf = (req: Request) => {
  if (!UUID.test(req.params.id)) throw new AnalysisRequestError('invalid_incident_id');
  return req.params.id.toLowerCase();
};

export function parseIncidentListFilter(req: Request): IncidentListFilter {
  allowOnly(req, ['from', 'to', 'criteria', 'sourceId', 'minTransactions', 'limit', 'cursor']);
  const from = timeOf(req, 'from'), to = timeOf(req, 'to');
  if (from !== null && to !== null && Date.parse(from) > Date.parse(to)) throw new AnalysisRequestError('invalid_period');
  const criteria = single(req, 'criteria', 'invalid_criteria') ?? null;
  if (criteria !== null && !(INCIDENT_CRITERIA_KINDS as readonly string[]).includes(criteria)) throw new AnalysisRequestError('invalid_criteria');
  const sourceId = single(req, 'sourceId', 'invalid_source_id') ?? null;
  if (sourceId !== null && !UUID.test(sourceId)) throw new AnalysisRequestError('invalid_source_id');
  const minimum = single(req, 'minTransactions', 'invalid_min_transactions');
  if (minimum !== undefined && (!/^[1-9]\d{0,3}$/.test(minimum) || Number(minimum) > INCIDENT_MIN_TRANSACTIONS.max)) {
    throw new AnalysisRequestError('invalid_min_transactions');
  }
  const cursor = cursorOf(req);
  if (cursor && !UUID.test(cursor[1])) throw new AnalysisRequestError('invalid_cursor');
  return { from, to, criteria, sourceId: sourceId?.toLowerCase() ?? null,
    minTransactions: minimum === undefined ? INCIDENT_MIN_TRANSACTIONS.default : Number(minimum), limit: limitOf(req, INCIDENT_LIST_LIMITS),
    cursor: cursor && { startAt: cursor[0], id: cursor[1].toLowerCase() } };
}
export function parseMemberQuery(req: Request): { limit: number; cursor: MemberCursor | null } {
  allowOnly(req, ['limit', 'cursor']);
  const cursor = cursorOf(req);
  if (cursor && !/^(transaction|decision):/.test(cursor[1])) throw new AnalysisRequestError('invalid_cursor');
  return { limit: limitOf(req, INCIDENT_MEMBER_LIMITS), cursor: cursor && { firstAt: cursor[0], key: cursor[1] } };
}

export function registerIncidentRoutes(router: Router, database: () => Pool) {
  const route = (handler: (req: Request, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) => { void handler(req, res).catch(next); };
  const found = (res: Response, value: unknown) => {
    if (value === null) { res.status(404).json({ code: 'incident_not_found' }); return; }
    res.json(value);
  };
  router.get('/incidents', route(async (req, res) => {
    res.json(await readIncidentList(database(), parseIncidentListFilter(req)));
  }));
  router.get('/incidents/:id', route(async (req, res) => {
    const id = incidentIdOf(req);
    allowOnly(req, []);
    found(res, await readIncidentDetail(database(), id));
  }));
  router.get('/incidents/:id/members', route(async (req, res) => {
    const id = incidentIdOf(req);
    const { limit, cursor } = parseMemberQuery(req);
    found(res, await readIncidentMembers(database(), id, limit, cursor));
  }));
}
