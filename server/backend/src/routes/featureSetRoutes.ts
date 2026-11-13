import crypto from 'node:crypto';
import { Router, Request, Response, NextFunction } from 'express';
import type { PoolClient } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth.js';
import { DEFINITION_KIND, FEATURE_SET_LOCK, FIELD_LIST, FeatureFieldError, SCHEMA_VERSION, TRANSFORM_VERSION,
  canonicalJson, definitionsForFields, expressionHash, featureSetContentHash, versionColumns, type FeatureDefinition } from './featureSetContract.js';

/**
 * 입력 변수 세트 등록(models-ui.md 2단계, trainer-service.md 입력 변수 세트 등록 규칙). `/api/jobs/feature-sets`에 jobRoutes보다 먼저 연결한다.
 * 여기서 처리하지 않는 요청(`GET /`, 목록)은 그대로 jobRoutes로 넘어간다.
 */
const router = Router();

class FeatureSetRequestError extends Error {
  constructor(readonly code: string, readonly status: number) { super(code); }
}

function route(handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) {
  return (req: Request, res: Response, next: NextFunction): void => {
    void handler(req as AuthenticatedRequest, res).catch(next);
  };
}

/** 개수·중복은 형식 오류 400, 목록 밖 필드는 지원하지 않는 값 422(`POST /api/jobs` `unsupported_model`과 같은 기준). */
const STATUS: Record<FeatureFieldError['code'], number> = { invalid_feature_count: 400, duplicate_feature_field: 400, unknown_feature_field: 422 };

export type RegisteredFeatureSet = { id: string; contentHash: string; featureNames: string[]; reused: boolean };

/**
 * 한 거래 안에서 lock → 같은 hash 세트 재사용 → 없으면 세트·변수 버전·멤버 INSERT. API 역할은 SELECT·INSERT만 쓴다(행 잠금 없음).
 * 같은 lock을 Trainer 등록(`catalog.register_feature_set`)도 잡으므로 두 경로가 같은 hash 세트를 두 번 만들지 않는다.
 */
export async function registerFeatureSet(client: PoolClient, definitions: FeatureDefinition[], actorId: string): Promise<RegisteredFeatureSet> {
  const contentHash = featureSetContentHash(definitions);
  const featureNames = definitions.map(item => item.name);
  await client.query('BEGIN');
  try {
    await client.query('SELECT pg_advisory_xact_lock($1, $2)', [...FEATURE_SET_LOCK]);
    const existing = await client.query('SELECT id FROM ml_feature_sets WHERE content_hash=$1 ORDER BY created_at, id LIMIT 1', [contentHash]);
    if (existing.rowCount) {
      await client.query('COMMIT');
      return { id: existing.rows[0].id, contentHash, featureNames, reused: true };
    }
    const id = crypto.randomUUID();
    await client.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,$3,$4::jsonb,$5,$6,clock_timestamp())`, [id, SCHEMA_VERSION, TRANSFORM_VERSION, JSON.stringify(featureNames), contentHash, actorId]);
    for (const [position, { name, expression }] of definitions.entries()) {
      const memberHash = expressionHash(expression);
      const reusable = await client.query(`SELECT id FROM ml_feature_versions
        WHERE definition_key=$1 AND content_hash=$2 AND definition_kind=$3 ORDER BY version LIMIT 1`, [name, memberHash, DEFINITION_KIND]);
      let versionId: string = reusable.rows[0]?.id;
      if (!versionId) {
        const next = await client.query('SELECT COALESCE(max(version),0)+1 AS version FROM ml_feature_versions WHERE definition_key=$1', [name]);
        const columns = versionColumns(expression);
        versionId = crypto.randomUUID();
        await client.query(`INSERT INTO ml_feature_versions(id,definition_key,version,display_name,definition_kind,expression,output_type,output_unit,
            value_policy,availability_spec,requires_fit,content_hash,created_by_actor_id,created_at)
          VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9::jsonb,$10::jsonb,$11,$12,$13,clock_timestamp())`,
        [versionId, name, next.rows[0].version, name, DEFINITION_KIND, canonicalJson(expression), columns.output_type, columns.output_unit,
          JSON.stringify(columns.value_policy), JSON.stringify(columns.availability_spec), columns.requires_fit, memberHash, actorId]);
      }
      await client.query(`INSERT INTO ml_feature_set_members(feature_set_id,feature_version_id,model_input,output_position,output_name,transform_settings)
        VALUES($1,$2,true,$3,$4,'{}'::jsonb)`, [id, versionId, position, name]);
    }
    await client.query('COMMIT');
    return { id, contentHash, featureNames, reused: false };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  }
}

router.get('/fields', authenticateToken as any, route(async (_req, res) => {
  res.json(FIELD_LIST);
}));

router.post('/', authenticateToken as any, route(async (req, res) => {
  if (req.user!.userType !== 'admin') throw new FeatureSetRequestError('admin_required', 403);
  const body = req.body;
  if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some(key => key !== 'fields')) {
    throw new FeatureSetRequestError('invalid_job_request', 400);
  }
  let definitions: FeatureDefinition[];
  try {
    definitions = definitionsForFields(body.fields);
  } catch (error) {
    if (error instanceof FeatureFieldError) throw new FeatureSetRequestError(error.code, STATUS[error.code]);
    throw error;
  }
  const client = await getDatabasePool().connect();
  try {
    const registered = await registerFeatureSet(client, definitions, req.user!.id);
    res.status(registered.reused ? 200 : 201).json(registered);
  } finally {
    client.release();
  }
}));

router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof FeatureSetRequestError) {
    res.status(error.status).json({ code: error.code });
    return;
  }
  res.status(503).json({ code: 'job_storage_unavailable' });
});

export default router;
