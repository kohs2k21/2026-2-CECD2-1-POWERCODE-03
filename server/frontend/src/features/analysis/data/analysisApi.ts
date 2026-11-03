import { HttpError, httpClient } from "@/services/api/client";

/**
 * 이상 분석 읽기 API(analysis-ui.md) 요청·응답. 명시한 필드만 복사하고 형식이 틀린 값은 버린다(null = 확인 불가).
 * 오류(400·401·404·503)는 httpClient가 HttpError로 던진다. 성공·빈 목록으로 바꾸지 않는다.
 */
/** 사이드바 판정 결과별 분류(와이어프레임 순서: 전체 / 이상 / [이상 징후 — 보류] / 판정 중·중간 판정 / 정상 여부 미확인 / 판정 실패·대체). */
export const categories = ["all", "anomaly", "in_progress", "undetermined", "failed_replaced"] as const;
export const signals = ["all", "yes", "no", "unknown"] as const;
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
export const defaultListParams: ListParams = {
  category: "all", q: "", signal: "all", completeness: "all", sort: "newest", page: 1, pageSize: 10,
};

/** 화면 주소 쿼리 → 목록 조건. 잘못된 값은 기본값(invalid=true면 화면이 replace로 정규화). */
export const parseListSearch = (search: string | URLSearchParams): { params: ListParams; invalid: boolean } => {
  const query = new URLSearchParams(search);
  let invalid = false;
  const pick = <T extends string>(key: string, allowed: readonly T[]): T => {
    const value = query.get(key);
    if (value === null) return allowed[0];
    if ((allowed as readonly string[]).includes(value)) return value as T;
    invalid = true;
    return allowed[0];
  };
  const category = pick("category", categories);
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
  };
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

export const readDecisionList = async (params: ListParams, signal?: AbortSignal) =>
  parseList(await httpClient.get<unknown>("/api/analysis/decisions", { signal, params: listApiParams(params) }));
export const readDecisionDetail = async (id: string, signal?: AbortSignal) =>
  parseDetail(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}`, { signal }), id);
export const readDecisionInterims = async (id: string, signal?: AbortSignal) =>
  parseInterims(await httpClient.get<unknown>(`/api/analysis/decisions/${encodeURIComponent(id)}/interims`, { signal }), id);

/** 오류 제목: 404 decision_not_found = 오류 코드 그대로(기존 문구에 없는 판정 없음 표현을 만들지 않음), 그 외 404·503 = 미연결, 그 밖 = 서버 메시지 또는 조회 실패. */
export const analysisErrorTitle = (error: unknown, what: string) =>
  error instanceof HttpError && error.status === 404 && error.code === "decision_not_found"
    ? `${what} · ${error.code}`
    : error instanceof HttpError && (error.status === 404 || error.status === 503)
      ? `${what} 미연결`
      : error instanceof Error && error.message
        ? error.message
        : `${what} 조회 실패`;
