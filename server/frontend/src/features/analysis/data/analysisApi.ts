import { HttpError, httpClient } from "@/services/api/client";
import type { EvidenceGroup } from "./evidence";

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
/**
 * 분류함 `완료` 아래 확인 결과별 완료(사용자 11/15): 그 확인 결과(가장 최근 확인 결과 기록)로 기록된 완료 판정. 관리자만(조사·조치 기록이 관리자 전용).
 * 확인 결과가 없는 완료는 `완료`에서만 보인다.
 */
export const outcomeCategories = ["workflow_resolved_actual_anomaly", "workflow_resolved_normal", "workflow_resolved_undetermined"] as const;
export type OutcomeCategory = (typeof outcomeCategories)[number];
export const outcomeOfCategory: Record<OutcomeCategory, FeedbackOutcome> = {
  workflow_resolved_actual_anomaly: "actual-anomaly",
  workflow_resolved_normal: "normal",
  workflow_resolved_undetermined: "undetermined",
};
export const isOutcomeCategory = (category: string): category is OutcomeCategory => (outcomeCategories as readonly string[]).includes(category);
export const categories = [...resultCategories, ...riskCategories, ...workflowCategories, ...outcomeCategories] as const;
/** 검색 필터 이상 신호(와이어프레임 fSignal: 전체 / 이상 / 이상 징후 / 이상 신호 없음 / 정상 여부 미확인). */
export const signalFilterOptions = ["all", "anomaly", "anomaly_sign", "no", "unknown"] as const;
/** 주소에서 받는 값: 위 + 이전 주소의 `yes`(이상 신호 있음 = 이상 + 이상 징후, 서버도 그대로 받음). */
export const signals = [...signalFilterOptions, "yes"] as const;
export const completenessOptions = ["all", "complete", "partial", "unavailable"] as const;
/** 근거 구분 선택(판정 상세 `근거 …`와 같은 구분·순서, evidence.ts evidenceGroups). all = 거르지 않음. */
export const basisOptions = ["all", "hard", "relation", "protocol", "soft", "ml"] as const satisfies readonly ("all" | EvidenceGroup)[];
export const sorts = ["newest", "oldest"] as const;
export const pageSizes = ["10", "5", "20"] as const;
export type Category = (typeof categories)[number];
export type SignalFilter = (typeof signals)[number];
export type CompletenessFilter = (typeof completenessOptions)[number];
export type BasisFilter = (typeof basisOptions)[number];
export type Sort = (typeof sorts)[number];

export type ListParams = {
  category: Category;
  q: string;
  signal: SignalFilter;
  completeness: CompletenessFilter;
  basis: BasisFilter;
  sort: Sort;
  page: number;
  pageSize: number;
};
/** 기본 분류 = 위험도별 전체(와이어프레임 analysisCat 'risk:all', 사용자 11/03). 판정 결과별 전체는 주소에 `category=all`로 남는다. */
export const defaultListParams: ListParams = {
  category: "risk_all", q: "", signal: "all", completeness: "all", basis: "all", sort: "newest", page: 1, pageSize: 10,
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
  const basis = pick("basis", basisOptions);
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
  return { params: { category, q, signal, completeness, basis, sort, page, pageSize }, invalid };
};

/** 목록 조건 → 화면 주소 쿼리(기본값은 생략, 다른 쿼리는 보존). */
export const listSearch = (params: ListParams, base: string | URLSearchParams = "") => {
  const query = new URLSearchParams(base);
  const set = (key: string, value: string, fallback: string) => (value === fallback ? query.delete(key) : query.set(key, value));
  set("category", params.category, defaultListParams.category);
  set("q", params.q.trim(), "");
  set("signal", params.signal, "all");
  set("completeness", params.completeness, "all");
  set("basis", params.basis, "all");
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
  basis: params.basis === "all" ? undefined : params.basis,
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
  workflow: { state: WorkflowState; changedAt: string | null };
  /** AI 판정 근거 요약(서버가 저장 근거로 만든 다섯 블록, analysis-ui.md). 만들 수 없는 판정이면 null(카드 없음). */
  evidenceSummary: EvidenceSummary | null;
} & DecisionPolish & {
  /** 지금 다듬기를 요청해도 되는지(서버가 판정·결과·생성 서비스 상태로 정함, 화면은 [근거 요약 생성]을 누른 뒤 요청). 모르면 false. */
  polishRequestable: boolean;
};
/** 블록 2 다듬은 문단(판정마다 공유). source=code면 코드 문단 그대로 쓴다. */
export type Polished = { text: string; source: "llm" | "code" };
export type PolishState = "pending" | "done" | "none";
/** GET …/polished 응답(판정 상세의 같은 두 키). */
export type DecisionPolish = { polishState: PolishState; polished: Polished | null };
/** evidenceSummary: 화면은 문자열을 그대로 그린다(문장을 만들거나 숫자를 다시 계산하지 않음). */
/** 이번 값 칸 하나: 이름(없으면 null) + 값 + 단위(없으면 null). unitUnknown = 건수 계열(열 머리 툴팁으로 안내). */
export type EvidenceSummaryObserved = { name: string | null; value: string; unit: string | null; unitUnknown: boolean };
export type EvidenceSummaryRuleRow = { kind: "rule"; methodId: string; condition: string; observed: EvidenceSummaryObserved[]; result: string };
export type EvidenceSummaryMlMember = { name: string; actual: string; usual: string; unit: string | null; unitUnknown: boolean };
export type EvidenceSummaryMlRow = { kind: "ml"; methodId: string; playerId: string; members: EvidenceSummaryMlMember[]; impactPercent: string };
export type EvidenceSummary = {
  templateVersion: string;
  summaryLine: { prefix: string; prefixKind: "risk" | "grade" | "signal"; text: string; kind: string };
  sentences: string[];
  table: (EvidenceSummaryRuleRow | EvidenceSummaryMlRow)[];
  checks: { code: string; text: string }[];
  notice: string;
  contributionNote: string | null;
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
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : []);
const prefixKinds = ["risk", "grade", "signal"] as const;
/** 근거 요약 읽기: 한 줄 요약·안내가 없으면 null(카드 없음). 모양이 어긋난 문장·행·구성원은 건너뛴다. */
export const parseEvidenceSummary = (v: unknown): EvidenceSummary | null => {
  if (!record(v) || !record(v.summaryLine) || !text(v.summaryLine.prefix) || !text(v.summaryLine.text) || !text(v.notice)) return null;
  const line = v.summaryLine;
  const table = (Array.isArray(v.table) ? v.table : []).flatMap((row): EvidenceSummary["table"] => {
    if (!record(row) || !text(row.methodId)) return [];
    if (row.kind === "rule" && typeof row.condition === "string" && Array.isArray(row.observed) && typeof row.result === "string") {
      const observed = row.observed.flatMap((o) => (record(o) && typeof o.value === "string"
        ? [{ name: text(o.name), value: o.value, unit: text(o.unit), unitUnknown: o.unitUnknown === true }] : []));
      return [{ kind: "rule", methodId: row.methodId as string, condition: row.condition, observed, result: row.result }];
    }
    if (row.kind !== "ml" || !text(row.playerId) || typeof row.impactPercent !== "string" || !Array.isArray(row.members)) return [];
    const members = row.members.flatMap((m) => (record(m) && typeof m.name === "string" && typeof m.actual === "string" && typeof m.usual === "string"
      ? [{ name: m.name, actual: m.actual, usual: m.usual, unit: text(m.unit), unitUnknown: m.unitUnknown === true }] : []));
    return members.length ? [{ kind: "ml", methodId: row.methodId as string, playerId: row.playerId as string, members, impactPercent: row.impactPercent }] : [];
  });
  return {
    templateVersion: text(v.templateVersion) ?? "",
    summaryLine: { prefix: line.prefix as string, prefixKind: oneOf(line.prefixKind, prefixKinds) ?? "signal", text: line.text as string, kind: text(line.kind) ?? "" },
    sentences: strings(v.sentences),
    table,
    checks: (Array.isArray(v.checks) ? v.checks : []).flatMap((c) => (record(c) && text(c.text) ? [{ code: text(c.code) ?? "", text: c.text as string }] : [])).slice(0, 2),
    notice: v.notice as string,
    contributionNote: text(v.contributionNote),
  };
};
const polishStates = ["pending", "done", "none"] as const;
/** 다듬은 문단·상태 읽기: 모르는 상태는 none, 문단이 문자열이 아니면 null(코드 문단 그대로). */
export const parsePolish = (v: unknown): DecisionPolish => {
  const p = record(v) && record(v.polished) ? v.polished : null;
  const polished = p && typeof p.text === "string" && p.text.trim() && (p.source === "llm" || p.source === "code") ? { text: p.text, source: p.source as Polished["source"] } : null;
  return { polishState: (record(v) ? oneOf(v.polishState, polishStates) : null) ?? "none", polished };
};
export const parseDetail = (value: unknown, requested: string): DecisionDetail => {
  const item = parseItem(value);
  const v = value as Record<string, unknown>;
  // 요청한 ID의 판정(또는 그 작업의 최종 판정)이 아닌 응답은 표시하지 않는다.
  if (!sameId(item.id, requested) && !sameId(item.jobId, requested)) return fail("판정 상세");
  const c = record(v.configuration) ? v.configuration : null;
  const j = record(v.job) ? v.job : null;
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
    workflow: {
      state: (record(v.workflow) ? oneOf(v.workflow.state, workflowStates) : null) ?? item.workflowState,
      changedAt: record(v.workflow) ? time(v.workflow.changedAt) : null,
    },
    evidenceSummary: parseEvidenceSummary(v.evidenceSummary),
    ...parsePolish(v),
    polishRequestable: v.polishRequestable === true,
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
/**
 * 기록 1건 = 서버의 현재 판본(032 수정은 정정 행 추가). `id` = 이 판본(수정 요청의 expectedId), `originId` = 최초 기록(수정 경로 키, 이전 API 응답이면 id),
 * `createdAt` = 최초 기록 시각(판정 기록 위치), `editedAt` = 수정 시각(수정 안 했으면 null), `actorId` = 최초 작성자(수정은 작성자만).
 */
export type FeedbackRecord = {
  id: string;
  originId: string;
  kind: FeedbackKind;
  reason: string;
  outcome: FeedbackOutcome | null;
  actorId: string | null;
  actorEmail: string | null;
  createdAt: string | null;
  editedAt: string | null;
};
const parseFeedbackRecord = (value: unknown): FeedbackRecord[] => {
  const kind = record(value) ? oneOf(value.kind, feedbackKinds) : null;
  if (!record(value) || !text(value.id) || !kind || typeof value.reason !== "string") return [];
  // 확인 결과: 없음·null = 미입력. 알 수 없는 값은 미입력으로 바꾸지 않고 형식이 틀린 항목으로 버린다(기존 규칙).
  const outcome = value.outcome === undefined || value.outcome === null ? null : oneOf(value.outcome, feedbackOutcomes);
  if (outcome === null && value.outcome !== undefined && value.outcome !== null) return [];
  return [{ id: value.id as string, originId: text(value.originId) ?? (value.id as string), kind, reason: value.reason, outcome,
    actorId: text(value.actorId), actorEmail: text(value.actorEmail), createdAt: time(value.createdAt), editedAt: time(value.editedAt) }];
};
export const parseFeedbackList = (value: unknown): { id: string; items: FeedbackRecord[] } => {
  if (!record(value) || !text(value.id) || !Array.isArray(value.items)) return fail("조사·조치 기록");
  return { id: value.id as string, items: value.items.flatMap(parseFeedbackRecord) };
};

export const readDecisionList = async (params: ListParams, signal?: AbortSignal) =>
  parseList(await httpClient.get<unknown>("/api/analysis/decisions", { signal, params: listApiParams(params) }));
/** 다듬은 문단·상태만(판정 상세와 같은 권한). */
export const readDecisionPolished = async (id: string, signal?: AbortSignal): Promise<DecisionPolish> =>
  parsePolish(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/polished`, { signal }));
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
/**
 * 조사·조치 기록 수정(032, 최초 작성자 관리자만): 경로 = 최초 기록 id, expectedId = 화면이 본 현재 판본 id(그 사이 바뀌었으면 409 feedback_edit_conflict).
 * 확인 결과를 비우면(null) outcome 키를 보내지 않는다(서버는 생략 = 미입력). 삭제는 없다.
 */
export const editDecisionFeedback = async (id: string, target: Pick<FeedbackRecord, "id" | "originId">, kind: FeedbackKind, reason: string,
  outcome: FeedbackOutcome | null) => {
  const value = await httpClient.put<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/feedback/${encodeURIComponent(target.originId)}`,
    outcome === null ? { kind, reason, expectedId: target.id } : { kind, reason, outcome, expectedId: target.id });
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
