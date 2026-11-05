import type { Pool } from 'pg';

/**
 * 이상 분석(/analysis) 판정 목록·상세·중간 판정 기록·Process Flow 읽기(analysis-ui.md).
 * - 판정 값은 저장된 그대로 반환하고 다시 계산하지 않는다. 처리 상태·읽음은 014 표에서 읽는다(쓰기는 analysisWorkflow.ts).
 * - 판정 항목 = 최종 판정 행(run_process_decisions) + 최종 판정이 없는 v03·v04 detect 작업(판정 중·중간 판정·판정 실패).
 *   v04 작업과 그 판정은 구성이 없으므로(016) config_version_id·상세 configuration이 null이다.
 * - 분류(category)와 이상 신호(signal)는 저장 열(status·evidence.anomalous)과 작업·중간 기록 상태에서만, 등급·위험도는
 *   저장된 evidence v4 composite.grade·composite.risk에서만 정한다(detection-decision.md D7·U6).
 */
type Queryable = Pick<Pool, 'query'>;

// 사이드바 분류(와이어프레임 #93 CATS·RISK_CATS·WORKFLOW 순서).
// - 판정 결과별: 전체 / 이상 / 이상 징후 / 판정 중·중간 판정 / 정상 여부 미확인 / 판정 실패·대체. 정상(이상 신호 없음)은 분류가 없고 signal=no 필터로 찾는다.
// - 위험도별: 전체(이상 신호 있음) / 위험 / 주의 / 참고 / 위험도 미정.
// - 분류함: 보류 / 완료(처리 상태, 014).
export const ANALYSIS_CATEGORIES = [
  'all', 'anomaly', 'anomaly_sign', 'in_progress', 'undetermined', 'failed_replaced',
  'risk_all', 'risk_critical', 'risk_warning', 'risk_info', 'risk_unrated',
  'workflow_open', 'workflow_resolved',
] as const;
// yes = 이상 신호 있음(이상 + 이상 징후), anomaly = 이상(저장 등급이 이상 징후가 아닌 이상 신호), anomaly_sign = 이상 징후.
export const ANALYSIS_SIGNALS = ['yes', 'anomaly', 'anomaly_sign', 'no', 'unknown'] as const;
export const ANALYSIS_COMPLETENESS = ['complete', 'partial', 'unavailable'] as const;
export const ANALYSIS_SORTS = ['newest', 'oldest'] as const;
export const ANALYSIS_PAGE_SIZES = [5, 10, 20] as const;
export const ANALYSIS_QUERY_MAX = 100;

export type AnalysisCategory = typeof ANALYSIS_CATEGORIES[number];
export type AnalysisListFilter = {
  category: AnalysisCategory;
  q: string;
  signal: typeof ANALYSIS_SIGNALS[number] | null;
  completeness: typeof ANALYSIS_COMPLETENESS[number] | null;
  sort: typeof ANALYSIS_SORTS[number];
  page: number;
  pageSize: number;
};

/**
 * 판정 항목 공통 CTE. 열: kind, id, decision_id, job_id, source_id, process_observation_id, config_version_id,
 * status, rule_status, ml_status, decision_code, anomaly_score, evidence, occurred_at, decided_at, signal, stage,
 * job_status, transaction_id, process_id, interface_id, channel_id, process_status, response_code.
 *
 * - final: 최종 판정 행. signal은 운영 리포트(operational-v1)와 같은 규칙 — 이상 = status<>unavailable ∧ anomalous=true,
 *   정상 = complete ∧ anomalous=false, 그 밖(부분+false·판정불가·anomalous 없음/null) = 정상 여부 미확인.
 *   같은 작업에 '대체됨'(superseded) 중간 기록이 있으면 stage=replaced.
 * - 작업 항목(최종 판정 없는 v03·v04 detect, succeeded 제외 — succeeded detect는 판정 행이 반드시 있다):
 *   failed → kind=failed(판정 실패·최종 없음), 열린(닫히지 않은) 첫 이상·근거 추가 기록이 있으면 kind=interim, 그 밖 kind=pending.
 *   interim의 evidence = 가장 최근 열린 기록 payload(그 시점 전체 스냅샷), signal=yes(중간 기록은 종합 이상일 때만 쓴다).
 */
const ITEMS_CTE = `replaced AS (
  SELECT DISTINCT job_id FROM run_decision_interims WHERE kind='superseded'
), final_items AS (
  SELECT 'final'::text AS kind, d.id, d.id AS decision_id, d.job_id, o.source_id, d.process_observation_id,
    d.config_version_id, d.status::text AS status, d.rule_status::text AS rule_status, d.ml_status::text AS ml_status,
    d.decision_code, d.anomaly_score::text AS anomaly_score, d.evidence, d.decided_at AS occurred_at, d.decided_at,
    CASE WHEN d.status<>'unavailable' AND d.evidence->'anomalous'='true'::jsonb THEN 'yes'
         WHEN d.status='complete' AND d.evidence->'anomalous'='false'::jsonb THEN 'no' ELSE 'unknown' END AS signal,
    CASE WHEN r.job_id IS NULL THEN 'final' ELSE 'replaced' END AS stage,
    NULL::text AS job_status
  FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id
  LEFT JOIN replaced r ON r.job_id=d.job_id
), open_jobs AS (
  SELECT j.id,j.status::text AS status,j.created_at,j.finished_at,j.input_config_version_id,m.source_id,m.process_observation_id
  FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
  WHERE j.kind='detect' AND j.contract_version IN ('v03','v04') AND j.status<>'succeeded'
    AND NOT EXISTS (SELECT 1 FROM run_process_decisions x WHERE x.job_id=j.id)
), job_items AS (
  SELECT CASE WHEN j.status='failed' THEN 'failed' WHEN li.id IS NOT NULL THEN 'interim' ELSE 'pending' END AS kind,
    j.id, NULL::uuid AS decision_id, j.id AS job_id, j.source_id, j.process_observation_id, j.input_config_version_id AS config_version_id,
    NULL::text AS status, NULL::text AS rule_status, NULL::text AS ml_status, NULL::text AS decision_code, NULL::text AS anomaly_score,
    CASE WHEN j.status='failed' THEN NULL ELSE li.payload END AS evidence,
    CASE WHEN j.status='failed' THEN coalesce(j.finished_at,j.created_at) ELSE coalesce(li.created_at,j.created_at) END AS occurred_at,
    NULL::timestamptz AS decided_at,
    CASE WHEN j.status='failed' THEN 'failed' WHEN li.id IS NOT NULL THEN 'yes' ELSE 'pending' END AS signal,
    CASE WHEN j.status='failed' THEN 'failed' WHEN li.kind='evidence_added' THEN 'updated' WHEN li.id IS NOT NULL THEN 'provisional'
         ELSE 'pending' END AS stage,
    j.status AS job_status
  FROM open_jobs j LEFT JOIN LATERAL (
    SELECT i.id,i.kind,i.payload,i.created_at FROM run_decision_interims i JOIN run_job_attempts a ON a.id=i.attempt_id
     WHERE i.job_id=j.id AND i.kind IN ('first_anomaly','evidence_added')
       AND NOT EXISTS (SELECT 1 FROM run_decision_interims c WHERE c.attempt_id=i.attempt_id AND c.kind IN ('superseded','no_final'))
     ORDER BY a.attempt_no DESC,i.seq DESC LIMIT 1) li ON true
), base AS (
  SELECT * FROM final_items UNION ALL SELECT * FROM job_items
), workflow AS (
  SELECT DISTINCT ON (coalesce(job_id,decision_id)) coalesce(job_id,decision_id) AS target,to_state,created_at
  FROM run_decision_workflow_events ORDER BY coalesce(job_id,decision_id),seq DESC
), v4 AS (
  SELECT b.*,coalesce(b.evidence->'schemaVersion'='4'::jsonb AND jsonb_typeof(b.evidence->'composite')='object',false) AS is_v4
  FROM base b
), items AS (
  SELECT b.kind,b.id,b.decision_id,b.job_id,b.source_id,b.process_observation_id,b.config_version_id,b.status,b.rule_status,
    b.ml_status,b.decision_code,b.anomaly_score,b.evidence,b.occurred_at,b.decided_at,b.signal,b.stage,b.job_status,
    p.transaction_id,p.process_id,t.interface_id,p.channel_id,p.status AS process_status,p.response_code,
    CASE WHEN b.is_v4 AND b.evidence->'composite'->>'grade' IN ('anomaly','anomaly_sign','normal','undetermined')
         THEN b.evidence->'composite'->>'grade' END AS grade,
    CASE WHEN b.signal<>'yes' THEN 'not_applicable'
         WHEN b.is_v4 AND b.evidence->'composite'->'risk'->>'status'='rated'
              AND b.evidence->'composite'->'risk'->>'level' IN ('critical','warning','info') THEN 'rated'
         ELSE 'unrated' END AS risk_status,
    CASE WHEN b.signal='yes' AND b.is_v4 AND b.evidence->'composite'->'risk'->>'status'='rated'
              AND b.evidence->'composite'->'risk'->>'level' IN ('critical','warning','info')
         THEN b.evidence->'composite'->'risk'->>'level' END AS risk_level,
    coalesce(w.to_state,'detected') AS workflow_state,w.created_at AS workflow_changed_at
  FROM v4 b LEFT JOIN data_processes p ON p.observation_id=b.process_observation_id
  LEFT JOIN data_transactions t ON t.observation_id=p.transaction_observation_id
  LEFT JOIN workflow w ON w.target=coalesce(b.job_id,b.decision_id)
)`;

/** 분류 조건(SQL). 분류끼리 겹칠 수 있다(예: 대체된 최종 판정은 이상 신호 분류와 판정 실패·대체 분류에 모두 든다). */
// 이상 / 이상 징후는 저장된 v4 composite.grade만 쓴다(v3는 등급 null → 이상, detection-decision.md U6).
// 위험도는 저장된 v4 composite.risk만 쓴다(D7, 재계산 없음). v3·형식이 틀린 risk의 이상 신호 = 위험도 미정(U6), 이상 신호가 아니면 해당 없음.
const CATEGORY_SQL: Record<AnalysisCategory, string> = {
  all: 'true',
  anomaly: "signal='yes' AND grade IS DISTINCT FROM 'anomaly_sign'",
  anomaly_sign: "signal='yes' AND grade='anomaly_sign'",
  undetermined: "signal='unknown'",
  in_progress: "kind IN ('pending','interim')",
  failed_replaced: "stage IN ('failed','replaced')",
  risk_all: "signal='yes'",
  risk_critical: "risk_level='critical'",
  risk_warning: "risk_level='warning'",
  risk_info: "risk_level='info'",
  risk_unrated: "risk_status='unrated'",
  workflow_open: "workflow_state='open'",
  workflow_resolved: "workflow_state='resolved'",
};
const SIGNAL_SQL: Record<typeof ANALYSIS_SIGNALS[number], string> = {
  yes: "signal='yes'",
  anomaly: CATEGORY_SQL.anomaly,
  anomaly_sign: CATEGORY_SQL.anomaly_sign,
  no: "signal='no'",
  unknown: "signal='unknown'",
};

const ITEM_JSON = `jsonb_build_object('id',id,'kind',kind,'stage',stage,'signal',signal,'decisionId',decision_id,'jobId',job_id,
  'jobStatus',job_status,'sourceId',source_id,'processObservationId',process_observation_id,'configVersionId',config_version_id,
  'status',status,'ruleStatus',rule_status,'mlStatus',ml_status,'decisionCode',decision_code,'anomalyScore',anomaly_score,
  'evidence',evidence,'occurredAt',to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'decidedAt',to_char(decided_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'transactionId',transaction_id,'processId',process_id,'interfaceId',interface_id,'channelId',channel_id,
  'processStatus',process_status,'responseCode',response_code,'grade',grade,'riskStatus',risk_status,'riskLevel',risk_level,
  'workflowState',workflow_state,
  'workflowChangedAt',to_char(workflow_changed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))`;

/**
 * 요청자 기준 미확인: 그 대상(작업 → job_id, 작업 없는 판정 → decision_id)의 읽음 행이 없거나 열람 시각이 항목 시각보다 이르다
 * (열람 뒤 중간 판정·최종 판정이 새로 생기면 다시 미확인). 페이지 행에만 계산한다(건수와 무관).
 */
const UNREAD_SQL = (actor: string) => `CASE WHEN f.job_id IS NOT NULL
    THEN NOT EXISTS (SELECT 1 FROM run_decision_reads r WHERE r.job_id=f.job_id AND r.actor_id=${actor} AND r.read_at>=f.occurred_at)
    ELSE NOT EXISTS (SELECT 1 FROM run_decision_reads r WHERE r.decision_id=f.decision_id AND r.actor_id=${actor} AND r.read_at>=f.occurred_at) END`;

/** GET /api/analysis/decisions: 분류별 건수(전체 기준) + 조건에 맞는 한 페이지. */
export async function readDecisionList(db: Pick<Pool, 'connect'>, filter: AnalysisListFilter, actorId: string) {
  const conditions = [CATEGORY_SQL[filter.category]];
  const values: unknown[] = [];
  if (filter.q) {
    // 부분 일치·대소문자 무시. LIKE 대신 strpos라 % _ 등 특수 문자를 그대로 찾는다. 필드마다 따로 비교(필드 경계를 넘어 일치하지 않음).
    values.push(filter.q.toLowerCase());
    const p = `$${values.length}`;
    conditions.push(`(${['id::text', 'job_id::text', 'transaction_id', 'process_id', 'interface_id']
      .map((field) => `strpos(lower(${field}),${p})>0`).join(' OR ')})`);
  }
  if (filter.signal) conditions.push(SIGNAL_SQL[filter.signal]);
  if (filter.completeness) { values.push(filter.completeness); conditions.push(`status=$${values.length}`); }
  values.push(actorId);
  const actor = `$${values.length}`;
  values.push(filter.pageSize, (filter.page - 1) * filter.pageSize);
  const direction = filter.sort === 'oldest' ? 'ASC' : 'DESC';
  const counts = ANALYSIS_CATEGORIES.map((key) => `'${key}',count(*) FILTER (WHERE ${CATEGORY_SQL[key]})::integer`).join(',');
  const client = await db.connect();
  try {
    // 건수·페이지·SSE 커서를 한 스냅샷에서 읽는다(건수와 목록이 서로 다른 시점이 되지 않게).
    await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
    const { rows: [row] } = await client.query(`WITH ${ITEMS_CTE}, filtered AS (SELECT * FROM items WHERE ${conditions.join(' AND ')})
      SELECT (SELECT jsonb_build_object(${counts}) FROM items) AS counts,
        (SELECT count(*)::integer FROM filtered) AS total,
        (SELECT to_char(max(decided_at) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM items) AS "latestDecidedAt",
        (SELECT coalesce(max(id),0)::text FROM run_decision_events) AS "latestEventId",
        (SELECT coalesce(jsonb_agg(item ORDER BY n),'[]'::jsonb) FROM (
           SELECT ${ITEM_JSON} || jsonb_build_object('unread',${UNREAD_SQL(actor)}) AS item,
                  row_number() OVER (ORDER BY occurred_at ${direction},id ${direction}) AS n
             FROM (SELECT * FROM filtered ORDER BY occurred_at ${direction},id ${direction}
                   LIMIT $${values.length - 1} OFFSET $${values.length}) f) page) AS items`, values);
    await client.query('COMMIT');
    return {
      observedAt: new Date().toISOString(),
      // jsonb는 키 순서를 보존하지 않으므로 분류 순서(사이드바 순서)대로 다시 담는다.
      counts: Object.fromEntries(ANALYSIS_CATEGORIES.map((key) => [key, row.counts[key]])), total: row.total, page: filter.page, pageSize: filter.pageSize,
      latestDecidedAt: row.latestDecidedAt ?? null, latestEventId: row.latestEventId, items: row.items,
    };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

/**
 * id 해석: 판정 ID → 그 판정. 아니면 detect 작업 ID → 그 작업의 최종 판정(있으면) 또는 작업 항목(판정 중·중간 판정·판정 실패).
 * 둘 다 아니면 null(404). 작업 ID로 요청해 최종 판정이 생긴 경우 응답 id는 판정 ID다(화면이 주소를 바꾼다).
 */
export async function resolveItem(db: Queryable, id: string) {
  const { rows: [row] } = await db.query(`WITH ${ITEMS_CTE}
    SELECT ${ITEM_JSON} AS item FROM items
     WHERE id=$1 OR (kind='final' AND job_id=$1) ORDER BY (id=$1) DESC LIMIT 1`, [id]);
  return row?.item ?? null;
}

/** GET /api/analysis/decisions/:id: 판정 항목 + 당시 구성 요약 + detect 작업 요약 + 요청자 본인의 최근 설명 작업. */
export async function readDecisionDetail(db: Queryable, id: string, actorId: string) {
  const item = await resolveItem(db, id);
  if (!item) return null;
  const { rows: [context] } = await db.query(`SELECT
      (SELECT jsonb_build_object('id',c.id,'createdAt',to_char(c.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
         'detectionMode',c.configuration_spec->'detectionPolicy'->>'mode',
         'ruleCount',(SELECT count(*)::integer FROM ml_config_rules r WHERE r.config_version_id=c.id),
         'models',(SELECT coalesce(jsonb_agg(jsonb_build_object('modelKey',m.model_key,'adapterName',m.adapter_spec->>'name') ORDER BY m.model_key),'[]'::jsonb)
                     FROM ml_config_models m WHERE m.config_version_id=c.id))
         FROM ml_config_versions c WHERE c.id=$1) AS configuration,
      (SELECT jsonb_build_object('id',j.id,'status',j.status,'attemptCount',j.attempt_count,'maxAttempts',j.max_attempts,
         'errorCode',j.error_code,'createdAt',to_char(j.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
         'finishedAt',to_char(j.finished_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
         FROM run_jobs j WHERE j.id=$2) AS job,
      (SELECT jsonb_build_object('jobId',j.id,'status',j.status,'errorCode',j.error_code,
         'createdAt',to_char(j.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
         FROM run_jobs j WHERE j.kind='explain' AND j.input_decision_id=$3 AND j.requested_by_actor_id=$4
         ORDER BY j.created_at DESC,j.id DESC LIMIT 1) AS explanation`,
  [item.configVersionId, item.jobId, item.decisionId, actorId]);
  return { ...item, configuration: context.configuration ?? null, job: context.job ?? null,
    explanation: context.explanation ?? null,
    workflow: { state: item.workflowState, changedAt: item.workflowChangedAt ?? null } };
}

/** GET /api/analysis/decisions/:id/interims: 그 판정·작업의 중간 판정 기록(시도 순, 시도 안 순번 순). */
export async function readDecisionInterims(db: Queryable, id: string) {
  const item = await resolveItem(db, id);
  if (!item) return null;
  if (!item.jobId) return { id: item.id, jobId: null, items: [] };
  const { rows } = await db.query(`SELECT i.id,i.attempt_id AS "attemptId",a.attempt_no AS "attemptNo",i.seq,i.kind,
      i.evidence_version AS "evidenceVersion",i.superseded_by_attempt_id AS "supersededByAttemptId",
      b.attempt_no AS "supersededByAttemptNo",i.reason_code AS "reasonCode",
      to_char(i.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "createdAt",i.payload
    FROM run_decision_interims i JOIN run_job_attempts a ON a.id=i.attempt_id
    LEFT JOIN run_job_attempts b ON b.id=i.superseded_by_attempt_id
    WHERE i.job_id=$1 ORDER BY a.attempt_no,i.seq`, [item.jobId]);
  return { id: item.id, jobId: item.jobId, items: rows };
}

export const FLOW_PROCESS_LIMIT = 200;
export const FLOW_MESSAGE_LIMIT = 500;
/**
 * GET /api/analysis/decisions/:id/flow: 판정 PROCESS가 속한 거래의 TRANSACTION·PROCESS·MESSAGE 저장값(조회 시점).
 * - 묶는 기준: 같은 원본(source) 안 거래 ID·프로세스 ID 문자열. 검증된 부모 연결(FK)이 있으면 TRANSACTION은 그 행을 쓴다.
 *   묶음은 화면 표시용이며 부모 연결을 새로 만들거나 저장하지 않는다(002 data_* 행 불변).
 * - 실행(execution)마다 가장 큰 리비전 1건. 판정 PROCESS 실행은 판정에 쓰인 관측 그대로(focus).
 * - 노출 필드는 와이어프레임 선택 노드 원본 정보 항목만. BODY(data_message_bodies)·response_message·원천 시각 원문은 읽지 않는다.
 */
export async function readDecisionFlow(db: Queryable, id: string,
  limits = { processes: FLOW_PROCESS_LIMIT, messages: FLOW_MESSAGE_LIMIT }) {
  const item = await resolveItem(db, id);
  if (!item) return null;
  const empty = { id: item.id, jobId: item.jobId, transaction: null, processes: [], messages: [], truncated: false,
    counts: { processes: 0, messages: 0, errors: 0 } };
  if (!item.processObservationId) return empty;
  const { rows: [focus] } = await db.query(`SELECT p.observation_id,p.transaction_observation_id,p.transaction_id,o.source_id
      FROM data_processes p JOIN data_observations o ON o.id=p.observation_id WHERE p.observation_id=$1`, [item.processObservationId]);
  if (!focus) return empty;
  const { rows: [tx] } = await db.query(`SELECT t.transaction_id AS "transactionId",t.interface_id AS "interfaceId",
      t.start_channel_id AS "channelId",t.status,t.response_code AS "responseCode",
      t.reported_process_time_ms::text AS "processTimeMs",t.process_count::text AS "processCount"
    FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id
    WHERE ($1::uuid IS NOT NULL AND t.observation_id=$1) OR ($1::uuid IS NULL AND $2::text IS NOT NULL AND t.transaction_id=$2 AND o.source_id=$3)
    ORDER BY (t.observation_id=$1) DESC NULLS LAST,o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id LIMIT 1`,
  [focus.transaction_observation_id, focus.transaction_id, focus.source_id]);
  const { rows: processes } = await db.query(`WITH candidates AS (
      SELECT p.observation_id FROM data_processes p WHERE $2::text IS NOT NULL AND p.transaction_id=$2
      UNION SELECT $1::uuid
    ), latest AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) p.observation_id,p.process_id,p.depend_process_id,p.status,p.response_code,
        p.total_count,p.start_time_raw,(p.observation_id=$1) AS focus
      FROM candidates c JOIN data_processes p ON p.observation_id=c.observation_id
      JOIN data_observations o ON o.id=p.observation_id AND o.source_id=$3 AND o.layer='P'
      ORDER BY coalesce(o.execution_id,o.id),(p.observation_id=$1) DESC,o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT observation_id AS "observationId",process_id AS "processId",depend_process_id AS "dependsOn",status,
      response_code AS "responseCode",total_count::text AS "totalCount",focus,
      count(*) OVER ()::integer AS "_total",(count(*) FILTER (WHERE status='F') OVER ())::integer AS "_errors"
    FROM latest ORDER BY start_time_raw NULLS LAST,process_id NULLS LAST,observation_id LIMIT $4`,
  [focus.observation_id, focus.transaction_id, focus.source_id, limits.processes + 1]);
  const { rows: messages } = focus.transaction_id === null ? { rows: [] } : await db.query(`WITH latest AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) m.observation_id,m.message_id,m.process_id,m.direction,m.status,
        m.data_size,m.message_index
      FROM data_messages m JOIN data_observations o ON o.id=m.observation_id AND o.source_id=$2 AND o.layer='M'
      WHERE m.transaction_id=$1
      ORDER BY coalesce(o.execution_id,o.id),o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT observation_id AS "observationId",message_id AS "messageId",process_id AS "processId",direction,status,
      data_size::text AS "dataSize",count(*) OVER ()::integer AS "_total"
    FROM latest ORDER BY process_id NULLS LAST,message_index NULLS LAST,message_id NULLS LAST,observation_id LIMIT $3`,
  [focus.transaction_id, focus.source_id, limits.messages + 1]);
  const strip = <T extends Record<string, unknown>>(row: T) =>
    Object.fromEntries(Object.entries(row).filter(([key]) => !key.startsWith('_')));
  return {
    id: item.id,
    jobId: item.jobId,
    transaction: tx ?? null,
    processes: processes.slice(0, limits.processes).map(strip),
    messages: messages.slice(0, limits.messages).map(strip),
    truncated: processes.length > limits.processes || messages.length > limits.messages,
    // 상한과 무관한 실제 건수(화면 머리 `(프로세스 N건 · 메시지 M건 · 에러 K건)`). 에러 = 상태 F PROCESS.
    counts: { processes: processes[0]?._total ?? 0, messages: messages[0]?._total ?? 0, errors: processes[0]?._errors ?? 0 },
  };
}
