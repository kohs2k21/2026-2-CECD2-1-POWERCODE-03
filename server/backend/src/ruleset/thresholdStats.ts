import type { Pool } from 'pg';
import { RuleSetError, SIGNIFICANT_DIGITS_MAX, THRESHOLD_MAX_LENGTH, decimalString, thresholdFixedDecimal } from './ruleSetContract.js';
import { softRuleField, type SoftRuleField } from './softRuleCatalog.js';

/**
 * 규칙별 수식 추천 POST /api/rule-set/threshold-stats(llm-recommendation-job.md). LLM·큐 없이 선택 기간 표본의 분위를 SQL로 계산한다.
 * 표본: 학습 스냅샷처럼 기간 필터를 먼저 적용하고 그 안에서 실행별 최신 revision을 고른 뒤(worker/trainer/snapshot.py), status=S, 최신 라벨이 anomaly가 아니고
 * 필드 값이 있는 행을 쓴다(LLM 작업과 같은 규칙, 기간 전체). 제외 구간은 선택된 시간 기준의 시각 열에 적용한다.
 */
const invalid = () => new RuleSetError('invalid_threshold_stats_request');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const OFFSET_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
const DAY_MS = 86_400_000;
const PERIOD_DAYS_MAX = 366;
const MINIMUM_N = 20;
const EXCLUDED_MAX = 32;
const EXCLUDED_NOTE_MAX = 200;
/** Q-L5(ii): 366일을 넘는 mode (a)는 일별 최대 이 건수만 표본으로 쓴다(근사, evidence.sampled). */
const DAILY_SAMPLE_MAX = 2000;
const STATEMENT_TIMEOUT_MS = 10_000;

const STATISTICS = { p95: 'percentile_cont(0.95) WITHIN GROUP (ORDER BY v)', p99: 'percentile_cont(0.99) WITHIN GROUP (ORDER BY v)', median: 'percentile_cont(0.5) WITHIN GROUP (ORDER BY v)', max: 'max(v)' } as const;
type Statistic = keyof typeof STATISTICS;
const MODES = ['active_model_training_start', 'recent_1m', 'recent_3m', 'custom'] as const;
type PeriodMode = typeof MODES[number];
const TIME_BASES = { esb_occurred_at: 'o.source_log_occurred_at', persisted_at: 'o.persisted_at' } as const;
type TimeBasis = keyof typeof TIME_BASES;
const REASON_CODES = ['known_incident', 'maintenance', 'collection_anomaly', 'value_spike', 'other'];

export type ThresholdStatsRequest = {
  field: string; statistic: Statistic; sourceId: string; mode: PeriodMode; from: string | null; until: string | null;
  timeBasis: TimeBasis | null; timezone: string; excludedIntervals: { from: string; until: string }[];
};
const plainObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const allowed = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every((key) => keys.includes(key));
/** 달력에 있는 날짜·시각인지 왕복으로 확인한다(Date.parse는 2월 30일을 3월로 넘긴다). */
const realCalendarTime = (text: string) => {
  const [year, month, day, hour, minute, second] = (text.match(/\d+/g) ?? []).slice(0, 6).map(Number);
  const value = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return value.getUTCFullYear() === year && value.getUTCMonth() === month - 1 && value.getUTCDate() === day &&
    value.getUTCHours() === hour && value.getUTCMinutes() === minute && value.getUTCSeconds() === second;
};
const instant = (value: unknown): value is string =>
  typeof value === 'string' && OFFSET_TIME.test(value) && Number.isFinite(Date.parse(value)) && realCalendarTime(value);
const utc = (value: string) => new Date(value).toISOString();
const validTimezone = (value: string) => {
  try { new Intl.DateTimeFormat('en', { timeZone: value }); return true; } catch { return false; }
};

/** 요청 형식 검사(400). 필드 등록 여부는 422 soft_rule_field_unknown. */
export function parseThresholdStatsRequest(body: unknown): ThresholdStatsRequest {
  if (!plainObject(body) || !allowed(body, ['field', 'statistic', 'period', 'sourceId', 'excludedIntervals'])) throw invalid();
  const { field, statistic = 'p95', period, sourceId, excludedIntervals = [] } = body;
  if (typeof field !== 'string' || typeof statistic !== 'string' || !Object.hasOwn(STATISTICS, statistic) || typeof sourceId !== 'string' || !UUID.test(sourceId) ||
      !plainObject(period) || !allowed(period, ['mode', 'from', 'until', 'timeBasis', 'timezone']) || !Array.isArray(excludedIntervals) ||
      excludedIntervals.length > EXCLUDED_MAX) throw invalid();
  const { mode, from, until, timeBasis, timezone = 'Asia/Seoul' } = period;
  if (typeof mode !== 'string' || !(MODES as readonly string[]).includes(mode) || typeof timezone !== 'string' || !validTimezone(timezone) ||
      (timeBasis !== undefined && (typeof timeBasis !== 'string' || !Object.hasOwn(TIME_BASES, timeBasis)))) throw invalid();
  if (mode === 'custom' ? !instant(from) || !instant(until) || Date.parse(from) >= Date.parse(until) : from !== undefined || until !== undefined) throw invalid();
  const intervals = excludedIntervals.map((item) => {
    if (!plainObject(item) || !allowed(item, ['from', 'until', 'reasonCode', 'note']) || !instant(item.from) || !instant(item.until) ||
        Date.parse(item.from) >= Date.parse(item.until) || (item.reasonCode !== undefined && !REASON_CODES.includes(item.reasonCode as string)) ||
        (item.note !== undefined && (typeof item.note !== 'string' || [...item.note].length > EXCLUDED_NOTE_MAX))) throw invalid();
    return { from: utc(item.from), until: utc(item.until) };
  });
  return {
    field, statistic: statistic as Statistic, sourceId: sourceId.toLowerCase(), mode: mode as PeriodMode,
    from: mode === 'custom' ? utc(from as string) : null, until: mode === 'custom' ? utc(until as string) : null,
    timeBasis: (timeBasis as TimeBasis | undefined) ?? null, timezone, excludedIntervals: intervals,
  };
}

type Queryable = Pick<Pool, 'query'>;
type ResolvedPeriod = { from: string; until: string; timeBasis: TimeBasis; resolvedFrom: string | null };

/**
 * mode (a): 활성 모델 집합 저장소가 아직 없으므로 v1·v2 투영만 쓴다(model-version.md). 활성 구성을 만든 train 작업의 input_spec.from,
 * 기준은 투영의 timeBasis persisted_at(요청이 정하면 그 값).
 */
async function activeModelPeriod(db: Queryable, request: ThresholdStatsRequest): Promise<ResolvedPeriod> {
  const { rows: [active] } = await db.query<{ id: string; from: string | null }>(`SELECT s.active_config_version_id AS id,
      (SELECT j.input_spec->>'from' FROM run_jobs j WHERE j.result_config_version_id=s.active_config_version_id AND j.kind='train'
        ORDER BY j.created_at DESC,j.id LIMIT 1) AS "from"
    FROM ops_system_state s WHERE s.id=1 AND s.active_config_version_id IS NOT NULL
      AND EXISTS(SELECT 1 FROM ml_config_models m WHERE m.config_version_id=s.active_config_version_id)`);
  if (!active) throw new RuleSetError('active_model_missing', 422);
  if (!active.from) throw new RuleSetError('active_model_period_unknown', 422, { hint: 'custom' });
  const { rows: [now] } = await db.query<{ until: string }>(`SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS until`);
  return { from: active.from, until: now.until, timeBasis: request.timeBasis ?? 'persisted_at', resolvedFrom: active.id };
}
/** 달력 월(요청 시각의 같은 날짜·시각, 달 끝은 PostgreSQL 규칙대로 그 달 마지막 날). */
async function recentPeriod(db: Queryable, request: ThresholdStatsRequest): Promise<ResolvedPeriod> {
  const months = request.mode === 'recent_1m' ? 1 : 3;
  const { rows: [row] } = await db.query<{ from: string; until: string }>(`WITH t AS (SELECT clock_timestamp() AS until)
    SELECT to_char(((until AT TIME ZONE $1 - make_interval(months=>$2)) AT TIME ZONE $1) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "from",
      to_char(until AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS until FROM t`, [request.timezone, months]);
  return { ...row, timeBasis: request.timeBasis ?? 'esb_occurred_at', resolvedFrom: null };
}
async function resolvePeriod(db: Queryable, request: ThresholdStatsRequest): Promise<ResolvedPeriod> {
  if (request.mode === 'active_model_training_start') return activeModelPeriod(db, request);
  if (request.mode !== 'custom') return recentPeriod(db, request);
  if (Date.parse(request.until!) - Date.parse(request.from!) > PERIOD_DAYS_MAX * DAY_MS) throw new RuleSetError('period_too_long');
  return { from: request.from!, until: request.until!, timeBasis: request.timeBasis ?? 'esb_occurred_at', resolvedFrom: null };
}
function checkExcluded(intervals: ThresholdStatsRequest['excludedIntervals'], period: ResolvedPeriod) {
  const sorted = [...intervals].sort((left, right) => Date.parse(left.from) - Date.parse(right.from));
  sorted.forEach((item, index) => {
    if (Date.parse(item.from) < Date.parse(period.from) || Date.parse(item.until) > Date.parse(period.until)) throw new RuleSetError('excluded_interval_out_of_range');
    if (index && Date.parse(sorted[index - 1].until) > Date.parse(item.from)) throw new RuleSetError('excluded_interval_overlap');
  });
}

/** Python datetime.fromisoformat이 받는 형식. 그 밖의 값은 계산 불가(결측과 같이 표본 제외), pg_input_is_valid가 2월 30일 같은 값을 거른다. */
const ISO_LOCAL = String.raw`^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?)?$`;
const ZONED = String.raw`(Z|[+-]\d{2}:\d{2})$`;
const readable = (raw: string) => `(${raw} ~ '${ISO_LOCAL}' AND pg_input_is_valid(${raw},'timestamptz'))`;
const PROCESS_COLUMNS: Record<string, string> = {
  TOTAL_COUNT: 'p.total_count', SUCCESS_COUNT: 'p.success_count', ERROR_COUNT: 'p.error_count', RETRY_COUNT: 'p.retry_count',
  START_TIME: 'p.start_time_raw', END_TIME: 'p.end_time_raw',
};
/**
 * 카탈로그 원천 식 → SQL(문자열은 모두 위 상수·카탈로그 값, 요청 값이 들어가지 않는다). 시각은 worker/derived_expression.py처럼 원천 시각 문자열로 계산한다
 * (collector는 parsed_times를 비워 둔다). 시간대 유무가 섞이면 파이썬도 뺄 수 없어(TypeError → None) 값 없음이다.
 */
function fieldExpression(field: SoftRuleField): string {
  if (field.source.kind === 'column') return PROCESS_COLUMNS[field.source.column];
  const [left, right] = field.source.ast.args.map((node) => PROCESS_COLUMNS[node.column]);
  if (field.source.ast.op === 'divide') return `${left}/NULLIF(${right},0)`;
  return `CASE WHEN ${readable(left)} AND ${readable(right)} THEN CASE
      WHEN ${left} ~ '${ZONED}' AND ${right} ~ '${ZONED}' THEN extract(epoch FROM ${left}::timestamptz-${right}::timestamptz)*1000
      WHEN ${left} !~ '${ZONED}' AND ${right} !~ '${ZONED}' THEN extract(epoch FROM ${left}::timestamp-${right}::timestamp)*1000 END END`;
}

type Sample = { n: number; min: number | null; max: number | null; p95: number | null; statistic: number | null };
async function measure(pool: Pool, field: SoftRuleField, request: ThresholdStatsRequest, period: ResolvedPeriod, sampled: boolean): Promise<Sample> {
  const time = TIME_BASES[period.timeBasis];
  const picked = sampled
    ? `SELECT v FROM (SELECT v,row_number() OVER (PARTITION BY date_trunc('day',occurred,$5) ORDER BY id) AS rank FROM valid) ranked WHERE rank<=${DAILY_SAMPLE_MAX}`
    : 'SELECT v FROM valid';
  const sql = `WITH candidates AS (
      SELECT o.id,o.execution_id,o.revision_no,o.persisted_at,${time} AS occurred FROM data_observations o
      WHERE o.source_id=$1 AND o.layer='P' AND o.execution_id IS NOT NULL AND ${time}>=$2::timestamptz AND ${time}<$3::timestamptz
    ), latest AS (
      SELECT DISTINCT ON (execution_id) id,occurred FROM candidates ORDER BY execution_id,revision_no DESC,persisted_at DESC,id DESC
    ), base AS (
      SELECT l.id,l.occurred,(${fieldExpression(field)})::float8 AS v
      FROM latest l JOIN data_processes p ON p.observation_id=l.id
      WHERE p.status='S'
        AND (SELECT e.label FROM data_label_evidence e WHERE e.observation_id=l.id ORDER BY e.revision_no DESC LIMIT 1) IS DISTINCT FROM 'anomaly'
        AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset($4::jsonb) AS x("from" timestamptz,until timestamptz)
          WHERE l.occurred>=x."from" AND l.occurred<x.until)
    ), valid AS (SELECT * FROM base WHERE v IS NOT NULL AND v NOT IN ('NaN','Infinity','-Infinity'))
    SELECT count(*)::int AS n,min(v) AS min,max(v) AS max,percentile_cont(0.95) WITHIN GROUP (ORDER BY v) AS p95,
      ${STATISTICS[request.statistic]} AS statistic FROM (${picked}) picked`;
  const client = await pool.connect();
  try {
    await client.query('BEGIN READ ONLY');
    await client.query(`SET LOCAL statement_timeout=${STATEMENT_TIMEOUT_MS}`);
    const parameters = [request.sourceId, period.from, period.until, JSON.stringify(request.excludedIntervals), ...(sampled ? [request.timezone] : [])];
    const { rows: [row] } = await client.query<Sample>(sql, parameters);
    await client.query('COMMIT');
    return row;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

/** `threshold`는 입력칸에 그대로 저장할 수 있어야 하므로 유효숫자 15자리·정수 필드는 정수로 맞춘다(rule-set.md threshold 규칙). 원값은 evidence. */
function thresholdText(value: number, field: SoftRuleField): string | null {
  const direct = field.valueType === 'integer' ? Math.round(value) : Number(value.toPrecision(SIGNIFICANT_DIGITS_MAX));
  const text = thresholdFixedDecimal(direct, field.valueType);
  if (text !== null) return text;
  const digits = field.displayPrecision + 6;
  const retried = field.displayPrecisionMode === 'significant' ? Number(value.toPrecision(digits)) : Number(value.toFixed(digits));
  return thresholdFixedDecimal(retried, field.valueType);
}
export function displayThreshold(value: number, field: SoftRuleField): string {
  const rounded = field.displayPrecisionMode === 'significant' ? Number(value.toPrecision(field.displayPrecision)) : Number(value.toFixed(field.displayPrecision));
  const text = decimalString(rounded);
  return text.length <= THRESHOLD_MAX_LENGTH ? text : decimalString(value);
}

export async function computeThresholdStats(pool: Pool, request: ThresholdStatsRequest) {
  const field = softRuleField(request.field);
  if (!field || field.status !== 'supported') throw new RuleSetError(`soft_rule_field_unknown:${request.field}`, 422);
  const { rowCount } = await pool.query('SELECT 1 FROM ops_sources WHERE id=$1', [request.sourceId]);
  if (!rowCount) throw new RuleSetError('source_not_found', 404);
  const period = await resolvePeriod(pool, request);
  checkExcluded(request.excludedIntervals, period);
  const sampled = request.mode === 'active_model_training_start' && Date.parse(period.until) - Date.parse(period.from) > PERIOD_DAYS_MAX * DAY_MS;
  const sample = await measure(pool, field, request, period, sampled);
  const evidence = {
    n: sample.n, min: sample.min, max: sample.max, p95: sample.p95,
    quantileMethod: sampled ? 'percentile_cont_daily_sampled' : 'percentile_cont', ...(sampled ? { sampled: true } : {}),
    exclusionRule: `status=S, explicit anomaly labels excluded, excludedIntervals=${request.excludedIntervals.length}`, scope: 'period',
    period: { mode: request.mode, from: period.from, until: period.until, timeBasis: period.timeBasis, resolvedFrom: period.resolvedFrom },
  };
  const base = { field: field.fieldId, statistic: request.statistic, unit: field.unit, source: 'data_p95', evidence, rationale: null };
  const closed = { ...base, operator: null, threshold: null, displayThreshold: null };
  if (sample.n < MINIMUM_N || sample.min === sample.max || sample.statistic === null) return { ...closed, status: 'no_applicable' };
  const threshold = thresholdText(sample.statistic, field);
  if (threshold === null) return { ...closed, status: 'rejected', rejectedReason: 'threshold_serialization_failed' };
  return { ...base, operator: 'gt', threshold, displayThreshold: displayThreshold(sample.statistic, field), status: 'proposed' };
}
