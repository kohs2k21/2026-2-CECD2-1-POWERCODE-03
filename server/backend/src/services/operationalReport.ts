const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
export class ReportRequestError extends Error {
  constructor(readonly code: string) { super(code); }
}
export type ReportFilter = {
  sourceId: string; configVersionId: string; from: string; until: string;
  timezone: string; scope: 'live' | 'replay'; limit: number; offset: number;
};
export function parseCandidateFilter(query: Record<string, unknown>) {
  if (Object.keys(query).some(key => !['sourceId','limit','cursor'].includes(key))) throw new ReportRequestError('unknown_report_filter');
  if (typeof query.sourceId !== 'string' || !UUID.test(query.sourceId)) throw new ReportRequestError('invalid_sourceId');
  let after: {createdAt:string;id:string} | null = null;
  if (query.cursor !== undefined) {
    try {
      if (typeof query.cursor !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(query.cursor)) throw new Error();
      after = JSON.parse(Buffer.from(query.cursor,'base64url').toString('utf8'));
      if (!after || Object.keys(after).sort().join(',') !== 'createdAt,id' || !time(after.createdAt) ||
          typeof after.id !== 'string' || !UUID.test(after.id)) throw new Error();
    } catch {throw new ReportRequestError('invalid_cursor');}
  }
  return {sourceId:query.sourceId,limit:pagination(query).limit,after};
}
function pagination(query: Record<string, unknown>) {
  const integers = {limit: 31, offset: 0};
  for (const key of ['limit','offset'] as const) {
    const value = query[key];
    if (value === undefined) continue;
    if (typeof value !== 'string' || !/^(0|[1-9]\d{0,4})$/.test(value) ||
        Number(value) > (key === 'limit' ? 100 : 10000) || (key === 'limit' && Number(value) === 0)) {
      throw new ReportRequestError(`invalid_${key}`);
    }
    integers[key] = Number(value);
  }
  return integers;
}
function time(value: unknown): value is string {
  if (typeof value !== 'string' || !UTC_TIME.test(value) || value.startsWith('0000-')) return false;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 19) === value.slice(0, 19);
}
function microseconds(value: string): bigint {
  const fraction = value.slice(19,-1).replace(/^\./,'').padEnd(6,'0');
  return BigInt(Date.parse(value.slice(0,19)+'Z'))*1000n+BigInt(fraction);
}
export function parseReportFilter(query: Record<string, unknown>): ReportFilter {
  const permitted = ['sourceId','configVersionId','from','until','timezone','scope','limit','offset'];
  if (Object.keys(query).some(key => !permitted.includes(key))) throw new ReportRequestError('unknown_report_filter');
  for (const key of ['sourceId','configVersionId']) {
    if (typeof query[key] !== 'string' || !UUID.test(query[key] as string)) throw new ReportRequestError(`invalid_${key}`);
  }
  if (!time(query.from) || !time(query.until) || microseconds(query.from) >= microseconds(query.until) ||
      microseconds(query.until) - microseconds(query.from) > 31n * 86400000000n) throw new ReportRequestError('invalid_report_period');
  if (query.scope !== 'live' && query.scope !== 'replay') throw new ReportRequestError('invalid_report_scope');
  if (typeof query.timezone !== 'string' || query.timezone.length > 64 ||
      !/^[A-Za-z_]+(?:\/[A-Za-z_+\-0-9]+){0,2}$/.test(query.timezone)) throw new ReportRequestError('invalid_report_timezone');
  try { new Intl.DateTimeFormat('en', { timeZone: query.timezone }); }
  catch { throw new ReportRequestError('invalid_report_timezone'); }
  return {...query, ...pagination(query)} as ReportFilter;
}
export function reportParameters(filter: ReportFilter): unknown[] {
  return [filter.sourceId, filter.configVersionId, filter.from, filter.until, filter.timezone, filter.scope, filter.limit, filter.offset];
}

// Origin is authoritative for switchable sources; older unknown sources are never called live.
export const SCOPED_OBSERVATIONS = `WITH parameters AS (
 SELECT $1::uuid AS source_id,$2::uuid AS config_id,$3::timestamptz AS report_from,
 $4::timestamptz AS report_until,$5::text AS timezone,$6::text AS scope
), scoped AS (
 SELECT o.*, CASE WHEN i.mode='api' THEN 'live' WHEN i.mode='csv' THEN 'replay'
   WHEN s.ingestion_method='customer-api' THEN 'live'
   WHEN s.ingestion_method IN ('demo-replay','csv-replay-v1') THEN 'replay' ELSE 'unknown' END AS report_scope
 FROM data_observations o JOIN ops_sources s ON s.id=o.source_id
 LEFT JOIN data_input_origins i ON i.observation_id=o.id
 WHERE o.source_id=$1 AND o.layer='P' AND o.persisted_at<$4::timestamptz
), ranked AS (
 SELECT o.*,row_number() OVER(PARTITION BY execution_id ORDER BY revision_no DESC,persisted_at DESC,id DESC) AS rank
 FROM scoped o WHERE execution_id IS NOT NULL AND report_scope=$6
), latest AS (
 SELECT o.*,d.id AS decision_id,d.status AS decision_status,d.rule_status,d.ml_status,d.evidence,d.decided_at,
 d.source_latency_ms,d.latency_status
 FROM ranked o LEFT JOIN LATERAL (
  SELECT d.* FROM run_process_decisions d WHERE d.process_observation_id=o.id AND d.config_version_id=$2
   AND d.decided_at<$4::timestamptz ORDER BY d.decided_at DESC,d.id DESC LIMIT 1
 ) d ON true WHERE o.rank=1
), cohort AS (SELECT * FROM latest WHERE decided_at >= $3::timestamptz)
`;

export const OPERATIONAL_SQL = `${SCOPED_OBSERVATIONS}, days AS (
 SELECT day::date::text AS day FROM generate_series(($3::timestamptz AT TIME ZONE $5)::date,
 (($4::timestamptz-interval '1 microsecond') AT TIME ZONE $5)::date,interval '1 day') day
), counts AS (
 SELECT (decided_at AT TIME ZONE $5)::date::text AS day,count(*)::integer AS total,
 count(*) FILTER(WHERE decision_status<>'unavailable' AND
   (evidence->'anomalous'='true'::jsonb OR (decision_status='complete' AND evidence->'anomalous'='false'::jsonb)))::integer AS decidable,
 count(*) FILTER(WHERE decision_status<>'unavailable' AND evidence->'anomalous'='true'::jsonb)::integer AS alarms,
 count(*) FILTER(WHERE rule_status='failed' OR ml_status='failed')::integer AS failed,
 max(decided_at) AS last_processed_at
 FROM cohort GROUP BY 1
), pending AS (
 SELECT (persisted_at AT TIME ZONE $5)::date::text AS day,count(*)::integer AS pending
 FROM latest WHERE decision_id IS NULL AND persisted_at >= $3::timestamptz GROUP BY 1
), pairs AS (
 SELECT (c.decided_at AT TIME ZONE $5)::date::text AS day,path,
 count(*) FILTER(WHERE comparable)::integer AS comparable,
 count(*) FILTER(WHERE comparable AND rule_hit AND ml_hit)::integer AS both_count,
 count(*) FILTER(WHERE comparable AND rule_hit AND NOT ml_hit)::integer AS rule_only,
 count(*) FILTER(WHERE comparable AND NOT rule_hit AND ml_hit)::integer AS ml_only,
 count(*) FILTER(WHERE comparable AND NOT rule_hit AND NOT ml_hit)::integer AS neither
 FROM cohort c CROSS JOIN (VALUES ('hr'),('sr')) p(path)
 CROSS JOIN LATERAL (SELECT
  c.evidence#>ARRAY['checkers',path,'status']='"succeeded"'::jsonb AND
  c.evidence#>'{checkers,ml,status}'='"succeeded"'::jsonb AND
  jsonb_typeof(c.evidence#>ARRAY['checkers',path,'anomalous'])='boolean' AND
  jsonb_typeof(c.evidence#>'{checkers,ml,anomalous}')='boolean' AS comparable,
  c.evidence#>ARRAY['checkers',path,'anomalous']='true'::jsonb AS rule_hit,
  c.evidence#>'{checkers,ml,anomalous}'='true'::jsonb AS ml_hit) flags
 GROUP BY 1,2
)
SELECT days.day,coalesce(c.total,0) AS total,coalesce(c.decidable,0) AS decidable,
 coalesce(c.alarms,0) AS alarms,coalesce(c.failed,0) AS failed,c.last_processed_at AS "lastProcessedAt",
 coalesce(p.pending,0) AS pending,
 (SELECT jsonb_object_agg(path,jsonb_build_object('comparable',comparable,'both',both_count,'ruleOnly',rule_only,
  'mlOnly',ml_only,'neither',neither)) FROM pairs WHERE pairs.day=days.day) AS comparisons,
 (SELECT count(*)::integer FROM days) AS "totalDays"
 FROM days LEFT JOIN counts c USING(day) LEFT JOIN pending p USING(day)
 ORDER BY days.day LIMIT $7 OFFSET $8`;

export const COVERAGE_SQL = `${SCOPED_OBSERVATIONS}
SELECT count(*) FILTER(WHERE report_scope='unknown')::integer AS "unknownScopeObservations",
 count(*) FILTER(WHERE report_scope=$6 AND execution_id IS NULL)::integer AS "missingIdentityObservations"
 FROM scoped WHERE persisted_at >= $3::timestamptz`;

export const COHORT_SUMMARY_SQL = `${SCOPED_OBSERVATIONS}
SELECT count(*)::integer AS "decisionCount",
 (($4::timestamptz-interval '1 microsecond') AT TIME ZONE $5)::date-($3::timestamptz AT TIME ZONE $5)::date+1 AS "totalDays"
 FROM cohort`;

export const BACKLOG_SQL = `${SCOPED_OBSERVATIONS}
SELECT count(*)::integer AS targets,
 count(*) FILTER(WHERE j.status='queued')::integer AS queued,
 count(*) FILTER(WHERE j.status='running')::integer AS running,
 count(*) FILTER(WHERE j.status='failed' AND j.finished_at >= $3::timestamptz AND j.finished_at < $4::timestamptz)::integer AS failed,
 extract(epoch FROM clock_timestamp()-min(j.created_at) FILTER(WHERE j.status IN ('queued','running'))) AS "oldestWaitSeconds"
 FROM run_jobs j JOIN scoped o ON j.input_spec->>'observationId'=o.id::text
 WHERE j.kind='detect' AND j.input_config_version_id=$2 AND o.report_scope=$6`;

export const COST_KEYS = ['totalMs','preprocessMs','trainMs','evaluationMs','inferenceMs','ruleMs','queueWaitMs','processRssBytes','throughput'];
export const COST_VALUE_SQL = `CASE WHEN jsonb_typeof(m.value->'value')='number' THEN
 CASE WHEN (m.value->>'value')::numeric BETWEEN 0 AND '1e100'::numeric THEN (m.value->>'value')::double precision END END`;
export const DETECTOR_COST_SQL = `${SCOPED_OBSERVATIONS}
SELECT m.key,count(*)::integer AS samples,sum(sample.value) AS sum,
 percentile_cont(.5) WITHIN GROUP(ORDER BY sample.value) AS p50,
 percentile_cont(.95) WITHIN GROUP(ORDER BY sample.value) AS p95,
 (SELECT count(*)::integer FROM cohort) AS total
 FROM cohort c CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(c.evidence#>'{telemetry,metrics}')='object'
 THEN c.evidence#>'{telemetry,metrics}' ELSE '{}'::jsonb END) m
 CROSS JOIN LATERAL (SELECT ${COST_VALUE_SQL} AS value) sample
 WHERE c.evidence#>>'{telemetry,metricVersion}'='execution-cost-v1' AND c.evidence#>>'{telemetry,status}'='measured'
 AND m.key=ANY($7::text[]) AND sample.value IS NOT NULL
 AND m.value->>'unit'=CASE WHEN m.key='processRssBytes' THEN 'bytes' WHEN m.key='throughput' THEN 'rows/s' ELSE 'ms' END
 GROUP BY m.key`;

export function measurement(value: number | null, unit: string, numerator: number | null,
  denominator: number | null, coverage: number | null, reason: string | null = null) {
  return {status: value === null ? 'unavailable' : 'measured', value, unit, numerator, denominator, coverage, reason};
}
export function dailyReport(row: Record<string, any>) {
  const total = row.total, decidable = row.decidable, alarms = row.alarms;
  const count = (n: number) => measurement(total ? n : null, 'process', total ? n : null, total, total ? 1 : null, total ? null : 'no_targets');
  const comparison = (path: string) => {
    const pair = row.comparisons?.[path], available = pair?.comparable ?? 0;
    const reason = !total ? 'no_targets' : !available ? 'checker_evidence_unavailable' : null;
    return {total, comparable: available, unavailable: total - available, coverage: total ? available / total : null,
      both: measurement(available ? pair.both : null, 'process', available ? pair.both : null, available, total ? available / total : null, reason),
      ruleOnly: measurement(available ? pair.ruleOnly : null, 'process', available ? pair.ruleOnly : null, available, total ? available / total : null, reason),
      mlOnly: measurement(available ? pair.mlOnly : null, 'process', available ? pair.mlOnly : null, available, total ? available / total : null, reason),
      neither: measurement(available ? pair.neither : null, 'process', available ? pair.neither : null, available, total ? available / total : null, reason)};
  };
  return {day: row.day, processCount: count(total), alarmProcessCount: count(alarms),
    alarmRatio: measurement(decidable ? alarms / decidable : null, 'ratio', alarms, decidable, total ? decidable / total : null,
      !total ? 'no_targets' : !decidable ? 'no_decidable_processes' : null),
    unavailableRate: measurement(total ? (total - decidable) / total : null, 'ratio', total ? total - decidable : null, total,
      total ? 1 : null, total ? null : 'no_targets'),
    failedProcessCount: count(row.failed), lastProcessedAt: row.lastProcessedAt,
    pendingArrivalCount: measurement(row.pending || null, 'process', row.pending || null, row.pending, row.pending ? 1 : null,
      row.pending ? null : 'no_pending_targets'),
    comparisons: {hrMl: comparison('hr'), srMl: comparison('sr')}};
}
export function costReport(rows: Record<string, any>[], total: number, keys = COST_KEYS) {
  return Object.fromEntries(keys.map(key => {
    const row = rows.find(item => item.key === key), samples = row?.samples ?? 0;
    const unit = key === 'processRssBytes' ? 'bytes' : key === 'throughput' ? 'rows/s' : 'ms';
    const reason = !total ? 'no_targets' : !samples ? 'telemetry_unavailable' : null;
    const metric = (value: number | null) => measurement(value, unit, null, samples, total ? samples / total : null, reason);
    return [key, {samples, targets: total, sum: ['processRssBytes','throughput'].includes(key) ? undefined : metric(row ? Number(row.sum) : null),
      p50: metric(row ? Number(row.p50) : null), p95: metric(row ? Number(row.p95) : null)}];
  }));
}
export const DETECTOR_COST_KEYS = COST_KEYS.filter(key => !['trainMs','evaluationMs'].includes(key));
