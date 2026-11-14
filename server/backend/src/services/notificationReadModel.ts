import type { Pool, PoolClient } from 'pg';
import { parseGroupKey } from './incidentReadModel.js';
import { NOTIFICATION_WINDOW_DAYS, RISK_RANK, type NotificationSettings } from './userSettings.js';

/**
 * 알림 목록·미확인 수·읽음 표시(contracts/notifications.md). 알림 항목은 저장하지 않고 읽을 때 만든다(사용자 11/13 "사건 단위 + 묶이지 않은 판정").
 *
 * 알림 시각(순번) = run_decision_events.id. 006·012 트리거가 advisory lock(81736, 0) 아래에서 붙이는 identity라 id 순서 = 커밋 순서다.
 * 조회 범위: 최근 NOTIFICATION_WINDOW_EVENTS개 이벤트(id > 최대 id − 범위, PK 범위 읽기) 중 사용자 알림 조회 기간(windowDays일, 026) 안의 이벤트.
 *   기간은 순번 경계로 바꿔 쓴다: 범위 시작 lo = max(최대 id − 범위, 개수 범위 안에서 지금 − 기간보다 이른 마지막 이벤트 id) → id > lo.
 *   created_at은 순번을 붙이는 같은 advisory lock 아래 clock_timestamp()라 순번 순서와 같다(경계 = 행마다 시각 비교와 같은 결과).
 *   개수 상한(5,000)은 요청 비용 상한으로 그대로 둔다(기간 경계 계산도 이 범위 안에서만 PK 역순으로 읽는다 — 새 색인 없음).
 *   범위 밖으로 밀려난 항목은 패널에서 사라진다.
 *
 * 1) 이벤트 판단(사용자 알림 설정, Detector 위험도 틀 Q-N1·Q-N1-1 ②): 이상 ∧ 판정 기준 충족 ∧ [위험도 ≥ 하한 ∨ (미정 포함 ∧ 단계 없는 근거 있음)]
 *    - 이상: 최종 = status<>unavailable ∧ evidence.anomalous=true(analysis-ui 이상 신호와 같음), 중간 기록 first_anomaly·evidence_added = 이상
 *    - 판정 기준: interim = 중간 기록·최종 모두, final_any = 최종만, final_complete = 최종 ∧ status=complete
 *    - 위험도: 저장된 v4 composite.risk만(재계산 없음, D7). v3·형식 오류 = 위험도 미정. 단계 없는 근거 = 미정이거나 unratedMethods가 비어 있지 않음
 * 2) 묶이지 않은 판정 항목(key job:<detect 작업> 또는 작업 없는 판정 decision:<판정>): 그 작업의 최종 판정이 사건 구성원이 아닌 것만.
 *    알림(순번 갱신) = 처음 통과(opened), 앞선 통과보다 위험도 상승(risk_raised), 앞서 통과한 뒤 최종 없음(no_final)·이상 아닌 최종(withdrawn).
 *    근거 추가·최종 도착만으로는 순번을 바꾸지 않는다(D8: 같은 알림 내용만 갱신).
 * 3) 사건 항목(key incident:<흐름 첫 사건>): 통과한 구성원 판정(최종)마다 알림 — 첫 구성원 opened, 위험도 상승 risk_raised, 그 밖 grew.
 *    흐름 = 구성원 사건에서 병합(양방향)·이어짐(양방향)으로 닿는 사건 전부(incidentReadModel resolveFlow와 같은 걸음, 고리는 UNION으로 끝남).
 *    흐름 id = 닿은 사건 중 (opened_at, 병합 뿌리 id)가 가장 작은 사건의 병합 뿌리 = buildIncidentFlows의 첫 구간 id(사건 보기 화면 흐름 id와 같음).
 *    병합 뿌리 = merged_into를 끝까지 따라간 사건(고리면 자기 자신). 어긋난 연결(incidents-ui flags)은 거르지 않는다(그런 흐름은 화면 id와 다를 수 있음).
 * 4) 읽음: 항목 순번 > max(모두 읽음 순번, 그 항목 읽음 순번)이면 미확인. 묶인 판정의 시각은 판정 이벤트 순번이므로 "모두 읽음" 뒤에 묶여도 다시 미확인이 되지 않는다.
 * 5) 프로토콜 항목(key protocol:<sha256(interfaceId, pathKey)>, contracts/protocol-settings-ui.md 5절): 범위 안 최종 판정의 evidence.protocolReview
 *    (Detector protocol-rules.md 5.3, T일 때만 있음)에서 일치하지 않은 경로마다 묶는다(경로를 만들 수 없는 미등록 인터페이스는 pathKey '' = 인터페이스 단위).
 *    관리자 목록·배지에만 있다(명령이 관리자 전용 — 일반 사용자 목록·배지·읽음 요청에서는 빠짐).
 *    알림은 범위 안에서 처음 나온 판정 1번(first_seen) — 같은 묶음이 다시 나와도 순번을 바꾸지 않는다(판정 수만 늘어남). 사용자 알림 설정(판정 기준·위험도)과 무관.
 *    처리 전 묶음은 첫 판정이 범위 밖으로 밀리면 범위 안 가장 이른 판정이 새 순번이 된다 → 아직 처리하지 않은 경로가 계속 나오면 다시 알린다(의도).
 *    [이상 유지](023 ops_protocol_alert_keeps) 뒤에는 순번이 누른 시점 순번을 넘지 않고, 그 순번이 범위 밖이면 읽음으로 본다(읽음 행이 범위 정리로 지워져도)
 *    → 같은 묶음을 다시 알리지 않는다(목록에는 남김).
 *    항목 읽음은 023 ops_user_protocol_alert_reads(022 항목 읽음 표의 키 형식이 UUID로 고정이라 따로 둔다).
 * 6) 작업 항목(key work:<run_jobs id>, 028, contracts/notifications.md "작업 알림" 절): v03 training 큐의 train·compare 작업 중 succeeded·failed로 끝난 것
 *    (대기·실행 중·재시도 대기·blocked 제외). 관리자 ∧ 설정 includeJobs일 때만. 조회 기간 = 종료 시각(finished_at)이 지금 − windowDays일 이후(이벤트 수 상한과 무관).
 *    작업 종료는 판정 이벤트 순번을 받지 않으므로 읽음은 종료 시각으로 판단한다: 미확인 = finished_at > max(모두 읽음 작업 경계 jobs_read_through, 항목 읽음 read_through).
 *    목록 정렬 순번(seq) = 종료 시각 이전 마지막 판정 이벤트 순번(개수 범위 안, 없으면 0 — 범위가 밀려도 같은 값이라 커서가 흔들리지 않음)
 *    — 정렬에만 쓰고 읽음 판단에는 쓰지 않는다. 범위 첫 이벤트보다 먼저 끝난 작업은 이벤트를 읽지 않고 바로 0(created_at으로 미리 거름).
 *    "모두 읽음"은 판정 순번(through)과 함께 응답의 latestJobAt(작업 알림 대상 중 가장 늦은 종료 시각)을 jobsThrough로 받아 그 시각까지 읽음으로 둔다.
 */
type Queryable = Pick<Pool, 'query'>;

export const NOTIFICATION_WINDOW_EVENTS = 5000;
export const NOTIFICATION_LIST_LIMITS = { default: 20, max: 50 } as const;
export const NOTIFICATION_READ_ITEMS_MAX = 50;
export const NOTIFICATION_VIEWS = ['unread', 'all'] as const;
export type NotificationView = typeof NOTIFICATION_VIEWS[number];
export type NotificationCursor = { seq: string; key: string };

const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
const RATED = (risk: string) => `coalesce(${risk}->>'status'='rated' AND ${risk}->>'level' IN ('critical','warning','info'),false)`;

/** 프로토콜 알림 항목 키(023 표 CHECK와 같은 형식). */
const PROTOCOL_KEY_SQL = (interfaceId: string, pathKey: string) =>
  `'protocol:'||encode(sha256(convert_to(jsonb_build_array(${interfaceId},${pathKey})::text,'UTF8')),'hex')`;
/**
 * 알림 목록에 오르는 protocolReview 판별(판정 단위): v1 객체, 종류 2가지, interfaceId 비지 않은 문자열. `review`는 jsonb SQL 식이다.
 * 프로토콜 경로 설명 접수(routes/protocolExplanationContract.ts, protocol-explanation-job.md 10절)도 이 식을 그대로 쓴다.
 * 값이 없으면(NULL) 결과도 NULL일 수 있다 — 조건으로 쓰면 거짓과 같다.
 */
export const protocolReviewListedSql = (review: string) => `(jsonb_typeof(${review})='object' AND ${review}->'version'='1'::jsonb
    AND ${review}->>'kind' IN ('path_unmatched','interface_unregistered')
    AND jsonb_typeof(${review}->'interfaceId')='string' AND ${review}->>'interfaceId'<>'')`;
/**
 * 프로토콜 알림 원천 CTE(top 다음 — top.lo = 범위 시작 순번(이 순번 초과가 범위)). pkeyed: 일치하지 않은 경로마다 1행(seq, decision_id, 종류, 인터페이스, pathKey, channels…, item_key).
 * protocolReview 형식이 다르면(버전·종류·인터페이스·경로 모양) 그 판정·경로는 알림을 만들지 않는다(Detector 계약 밖 값은 해석하지 않음).
 */
const protocolCte = (included: boolean) => `pev AS (
  SELECT e.id AS seq,e.created_at,d.id AS decision_id,d.evidence->'protocolReview' AS review
  FROM top JOIN run_decision_events e ON e.id>top.lo AND e.event_kind='final'
  JOIN run_process_decisions d ON d.id=e.decision_id
  WHERE ${included ? 'true' : 'false'} AND ${protocolReviewListedSql(`d.evidence->'protocolReview'`)}
), proute AS (
  SELECT pev.seq,pev.created_at,pev.decision_id,pev.review->>'kind' AS pkind,pev.review->>'interfaceId' AS interface_id,
    coalesce(rt->>'pathKey','') AS path_key,rt->'channels' AS channels,
    CASE WHEN jsonb_typeof(pev.review->'routesReason')='string' THEN pev.review->>'routesReason' END AS routes_reason,
    CASE WHEN jsonb_typeof(pev.review->'protocolRuleId')='string' THEN pev.review->>'protocolRuleId' END AS protocol_rule_id
  FROM pev CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(pev.review->'routes')='array' AND jsonb_array_length(pev.review->'routes')>0
    THEN pev.review->'routes' WHEN pev.review->>'kind'='interface_unregistered' THEN '[{"matched":false}]'::jsonb ELSE '[]'::jsonb END) rt
  WHERE jsonb_typeof(rt)='object' AND rt->'matched'='false'::jsonb
    AND ((rt->'pathKey' IS NULL AND rt->'channels' IS NULL) OR (jsonb_typeof(rt->'pathKey')='string' AND rt->>'pathKey'<>''
      AND jsonb_typeof(rt->'channels')='array' AND jsonb_array_length(rt->'channels')>0))
), pkeyed AS (
  SELECT proute.*,${PROTOCOL_KEY_SQL('proute.interface_id', 'proute.path_key')} AS item_key FROM proute
)`;

/**
 * 범위 CTE `top`: top = 최대 이벤트 id, lo = 범위 시작(id > lo가 범위), by_days = 기간 경계가 개수 경계보다 좁음.
 * $1 범위 이벤트 수, $6 조회 기간(일). 기간 경계는 개수 범위 안만 PK 역순으로 읽는다(최대 $1행, created_at 색인 불필요).
 */
const windowCte = `top AS (
  SELECT t.top,greatest(t.top-$1::bigint,coalesce(b.old,t.top-$1::bigint)) AS lo,b.old IS NOT NULL AS by_days
  FROM (SELECT coalesce(max(id),0) AS top FROM run_decision_events) t
  CROSS JOIN LATERAL (SELECT max(e.id) AS old FROM run_decision_events e
    WHERE e.id>t.top-$1::bigint AND e.created_at<now()-make_interval(days=>$6::integer)) b
)`;

/**
 * 공통 CTE. 인자: $1 범위 이벤트 수, $2 판정 기준, $3 하한 순위(1~3), $4 미정 포함, $5 actor_id, $6 조회 기간(일).
 * protocol = 프로토콜 항목 포함(관리자만, 서버 값이라 SQL 문자열에 넣음).
 * 결과 CTE `state`: kind, item_key, notify_seq, notified_at, reason, unread + 화면 값 열(+ 프로토콜 항목 extra).
 */
const stateCte = (protocol: boolean, jobs = false) => `WITH RECURSIVE ${windowCte}, ${protocolCte(protocol)}, ${jobCte(jobs)}, ev AS (
  SELECT e.id AS seq,e.event_kind AS kind,e.created_at,d.id AS decision_id,coalesce(d.job_id,i.job_id) AS job_id,d.status::text AS status,
    CASE WHEN e.event_kind='final' THEN d.evidence WHEN e.event_kind IN ('first_anomaly','evidence_added') THEN i.payload END AS snap
  FROM top JOIN run_decision_events e ON e.id>top.lo
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
    -- 최종 판정의 이상 신호(analysis-ui signal): yes 이상 / no 이상 신호 없음(complete ∧ anomalous=false) / unknown 정상 여부 미확인.
    CASE WHEN x.kind<>'final' THEN NULL WHEN x.status<>'unavailable' AND x.anomalous='true'::jsonb THEN 'yes'
      WHEN x.status='complete' AND x.anomalous='false'::jsonb THEN 'no' ELSE 'unknown' END AS final_signal,
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
    (array_agg(d.final_signal ORDER BY d.seq DESC) FILTER (WHERE d.kind='final'))[1] AS final_signal,
    (array_agg(d.grade ORDER BY d.seq DESC) FILTER (WHERE d.anomaly))[1] AS grade,
    (array_agg(d.risk_level ORDER BY d.seq DESC) FILTER (WHERE d.anomaly))[1] AS risk_level,
    coalesce((array_agg(d.has_unrated ORDER BY d.seq DESC) FILTER (WHERE d.anomaly))[1],false) AS has_unrated,
    NULL::uuid AS incident_id,NULL::integer AS members,NULL::jsonb AS extra
  FROM dlast l JOIN dev d ON d.item_key=l.item_key GROUP BY l.item_key,l.notify_seq,l.notified_at,l.reason
), up(start_id,id) AS (
  SELECT DISTINCT incident_id,incident_id FROM mem
  UNION
  SELECT up.start_id,n.id FROM up CROSS JOIN LATERAL (
    SELECT e.target_incident_id FROM run_incident_events e WHERE e.incident_id=up.id AND e.kind='merged_into'
    UNION ALL SELECT e.incident_id FROM run_incident_events e WHERE e.target_incident_id=up.id AND e.kind='merged_into'
    UNION ALL SELECT i.continues_from FROM run_incidents i WHERE i.id=up.id AND i.continues_from IS NOT NULL
    UNION ALL SELECT s.id FROM run_incidents s WHERE s.continues_from=up.id
  ) n(id)
), mroot(id,at) AS (
  -- 닿은 사건마다 merged_into를 끝까지 따라간다(고리는 UNION으로 끝나고 끝 사건이 없으면 자기 자신을 뿌리로 쓴다).
  SELECT DISTINCT id,id FROM up
  UNION
  SELECT m.id,e.target_incident_id FROM mroot m JOIN run_incident_events e ON e.incident_id=m.at AND e.kind='merged_into'
), roots AS (
  SELECT u.id,coalesce((SELECT m.at FROM mroot m WHERE m.id=u.id
    AND NOT EXISTS (SELECT 1 FROM run_incident_events e WHERE e.incident_id=m.at AND e.kind='merged_into') LIMIT 1),u.id) AS root
  FROM (SELECT DISTINCT id FROM up) u
), head AS (
  SELECT DISTINCT ON (up.start_id) up.start_id,r.root AS head_id FROM up JOIN run_incidents i ON i.id=up.id JOIN roots r ON r.id=up.id
  ORDER BY up.start_id,i.opened_at,r.root
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
    NULL::text AS last_event,NULL::uuid AS job_id,NULL::uuid AS decision_id,NULL::text AS completeness,NULL::text AS final_signal,NULL::text AS grade,
    CASE max(rank) WHEN 3 THEN 'critical' WHEN 2 THEN 'warning' WHEN 1 THEN 'info' END AS risk_level,bool_or(has_unrated) AS has_unrated,
    incident_id,count(*)::integer AS members,NULL::jsonb AS extra
  FROM iprior GROUP BY incident_id
), pitems AS (
  SELECT 'protocol'::text AS kind,p.item_key,
    CASE WHEN k.alert_key IS NULL THEN min(p.seq) ELSE least(min(p.seq),k.kept_through_seq) END AS notify_seq,
    (array_agg(p.created_at ORDER BY p.seq))[1] AS notified_at,'first_seen'::text AS reason,
    NULL::text AS last_event,NULL::uuid AS job_id,(array_agg(p.decision_id ORDER BY p.seq DESC))[1] AS decision_id,
    NULL::text AS completeness,NULL::text AS final_signal,NULL::text AS grade,NULL::text AS risk_level,false AS has_unrated,
    NULL::uuid AS incident_id,count(DISTINCT p.decision_id)::integer AS members,
    jsonb_build_object('kind',(array_agg(p.pkind ORDER BY p.seq DESC))[1],'interfaceId',(array_agg(p.interface_id))[1],
      'pathKey',nullif((array_agg(p.path_key))[1],''),'channels',(array_agg(p.channels ORDER BY p.seq DESC))[1],
      'routesReason',(array_agg(p.routes_reason ORDER BY p.seq DESC))[1],'protocolRuleId',(array_agg(p.protocol_rule_id ORDER BY p.seq DESC))[1],
      'keptAt',${ISO('k.kept_at')},'keptBy',k.kept_by_actor_id) AS extra
  FROM pkeyed p LEFT JOIN ops_protocol_alert_keeps k ON k.alert_key=p.item_key
  GROUP BY p.item_key,k.alert_key,k.kept_through_seq,k.kept_at,k.kept_by_actor_id
), items AS (
  SELECT * FROM ditems UNION ALL SELECT * FROM iitems UNION ALL SELECT * FROM pitems UNION ALL SELECT * FROM jitems
), state AS (
  -- 이상 유지한 프로토콜 묶음: 순번(≤ 유지 순번)이 범위 밖이면 읽음(범위 정리로 읽음 행이 지워져도 다시 미확인이 되지 않는다).
  -- 작업 항목: 판정 순번이 아니라 종료 시각으로 판단(6).
  SELECT items.*,CASE WHEN items.kind='job'
      THEN items.notified_at>coalesce(greatest(c.jobs_read_through,jr.read_through),'-infinity'::timestamptz)
      ELSE items.notify_seq>greatest(coalesce(c.read_through,0),coalesce(rd.read_seq,0),coalesce(prd.read_seq,0))
        AND NOT (items.kind='protocol' AND items.extra->>'keptAt' IS NOT NULL AND items.notify_seq<=(SELECT lo FROM top)) END AS unread
  FROM items LEFT JOIN ops_user_notification_cursors c ON c.actor_id=$5::text
  LEFT JOIN ops_user_notification_reads rd ON rd.actor_id=$5::text AND rd.item_key=items.item_key
  LEFT JOIN ops_user_protocol_alert_reads prd ON prd.actor_id=$5::text AND prd.item_key=items.item_key
  LEFT JOIN ops_user_job_notification_reads jr ON items.kind='job' AND jr.actor_id=$5::text AND jr.job_id=(items.extra->>'id')::uuid
)`;

/**
 * 작업 알림 대상(6). jobs = 관리자 ∧ includeJobs(서버 값이라 SQL 문자열에 넣음). 016 run_jobs_v03_queue_idx(queue,status,…) 앞부분으로 training 큐만 읽는다.
 * `days` = 조회 기간(일) SQL 식, null이면 기간 없이(latestJobAt·모두 읽음 상한).
 */
const jobSourceWhere = (jobs: boolean, days: string | null) => `${jobs ? 'true' : 'false'} AND j.contract_version='v03' AND j.queue='training'
    AND j.status IN ('succeeded','failed') AND j.kind IN ('train','compare') AND j.finished_at IS NOT NULL
    ${days ? `AND j.finished_at>=now()-make_interval(days=>${days})` : ''}`;
/**
 * 작업 항목 CTE(stateCte 안, top 다음). 결과 열은 다른 항목 CTE와 같다(UNION ALL). 작업 값은 extra에:
 * id·kind·status·errorCode·versionId(학습 결과 모델 버전)·failedCount(비교 평가 실패 항목 수)·recommended(비교 평가 교체 추천 대상이 있음 — 저장된 replacementRecommendations[].targetVersionId).
 */
const jobCte = (jobs: boolean) => `jsrc AS (
  SELECT j.id,j.kind::text AS jkind,j.status::text AS jstatus,j.error_code,j.finished_at,
    coalesce(j.result_config_version_id::text,CASE WHEN jsonb_typeof(j.result_summary)='object' AND jsonb_typeof(j.result_summary->'configVersionId')='string'
      THEN j.result_summary->>'configVersionId' END) AS version_id,
    CASE WHEN jsonb_typeof(j.result_summary->'failedCount')='number' THEN j.result_summary->'failedCount' END AS failed_count,
    coalesce(jsonb_path_exists(j.result_summary,'strict $.replacementRecommendations[*] ? (@.targetVersionId.type() == "string")','{}',true),false) AS recommended
  FROM run_jobs j WHERE ${jobSourceWhere(jobs, '$6::integer')}
), jfirst AS (
  -- 개수 범위 첫 이벤트 시각(PK 범위 첫 행 1개). 이보다 먼저 끝난 작업은 범위 안에 앞선 이벤트가 없다.
  SELECT (SELECT e.created_at FROM run_decision_events e WHERE e.id>top.top-$1::bigint ORDER BY e.id LIMIT 1) AS at FROM top
), jitems AS (
  SELECT 'job'::text AS kind,'work:'||js.id::text AS item_key,
    CASE WHEN jfirst.at IS NULL OR js.finished_at<jfirst.at THEN 0::bigint
      ELSE coalesce((SELECT e.id FROM run_decision_events e WHERE e.id>top.top-$1::bigint AND e.created_at<=js.finished_at ORDER BY e.id DESC LIMIT 1),0) END AS notify_seq,
    js.finished_at AS notified_at,'finished'::text AS reason,
    NULL::text AS last_event,NULL::uuid AS job_id,NULL::uuid AS decision_id,NULL::text AS completeness,NULL::text AS final_signal,NULL::text AS grade,
    NULL::text AS risk_level,false AS has_unrated,NULL::uuid AS incident_id,NULL::integer AS members,
    jsonb_build_object('id',js.id,'kind',js.jkind,'status',js.jstatus,'errorCode',js.error_code,'versionId',js.version_id,
      'failedCount',js.failed_count,'recommended',js.recommended) AS extra
  FROM jsrc js CROSS JOIN top CROSS JOIN jfirst
)`;

const baseParams = (actorId: string, settings: NotificationSettings, windowEvents: number) =>
  [windowEvents, settings.decisionBasis, RISK_RANK[settings.minRiskLevel], settings.includeUnrated, actorId, settings.windowDays];

const encodeCursor = (value: NotificationCursor) => Buffer.from(JSON.stringify([value.seq, value.key]), 'utf8').toString('base64url');
const ITEM_KEY = /^((incident|job|decision|work):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|protocol:[0-9a-f]{64})$/;
const PROTOCOL_ITEM = /^protocol:[0-9a-f]{64}$/;
const JOB_ITEM = /^work:/;
/** 작업 알림 시각(latestJobAt·jobsThrough): UTC 마이크로초 6자리 고정 형식(서버가 응답에 낸 값 그대로). */
const JOB_AT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;
export const isJobAt = (value: unknown): value is string => typeof value === 'string' && JOB_AT.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 23) === value.slice(0, 23);
/** 작업 알림 대상 중 가장 늦은 종료 시각(기간 무관). 작업 알림이 없거나 꺼졌으면 NULL. */
const latestJobAtSql = (jobs: boolean) => `(SELECT ${ISO('max(j.finished_at)')} FROM run_jobs j WHERE ${jobSourceWhere(jobs, null)})`;
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
/**
 * 판정 항목 현재 단계. 최종 판정이 있으면 최종(철회면 cleared = 이상이 아닌 최종) — 재시도 대체 때 012 트리거가 새 시도 최종 뒤에
 * 이전 시도 superseded를 기록하므로 마지막 이벤트 종류로 정하지 않는다. 최종이 없으면 마지막 이벤트: 중간 판정·근거 추가·대체됨(재처리 중)·최종 없음.
 */
const stageOf = (row: Row) => row.decision_id
  ? (row.reason === 'withdrawn' ? 'cleared' : 'final')
  : row.reason === 'withdrawn' ? 'no_final'
  : (({ first_anomaly: 'interim', evidence_added: 'updated', superseded: 'superseded', no_final: 'no_final' } as Record<string, string>)[row.last_event] ?? 'interim');

/** 현재 룰 세트 프로토콜 항목(인터페이스 → 허용 경로 목록). 프로토콜 알림의 "추가됨" 판단에만 쓴다(읽기 1회). */
type ProtocolEntries = Map<string, string[][]>;
async function currentProtocolEntries(db: Queryable): Promise<ProtocolEntries> {
  const { rows: [row] } = await db.query(`SELECT snapshot->'protocolRules' AS rules FROM ops_rule_set_revisions ORDER BY revision DESC LIMIT 1`);
  const entries: ProtocolEntries = new Map();
  for (const rule of Array.isArray(row?.rules) ? row.rules : []) {
    if (typeof rule?.interfaceId !== 'string' || !Array.isArray(rule.paths)) continue;
    entries.set(rule.interfaceId, rule.paths.map((path: { channels?: unknown }) => (Array.isArray(path?.channels) ? path.channels as string[] : [])));
  }
  return entries;
}
const sameChannels = (left: readonly unknown[], right: readonly unknown[]) => left.length === right.length && left.every((value, index) => value === right[index]);
/**
 * 프로토콜 항목 처리 상태: kept([이상 유지]) / added(현재 룰 세트에 그 경로 — 경로 없는 미등록 알림은 그 인터페이스 항목 — 가 있음, 옛 알림 "추가됨") / null(처리 전).
 * Detector는 옛 판정을 고치지 않으므로(protocol-rules.md 5.3) 추가됨은 화면이 현재 룰 세트와 대조해 보인다.
 */
function protocolOf(row: Row, entries: ProtocolEntries) {
  const extra = (row.extra ?? {}) as Record<string, unknown>;
  const interfaceId = extra.interfaceId as string;
  const channels = Array.isArray(extra.channels) && extra.channels.every((value) => typeof value === 'string') ? extra.channels as string[] : null;
  const allowed = entries.get(interfaceId);
  const added = allowed !== undefined && (channels === null ? true : allowed.some((path) => sameChannels(path, channels)));
  return {
    kind: extra.kind as 'path_unmatched' | 'interface_unregistered', interfaceId, pathKey: (extra.pathKey as string | null) ?? null, channels,
    routesReason: (extra.routesReason as string | null) ?? null, protocolRuleId: (extra.protocolRuleId as string | null) ?? null,
    decisionId: row.decision_id as string, decisions: row.members as number,
    resolution: extra.keptAt ? 'kept' as const : added ? 'added' as const : null,
    keptAt: (extra.keptAt as string | null) ?? null, keptBy: (extra.keptBy as string | null) ?? null,
    processId: row.process_id ?? null, transactionId: row.transaction_id ?? null,
  };
}

/** 작업 항목(6). 문구에 필요한 값만: 종류·상태·오류 코드·종료 시각, 학습 결과 버전, 비교 평가 실패 항목 수·교체 추천 대상 있음. */
function jobOf(row: Row, finishedAt: string) {
  const extra = (row.extra ?? {}) as Record<string, unknown>;
  return {
    id: extra.id as string, kind: extra.kind as 'train' | 'compare', status: extra.status as 'succeeded' | 'failed',
    errorCode: (extra.errorCode as string | null) ?? null, finishedAt,
    configVersionId: extra.kind === 'train' ? (extra.versionId as string | null) ?? null : null,
    failedCount: extra.kind === 'compare' && typeof extra.failedCount === 'number' ? extra.failedCount : null,
    replacementRecommended: extra.kind === 'compare' && extra.status === 'succeeded' && extra.recommended === true,
  };
}

function itemOf(row: Row, entries: ProtocolEntries) {
  const base = { key: row.item_key as string, seq: row.seq_text as string, reason: row.reason as string, notifiedAt: row.notified_at as string, unread: Boolean(row.unread) };
  if (row.kind === 'protocol') return { ...base, type: 'protocol' as const, protocol: protocolOf(row, entries) };
  if (row.kind === 'job') return { ...base, type: 'job' as const, job: jobOf(row, base.notifiedAt) };
  if (row.kind === 'incident') {
    const { kind, value, valueHash } = parseGroupKey(row.group_key ?? '');
    return { ...base, type: 'incident' as const, incident: { id: row.incident_id as string, openedAt: row.opened_at as string | null,
      criteria: { kind, value, longValue: valueHash !== null }, memberDecisions: row.members as number, ...riskOf(row) } };
  }
  return { ...base, type: 'decision' as const, decision: { detailId: (row.decision_id ?? row.job_id) as string, decisionId: row.decision_id ?? null,
    jobId: row.job_id ?? null, stage: stageOf(row), completeness: row.completeness ?? null, finalSignal: row.final_signal ?? null, grade: row.grade ?? null, ...riskOf(row),
    processId: row.process_id ?? null, transactionId: row.transaction_id ?? null } };
}

/** GET /api/notifications. 최근 순번 → 항목 키 내림차순 키셋. view=unread면 미확인만. */
export async function readNotifications(db: Queryable, actorId: string, settings: NotificationSettings,
  { view, limit, cursor, windowEvents = NOTIFICATION_WINDOW_EVENTS, protocol = false, jobs = false }:
    { view: NotificationView; limit: number; cursor: NotificationCursor | null; windowEvents?: number; protocol?: boolean; jobs?: boolean },
  now = () => new Date()) {
  const { rows: [row] } = await db.query(`${stateCte(protocol, jobs)}, filtered AS (
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
    SELECT (SELECT top::text FROM top) AS top,(SELECT lo::text FROM top) AS lo,(SELECT by_days FROM top) AS by_days,
      ${latestJobAtSql(jobs)} AS latest_job_at,
      (SELECT read_through::text FROM ops_user_notification_cursors WHERE actor_id=$5::text) AS read_through,
      (SELECT count(*)::integer FROM state WHERE unread) AS unread,
      (SELECT count(*)::integer FROM filtered) AS total,
      (SELECT coalesce(jsonb_agg(to_jsonb(s)-'notified_at'||jsonb_build_object('notified_at',s.notified_iso)
        ORDER BY s.notify_seq DESC,s.item_key COLLATE "C" DESC),'[]'::jsonb) FROM shown s) AS items`,
  [...baseParams(actorId, settings, windowEvents), view, cursor?.seq ?? null, cursor?.key ?? null, limit + 1]);
  const rows = row.items as Row[];
  const page = rows.slice(0, limit);
  const entries = page.some((item) => item.kind === 'protocol') ? await currentProtocolEntries(db) : new Map();
  const last = page.at(-1);
  const lo = BigInt(row.lo as string);
  const fromSeq = lo > 0n ? lo + 1n : 1n;
  const truncated = fromSeq > 1n;
  return {
    observedAt: now().toISOString(),
    latestSeq: row.top as string,
    latestJobAt: (row.latest_job_at as string | null) ?? null,
    readThrough: (row.read_through as string | null) ?? '0',
    // truncatedBy: 범위를 정한 쪽 — days(조회 기간보다 이른 이벤트가 있음) / events(기간 안 이벤트가 개수 상한을 넘음) / null(잘림 없음).
    window: { events: windowEvents, days: settings.windowDays, fromSeq: String(fromSeq), truncated,
      truncatedBy: !truncated ? null : row.by_days ? 'days' as const : 'events' as const },
    settings,
    unreadCount: row.unread as number,
    total: row.total as number,
    nextCursor: rows.length > limit && last ? encodeCursor({ seq: last.seq_text as string, key: last.item_key as string }) : null,
    items: page.map((item) => itemOf(item, entries)),
  };
}

/**
 * GET /api/notifications/unread-count: 배지용. 목록과 같은 계산의 미확인 항목 수와 종류별 수(unreadByType, 합 = unreadCount).
 * 종류별 수는 상단 메뉴 배지(이상 분석 = decision + incident, 탐지 설정 = job)가 쓴다. 프로토콜·작업 항목이 없는 일반 사용자는 protocol·job 0.
 */
export async function readUnreadCount(db: Queryable, actorId: string, settings: NotificationSettings, windowEvents = NOTIFICATION_WINDOW_EVENTS, protocol = false,
  jobs = false) {
  const { rows: [row] } = await db.query(`${stateCte(protocol, jobs)}
    SELECT (SELECT top::text FROM top) AS top,${latestJobAtSql(jobs)} AS latest_job_at,c.*
    FROM (SELECT count(*)::integer AS unread,count(*) FILTER (WHERE kind='decision')::integer AS decision,
      count(*) FILTER (WHERE kind='incident')::integer AS incident,count(*) FILTER (WHERE kind='protocol')::integer AS protocol,
      count(*) FILTER (WHERE kind='job')::integer AS job
      FROM state WHERE unread) c`, baseParams(actorId, settings, windowEvents));
  return { latestSeq: row.top as string, latestJobAt: (row.latest_job_at as string | null) ?? null, unreadCount: row.unread as number,
    unreadByType: { decision: row.decision as number, incident: row.incident as number, protocol: row.protocol as number, job: row.job as number } };
}

/** through = 판정 순번 모두 읽음, jobsThrough = 작업 알림 모두 읽음 경계(응답 latestJobAt 그대로, 생략 가능). */
export type ReadRequest = { through: string; jobsThrough?: string } | { items: { key: string; seq: string }[] };

/**
 * POST /api/notifications/read. 순번은 현재 최대 이벤트 id로 줄여 저장하고 줄이지 않는다(GREATEST).
 * - through: 모두 읽음(그 순번 이하 항목 전부). 항목 읽음 행 중 그 이하인 것은 지운다.
 * - items: 항목별 읽음(그 항목의 순번까지). 다시 알리면(순번이 커지면) 다시 미확인. 요청자 설정 기준 지금 조회 범위에 있는 항목 키만 저장하고
 *   (없는 키는 무시 → 사용자당 행 수는 범위 안 항목 수 이하), 순번은 그 항목의 현재 알림 순번 이하로 줄인다.
 * 어느 쪽이든 개수 범위 밖(최대 id − 범위 이하)으로 밀려난 항목 읽음 행을 지운다(사용자당 행 수 = 최근 알림 수 이하).
 * 조회 기간(windowDays)으로는 지우지 않는다: 기간을 늘리면 다시 보이는 항목이 읽음을 잃지 않게(행 수 상한은 개수 범위로 충분).
 * 작업 항목(6): 모두 읽음은 jobsThrough(작업 알림 대상 중 가장 늦은 종료 시각 이하로 줄임, 줄이지 않음), 항목 읽음은 그 작업의 현재 종료 시각(요청 seq는 형식만 본다).
 *   작업 읽음 행은 모두 읽음 경계 이하이거나 최대 조회 기간(30일)보다 오래된 것을 지운다(기간을 늘려도 다시 보일 수 없는 행만).
 */
export async function markNotificationsRead(pool: Pool, actorId: string, settings: NotificationSettings, request: ReadRequest,
  windowEvents = NOTIFICATION_WINDOW_EVENTS, protocol = false, jobs = false) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows: [{ top }] } = await client.query('SELECT coalesce(max(id),0)::text AS top FROM run_decision_events');
    if ('through' in request) {
      // jobsThrough 생략($4 NULL)이면 작업 경계는 그대로(GREATEST는 NULL을 무시). 상한 = 지금 작업 알림 대상 중 가장 늦은 종료 시각.
      await client.query(`INSERT INTO ops_user_notification_cursors(actor_id,read_through,jobs_read_through)
        VALUES($1,least($2::bigint,$3::bigint),CASE WHEN $4::timestamptz IS NULL THEN NULL
          ELSE (SELECT CASE WHEN max(j.finished_at) IS NOT NULL THEN least($4::timestamptz,max(j.finished_at)) END
            FROM run_jobs j WHERE ${jobSourceWhere(jobs, null)}) END)
        ON CONFLICT (actor_id) DO UPDATE SET read_through=GREATEST(ops_user_notification_cursors.read_through,EXCLUDED.read_through),
          jobs_read_through=GREATEST(ops_user_notification_cursors.jobs_read_through,EXCLUDED.jobs_read_through),updated_at=clock_timestamp()`,
      [actorId, request.through, top, request.jobsThrough ?? null]);
    } else {
      const { rows: present } = await client.query(`${stateCte(protocol, jobs)} SELECT item_key,notify_seq::text AS seq,
          ${ISO('notified_at')} AS at,extra->>'id' AS job_id FROM state WHERE item_key=ANY($7::text[])`,
        [...baseParams(actorId, settings, windowEvents), request.items.map((item) => item.key)]);
      const current = new Map(present.map((row) => [row.item_key as string, row as Row]));
      for (const item of request.items) {
        const found = current.get(item.key);
        if (found === undefined) continue;
        if (JOB_ITEM.test(item.key)) {
          await client.query(`INSERT INTO ops_user_job_notification_reads(actor_id,job_id,read_through) VALUES($1,$2::uuid,$3::timestamptz)
            ON CONFLICT (actor_id,job_id) DO UPDATE SET read_through=GREATEST(ops_user_job_notification_reads.read_through,EXCLUDED.read_through),read_at=clock_timestamp()`,
          [actorId, found.job_id, found.at]);
          continue;
        }
        const seq = found.seq as string;
        // 프로토콜 항목은 023 표(022 표의 키 형식은 UUID만), 그 밖은 022 표. 저장 규칙은 같다.
        const table = PROTOCOL_ITEM.test(item.key) ? 'ops_user_protocol_alert_reads' : 'ops_user_notification_reads';
        await client.query(`INSERT INTO ${table}(actor_id,item_key,read_seq) VALUES($1,$2,least($3::bigint,$4::bigint))
          ON CONFLICT (actor_id,item_key) DO UPDATE SET read_seq=GREATEST(${table}.read_seq,EXCLUDED.read_seq),read_at=clock_timestamp()`,
        [actorId, item.key, item.seq, seq]);
      }
    }
    for (const table of ['ops_user_notification_reads', 'ops_user_protocol_alert_reads']) {
      await client.query(`DELETE FROM ${table} WHERE actor_id=$1 AND (read_seq<=$2::bigint-$3::bigint
        OR read_seq<=(SELECT read_through FROM ops_user_notification_cursors WHERE actor_id=$1))`, [actorId, top, windowEvents]);
    }
    await client.query(`DELETE FROM ops_user_job_notification_reads WHERE actor_id=$1 AND (read_through<now()-make_interval(days=>$2::integer)
      OR read_through<=(SELECT jobs_read_through FROM ops_user_notification_cursors WHERE actor_id=$1))`, [actorId, NOTIFICATION_WINDOW_DAYS.max]);
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

export class ProtocolAlertError extends Error {
  constructor(readonly code: string, readonly status: number) { super(code); }
}
export type ProtocolKeepRequest = { interfaceId: string; pathKey: string | null };
/**
 * POST /api/notifications/protocol-alerts/keep(관리자, contracts/protocol-settings-ui.md 5절): [이상 유지]. 룰 세트·판정은 바꾸지 않는다(판정은 이상 그대로).
 * 지금 조회 범위에 그 묶음(인터페이스, pathKey — null = 경로 없는 미등록 인터페이스 알림)이 있어야 한다(없으면 404 protocol_alert_not_found).
 * 기록 = 그 묶음의 현재 최대 순번까지(kept_through_seq) → 그 뒤 같은 묶음은 다시 알리지 않는다. 이미 기록돼 있으면 처음 기록 그대로(alreadyKept).
 * 누른 관리자에게는 그 항목을 읽음으로 둔다.
 */
export async function keepProtocolAlert(pool: Pool, actorId: string, request: ProtocolKeepRequest, windowEvents = NOTIFICATION_WINDOW_EVENTS) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    // 범위 = 개수 범위만(관리자 명령, 사용자 조회 기간과 무관 — 기간 범위보다 넓어 화면에 보이는 묶음은 모두 찾는다).
    const { rows: [found] } = await client.query(`WITH top AS (SELECT coalesce(max(id),0)-$1::bigint AS lo FROM run_decision_events), ${protocolCte(true)}
      SELECT item_key,max(seq)::text AS seq,(array_agg(decision_id ORDER BY seq DESC))[1] AS decision_id
      FROM pkeyed WHERE interface_id=$2 AND path_key=$3 GROUP BY item_key`, [windowEvents, request.interfaceId, request.pathKey ?? '']);
    if (!found) throw new ProtocolAlertError('protocol_alert_not_found', 404);
    const inserted = await client.query(`INSERT INTO ops_protocol_alert_keeps(alert_key,interface_id,path_key,kept_through_seq,decision_id,kept_by_actor_id)
      VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT (alert_key) DO NOTHING`, [found.item_key, request.interfaceId, request.pathKey ?? '', found.seq, found.decision_id, actorId]);
    const { rows: [kept] } = await client.query(`SELECT kept_through_seq::text AS seq,${ISO('kept_at')} AS "keptAt",kept_by_actor_id AS "keptBy"
      FROM ops_protocol_alert_keeps WHERE alert_key=$1`, [found.item_key]);
    await client.query(`INSERT INTO ops_user_protocol_alert_reads(actor_id,item_key,read_seq) VALUES($1,$2,$3)
      ON CONFLICT (actor_id,item_key) DO UPDATE SET read_seq=GREATEST(ops_user_protocol_alert_reads.read_seq,EXCLUDED.read_seq),read_at=clock_timestamp()`,
    [actorId, found.item_key, found.seq]);
    await client.query('COMMIT');
    return { key: found.item_key as string, keptThroughSeq: kept.seq as string, keptAt: kept.keptAt as string, keptBy: kept.keptBy as string,
      alreadyKept: inserted.rowCount === 0 };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}
