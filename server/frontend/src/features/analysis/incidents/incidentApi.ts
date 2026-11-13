import { HttpError, httpClient } from "@/services/api/client";

/**
 * 사건 보기 읽기 API(contracts/incidents-ui.md) 요청·응답. 명시한 필드만 복사하고 형식이 틀린 값은 버린다.
 * 오류(400·401·404·503)는 httpClient가 HttpError로 던진다. 성공·빈 목록으로 바꾸지 않는다.
 */
/** 첫 값이 기본 기간. 기본 = 전체(과거 CSV 재생 데이터는 최근 7일에 사건이 없음, 사용자 11/13). */
export const incidentPeriods = ["all", "24h", "7d", "30d"] as const;
export type IncidentPeriod = (typeof incidentPeriods)[number];
const periodMs: Record<IncidentPeriod, number | null> = { "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000, all: null };
/** 흐름 flags(contracts/incidents-ui.md 2절, 순서 고정). 쓰지 않은 연결은 원인별로 온다. */
export const incidentFlags = [
  "merge_mismatch",
  "merge_cycle",
  "merge_target_missing",
  "continuation_mismatch",
  "continuation_within_segment",
  "continuation_target_missing",
  "continuation_branched",
  "continuation_cycle",
  "member_source_mismatch",
] as const;
export type IncidentFlag = (typeof incidentFlags)[number];
export type IncidentGrade = "anomaly" | "anomaly_sign" | "normal" | "undetermined";
const grades = ["anomaly", "anomaly_sign", "normal", "undetermined"] as const;

export type IncidentCounts = {
  transactions: number;
  releasedTransactions: number;
  decisions: number;
  releasedDecisions: number;
  missingTransactionId: number;
  /** 최신 판정 등급을 몰라(v3·형식 오류) 해제된 판정 수(releasedDecisions에 포함, Detector incidents.md 8절). */
  unknownGrade: number;
};
/** 구성원 시각 출처별 판정 수(Detector incidents.md 3절 occurred_basis, 해제 포함). */
export type IncidentOccurredBasis = { processTimeRaw: number; decidedAt: number };
/** 거래 줄 시각 출처: 원천 시각 · 판정 시각 대체 · 섞임. */
export type IncidentRowBasis = "process_time_raw" | "decided_at" | "mixed";
/** value: 원래 기준값(긴 값은 API가 원천 행에서 찾은 값, 못 찾으면 null). longValue: 저장 키에 해시만 있음(Detector 4.2). */
export type IncidentCriteria = { kind: string | null; value: string | null; longValue: boolean; version: string | null };
export type IncidentItem = {
  id: string;
  criteria: IncidentCriteria;
  startAt: string;
  endAt: string;
  durationMs: number;
  counts: IncidentCounts;
  occurredBasis: IncidentOccurredBasis;
  chain: { length: number; mergedIncidents: number };
  flags: IncidentFlag[];
  /** 흐름이 읽기 상한 밖 사건을 가리킴(일부만일 수 있음). */
  truncated: boolean;
};
export type IncidentList = { observedAt: string | null; truncated: boolean; total: number; nextCursor: string | null; items: IncidentItem[] };
export type IncidentSegment = {
  id: string;
  openedAt: string;
  endAt: string;
  durationMs: number;
  mergedIncidentIds: string[];
  continuesFrom: string[];
  counts: IncidentCounts;
  occurredBasis: IncidentOccurredBasis;
};
export type IncidentDetail = Omit<IncidentItem, "chain" | "truncated"> & {
  requestedId: string;
  truncated: boolean;
  criteria: IncidentCriteria & { gapSeconds: number | null; maxSpanSeconds: number | null };
  segments: IncidentSegment[];
};
export type IncidentMemberDecision = {
  decisionId: string;
  segmentId: string | null;
  occurredAt: string | null;
  occurredBasis: "process_time_raw" | "decided_at" | null;
  processId: string | null;
  grade: IncidentGrade | null;
  released: boolean;
  latestDecisionId: string | null;
  latestGrade: IncidentGrade | null;
  /** 최신 판정 등급을 모름(이상·이상 징후가 아니므로 해제, Detector 8절). */
  gradeUnknown: boolean;
  /** 실행의 자리 묶음 id(병합 뿌리, Detector 8절). 이 구간이 아니면 released(표시는 해제와 같음). 화면 표시는 아직 쓰지 않음. */
  homeIncidentId: string | null;
};
export type IncidentMemberRow = {
  key: string;
  transactionId: string | null;
  interfaceId: string | null;
  firstOccurredAt: string | null;
  lastOccurredAt: string | null;
  occurredBasis: IncidentRowBasis | null;
  released: boolean;
  decisionCount: number;
  decisionsTruncated: boolean;
  decisions: IncidentMemberDecision[];
};
export type IncidentMembers = { id: string; total: number; nextCursor: string | null; items: IncidentMemberRow[] };

const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const time = (v: unknown): string | null => (typeof v === "string" && Number.isFinite(Date.parse(v)) ? v : null);
const count = (v: unknown): number => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : 0);
const oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | null =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : null;
const fail = (what: string): never => {
  throw new Error(`${what} 응답을 확인할 수 없습니다.`);
};
const seconds = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);

const countKeys = ["transactions", "releasedTransactions", "decisions", "releasedDecisions", "missingTransactionId", "unknownGrade"] as const;
const isCount = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0;
/** 건수는 모두 필수(0 이상 정수). 하나라도 없거나 형식이 틀리면 응답 오류(0으로 바꾸지 않음). */
const parseCounts = (v: unknown, what: string): IncidentCounts => {
  if (!record(v) || !countKeys.every((key) => isCount(v[key]))) return fail(what);
  return Object.fromEntries(countKeys.map((key) => [key, v[key]])) as IncidentCounts;
};
/** 시각 출처 수도 필수(0 이상 정수). 없거나 형식이 틀리면 응답 오류. */
const parseBasis = (v: unknown, what: string): IncidentOccurredBasis =>
  record(v) && isCount(v.processTimeRaw) && isCount(v.decidedAt) ? { processTimeRaw: v.processTimeRaw, decidedAt: v.decidedAt } : fail(what);
const rowBases = ["process_time_raw", "decided_at", "mixed"] as const;
const parseCriteria = (v: unknown): IncidentCriteria => {
  const c = record(v) ? v : {};
  return { kind: text(c.kind), value: typeof c.value === "string" ? c.value : null, longValue: c.longValue === true, version: text(c.version) };
};
const parseFlags = (v: unknown): IncidentFlag[] =>
  Array.isArray(v) ? incidentFlags.filter((flag) => v.includes(flag)) : [];
const ids = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.length > 0) : []);

export const parseIncidentItem = (v: unknown): IncidentItem => {
  if (!record(v) || !text(v.id) || !time(v.startAt) || !time(v.endAt)) return fail("사건");
  const chain = record(v.chain) ? v.chain : {};
  return {
    id: v.id as string,
    criteria: parseCriteria(v.criteria),
    startAt: v.startAt as string,
    endAt: v.endAt as string,
    durationMs: count(v.durationMs),
    counts: parseCounts(v.counts, "사건"),
    occurredBasis: parseBasis(v.occurredBasis, "사건"),
    chain: { length: Math.max(1, count(chain.length)), mergedIncidents: count(chain.mergedIncidents) },
    flags: parseFlags(v.flags),
    truncated: v.truncated === true,
  };
};
export const parseIncidentList = (v: unknown): IncidentList => {
  if (!record(v) || !Array.isArray(v.items)) return fail("사건 목록");
  return {
    observedAt: time(v.observedAt),
    truncated: v.truncated === true,
    total: count(v.total),
    nextCursor: text(v.nextCursor),
    items: v.items.map(parseIncidentItem),
  };
};
export const parseIncidentDetail = (v: unknown): IncidentDetail => {
  if (!record(v) || !Array.isArray(v.segments)) return fail("사건");
  const item = parseIncidentItem({ ...v, chain: { length: v.segments.length } });
  const document = record(v.criteria) && record(v.criteria.document) ? v.criteria.document : {};
  return {
    id: item.id,
    criteria: { ...item.criteria, gapSeconds: seconds(document.gapSeconds), maxSpanSeconds: seconds(document.maxSpanSeconds) },
    startAt: item.startAt,
    endAt: item.endAt,
    durationMs: item.durationMs,
    counts: item.counts,
    occurredBasis: item.occurredBasis,
    flags: item.flags,
    requestedId: text(v.requestedId) ?? item.id,
    truncated: v.truncated === true,
    segments: v.segments.flatMap((s) =>
      record(s) && text(s.id) && time(s.openedAt) && time(s.endAt)
        ? [
            {
              id: s.id as string,
              openedAt: s.openedAt as string,
              endAt: s.endAt as string,
              durationMs: count(s.durationMs),
              mergedIncidentIds: ids(s.mergedIncidentIds),
              continuesFrom: ids(s.continuesFrom),
              counts: parseCounts(s.counts, "사건 구간"),
              occurredBasis: parseBasis(s.occurredBasis, "사건 구간"),
            },
          ]
        : [],
    ),
  };
};
const parseDecision = (v: unknown): IncidentMemberDecision[] =>
  record(v) && text(v.decisionId)
    ? [
        {
          decisionId: v.decisionId as string,
          segmentId: text(v.segmentId),
          occurredAt: time(v.occurredAt),
          occurredBasis: oneOf(v.occurredBasis, ["process_time_raw", "decided_at"] as const),
          processId: text(v.processId),
          grade: oneOf(v.grade, grades),
          released: v.released === true,
          latestDecisionId: text(v.latestDecisionId),
          latestGrade: oneOf(v.latestGrade, grades),
          gradeUnknown: v.gradeUnknown === true,
          homeIncidentId: text(v.homeIncidentId),
        },
      ]
    : [];
export const parseIncidentMembers = (v: unknown): IncidentMembers => {
  if (!record(v) || !text(v.id) || !Array.isArray(v.items)) return fail("사건 구성원");
  return {
    id: v.id as string,
    total: count(v.total),
    nextCursor: text(v.nextCursor),
    items: v.items.flatMap((row) =>
      record(row) && text(row.key)
        ? [
            {
              key: row.key as string,
              transactionId: typeof row.transactionId === "string" ? row.transactionId : null,
              interfaceId: text(row.interfaceId),
              firstOccurredAt: time(row.firstOccurredAt),
              lastOccurredAt: time(row.lastOccurredAt),
              occurredBasis: oneOf(row.occurredBasis, rowBases),
              released: row.released === true,
              decisionCount: count(row.decisionCount),
              decisionsTruncated: row.decisionsTruncated === true,
              decisions: Array.isArray(row.decisions) ? row.decisions.flatMap(parseDecision) : [],
            },
          ]
        : [],
    ),
  };
};

/** 목록 기본: 해제되지 않은 거래 2건 미만 흐름 숨김(사용자 선호 추천, 되돌릴 수 있음, contracts 3절). "모두 보기" = 주소 `all=1`. */
export const INCIDENT_DEFAULT_MIN_TRANSACTIONS = 2;
export type IncidentSearch = { period: IncidentPeriod; cursor: string | null; all: boolean };
/** 화면 주소 쿼리 → 목록 조건(period·cursor·all). 잘못된 값은 기본값(invalid=true면 화면이 replace로 정규화). */
export const parseIncidentSearch = (search: string | URLSearchParams): IncidentSearch & { invalid: boolean } => {
  const query = new URLSearchParams(search);
  const raw = query.get("period");
  const period = oneOf(raw, incidentPeriods) ?? incidentPeriods[0];
  const cursor = query.get("cursor");
  const all = query.get("all");
  return { period, cursor: cursor && /^[A-Za-z0-9_-]{1,512}$/.test(cursor) ? cursor : null, all: all === "1",
    invalid: (raw !== null && period !== raw) || (cursor !== null && !/^[A-Za-z0-9_-]{1,512}$/.test(cursor)) || (all !== null && all !== "1") };
};
export const incidentSearch = ({ period, cursor, all }: IncidentSearch) => {
  const query = new URLSearchParams();
  if (period !== incidentPeriods[0]) query.set("period", period);
  if (all) query.set("all", "1");
  if (cursor) query.set("cursor", cursor);
  const value = query.toString();
  return value ? `?${value}` : "";
};
/** 기간 시작(분 단위로 내림 — 같은 분 안에서는 같은 조회 키). 전체 = null. */
export const periodFrom = (period: IncidentPeriod, now = Date.now()): string | null => {
  const span = periodMs[period];
  return span === null ? null : new Date(Math.floor(now / 60_000) * 60_000 - span).toISOString();
};

export const INCIDENT_PAGE_SIZE = 20;
export const INCIDENT_MEMBER_PAGE_SIZE = 50;
export const readIncidentList = async (params: { from: string | null; cursor: string | null; all: boolean }, signal?: AbortSignal) =>
  parseIncidentList(
    await httpClient.get<unknown>("/api/analysis/incidents", {
      signal,
      params: {
        from: params.from ?? undefined,
        minTransactions: params.all ? undefined : INCIDENT_DEFAULT_MIN_TRANSACTIONS,
        cursor: params.cursor ?? undefined,
        limit: INCIDENT_PAGE_SIZE,
      },
    }),
  );
export const readIncidentDetail = async (id: string, signal?: AbortSignal) =>
  parseIncidentDetail(await httpClient.get<unknown>(`/api/analysis/incidents/${encodeURIComponent(id)}`, { signal }));
export const readIncidentMembers = async (id: string, cursor: string | null, signal?: AbortSignal) =>
  parseIncidentMembers(
    await httpClient.get<unknown>(`/api/analysis/incidents/${encodeURIComponent(id)}/members`, {
      signal,
      params: { cursor: cursor ?? undefined, limit: INCIDENT_MEMBER_PAGE_SIZE },
    }),
  );

export const incidentErrorTitle = (error: unknown, what: string) =>
  error instanceof HttpError && error.status === 404 && error.code === "incident_not_found"
    ? `${what} · ${error.code}`
    : error instanceof HttpError && (error.status === 404 || error.status === 503)
      ? `${what} 미연결`
      : error instanceof Error && error.message
        ? error.message
        : `${what} 조회 실패`;
