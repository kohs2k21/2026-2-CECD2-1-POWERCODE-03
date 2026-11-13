import type { Pool, PoolClient } from 'pg';
import { ML_METHODS, mlMethod, modelKeyOf } from '../ruleset/methodCatalog.js';
import { realCalendarTime } from '../ruleset/thresholdStats.js';

/**
 * 모델 버전 조회 GET /api/configurations·GET /api/configurations/:id(model-version.md 조회). 저장된 구성을 바꾸지 않고 응답만 만든다.
 * 지금 저장된 행은 schemaVersion 1·2뿐이라 v1·v2 투영을 쓰고, v3 행은 spec 값을 읽는다. `executable`은 저장하지 않고 응답 시 카탈로그로 계산한다.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UTC_MICROSECONDS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;
const DEFAULT_LIMIT = 50;

export class ConfigurationQueryError extends Error {
  constructor(readonly code: string) { super(code); }
}

export type ConfigurationQuery = {
  sourceId: string | null; limit: number; after: { createdAt: string; id: string } | null;
  createdFrom: 'train' | 'compose' | null; modelKey: string | null; executable: boolean | null; active: boolean | null;
};

function parseCursor(value: unknown): ConfigurationQuery['after'] {
  try {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(value)) throw new Error();
    const after = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
    if (!after || Object.keys(after).sort().join(',') !== 'createdAt,id' || typeof after.createdAt !== 'string' || !UTC_MICROSECONDS.test(after.createdAt) || !realCalendarTime(after.createdAt) ||
        typeof after.id !== 'string' || !UUID.test(after.id)) throw new Error();
    return after;
  } catch { throw new ConfigurationQueryError('invalid_cursor'); }
}

export function parseConfigurationQuery(query: Record<string, unknown>): ConfigurationQuery {
  if (Object.keys(query).some((key) => !['sourceId', 'limit', 'cursor', 'createdFrom', 'modelKey', 'executable', 'active'].includes(key))) throw new ConfigurationQueryError('unknown_configuration_filter');
  const { sourceId, limit, cursor, createdFrom, modelKey, executable, active } = query;
  if (sourceId !== undefined && (typeof sourceId !== 'string' || !UUID.test(sourceId))) throw new ConfigurationQueryError('invalid_source_id');
  if (limit !== undefined && (typeof limit !== 'string' || !/^[1-9]\d{0,2}$/.test(limit) || Number(limit) > 100)) throw new ConfigurationQueryError('invalid_limit');
  if (createdFrom !== undefined && createdFrom !== 'train' && createdFrom !== 'compose') throw new ConfigurationQueryError('invalid_created_from');
  if (modelKey !== undefined && (typeof modelKey !== 'string' || !/^[a-z][a-z0-9_-]{1,63}$/.test(modelKey))) throw new ConfigurationQueryError('invalid_model_key');
  if (executable !== undefined && executable !== 'true' && executable !== 'false') throw new ConfigurationQueryError('invalid_executable');
  if (active !== undefined && active !== 'true' && active !== 'false') throw new ConfigurationQueryError('invalid_active');
  return {
    sourceId: sourceId === undefined ? null : (sourceId as string).toLowerCase(), limit: limit === undefined ? DEFAULT_LIMIT : Number(limit),
    after: cursor === undefined ? null : parseCursor(cursor), createdFrom: (createdFrom as ConfigurationQuery['createdFrom']) ?? null,
    modelKey: (modelKey as string | undefined) ?? null, executable: executable === undefined ? null : executable === 'true',
    active: active === undefined ? null : active === 'true',
  };
}

type Spec = Record<string, any>;
type ActiveSince = { revision: number; at: string };
/** 운영 중인 버전 → 포함된 시점. */
export type ActiveState = Map<string, ActiveSince>;

/**
 * 활성 집합(ops_active_model_set_revisions)이 정본이다(v04 판정이 시도마다 읽는 집합). 활성 집합 저장 경로(운영 설정 하위 7)가 생겨
 * v1·v2 운영의 ops_system_state 단일 구성은 운영 중으로 보지 않는다(교체·복원 화면의 기준과 같게).
 */
export async function readActiveState(db: Pick<PoolClient, 'query'>): Promise<ActiveState> {
  const { rows: [set] } = await db.query<{ revision: string; models: { configVersionId: string; activatedAtRevision: number }[] }>(
    'SELECT active_model_set_revision AS revision,active_models AS models FROM ops_operational_state');
  if (!set) throw new Error('active_model_set_missing');
  const models = set.models ?? [];
  const revisions = await db.query<{ revision: string; at: string }>(
    `SELECT revision,to_char(changed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS at FROM ops_active_model_set_revisions WHERE revision=ANY($1::bigint[])`,
    [models.map((model) => model.activatedAtRevision)]);
  const changedAt = new Map(revisions.rows.map((row) => [Number(row.revision), row.at]));
  return new Map(models.map((model) => [model.configVersionId, { revision: model.activatedAtRevision, at: changedAt.get(model.activatedAtRevision) ?? '' }]));
}

/** 카탈로그 기준 방법 상태(실행 가능 여부는 저장값이 아니라 지금 카탈로그). 카탈로그에 없는 키는 실행 불가다. */
const methodState = (modelKey: string, enabled: boolean) => {
  const method = mlMethod(modelKey);
  return {
    methodId: `ml:${modelKey}`, kind: 'ml', enabled, status: method?.status ?? 'unknown',
    executable: method?.executable ?? { process: false, window: false },
    executableReason: method ? (method.executableReason?.process ?? null) : 'method_not_in_catalog',
  };
};
const v3 = (spec: Spec) => spec.schemaVersion === 3;
const requestedMode = (spec: Spec): string | null =>
  v3(spec) ? 'ml' : spec.schemaVersion === 1 || spec.schemaVersion === 2 ? (spec.detectionPolicy?.mode ?? 'parallel') : null;
const specMethods = (spec: Spec, modelKeys: string[]) =>
  v3(spec) && Array.isArray(spec.methods)
    ? spec.methods.map((item: Spec) => methodState(String(item.methodId).slice(3), item.enabled === true))
    : modelKeys.map((key) => methodState(key, true));

type TrainInput = { from?: unknown; until?: unknown } | null;
function trainingSelection(spec: Spec, input: TrainInput) {
  const selection = spec.trainingSelection ?? {};
  if (v3(spec)) return { ...selection, excludedIntervalCount: selection.excludedIntervalCount ?? 0 };
  const known = typeof input?.from === 'string' && typeof input?.until === 'string';
  return { ...selection, trainingPeriod: known ? { from: input!.from, until: input!.until, timeBasis: 'persisted_at' } : null, excludedIntervalCount: 0 };
}
function operationalEligibility(spec: Spec) {
  if (spec.operationalEligibility) return { eligible: spec.operationalEligibility.eligible === true, reason: spec.operationalEligibility.reason ?? null };
  return spec.preparedDatasetId ? { eligible: false, reason: 'prepared_detector_reproduction_unverified' } : { eligible: true, reason: null };
}

type StoredSummary = { featureNames?: unknown; parameters?: unknown; thresholdValue?: unknown };
/**
 * 목록의 입력 변수·학습 파라미터·판정선 요약. 저장된 값만 쓰고 없으면 null이다(#111 이전 v1·v2 행은 파라미터가 없다). 모델 없는 구성은 요약 자체가 null.
 * v3 파라미터는 spec의 `methods[0].parameters`가 정본이다.
 */
function modelSummary(spec: Spec, stored: StoredSummary | null) {
  if (!stored) return null;
  const parameters = (v3(spec) ? spec.methods?.[0]?.parameters : undefined) ?? stored.parameters ?? null;
  return {
    featureCount: Array.isArray(stored.featureNames) ? stored.featureNames.length : null, parameters,
    thresholdValue: typeof stored.thresholdValue === 'string' ? stored.thresholdValue : null,
  };
}

export type VersionRow = {
  id: string; sourceId: string; spec: Spec; contentHash: string; createdAt: string | Date; modelKeys: string[]; modelSummary: StoredSummary | null;
  trainJobId: string | null; trainInput: TrainInput; latestEvaluation: Record<string, unknown> | null;
  eligibleEvaluationId: string | null; unavailableEvaluation: boolean;
};

/** 목록·상세 공통 투영(model-version.md 조회 절). */
export function projectVersion(row: VersionRow, active: ActiveState) {
  const { spec } = row;
  const methods = specMethods(spec, row.modelKeys);
  const executable = methods.length > 0 && methods.every((method: { enabled: boolean; executable: { process: boolean } }) => !method.enabled || method.executable.process);
  const operational = operationalEligibility(spec);
  const applicationEligibility =
    !operational.eligible ? { eligible: false, evaluationId: null, reason: 'operational_ineligible' }
      : !executable ? { eligible: false, evaluationId: null, reason: 'not_executable' }
        : row.eligibleEvaluationId ? { eligible: true, evaluationId: row.eligibleEvaluationId, reason: null }
          : { eligible: false, evaluationId: null, reason: row.unavailableEvaluation ? 'method_results_unavailable' : 'no_eligible_evaluation' };
  const since = active.get(row.id) ?? null;
  return {
    id: row.id, sourceId: row.sourceId, schemaVersion: spec.schemaVersion ?? null, displayName: spec.displayName ?? null, contentHash: row.contentHash,
    contentHashVersion: spec.contentHashVersion ?? 1, createdAt: row.createdAt, requestedMode: requestedMode(spec), modelKey: methods[0]?.methodId.slice(3) ?? null,
    active: since !== null, activeSince: since,
    createdFrom: spec.createdFrom ?? { kind: 'train', trainJobId: row.trainJobId, recommendationJobId: null },
    executable, methods, trainingSelection: trainingSelection(spec, row.trainInput), operationalEligibility: operational, applicationEligibility,
    modelSummary: modelSummary(spec, row.modelSummary), latestEvaluation: row.latestEvaluation, latestComparison: null,
    ...(v3(spec) ? {} : { legacyRules: { statusRule: spec.statusRulePolicy !== undefined, ruleVersionIds: spec.ruleVersionIds ?? [] } }),
  };
}

const VERSION_COLUMNS = `c.id,c.source_id AS "sourceId",c.configuration_spec AS spec,c.content_hash AS "contentHash",c.created_at AS "createdAt",
  to_char(c.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "cursorCreatedAt",COALESCE(m.keys,'{}') AS "modelKeys",m.summary AS "modelSummary",
  j.id AS "trainJobId",j.input_spec AS "trainInput",e.summary AS "latestEvaluation",g.id AS "eligibleEvaluationId",COALESCE(u.found,false) AS "unavailableEvaluation"`;
const VERSION_JOINS = `LEFT JOIN LATERAL (SELECT array_agg(model_key ORDER BY model_key) AS keys,(array_agg(jsonb_build_object('featureNames',training_settings->'featureNames',
      'parameters',training_settings->'parameters','thresholdValue',threshold_spec->'value') ORDER BY model_key))[1] AS summary
    FROM ml_config_models WHERE config_version_id=c.id) m ON true
  LEFT JOIN LATERAL (SELECT id,input_spec FROM run_jobs WHERE result_config_version_id=c.id AND kind='train' AND status='succeeded' ORDER BY finished_at DESC,id DESC LIMIT 1) j ON true
  LEFT JOIN LATERAL (SELECT jsonb_build_object('evaluationId',id,'snapshotId',snapshot_id,'purpose',purpose,'metricVersion',evaluation_spec_version,
      'applicability',applicability,'completedAt',completed_at) AS summary
    FROM run_evaluations WHERE config_version_id=c.id ORDER BY completed_at DESC,id DESC LIMIT 1) e ON true
  LEFT JOIN LATERAL (SELECT x.id FROM run_evaluations x WHERE x.config_version_id=c.id AND x.purpose='validation' AND x.applicability='valid'
      AND x.scored_count>0 AND x.unavailable_count=0 AND NOT EXISTS(SELECT 1 FROM data_prepared_datasets d WHERE d.snapshot_id=x.snapshot_id)
    ORDER BY x.completed_at DESC,x.id DESC LIMIT 1) g ON true
  LEFT JOIN LATERAL (SELECT true AS found FROM run_evaluations x WHERE x.config_version_id=c.id AND x.purpose='validation' AND x.applicability='valid' AND x.unavailable_count>0 LIMIT 1) u ON true`;

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

export function readConfigurations(pool: Pick<Pool, 'connect'>, query: ConfigurationQuery) {
  const runnable = ML_METHODS.filter((method) => method.executable.process).map(modelKeyOf);
  return readOnly(pool, async (client) => {
    const active = await readActiveState(client);
    // 실행 가능 = 모델이 있고 전부 지금 카탈로그에서 실행 가능. v3 행의 모델도 ml_config_models 1행이라 같은 식이다.
    const { rows } = await client.query<VersionRow & { cursorCreatedAt: string }>(
      `SELECT ${VERSION_COLUMNS} FROM ml_config_versions c ${VERSION_JOINS}
       WHERE ($1::uuid IS NULL OR c.source_id=$1)
         AND ($2::timestamptz IS NULL OR (c.created_at,c.id)<($2::timestamptz,$3::uuid))
         AND ($4::text IS NULL OR COALESCE(c.configuration_spec->'createdFrom'->>'kind','train')=$4)
         AND ($5::text IS NULL OR $5=ANY(COALESCE(m.keys,'{}')))
         AND ($6::boolean IS NULL OR $6=(cardinality(COALESCE(m.keys,'{}'))>0 AND COALESCE(m.keys,'{}')<@$7::text[]))
         AND ($8::boolean IS NULL OR $8=(c.id=ANY($9::uuid[])))
       ORDER BY c.created_at DESC,c.id DESC LIMIT $10`,
      [query.sourceId, query.after?.createdAt ?? null, query.after?.id ?? null, query.createdFrom, query.modelKey, query.executable, runnable, query.active, [...active.keys()], query.limit + 1]);
    const page = rows.slice(0, query.limit);
    const hasMore = rows.length > query.limit, last = page.at(-1);
    return {
      items: page.map((row) => projectVersion(row, active)),
      pagination: {
        limit: query.limit, hasMore,
        nextCursor: hasMore && last ? Buffer.from(JSON.stringify({ createdAt: last.cursorCreatedAt, id: last.id })).toString('base64url') : null,
      },
    };
  });
}

type ModelRow = {
  modelKey: string; featureSetId: string; modelArtifactId: string | null; fitArtifactId: string | null;
  adapterSpec: unknown; trainingSettings: Spec; thresholdSpec: unknown;
};

/** 상세의 `methods[]`: v3는 spec 그대로, v1·v2는 `training_settings`(#111 이전 행은 파라미터가 없어 null). */
function detailMethods(row: VersionRow, models: ModelRow[], base: ReturnType<typeof projectVersion>['methods']) {
  return base.map((method: { methodId: string }, index: number) => {
    const stored = v3(row.spec) ? row.spec.methods?.[index] : undefined;
    if (stored) return { ...stored, ...method };
    const model = models.find((item) => `ml:${item.modelKey}` === method.methodId);
    return {
      ...method, methodVersion: model?.trainingSettings?.methodVersion ?? null, featureSetId: model?.featureSetId ?? null, parameters: model?.trainingSettings?.parameters ?? null,
      artifacts: { candidateConfigVersionId: row.id, trainJobId: row.trainJobId, modelArtifactId: model?.modelArtifactId ?? null, fitArtifactId: model?.fitArtifactId ?? null },
    };
  });
}

/** 없으면 null. 기존 키(`configurationSpec` 등)는 유지하고 더한다. */
export function readConfiguration(pool: Pick<Pool, 'connect'>, id: string) {
  return readOnly(pool, async (client) => {
    const { rows: [row] } = await client.query<VersionRow>(`SELECT ${VERSION_COLUMNS} FROM ml_config_versions c ${VERSION_JOINS} WHERE c.id=$1`, [id]);
    if (!row) return null;
    const active = await readActiveState(client);
    const { rows: models } = await client.query<ModelRow>(
      `SELECT model_key AS "modelKey",feature_set_id AS "featureSetId",model_artifact_id AS "modelArtifactId",fit_artifact_id AS "fitArtifactId",
         adapter_spec AS "adapterSpec",training_settings AS "trainingSettings",threshold_spec AS "thresholdSpec"
       FROM ml_config_models WHERE config_version_id=$1 ORDER BY model_key`, [id]);
    const version = projectVersion(row, active);
    const rules = v3(row.spec) ? {} : {
      rules: (await client.query(`SELECT rule_version_id AS "ruleVersionId",evaluation_order AS "evaluationOrder" FROM ml_config_rules WHERE config_version_id=$1 ORDER BY evaluation_order`, [id])).rows,
    };
    return { ...version, configurationSpec: row.spec, methods: detailMethods(row, models, version.methods), models, ...rules };
  });
}
