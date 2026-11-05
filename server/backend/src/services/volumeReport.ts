import type { Pool } from 'pg';
import { instant, validTimezone } from '../ruleset/thresholdStats.js';
import { ReportRequestError } from './operationalReport.js';

/**
 * 거래량 집계 GET /api/pipeline/reports/volume(trainer-service.md 거래량 집계 API). 새 학습 기간 단계의 그래프·건수 원천이다.
 * 표본은 학습 스냅샷처럼 기간을 먼저 적용하고 그 안에서 실행별 최신 revision을 고른다(worker/trainer/snapshot.py, thresholdStats.ts와 같은 순서).
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DAY_MS = 86_400_000;
const STATEMENT_TIMEOUT_MS = 10_000;
const BUCKET_MAX_DAYS = { month: null, day: 1096, hour: 92, minute: 7 } as const;
const TIME_BASES = { esb_occurred_at: 'o.source_log_occurred_at', persisted_at: 'o.persisted_at' } as const;
/** 시계열 표본 간격. 모든 버킷보다 짧아 어떤 버킷도 건너뛰지 않는다(시간대 오프셋·일광절약 변화의 최소 단위는 15분). */
const SERIES_STEP = { month: '1 day', day: '1 hour', hour: '15 minutes', minute: '1 minute' } as const;
type Bucket = keyof typeof BUCKET_MAX_DAYS;
type TimeBasis = keyof typeof TIME_BASES;

export type VolumeFilter = { sourceId: string; from: string; until: string; bucket: Bucket; timeBasis: TimeBasis; timezone: string };

/** 단위별 범위 상한 초과. 응답에 `bucket`·`maxDays`를 함께 준다. */
export class VolumeRangeError extends Error {
  constructor(readonly bucket: Bucket, readonly maxDays: number) { super('volume_range_too_large'); }
}

export function parseVolumeFilter(query: Record<string, unknown>): VolumeFilter {
  if (Object.keys(query).some((key) => !['sourceId', 'from', 'until', 'bucket', 'timeBasis', 'timezone'].includes(key))) throw new ReportRequestError('unknown_report_filter');
  const { sourceId, from, until, bucket, timeBasis, timezone = 'Asia/Seoul' } = query;
  if (typeof sourceId !== 'string' || !UUID.test(sourceId)) throw new ReportRequestError('invalid_sourceId');
  if (!instant(from) || !instant(until) || Date.parse(from) >= Date.parse(until)) throw new ReportRequestError('invalid_report_period');
  if (typeof bucket !== 'string' || !Object.hasOwn(BUCKET_MAX_DAYS, bucket)) throw new ReportRequestError('invalid_report_bucket');
  if (typeof timeBasis !== 'string' || !Object.hasOwn(TIME_BASES, timeBasis)) throw new ReportRequestError('invalid_report_time_basis');
  if (typeof timezone !== 'string' || timezone.length > 64 || !validTimezone(timezone)) throw new ReportRequestError('invalid_report_timezone');
  const maxDays = BUCKET_MAX_DAYS[bucket as Bucket];
  if (maxDays !== null && Date.parse(until) - Date.parse(from) > maxDays * DAY_MS) throw new VolumeRangeError(bucket as Bucket, maxDays);
  return { sourceId: sourceId.toLowerCase(), from: new Date(from).toISOString(), until: new Date(until).toISOString(), bucket: bucket as Bucket, timeBasis: timeBasis as TimeBasis, timezone };
}

/**
 * $1 원천 $2 from $3 until $4 단위 $5 시간대. ESB 기준에서 시각이 없는 행은 구간을 알 수 없어 저장 시각으로 기간에 넣고 `timeUnknownCount`로만 센다.
 * 빈 버킷은 0으로 채운다. 버킷 시작은 시간대 경계를 UTC로 돌려준다. 시계열은 실제 시각 축에서 만들어 관측과 같은 date_trunc로 버킷을 정한다:
 * 현지 시각으로 세면 일광절약 전환 때 없는 시각이 생기고 겹치는 시각이 사라진다.
 */
const volumeSql = ({ timeBasis, bucket }: Pick<VolumeFilter, 'timeBasis' | 'bucket'>) => {
  const time = TIME_BASES[timeBasis];
  const unknownTime = timeBasis === 'esb_occurred_at' ? `OR (${time} IS NULL AND o.persisted_at>=$2::timestamptz AND o.persisted_at<$3::timestamptz)` : '';
  return `WITH candidates AS (
      SELECT o.id,o.execution_id,o.revision_no,o.persisted_at,${time} AS occurred FROM data_observations o
      WHERE o.source_id=$1 AND o.layer='P' AND o.execution_id IS NOT NULL
        AND ((${time}>=$2::timestamptz AND ${time}<$3::timestamptz) ${unknownTime})
    ), latest AS (
      SELECT DISTINCT ON (execution_id) id,occurred FROM candidates ORDER BY execution_id,revision_no DESC,persisted_at DESC,id DESC
    ), counted AS (
      SELECT date_trunc($4,l.occurred,$5) AS bucket,p.status FROM latest l JOIN data_processes p ON p.observation_id=l.id WHERE l.occurred IS NOT NULL
    ), grouped AS (
      SELECT bucket,count(*)::int AS n,count(*) FILTER (WHERE status='S')::int AS s,count(*) FILTER (WHERE status='F')::int AS f,
        count(*) FILTER (WHERE status='P')::int AS p,count(*) FILTER (WHERE status='N')::int AS nn FROM counted GROUP BY bucket
    ), series AS (
      SELECT date_trunc($4,at,$5) AS bucket FROM generate_series($2::timestamptz,$3::timestamptz-interval '1 microsecond',interval '${SERIES_STEP[bucket]}') AS at
      UNION SELECT date_trunc($4,$3::timestamptz-interval '1 microsecond',$5)
    )
    SELECT to_char(x.bucket AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') AS "bucketStart",COALESCE(g.n,0) AS n,COALESCE(g.s,0) AS s,COALESCE(g.f,0) AS f,
      COALESCE(g.p,0) AS p,COALESCE(g.nn,0) AS nn,
      (SELECT count(*)::int FROM latest l JOIN data_processes p ON p.observation_id=l.id WHERE l.occurred IS NULL) AS "timeUnknownCount"
    FROM series x LEFT JOIN grouped g ON g.bucket=x.bucket ORDER BY x.bucket`;
};

type VolumeRow = { bucketStart: string; n: number; s: number; f: number; p: number; nn: number; timeUnknownCount: number };

/** 원천이 없으면 null(404). 시간대는 PostgreSQL이 아는 이름이어야 한다. */
export async function readVolume(database: () => Pick<Pool, 'connect'>, filter: VolumeFilter) {
  const client = await database().connect();
  try {
    await client.query('BEGIN READ ONLY');
    await client.query(`SET LOCAL statement_timeout=${STATEMENT_TIMEOUT_MS}`);
    const source = await client.query('SELECT 1 FROM ops_sources WHERE id=$1', [filter.sourceId]);
    if (!source.rowCount) { await client.query('ROLLBACK'); return null; }
    const zone = await client.query('SELECT 1 FROM pg_timezone_names WHERE name=$1', [filter.timezone]);
    if (!zone.rowCount) throw new ReportRequestError('invalid_report_timezone');
    const { rows } = await client.query<VolumeRow>(volumeSql(filter), [filter.sourceId, filter.from, filter.until, filter.bucket, filter.timezone]);
    await client.query('COMMIT');
    return {
      ...filter, totalCount: rows.reduce((sum, row) => sum + row.n, 0), timeUnknownCount: rows[0]?.timeUnknownCount ?? 0,
      buckets: rows.map((row) => ({
        bucketStart: row.bucketStart, processCount: row.n,
        statusCounts: { S: row.s, F: row.f, P: row.p, N: row.nn, other: row.n - row.s - row.f - row.p - row.nn },
      })),
    };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}
