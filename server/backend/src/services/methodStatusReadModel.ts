import type { Pool, PoolClient } from 'pg';
import { projectHardRules, type StoredHardRule } from '../ruleset/ruleSetContract.js';

/**
 * GET /api/operations/method-status(operations-ui.md 방법별 운영 상태 절): 지금 운영 중인 방법(켜진 이상 규칙·이상 징후 규칙·활성 모델)마다
 * 기간 안 v4 판정 행의 evidence.methodResults[]를 센다(의미 정본 detection-decision.md 11.5·11.6). 상태 배지는 화면이 정한다.
 * - 성공 = succeeded(판단하지 않음 not_applicable 포함), 미판정 = not_ready + failed, 이상 탐지 = anomalous true, 기한 초과 = cause timeout
 * - 처리 시간 = elapsedMs p95(시도 기한 기준점부터 결과까지, 순수 소요 시간 아님)
 * - 모델은 methodId가 같아도 지금 활성 버전(modelVersionId)의 결과만 센다. 이전 버전 결과는 그 버전의 것이다
 * - v1·v3 판정 행은 방법별 결과가 없어 따로 센다. 판정 행 없이 실패한 detect 작업은 오류 코드별로 따로 센다
 * 기존 표만 읽는다(마이그레이션 없음). decided_at 인덱스가 없어 기간 조건은 표를 훑는다 → 10초 상한, 넘으면 503(부분 결과 없음).
 */
export const METHOD_STATUS_PERIODS = { '1h': 1, '24h': 24 } as const;
export type MethodStatusPeriod = keyof typeof METHOD_STATUS_PERIODS;
export const isMethodStatusPeriod = (value: unknown): value is MethodStatusPeriod => typeof value === 'string' && Object.hasOwn(METHOD_STATUS_PERIODS, value);
/** 적용 뒤 이 시간 동안 실행이 0이면 화면은 '적용됨 · 첫 실행 대기', 지나면 '점검 필요 · 실행 없음'(사용자 11/02 9.8, 값은 와이어프레임 예시 1시간). */
export const FIRST_RUN_WAIT_MS = 3_600_000;
const STATEMENT_TIMEOUT_MS = 10_000;
/** 켜진 시점을 찾을 때 거슬러 읽는 룰 세트 리비전 수 상한. 넘으면 읽은 가장 오래된 리비전 시각을 쓴다. */
const RULE_HISTORY_LIMIT = 1000;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

type Current = { methodId: string; category: 'hard' | 'soft' | 'ml'; modelVersionId: string | null; enabledSince: string | null };
type StatRow = {
  methodId: string; modelVersionId: string | null; runs: number; succeeded: number; notReady: number; failed: number; anomalous: number;
  timeouts: number; lastRunAt: string | null; elapsedP95Ms: number | null;
};

/** 지금 켜진 규칙 각각이 끊김 없이 켜져 있기 시작한 리비전의 저장 시각(직전 리비전에 없거나 꺼져 있으면 그 리비전부터). */
async function currentRules(db: PoolClient): Promise<{ revision: number; methods: Current[] }> {
  const { rows } = await db.query<{ revision: string; savedAt: string; snapshot: { hardRules?: StoredHardRule[]; softRules?: { id: string; enabled: boolean }[] } }>(
    `SELECT revision::text,${ISO('saved_at')} AS "savedAt",snapshot FROM ops_rule_set_revisions ORDER BY revision DESC LIMIT $1`, [RULE_HISTORY_LIMIT]);
  if (!rows.length) throw new Error('rule_set_revision_row_missing');
  const enabledIn = (snapshot: (typeof rows)[number]['snapshot'], id: string) =>
    [...(snapshot.hardRules ?? []), ...(snapshot.softRules ?? [])].some((rule) => rule.id === id && rule.enabled === true);
  const latest = rows[0].snapshot;
  const enabled: Current[] = [
    ...projectHardRules(latest.hardRules ?? []).filter((rule) => rule.enabled).map((rule) => ({ methodId: rule.id, category: 'hard' as const })),
    ...(latest.softRules ?? []).filter((rule) => rule.enabled === true).map((rule) => ({ methodId: rule.id, category: 'soft' as const })),
  ].map((method) => {
    let since: string | null = null;
    for (const row of rows) {
      if (!enabledIn(row.snapshot, method.methodId)) break;
      since = row.savedAt;
    }
    return { ...method, modelVersionId: null, enabledSince: since };
  });
  return { revision: Number(rows[0].revision), methods: enabled };
}

async function currentModels(db: PoolClient): Promise<{ revision: number; methods: Current[] }> {
  const { rows: [set] } = await db.query<{ revision: string; models: { configVersionId: string; modelKey: string; activatedAtRevision: number }[] | null }>(
    'SELECT active_model_set_revision::text AS revision,active_models AS models FROM ops_operational_state');
  if (!set) throw new Error('operational_state_unavailable');
  const models = set.models ?? [];
  const { rows } = await db.query<{ revision: string; at: string }>(
    `SELECT revision::text,${ISO('changed_at')} AS at FROM ops_active_model_set_revisions WHERE revision=ANY($1::bigint[])`, [models.map((model) => model.activatedAtRevision)]);
  const at = new Map(rows.map((row) => [Number(row.revision), row.at]));
  return {
    revision: Number(set.revision),
    methods: models.map((model) => ({ methodId: `ml:${model.modelKey}`, category: 'ml' as const, modelVersionId: model.configVersionId, enabledSince: at.get(Number(model.activatedAtRevision)) ?? null })),
  };
}

export async function readMethodStatus(pool: Pick<Pool, 'connect'>, period: MethodStatusPeriod) {
  const client = await pool.connect() as PoolClient;
  try {
    await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
    await client.query(`SET LOCAL statement_timeout=${STATEMENT_TIMEOUT_MS}`);
    const { rows: [window] } = await client.query<{ from: string; until: string; fromAt: Date; untilAt: Date }>(
      `SELECT ${ISO('(t.now-make_interval(hours=>$1::integer))')} AS "from",${ISO('t.now')} AS until,
         t.now-make_interval(hours=>$1::integer) AS "fromAt",t.now AS "untilAt" FROM (SELECT clock_timestamp() AS now) t`, [METHOD_STATUS_PERIODS[period]]);
    const rules = await currentRules(client);
    const models = await currentModels(client);
    const range = [window.fromAt, window.untilAt];
    const { rows: [counts] } = await client.query<{ v4: number; legacy: number }>(
      `SELECT count(*) FILTER (WHERE evidence->>'schemaVersion'='4')::int AS v4,count(*) FILTER (WHERE evidence->>'schemaVersion' IS DISTINCT FROM '4')::int AS legacy
         FROM run_process_decisions WHERE decided_at>=$1 AND decided_at<$2`, range);
    const { rows: stats } = await client.query<StatRow>(
      `SELECT m->>'methodId' AS "methodId",CASE WHEN m->>'category'='ml' THEN lower(m->>'modelVersionId') END AS "modelVersionId",
         count(*)::int AS runs,count(*) FILTER (WHERE m->>'status'='succeeded')::int AS succeeded,
         count(*) FILTER (WHERE m->>'status'='not_ready')::int AS "notReady",count(*) FILTER (WHERE m->>'status'='failed')::int AS failed,
         count(*) FILTER (WHERE m->'anomalous'='true'::jsonb)::int AS anomalous,count(*) FILTER (WHERE m->>'cause'='timeout')::int AS timeouts,
         ${ISO('max(d.decided_at)')} AS "lastRunAt",
         percentile_cont(0.95) WITHIN GROUP (ORDER BY (m->>'elapsedMs')::float8) FILTER (WHERE jsonb_typeof(m->'elapsedMs')='number') AS "elapsedP95Ms"
       FROM run_process_decisions d CROSS JOIN LATERAL jsonb_array_elements(d.evidence->'methodResults') m
       WHERE d.decided_at>=$1 AND d.decided_at<$2 AND d.evidence->>'schemaVersion'='4' AND jsonb_typeof(d.evidence->'methodResults')='array'
       GROUP BY 1,2`, range);
    const { rows: causes } = await client.query<{ methodId: string; modelVersionId: string | null; cause: string; count: number }>(
      `SELECT m->>'methodId' AS "methodId",CASE WHEN m->>'category'='ml' THEN lower(m->>'modelVersionId') END AS "modelVersionId",
         COALESCE(m->>'cause','unknown') AS cause,count(*)::int AS count
       FROM run_process_decisions d CROSS JOIN LATERAL jsonb_array_elements(d.evidence->'methodResults') m
       WHERE d.decided_at>=$1 AND d.decided_at<$2 AND d.evidence->>'schemaVersion'='4' AND jsonb_typeof(d.evidence->'methodResults')='array'
         AND m->>'status' IN ('not_ready','failed')
       GROUP BY 1,2,3 ORDER BY 4 DESC,3`, range);
    const { rows: failedJobs } = await client.query<{ code: string; count: number }>(
      `SELECT COALESCE(NULLIF(j.error_code,''),'job_failed') AS code,count(*)::int AS count FROM run_jobs j
        WHERE j.kind='detect' AND j.status='failed' AND j.finished_at>=$1 AND j.finished_at<$2
          AND NOT EXISTS (SELECT 1 FROM run_process_decisions p WHERE p.job_id=j.id)
        GROUP BY 1 ORDER BY 2 DESC,1`, range);
    await client.query('COMMIT');
    const key = (methodId: string, version: string | null) => `${methodId}|${version ?? ''}`;
    const byKey = new Map(stats.map((row) => [key(row.methodId, row.modelVersionId), row]));
    const methods = [...rules.methods, ...models.methods].map((method) => {
      const version = method.modelVersionId?.toLowerCase() ?? null;
      const row = byKey.get(key(method.methodId, version));
      return {
        ...method,
        runs: row?.runs ?? 0, succeeded: row?.succeeded ?? 0, notReady: row?.notReady ?? 0, failed: row?.failed ?? 0,
        anomalous: row?.anomalous ?? 0, timeouts: row?.timeouts ?? 0, lastRunAt: row?.lastRunAt ?? null,
        elapsedP95Ms: row?.elapsedP95Ms === null || row?.elapsedP95Ms === undefined ? null : Number(row.elapsedP95Ms),
        causes: causes.filter((item) => item.methodId === method.methodId && item.modelVersionId === version).map(({ cause, count }) => ({ cause, count })),
      };
    });
    return {
      period, from: window.from, until: window.until, firstRunWaitMs: FIRST_RUN_WAIT_MS,
      ruleSetRevision: rules.revision, activeModelSetRevision: models.revision,
      decisionCount: counts.v4, legacyDecisionCount: counts.legacy,
      failedWithoutDecision: { count: failedJobs.reduce((sum, row) => sum + row.count, 0), byCode: failedJobs },
      methods,
    };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}
