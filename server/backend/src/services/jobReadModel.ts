import type { Pool } from 'pg';

/**
 * 작업 목록·작업 상세 읽기(operations-ui.md '작업 목록·작업 상세'). `GET /api/jobs`·`GET /api/jobs/:id`가 쓴다.
 * - 범위: 요청자 본인 작업, 관리자는 전체(기존 규칙 그대로).
 * - `kind` 필터는 사용자가 요청하는 v03 작업만 본다. 종류마다 큐가 정해져 있어(021 run_jobs_v03_contract) 큐 조건을 함께 걸고,
 *   그래서 판정 작업(detect, 큐 detection)이 아무리 많아도 v03 큐 인덱스로 해당 큐 행만 읽는다. 지금 쓰기 경로는 모두 v03이다.
 * - 목록은 결과 원문(resultSummary)을 싣지 않는다(규칙 후보 결과는 그래프까지 커서). 화면 연결에 쓰는 id·건수만 `result`로 골라 낸다.
 * - 값은 저장된 그대로이고 다시 계산하지 않는다.
 */
type Queryable = Pick<Pool, 'query'>;

export class JobReadError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}

/** 종류 → 큐(021 run_jobs_v03_contract의 분기와 같다). detect는 시스템 작업이라 목록 필터에서 받지 않는다. */
export const LISTABLE_JOB_KINDS: Record<string, string> = {
  preview: 'training', train: 'training', evaluate: 'training', import_csv: 'training',
  evaluation_set: 'training', compare: 'training', recommend_relations: 'training',
  recommend_rule: 'llm_recommendation', recommend_feature: 'llm_recommendation',
  recommend_methods: 'llm_recommendation', recommend_thresholds: 'llm_recommendation',
  explain: 'llm_explanation', explain_protocol: 'llm_explanation',
};
export const JOB_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'blocked'] as const;
const LIMIT_DEFAULT = 50;
const LIMIT_MAX = 100;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type Cursor = { at: string; id: string };
export type JobListFilter = { kinds: string[] | null; statuses: string[] | null; limit: number; cursor: Cursor | null };

const encodeCursor = (cursor: Cursor) => Buffer.from(JSON.stringify(cursor)).toString('base64url');
/** 서버가 발급한 커서의 시각 형식(UTC 고정, 마이크로초 6자리 — 아래 `rawAt`). DB DateStyle과 무관하다. 그 밖은 400. */
const CURSOR_AT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;

function decodeCursor(value: unknown): Cursor | null {
  if (value === undefined) return null;
  try {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,512}$/.test(value)) throw new Error();
    const parsed = JSON.parse(Buffer.from(value, 'base64url').toString('utf8')) as Record<string, unknown>;
    if (Object.keys(parsed).sort().join() !== 'at,id' || typeof parsed.at !== 'string' || !CURSOR_AT.test(parsed.at) || !Number.isFinite(Date.parse(parsed.at))
      || typeof parsed.id !== 'string' || !UUID.test(parsed.id)) throw new Error();
    return { at: parsed.at, id: parsed.id };
  } catch { throw new JobReadError('invalid_cursor'); }
}

/** 쉼표 목록(`kind=train,compare`). 빈 값·중복·목록 밖 값은 형식 오류. */
function listOf(value: unknown, allowed: (item: string) => boolean, code: string): string[] | null {
  if (value === undefined) return null;
  if (typeof value !== 'string' || value.length > 512) throw new JobReadError(code);
  const items = value.split(',');
  if (!items.every((item) => item !== '' && allowed(item)) || new Set(items).size !== items.length) throw new JobReadError(code);
  return items;
}

export function parseJobListQuery(query: Record<string, unknown>): JobListFilter {
  if (Object.keys(query).some((key) => !['kind', 'status', 'limit', 'cursor'].includes(key))) throw new JobReadError('unknown_job_filter');
  const { limit } = query;
  if (limit !== undefined && (typeof limit !== 'string' || !/^[1-9]\d{0,2}$/.test(limit) || Number(limit) > LIMIT_MAX)) throw new JobReadError('invalid_limit');
  return {
    kinds: listOf(query.kind, (item) => Object.hasOwn(LISTABLE_JOB_KINDS, item), 'invalid_job_kind'),
    statuses: listOf(query.status, (item) => (JOB_STATUSES as readonly string[]).includes(item), 'invalid_job_status'),
    limit: limit === undefined ? LIMIT_DEFAULT : Number(limit),
    cursor: decodeCursor(query.cursor),
  };
}

const COMMON = `id, kind::text AS kind, status::text AS status, input_spec AS "inputSpec", attempt_count AS "attemptCount",
  max_attempts AS "maxAttempts", next_run_at AS "nextRunAt", error_code AS "errorCode",
  result_config_version_id AS "resultConfigVersionId", created_at AS "createdAt", finished_at AS "finishedAt"`;
// 결과 화면 연결 값. 결과가 객체가 아니면(없음 포함) 모두 null이다.
const RESULT_LINKS = `CASE WHEN jsonb_typeof(result_summary) = 'object' THEN jsonb_build_object(
    'configVersionId', result_summary->>'configVersionId', 'evaluationId', result_summary->>'evaluationId',
    'evaluationSetId', result_summary->>'evaluationSetId', 'comparisonId', result_summary->>'comparisonId',
    'itemCount', CASE WHEN jsonb_typeof(result_summary->'itemCount') = 'number' THEN result_summary->'itemCount' END,
    'failedCount', CASE WHEN jsonb_typeof(result_summary->'failedCount') = 'number' THEN result_summary->'failedCount' END) END`;

export async function readJobPage(db: Queryable, actor: { id: string; admin: boolean }, filter: JobListFilter) {
  const queues = filter.kinds ? [...new Set(filter.kinds.map((kind) => LISTABLE_JOB_KINDS[kind]))] : null;
  let rows: { id: string; rawAt: string }[];
  try {
    rows = (await db.query<{ id: string; rawAt: string }>(
      `SELECT ${COMMON}, ${RESULT_LINKS} AS result, to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "rawAt"
         FROM run_jobs
        WHERE (requested_by_actor_id = $1 OR $2::boolean)
          AND ($3::text[] IS NULL OR (contract_version = 'v03' AND queue = ANY($4::text[]::queue_kind[]) AND kind::text = ANY($3::text[])))
          AND ($5::text[] IS NULL OR status::text = ANY($5::text[]))
          AND ($6::timestamptz IS NULL OR (created_at, id) < ($6::timestamptz, $7::uuid))
        ORDER BY created_at DESC, id DESC LIMIT $8`,
      [actor.id, actor.admin, filter.kinds, queues, filter.statuses, filter.cursor?.at ?? null, filter.cursor?.id ?? null, filter.limit + 1],
    )).rows;
  } catch (error) {
    // 형식은 맞지만 범위 밖인 위조 커서 시각(예: 2월 30일)은 PG 날짜 오류 → 400.
    if (filter.cursor && ['22007', '22008', '22009'].includes((error as { code?: string }).code ?? '')) throw new JobReadError('invalid_cursor');
    throw error;
  }
  const items = rows.slice(0, filter.limit).map(({ rawAt: _at, ...item }) => item);
  const hasMore = rows.length > filter.limit;
  const last = rows[filter.limit - 1];
  return { items, pagination: { limit: filter.limit, hasMore, nextCursor: hasMore && last ? encodeCursor({ at: last.rawAt, id: last.id }) : null } };
}

/** 작업 1건 + 시도 기록(시도 번호 순). 작업자 이름·lease 값은 싣지 않는다. 없거나 볼 수 없으면 null. */
export async function readJobDetail(db: Queryable, actor: { id: string; admin: boolean }, id: string) {
  const { rows: [job] } = await db.query(
    `SELECT ${COMMON}, result_summary AS "resultSummary"
       FROM run_jobs WHERE id = $1 AND (requested_by_actor_id = $2 OR $3::boolean)`,
    [id, actor.id, actor.admin],
  );
  if (!job) return null;
  const { rows: attempts } = await db.query(
    `SELECT attempt_no AS "attemptNo", status::text AS status, started_at AS "startedAt", heartbeat_at AS "heartbeatAt",
            finished_at AS "finishedAt", error_code AS "errorCode"
       FROM run_job_attempts WHERE job_id = $1 ORDER BY attempt_no`,
    [id],
  );
  return { ...job, attempts };
}
