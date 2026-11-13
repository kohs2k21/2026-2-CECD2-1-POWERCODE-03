import type { Pool, PoolClient } from 'pg';
import { mlMethod } from '../ruleset/methodCatalog.js';
import { RULE_SET_LOCK, enabledRuleCount, type ActorResolver } from '../ruleset/ruleSetStore.js';

/**
 * 활성 모델 집합(model-version.md 활성 집합 절, 표 ops_active_model_set_revisions). 현재 상태 = 최대 revision 행, append-only.
 * 변경은 트랜잭션 1개: 공통 잠금(룰 세트·정책 저장과 같은 키) → 현재 리비전 비교 → gate·제약 검사 → revision+1 INSERT.
 * 어느 단계에서 실패해도 활성 집합은 바뀌지 않는다. Detector는 시도 시작마다 뷰 ops_operational_state로 이 표를 읽는다.
 */
type Queryable = Pick<Pool, 'query'>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const NOTE_MAX = 500;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
/** 정책 행이 없을 때(리비전 0)의 내장 기본 정책(model-version.md 운영 설정 절). */
export const DEFAULT_DETECTION_POLICY = { version: 2, policyKey: 'category-k-of-n-or-v1', k: { hard: 1, soft: 1, ml: 1 }, deadlineMs: 3000 };

export class ActiveSetError extends Error {
  constructor(readonly code: string, readonly status = 400, readonly detail?: Record<string, unknown>) { super(code); }
}

export type ActiveModel = { configVersionId: string; modelKey: string; displayName: string | null; activatedAtRevision: number };
type Change = {
  type: 'activated' | 'replaced' | 'retired' | 'restored'; configVersionId: string; modelKey: string; previousConfigVersionId?: string;
  /** 조작 시점 근거(model-version.md gate_evidence 제안 항목). 활성 집합 표에 별도 열이 없어 변경 항목에 남긴다. */
  gate?: Record<string, unknown>;
};
type SetRow = {
  revision: string; snapshot: { models?: ActiveModel[] }; changes: Change[]; note: string | null;
  restoredFromRevision: string | null; actorId: string; changedAt: string;
};
const COLUMNS = `r.revision::text AS revision,snapshot,changes,note,restored_from_revision::text AS "restoredFromRevision",
  changed_by_actor_id AS "actorId",${ISO('changed_at')} AS "changedAt"`;

async function latestSet(db: Queryable): Promise<SetRow> {
  const { rows: [row] } = await db.query<SetRow>(`SELECT ${COLUMNS} FROM ops_active_model_set_revisions r ORDER BY r.revision DESC LIMIT 1`);
  // r0은 마이그레이션이 넣는다. 행이 없으면 빈 집합으로 대신하지 않고 저장소 오류로 둔다.
  if (!row) throw new Error('active_model_set_revision_row_missing');
  return row;
}
async function setAt(db: Queryable, revision: number): Promise<SetRow | null> {
  const { rows: [row] } = await db.query<SetRow>(`SELECT ${COLUMNS} FROM ops_active_model_set_revisions r WHERE r.revision=$1`, [revision]);
  return row ?? null;
}
const modelsOf = (row: SetRow): ActiveModel[] => (row.snapshot.models ?? []).map((model) => ({
  configVersionId: model.configVersionId, modelKey: model.modelKey, displayName: model.displayName ?? null, activatedAtRevision: Number(model.activatedAtRevision),
}));

/** 스냅샷의 표시 이름은 저장 시점 값이다. 구성의 현재 이름(불변 행)을 우선해 보여 준다. */
async function withNames(db: Queryable, models: ActiveModel[]) {
  if (!models.length) return models;
  const { rows } = await db.query<{ id: string; name: string | null }>(
    `SELECT id::text,configuration_spec->>'displayName' AS name FROM ml_config_versions WHERE id=ANY($1::uuid[])`, [models.map((model) => model.configVersionId)]);
  const names = new Map(rows.map((row) => [row.id, row.name]));
  return models.map((model) => ({ ...model, displayName: names.get(model.configVersionId) ?? model.displayName }));
}

async function readOnly<T>(pool: Pick<Pool, 'connect'>, read: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect() as PoolClient;
  try {
    await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
    const value = await read(client);
    await client.query('COMMIT');
    return value;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

/**
 * GET /api/operational-state(model-version.md 일관 읽기 요구사항): 룰 세트·활성 집합·정책 세 리비전을 뷰 한 문장으로 읽는다.
 * 화면은 판정 정책을 보여 주지 않는다(사용자 11/02 Q-M10). 값은 운영 담당용으로 응답에만 둔다.
 */
export function readOperationalState(pool: Pick<Pool, 'connect'>, actor: ActorResolver) {
  return readOnly(pool, async (client) => {
    const { rows: [state] } = await client.query(`SELECT rule_set_revision::text AS "ruleRevision",rule_set_content_hash AS "ruleHash",
        rule_set_content_hash_version AS "ruleHashVersion",active_model_set_revision::text AS "setRevision",
        detection_policy_revision::text AS "policyRevision",detection_policy AS policy,${ISO('clock_timestamp()')} AS "readAt"
      FROM ops_operational_state`);
    if (!state) throw new Error('operational_state_unavailable');
    const { rows: [rule] } = await client.query<{ actorId: string; savedAt: string }>(
      `SELECT saved_by_actor_id AS "actorId",${ISO('saved_at')} AS "savedAt" FROM ops_rule_set_revisions WHERE revision=$1`, [state.ruleRevision]);
    const set = await setAt(client, Number(state.setRevision));
    if (!rule || !set) throw new Error('operational_state_unavailable');
    const policyRevision = Number(state.policyRevision);
    return {
      ruleSet: { revision: Number(state.ruleRevision), contentHash: state.ruleHash, contentHashVersion: state.ruleHashVersion, savedAt: rule.savedAt, savedBy: await actor(rule.actorId) },
      activeModelSet: {
        revision: Number(set.revision), models: await withNames(client, modelsOf(set)), changedAt: set.changedAt, changedBy: await actor(set.actorId),
        restoredFromRevision: set.restoredFromRevision === null ? null : Number(set.restoredFromRevision),
      },
      detectionPolicy: { revision: policyRevision, policy: policyRevision === 0 ? DEFAULT_DETECTION_POLICY : state.policy, isDefault: policyRevision === 0 },
      readAt: state.readAt,
    };
  });
}

// ---------- 변경 요청 ----------

export type AcceptedRecommendation = { comparisonJobId: string; modelKey: string; targetVersionId: string };
export type ActiveSetRequest =
  | { operation: 'apply'; evaluationId: string; expectedRevision: number; acceptedRecommendation: AcceptedRecommendation | null }
  | { operation: 'replace'; evaluationId: string; expectedRevision: number; expectedActiveVersionId: string; acceptedRecommendation: AcceptedRecommendation | null }
  | { operation: 'retire'; expectedRevision: number; note: string | null }
  | { operation: 'restore'; expectedRevision: number; targetRevision: number; confirmRetire: boolean };

const isObject = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const revisionValue = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;

/**
 * v1·v2 운영(ops_system_state 단일 구성) 요청인지. 그 경로는 `expectedActiveVersionId`를 apply·restore 본문에 둔다(configRoutes 기존 형식).
 * 활성 집합의 apply·restore 본문에는 그 키가 없다(replace만 둔다). 그래서 apply·restore + 이 키 = v1·v2 경로로 그대로 보낸다.
 */
export const isLegacyApplication = (body: unknown) =>
  isObject(body) && (body.operation === 'apply' || body.operation === 'restore') && Object.hasOwn(body, 'expectedActiveVersionId');

function parseAccepted(value: unknown): AcceptedRecommendation | null {
  if (value === undefined || value === null) return null;
  if (!isObject(value) || Object.keys(value).sort().join() !== 'comparisonJobId,modelKey,targetVersionId' ||
      typeof value.comparisonJobId !== 'string' || !UUID.test(value.comparisonJobId) ||
      typeof value.modelKey !== 'string' || !/^[a-z][a-z0-9_-]{1,63}$/.test(value.modelKey) ||
      typeof value.targetVersionId !== 'string' || !UUID.test(value.targetVersionId)) throw new ActiveSetError('invalid_application_request');
  return { comparisonJobId: value.comparisonJobId.toLowerCase(), modelKey: value.modelKey, targetVersionId: value.targetVersionId.toLowerCase() };
}

const KEYS: Record<ActiveSetRequest['operation'], { required: string[]; optional: string[] }> = {
  apply: { required: ['operation', 'evaluationId', 'expectedRevision'], optional: ['acceptedRecommendation'] },
  replace: { required: ['operation', 'evaluationId', 'expectedRevision', 'expectedActiveVersionId'], optional: ['acceptedRecommendation'] },
  retire: { required: ['operation', 'expectedRevision', 'confirmRetire'], optional: ['note'] },
  restore: { required: ['operation', 'expectedRevision', 'targetRevision'], optional: ['confirmRetire'] },
};

/** 본문 형식 검사(model-version.md 활성 집합 조작 표). 형식 오류는 모두 400 invalid_application_request. */
export function parseActiveSetRequest(body: unknown): ActiveSetRequest {
  const invalid = () => new ActiveSetError('invalid_application_request');
  if (!isObject(body) || typeof body.operation !== 'string' || !Object.hasOwn(KEYS, body.operation)) throw invalid();
  const operation = body.operation as ActiveSetRequest['operation'];
  const { required, optional } = KEYS[operation];
  if (required.some((key) => !Object.hasOwn(body, key)) || Object.keys(body).some((key) => !required.includes(key) && !optional.includes(key))) throw invalid();
  if (!revisionValue(body.expectedRevision)) throw invalid();
  const expectedRevision = body.expectedRevision as number;
  if (operation === 'apply' || operation === 'replace') {
    if (typeof body.evaluationId !== 'string' || !UUID.test(body.evaluationId)) throw invalid();
    const acceptedRecommendation = parseAccepted(body.acceptedRecommendation);
    if (operation === 'apply') return { operation, evaluationId: body.evaluationId.toLowerCase(), expectedRevision, acceptedRecommendation };
    if (typeof body.expectedActiveVersionId !== 'string' || !UUID.test(body.expectedActiveVersionId)) throw invalid();
    return { operation, evaluationId: body.evaluationId.toLowerCase(), expectedRevision, expectedActiveVersionId: body.expectedActiveVersionId.toLowerCase(), acceptedRecommendation };
  }
  if (operation === 'retire') {
    if (typeof body.confirmRetire !== 'boolean') throw invalid();
    if (body.note !== undefined && body.note !== null && (typeof body.note !== 'string' || body.note.length > NOTE_MAX)) throw invalid();
    if (body.confirmRetire !== true) throw new ActiveSetError('retire_confirmation_required', 422);
    const note = typeof body.note === 'string' && body.note.trim() ? body.note.trim() : null;
    return { operation, expectedRevision, note };
  }
  if (!revisionValue(body.targetRevision)) throw invalid();
  if (body.confirmRetire !== undefined && typeof body.confirmRetire !== 'boolean') throw invalid();
  return { operation, expectedRevision, targetRevision: body.targetRevision as number, confirmRetire: body.confirmRetire === true };
}

// ---------- gate ----------

type GatedVersion = { configVersionId: string; modelKey: string; displayName: string | null; evaluationId: string };

/**
 * 기술 무결성 gate(model-version.md 활성 집합 절, 기존 configRoutes 검사를 모델 1개 기준으로): 모델 1개·카탈로그 실행 가능·prepared 아님·
 * 산출물 행 일치·적격 평가. 산출물 파일 hash는 API가 볼 수 없어 Detector가 시도마다 읽을 때 검증한다(실패 시 판정 없이 시도 실패).
 * evaluationId가 null이면(복원) 그 버전의 최근 적격 평가를 쓴다.
 */
async function gateVersion(db: Queryable, configVersionId: string, evaluationId: string | null): Promise<GatedVersion> {
  const { rows: [config] } = await db.query<{ spec: Record<string, any> }>('SELECT configuration_spec AS spec FROM ml_config_versions WHERE id=$1', [configVersionId]);
  if (!config) throw new ActiveSetError('config_not_found', 404);
  const spec = config.spec ?? {};
  // v3 활성화는 Detector v3 로더 병합 뒤(model-version.md schemaVersion 3 판독 규칙 절 허용 시점).
  if (spec.schemaVersion === 3) throw new ActiveSetError('config_schema_not_supported_yet', 422);
  const { rows: models } = await db.query<{ modelKey: string }>('SELECT model_key AS "modelKey" FROM ml_config_models WHERE config_version_id=$1', [configVersionId]);
  if (models.length !== 1) throw new ActiveSetError('model_artifacts_not_ready', 409);
  const modelKey = models[0].modelKey;
  if (mlMethod(modelKey)?.executable.process !== true) throw new ActiveSetError(`method_not_executable:ml:${modelKey}`, 409);
  if (spec.operationalEligibility?.eligible === false || spec.preparedDatasetId ||
      (await db.query(`SELECT 1 FROM ml_config_models cm JOIN ml_artifacts a ON a.id=cm.model_artifact_id
        JOIN data_prepared_datasets d ON d.snapshot_id=a.snapshot_id WHERE cm.config_version_id=$1 LIMIT 1`, [configVersionId])).rowCount) {
    throw new ActiveSetError('prepared_detector_reproduction_unverified', 409);
  }
  const artifacts = await db.query(
    `SELECT 1 FROM ml_config_models AS cm
       JOIN ml_artifacts AS m ON m.id = cm.model_artifact_id AND m.kind = 'model'
       JOIN ml_artifacts AS f ON f.id = cm.fit_artifact_id AND f.kind = 'fit'
      WHERE cm.config_version_id = $1 AND cm.model_key = $2
        AND cm.feature_set_id = m.feature_set_id AND cm.feature_set_id = f.feature_set_id
        AND m.fit_artifact_id = f.id AND m.snapshot_id = f.snapshot_id`, [configVersionId, modelKey]);
  if (artifacts.rowCount !== 1) throw new ActiveSetError('model_artifacts_not_ready', 409);
  const { rows: [gate] } = await db.query<{ id: string }>(
    `SELECT e.id::text FROM run_evaluations AS e
      WHERE e.config_version_id = $1 AND ($2::uuid IS NULL OR e.id = $2) AND e.applicability = 'valid'
        AND e.purpose = 'validation' AND e.scored_count > 0 AND e.unavailable_count = 0
        AND NOT EXISTS (SELECT 1 FROM data_prepared_datasets d WHERE d.snapshot_id=e.snapshot_id)
      ORDER BY e.completed_at DESC, e.id DESC LIMIT 1`, [configVersionId, evaluationId]);
  if (!gate) throw new ActiveSetError('evaluation_gate_not_met', 409);
  return { configVersionId, modelKey, displayName: typeof spec.displayName === 'string' ? spec.displayName : null, evaluationId: gate.id };
}

/** 추천 수락(config-comparison-evaluation.md 수락 경로·stale 절). 비교 결과에 그 추천이 없으면 mismatch, 같은 modelKey 운영 버전이 바뀌었으면 stale. */
async function checkAccepted(db: Queryable, accepted: AcceptedRecommendation, target: GatedVersion, current: ActiveModel[]) {
  if (accepted.targetVersionId !== target.configVersionId || accepted.modelKey !== target.modelKey) throw new ActiveSetError('accepted_recommendation_mismatch');
  const { rows: [comparison] } = await db.query<{ summary: Record<string, any>; setStatus: string | null }>(
    `SELECT c.summary,s.status AS "setStatus" FROM run_config_comparisons c LEFT JOIN data_evaluation_sets s ON s.id=c.evaluation_set_id
      WHERE c.job_id=$1 AND c.status='succeeded'`, [accepted.comparisonJobId]);
  const recommendations: Record<string, any>[] = Array.isArray(comparison?.summary?.replacementRecommendations) ? comparison!.summary.replacementRecommendations : [];
  const recommendation = recommendations.find((item) => item?.modelKey === accepted.modelKey);
  if (!recommendation || String(recommendation.targetVersionId ?? '').toLowerCase() !== accepted.targetVersionId) throw new ActiveSetError('accepted_recommendation_mismatch');
  const activeNow = current.find((model) => model.modelKey === accepted.modelKey)?.configVersionId ?? null;
  const baseline = recommendation.baselineConfigVersionId ? String(recommendation.baselineConfigVersionId).toLowerCase() : null;
  if (activeNow !== baseline || comparison!.setStatus !== 'ready') throw new ActiveSetError('accepted_recommendation_stale');
}

// ---------- 저장 ----------

type Context = { ruleSet: { revision: number; contentHash: string; contentHashVersion: number }; detectionPolicyRevision: number };
async function context(db: Queryable): Promise<Context> {
  const { rows: [row] } = await db.query(`SELECT rule_set_revision::text AS revision,rule_set_content_hash AS hash,rule_set_content_hash_version AS version,
    detection_policy_revision::text AS policy FROM ops_operational_state`);
  if (!row) throw new Error('operational_state_unavailable');
  return { ruleSet: { revision: Number(row.revision), contentHash: row.hash, contentHashVersion: row.version }, detectionPolicyRevision: Number(row.policy) };
}

type Plan = { models: ActiveModel[]; changes: Change[]; note: string | null; restoredFrom: number | null };

async function planChange(db: Queryable, configVersionId: string | null, request: ActiveSetRequest, current: ActiveModel[], revision: number): Promise<Plan> {
  const evidence = await context(db);
  const gateOf = (version: GatedVersion, accepted: AcceptedRecommendation | null) => ({
    evaluationId: version.evaluationId, acceptedRecommendation: accepted, ruleSet: evidence.ruleSet, detectionPolicyRevision: evidence.detectionPolicyRevision,
    activeModelSet: { before: revision - 1, after: revision },
  });
  const entry = (version: GatedVersion): ActiveModel => ({ configVersionId: version.configVersionId, modelKey: version.modelKey, displayName: version.displayName, activatedAtRevision: revision });

  if (request.operation === 'apply' || request.operation === 'replace') {
    const target = await gateVersion(db, configVersionId!, request.evaluationId);
    const same = current.find((model) => model.modelKey === target.modelKey);
    if (request.acceptedRecommendation) await checkAccepted(db, request.acceptedRecommendation, target, current);
    if (request.operation === 'apply') {
      if (same) throw new ActiveSetError(`active_model_key_duplicate:${target.modelKey}`, 422);
      return { models: [...current, entry(target)], changes: [{ type: 'activated', configVersionId: target.configVersionId, modelKey: target.modelKey, gate: gateOf(target, request.acceptedRecommendation) }], note: null, restoredFrom: null };
    }
    if (!same) throw new ActiveSetError('active_model_key_missing', 422);
    if (same.configVersionId !== request.expectedActiveVersionId) throw new ActiveSetError('active_version_mismatch', 409, { currentActiveVersionId: same.configVersionId });
    if (same.configVersionId === target.configVersionId) throw new ActiveSetError('active_model_set_unchanged', 409);
    return {
      models: current.map((model) => (model === same ? entry(target) : model)),
      changes: [{ type: 'replaced', configVersionId: target.configVersionId, modelKey: target.modelKey, previousConfigVersionId: same.configVersionId, gate: gateOf(target, request.acceptedRecommendation) }],
      note: null, restoredFrom: null,
    };
  }
  if (request.operation === 'retire') {
    const retired = current.find((model) => model.configVersionId === configVersionId);
    if (!retired) throw new ActiveSetError('not_active', 422);
    return {
      models: current.filter((model) => model !== retired),
      changes: [{ type: 'retired', configVersionId: retired.configVersionId, modelKey: retired.modelKey, gate: { ruleSet: evidence.ruleSet, detectionPolicyRevision: evidence.detectionPolicyRevision, activeModelSet: { before: revision - 1, after: revision } } }],
      note: request.note, restoredFrom: null,
    };
  }
  // restore: 과거 리비전의 집합을 새 리비전으로 다시 적용(룰 세트 되돌리기와 같은 방식). 그대로 남는 버전은 처음 활성화 리비전을 유지한다.
  if (request.targetRevision >= revision - 1) throw new ActiveSetError('invalid_target_revision', 422);
  const source = await setAt(db, request.targetRevision);
  if (!source) throw new ActiveSetError('active_model_set_revision_not_found', 404);
  const wanted = modelsOf(source);
  const kept = current.filter((model) => wanted.some((item) => item.configVersionId === model.configVersionId));
  const added = wanted.filter((item) => !current.some((model) => model.configVersionId === item.configVersionId));
  const removed = current.filter((model) => !wanted.some((item) => item.configVersionId === model.configVersionId));
  if (!added.length && !removed.length) throw new ActiveSetError('active_model_set_unchanged', 409);
  if (removed.length && !request.confirmRetire) throw new ActiveSetError('retire_confirmation_required', 422);
  // 스냅샷의 모든 버전에 gate를 다시 건다(model-version.md restore 행): 그대로 남는 버전도 지금 실행 가능·산출물·적격 평가가 있어야 한다.
  for (const item of kept) await gateVersion(db, item.configVersionId, null);
  const gated: GatedVersion[] = [];
  for (const item of added) gated.push(await gateVersion(db, item.configVersionId, null));
  const changes: Change[] = [
    ...gated.map((version) => {
      const previous = removed.find((model) => model.modelKey === version.modelKey);
      return { type: 'restored' as const, configVersionId: version.configVersionId, modelKey: version.modelKey, ...(previous ? { previousConfigVersionId: previous.configVersionId } : {}), gate: gateOf(version, null) };
    }),
    ...removed.filter((model) => !gated.some((version) => version.modelKey === model.modelKey))
      .map((model) => ({ type: 'retired' as const, configVersionId: model.configVersionId, modelKey: model.modelKey })),
  ];
  return { models: [...kept, ...gated.map(entry)], changes, note: null, restoredFrom: request.targetRevision };
}

export async function changeActiveModelSet(pool: Pool, configVersionId: string | null, request: ActiveSetRequest, actorId: string, actor: ActorResolver) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock($1,$2)', [...RULE_SET_LOCK]);
    const row = await latestSet(client);
    const currentRevision = Number(row.revision);
    if (request.expectedRevision !== currentRevision) {
      throw new ActiveSetError('active_set_revision_conflict', 409, { currentRevision, changedAt: row.changedAt, changedBy: await actor(row.actorId) });
    }
    const revision = currentRevision + 1;
    const current = modelsOf(row);
    const plan = await planChange(client, configVersionId, request, current, revision);
    // 같은 modelKey는 동시에 1개(Detector 합의). 복원 스냅샷도 이 규칙으로 저장됐지만 저장 직전에 한 번 더 막는다.
    const keys = plan.models.map((model) => model.modelKey);
    const duplicate = keys.find((key, index) => keys.indexOf(key) !== index);
    if (duplicate) throw new ActiveSetError(`active_model_key_duplicate:${duplicate}`, 422);
    if (!plan.models.length && await enabledRuleCount(client) === 0) throw new ActiveSetError('detection_methods_empty', 422);
    const { rows: [saved] } = await client.query<{ changedAt: string }>(`INSERT INTO ops_active_model_set_revisions
        (revision,snapshot,changes,note,restored_from_revision,changed_by_actor_id,changed_at)
      VALUES($1,$2::jsonb,$3::jsonb,$4,$5,$6,clock_timestamp()) RETURNING ${ISO('changed_at')} AS "changedAt"`,
    [revision, JSON.stringify({ models: plan.models }), JSON.stringify(plan.changes), plan.note, plan.restoredFrom, actorId]);
    await client.query('COMMIT');
    return {
      revision, previousRevision: currentRevision, models: plan.models, changes: plan.changes.map(({ gate: _gate, ...change }) => change),
      changedAt: saved.changedAt, appliesFrom: 'next_decision' as const,
    };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

// ---------- 이력 ----------

const HISTORY_LIMIT_DEFAULT = 50;
const HISTORY_LIMIT_MAX = 100;
export function parseActiveHistoryQuery(query: Record<string, unknown>) {
  if (Object.keys(query).some((key) => !['limit', 'cursor'].includes(key))) throw new ActiveSetError('unknown_active_model_filter');
  const { limit, cursor } = query;
  if (limit !== undefined && (typeof limit !== 'string' || !/^[1-9]\d{0,2}$/.test(limit) || Number(limit) > HISTORY_LIMIT_MAX)) throw new ActiveSetError('invalid_limit');
  let before: number | null = null;
  if (cursor !== undefined) {
    try {
      if (typeof cursor !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(cursor)) throw new Error();
      const parsed = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8')) as { revision?: unknown };
      if (Object.keys(parsed).join() !== 'revision' || !revisionValue(parsed.revision)) throw new Error();
      before = parsed.revision as number;
    } catch { throw new ActiveSetError('invalid_cursor'); }
  }
  return { limit: limit === undefined ? HISTORY_LIMIT_DEFAULT : Number(limit), before };
}

const summaryOf = (changes: Change[]) => {
  const count = (type: Change['type']) => changes.filter((change) => change.type === type).length;
  return { activated: count('activated'), replaced: count('replaced'), retired: count('retired'), restored: count('restored') };
};
/** 이력 변경 항목의 버전 표시 이름(구성 행 값). 근거 중 평가 ID·추천 수락만 밖으로 낸다. */
async function changeNames(db: Queryable, rows: SetRow[]) {
  const ids = [...new Set(rows.flatMap((row) => [...(row.changes ?? []).flatMap((change) => [change.configVersionId, change.previousConfigVersionId]),
    ...modelsOf(row).map((model) => model.configVersionId)]).filter((id): id is string => typeof id === 'string' && UUID.test(id)))];
  if (!ids.length) return new Map<string, string | null>();
  const { rows: names } = await db.query<{ id: string; name: string | null }>(
    `SELECT id::text,configuration_spec->>'displayName' AS name FROM ml_config_versions WHERE id=ANY($1::uuid[])`, [ids]);
  return new Map(names.map((item) => [item.id, item.name]));
}
const historyItem = async (row: SetRow, actor: ActorResolver, names: Map<string, string | null>) => {
  const changes = (row.changes ?? []).map(({ gate, ...change }) => ({
    ...change, displayName: names.get(change.configVersionId) ?? null,
    ...(change.previousConfigVersionId ? { previousDisplayName: names.get(change.previousConfigVersionId) ?? null } : {}),
    ...(gate && typeof gate.evaluationId === 'string' ? { evaluationId: gate.evaluationId } : {}),
    ...(gate?.acceptedRecommendation ? { acceptedRecommendation: gate.acceptedRecommendation } : {}),
  }));
  // 복원 선택창이 리비전마다 상세를 다시 읽지 않도록 목록에도 그 리비전의 집합을 둔다(표시 이름은 구성 행 값).
  const models = modelsOf(row).map((model) => ({ ...model, displayName: names.get(model.configVersionId) ?? model.displayName }));
  return {
    revision: Number(row.revision), changedAt: row.changedAt, changedBy: await actor(row.actorId), note: row.note,
    restoredFromRevision: row.restoredFromRevision === null ? null : Number(row.restoredFromRevision), changes, summary: summaryOf(row.changes ?? []),
    snapshot: { models },
  };
};

export async function readActiveHistory(db: Queryable, filter: { limit: number; before: number | null }, actor: ActorResolver) {
  const { rows } = await db.query<SetRow>(`SELECT ${COLUMNS} FROM ops_active_model_set_revisions r
    WHERE $1::bigint IS NULL OR r.revision<$1 ORDER BY r.revision DESC LIMIT $2`, [filter.before, filter.limit + 1]);
  const page = rows.slice(0, filter.limit);
  const hasMore = rows.length > filter.limit;
  const last = page.at(-1);
  const names = await changeNames(db, page);
  return {
    items: await Promise.all(page.map((row) => historyItem(row, actor, names))),
    pagination: { limit: filter.limit, hasMore, nextCursor: hasMore && last ? Buffer.from(JSON.stringify({ revision: Number(last.revision) })).toString('base64url') : null },
  };
}

export async function readActiveHistoryItem(db: Queryable, revision: number, actor: ActorResolver) {
  const row = await setAt(db, revision);
  if (!row) throw new ActiveSetError('active_model_set_revision_not_found', 404);
  return historyItem(row, actor, await changeNames(db, [row]));
}
