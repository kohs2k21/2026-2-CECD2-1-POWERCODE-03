import type { Pool } from 'pg';
import { DECISION_TREND_SQL } from './operationalReport.js';

/**
 * 운영 현황(/operations) 읽기 전용 집계(operations-ui.md).
 * - 기존 테이블만 조회한다(마이그레이션·스키마 변경 없음). 원본 값·BODY·인증값·오류 원문은 반환하지 않는다.
 * - 저장되지 않은 값은 null로 반환한다(고정값·추정값 금지). 0은 실제로 센 모집단이 0일 때만 반환한다.
 */
type Queryable = Pick<Pool, 'query'>;

export const OPERATIONS_MAX_AGE_MS = 60_000;
export const OPERATIONS_WINDOW = '1h';
export const TREND_TIMEZONE = 'Asia/Seoul';
/** 원천 시각에 시간대가 없고 원천 설정이 검증 전일 때 Collector가 쓰는 가정(collector-metrics.md 1.1·3.4). */
export const ASSUMED_TIMEZONE = 'Asia/Seoul';
const LAYERS = ['T', 'P', 'M', 'B'] as const;
export type Layer = typeof LAYERS[number];
export const isLayer = (value: unknown): value is Layer => typeof value === 'string' && (LAYERS as readonly string[]).includes(value);

/**
 * 0 이상 유한 수만 값으로 돌려준다(건수·크기·소요). 부호 있는 지연(원천 → 저장·판정)은 이 함수로 거르지 않는다 —
 * 음수 표본은 SQL에서 분위수 밖으로 빼 skewCount로 세고(collector-metrics.md 3.2, detection-decision.md 18절),
 * 시간대 확인용 clockCheck는 부호를 그대로 둔다(signed).
 */
const num = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
};
const signed = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
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
    -- 원천 발생 → 판정: measured 판정의 부호 있는 원값 중 0 이상만(음수 = 원천 시계가 앞섬, 분위수에서 뺀다 — detection-decision.md 18절).
    (SELECT percentile_cont(.5) WITHIN GROUP (ORDER BY source_latency_ms::double precision) FROM run_process_decisions
      WHERE latency_status='measured' AND source_latency_ms>=0
        AND decided_at>=clock_timestamp()-interval '1 hour') AS "endToEndP50Ms"`);
  const resources = await readResources(db);
  const { rows: [active] } = await db.query(`SELECT s.active_config_version_id AS "configVersionId",s.revision::float8 AS revision,
      s.changed_at AS "changedAt",c.source_id AS "sourceId",c.configuration_spec->'detectionPolicy'->>'mode' AS "detectionMode",
      (SELECT count(*) FROM ml_config_rules r WHERE r.config_version_id=c.id)::integer AS "ruleCount",
      (SELECT coalesce(jsonb_object_agg(sev,n),'{}'::jsonb) FROM (SELECT v.rule_spec->'outcome'->>'severity' AS sev,count(*)::integer AS n
         FROM ml_config_rules r JOIN ml_rule_versions v ON v.id=r.rule_version_id WHERE r.config_version_id=c.id
          AND v.rule_spec->'outcome'->>'severity' IN ('info','warning','critical') GROUP BY 1) t) AS "ruleSeverityCounts",
      (SELECT coalesce(jsonb_agg(jsonb_build_object('modelKey',m.model_key,'adapterName',m.adapter_spec->>'name') ORDER BY m.model_key),'[]'::jsonb)
         FROM ml_config_models m WHERE m.config_version_id=c.id) AS models
    FROM ops_system_state s LEFT JOIN ml_config_versions c ON c.id=s.active_config_version_id WHERE s.id=1`);
  // 구성 없이(v04) 운영되는 룰 세트·활성 모델 집합 버전(015 일관 읽기 뷰, 한 문장 = 한 스냅샷). 행이 없으면 저장소 이상이라 null.
  const { rows: [state] } = await db.query(`SELECT rule_set_revision::float8 AS "ruleSetRevision",
      active_model_set_revision::float8 AS "activeModelSetRevision",
      (SELECT coalesce(jsonb_agg(jsonb_build_object('modelKey',m->>'modelKey') ORDER BY m->>'modelKey'),'[]'::jsonb)
         FROM jsonb_array_elements(CASE WHEN jsonb_typeof(active_models)='array' THEN active_models ELSE '[]'::jsonb END) m
        WHERE jsonb_typeof(m->'modelKey')='string') AS models
    FROM ops_operational_state`);
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
    operationalState: state ? { ruleSetRevision: num(state.ruleSetRevision), activeModelSetRevision: num(state.activeModelSetRevision),
      models: state.models } : null,
  };
  assign(overview, 'activeConfigurationVersion', active?.configVersionId ?? null);
  assign(overview, 'endToEndLatencyMs', num(row.endToEndP50Ms));
  // 전체 원천 판정 큐 대기(detect 작업 생성 → 첫 시도) 최근 1시간 p95. 표본 없으면 키 생략.
  assign(overview, 'queueWaitP95Ms', num(row.queueWaitP95Ms));
  // 시스템 자원(collector-metrics.md 4.3): 신선한 최신 표본이 measured일 때만. 오래되거나 측정 불가면 키 생략.
  assign(overview, 'cpuPercent', resources.cpuPercent);
  assign(overview, 'memoryPercent', resources.memoryPercent);
  assign(overview, 'diskPercent', resources.diskPercent);
  return overview;
}

/** 자원 표본 신선도(collector-metrics.md 4.3): 주기 60초 × 3. 미래 5초 초과도 값 없음. */
const FRESH = (column: string) =>
  `${column} BETWEEN clock_timestamp()-interval '180 seconds' AND clock_timestamp()+interval '5 seconds'`;
const percentOf = (part: number | null, whole: number | null) =>
  part !== null && whole !== null && whole > 0 && part <= whole ? (part / whole) * 100 : null;

/** 개요 cpu·memory·disk(%). 디스크는 database_data 키만 본다(artifacts로 대신하지 않음, 계약 4.3 Q3). */
async function readResources(db: Queryable) {
  const { rows: [row] } = await db.query(`SELECT
      (SELECT to_jsonb(x) FROM (SELECT r.cpu_status AS "cpuStatus",r.cpu_percent AS "cpuPercent",r.memory_status AS "memoryStatus",
          r.memory_used_bytes::float8 AS "memoryUsed",r.memory_total_bytes::float8 AS "memoryTotal"
         FROM ops_resource_measurements r WHERE ${FRESH('r.measured_at')} ORDER BY r.measured_at DESC LIMIT 1) x) AS resource,
      (SELECT to_jsonb(x) FROM (SELECT s.status,s.used_bytes::float8 AS used,s.available_bytes::float8 AS available,
          ${FRESH('s.measured_at')} AS fresh
         FROM ops_storage_measurements s WHERE s.storage_key='database_data' AND s.target_kind='filesystem'
        ORDER BY s.measured_at DESC LIMIT 1) x) AS disk`);
  const resource = row.resource as Record<string, unknown> | null;
  const disk = row.disk as Record<string, unknown> | null;
  const used = num(disk?.used), available = num(disk?.available);
  return {
    cpuPercent: resource?.cpuStatus === 'measured' ? num(resource.cpuPercent) : null,
    memoryPercent: resource?.memoryStatus === 'measured' ? percentOf(num(resource.memoryUsed), num(resource.memoryTotal)) : null,
    // 최신 행이 신선하고 measured일 때만(이전 measured 행으로 거슬러 가지 않는다 — 표본기는 측정 불가 행을 남긴다).
    diskPercent: disk?.status === 'measured' && disk.fresh === true && used !== null && available !== null
      ? percentOf(used, used + available) : null,
  };
}

/** 시간별 T 건수 기준(collector-metrics.md 2절): 기본 저장 시각, `source_event`이면 원천 발생 시각(030 부분 인덱스). */
export const HOURLY_BASES = ['persisted_at', 'source_event'] as const;
export type HourlyBasis = typeof HOURLY_BASES[number];
export const isHourlyBasis = (value: unknown): value is HourlyBasis =>
  typeof value === 'string' && (HOURLY_BASES as readonly string[]).includes(value);

/** GET /api/operations/collection: 원천별 입력 모드(읽기)·계층별 저장 건수·최근 24시간 T 건수·입력 실행 이력. */
export async function readCollection(db: Queryable, sourceId: string, hourlyBasis: HourlyBasis = 'persisted_at') {
  const { rows: [source] } = await db.query(`SELECT s.id,s.dataset_key AS "datasetKey",s.display_name AS "displayName",
      s.ingestion_method AS "ingestionMethod",c.mode,c.revision::float8 AS revision,c.updated_at AS "updatedAt",
      c.worker_heartbeat_at AS "workerHeartbeatAt",r.status,r.settings,r.next_position::float8 AS position,
      r.last_success_at AS "lastSuccessAt",r.last_attempt_at AS "lastAttemptAt",
      extract(epoch FROM clock_timestamp()-r.last_success_at)*1000 AS "sinceLastSuccessMs",
      -- CSV 실제 속도: 실행이 running일 때만 최근 60초 저장 관측 수/60(한 원천에 Collector는 하나, 중복 행은 저장되지 않음).
      CASE WHEN r.status='running' THEN (SELECT count(*) FROM data_observations o WHERE o.source_id=s.id
        AND o.persisted_at>=clock_timestamp()-interval '60 seconds')::float8/60 END AS "actualRate",
      p.replay_key AS "replayKey",p.record_count::float8 AS "recordCount"
    FROM ops_sources s LEFT JOIN ops_input_controls c ON c.source_id=s.id
    LEFT JOIN ops_input_runs r ON r.id=c.run_id LEFT JOIN data_input_replays p ON p.id=r.replay_id WHERE s.id=$1`, [sourceId]);
  if (!source) return null;
  const layers = await db.query(`SELECT layer::text AS layer,count(*)::float8 AS "storedCount",
      count(*) FILTER (WHERE persisted_at>=clock_timestamp()-interval '1 minute')::float8 AS "lastMinuteCount",
      max(persisted_at) AS "lastPersistedAt",count(*) FILTER (WHERE revision_no>1)::float8 AS "revisionArrivalCount",
      count(source_log_occurred_at)::float8 AS "sourceTimeKnownCount",max(source_log_occurred_at) AS "lastSourceEventAt"
    FROM data_observations WHERE source_id=$1 GROUP BY layer`, [sourceId]);
  const hourlyColumn = hourlyBasis === 'source_event' ? 'o.source_log_occurred_at' : 'o.persisted_at';
  const hourly = await db.query(`WITH hours AS (SELECT generate_series(date_trunc('hour',clock_timestamp())-interval '23 hours',
      date_trunc('hour',clock_timestamp()),interval '1 hour') AS hour_start)
    SELECT h.hour_start AS "hourStart",count(o.id)::integer AS count FROM hours h
    LEFT JOIN data_observations o ON o.source_id=$1 AND o.layer='T' AND ${hourlyColumn}>=h.hour_start
      AND ${hourlyColumn}<h.hour_start+interval '1 hour'
    GROUP BY h.hour_start ORDER BY h.hour_start`, [sourceId]);
  // source_event: 같은 24시간 저장 창에 들었지만 원천 시각이 없는 T 관측 수(030 이전 행·해석 실패·값 없음).
  const timeUnknown = hourlyBasis === 'source_event' ? (await db.query(`SELECT count(*)::integer AS n FROM data_observations o
    WHERE o.source_id=$1 AND o.layer='T' AND o.source_log_occurred_at IS NULL
      AND o.persisted_at>=date_trunc('hour',clock_timestamp())-interval '23 hours'`, [sourceId])).rows[0].n : null;
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
      actualRate: num(source.actualRate),
    } : null,
    // 오류 원문·error_code는 싣지 않는다(lastError 제외 규칙). 성공이 없으면 경과는 null.
    api: source.mode === 'api' ? { pollSeconds: num(settings.pollSeconds), lastSuccessAt: iso(source.lastSuccessAt),
      lastAttemptAt: iso(source.lastAttemptAt), sinceLastSuccessMs: num(source.sinceLastSuccessMs) } : null,
  } : null;
  return {
    observedAt: new Date().toISOString(),
    source: { id: source.id, datasetKey: source.datasetKey, displayName: source.displayName, ingestionMethod: source.ingestionMethod },
    input,
    layers: LAYERS.map((layer) => {
      const found = layers.rows.find((item) => item.layer === layer);
      return { layer, storedCount: found ? found.storedCount : 0, lastMinuteCount: found ? found.lastMinuteCount : 0,
        lastPersistedAt: found ? iso(found.lastPersistedAt) : null, revisionArrivalCount: found ? found.revisionArrivalCount : 0,
        sourceTimeKnownCount: found ? found.sourceTimeKnownCount : 0, lastSourceEventAt: found ? iso(found.lastSourceEventAt) : null };
    }),
    hourly: hourlyBasis === 'source_event'
      ? { layer: 'T', basis: 'source_log_occurred_at', items: hourly.rows.map((item) => ({ hourStart: iso(item.hourStart), count: item.count })),
          timeUnknownCount: timeUnknown }
      : { layer: 'T', basis: 'persisted_at', items: hourly.rows.map((item) => ({ hourStart: iso(item.hourStart), count: item.count })) },
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

/**
 * GET /api/operations/latency: 원천별 최근 1시간 단계 지연(p50/p95)·대기·최근 실패. 계측되지 않은 단계는 null.
 * detect 작업의 원천은 입력 manifest로 찾는다(v04 작업은 구성이 없다). v03은 트리거가 manifest 원천 = 구성 원천을 강제하고,
 * manifest 없는 detect 작업은 run_jobs_v03_contract가 막으므로 구성으로 찾던 결과와 같다.
 * 원천 시각 지연(endToEnd·sourceToStored)은 부호 있는 원값에서 0 이상만 분위수로 쓰고 음수는 skewCount로 센다(0으로 자르지 않음).
 */
export async function readLatency(db: Queryable, sourceId: string) {
  const exists = await db.query(`SELECT clock_basis->>'status' AS "clockBasisStatus" FROM ops_sources WHERE id=$1`, [sourceId]);
  if (!exists.rowCount) return null;
  const { rows: [poll] } = await db.query(`SELECT ${quantiles('duration_ms')},
      count(*) FILTER (WHERE error_code IS NOT NULL)::integer AS failures,
      count(db_commit_ms)::integer AS commit_samples,
      percentile_cont(.5) WITHIN GROUP (ORDER BY db_commit_ms) AS commit_p50,
      percentile_cont(.95) WITHIN GROUP (ORDER BY db_commit_ms) AS commit_p95
    FROM ops_api_poll_samples WHERE source_id=$1 AND started_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  const { rows: [wait] } = await db.query(`SELECT ${quantiles('wait_ms')} FROM (
      SELECT extract(epoch FROM min(a.started_at)-j.created_at)*1000 AS wait_ms
        FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
        JOIN run_job_attempts a ON a.job_id=j.id
       WHERE j.kind='detect' AND m.source_id=$1 AND j.created_at>=clock_timestamp()-interval '1 hour'
       GROUP BY j.id,j.created_at) w`, [sourceId]);
  const { rows: [queue] } = await db.query(`SELECT count(*)::integer AS pending,
      extract(epoch FROM clock_timestamp()-min(created_at))*1000 AS oldest_ms
    FROM powercode_queue.detection_logs q WHERE status='pending' AND (q.payload->>'sourceId'=$1::text OR
      -- 이전 CSV 재생 메시지는 sourceId가 없으므로 관측 참조로 원천을 확인한다(pg_queue.py claim 범위와 같은 규칙).
      -- payload 값을 uuid로 바꿔 PK로 찾는다(pg_queue.py _payload_uuid). o.id::text 비교는 대기 행마다 관측 전체를 읽는다.
      (NOT (q.payload ? 'sourceId') AND (SELECT o.source_id FROM data_observations o
        WHERE o.id=CASE WHEN q.payload->>'observationId' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          THEN (q.payload->>'observationId')::uuid END)=$1::uuid))`, [sourceId]);
  const { rows: [detector] } = await db.query(`SELECT ${quantiles('m.duration_ms::double precision')}
    FROM ops_pipeline_measurements m JOIN data_observations o ON o.id=m.observation_id
    WHERE m.stage='detector' AND o.source_id=$1 AND m.measured_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  const { rows: [failed] } = await db.query(`SELECT count(*)::integer AS failures FROM run_jobs j
    JOIN run_input_manifests m ON m.id=j.input_manifest_id
    WHERE j.kind='detect' AND j.status='failed' AND m.source_id=$1 AND j.finished_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  // 판정 저장 소요(migration 031, detection-decision.md 19절): 첫 판정마다 1행. 실패 수는 null(저장 실패는 detector 실패에 잡힌다).
  const { rows: [store] } = await db.query(`SELECT ${quantiles('m.duration_ms::double precision')}
    FROM ops_pipeline_measurements m JOIN data_observations o ON o.id=m.observation_id
    WHERE m.stage='judgment_store' AND o.source_id=$1 AND m.measured_at>=clock_timestamp()-interval '1 hour'`, [sourceId]);
  // 원천 → 저장(collector-metrics.md 3.2)과 시간대 가정 확인(3.4)은 같은 후보 관측(P·첫 revision·1시간·API 입력)에서 센다.
  const { rows: [clock] } = await db.query(`WITH c AS (
      SELECT o.clock_status, i.ingest_phase,
             extract(epoch FROM o.persisted_at - o.source_log_occurred_at) * 1000 AS ms
        FROM data_observations o JOIN data_input_origins i ON i.observation_id = o.id
       WHERE o.source_id = $1 AND o.layer = 'P' AND o.revision_no = 1
         AND o.persisted_at >= clock_timestamp() - interval '1 hour' AND i.mode = 'api'
         AND (i.ingest_phase = 'live' OR i.ingest_phase IS NULL)),
    v AS (SELECT * FROM c WHERE ingest_phase = 'live'
            AND clock_status IN ('offset','verified_tz','assumed_tz') AND ms IS NOT NULL),
    a AS (SELECT ms FROM v WHERE clock_status = 'assumed_tz')
    SELECT (SELECT count(*) FROM v WHERE ms >= 0)::integer AS samples,
           (SELECT percentile_cont(.5)  WITHIN GROUP (ORDER BY ms) FROM v WHERE ms >= 0) AS p50,
           (SELECT percentile_cont(.95) WITHIN GROUP (ORDER BY ms) FROM v WHERE ms >= 0) AS p95,
           (SELECT count(*) FROM v WHERE ms < 0)::integer AS skew,
           (SELECT count(*) FROM c WHERE ingest_phase='live' AND clock_status='unparsed')::integer AS unparsed,
           (SELECT count(*) FROM c WHERE ingest_phase='live' AND clock_status='absent')::integer AS absent,
           (SELECT count(*) FROM c WHERE ingest_phase IS NULL)::integer AS legacy_unknown,
           (SELECT count(*) FROM a)::integer AS assumed_samples,
           (SELECT percentile_cont(.5)  WITHIN GROUP (ORDER BY ms) FROM a) AS assumed_p50,
           (SELECT percentile_cont(.95) WITHIN GROUP (ORDER BY ms) FROM a) AS assumed_p95,
           (SELECT count(*) FROM a WHERE ms < 0)::integer AS assumed_negative`, [sourceId]);
  // 원천 발생 → 판정(detection-decision.md 18절): 이 원천 관측의 measured 판정, 부호 있는 원값. 음수는 분위수 밖·skewCount.
  const { rows: [e2e] } = await db.query(`SELECT count(*) FILTER (WHERE d.source_latency_ms >= 0)::integer AS samples,
      percentile_cont(.5)  WITHIN GROUP (ORDER BY d.source_latency_ms::double precision) FILTER (WHERE d.source_latency_ms >= 0) AS p50,
      percentile_cont(.95) WITHIN GROUP (ORDER BY d.source_latency_ms::double precision) FILTER (WHERE d.source_latency_ms >= 0) AS p95,
      count(*) FILTER (WHERE d.source_latency_ms < 0)::integer AS skew
    FROM run_process_decisions d JOIN data_observations o ON o.id = d.process_observation_id
    WHERE o.source_id = $1 AND d.latency_status = 'measured' AND d.decided_at >= clock_timestamp() - interval '1 hour'`, [sourceId]);
  const commit = { samples: poll.commit_samples, p50: poll.commit_p50, p95: poll.commit_p95 };
  const sourceToStoredReason = clock.samples > 0 ? null : clock.skew > 0 ? 'source_clock_skew'
    : clock.unparsed + clock.absent > 0 ? 'source_time_unavailable' : 'no_live_api_samples';
  const endToEndReason = e2e.samples > 0 ? null : e2e.skew > 0 ? 'source_clock_skew' : 'source_time_unavailable';
  return {
    observedAt: new Date().toISOString(), window: OPERATIONS_WINDOW, sourceId,
    stages: [
      // 고객 API 조회 표본(ops_api_poll_samples)만 계측된다. CSV 재생은 표본이 없어 null.
      { id: 'collector', ...stat(poll, poll.samples > 0 ? poll.failures : null), queuePending: null },
      { id: 'persist', ...stat(commit), queuePending: null },
      { id: 'detectionQueue', ...stat(wait), queuePending: queue.pending, oldestWaitMs: num(queue.oldest_ms) },
      { id: 'detector', ...stat(detector, failed.failures), queuePending: null },
      { id: 'judgmentStore', ...stat(store), queuePending: null },
    ],
    endToEnd: { window: OPERATIONS_WINDOW, samples: e2e.samples, p50Ms: e2e.samples > 0 ? num(e2e.p50) : null,
      p95Ms: e2e.samples > 0 ? num(e2e.p95) : null, skewCount: e2e.skew, reason: endToEndReason },
    sourceToStored: { window: OPERATIONS_WINDOW, samples: clock.samples, p50Ms: clock.samples > 0 ? num(clock.p50) : null,
      p95Ms: clock.samples > 0 ? num(clock.p95) : null, skewCount: clock.skew,
      excludedCounts: { unparsed: clock.unparsed, absent: clock.absent, legacyUnknown: clock.legacy_unknown },
      reason: sourceToStoredReason },
    // 지연 지표가 아니라 Asia/Seoul 가정 확인용(3.4): assumed_tz 표본의 부호 있는 분위수(음수 포함).
    clockCheck: { window: OPERATIONS_WINDOW, assumedTimezone: ASSUMED_TIMEZONE,
      sourceClockBasisStatus: exists.rows[0].clockBasisStatus ?? null, sampleCount: clock.assumed_samples,
      p50Ms: clock.assumed_samples > 0 ? signed(clock.assumed_p50) : null,
      p95Ms: clock.assumed_samples > 0 ? signed(clock.assumed_p95) : null,
      negativeRatio: clock.assumed_samples > 0 ? clock.assumed_negative / clock.assumed_samples : null },
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

/** 저장공간 키(collector-metrics.md 4.3): 응답 disks[] 순서 고정. `disk`는 database_data만. */
export const STORAGE_KEYS = ['database_data', 'artifacts'] as const;

/**
 * GET /api/operations/storage: PostgreSQL 데이터베이스 크기(실측)와 Collector 표본기 디스크 측정(collector-metrics.md 4.3).
 * 키마다 최신 행 하나만 본다 — 오래됐거나(180초) 측정 불가면 값 없음, 이전 measured 행으로 거슬러 가지 않는다.
 */
export async function readStorage(db: Queryable) {
  const { rows: [row] } = await db.query(`SELECT pg_database_size(current_database())::float8 AS "databaseBytes",
      clock_timestamp() AS "measuredAt"`);
  const { rows } = await db.query(`SELECT DISTINCT ON (storage_key) storage_key AS "storageKey",status,filesystem_key AS "filesystemKey",
      capacity_bytes::float8 AS "capacityBytes",used_bytes::float8 AS "usedBytes",available_bytes::float8 AS "availableBytes",
      warning_state AS "warningState",reason_code AS "reasonCode",measured_at AS "measuredAt",${FRESH('measured_at')} AS fresh
    FROM ops_storage_measurements WHERE storage_key = ANY($1::text[]) AND target_kind='filesystem'
    ORDER BY storage_key,measured_at DESC`, [STORAGE_KEYS]);
  const disks = STORAGE_KEYS.map((storageKey) => {
    const found = rows.find((item) => item.storageKey === storageKey);
    // 두 사유(not_measured·measurement_stale)는 API가 붙인다. 표본기 사유(mount_not_visible 등)는 그대로.
    if (!found) return { storageKey, status: 'unavailable', filesystemKey: null, capacityBytes: null, usedBytes: null, availableBytes: null,
      warningState: 'unknown', reasonCode: 'not_measured', measuredAt: null };
    if (found.fresh !== true) return { storageKey, status: 'unavailable', filesystemKey: found.filesystemKey ?? null, capacityBytes: null,
      usedBytes: null, availableBytes: null, warningState: 'unknown', reasonCode: 'measurement_stale', measuredAt: iso(found.measuredAt) };
    return { storageKey, status: found.status, filesystemKey: found.filesystemKey ?? null, capacityBytes: num(found.capacityBytes),
      usedBytes: num(found.usedBytes), availableBytes: num(found.availableBytes), warningState: found.warningState,
      reasonCode: found.reasonCode ?? null, measuredAt: iso(found.measuredAt) };
  });
  const data = disks[0];
  const disk = data.status === 'measured' ? { storageKey: data.storageKey, capacityBytes: data.capacityBytes, usedBytes: data.usedBytes,
    availableBytes: data.availableBytes, warningState: data.warningState, measuredAt: data.measuredAt } : null;
  return { measuredAt: iso(row.measuredAt), databaseBytes: num(row.databaseBytes), disk, disks };
}

/**
 * GET /api/operations/decision-trend: 전 원천의 일별 판정 수·이상 신호 비율·정상 여부 미확인 비율. 운영 구성과 무관하다.
 * 운영 리포트(operational-v1)의 구성 생략 경로와 같은 범위·최신 판정 규칙·건수 조건(DECISION_TREND_SQL)을 원천마다 실행해
 * live·replay 범위를 합산한다(원천 범위 미확인 관측 제외). 판정이 없는 날도 0건으로 돌려준다.
 */
export async function readDecisionTrend(db: Pick<Pool, 'connect'>, days: number) {
  const client = await db.connect();
  try {
    await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
    const { rows: [period] } = await client.query(`WITH t AS (SELECT clock_timestamp() AS at_now,
        date_trunc('day',clock_timestamp() AT TIME ZONE $1) AS today)
      SELECT to_char(((today-make_interval(days=>$2::integer-1)) AT TIME ZONE $1) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "from",
        to_char(at_now AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS until,
        ARRAY(SELECT to_char(day,'YYYY-MM-DD') FROM generate_series(today-make_interval(days=>$2::integer-1),today,interval '1 day') day) AS days
      FROM t`, [TREND_TIMEZONE, days]);
    const { rows: sources } = await client.query('SELECT id FROM ops_sources ORDER BY id');
    const byDay = new Map<string, { total: number; decidable: number; alarms: number }>(
      period.days.map((day: string) => [day, { total: 0, decidable: 0, alarms: 0 }]));
    for (const source of sources) {
      for (const scope of ['live', 'replay']) {
        const result = await client.query(DECISION_TREND_SQL, [source.id, null, period.from, period.until, TREND_TIMEZONE, scope]);
        for (const item of result.rows) {
          const day = byDay.get(item.day) ?? { total: 0, decidable: 0, alarms: 0 };
          day.total += item.total;
          day.decidable += item.decidable;
          day.alarms += item.alarms;
          byDay.set(item.day, day);
        }
      }
    }
    await client.query('COMMIT');
    return {
      timezone: TREND_TIMEZONE, from: period.from, until: period.until,
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
