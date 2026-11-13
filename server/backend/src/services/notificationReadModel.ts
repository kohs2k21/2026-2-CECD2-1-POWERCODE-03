import type { Pool, PoolClient } from 'pg';
import { parseGroupKey } from './incidentReadModel.js';
import { RISK_RANK, type NotificationSettings } from './userSettings.js';

/**
 * 알림 목록·미확인 수·읽음 표시(contracts/notifications.md). 알림 항목은 저장하지 않고 읽을 때 만든다(사용자 11/13 "사건 단위 + 묶이지 않은 판정").
 *
 * 알림 시각(순번) = run_decision_events.id. 006·012 트리거가 advisory lock(81736, 0) 아래에서 붙이는 identity라 id 순서 = 커밋 순서다.
 * 조회 범위: 최근 NOTIFICATION_WINDOW_EVENTS개 이벤트(id > 최대 id − 범위, PK 범위 읽기). 범위 밖으로 밀려난 항목은 패널에서 사라진다.
 *
 * 1) 이벤트 판단(사용자 알림 설정, Detector 위험도 틀 Q-N1·Q-N1-1 ②): 이상 ∧ 판정 기준 충족 ∧ [위험도 ≥ 하한 ∨ (미정 포함 ∧ 단계 없는 근거 있음)]
 *    - 이상: 최종 = status<>unavailable ∧ evidence.anomalous=true(analysis-ui 이상 신호와 같음), 중간 기록 first_anomaly·evidence_added = 이상
 *    - 판정 기준: interim = 중간 기록·최종 모두, final_any = 최종만, final_complete = 최종 ∧ status=complete
 *    - 위험도: 저장된 v4 composite.risk만(재계산 없음, D7). v3·형식 오류 = 위험도 미정. 단계 없는 근거 = 미정이거나 unratedMethods가 비어 있지 않음
 * 2) 묶이지 않은 판정 항목(key job:<detect 작업> 또는 작업 없는 판정 decision:<판정>): 그 작업의 최종 판정이 사건 구성원이 아닌 것만.
 *    알림(순번 갱신) = 처음 통과(opened), 앞선 통과보다 위험도 상승(risk_raised), 앞서 통과한 뒤 최종 없음(no_final)·이상 아닌 최종(withdrawn).
 *    근거 추가·최종 도착만으로는 순번을 바꾸지 않는다(D8: 같은 알림 내용만 갱신).
 * 3) 사건 항목(key incident:<흐름 첫 사건>): 통과한 구성원 판정(최종)마다 알림 — 첫 구성원 opened, 위험도 상승 risk_raised, 그 밖 grew.
 *    흐름 첫 사건 = 병합 대상(merged_into)·앞 구간(continues_from)을 끝까지 따라간 사건 중 opened_at이 가장 이른 것(사슬 INCIDENT_HEAD_DEPTH 단계까지).
 *    어긋난 연결(incidents-ui flags)은 따로 거르지 않는다. 링크는 사건 상세가 어느 사건 id든 흐름으로 풀어 준다.
 * 4) 읽음: 항목 순번 > max(모두 읽음 순번, 그 항목 읽음 순번)이면 미확인. 묶인 판정의 시각은 판정 이벤트 순번이므로 "모두 읽음" 뒤에 묶여도 다시 미확인이 되지 않는다.
 */
type Queryable = Pick<Pool, 'query'>;

export const NOTIFICATION_WINDOW_EVENTS = 5000;
export const NOTIFICATION_LIST_LIMITS = { default: 20, max: 50 } as const;
export const NOTIFICATION_READ_ITEMS_MAX = 50;
export const INCIDENT_HEAD_DEPTH = 500;
export const NOTIFICATION_VIEWS = ['unread', 'all'] as const;
export type NotificationView = typeof NOTIFICATION_VIEWS[number];
export type NotificationCursor = { seq: string; key: string };

const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
const RATED = (risk: string) => `coalesce(${risk}->>'status'='rated' AND ${risk}->>'level' IN ('critical','warning','info'),false)`;

/**
 * 공통 CTE. 인자: $1 범위 이벤트 수, $2 판정 기준, $3 하한 순위(1~3), $4 미정 포함, $5 actor_id, $6 사슬 깊이.
 * 결과 CTE `state`: kind, item_key, notify_seq, notified_at, reason, unread + 화면 값 열.
 */
const STATE_CTE = `WITH RECURSIVE top AS (
  SELECT coalesce(max(id),0) AS top FROM run_decision_events
), ev AS (
  SELECT e.id AS seq,e.event_kind AS kind,e.created_at,d.id AS decision_id,coalesce(d.job_id,i.job_id) AS job_id,d.status::text AS status,
    CASE WHEN e.event_kind='final' THEN d.evidence WHEN e.event_kind IN ('first_anomaly','evidence_added') THEN i.payload END AS snap
  FROM top JOIN run_decision_events e ON e.id>top.top-$1::bigint
  LEFT JOIN run_process_decisions d ON e.event_kind='final' AND d.id=e.decision_id
  LEFT JOIN run_decision_interims i ON e.event_kind<>'final' AND i.id=e.interim_id
  WHERE (e.event_kind='final' AND d.id IS NOT NULL) OR (e.event_kind<>'final' AND i.id IS NOT NULL)
), x AS (
  -- evidence·payload는 행마다 한 번만 풀어 최상위 키 3개만 꺼낸다(analysisReadModel fields와 같은 방식).
  SELECT ev.seq,ev.kind,ev.created_at,ev.decision_id,ev.job_id,ev.status,f.anomalous,
    CASE WHEN f."schemaVersion"='4'::jsonb AND jsonb_typeof(f.composite)='object' THEN f.composite END AS composite
  FROM ev CROSS JOIN LATERAL jsonb_to_record(coalesce(
    jsonb_path_query_first(ev.snap,'strict $ ? (@.type() == "object")','{}',true),'{}')) AS f(anomalous jsonb,"schemaVersion" jsonb,composite jsonb)
), r AS (
  SELECT x.seq,x.kind,x.created_at,x.decision_id,x.job_id,x.status,
    coalesce('job:'||x.job_id::text,'decision:'||x.decision_id::text) AS item_key,
    CASE WHEN x.kind='final' THEN coalesce(x.status<>'unavailable' AND x.anomalous='true'::jsonb,false)
      ELSE x.kind IN ('first_anomaly','evidence_added') END AS anomaly,
    CASE WHEN x.composite->>'grade' IN ('anomaly','anomaly_sign','normal','undetermined') THEN x.composite->>'grade' END AS grade,
    CASE WHEN ${RATED("x.composite->'risk'")} THEN x.composite->'risk'->>'level' END AS risk_level,
    (NOT ${RATED("x.composite->'risk'")} OR CASE WHEN jsonb_typeof(x.composite->'risk'->'unratedMethods')='array'
      THEN jsonb_array_length(x.composite->'risk'->'unratedMethods')>0 ELSE false END) AS has_unrated
  FROM x
), q AS (
  SELECT r.*,CASE r.risk_level WHEN 'critical' THEN 3 WHEN 'warning' THEN 2 WHEN 'info' THEN 1 ELSE 0 END AS rank FROM r
), ok AS (
  SELECT q.*,(q.anomaly AND CASE $2::text WHEN 'interim' THEN true WHEN 'final_any' THEN q.kind='final' ELSE q.kind='final' AND q.status='complete' END
    AND (q.rank>=$3::integer OR ($4::boolean AND q.has_unrated))) AS pass
  FROM q
), mem AS (
  SELECT m.decision_id,m.incident_id,m.added_at FROM ok JOIN run_incident_members m ON m.decision_id=ok.decision_id WHERE ok.kind='final'
), bundled AS (
  SELECT DISTINCT ok.item_key FROM ok JOIN mem USING (decision_id)
), dev AS (
  SELECT ok.* FROM ok WHERE NOT EXISTS (SELECT 1 FROM bundled b WHERE b.item_key=ok.item_key)
), dprior AS (
  SELECT dev.*,max(rank) FILTER (WHERE pass) OVER w AS prior_rank,coalesce(bool_or(pass) OVER w,false) AS prior_pass
  FROM dev WINDOW w AS (PARTITION BY item_key ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
), dnote AS (
  SELECT item_key,seq,created_at,CASE WHEN pass AND NOT prior_pass THEN 'opened' WHEN pass AND rank>coalesce(prior_rank,0) THEN 'risk_raised'
    WHEN NOT pass AND prior_pass AND (kind='no_final' OR (kind='final' AND NOT anomaly)) THEN 'withdrawn' END AS reason
  FROM dprior
), dlast AS (
  SELECT DISTINCT ON (item_key) item_key,seq AS notify_seq,created_at AS notified_at,reason FROM dnote WHERE reason IS NOT NULL ORDER BY item_key,seq DESC
), ditems AS (
  SELECT 'decision'::text AS kind,l.item_key,l.notify_seq,l.notified_at,l.reason,
    (array_agg(d.kind ORDER BY d.seq DESC))[1] AS last_event,(array_agg(d.job_id ORDER BY d.seq DESC))[1] AS job_id,
    (array_agg(d.decision_id ORDER BY d.seq DESC) FILTER (WHERE d.kind='final'))[1] AS decision_id,
    (array_agg(d.status ORDER BY d.seq DESC) FILTER (WHERE d.kind='final'))[1] AS completeness,
    (array_agg(d.grade ORDER BY d.seq DESC) FILTER (WHERE d.anomaly))[1] AS grade,
    (array_agg(d.risk_level ORDER BY d.seq DESC) FILTER (WHERE d.anomaly))[1] AS risk_level,
    coalesce((array_agg(d.has_unrated ORDER BY d.seq DESC) FILTER (WHERE d.anomaly))[1],false) AS has_unrated,
    NULL::uuid AS incident_id,NULL::integer AS members
  FROM dlast l JOIN dev d ON d.item_key=l.item_key GROUP BY l.item_key,l.notify_seq,l.notified_at,l.reason
), up(start_id,id,depth) AS (
  SELECT DISTINCT incident_id,incident_id,0 FROM mem
  UNION
  SELECT up.start_id,n.id,up.depth+1 FROM up CROSS JOIN LATERAL (
    SELECT e.target_incident_id FROM run_incident_events e WHERE e.incident_id=up.id AND e.kind='merged_into'
    UNION ALL SELECT i.continues_from FROM run_incidents i WHERE i.id=up.id AND i.continues_from IS NOT NULL
  ) n(id) WHERE up.depth<$6::integer
), head AS (
  SELECT DISTINCT ON (up.start_id) up.start_id,up.id AS head_id FROM up JOIN run_incidents i ON i.id=up.id
  WHERE i.continues_from IS NULL AND NOT EXISTS (SELECT 1 FROM run_incident_events e WHERE e.incident_id=up.id AND e.kind='merged_into')
  ORDER BY up.start_id,i.opened_at,i.id
), iev AS (
  SELECT coalesce(h.head_id,mem.incident_id) AS incident_id,ok.seq,ok.rank,ok.has_unrated,mem.added_at
  FROM mem JOIN ok ON ok.decision_id=mem.decision_id AND ok.kind='final' AND ok.pass LEFT JOIN head h ON h.start_id=mem.incident_id
), iprior AS (
  SELECT iev.*,max(rank) OVER w AS prior_rank,count(*) OVER w AS prior_n
  FROM iev WINDOW w AS (PARTITION BY incident_id ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
), iitems AS (
  SELECT 'incident'::text AS kind,'incident:'||incident_id::text AS item_key,max(seq) AS notify_seq,
    (array_agg(added_at ORDER BY seq DESC))[1] AS notified_at,
    (array_agg(CASE WHEN prior_n=0 THEN 'opened' WHEN rank>coalesce(prior_rank,0) THEN 'risk_raised' ELSE 'grew' END ORDER BY seq DESC))[1] AS reason,
    NULL::text AS last_event,NULL::uuid AS job_id,NULL::uuid AS decision_id,NULL::text AS completeness,NULL::text AS grade,
    CASE max(rank) WHEN 3 THEN 'critical' WHEN 2 THEN 'warning' WHEN 1 THEN 'info' END AS risk_level,bool_or(has_unrated) AS has_unrated,
    incident_id,count(*)::integer AS members
  FROM iprior GROUP BY incident_id
), items AS (
  SELECT * FROM ditems UNION ALL SELECT * FROM iitems
), state AS (
  SELECT items.*,items.notify_seq>greatest(coalesce(c.read_through,0),coalesce(rd.read_seq,0)) AS unread
  FROM items LEFT JOIN ops_user_notification_cursors c ON c.actor_id=$5::text
  LEFT JOIN ops_user_notification_reads rd ON rd.actor_id=$5::text AND rd.item_key=items.item_key
)`;

const baseParams = (actorId: string, settings: NotificationSettings, windowEvents: number) =>
  [windowEvents, settings.decisionBasis, RISK_RANK[settings.minRiskLevel], settings.includeUnrated, actorId, INCIDENT_HEAD_DEPTH];

const encodeCursor = (value: NotificationCursor) => Buffer.from(JSON.stringify([value.seq, value.key]), 'utf8').toString('base64url');
const ITEM_KEY = /^(incident|job|decision):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SEQ = /^(0|[1-9]\d{0,18})$/;
export const isItemKey = (value: unknown): value is string => typeof value === 'string' && ITEM_KEY.test(value);
export const isSeq = (value: unknown): value is string => typeof value === 'string' && SEQ.test(value) && BigInt(value) <= 9223372036854775807n;
/** 커서 = 응답 nextCursor 그대로(base64url JSON [순번, 항목 키]). 형식이 다르면 null. */
export function decodeCursor(value: string): NotificationCursor | null {
  if (value.length > 256 || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const parsed = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
    if (Array.isArray(parsed) && parsed.length === 2 && isSeq(parsed[0]) && isItemKey(parsed[1])) return { seq: parsed[0], key: parsed[1] };
  } catch { /* 아래 null */ }
  return null;
}

type Row = Record<string, any>;
const riskOf = (row: Row) => ({ riskStatus: row.risk_level ? 'rated' : 'unrated', riskLevel: row.risk_level ?? null, hasUnratedEvidence: Boolean(row.has_unrated) });
/** 판정 항목 현재 단계(마지막 이벤트): 중간 판정 → 근거 추가 → 최종 / 대체됨(재처리 중) / 최종 없음 / 이상이 아닌 최종. */
const stageOf = (row: Row) => ({ first_anomaly: 'interim', evidence_added: 'updated', superseded: 'superseded', no_final: 'no_final' } as Record<string, string>)[row.last_event]
  ?? (row.reason === 'withdrawn' ? 'cleared' : 'final');

function itemOf(row: Row) {
  const base = { key: row.item_key as string, seq: row.seq_text as string, reason: row.reason as string, notifiedAt: row.notified_at as string, unread: Boolean(row.unread) };
  if (row.kind === 'incident') {
    const { kind, value, valueHash } = parseGroupKey(row.group_key ?? '');
    return { ...base, type: 'incident' as const, incident: { id: row.incident_id as string, openedAt: row.opened_at as string | null,
      criteria: { kind, value, longValue: valueHash !== null }, memberDecisions: row.members as number, ...riskOf(row) } };
  }
  return { ...base, type: 'decision' as const, decision: { detailId: (row.decision_id ?? row.job_id) as string, decisionId: row.decision_id ?? null,
    jobId: row.job_id ?? null, stage: stageOf(row), completeness: row.completeness ?? null, grade: row.grade ?? null, ...riskOf(row),
    processId: row.process_id ?? null, transactionId: row.transaction_id ?? null } };
}

/** GET /api/notifications. 최근 순번 → 항목 키 내림차순 키셋. view=unread면 미확인만. */
export async function readNotifications(db: Queryable, actorId: string, settings: NotificationSettings,
  { view, limit, cursor, windowEvents = NOTIFICATION_WINDOW_EVENTS }: { view: NotificationView; limit: number; cursor: NotificationCursor | null; windowEvents?: number },
  now = () => new Date()) {
  const { rows: [row] } = await db.query(`${STATE_CTE}, filtered AS (
      SELECT * FROM state WHERE $7::text='all' OR unread
    ), page AS (
      SELECT * FROM filtered WHERE $8::bigint IS NULL OR (notify_seq,item_key COLLATE "C")<($8::bigint,$9::text COLLATE "C")
      ORDER BY notify_seq DESC,item_key COLLATE "C" DESC LIMIT $10
    ), shown AS (
      SELECT p.*,p.notify_seq::text AS seq_text,${ISO('p.notified_at')} AS notified_iso,${ISO('ri.opened_at')} AS opened_at,ri.group_key,dp.process_id,dp.transaction_id
      FROM page p LEFT JOIN run_incidents ri ON ri.id=p.incident_id
      LEFT JOIN run_process_decisions pd ON pd.id=p.decision_id
      LEFT JOIN run_jobs pj ON p.decision_id IS NULL AND pj.id=p.job_id
      LEFT JOIN run_input_manifests pm ON pm.id=pj.input_manifest_id
      LEFT JOIN data_processes dp ON dp.observation_id=coalesce(pd.process_observation_id,pm.process_observation_id)
    )
    SELECT (SELECT top::text FROM top) AS top,
      (SELECT read_through::text FROM ops_user_notification_cursors WHERE actor_id=$5::text) AS read_through,
      (SELECT count(*)::integer FROM state WHERE unread) AS unread,
      (SELECT count(*)::integer FROM filtered) AS total,
      (SELECT coalesce(jsonb_agg(to_jsonb(s)-'notified_at'||jsonb_build_object('notified_at',s.notified_iso)
        ORDER BY s.notify_seq DESC,s.item_key COLLATE "C" DESC),'[]'::jsonb) FROM shown s) AS items`,
  [...baseParams(actorId, settings, windowEvents), view, cursor?.seq ?? null, cursor?.key ?? null, limit + 1]);
  const rows = row.items as Row[];
  const page = rows.slice(0, limit);
  const last = page.at(-1);
  const top = BigInt(row.top as string);
  const fromSeq = top > BigInt(windowEvents) ? top - BigInt(windowEvents) + 1n : 1n;
  return {
    observedAt: now().toISOString(),
    latestSeq: row.top as string,
    readThrough: (row.read_through as string | null) ?? '0',
    window: { events: windowEvents, fromSeq: String(fromSeq), truncated: fromSeq > 1n },
    settings,
    unreadCount: row.unread as number,
    total: row.total as number,
    nextCursor: rows.length > limit && last ? encodeCursor({ seq: last.seq_text as string, key: last.item_key as string }) : null,
    items: page.map(itemOf),
  };
}

/** GET /api/notifications/unread-count: 배지용. 목록과 같은 계산의 미확인 항목 수. */
export async function readUnreadCount(db: Queryable, actorId: string, settings: NotificationSettings, windowEvents = NOTIFICATION_WINDOW_EVENTS) {
  const { rows: [row] } = await db.query(`${STATE_CTE}
    SELECT (SELECT top::text FROM top) AS top,(SELECT count(*)::integer FROM state WHERE unread) AS unread`, baseParams(actorId, settings, windowEvents));
  return { latestSeq: row.top as string, unreadCount: row.unread as number };
}

export type ReadRequest = { through: string } | { items: { key: string; seq: string }[] };

/**
 * POST /api/notifications/read. 순번은 현재 최대 이벤트 id로 줄여 저장하고 줄이지 않는다(GREATEST).
 * - through: 모두 읽음(그 순번 이하 항목 전부). 항목 읽음 행 중 그 이하인 것은 지운다.
 * - items: 항목별 읽음(그 항목의 순번까지). 다시 알리면(순번이 커지면) 다시 미확인.
 * 어느 쪽이든 조회 범위 밖(최대 id − 범위 이하)으로 밀려난 항목 읽음 행을 지운다(사용자당 행 수 = 최근 알림 수 이하).
 */
export async function markNotificationsRead(pool: Pool, actorId: string, request: ReadRequest, windowEvents = NOTIFICATION_WINDOW_EVENTS) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows: [{ top }] } = await client.query('SELECT coalesce(max(id),0)::text AS top FROM run_decision_events');
    if ('through' in request) {
      await client.query(`INSERT INTO ops_user_notification_cursors(actor_id,read_through) VALUES($1,least($2::bigint,$3::bigint))
        ON CONFLICT (actor_id) DO UPDATE SET read_through=GREATEST(ops_user_notification_cursors.read_through,EXCLUDED.read_through),updated_at=clock_timestamp()`,
      [actorId, request.through, top]);
    } else {
      for (const item of request.items) {
        await client.query(`INSERT INTO ops_user_notification_reads(actor_id,item_key,read_seq) VALUES($1,$2,least($3::bigint,$4::bigint))
          ON CONFLICT (actor_id,item_key) DO UPDATE SET read_seq=GREATEST(ops_user_notification_reads.read_seq,EXCLUDED.read_seq),read_at=clock_timestamp()`,
        [actorId, item.key, item.seq, top]);
      }
    }
    await client.query(`DELETE FROM ops_user_notification_reads WHERE actor_id=$1 AND (read_seq<=$2::bigint-$3::bigint
      OR read_seq<=(SELECT read_through FROM ops_user_notification_cursors WHERE actor_id=$1))`, [actorId, top, windowEvents]);
    const { rows: [cursor] } = await client.query('SELECT read_through::text AS n FROM ops_user_notification_cursors WHERE actor_id=$1', [actorId]);
    await client.query('COMMIT');
    return { readThrough: (cursor?.n as string | undefined) ?? '0' };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}
