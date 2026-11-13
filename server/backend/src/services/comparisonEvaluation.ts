import crypto from 'node:crypto';
import type { Pool, PoolClient } from 'pg';

/**
 * 평가 세트(evaluation-set-v1)·모델 버전 비교(config-comparison-v1) API 쪽 구현(config-comparison-evaluation.md v1 구현 범위).
 * API는 접수·검증·조회만 한다. 세트 생성과 비교 평가 실행·결과 기록은 Trainer가 한다(같은 training 큐).
 * - 세트: 합성 없는 원본 분할만(`synthetic: null`). 합성 요청은 400 `synthetic_set_unsupported`.
 * - 비교: 후보 모델 종류 1개, 같은 종류의 현재 운영 버전을 접수 시점에 고정해 넣는다. 교체 추천은 v1에 없다(`not_implemented_v1`).
 */
type Queryable = Pick<Pool, 'query'>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
const DISPLAY_NAME_MAX = 120;
/** 활성 포함 총 상한(config-comparison-evaluation.md Q-L=A). */
export const COMPARISON_MAX_CONFIGS = 8;
/**
 * 비교할 수 있는 구성 schemaVersion. 지금 저장되는 모델 버전은 v1·v2이고(학습 요청이 v1 정책을 보냄), 운영 적용 gate도 v1·v2만 받는다.
 * 그래서 v1 구현은 1·2·3 + 모델 행 1개를 받는다(config-comparison-evaluation.md v1 구현 범위). v3만 받게 바꿀 때는 이 값만 고친다.
 */
export const COMPARABLE_SCHEMA_VERSIONS: readonly number[] = [1, 2, 3];
export const RECOMMENDATIONS_NOT_IMPLEMENTED = 'not_implemented_v1';

export class ComparisonError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}

const isObject = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const uuid = (value: unknown): value is string => typeof value === 'string' && UUID.test(value);
const sha256 = (value: unknown) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

async function inTransaction<T>(pool: Pick<Pool, 'connect'>, work: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect() as PoolClient;
  try {
    await client.query('BEGIN');
    const value = await work(client);
    await client.query('COMMIT');
    return value;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

const enqueue = (client: PoolClient, jobId: string) => client.query(`INSERT INTO powercode_queue.training_jobs (id, payload) VALUES ($1, $2::jsonb)`,
  [crypto.randomUUID(), JSON.stringify({ schemaVersion: 1, jobId, enqueuedAt: new Date().toISOString() })]);

/** v03 training 모양 작업 행(jobRoutes.ts·ruleCandidates.ts와 같은 열). 같은 요청자·키가 이미 있으면 null. */
async function insertJob(client: PoolClient, row: { id: string; kind: string; actorId: string; key: string; requestHash: string; snapshotId: string | null; input: unknown }) {
  const { rows: [inserted] } = await client.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash,
      input_snapshot_id, input_spec, max_attempts, attempt_count, next_run_at, created_at, contract_version, queue, producer_kind, producer_key)
    VALUES ($1, $2, 'queued', $3, 'admin', $4, $5, $6, $7::jsonb, 3, 0, clock_timestamp(), clock_timestamp(), 'v03', 'training', 'user', $3)
    ON CONFLICT (requested_by_actor_id, kind, idempotency_key) DO NOTHING
    RETURNING id::text AS id, status::text AS status, kind::text AS kind, input_spec AS "inputSpec"`,
  [row.id, row.kind, row.actorId, row.key, row.requestHash, row.snapshotId, JSON.stringify(row.input)]);
  return inserted ?? null;
}

/** 같은 요청자·키의 이전 작업. 본문이 다르면 409. */
async function priorJob(client: PoolClient, kind: string, actorId: string, key: string, requestHash: string) {
  const { rows: [prior] } = await client.query(`SELECT id::text AS id, status::text AS status, kind::text AS kind, input_spec AS "inputSpec", request_hash
    FROM run_jobs WHERE requested_by_actor_id = $1 AND kind::text = $2 AND idempotency_key = $3`, [actorId, kind, key]);
  if (!prior) return null;
  if (prior.request_hash !== requestHash) throw new ComparisonError('idempotency_payload_conflict', 409);
  const { request_hash: _hash, ...job } = prior;
  return job as { id: string; status: string; kind: string; inputSpec: Record<string, unknown> };
}

// ---------- 평가 세트 ----------

export type EvaluationSetRequest = { sourceId: string; snapshotId: string; purpose: 'validation' | 'test'; displayName: string; synthetic: null };

export function parseEvaluationSetRequest(body: unknown): EvaluationSetRequest {
  const invalid = () => new ComparisonError('invalid_evaluation_set_request');
  if (!isObject(body) || Object.keys(body).some((key) => !['sourceId', 'snapshotId', 'purpose', 'displayName', 'synthetic'].includes(key))) throw invalid();
  const { sourceId, snapshotId, purpose, displayName, synthetic } = body;
  if (!uuid(sourceId) || !uuid(snapshotId) || (purpose !== 'validation' && purpose !== 'test') || typeof displayName !== 'string') throw invalid();
  const name = displayName.trim();
  if (!name || name.length > DISPLAY_NAME_MAX) throw invalid();
  if (synthetic !== undefined && synthetic !== null) throw new ComparisonError('synthetic_set_unsupported');
  return { sourceId: sourceId.toLowerCase(), snapshotId: snapshotId.toLowerCase(), purpose, displayName: name, synthetic: null };
}

/**
 * POST /api/evaluation-sets: 작업 행(kind evaluation_set) + 세트 행(queued) + 큐 메시지를 한 트랜잭션으로.
 * 작업 입력은 Trainer가 읽는 키 `{kind, evaluationSetId, sourceId, snapshotId, purpose}`만 둔다(이름은 세트 행에).
 */
export async function createEvaluationSet(pool: Pick<Pool, 'connect'>, request: EvaluationSetRequest, actorId: string, idempotencyKey: string | null) {
  const requestHash = sha256({ kind: 'evaluation_set', ...request });
  const key = idempotencyKey ?? crypto.randomUUID();
  return inTransaction(pool, async (client) => {
    const replay = async () => {
      const job = await priorJob(client, 'evaluation_set', actorId, key, requestHash);
      if (!job) return null;
      const { rows: [set] } = await client.query(`SELECT id::text AS id, status FROM data_evaluation_sets WHERE job_id = $1`, [job.id]);
      if (!set) throw new Error('evaluation_set_row_missing');
      return { created: false, body: { id: set.id, jobId: job.id, status: set.status } };
    };
    if (idempotencyKey !== null) {
      const existing = await replay();
      if (existing) return existing;
    }
    const source = await client.query('SELECT 1 FROM ops_sources WHERE id = $1', [request.sourceId]);
    if (!source.rowCount) throw new ComparisonError('source_not_found', 404);
    const { rows: [snapshot] } = await client.query<{ status: string; schemaVersion: string }>(
      `SELECT status::text AS status, schema_version AS "schemaVersion" FROM data_snapshots WHERE id = $1 AND source_id = $2`, [request.snapshotId, request.sourceId]);
    if (!snapshot) throw new ComparisonError('snapshot_not_found', 404);
    if (snapshot.schemaVersion === 'prepared-features-v1') throw new ComparisonError('prepared_not_comparable');
    if (snapshot.status !== 'ready') throw new ComparisonError('snapshot_not_ready', 409);
    // Trainer는 process-v1 스냅샷만 세트로 만든다(아니면 작업 실패 evaluation_set_snapshot_unavailable). 접수 때 같은 코드로 막는다.
    if (snapshot.schemaVersion !== 'process-v1') throw new ComparisonError('evaluation_set_snapshot_unavailable', 409);
    const setId = crypto.randomUUID();
    const jobId = crypto.randomUUID();
    const input = { kind: 'evaluation_set', evaluationSetId: setId, sourceId: request.sourceId, snapshotId: request.snapshotId, purpose: request.purpose };
    const job = await insertJob(client, { id: jobId, kind: 'evaluation_set', actorId, key, requestHash, snapshotId: request.snapshotId, input });
    if (!job) {
      // 같은 키의 동시 요청이 먼저 넣은 경우.
      const existing = await replay();
      if (!existing) throw new ComparisonError('idempotency_payload_conflict', 409);
      return existing;
    }
    await client.query(`INSERT INTO data_evaluation_sets (id, job_id, source_id, snapshot_id, purpose, display_name, synthetic_request, created_by_actor_id)
      VALUES ($1, $2, $3, $4, $5, $6, NULL, $7)`, [setId, jobId, request.sourceId, request.snapshotId, request.purpose, request.displayName, actorId]);
    await enqueue(client, jobId);
    return { created: true, body: { id: setId, jobId, status: 'queued' } };
  });
}

type SetRow = {
  id: string; jobId: string; sourceId: string; snapshotId: string; purpose: string; displayName: string; status: string;
  synthetic: Record<string, unknown> | null; binding: Record<string, unknown> | null; generation: Record<string, unknown> | null;
  contentHash: string | null; errorCode: string | null; createdAt: string; readyAt: string | null;
};
const SET_COLUMNS = `s.id::text AS id, s.job_id::text AS "jobId", s.source_id::text AS "sourceId", s.snapshot_id::text AS "snapshotId", s.purpose::text AS purpose,
  s.display_name AS "displayName", s.status, s.synthetic_request AS synthetic, s.synthetic_profile_binding AS binding, s.generation,
  s.content_hash AS "contentHash", s.error_code AS "errorCode", ${ISO('s.created_at')} AS "createdAt",
  CASE WHEN s.ready_at IS NULL THEN NULL ELSE ${ISO('s.ready_at')} END AS "readyAt"`;
const count = (value: unknown) => (Number.isSafeInteger(value) ? value as number : null);

/** 목록 항목(계약 조회 절). 산출물 경로는 내지 않는다(`artifactRef`는 null). 생성 전·실패는 `generation` null. */
const setItem = (row: SetRow) => ({
  id: row.id, sourceId: row.sourceId, snapshotId: row.snapshotId, purpose: row.purpose, displayName: row.displayName, status: row.status,
  synthetic: row.synthetic,
  generation: row.status === 'ready' && row.generation && row.contentHash ? {
    rowCount: count(row.generation.rowCount), originalRowCount: count(row.generation.originalRowCount), syntheticRowCount: count(row.generation.syntheticRowCount),
    truthCount: count(row.generation.truthCount), contentHash: row.contentHash, artifactRef: null,
  } : null,
  createdAt: row.createdAt, readyAt: row.readyAt,
});
const setDetail = (row: SetRow) => {
  const item = setItem(row);
  const extra = (key: string) => (row.generation && Object.hasOwn(row.generation, key) ? { [key]: row.generation[key] } : {});
  return {
    ...item, jobId: row.jobId, errorCode: row.errorCode, syntheticProfileBinding: row.binding,
    generation: item.generation ? { ...item.generation, ...extra('trialStatuses'), ...extra('unsupportedScenarios') } : null,
  };
};

const LIMIT_DEFAULT = 50;
const LIMIT_MAX = 100;
type Cursor = { at: string; id: string };
const encodeCursor = (cursor: Cursor) => Buffer.from(JSON.stringify(cursor)).toString('base64url');
function decodeCursor(value: unknown): Cursor | null {
  if (value === undefined) return null;
  try {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,512}$/.test(value)) throw new Error();
    const parsed = JSON.parse(Buffer.from(value, 'base64url').toString('utf8')) as Record<string, unknown>;
    if (Object.keys(parsed).sort().join() !== 'at,id' || typeof parsed.at !== 'string' || !Number.isFinite(Date.parse(parsed.at)) || !uuid(parsed.id)) throw new Error();
    return { at: parsed.at, id: parsed.id };
  } catch { throw new ComparisonError('invalid_cursor'); }
}
function parseLimit(value: unknown) {
  if (value === undefined) return LIMIT_DEFAULT;
  if (typeof value !== 'string' || !/^[1-9]\d{0,2}$/.test(value) || Number(value) > LIMIT_MAX) throw new ComparisonError('invalid_limit');
  return Number(value);
}
const optionalUuid = (value: unknown, code: string) => {
  if (value === undefined) return null;
  if (!uuid(value)) throw new ComparisonError(code);
  return value.toLowerCase();
};
const page = <T>(rows: T[], limit: number, cursorOf: (row: T) => Cursor) => {
  const items = rows.slice(0, limit);
  const hasMore = rows.length > limit;
  const last = items.at(-1);
  return { items, pagination: { limit, hasMore, nextCursor: hasMore && last ? encodeCursor(cursorOf(last)) : null } };
};

export function parseSetListQuery(query: Record<string, unknown>) {
  if (Object.keys(query).some((key) => !['sourceId', 'limit', 'cursor'].includes(key))) throw new ComparisonError('unknown_evaluation_set_filter');
  return { sourceId: optionalUuid(query.sourceId, 'invalid_source_id'), limit: parseLimit(query.limit), cursor: decodeCursor(query.cursor) };
}

export async function readEvaluationSets(db: Queryable, filter: ReturnType<typeof parseSetListQuery>) {
  const { rows } = await db.query<SetRow & { rawAt: string }>(`SELECT ${SET_COLUMNS}, s.created_at::text AS "rawAt" FROM data_evaluation_sets s
    WHERE ($1::uuid IS NULL OR s.source_id = $1)
      AND ($2::timestamptz IS NULL OR (s.created_at, s.id) < ($2::timestamptz, $3::uuid))
    ORDER BY s.created_at DESC, s.id DESC LIMIT $4`, [filter.sourceId, filter.cursor?.at ?? null, filter.cursor?.id ?? null, filter.limit + 1]);
  const result = page(rows, filter.limit, (row) => ({ at: row.rawAt, id: row.id }));
  return { items: result.items.map(setItem), pagination: result.pagination };
}

export async function readEvaluationSet(db: Queryable, id: string) {
  const { rows: [row] } = await db.query<SetRow>(`SELECT ${SET_COLUMNS} FROM data_evaluation_sets s WHERE s.id = $1`, [id]);
  if (!row) throw new ComparisonError('evaluation_set_not_found', 404);
  return setDetail(row);
}

/**
 * GET /api/evaluation-sets/snapshots: 세트를 만들 수 있는 기준 스냅샷 = 모델 버전의 학습 스냅샷(모델 산출물의 snapshot_id) 중 준비된 process-v1.
 * 화면 기본값(선택 버전의 학습 스냅샷 검증 분할)에 쓴다. 스냅샷 목록 API가 계약에 없어 WebUI가 정한 형식이다(evaluations-ui.md).
 */
export async function readSetSnapshots(db: Queryable, sourceId: string | null) {
  const { rows } = await db.query(`SELECT s.id::text AS id, s.source_id::text AS "sourceId", ${ISO('s.created_at')} AS "createdAt",
      s.selection_spec->>'from' AS "from", s.selection_spec->>'until' AS "until",
      array_agg(DISTINCT cm.config_version_id::text ORDER BY cm.config_version_id::text) AS "configVersionIds"
    FROM data_snapshots s JOIN ml_artifacts a ON a.snapshot_id = s.id AND a.kind = 'model'
    JOIN ml_config_models cm ON cm.model_artifact_id = a.id
    WHERE s.status = 'ready' AND s.schema_version = 'process-v1' AND ($1::uuid IS NULL OR s.source_id = $1)
    GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC LIMIT 200`, [sourceId]);
  return { items: rows };
}

// ---------- 비교 작업 ----------

export type ComparisonRequest = { kind: 'compare'; sourceId: string; evaluationSetId: string; configVersionIds: string[] };

export function parseComparisonRequest(body: unknown): ComparisonRequest {
  const invalid = () => new ComparisonError('invalid_comparison_request');
  if (!isObject(body) || Object.keys(body).some((key) => !['kind', 'sourceId', 'evaluationSetId', 'configVersionIds'].includes(key))) throw invalid();
  const { sourceId, evaluationSetId, configVersionIds } = body;
  if (body.kind !== 'compare' || !uuid(sourceId) || !uuid(evaluationSetId) || !Array.isArray(configVersionIds) || !configVersionIds.every(uuid)) throw invalid();
  const ids = configVersionIds.map((id) => (id as string).toLowerCase());
  if (new Set(ids).size !== ids.length) throw invalid();
  if (!ids.length) throw new ComparisonError('comparison_configs_required');
  if (ids.length > COMPARISON_MAX_CONFIGS) throw new ComparisonError('comparison_too_many_configs');
  return { kind: 'compare', sourceId: sourceId.toLowerCase(), evaluationSetId: evaluationSetId.toLowerCase(), configVersionIds: [...ids].sort() };
}

type VersionRow = { id: string; sourceId: string; schemaVersion: unknown; modelKeys: string[] };
const comparable = (row: VersionRow) => COMPARABLE_SCHEMA_VERSIONS.includes(row.schemaVersion as number) && row.modelKeys.length === 1;

/**
 * POST /api/jobs kind=compare. 접수 시 고정: 같은 종류 운영 버전(기준선), 활성 집합·룰 세트·판정 정책 리비전.
 * request_hash는 요청 본문만(같은 키 재요청은 그 사이 운영 상태가 바뀌어도 같은 작업 200).
 */
export async function enqueueComparison(pool: Pick<Pool, 'connect'>, request: ComparisonRequest, actorId: string, idempotencyKey: string | null) {
  const requestHash = sha256(request);
  const key = idempotencyKey ?? crypto.randomUUID();
  return inTransaction(pool, async (client) => {
    if (idempotencyKey !== null) {
      const existing = await priorJob(client, 'compare', actorId, key, requestHash);
      if (existing) return { created: false, job: existing };
    }
    const source = await client.query('SELECT 1 FROM ops_sources WHERE id = $1', [request.sourceId]);
    if (!source.rowCount) throw new ComparisonError('source_not_found', 404);
    const { rows: [set] } = await client.query<{ sourceId: string; status: string }>(
      `SELECT source_id::text AS "sourceId", status FROM data_evaluation_sets WHERE id = $1`, [request.evaluationSetId]);
    if (!set) throw new ComparisonError('evaluation_set_not_found', 404);
    if (set.sourceId !== request.sourceId) throw new ComparisonError('evaluation_set_source_mismatch');
    if (set.status !== 'ready') throw new ComparisonError('evaluation_set_not_ready', 409);
    const versions = async (ids: string[]) => (await client.query<VersionRow>(`SELECT c.id::text AS id, c.source_id::text AS "sourceId",
        c.configuration_spec->'schemaVersion' AS "schemaVersion",
        COALESCE(array_agg(m.model_key ORDER BY m.model_key) FILTER (WHERE m.model_key IS NOT NULL), '{}') AS "modelKeys"
      FROM ml_config_versions c LEFT JOIN ml_config_models m ON m.config_version_id = c.id
      WHERE c.id = ANY($1::uuid[]) GROUP BY c.id`, [ids])).rows;
    const candidates = await versions(request.configVersionIds);
    if (candidates.length !== request.configVersionIds.length) throw new ComparisonError('config_not_found', 404);
    if (candidates.some((row) => row.sourceId !== request.sourceId)) throw new ComparisonError('config_source_mismatch');
    if (!candidates.every(comparable)) throw new ComparisonError('comparison_config_not_comparable');
    const modelKeys = [...new Set(candidates.map((row) => row.modelKeys[0]))];
    if (modelKeys.length !== 1) throw new ComparisonError('comparison_model_key_mixed');
    const modelKey = modelKeys[0];

    const { rows: [state] } = await client.query(`SELECT rule_set_revision::text AS "ruleRevision", rule_set_content_hash AS "ruleHash",
        active_model_set_revision::text AS "setRevision", active_models AS models,
        detection_policy_revision::text AS "policyRevision", detection_policy AS policy FROM ops_operational_state`);
    if (!state) throw new Error('operational_state_unavailable');
    const models: { configVersionId?: unknown; modelKey?: unknown }[] = Array.isArray(state.models) ? state.models : [];
    const active = models.find((model) => model.modelKey === modelKey);
    const baselines = active && typeof active.configVersionId === 'string' ? [{ modelKey, configVersionId: active.configVersionId.toLowerCase() }] : [];
    if (baselines.length && !request.configVersionIds.includes(baselines[0].configVersionId)) {
      const [baseline] = await versions([baselines[0].configVersionId]);
      // 기준선이 다른 출처면 Trainer 결과 기록(validate_comparison_lineage)이 거절한다. 접수 때 막는다.
      if (!baseline || baseline.sourceId !== request.sourceId) throw new ComparisonError('comparison_baseline_source_mismatch', 409);
    }
    const configVersionIds = [...new Set([...request.configVersionIds, ...baselines.map((item) => item.configVersionId)])].sort();
    if (configVersionIds.length > COMPARISON_MAX_CONFIGS) throw new ComparisonError('comparison_too_many_configs');
    const input = {
      kind: 'compare', sourceId: request.sourceId, evaluationSetId: request.evaluationSetId, configVersionIds, baselines,
      activeModelSetRevision: Number(state.setRevision), ruleSetRevision: Number(state.ruleRevision), ruleSetContentHash: state.ruleHash,
      // 정책 리비전 0(행 없음)은 null 그대로 — Trainer가 내장 기본 정책으로 읽는다(계약 v1 구현 범위).
      detectionPolicyRevision: Number(state.policyRevision), detectionPolicy: state.policy ?? null,
    };
    const id = crypto.randomUUID();
    const job = await insertJob(client, { id, kind: 'compare', actorId, key, requestHash, snapshotId: null, input });
    if (!job) {
      const existing = await priorJob(client, 'compare', actorId, key, requestHash);
      if (!existing) throw new ComparisonError('idempotency_payload_conflict', 409);
      return { created: false, job: existing };
    }
    await enqueue(client, id);
    return { created: true, job };
  });
}

// ---------- 비교 조회 ----------

type ComparisonRow = {
  jobId: string; jobStatus: string; jobError: string | null; input: Record<string, any>; createdAt: string; rawAt: string; finishedAt: string | null;
  comparisonId: string | null; comparisonStatus: string | null; summary: Record<string, any> | null; completedAt: string | null;
  succeededCount: number; failedCount: number;
};
const COMPARISON_FROM = `FROM run_jobs j LEFT JOIN run_config_comparisons c ON c.job_id = j.id
  LEFT JOIN LATERAL (SELECT count(*) FILTER (WHERE i.status = 'succeeded')::int AS ok, count(*) FILTER (WHERE i.status = 'failed')::int AS bad
    FROM run_config_comparison_items i WHERE i.comparison_id = c.id) n ON true`;
const COMPARISON_COLUMNS = `j.id::text AS "jobId", j.status::text AS "jobStatus", j.error_code AS "jobError", j.input_spec AS input,
  ${ISO('j.created_at')} AS "createdAt", j.created_at::text AS "rawAt", CASE WHEN j.finished_at IS NULL THEN NULL ELSE ${ISO('j.finished_at')} END AS "finishedAt",
  c.id::text AS "comparisonId", c.status AS "comparisonStatus", c.summary,
  CASE WHEN c.completed_at IS NULL THEN NULL ELSE ${ISO('c.completed_at')} END AS "completedAt",
  COALESCE(n.ok, 0) AS "succeededCount", COALESCE(n.bad, 0) AS "failedCount"`;

/** 상태: 비교 묶음이 있으면 그 값, 없으면 작업 상태(대기·실행 중·실패). */
const statusOf = (row: ComparisonRow) => row.comparisonStatus ?? row.jobStatus;
const baselinesOf = (row: ComparisonRow) => (Array.isArray(row.summary?.baselines) ? row.summary!.baselines : Array.isArray(row.input.baselines) ? row.input.baselines : [])
  .map((item: Record<string, unknown>) => ({ modelKey: item.modelKey, configVersionId: item.configVersionId }));

type ActiveContext = { models: { configVersionId: string; modelKey: string }[]; revision: number; setStatus: Map<string, string> };
async function activeContext(db: Queryable, setIds: string[]): Promise<ActiveContext> {
  const { rows: [state] } = await db.query(`SELECT active_model_set_revision::text AS revision, active_models AS models FROM ops_operational_state`);
  if (!state) throw new Error('operational_state_unavailable');
  const sets = setIds.length ? (await db.query<{ id: string; status: string }>(`SELECT id::text AS id, status FROM data_evaluation_sets WHERE id = ANY($1::uuid[])`, [setIds])).rows : [];
  const models = (Array.isArray(state.models) ? state.models : []).map((model: Record<string, unknown>) => ({ configVersionId: String(model.configVersionId).toLowerCase(), modelKey: String(model.modelKey) }));
  return { models, revision: Number(state.revision), setStatus: new Map(sets.map((set) => [set.id, set.status])) };
}

/** 추천 상태는 저장하지 않고 조회 때 파생한다(계약 stale 절). v1은 추천이 없어(null) 상태도 없다. */
const recommendationState = (rec: Record<string, any>, context: ActiveContext, setId: string) => {
  const target = typeof rec.targetVersionId === 'string' ? rec.targetVersionId.toLowerCase() : null;
  if (!target) return 'none';
  if (context.models.some((model) => model.configVersionId === target)) return 'applied';
  const activeNow = context.models.find((model) => model.modelKey === rec.modelKey)?.configVersionId ?? null;
  const baseline = typeof rec.baselineConfigVersionId === 'string' ? rec.baselineConfigVersionId.toLowerCase() : null;
  return activeNow === baseline && context.setStatus.get(setId) === 'ready' ? 'current' : 'stale';
};
const recommendationsOf = (row: ComparisonRow, context: ActiveContext) => {
  const stored = row.summary?.replacementRecommendations;
  if (Array.isArray(stored)) {
    return { replacementRecommendations: stored.map((rec) => ({ ...rec, recommendationState: recommendationState(rec, context, String(row.input.evaluationSetId)) })), replacementRecommendationsReason: null };
  }
  // 완료 전은 아직 없음(null, 사유 없음). 완료 뒤 null은 저장된 사유 그대로(v1 `not_implemented_v1`).
  const reason = row.summary ? (typeof row.summary.replacementRecommendationsReason === 'string' ? row.summary.replacementRecommendationsReason : RECOMMENDATIONS_NOT_IMPLEMENTED) : null;
  return { replacementRecommendations: null, replacementRecommendationsReason: reason };
};

const listItem = (row: ComparisonRow, context: ActiveContext) => {
  const { replacementRecommendations, replacementRecommendationsReason } = recommendationsOf(row, context);
  return {
    comparisonId: row.comparisonId, jobId: row.jobId, evaluationSetId: row.input.evaluationSetId ?? null, baselines: baselinesOf(row),
    activeModelSetRevision: Number(row.input.activeModelSetRevision), status: statusOf(row), errorCode: row.jobError,
    // 완료 뒤는 Trainer summary의 수(접수 뒤 지워진 구성 `config_not_found`는 항목 표에 없고 summary.items에만 있다). 없으면 항목 표에서 센다.
    itemCount: count(row.summary?.itemCount) ?? (Array.isArray(row.input.configVersionIds) ? row.input.configVersionIds.length : 0),
    succeededCount: count(row.summary?.succeededCount) ?? row.succeededCount, failedCount: count(row.summary?.failedCount) ?? row.failedCount, createdAt: row.createdAt, completedAt: row.completedAt ?? row.finishedAt,
    recommendations: replacementRecommendations?.map((rec: Record<string, any>) => ({ modelKey: rec.modelKey, targetVersionId: rec.targetVersionId ?? null, state: rec.recommendationState })) ?? null,
    recommendationsReason: replacementRecommendationsReason,
  };
};

export function parseComparisonListQuery(query: Record<string, unknown>) {
  if (Object.keys(query).some((key) => !['sourceId', 'evaluationSetId', 'modelKey', 'scope', 'limit', 'cursor'].includes(key))) throw new ComparisonError('unknown_comparison_filter');
  const { modelKey, scope } = query;
  if (modelKey !== undefined && (typeof modelKey !== 'string' || !/^[a-z][a-z0-9_-]{1,63}$/.test(modelKey))) throw new ComparisonError('invalid_model_key');
  if (scope !== undefined && scope !== 'current' && scope !== 'all') throw new ComparisonError('invalid_scope');
  return {
    sourceId: optionalUuid(query.sourceId, 'invalid_source_id'), evaluationSetId: optionalUuid(query.evaluationSetId, 'invalid_evaluation_set_id'),
    modelKey: (modelKey as string | undefined) ?? null, scope: (scope as 'current' | 'all' | undefined) ?? 'all', limit: parseLimit(query.limit), cursor: decodeCursor(query.cursor),
  };
}

/**
 * GET /api/comparisons: 비교 작업 최신순(완료 전 작업 포함 — 묶음은 완료 때 생기므로 `comparisonId` null).
 * `scope=current`(WebUI 탭): 접수 때 활성 집합 리비전이 지금과 같고 실패가 아닌 것.
 */
export async function readComparisons(db: Queryable, filter: ReturnType<typeof parseComparisonListQuery>) {
  const { rows: [state] } = await db.query(`SELECT active_model_set_revision AS revision FROM ops_operational_state`);
  const { rows } = await db.query<ComparisonRow>(`SELECT ${COMPARISON_COLUMNS} ${COMPARISON_FROM}
    WHERE j.kind::text = 'compare'
      AND ($1::uuid IS NULL OR j.input_spec->>'sourceId' = ($1::uuid)::text)
      AND ($2::uuid IS NULL OR j.input_spec->>'evaluationSetId' = ($2::uuid)::text)
      AND ($3::text IS NULL OR EXISTS (SELECT 1 FROM ml_config_models m WHERE m.model_key = $3
        AND m.config_version_id::text IN (SELECT jsonb_array_elements_text(j.input_spec->'configVersionIds'))))
      AND (NOT $4::boolean OR ((j.input_spec->>'activeModelSetRevision')::bigint = $5::bigint AND COALESCE(c.status, j.status::text) <> 'failed'))
      AND ($6::timestamptz IS NULL OR (j.created_at, j.id) < ($6::timestamptz, $7::uuid))
    ORDER BY j.created_at DESC, j.id DESC LIMIT $8`,
  [filter.sourceId, filter.evaluationSetId, filter.modelKey, filter.scope === 'current', state?.revision ?? null, filter.cursor?.at ?? null, filter.cursor?.id ?? null, filter.limit + 1]);
  const result = page(rows, filter.limit, (row) => ({ at: row.rawAt, id: row.jobId }));
  const context = await activeContext(db, [...new Set(result.items.map((row) => String(row.input.evaluationSetId)).filter(uuid))]);
  return { items: result.items.map((row) => listItem(row, context)), pagination: result.pagination };
}

/**
 * GET /api/comparisons/:id — 비교 묶음 ID 또는 비교 작업 ID. 완료 뒤: 저장된 `summary`(= 작업 resultSummary) 전체 + 파생 추천 상태.
 * 완료 전·묶음 없는 실패: 접수 때 고정한 값과 접수 항목만(항목 `status: "pending"`).
 */
export async function readComparison(db: Queryable, id: string) {
  const { rows: [row] } = await db.query<ComparisonRow>(`SELECT ${COMPARISON_COLUMNS} ${COMPARISON_FROM}
    WHERE j.kind::text = 'compare' AND (c.id = $1 OR j.id = $1) ORDER BY (c.id = $1) DESC NULLS LAST LIMIT 1`, [id]);
  if (!row) throw new ComparisonError('comparison_not_found', 404);
  const context = await activeContext(db, uuid(row.input.evaluationSetId) ? [String(row.input.evaluationSetId)] : []);
  const recommendations = recommendationsOf(row, context);
  if (row.summary) {
    return {
      ...row.summary, comparisonId: row.comparisonId, jobId: row.jobId, status: statusOf(row), errorCode: row.jobError,
      completedAt: row.completedAt, ...recommendations,
    };
  }
  const ids: string[] = Array.isArray(row.input.configVersionIds) ? row.input.configVersionIds : [];
  const keys = ids.length ? new Map((await db.query<{ id: string; modelKey: string }>(`SELECT config_version_id::text AS id, min(model_key) AS "modelKey"
    FROM ml_config_models WHERE config_version_id = ANY($1::uuid[]) GROUP BY config_version_id`, [ids])).rows.map((item) => [item.id, item.modelKey])) : new Map();
  const baselines = baselinesOf(row);
  return {
    comparisonId: null, jobId: row.jobId, status: statusOf(row), errorCode: row.jobError, completedAt: row.finishedAt,
    evaluationSet: { id: row.input.evaluationSetId ?? null }, baselines, activeModelSetRevision: Number(row.input.activeModelSetRevision),
    ruleSet: { revision: Number(row.input.ruleSetRevision), contentHash: row.input.ruleSetContentHash ?? null, fixedAt: 'accepted' },
    detectionPolicy: { revision: Number(row.input.detectionPolicyRevision), policy: row.input.detectionPolicy ?? null, fixedAt: 'accepted' },
    items: ids.map((configVersionId) => ({
      configVersionId, modelKey: keys.get(configVersionId) ?? null,
      role: baselines.some((item: { configVersionId: unknown }) => item.configVersionId === configVersionId) ? 'active' : 'candidate', status: 'pending',
    })),
    ...recommendations,
  };
}
