import type { Pool } from 'pg';
import { buildInstanceGraph, GRAPH_ABSENT_SQL, GRAPH_RETRY_LIMIT, graphValueAbsent, pickTransactionRow, type GraphTransactionRow } from './processGraph.js';
import { mlMethod, softRuleField } from '../ruleset/methodCatalog.js';

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
// - 판정 결과별: 전체 / 이상 / 이상 징후 / 정상 / 판정 중·중간 판정 / 정상 여부 미확인 / 판정 실패·대체. 정상 = 이상 신호 없음(signal=no, 사용자 11/13).
// - 위험도별: 전체(이상 신호 있음) / 위험 / 주의 / 참고 / 위험도 미정.
// - 분류함: 보류 / 완료(처리 상태, 014).
export const ANALYSIS_CATEGORIES = [
  'all', 'anomaly', 'anomaly_sign', 'normal', 'in_progress', 'undetermined', 'failed_replaced',
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
 * job_status, transaction_id, process_id, transaction_observation_id, channel_id, process_status, response_code(interfaceId는 TX_INTERFACE_SQL).
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
    NULL::text AS signal,
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
), fields AS (
  -- evidence(판정당 수 KB, TOAST 압축)는 행마다 한 번만 풀어 최상위 키 3개만 꺼낸다. ->마다 다시 풀면 판정이 많을 때 목록 1회가 수 초 걸린다.
  -- 객체가 아닌 evidence는 빈 객체로 본다(->가 NULL을 주던 것과 같음). jsonb_path_query_first(strict, silent)는 객체면 그대로, 아니면 NULL.
  SELECT b.*,e.anomalous AS ev_anomalous,
    coalesce(e."schemaVersion"='4'::jsonb AND jsonb_typeof(e.composite)='object',false) AS is_v4,
    e.composite->>'grade' AS ev_grade,e.composite->'risk'->>'status' AS ev_risk_status,e.composite->'risk'->>'level' AS ev_risk_level
  FROM base b CROSS JOIN LATERAL jsonb_to_record(coalesce(
    jsonb_path_query_first(b.evidence,'strict $ ? (@.type() == "object")','{}',true),'{}')) AS e(anomalous jsonb,"schemaVersion" jsonb,composite jsonb)
), v4 AS (
  SELECT f.*,CASE WHEN f.kind<>'final' THEN f.signal
      WHEN f.status<>'unavailable' AND f.ev_anomalous='true'::jsonb THEN 'yes'
      WHEN f.status='complete' AND f.ev_anomalous='false'::jsonb THEN 'no' ELSE 'unknown' END AS item_signal
  FROM fields f
), items AS (
  SELECT b.kind,b.id,b.decision_id,b.job_id,b.source_id,b.process_observation_id,b.config_version_id,b.status,b.rule_status,
    b.ml_status,b.decision_code,b.anomaly_score,b.evidence,b.occurred_at,b.decided_at,b.item_signal AS signal,b.stage,b.job_status,
    p.transaction_id,p.process_id,p.transaction_observation_id,p.channel_id,p.status AS process_status,p.response_code,
    CASE WHEN b.is_v4 AND b.ev_grade IN ('anomaly','anomaly_sign','normal','undetermined') THEN b.ev_grade END AS grade,
    CASE WHEN b.item_signal<>'yes' THEN 'not_applicable'
         WHEN b.is_v4 AND b.ev_risk_status='rated' AND b.ev_risk_level IN ('critical','warning','info') THEN 'rated'
         ELSE 'unrated' END AS risk_status,
    CASE WHEN b.item_signal='yes' AND b.is_v4 AND b.ev_risk_status='rated' AND b.ev_risk_level IN ('critical','warning','info')
         THEN b.ev_risk_level END AS risk_level,
    coalesce(w.to_state,'detected') AS workflow_state,w.created_at AS workflow_changed_at
  FROM v4 b LEFT JOIN data_processes p ON p.observation_id=b.process_observation_id
  LEFT JOIN workflow w ON w.target=coalesce(b.job_id,b.decision_id)
)`;

/** 분류 조건(SQL). 분류끼리 겹칠 수 있다(예: 대체된 최종 판정은 이상 신호 분류와 판정 실패·대체 분류에 모두 든다). */
// 이상 / 이상 징후는 저장된 v4 composite.grade만 쓴다(v3는 등급 null → 이상, detection-decision.md U6).
// 위험도는 저장된 v4 composite.risk만 쓴다(D7, 재계산 없음). v3·형식이 틀린 risk의 이상 신호 = 위험도 미정(U6), 이상 신호가 아니면 해당 없음.
const CATEGORY_SQL: Record<AnalysisCategory, string> = {
  all: 'true',
  anomaly: "signal='yes' AND grade IS DISTINCT FROM 'anomaly_sign'",
  anomaly_sign: "signal='yes' AND grade='anomaly_sign'",
  // 정상 = 최종 판정 complete ∧ anomalous=false(signal=no). v03·v04 모두 같은 저장 열로 정한다(구성 유무와 무관).
  normal: "signal='no'",
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
  no: CATEGORY_SQL.normal,
  unknown: "signal='unknown'",
};

/**
 * 항목 인터페이스 ID(`a` = 항목 행 별칭). 실행 그래프 거래 노드 대표 행(readDecisionGraph, process-graph.md "거래 노드 대표 행")과 같은 행의 값:
 * - 먼저 읽는 TRANSACTION(tx0) = 검증된 부모 연결(FK)이 있으면 그 행, 없으면 같은 원천·같은 거래 ID 중 최신 리비전 1건
 *   (현재 적재 경로는 data_processes.transaction_observation_id를 채우지 않아 거래 ID로 찾는다)
 * - 거래 ID가 있으면 같은 원천·같은 거래 ID의 실행마다 최신 리비전 중 pickTransactionRow 규칙(실행 1개 = 그 행, 여럿 = 재시도 번호 최대,
 *   실행 id·재시도 번호 없음·최대 동률 = 모호 → null). 같은 문자열 행이 없거나 거래 ID가 없음이면 tx0 값
 * 페이지 행·상세 1건에서만 행마다 계산한다(건수 집계에는 쓰지 않음). 검색어 조건은 같은 규칙의 묶음 계산(SEARCH_INTERFACE_CTE)을 쓴다.
 */
const TX_LATEST_ORDER = 'o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id';
/** 실행별 최신 리비전 행(execution_id·retry_count·interface_id·observation_id 열) 집계: 실행 수·빈 값 수·재시도 번호 큰 순 앞 2건. */
const TX_TOP_COLUMNS = `count(*) AS n,count(*) FILTER (WHERE execution_id IS NULL) AS missing_execution,
      count(*) FILTER (WHERE retry_count IS NULL) AS missing_retry,
      (array_agg(interface_id ORDER BY retry_count DESC NULLS FIRST,observation_id))[1] AS interface_id,
      (array_agg(retry_count ORDER BY retry_count DESC NULLS FIRST,observation_id))[1:2] AS retries`;
/** 대표 인터페이스: 같은 문자열 실행이 없으면(top 행 없음 또는 n=0) tx0 값, 1개면 그 행, 여럿이면 모호하지 않을 때만 재시도 번호 최대 행. */
const TX_PICK = (fallback: string, top: string) => `CASE WHEN coalesce(${top}.n,0)=0 THEN ${fallback} WHEN ${top}.n=1 THEN ${top}.interface_id
    WHEN ${top}.missing_execution>0 OR ${top}.missing_retry>0 OR ${top}.retries[1]=${top}.retries[2] THEN NULL ELSE ${top}.interface_id END`;
const TX_INTERFACE_SQL = (a: string) => `(WITH tx0 AS (
    SELECT t.transaction_id,t.interface_id,o.source_id FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id
     WHERE t.observation_id=${a}.transaction_observation_id
    UNION ALL (SELECT t.transaction_id,t.interface_id,o.source_id FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id
     WHERE ${a}.transaction_observation_id IS NULL AND t.transaction_id=${a}.transaction_id AND o.source_id=${a}.source_id
     ORDER BY ${TX_LATEST_ORDER} LIMIT 1)
    LIMIT 1
  ), r AS (
    SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) t.observation_id,o.execution_id,t.retry_count,t.interface_id
    FROM tx0 JOIN data_transactions t ON t.transaction_id=tx0.transaction_id
    JOIN data_observations o ON o.id=t.observation_id AND o.source_id=tx0.source_id AND o.layer='T'
    WHERE NOT ${GRAPH_ABSENT_SQL('tx0.transaction_id')}
    ORDER BY coalesce(o.execution_id,o.id),${TX_LATEST_ORDER}
  ), top AS (
    SELECT ${TX_TOP_COLUMNS} FROM r
  )
  SELECT ${TX_PICK('tx0.interface_id', 'top')}
  FROM tx0 CROSS JOIN top)`;

/**
 * 검색어(`p` = 소문자 검색어 매개변수)의 인터페이스 일치를 행마다 계산하지 않고 묶음으로 구한다(행마다 하위 조회를 돌리면 판정이 많을 때 검색이 느려진다).
 * - q_keys: 인터페이스 ID에 검색어가 든 TRANSACTION 행의 (원천, 거래 ID). 대표 인터페이스는 언제나 그 원천·거래 ID 행 중 하나의 값이므로
 *   대표 값이 일치하는 항목의 키는 반드시 여기 있다(후보 상한).
 * - q_match: 후보 키마다 TX_INTERFACE_SQL의 거래 ID 경로(FK 없음)와 같은 규칙으로 대표 인터페이스를 구해 검색어가 든 키만 남긴다.
 * FK가 있는 항목은 후보 키(또는 FK 행 자체 값)가 일치할 때만 행마다 TX_INTERFACE_SQL로 확인한다(SEARCH_INTERFACE_SQL).
 */
const SEARCH_INTERFACE_CTE = (p: string) => `q_keys AS (
  SELECT DISTINCT o.source_id,t.transaction_id FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id
   WHERE strpos(lower(t.interface_id),${p})>0
), q_rows AS (
  SELECT k.source_id,k.transaction_id,t.observation_id,t.interface_id,t.retry_count,o.id,o.execution_id,o.layer,o.revision_no,o.persisted_at
  FROM q_keys k JOIN data_transactions t ON t.transaction_id=k.transaction_id
  JOIN data_observations o ON o.id=t.observation_id AND o.source_id=k.source_id
), q_tx0 AS (
  SELECT DISTINCT ON (o.source_id,o.transaction_id) o.source_id,o.transaction_id,o.interface_id
  FROM q_rows o ORDER BY o.source_id,o.transaction_id,${TX_LATEST_ORDER}
), q_r AS (
  SELECT DISTINCT ON (o.source_id,o.transaction_id,coalesce(o.execution_id,o.id)) o.source_id,o.transaction_id,o.observation_id,
    o.execution_id,o.retry_count,o.interface_id
  FROM q_rows o WHERE o.layer='T' AND NOT ${GRAPH_ABSENT_SQL('o.transaction_id')}
  ORDER BY o.source_id,o.transaction_id,coalesce(o.execution_id,o.id),${TX_LATEST_ORDER}
), q_top AS (
  SELECT source_id,transaction_id,${TX_TOP_COLUMNS} FROM q_r GROUP BY source_id,transaction_id
), q_match AS (
  SELECT x.source_id,x.transaction_id FROM q_tx0 x
  LEFT JOIN q_top top ON top.source_id=x.source_id AND top.transaction_id=x.transaction_id
  WHERE strpos(lower(${TX_PICK('x.interface_id', 'top')}),${p})>0
)`;
const SEARCH_INTERFACE_SQL = (a: string, p: string) => `CASE WHEN ${a}.transaction_observation_id IS NULL
    THEN (${a}.source_id,${a}.transaction_id) IN (SELECT source_id,transaction_id FROM q_match)
  WHEN EXISTS (SELECT 1 FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id
     WHERE t.observation_id=${a}.transaction_observation_id
       AND (strpos(lower(t.interface_id),${p})>0 OR (o.source_id,t.transaction_id) IN (SELECT source_id,transaction_id FROM q_keys)))
    THEN strpos(lower(${TX_INTERFACE_SQL(a)}),${p})>0
  ELSE false END`;

const ITEM_JSON = (a: string) => `jsonb_build_object('id',id,'kind',kind,'stage',stage,'signal',signal,'decisionId',decision_id,'jobId',job_id,
  'jobStatus',job_status,'sourceId',source_id,'processObservationId',process_observation_id,'configVersionId',config_version_id,
  'status',status,'ruleStatus',rule_status,'mlStatus',ml_status,'decisionCode',decision_code,'anomalyScore',anomaly_score,
  'evidence',evidence,'occurredAt',to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'decidedAt',to_char(decided_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'transactionId',transaction_id,'processId',process_id,'interfaceId',${TX_INTERFACE_SQL(a)},'channelId',channel_id,
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

/**
 * 목록 행 이상 원인 요약(사용자 11/13 "F여서 오류가 떴다"): 이상 신호(signal=yes) 항목의 저장 evidence에서 이상(anomalous=true)인 방법만 그대로 옮긴다.
 * 판정을 다시 계산하지 않고, 표시 이름은 방법 카탈로그(method-catalog.json)의 소프트룰 필드·ML 방법 이름만 붙인다(문구는 화면이 만든다).
 * - v4(schemaVersion 4 + methodResults[]): 순서 = 종합 근거(composite.basisMethods) 먼저, 나머지는 저장 순서
 * - v3: rules[] result=hit(상태 규칙 → hard:process-status, 그 밖 → 과거 등록 Rule ruleKey) + models[] anomalous=true(점수 = 판정 행 anomaly_score)
 */
export type DecisionCause = {
  category: 'hard' | 'soft' | 'ml';
  methodId: string | null;
  ruleKey: string | null;
  code: string | null;
  observedStatus: string | null;
  fieldName: string | null;
  operator: string | null;
  threshold: string | null;
  unit: string | null;
  observedValue: string | null;
  modelName: string | null;
  score: number | null;
  scoreThreshold: string | null;
};
type Json = Record<string, unknown>;
const isRecord = (value: unknown): value is Json => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const str = (value: unknown) => (typeof value === 'string' && value ? value : null);
const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const numText = (value: unknown) => str(value) ?? (num(value) === null ? null : String(value));
const records = (value: unknown) => (Array.isArray(value) ? value.filter(isRecord) : []);
const blank: Omit<DecisionCause, 'category'> = { methodId: null, ruleKey: null, code: null, observedStatus: null, fieldName: null, operator: null,
  threshold: null, unit: null, observedValue: null, modelName: null, score: null, scoreThreshold: null };
const modelName = (key: string) => mlMethod(key)?.displayName ?? key;

export function decisionCauses(item: { signal: string; evidence: unknown; anomalyScore: unknown }): DecisionCause[] {
  const evidence = item.evidence;
  if (item.signal !== 'yes' || !isRecord(evidence)) return [];
  const legacyStatus = records(evidence.rules).find((rule) => rule.ruleKey === 'process-status');
  if (evidence.schemaVersion === 4 && Array.isArray(evidence.methodResults)) {
    const basis = isRecord(evidence.composite) && Array.isArray(evidence.composite.basisMethods) ? evidence.composite.basisMethods : [];
    const causes = records(evidence.methodResults).flatMap((method): DecisionCause[] => {
      const category = method.category, methodId = str(method.methodId);
      if (method.anomalous !== true || (category !== 'hard' && category !== 'soft' && category !== 'ml') || !methodId?.startsWith(`${category}:`)) return [];
      const cause: DecisionCause = { ...blank, category, methodId, code: str(method.code) };
      if (category === 'hard' && methodId === 'hard:process-status')
        cause.observedStatus = str(records(method.evidence)[0]?.observedStatus) ?? str(legacyStatus?.observedStatus);
      if (category === 'soft') {
        const condition = isRecord(method.condition) ? method.condition : {};
        const field = str(condition.field);
        Object.assign(cause, { fieldName: field ? (softRuleField(field)?.displayName ?? field) : null, operator: str(condition.operator),
          threshold: numText(condition.threshold), unit: str(condition.unit), observedValue: numText(method.observedValue) });
      }
      if (category === 'ml') {
        const detail = isRecord(method.scoreDetail) ? method.scoreDetail : {};
        Object.assign(cause, { modelName: modelName(methodId.slice(3)), score: num(detail.raw) ?? num(method.score), scoreThreshold: numText(detail.threshold) });
      }
      return [cause];
    });
    const rank = (cause: DecisionCause) => (basis.includes(cause.methodId) ? 0 : 1);
    return causes.map((cause, index) => ({ cause, index })).sort((a, b) => rank(a.cause) - rank(b.cause) || a.index - b.index).map(({ cause }) => cause);
  }
  const score = num(typeof item.anomalyScore === 'string' ? Number(item.anomalyScore) : item.anomalyScore);
  return [
    ...records(evidence.rules).filter((rule) => rule.result === 'hit' && str(rule.ruleKey)).map((rule): DecisionCause => rule.ruleKey === 'process-status'
      ? { ...blank, category: 'hard', methodId: 'hard:process-status', observedStatus: str(rule.observedStatus) }
      : { ...blank, category: 'soft', ruleKey: str(rule.ruleKey) }),
    ...records(evidence.models).filter((model) => model.anomalous === true && str(model.modelKey)).map((model): DecisionCause => ({
      ...blank, category: 'ml', methodId: `ml:${model.modelKey}`, modelName: modelName(model.modelKey as string), score })),
  ];
}

/** GET /api/analysis/decisions: 분류별 건수(전체 기준) + 조건에 맞는 한 페이지. */
export async function readDecisionList(db: Pick<Pool, 'connect'>, filter: AnalysisListFilter, actorId: string) {
  const conditions = [CATEGORY_SQL[filter.category]];
  const values: unknown[] = [];
  let searchCte = '';
  if (filter.q) {
    // 부분 일치·대소문자 무시. LIKE 대신 strpos라 % _ 등 특수 문자를 그대로 찾는다. 필드마다 따로 비교(필드 경계를 넘어 일치하지 않음).
    values.push(filter.q.toLowerCase());
    const p = `$${values.length}`;
    conditions.push(`(${['i.id::text', 'i.job_id::text', 'i.transaction_id', 'i.process_id']
      .map((field) => `strpos(lower(${field}),${p})>0`).join(' OR ')} OR ${SEARCH_INTERFACE_SQL('i', p)})`);
    searchCte = `, ${SEARCH_INTERFACE_CTE(p)}`;
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
    const { rows: [row] } = await client.query(`WITH ${ITEMS_CTE}${searchCte}, filtered AS (SELECT * FROM items i WHERE ${conditions.join(' AND ')})
      SELECT (SELECT jsonb_build_object(${counts}) FROM items) AS counts,
        (SELECT count(*)::integer FROM filtered) AS total,
        (SELECT to_char(max(decided_at) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM items) AS "latestDecidedAt",
        (SELECT coalesce(max(id),0)::text FROM run_decision_events) AS "latestEventId",
        (SELECT coalesce(jsonb_agg(item ORDER BY n),'[]'::jsonb) FROM (
           SELECT ${ITEM_JSON('f')} || jsonb_build_object('unread',${UNREAD_SQL(actor)}) AS item,
                  row_number() OVER (ORDER BY occurred_at ${direction},id ${direction}) AS n
             FROM (SELECT * FROM filtered ORDER BY occurred_at ${direction},id ${direction}
                   LIMIT $${values.length - 1} OFFSET $${values.length}) f) page) AS items`, values);
    await client.query('COMMIT');
    return {
      observedAt: new Date().toISOString(),
      // jsonb는 키 순서를 보존하지 않으므로 분류 순서(사이드바 순서)대로 다시 담는다.
      counts: Object.fromEntries(ANALYSIS_CATEGORIES.map((key) => [key, row.counts[key]])), total: row.total, page: filter.page, pageSize: filter.pageSize,
      latestDecidedAt: row.latestDecidedAt ?? null, latestEventId: row.latestEventId,
      items: row.items.map((item: { signal: string; evidence: unknown; anomalyScore: unknown }) => ({ ...item, causes: decisionCauses(item) })),
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
    SELECT ${ITEM_JSON('i')} AS item FROM items i
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
type FlowLimits = { processes: number; messages: number };
/**
 * Process Flow·실행 그래프 공통 조회(쿼리 3개, 상한 같음). `_`로 시작하는 열은 그래프 전용 원천 열이며 Flow 응답에서 뺀다.
 * - 묶는 기준: 같은 원본(source) 안 거래 ID·프로세스 ID 문자열. 검증된 부모 연결(FK)이 있으면 TRANSACTION은 그 행을 쓴다.
 *   묶음은 화면 표시용이며 부모 연결을 새로 만들거나 저장하지 않는다(002 data_* 행 불변).
 * - 실행(execution)마다 가장 큰 리비전 1건. 판정 PROCESS 실행은 판정에 쓰인 관측 그대로(focus).
 * - 노출 필드는 와이어프레임 선택 노드 원본 정보 항목만. BODY(data_message_bodies)·response_message·원천 시각 원문은 읽지 않는다.
 * - 그래프 전용 추가 조회(최신 판정·TRANSACTION 재시도)는 여기서 하지 않는다(readDecisionGraph만). Flow 요청은 그 비용을 내지 않는다.
 */
async function loadFlowRows(db: Queryable, id: string, limits: FlowLimits) {
  const item = await resolveItem(db, id);
  if (!item) return null;
  const empty = { item, sourceId: null as string | null, tx: null as Record<string, any> | null, processes: [] as Record<string, any>[],
    messages: [] as Record<string, any>[] };
  if (!item.processObservationId) return empty;
  const { rows: [focus] } = await db.query(`SELECT p.observation_id,p.transaction_observation_id,p.transaction_id,o.source_id
      FROM data_processes p JOIN data_observations o ON o.id=p.observation_id WHERE p.observation_id=$1`, [item.processObservationId]);
  if (!focus) return empty;
  const { rows: [tx] } = await db.query(`SELECT t.transaction_id AS "transactionId",t.interface_id AS "interfaceId",
      t.start_channel_id AS "channelId",t.status,t.response_code AS "responseCode",
      t.reported_process_time_ms::text AS "processTimeMs",t.process_count::text AS "processCount",
      t.observation_id AS "_observationId",o.revision_no::text AS "_revisionNo",o.source_id AS "_sourceId"
    FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id
    WHERE ($1::uuid IS NOT NULL AND t.observation_id=$1) OR ($1::uuid IS NULL AND $2::text IS NOT NULL AND t.transaction_id=$2 AND o.source_id=$3)
    ORDER BY (t.observation_id=$1) DESC NULLS LAST,o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id LIMIT 1`,
  [focus.transaction_observation_id, focus.transaction_id, focus.source_id]);
  const { rows: processes } = await db.query(`WITH candidates AS (
      SELECT p.observation_id FROM data_processes p WHERE $2::text IS NOT NULL AND p.transaction_id=$2
      UNION SELECT $1::uuid
    ), latest AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) p.observation_id,p.process_id,p.depend_process_id,p.status,p.response_code,
        p.total_count,p.start_time_raw,(p.observation_id=$1) AS focus,o.execution_id,o.revision_no,p.transaction_id,
        p.transaction_observation_id,p.retry_count
      FROM candidates c JOIN data_processes p ON p.observation_id=c.observation_id
      JOIN data_observations o ON o.id=p.observation_id AND o.source_id=$3 AND o.layer='P'
      ORDER BY coalesce(o.execution_id,o.id),(p.observation_id=$1) DESC,o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT l.observation_id AS "observationId",l.process_id AS "processId",l.depend_process_id AS "dependsOn",l.status,
      l.response_code AS "responseCode",l.total_count::text AS "totalCount",l.focus,
      count(*) OVER ()::integer AS "_total",(count(*) FILTER (WHERE l.status='F') OVER ())::integer AS "_errors",
      l.execution_id AS "_executionId",l.revision_no::text AS "_revisionNo",l.transaction_id AS "_transactionId",
      l.transaction_observation_id AS "_transactionObservationId",l.retry_count::text AS "_retryCount"
    FROM latest l
    ORDER BY l.start_time_raw NULLS LAST,l.process_id NULLS LAST,l.observation_id LIMIT $4`,
  [focus.observation_id, focus.transaction_id, focus.source_id, limits.processes + 1]);
  const { rows: messages } = focus.transaction_id === null ? { rows: [] } : await db.query(`WITH latest AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) m.observation_id,m.message_id,m.process_id,m.direction,m.status,
        m.data_size,m.message_index,o.execution_id,o.revision_no,m.process_observation_id,m.transaction_id
      FROM data_messages m JOIN data_observations o ON o.id=m.observation_id AND o.source_id=$2 AND o.layer='M'
      WHERE m.transaction_id=$1
      ORDER BY coalesce(o.execution_id,o.id),o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT observation_id AS "observationId",message_id AS "messageId",process_id AS "processId",direction,status,
      data_size::text AS "dataSize",count(*) OVER ()::integer AS "_total",
      execution_id AS "_executionId",revision_no::text AS "_revisionNo",process_observation_id AS "_processObservationId",
      transaction_id AS "_transactionId",message_index::text AS "_messageIndex"
    FROM latest ORDER BY process_id NULLS LAST,message_index NULLS LAST,message_id NULLS LAST,observation_id LIMIT $3`,
  [focus.transaction_id, focus.source_id, limits.messages + 1]);
  return { item, sourceId: focus.source_id as string, tx: tx ?? null, processes, messages };
}

const strip = <T extends Record<string, unknown>>(row: T) =>
  Object.fromEntries(Object.entries(row).filter(([key]) => !key.startsWith('_')));
const flowSummary = (loaded: NonNullable<Awaited<ReturnType<typeof loadFlowRows>>>, limits: FlowLimits) => ({
  truncated: loaded.processes.length > limits.processes || loaded.messages.length > limits.messages,
  // 상한과 무관한 실제 건수(화면 머리 `(프로세스 N건 · 메시지 M건 · 에러 K건)`). 에러 = 상태 F PROCESS.
  counts: { processes: loaded.processes[0]?._total ?? 0, messages: loaded.messages[0]?._total ?? 0, errors: loaded.processes[0]?._errors ?? 0 },
});

/** GET /api/analysis/decisions/:id/flow: 판정 PROCESS가 속한 거래의 TRANSACTION·PROCESS·MESSAGE 저장값(조회 시점). */
export async function readDecisionFlow(db: Queryable, id: string,
  limits: FlowLimits = { processes: FLOW_PROCESS_LIMIT, messages: FLOW_MESSAGE_LIMIT }) {
  const loaded = await loadFlowRows(db, id, limits);
  if (!loaded) return null;
  return {
    id: loaded.item.id,
    jobId: loaded.item.jobId,
    transaction: loaded.tx ? strip(loaded.tx) : null,
    processes: loaded.processes.slice(0, limits.processes).map(strip),
    messages: loaded.messages.slice(0, limits.messages).map(strip),
    ...flowSummary(loaded, limits),
  };
}

/**
 * GET /api/analysis/decisions/:id/graph: 같은 조회 결과로 만든 거래 내부 실행 그래프(층 A, contracts/process-graph.md).
 * 응답 `{id, jobId, truncated, counts, graph, nodes, edges}`. 상한·묶는 기준·노출 필드는 Flow와 같다.
 * Flow 조회 뒤 그래프 전용 조회 2개를 더한다(Flow 경로는 하지 않음):
 * - 상한 안 PROCESS 시도(최대 FLOW_PROCESS_LIMIT건)마다 그 관측의 최신 최종 판정 1건(018 인덱스 순서 decided_at DESC, id DESC,
 *   운영 리포트와 같음)의 ID·v4 등급
 * - 같은 원본·같은 거래 ID의 TRANSACTION 실행마다 최신 리비전, 재시도 번호 순 앞 GRAPH_RETRY_LIMIT건(넘으면 retriesTruncated=true)
 */
export async function readDecisionGraph(db: Queryable, id: string,
  limits: FlowLimits = { processes: FLOW_PROCESS_LIMIT, messages: FLOW_MESSAGE_LIMIT }, now = () => new Date(),
  retryLimit = GRAPH_RETRY_LIMIT) {
  const loaded = await loadFlowRows(db, id, limits);
  if (!loaded) return null;
  const summary = flowSummary(loaded, limits);
  const tx = loaded.tx;
  const processRows = loaded.processes.slice(0, limits.processes);
  const { rows: decisionRows } = processRows.length === 0 ? { rows: [] as Record<string, any>[] } : await db.query(`SELECT
      o.id AS "observationId",d.id AS "decisionId",
      CASE WHEN d.evidence->'schemaVersion'='4'::jsonb AND jsonb_typeof(d.evidence->'composite')='object'
            AND d.evidence->'composite'->>'grade' IN ('anomaly','anomaly_sign','normal','undetermined')
           THEN d.evidence->'composite'->>'grade' END AS grade
    FROM unnest($1::uuid[]) AS o(id) JOIN LATERAL (SELECT x.id,x.evidence FROM run_process_decisions x WHERE x.process_observation_id=o.id
      ORDER BY x.decided_at DESC,x.id DESC LIMIT 1) d ON true`, [processRows.map((p) => p.observationId)]);
  const latestDecision = new Map(decisionRows.map((r) => [r.observationId as string, r]));
  // 거래 ID가 없음(graph_value_absent: NULL·공백·""·"\N")이면 같은 문자열의 무관한 거래를 모으지 않도록 조회하지 않는다.
  const keyedTx = tx !== null && !graphValueAbsent(tx.transactionId, 'TRANSACTION_ID');
  const { rows: retryRows } = !keyedTx ? { rows: [] as Record<string, any>[] } : await db.query(`SELECT
      r.retry_count::text AS "retryCount",r.status,r.observation_id AS "observationId"
    FROM (SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) t.observation_id,t.retry_count,t.status
      FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id AND o.source_id=$2 AND o.layer='T'
      WHERE t.transaction_id=$1
      ORDER BY coalesce(o.execution_id,o.id),o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id) r
    ORDER BY r.retry_count NULLS LAST,r.observation_id LIMIT $3`, [tx!.transactionId, tx!._sourceId, retryLimit + 1]);
  // 거래 노드 대표 행(process-graph.md "거래 노드 대표 행", Detector relation-rules.md 7.2와 같음): 실행마다 최신 리비전 중 재시도 번호가 가장 큰 실행.
  // 재시도 번호 큰 순(번호 없음 먼저) 앞 2건 + 실행 수·실행 id 없음 수·재시도 번호 없음 수만 읽는다(재시도가 많아도 2행).
  const { rows: topRows } = !keyedTx ? { rows: [] as Record<string, any>[] } : await db.query(`WITH r AS (
      SELECT DISTINCT ON (coalesce(o.execution_id,o.id)) t.observation_id,o.execution_id,o.revision_no,t.retry_count,t.transaction_id,
        t.interface_id,t.start_channel_id,t.status,t.response_code,t.reported_process_time_ms,t.process_count
      FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id AND o.source_id=$2 AND o.layer='T'
      WHERE t.transaction_id=$1
      ORDER BY coalesce(o.execution_id,o.id),o.revision_no DESC NULLS LAST,o.persisted_at DESC,o.id
    )
    SELECT observation_id AS "observationId",revision_no::text AS "revisionNo",retry_count::text AS "retryCount",
      transaction_id AS "transactionId",interface_id AS "interfaceId",start_channel_id AS "channelId",status,response_code AS "responseCode",
      reported_process_time_ms::text AS "processTimeMs",process_count::text AS "processCount",
      count(*) OVER ()::integer AS "_executions",(count(*) FILTER (WHERE execution_id IS NULL) OVER ())::integer AS "_missingExecutionId",
      (count(*) FILTER (WHERE retry_count IS NULL) OVER ())::integer AS "_missingRetryCount"
    FROM r ORDER BY retry_count DESC NULLS FIRST,observation_id LIMIT 2`, [tx!.transactionId, tx!._sourceId]);
  const picked = pickTransactionRow({ executions: topRows[0]?._executions ?? 0, missingExecutionId: topRows[0]?._missingExecutionId ?? 0,
    missingRetryCount: topRows[0]?._missingRetryCount ?? 0, top: topRows });
  // 대표 = picked.row. 같은 문자열 행이 없으면(실행 0건 — 원본이 다른 FK 행 등) 읽어 둔 행, 모호면 대표 값 없이 거래 ID만(상태 null = 화면 보류).
  const representative = picked.row ? strip(picked.row)
    : tx && !picked.ambiguous ? { observationId: tx._observationId, revisionNo: tx._revisionNo, transactionId: tx.transactionId,
      interfaceId: tx.interfaceId, channelId: tx.channelId, status: tx.status, responseCode: tx.responseCode,
      processTimeMs: tx.processTimeMs, processCount: tx.processCount }
    : tx ? { observationId: null, revisionNo: null, transactionId: tx.transactionId, interfaceId: null, channelId: null, status: null,
      responseCode: null, processTimeMs: null, processCount: null } : null;
  const graph = buildInstanceGraph({
    sourceId: loaded.sourceId,
    transaction: representative ? { ...(representative as Omit<GraphTransactionRow, 'retries'>),
      retries: retryRows.slice(0, retryLimit).map((r) => ({ retryCount: r.retryCount, status: r.status, observationId: r.observationId })),
      retriesTruncated: retryRows.length > retryLimit, ambiguous: picked.ambiguous } : null,
    processes: processRows.map((p) => ({ observationId: p.observationId, executionId: p._executionId,
      revisionNo: p._revisionNo, transactionId: p._transactionId, transactionObservationId: p._transactionObservationId,
      processId: p.processId, dependsOn: p.dependsOn, status: p.status, responseCode: p.responseCode, totalCount: p.totalCount,
      retryCount: p._retryCount, focus: p.focus === true, decisionId: latestDecision.get(p.observationId)?.decisionId ?? null,
      grade: latestDecision.get(p.observationId)?.grade ?? null })),
    messages: loaded.messages.slice(0, limits.messages).map((m) => ({ observationId: m.observationId, executionId: m._executionId,
      revisionNo: m._revisionNo, processObservationId: m._processObservationId, transactionId: m._transactionId,
      processId: m.processId, messageId: m.messageId, direction: m.direction, status: m.status, dataSize: m.dataSize,
      messageIndex: m._messageIndex })),
    processesTruncated: loaded.processes.length > limits.processes,
    messagesTruncated: loaded.messages.length > limits.messages,
    generatedAt: now().toISOString(),
  });
  return { id: loaded.item.id, jobId: loaded.item.jobId, ...summary, ...graph };
}
