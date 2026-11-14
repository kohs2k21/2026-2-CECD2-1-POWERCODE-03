import { HttpError, httpClient } from "@/services/api/client";

/**
 * 이상 분석 읽기 API(analysis-ui.md) 요청·응답. 명시한 필드만 복사하고 형식이 틀린 값은 버린다(null = 확인 불가).
 * 오류(400·401·404·503)는 httpClient가 HttpError로 던진다. 성공·빈 목록으로 바꾸지 않는다.
 */
/**
 * 사이드바 판정 결과별 분류(와이어프레임 CATS 순서 + 정상: 전체 / 이상 / 이상 징후 / 정상 / 판정 중·중간 판정 / 정상 여부 미확인 / 판정 실패·대체).
 * 정상 = 이상 신호 없음(signal=no, 사용자 11/13).
 */
export const resultCategories = ["all", "anomaly", "anomaly_sign", "normal", "in_progress", "undetermined", "failed_replaced"] as const;
/** 위험도별 보기(와이어프레임 RISK_CATS: 전체 / 위험 / 주의 / 참고 / 위험도 미정). 전체 = 이상 신호가 있는 항목. */
export const riskCategories = ["risk_all", "risk_critical", "risk_warning", "risk_info", "risk_unrated"] as const;
/** 분류함(와이어프레임 WORKFLOW: 보류 / 완료). 두 보기 모두에서 아래 별도 구역. */
export const workflowCategories = ["workflow_open", "workflow_resolved"] as const;
export const categories = [...resultCategories, ...riskCategories, ...workflowCategories] as const;
/** 검색 필터 이상 신호(와이어프레임 fSignal: 전체 / 이상 / 이상 징후 / 이상 신호 없음 / 정상 여부 미확인). */
export const signalFilterOptions = ["all", "anomaly", "anomaly_sign", "no", "unknown"] as const;
/** 주소에서 받는 값: 위 + 이전 주소의 `yes`(이상 신호 있음 = 이상 + 이상 징후, 서버도 그대로 받음). */
export const signals = [...signalFilterOptions, "yes"] as const;
export const completenessOptions = ["all", "complete", "partial", "unavailable"] as const;
export const sorts = ["newest", "oldest"] as const;
export const pageSizes = ["10", "5", "20"] as const;
export type Category = (typeof categories)[number];
export type SignalFilter = (typeof signals)[number];
export type CompletenessFilter = (typeof completenessOptions)[number];
export type Sort = (typeof sorts)[number];

export type ListParams = {
  category: Category;
  q: string;
  signal: SignalFilter;
  completeness: CompletenessFilter;
  sort: Sort;
  page: number;
  pageSize: number;
};
/** 기본 분류 = 위험도별 전체(와이어프레임 analysisCat 'risk:all', 사용자 11/03). 판정 결과별 전체는 주소에 `category=all`로 남는다. */
export const defaultListParams: ListParams = {
  category: "risk_all", q: "", signal: "all", completeness: "all", sort: "newest", page: 1, pageSize: 10,
};

/** 화면 주소 쿼리 → 목록 조건. 잘못된 값은 기본값(invalid=true면 화면이 replace로 정규화). */
export const parseListSearch = (search: string | URLSearchParams): { params: ListParams; invalid: boolean } => {
  const query = new URLSearchParams(search);
  let invalid = false;
  const pick = <T extends string>(key: string, allowed: readonly T[], fallback: T = allowed[0]): T => {
    const value = query.get(key);
    if (value === null) return fallback;
    if ((allowed as readonly string[]).includes(value)) return value as T;
    invalid = true;
    return fallback;
  };
  const category = pick("category", categories, defaultListParams.category);
  const signal = pick("signal", signals);
  const completeness = pick("completeness", completenessOptions);
  const sort = pick("sort", sorts);
  const pageSize = Number(pick("pageSize", pageSizes));
  const rawPage = query.get("page");
  let page = 1;
  if (rawPage !== null) {
    if (/^[1-9]\d{0,5}$/.test(rawPage)) page = Number(rawPage);
    else invalid = true;
  }
  const q = (query.get("q") ?? "").slice(0, 100);
  if ((query.get("q") ?? "").length > 100) invalid = true;
  return { params: { category, q, signal, completeness, sort, page, pageSize }, invalid };
};

/** 목록 조건 → 화면 주소 쿼리(기본값은 생략, 다른 쿼리는 보존). */
export const listSearch = (params: ListParams, base: string | URLSearchParams = "") => {
  const query = new URLSearchParams(base);
  const set = (key: string, value: string, fallback: string) => (value === fallback ? query.delete(key) : query.set(key, value));
  set("category", params.category, defaultListParams.category);
  set("q", params.q.trim(), "");
  set("signal", params.signal, "all");
  set("completeness", params.completeness, "all");
  set("sort", params.sort, defaultListParams.sort);
  set("page", String(params.page), "1");
  set("pageSize", String(params.pageSize), String(defaultListParams.pageSize));
  const text = query.toString();
  return text ? `?${text}` : "";
};

/** 목록 조건 → API 쿼리('all'·빈 검색어는 보내지 않음). */
export const listApiParams = (params: ListParams) => ({
  category: params.category === "all" ? undefined : params.category,
  q: params.q.trim() || undefined,
  signal: params.signal === "all" ? undefined : params.signal,
  completeness: params.completeness === "all" ? undefined : params.completeness,
  sort: params.sort === "newest" ? undefined : params.sort,
  page: params.page === 1 ? undefined : params.page,
  pageSize: params.pageSize,
});

export type ItemKind = "final" | "interim" | "pending" | "failed";
export type Grade = "anomaly" | "anomaly_sign" | "normal" | "undetermined";
export type RiskStatus = "rated" | "unrated" | "not_applicable";
export type RiskLevel = "critical" | "warning" | "info";
export const workflowStates = ["detected", "open", "resolved"] as const;
export type WorkflowState = (typeof workflowStates)[number];
export type ItemStage = "final" | "replaced" | "provisional" | "updated" | "pending" | "failed";
export type ItemSignal = "yes" | "no" | "unknown" | "pending" | "failed";
export type DecisionItem = {
  id: string;
  kind: ItemKind;
  stage: ItemStage;
  signal: ItemSignal;
  decisionId: string | null;
  jobId: string | null;
  jobStatus: string | null;
  sourceId: string | null;
  processObservationId: string | null;
  configVersionId: string | null;
  status: "complete" | "partial" | "unavailable" | null;
  ruleStatus: string | null;
  mlStatus: string | null;
  decisionCode: string | null;
  anomalyScore: string | null;
  evidence: Record<string, unknown> | null;
  occurredAt: string | null;
  decidedAt: string | null;
  transactionId: string | null;
  processId: string | null;
  interfaceId: string | null;
  channelId: string | null;
  processStatus: string | null;
  responseCode: string | null;
  /** 응답 코드 사전(룰 설정 › 응답 코드) 메시지. 사전에 없으면 null(contracts/response-codes-ui.md 5절). */
  responseCodeMessage: string | null;
  /** 저장된 v4 composite.grade(서버가 저장값만 읽음). v3·없음 = null. */
  grade: Grade | null;
  /** 저장된 v4 composite.risk(서버가 저장값만 읽음). 이상 신호가 아니면 not_applicable, v3 이상 = unrated. */
  riskStatus: RiskStatus | null;
  riskLevel: RiskLevel | null;
  workflowState: WorkflowState;
  /** 요청자 기준 미확인(목록 응답만, 상세는 null). */
  unread: boolean | null;
  /** 이상 원인 요약(목록 응답만, 서버가 저장 evidence에서 옮김. 이상 신호가 아니거나 상세 응답이면 빈 배열). */
  causes: DecisionCause[];
};
/** 목록 행 이상 원인 한 건(analysis-ui.md `causes`). 해당 없는 값은 null. */
export type DecisionCause = {
  category: "hard" | "soft" | "ml";
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
export type DecisionList = {
  observedAt: string;
  counts: Record<Category, number | null>;
  total: number;
  page: number;
  pageSize: number;
  latestDecidedAt: string | null;
  latestEventId: string | null;
  items: DecisionItem[];
};
export type DecisionDetail = DecisionItem & {
  configuration: null | {
    id: string;
    createdAt: string | null;
    detectionMode: string | null;
    ruleCount: number | null;
    models: { modelKey: string; adapterName: string | null }[];
  };
  job: null | {
    id: string;
    status: string;
    attemptCount: number | null;
    maxAttempts: number | null;
    errorCode: string | null;
    createdAt: string | null;
    finishedAt: string | null;
  };
  explanation: null | { jobId: string; status: string; errorCode: string | null; createdAt: string | null };
  workflow: { state: WorkflowState; changedAt: string | null };
};
export type InterimRecord = {
  id: string;
  attemptNo: number | null;
  seq: number | null;
  kind: "first_anomaly" | "evidence_added" | "superseded" | "no_final";
  evidenceVersion: string | null;
  supersededByAttemptNo: number | null;
  reasonCode: string | null;
  createdAt: string | null;
  payload: Record<string, unknown> | null;
};
export type InterimHistory = { id: string; jobId: string | null; items: InterimRecord[] };

/** UUID 비교: 대소문자 무시(서버는 소문자로 돌려준다). */
export const sameId = (a: string | null, b: string) => a !== null && a.toLowerCase() === b.toLowerCase();
export const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const time = (v: unknown): string | null => (typeof v === "string" && Number.isFinite(Date.parse(v)) ? v : null);
const count = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);
const oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | null =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : null;
const fail = (what: string): never => {
  throw new Error(`${what} 응답을 확인할 수 없습니다.`);
};

const kinds = ["final", "interim", "pending", "failed"] as const;
const stages = ["final", "replaced", "provisional", "updated", "pending", "failed"] as const;
const itemSignals = ["yes", "no", "unknown", "pending", "failed"] as const;
export const parseItem = (value: unknown): DecisionItem => {
  if (!record(value) || !text(value.id)) return fail("판정");
  const kind = oneOf(value.kind, kinds), stage = oneOf(value.stage, stages), signal = oneOf(value.signal, itemSignals);
  if (!kind || !stage || !signal) return fail("판정");
  return {
    id: value.id as string, kind, stage, signal,
    decisionId: text(value.decisionId), jobId: text(value.jobId), jobStatus: text(value.jobStatus), sourceId: text(value.sourceId),
    processObservationId: text(value.processObservationId), configVersionId: text(value.configVersionId),
    status: oneOf(value.status, ["complete", "partial", "unavailable"] as const),
    ruleStatus: text(value.ruleStatus), mlStatus: text(value.mlStatus), decisionCode: text(value.decisionCode),
    anomalyScore: text(value.anomalyScore), evidence: record(value.evidence) ? value.evidence : null,
    occurredAt: time(value.occurredAt), decidedAt: time(value.decidedAt),
    transactionId: text(value.transactionId), processId: text(value.processId), interfaceId: text(value.interfaceId),
    channelId: text(value.channelId), processStatus: text(value.processStatus), responseCode: text(value.responseCode),
    responseCodeMessage: text(value.responseCodeMessage),
    grade: oneOf(value.grade, ["anomaly", "anomaly_sign", "normal", "undetermined"] as const),
    riskStatus: oneOf(value.riskStatus, ["rated", "unrated", "not_applicable"] as const),
    riskLevel: oneOf(value.riskLevel, ["critical", "warning", "info"] as const),
    workflowState: oneOf(value.workflowState, workflowStates) ?? "detected",
    unread: typeof value.unread === "boolean" ? value.unread : null,
    causes: Array.isArray(value.causes) ? value.causes.flatMap(parseCause) : [],
  };
};
/** 원인 한 건: 대분류가 틀리면 버림, 나머지 값은 형식이 맞을 때만. */
const parseCause = (value: unknown): DecisionCause[] => {
  const category = record(value) ? oneOf(value.category, ["hard", "soft", "ml"] as const) : null;
  if (!record(value) || !category) return [];
  return [{
    category, methodId: text(value.methodId), ruleKey: text(value.ruleKey), code: text(value.code), observedStatus: text(value.observedStatus),
    fieldName: text(value.fieldName), operator: text(value.operator), threshold: text(value.threshold), unit: text(value.unit),
    observedValue: text(value.observedValue), modelName: text(value.modelName),
    score: typeof value.score === "number" && Number.isFinite(value.score) ? value.score : null, scoreThreshold: text(value.scoreThreshold),
  }];
};
export const parseList = (value: unknown): DecisionList => {
  if (!record(value) || !record(value.counts) || !Array.isArray(value.items) || count(value.total) === null) return fail("판정 목록");
  const counts = Object.fromEntries(categories.map((key) => [key, count((value.counts as Record<string, unknown>)[key])])) as DecisionList["counts"];
  return {
    observedAt: time(value.observedAt) ?? "",
    counts, total: value.total as number, page: count(value.page) ?? 1, pageSize: count(value.pageSize) ?? 10,
    latestDecidedAt: time(value.latestDecidedAt),
    latestEventId: typeof value.latestEventId === "string" && /^(0|[1-9]\d{0,18})$/.test(value.latestEventId) ? value.latestEventId : null,
    items: value.items.map(parseItem),
  };
};
export const parseDetail = (value: unknown, requested: string): DecisionDetail => {
  const item = parseItem(value);
  const v = value as Record<string, unknown>;
  // 요청한 ID의 판정(또는 그 작업의 최종 판정)이 아닌 응답은 표시하지 않는다.
  if (!sameId(item.id, requested) && !sameId(item.jobId, requested)) return fail("판정 상세");
  const c = record(v.configuration) ? v.configuration : null;
  const j = record(v.job) ? v.job : null;
  const e = record(v.explanation) ? v.explanation : null;
  return {
    ...item,
    configuration: c && text(c.id) ? {
      id: c.id as string, createdAt: time(c.createdAt), detectionMode: text(c.detectionMode), ruleCount: count(c.ruleCount),
      models: Array.isArray(c.models) ? c.models.flatMap((m) => (record(m) && text(m.modelKey) ? [{ modelKey: m.modelKey as string, adapterName: text(m.adapterName) }] : [])) : [],
    } : null,
    job: j && text(j.id) && text(j.status) ? {
      id: j.id as string, status: j.status as string, attemptCount: count(j.attemptCount), maxAttempts: count(j.maxAttempts),
      errorCode: text(j.errorCode), createdAt: time(j.createdAt), finishedAt: time(j.finishedAt),
    } : null,
    explanation: e && text(e.jobId) && text(e.status) ? { jobId: e.jobId as string, status: e.status as string, errorCode: text(e.errorCode), createdAt: time(e.createdAt) } : null,
    workflow: {
      state: (record(v.workflow) ? oneOf(v.workflow.state, workflowStates) : null) ?? item.workflowState,
      changedAt: record(v.workflow) ? time(v.workflow.changedAt) : null,
    },
  };
};
const interimKinds = ["first_anomaly", "evidence_added", "superseded", "no_final"] as const;
export const parseInterims = (value: unknown, requested: string): InterimHistory => {
  if (!record(value) || !text(value.id) || !Array.isArray(value.items)) return fail("판정 기록");
  // 요청한 판정(또는 그 작업)의 기록이 아닌 응답은 표시하지 않는다.
  if (!sameId(value.id as string, requested) && !sameId(text(value.jobId), requested)) return fail("판정 기록");
  return {
    id: value.id as string,
    jobId: text(value.jobId),
    items: value.items.flatMap((item) => {
      const kind = record(item) ? oneOf(item.kind, interimKinds) : null;
      if (!record(item) || !text(item.id) || !kind) return [];
      return [{ id: item.id as string, kind, attemptNo: count(item.attemptNo), seq: count(item.seq), evidenceVersion: text(item.evidenceVersion),
        supersededByAttemptNo: count(item.supersededByAttemptNo), reasonCode: text(item.reasonCode), createdAt: time(item.createdAt),
        payload: record(item.payload) ? item.payload : null }];
    }),
  };
};

/** Process Flow(GET …/flow): 같은 원본 안 같은 거래 ID의 저장값. 와이어프레임 선택 노드 원본 정보 항목만(BODY 없음). */
export type FlowTransaction = {
  transactionId: string | null;
  interfaceId: string | null;
  channelId: string | null;
  status: string | null;
  responseCode: string | null;
  processTimeMs: string | null;
  processCount: string | null;
};
export type FlowProcess = {
  observationId: string;
  processId: string | null;
  dependsOn: string | null;
  status: string | null;
  responseCode: string | null;
  totalCount: string | null;
  focus: boolean;
};
export type FlowMessage = {
  observationId: string;
  messageId: string | null;
  processId: string | null;
  direction: string | null;
  status: string | null;
  dataSize: string | null;
};
/** counts = 상한(PROCESS 200·MESSAGE 500)과 무관한 실제 건수. 응답에 없거나 형식이 틀리면 null(화면은 받은 목록으로 센다). */
export type DecisionFlow = {
  id: string;
  transaction: FlowTransaction | null;
  processes: FlowProcess[];
  messages: FlowMessage[];
  truncated: boolean;
  counts: { processes: number; messages: number; errors: number } | null;
};
export const parseFlow = (value: unknown, requested: string): DecisionFlow => {
  if (!record(value) || !text(value.id) || !Array.isArray(value.processes) || !Array.isArray(value.messages)) return fail("Process Flow");
  // 요청한 판정(또는 그 작업)의 흐름이 아닌 응답은 표시하지 않는다.
  if (!sameId(value.id as string, requested) && !sameId(text(value.jobId), requested)) return fail("Process Flow");
  const t = record(value.transaction) ? value.transaction : null;
  return {
    id: value.id as string,
    transaction: t ? {
      transactionId: text(t.transactionId), interfaceId: text(t.interfaceId), channelId: text(t.channelId), status: text(t.status),
      responseCode: text(t.responseCode), processTimeMs: text(t.processTimeMs), processCount: text(t.processCount),
    } : null,
    processes: value.processes.flatMap((p) => (record(p) && text(p.observationId) ? [{
      observationId: p.observationId as string, processId: text(p.processId), dependsOn: text(p.dependsOn), status: text(p.status),
      responseCode: text(p.responseCode), totalCount: text(p.totalCount), focus: p.focus === true,
    }] : [])),
    messages: value.messages.flatMap((m) => (record(m) && text(m.observationId) ? [{
      observationId: m.observationId as string, messageId: text(m.messageId), processId: text(m.processId), direction: text(m.direction),
      status: text(m.status), dataSize: text(m.dataSize),
    }] : [])),
    truncated: value.truncated === true,
    counts: record(value.counts) && count(value.counts.processes) !== null && count(value.counts.messages) !== null && count(value.counts.errors) !== null
      ? { processes: value.counts.processes as number, messages: value.counts.messages as number, errors: value.counts.errors as number }
      : null,
  };
};

/**
 * 실행 그래프(GET …/graph, contracts/process-graph.md). 층 A(거래 내부) = Process Flow와 같은 조회 결과의 노드·간선.
 * - id는 쪼개 읽지 않는다(구성 요소에 ':'가 이스케이프돼 들어간다). 간선 끝은 `from`·`to`로만 찾는다.
 * - attrs는 종류별 원천 값 그대로(원래 공백 포함) 넘긴다. 어휘 밖 kind·verification인 노드·간선은 버린다.
 */
export const graphLayers = ["instance", "pattern", "incident"] as const;
export const graphNodeKinds = ["transaction", "process", "message", "missing"] as const;
export const graphEdgeKinds = ["contains", "precedes", "hands_off"] as const;
export const graphVerifications = ["not_checked", "unmatched", "verified", "ambiguous", "failed"] as const;
export const handoffResults = ["match", "mismatch", "undetermined"] as const;
export type GraphNodeKind = (typeof graphNodeKinds)[number];
export type GraphEdgeKind = (typeof graphEdgeKinds)[number];
export type GraphVerification = (typeof graphVerifications)[number];
export type HandoffResult = (typeof handoffResults)[number];
export type GraphNode = { id: string; kind: GraphNodeKind; attrs: Record<string, unknown> };
export type GraphEdge = {
  id: string;
  kind: GraphEdgeKind;
  from: string;
  to: string;
  verification: GraphVerification | null;
  evidence: Record<string, unknown> | null;
};
export type GraphHeader = {
  layer: (typeof graphLayers)[number];
  id: string | null;
  version: string | null;
  sourceId: string | null;
  snapshotId: string | null;
  period: { from: string; to: string } | null;
  ruleSetRevision: number | null;
  generatedAt: string | null;
};
export type DecisionGraph = {
  id: string;
  graph: GraphHeader;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  counts: { processes: number; messages: number; errors: number } | null;
};
export const parseGraph = (value: unknown, requested: string): DecisionGraph => {
  if (!record(value) || !text(value.id) || !record(value.graph) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) return fail("실행 순서");
  if (!sameId(value.id as string, requested) && !sameId(text(value.jobId), requested)) return fail("실행 순서");
  const layer = oneOf(value.graph.layer, graphLayers);
  if (!layer) return fail("실행 순서");
  const nodes = value.nodes.flatMap((n): GraphNode[] => {
    const kind = record(n) ? oneOf(n.kind, graphNodeKinds) : null;
    return record(n) && text(n.id) && kind ? [{ id: n.id as string, kind, attrs: record(n.attrs) ? n.attrs : {} }] : [];
  });
  const edges = value.edges.flatMap((e): GraphEdge[] => {
    const kind = record(e) ? oneOf(e.kind, graphEdgeKinds) : null;
    if (!record(e) || !kind || !text(e.id) || !text(e.from) || !text(e.to)) return [];
    const verification = e.verification === null ? null : oneOf(e.verification, graphVerifications);
    if (verification === null && e.verification !== null) return [];
    return [{ id: e.id as string, kind, from: e.from as string, to: e.to as string, verification, evidence: record(e.evidence) ? e.evidence : null }];
  });
  const counts = value.counts, period = value.graph.period;
  return {
    id: value.id as string,
    graph: { layer, id: text(value.graph.id), version: text(value.graph.version), sourceId: text(value.graph.sourceId),
      snapshotId: text(value.graph.snapshotId),
      period: record(period) && time(period.from) && time(period.to) ? { from: period.from as string, to: period.to as string } : null,
      ruleSetRevision: count(value.graph.ruleSetRevision), generatedAt: time(value.graph.generatedAt) },
    nodes, edges,
    truncated: value.truncated === true,
    counts: record(counts) && count(counts.processes) !== null && count(counts.messages) !== null && count(counts.errors) !== null
      ? { processes: counts.processes as number, messages: counts.messages as number, errors: counts.errors as number }
      : null,
  };
};

/** 조사·조치 기록(관리자 전용, 와이어프레임 feedbackBox). 확정 라벨·학습에 반영되지 않는다. */
export const feedbackKinds = ["action", "suspected-false-positive", "insufficient-evidence"] as const;
export type FeedbackKind = (typeof feedbackKinds)[number];
export const FEEDBACK_REASON_MAX = 2000;
/** 확인 결과(029, 기획 "확인 결과(실제 이상·정상·판단 불가)"). null = 미입력. */
export const feedbackOutcomes = ["actual-anomaly", "normal", "undetermined"] as const;
export type FeedbackOutcome = (typeof feedbackOutcomes)[number];
export type FeedbackRecord = {
  id: string;
  kind: FeedbackKind;
  reason: string;
  outcome: FeedbackOutcome | null;
  actorId: string | null;
  actorEmail: string | null;
  createdAt: string | null;
};
const parseFeedbackRecord = (value: unknown): FeedbackRecord[] => {
  const kind = record(value) ? oneOf(value.kind, feedbackKinds) : null;
  if (!record(value) || !text(value.id) || !kind || typeof value.reason !== "string") return [];
  // 확인 결과: 없음·null = 미입력. 알 수 없는 값은 미입력으로 바꾸지 않고 형식이 틀린 항목으로 버린다(기존 규칙).
  const outcome = value.outcome === undefined || value.outcome === null ? null : oneOf(value.outcome, feedbackOutcomes);
  if (outcome === null && value.outcome !== undefined && value.outcome !== null) return [];
  return [{ id: value.id as string, kind, reason: value.reason, outcome, actorId: text(value.actorId), actorEmail: text(value.actorEmail), createdAt: time(value.createdAt) }];
};
export const parseFeedbackList = (value: unknown): { id: string; items: FeedbackRecord[] } => {
  if (!record(value) || !text(value.id) || !Array.isArray(value.items)) return fail("조사·조치 기록");
  return { id: value.id as string, items: value.items.flatMap(parseFeedbackRecord) };
};

export const readDecisionList = async (params: ListParams, signal?: AbortSignal) =>
  parseList(await httpClient.get<unknown>("/api/analysis/decisions", { signal, params: listApiParams(params) }));
export const readDecisionDetail = async (id: string, signal?: AbortSignal) =>
  parseDetail(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}`, { signal }), id);
export const readDecisionInterims = async (id: string, signal?: AbortSignal) =>
  parseInterims(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/interims`, { signal }), id);

export const readDecisionFlow = async (id: string, signal?: AbortSignal) =>
  parseFlow(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/flow`, { signal }), id);
export const readDecisionGraph = async (id: string, signal?: AbortSignal) =>
  parseGraph(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/graph`, { signal }), id);
export const readDecisionFeedback = async (id: string, signal?: AbortSignal) =>
  parseFeedbackList(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/feedback`, { signal }));
/** 처리 상태 변경: 현재 상태(expectedState)가 서버와 다르면 409 workflow_state_conflict. */
export const changeDecisionWorkflow = async (id: string, state: WorkflowState, expectedState: WorkflowState) => {
  const value = await httpClient.put<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/workflow`, { state, expectedState });
  const workflow = record(value) && record(value.workflow) ? oneOf(value.workflow.state, workflowStates) : null;
  if (!workflow) return fail("업무 분류");
  return workflow;
};
/** 조사·조치 기록 저장. 확인 결과를 고르지 않았으면(null) outcome 키를 보내지 않는다(서버는 생략 = 미입력). */
export const saveDecisionFeedback = async (id: string, kind: FeedbackKind, reason: string, outcome: FeedbackOutcome | null = null) => {
  const value = await httpClient.post<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/feedback`,
    outcome === null ? { kind, reason } : { kind, reason, outcome });
  const [item] = record(value) ? parseFeedbackRecord(value.item) : [];
  return item ?? fail("조사·조치 기록");
};
/** 상세를 열면 요청자 기준 확인 처리(읽음 표시). 화면이 본 항목 시각까지만 읽음 → 그 뒤 생긴 판정 기록은 미확인으로 남는다. */
export const markDecisionRead = async (id: string, occurredAt: string) => {
  await httpClient.put<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/read`, { occurredAt });
};

/** 쓰기 오류 제목: `<항목> 실패 · <오류 코드>`(설명 작업 실패 표시와 같은 형식), 코드가 없으면 서버 메시지. */
export const analysisWriteErrorTitle = (error: unknown, what: string) =>
  error instanceof HttpError && error.code
    ? `${what} 실패 · ${error.code}`
    : error instanceof Error && error.message
      ? `${what} 실패 · ${error.message}`
      : `${what} 실패`;

/** 오류 제목: 404 decision_not_found = 오류 코드 그대로(기존 문구에 없는 판정 없음 표현을 만들지 않음), 그 외 404·503 = 미연결, 그 밖 = 서버 메시지 또는 조회 실패. */
export const analysisErrorTitle = (error: unknown, what: string) =>
  error instanceof HttpError && error.status === 404 && error.code === "decision_not_found"
    ? `${what} · ${error.code}`
    : error instanceof HttpError && (error.status === 404 || error.status === 503)
      ? `${what} 미연결`
      : error instanceof Error && error.message
        ? error.message
        : `${what} 조회 실패`;
