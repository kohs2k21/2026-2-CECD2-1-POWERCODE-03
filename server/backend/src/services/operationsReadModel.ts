import type { Pool } from 'pg';
import { OPERATIONAL_SQL, dailyReport } from './operationalReport.js';

/**
 * 운영 현황(/operations) 읽기 전용 집계(operations-ui.md).
 * - 기존 테이블만 조회한다(마이그레이션·스키마 변경 없음). 원본 값·BODY·인증값·오류 원문은 반환하지 않는다.
 * - 저장되지 않은 값은 null로 반환한다(고정값·추정값 금지). 0은 실제로 센 모집단이 0일 때만 반환한다.
 */
type Queryable = Pick<Pool, 'query'>;

export const OPERATIONS_MAX_AGE_MS = 60_000;
export const OPERATIONS_WINDOW = '1h';
export const TREND_TIMEZONE = 'Asia/Seoul';
const LAYERS = ['T', 'P', 'M', 'B'] as const;
export type Layer = typeof LAYERS[number];
export const isLayer = (value: unknown): value is Layer => typeof value === 'string' && (LAYERS as readonly string[]).includes(value);

const num = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
};
const iso = (value: unknown): string | null => (value instanceof Date ? value.toISOString() : typeof value === 'string' ? new Date(value).toISOString() : null);
const assign = <T extends object>(target: T, key: string, value: unknown) => {
  if (value !== null && value !== undefined) (target as Record<string, unknown>)[key] = value;
};

/** GET /api/operations/overview: 전체 원천 기준 단계별 최근 처리·큐 대기와 현재 운영 구성 요약. */
export async function readOverview(db: Queryable) {
  const { rows: [row] } = await db.query(`SELECT clock_timestamp() AS "observedAt",
    (SELECT max(x.last) FROM ops_sources s CROSS JOIN LATERAL (
       SELECT max(o.persisted_at) AS last FROM data_observations o WHERE o.source_id=s.id) x) AS "collectorLast",
    (SELECT sum(x.n) FROM ops_sources s CROSS JOIN LATERAL (
       SELECT count(*) AS n FROM data_observations o WHERE o.source_id=s.id
          AND o.persisted_at>=clock_timestamp()-interval '60 seconds') x)::float8 AS "lastMinuteCount",
    (SELECT count(*) FROM powercode_queue.detection_logs WHERE status='pending')::integer AS "queuePending",
    (SELECT extract(epoch FROM clock_timestamp()-min(created_at))*1000 FROM powercode_queue.detection_logs
      WHERE status='pending') AS "queueOldestMs",
    (SELECT count(*) FROM powercode_queue.detection_logs WHERE status='leased')::integer AS "detectorLeased",
    (SELECT max(measured_at) FROM ops_pipeline_measurements WHERE stage='detector') AS "detectorLast",
    (SELECT percentile_cont(.95) WITHIN GROUP (ORDER BY w.wait_ms) FROM (
       SELECT extract(epoch FROM min(a.started_at)-j.created_at)*1000 AS wait_ms FROM run_jobs j
         JOIN run_job_attempts a ON a.job_id=j.id
        WHERE j.kind='detect' AND j.created_at>=clock_timestamp()-interval '1 hour' GROUP BY j.id,j.created_at) w) AS "queueWaitP95Ms",
    (SELECT max(decided_at) FROM run_process_decisions) AS "storeLast",
    (SELECT percentile_cont(.5) WITHIN GROUP (ORDER BY source_latency_ms::double precision) FROM run_process_decisions
      WHERE latency_status='measured' AND decided_at>=clock_timestamp()-interval '1 hour') AS "endToEndP50Ms"`);
  const { rows: [active] } = await db.query(`SELECT s.active_config_version_id AS "configVersionId",s.revision::float8 AS revision,
      s.changed_at AS "changedAt",c.source_id AS "sourceId",c.configuration_spec->'detectionPolicy'->>'mode' AS "detectionMode",
      (SELECT count(*) FROM ml_config_rules r WHERE r.config_version_id=c.id)::integer AS "ruleCount",
      (SELECT coalesce(jsonb_object_agg(sev,n),'{}'::jsonb) FROM (SELECT v.rule_spec->'outcome'->>'severity' AS sev,count(*)::integer AS n
         FROM ml_config_rules r JOIN ml_rule_versions v ON v.id=r.rule_version_id WHERE r.config_version_id=c.id
          AND v.rule_spec->'outcome'->>'severity' IN ('info','warning','critical') GROUP BY 1) t) AS "ruleSeverityCounts",
      (SELECT coalesce(jsonb_agg(jsonb_build_object('modelKey',m.model_key,'adapterName',m.adapter_spec->>'name') ORDER BY m.model_key),'[]'::jsonb)
         FROM ml_config_models m WHERE m.config_version_id=c.id) AS models
    FROM ops_system_state s LEFT JOIN ml_config_versions c ON c.id=s.active_config_version_id WHERE s.id=1`);
  const stages: Record<string, Record<string, unknown>> = { collector: {}, observationQueue: {}, detector: {}, judgmentStore: {} };
  assign(stages.collector, 'lastProcessedAt', iso(row.collectorLast));
  // 0건은 실제로 센 값이다(대기 큐 전체 조회). 가장 오래된 대기는 대기 건이 있을 때만 있다.
  stages.observationQueue.pendingCount = row.queuePending;
  assign(stages.observationQueue, 'oldestWaitMs', num(row.queueOldestMs));
  stages.detector.pendingCount = row.detectorLeased;
  assign(stages.detector, 'lastProcessedAt', iso(row.detectorLast));
  assign(stages.judgmentStore, 'lastProcessedAt', iso(row.storeLast));
  const overview: Record<string, unknown> = {
    observedAt: iso(row.observedAt), maxAgeMs: OPERATIONS_MAX_AGE_MS, stages,
    collectorEps: (num(row.lastMinuteCount) ?? 0) / 60,
    activeConfiguration: active?.configVersionId ? {
      configVersionId: active.configVersionId, revision: num(active.revision), changedAt: iso(active.changedAt),
      sourceId: active.sourceId, detectionMode: active.detectionMode ?? null, ruleCount: active.ruleCount,
      ruleSeverityCounts: active.ruleSeverityCounts, models: active.models,
    } : null,
  };
  assign(overview, 'activeConfigurationVersion', active?.configVersionId ?? null);
  assign(overview, 'endToEndLatencyMs', num(row.endToEndP50Ms));
  // 전체 원천 판정 큐 대기(detect 작업 생성 → 첫 시도) 최근 1시간 p95. 표본 없으면 키 생략.
  assign(overview, 'queueWaitP95Ms', num(row.queueWaitP95Ms));
  return overview;
}

/** GET /api/operations/collection: 원천별 입력 모드(읽기)·계층별 저장 건수·최근 24시간 T 건수·입력 실행 이력. */
export async function readCollection(db: Queryable, sourceId: string) {
  const { rows: [source] } = await db.query(`SELECT s.id,s.dataset_key AS "datasetKey",s.display_name AS "displayName",
      s.ingestion_method AS "ingestionMethod",c.mode,c.revision::float8 AS revision,c.updated_at AS "updatedAt",
      c.worker_heartbeat_at AS "workerHeartbeatAt",r.status,r.settings,r.next_position::float8 AS position,
      p.replay_key AS "replayKey",p.record_count::float8 AS "recordCount"
    FROM ops_sources s LEFT JOIN ops_input_controls c ON c.source_id=s.id
    LEFT JOIN ops_input_runs r ON r.id=c.run_id LEFT JOIN data_input_replays p ON p.id=r.replay_id WHERE s.id=$1`, [sourceId]);
  if (!source) return null;
  const layers = await db.query(`SELECT layer::text AS layer,count(*)::float8 AS "storedCount",
      count(*) FILTER (WHERE persisted_at>=clock_timestamp()-interval '1 minute')::float8 AS "lastMinuteCount",
      max(persisted_at) AS "lastPersistedAt",count(*) FILTER (WHERE revision_no>1)::float8 AS "revisionArrivalCount"
    FROM data_observations WHERE source_id=$1 GROUP BY layer`, [sourceId]);
  const hourly = await db.query(`WITH hours AS (SELECT generate_series(date_trunc('hour',clock_timestamp())-interval '23 hours',
      date_trunc('hour',clock_timestamp()),interval '1 hour') AS hour_start)
    SELECT h.hour_start AS "hourStart",count(o.id)::integer AS count FROM hours h
    LEFT JOIN data_observations o ON o.source_id=$1 AND o.layer='T' AND o.persisted_at>=h.hour_start
      AND o.persisted_at<h.hour_start+interval '1 hour'
    GROUP BY h.hour_start ORDER BY h.hour_start`, [sourceId]);
  const history = await db.query(`SELECT r.id,r.created_at AS "createdAt",r.mode,p.replay_key AS "replayKey",r.status,
      r.last_success_at AS "lastSuccessAt",r.accepted_count::float8 AS "acceptedCount"
    FROM ops_input_runs r LEFT JOIN data_input_replays p ON p.id=r.replay_id
    WHERE r.source_id=$1 ORDER BY r.created_at DESC,r.id DESC LIMIT 10`, [sourceId]);
  const settings = source.settings && typeof source.settings === 'object' ? source.settings : {};
  const repeat = Number.isInteger(settings.repeat) && settings.repeat > 0 ? settings.repeat : null;
  const input = source.mode ? {
    mode: source.mode, revision: num(source.revision), status: source.status ?? null,
    updatedAt: iso(source.updatedAt), workerHeartbeatAt: iso(source.workerHeartbeatAt),
    csv: source.mode === 'csv' ? {
      replayKey: source.replayKey ?? null, recordCount: num(source.recordCount),
      rate: num(settings.rate), repeat, position: num(source.position),
      total: num(source.recordCount) !== null && repeat !== null ? Number(source.recordCount) * repeat : null,
    } : null,
    api: source.mode === 'api' ? { pollSeconds: num(settings.pollSeconds) } : null,
  } : null;
  return {
    observedAt: new Date().toISOString(),
    source: { id: source.id, datasetKey: source.datasetKey, displayName: source.displayName, ingestionMethod: source.ingestionMethod },
    input,
    layers: LAYERS.map((layer) => {
      const found = layers.rows.find((item) => item.layer === layer);
      return { layer, storedCount: found ? found.storedCount : 0, lastMinuteCount: found ? found.lastMinuteCount : 0,
        lastPersistedAt: found ? iso(found.lastPersistedAt) : null, revisionArrivalCount: found ? found.revisionArrivalCount : 0 };
    }),
    hourly: { layer: 'T', basis: 'persisted_at', items: hourly.rows.map((item) => ({ hourStart: iso(item.hourStart), count: item.count })) },
    history: history.rows.map((item) => ({ id: item.id, createdAt: iso(item.createdAt), mode: item.mode, replayKey: item.replayKey ?? null,
      status: item.status, lastSuccessAt: iso(item.lastSuccessAt), acceptedCount: num(item.acceptedCount) })),
  };
}

const quantiles = (column: string) =>
  `count(${column})::integer AS samples,percentile_cont(.5) WITHIN GROUP (ORDER BY ${column}) AS p50,
   percentile_cont(.95) WITHIN GROUP (ORDER BY ${column}) AS p95`;
const stat = (row: Record<string, unknown> | undefined, failures: unknown = null, extra: Record<string, unknown> = {}) => ({
  samples: num(row?.samples) ?? 0,
  p50Ms: row && Number(row.samples) > 0 ? num(row.p50) : null,
  p95Ms: row && Number(row.samples) > 0 ? num(row.p95) : null,
  recentFailures: num(failures),
  ...extra,
});

/** GET /api/operations/latency: 원천별 최근 1시간 단계 지연(p50/p95)·대기·최근 실패. 계측되지 않은 단계는 null. */
export async function readLatency(db: Queryable, sourceId: string) {
  const exists = await db.query('SELECT 1 FROM ops_sources WHERE id=$1', [sourceId]);
  if (!exists.rowCount) return null;
  const { rows: [poll] } = await db.query(`SELECT ${quantiles('duration_ms')},
      count(*) FILTER (WHERE error_code IS NOT NULL)::integer AS failures,
      count(db_commit_ms)::integer AS commit_samples,
      percentile_cont(.5) WITHIN GROUP (ORDER BY db_commit_ms) AS commit_p50,
      percentile_cont(.95) WITHIN GROUP (ORDER BY db_commit_ms) AS commit_p95
    FROM ops_api_poll_samples WHERE source_id=$1 AND started_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  const { rows: [wait] } = await db.query(`SELECT ${quantiles('wait_ms')} FROM (
      SELECT extract(epoch FROM min(a.started_at)-j.created_at)*1000 AS wait_ms
        FROM run_jobs j JOIN ml_config_versions c ON c.id=j.input_config_version_id
        JOIN run_job_attempts a ON a.job_id=j.id
       WHERE j.kind='detect' AND c.source_id=$1 AND j.created_at>=clock_timestamp()-interval '1 hour'
       GROUP BY j.id,j.created_at) w`, [sourceId]);
  const { rows: [queue] } = await db.query(`SELECT count(*)::integer AS pending,
      extract(epoch FROM clock_timestamp()-min(created_at))*1000 AS oldest_ms
    FROM powercode_queue.detection_logs q WHERE status='pending' AND (q.payload->>'sourceId'=$1::text OR
      -- 이전 CSV 재생 메시지는 sourceId가 없으므로 관측 참조로 원천을 확인한다(pg_queue.py claim 범위와 같은 규칙).
      (NOT (q.payload ? 'sourceId') AND EXISTS (SELECT 1 FROM data_observations o
        WHERE o.id::text=q.payload->>'observationId' AND o.source_id=$1::uuid)))`, [sourceId]);
  const { rows: [detector] } = await db.query(`SELECT ${quantiles('m.duration_ms::double precision')}
    FROM ops_pipeline_measurements m JOIN data_observations o ON o.id=m.observation_id
    WHERE m.stage='detector' AND o.source_id=$1 AND m.measured_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  const { rows: [failed] } = await db.query(`SELECT count(*)::integer AS failures FROM run_jobs j
    JOIN ml_config_versions c ON c.id=j.input_config_version_id
    WHERE j.kind='detect' AND j.status='failed' AND c.source_id=$1 AND j.finished_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  const commit = { samples: poll.commit_samples, p50: poll.commit_p50, p95: poll.commit_p95 };
  return {
    observedAt: new Date().toISOString(), window: OPERATIONS_WINDOW, sourceId,
    stages: [
      // 고객 API 조회 표본(ops_api_poll_samples)만 계측된다. CSV 재생은 표본이 없어 null.
      { id: 'collector', ...stat(poll, poll.samples > 0 ? poll.failures : null), queuePending: null },
      { id: 'persist', ...stat(commit), queuePending: null },
      { id: 'detectionQueue', ...stat(wait), queuePending: queue.pending, oldestWaitMs: num(queue.oldest_ms) },
      { id: 'detector', ...stat(detector, failed.failures), queuePending: null },
      // 판정 저장 소요는 저장하지 않는다(decision_committed_at 미기록).
      { id: 'judgmentStore', samples: 0, p50Ms: null, p95Ms: null, recentFailures: null, queuePending: null },
    ],
    // 원천 로그 시각이 검증되지 않아 측정하지 않는다(latency_status='unavailable').
    endToEnd: { p50Ms: null, p95Ms: null, reason: 'source_clock_unverified' },
  };
}

/** GET /api/operations/source-fields: 최근 관측 표본(최대 1000건)의 필드 제공·결측·품질 표시. 값 자체는 반환하지 않는다. */
export const SOURCE_FIELD_SAMPLE = 1000;
export async function readSourceFields(db: Queryable, sourceId: string, layer: Layer) {
  const exists = await db.query('SELECT 1 FROM ops_sources WHERE id=$1', [sourceId]);
  if (!exists.rowCount) return null;
  const { rows: [row] } = await db.query(`WITH sample AS (
      SELECT allowed_values,missing_fields,quality_flags FROM data_observations
       WHERE source_id=$1 AND layer=$2::source_layer ORDER BY persisted_at DESC,id DESC LIMIT $3),
    entries AS (
      SELECT f.key AS name,CASE WHEN f.value='null'::jsonb THEN 'null' ELSE 'present' END AS kind FROM sample,
        jsonb_each(CASE WHEN jsonb_typeof(allowed_values)='object' THEN allowed_values ELSE '{}'::jsonb END) f
      UNION ALL SELECT m,'missing' FROM sample,
        jsonb_array_elements_text(CASE WHEN jsonb_typeof(missing_fields)='array' THEN missing_fields ELSE '[]'::jsonb END) m),
    flags AS (SELECT q AS flag,count(*)::integer AS n FROM sample,
        jsonb_array_elements_text(CASE WHEN jsonb_typeof(quality_flags)='array' THEN quality_flags ELSE '[]'::jsonb END) q GROUP BY q)
    SELECT (SELECT count(*)::integer FROM sample) AS "sampleSize",
      (SELECT coalesce(jsonb_agg(jsonb_build_object('name',name,'presentCount',present,'nullCount',nulls,'missingCount',missing) ORDER BY name),'[]'::jsonb)
         FROM (SELECT name,count(*) FILTER (WHERE kind='present')::integer AS present,count(*) FILTER (WHERE kind='null')::integer AS nulls,
                 count(*) FILTER (WHERE kind='missing')::integer AS missing FROM entries GROUP BY name) g) AS fields,
      (SELECT coalesce(jsonb_agg(jsonb_build_object('flag',flag,'count',n) ORDER BY flag),'[]'::jsonb) FROM flags) AS "qualityFlags"`,
  [sourceId, layer, SOURCE_FIELD_SAMPLE]);
  return { observedAt: new Date().toISOString(), sourceId, layer, sampleLimit: SOURCE_FIELD_SAMPLE, sampleSize: row.sampleSize,
    fields: row.fields, qualityFlags: row.qualityFlags };
}

/** GET /api/operations/storage: PostgreSQL 데이터베이스 크기(실측). 디스크는 저장된 측정이 있을 때만. */
export async function readStorage(db: Queryable) {
  const { rows: [row] } = await db.query(`SELECT pg_database_size(current_database())::float8 AS "databaseBytes",
      clock_timestamp() AS "measuredAt",
      (SELECT to_jsonb(x) FROM (SELECT storage_key AS "storageKey",capacity_bytes::float8 AS "capacityBytes",
         used_bytes::float8 AS "usedBytes",available_bytes::float8 AS "availableBytes",warning_state AS "warningState",
         measured_at AS "measuredAt" FROM ops_storage_measurements WHERE status='measured'
         ORDER BY measured_at DESC LIMIT 1) x) AS disk`);
  return { measuredAt: iso(row.measuredAt), databaseBytes: num(row.databaseBytes), disk: row.disk ?? null };
}

/**
 * GET /api/operations/decision-trend: 현재 운영 구성의 일별 판정 수·이상 신호 비율·정상 여부 미확인 비율.
 * 운영 리포트(operational-v1)의 집계 SQL·정책을 그대로 쓰고 live·replay 범위를 합산한다(원천 범위 미확인 관측 제외).
 */
export async function readDecisionTrend(db: Pick<Pool, 'connect'>, days: number) {
  const client = await db.connect();
  try {
    await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
    const { rows: [active] } = await client.query(`SELECT s.active_config_version_id AS "configVersionId",c.source_id AS "sourceId",
        to_char(((date_trunc('day',clock_timestamp() AT TIME ZONE $1)-make_interval(days=>$2::integer-1)) AT TIME ZONE $1) AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "from",
        to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS until
      FROM ops_system_state s LEFT JOIN ml_config_versions c ON c.id=s.active_config_version_id WHERE s.id=1`, [TREND_TIMEZONE, days]);
    if (!active?.configVersionId) {
      await client.query('COMMIT');
      return { configVersionId: null, timezone: TREND_TIMEZONE, items: [], reason: 'no_active_configuration' };
    }
    const byDay = new Map<string, { total: number; decidable: number; alarms: number }>();
    for (const scope of ['live', 'replay']) {
      const result = await client.query(OPERATIONAL_SQL,
        [active.sourceId, active.configVersionId, active.from, active.until, TREND_TIMEZONE, scope, 31, 0]);
      for (const item of result.rows.map(dailyReport)) {
        const day = byDay.get(item.day) ?? { total: 0, decidable: 0, alarms: 0 };
        day.total += item.processCount.denominator ?? 0;
        day.decidable += item.alarmRatio.denominator ?? 0;
        day.alarms += item.alarmRatio.numerator ?? 0;
        byDay.set(item.day, day);
      }
    }
    await client.query('COMMIT');
    return {
      configVersionId: active.configVersionId, timezone: TREND_TIMEZONE, from: active.from, until: active.until,
      items: [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, value]) => ({
        day, decisionCount: value.total,
        anomalySignalRatio: value.decidable ? value.alarms / value.decidable : null,
        unavailableRatio: value.total ? (value.total - value.decidable) / value.total : null,
      })),
    };
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}
