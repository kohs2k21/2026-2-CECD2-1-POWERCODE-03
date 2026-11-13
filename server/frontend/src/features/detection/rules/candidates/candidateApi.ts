import { httpClient } from "@/services/api/client";

/**
 * 규칙 추천 API(contracts/rule-candidates-ui.md) 요청·응답. 서버가 Trainer 결과를 이미 검사하므로 여기서는 화면이 쓰는 값의 형식만 본다.
 * 형식이 틀리면 빈 목록으로 바꾸지 않고 오류로 던진다. 오류(400·403·404·503)는 httpClient가 HttpError로 던진다.
 */
export type RelationKind = "required_step" | "precedes" | "message_handoff";
export type RelationStatus = "candidate" | "review_needed" | "insufficient_evidence" | "not_candidate";
export type ThresholdStatus = "candidate" | "insufficient_evidence" | "no_variation" | "rejected";
export type Counts = { applicable: number; satisfied: number; violated: number; undetermined: number };
export type CandidateJob = { id: string; status: string; errorCode: string | null; createdAt: string; finishedAt: string | null; snapshotId: string | null };
export type Approval = { available: boolean; reason: string | null; contentHashVersion: number };
export type RelationCandidate = {
  candidateId: string;
  kind: RelationKind;
  status: RelationStatus;
  approvable: boolean;
  listedForPairing: boolean;
  /** message_handoff만: 짝 precedes 후보가 승인 가능한가(저장 경고 미리 보기, Trainer pairApprovable). */
  pairApprovable: boolean | null;
  interfaceId: string;
  scope: Record<string, unknown> | null;
  from: { processId: string };
  to: { processId: string } | null;
  counts: Counts;
  satisfactionRate: number | null;
  evaluatedAt: string | null;
  nonStandardProcessId: boolean;
};
export type ThresholdCandidate = {
  candidateId: string;
  status: ThresholdStatus;
  reason: string | null;
  scope: { processId: string; adapterType?: string };
  interfaceIds: string[];
  operator: string | null;
  threshold: number | null;
  displayThreshold: string | null;
  unit: string;
  source: string | null;
  evidence: {
    n: number; rows: number; referenceRows: number; endTimeMissingReferenceRows: number; unavailableReferenceRows: number;
    p95: number | null; min: number | null; max: number | null; quantileMethod: string | null;
  };
  limitation: string | null;
  nonStandardProcessId: boolean;
};
export type PatternNode = { id: string; kind: "pattern_step"; attrs: { interfaceId: string; processId: string | null; adapterType: string | null; transactions: number | null } };
export type PatternEdge = {
  id: string; kind: string; from: string; to: string;
  evidence: { status: string | null; satisfactionRate: number | null; applicable: number | null; observedTransactions: number | null };
};
export type InterfaceSummary = { interfaceId: string; transactions: number; relationCandidates: number; approvable: number; thresholdCandidates: number };
export type ResultHead = {
  job: CandidateJob;
  snapshotId: string | null;
  sourceId: string | null;
  period: { from: string | null; until: string | null; timeBasis: string | null };
  generatedAt: string | null;
  parameters: { minApplicable: number | null; candidatePercent: number | null; reviewPercent: number | null; maxCounterexamples: number | null };
};
export type CandidateList = {
  latestJob: CandidateJob | null;
  approval: Approval;
  result: (ResultHead & {
    interfaces: InterfaceSummary[];
    selectedInterfaceId: string | null;
    graph: { nodes: PatternNode[]; edges: PatternEdge[] } | null;
    relationCandidates: RelationCandidate[];
    thresholdCandidates: ThresholdCandidate[];
    thresholdsWithoutInterface: number;
    thresholdLimitations: string[];
    totals: { relationCandidates: number; approvable: number; thresholdCandidates: number; thresholdApprovable: number };
  }) | null;
};
export type Counterexample = { transactionId: string | null; reason: string | null; transactionStatus: string | null; decisionId: string | null };
export type RelationDetail = RelationCandidate & {
  violationReasons: Record<string, number>;
  undeterminedReasons: Record<string, number>;
  violationsByTransactionStatus: Record<string, number>;
};
export type CandidateDetail = ResultHead & { isLatest: boolean; approval: Approval } & (
  | { type: "relation"; relation: RelationDetail; counterexamples: Counterexample[];
      pairedPrecedes: { candidateId: string; status: RelationStatus; approvable: boolean; counts: Counts; satisfactionRate: number | null } | null;
      ruleDraft: Record<string, unknown> }
  | { type: "threshold"; threshold: ThresholdCandidate; ruleDraft: Record<string, unknown> | null }
);

type Json = Record<string, unknown>;
const object = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown) => (typeof value === "string" ? value : null);
const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const fail = (what: string): never => {
  throw new Error(`${what} 응답 형식 오류`);
};
const relationKinds: readonly string[] = ["required_step", "precedes", "message_handoff"];
const relationStatuses: readonly string[] = ["candidate", "review_needed", "insufficient_evidence", "not_candidate"];
const thresholdStatuses: readonly string[] = ["candidate", "insufficient_evidence", "no_variation", "rejected"];

const countsOf = (value: unknown, what: string): Counts => {
  if (!object(value) || !["applicable", "satisfied", "violated", "undetermined"].every((key) => count(value[key]))) fail(what);
  const v = value as Json;
  return { applicable: v.applicable as number, satisfied: v.satisfied as number, violated: v.violated as number, undetermined: v.undetermined as number };
};
const jobOf = (value: unknown): CandidateJob => {
  if (!object(value) || !str(value.id) || !str(value.status) || !str(value.createdAt)) fail("후보 작업");
  const v = value as Json;
  return { id: v.id as string, status: v.status as string, errorCode: str(v.errorCode), createdAt: v.createdAt as string, finishedAt: str(v.finishedAt), snapshotId: str(v.snapshotId) };
};
const approvalOf = (value: unknown): Approval => {
  if (!object(value) || typeof value.available !== "boolean" || !count(value.contentHashVersion)) fail("승인 상태");
  const v = value as Json;
  return { available: v.available as boolean, reason: str(v.reason), contentHashVersion: v.contentHashVersion as number };
};
const tally = (value: unknown): Record<string, number> =>
  object(value) ? Object.fromEntries(Object.entries(value).filter(([, n]) => count(n))) as Record<string, number> : {};

export const parseRelation = (value: unknown): RelationCandidate => {
  if (!object(value) || !str(value.candidateId) || !relationKinds.includes(value.kind as string) || !relationStatuses.includes(value.status as string)
    || typeof value.approvable !== "boolean" || !str(value.interfaceId) || !object(value.from) || !str(value.from.processId)) fail("관계 후보");
  const v = value as Json;
  const to = object(v.to) && str(v.to.processId) ? { processId: v.to.processId as string } : null;
  if ((v.kind === "required_step") !== (to === null)) fail("관계 후보");
  return {
    candidateId: v.candidateId as string, kind: v.kind as RelationKind, status: v.status as RelationStatus, approvable: v.approvable as boolean,
    listedForPairing: v.listedForPairing === true, pairApprovable: typeof v.pairApprovable === "boolean" ? v.pairApprovable : null,
    interfaceId: v.interfaceId as string, scope: object(v.scope) ? v.scope : null, from: { processId: (v.from as Json).processId as string }, to,
    counts: countsOf(v.counts, "관계 후보"), satisfactionRate: num(v.satisfactionRate), evaluatedAt: str(v.evaluatedAt), nonStandardProcessId: v.nonStandardProcessId === true,
  };
};
export const parseThreshold = (value: unknown): ThresholdCandidate => {
  if (!object(value) || !str(value.candidateId) || !thresholdStatuses.includes(value.status as string) || !object(value.scope) || !str(value.scope.processId)
    || !Array.isArray(value.interfaceIds) || !object(value.evidence)) fail("처리 시간 후보");
  // API는 Trainer 고정소수 문자열을 검증해 number로 낸다(rule-candidates-ui.md 4절). 문자열 등 다른 형식은 조용히 비우지 않고 형식 오류로 본다.
  const v = value as Json;
  if (v.threshold !== null && num(v.threshold) === null) fail("처리 시간 후보");
  const e = v.evidence as Json;
  if (!["n", "rows", "referenceRows", "endTimeMissingReferenceRows", "unavailableReferenceRows"].every((key) => count(e[key]))) fail("처리 시간 후보");
  const scope = v.scope as Json;
  return {
    candidateId: v.candidateId as string, status: v.status as ThresholdStatus, reason: str(v.reason),
    scope: { processId: scope.processId as string, ...(str(scope.adapterType) ? { adapterType: scope.adapterType as string } : {}) },
    interfaceIds: (v.interfaceIds as unknown[]).filter((x): x is string => typeof x === "string"),
    operator: str(v.operator), threshold: num(v.threshold), displayThreshold: str(v.displayThreshold), unit: str(v.unit) ?? "", source: str(v.source),
    evidence: { n: e.n as number, rows: e.rows as number, referenceRows: e.referenceRows as number, endTimeMissingReferenceRows: e.endTimeMissingReferenceRows as number,
      unavailableReferenceRows: e.unavailableReferenceRows as number, p95: num(e.p95), min: num(e.min), max: num(e.max), quantileMethod: str(e.quantileMethod) },
    limitation: str(v.limitation), nonStandardProcessId: v.nonStandardProcessId === true,
  };
};
const headOf = (v: Json): ResultHead => {
  const period = object(v.period) ? v.period : {};
  const p = object(v.parameters) ? v.parameters : {};
  return {
    job: jobOf(v.job), snapshotId: str(v.snapshotId), sourceId: str(v.sourceId),
    period: { from: str(period.from), until: str(period.until), timeBasis: str(period.timeBasis) }, generatedAt: str(v.generatedAt),
    parameters: { minApplicable: num(p.minApplicable), candidatePercent: num(p.candidatePercent), reviewPercent: num(p.reviewPercent), maxCounterexamples: num(p.maxCounterexamples) },
  };
};
const graphOf = (value: unknown) => {
  if (value === null) return null;
  if (!object(value) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) return fail("패턴 그래프");
  const nodes = value.nodes.filter(object).map((n): PatternNode => {
    const a = object(n.attrs) ? n.attrs : {};
    return { id: String(n.id), kind: "pattern_step", attrs: { interfaceId: str(a.interfaceId) ?? "", processId: str(a.processId), adapterType: str(a.adapterType), transactions: count(a.transactions) ? (a.transactions as number) : null } };
  });
  const edges = value.edges.filter(object).map((e): PatternEdge => {
    const ev = object(e.evidence) ? e.evidence : {};
    return { id: String(e.id), kind: String(e.kind), from: String(e.from), to: String(e.to),
      evidence: { status: str(ev.status), satisfactionRate: num(ev.satisfactionRate), applicable: count(ev.applicable) ? (ev.applicable as number) : null,
        observedTransactions: count(ev.observedTransactions) ? (ev.observedTransactions as number) : null } };
  });
  return { nodes, edges };
};

export const parseCandidateList = (value: unknown): CandidateList => {
  if (!object(value)) return fail("규칙 추천");
  const latestJob = value.latestJob === null ? null : jobOf(value.latestJob);
  const approval = approvalOf(value.approval);
  if (value.result === null) return { latestJob, approval, result: null };
  if (!object(value.result)) return fail("규칙 추천");
  const r = value.result;
  if (!Array.isArray(r.interfaces) || !Array.isArray(r.relationCandidates) || !Array.isArray(r.thresholdCandidates) || !object(r.totals)) fail("규칙 추천");
  const totals = r.totals as Json;
  return {
    latestJob, approval,
    result: {
      ...headOf(r),
      interfaces: (r.interfaces as unknown[]).filter(object).map((i) => ({ interfaceId: String(i.interfaceId), transactions: Number(i.transactions) || 0,
        relationCandidates: Number(i.relationCandidates) || 0, approvable: Number(i.approvable) || 0, thresholdCandidates: Number(i.thresholdCandidates) || 0 })),
      selectedInterfaceId: str(r.selectedInterfaceId),
      graph: graphOf(r.graph ?? null),
      relationCandidates: (r.relationCandidates as unknown[]).map(parseRelation),
      thresholdCandidates: (r.thresholdCandidates as unknown[]).map(parseThreshold),
      thresholdsWithoutInterface: count(r.thresholdsWithoutInterface) ? (r.thresholdsWithoutInterface as number) : 0,
      thresholdLimitations: Array.isArray(r.thresholdLimitations) ? r.thresholdLimitations.filter((x): x is string => typeof x === "string") : [],
      totals: { relationCandidates: Number(totals.relationCandidates) || 0, approvable: Number(totals.approvable) || 0,
        thresholdCandidates: Number(totals.thresholdCandidates) || 0, thresholdApprovable: Number(totals.thresholdApprovable) || 0 },
    },
  };
};

export const parseCandidateDetail = (value: unknown): CandidateDetail => {
  if (!object(value) || (value.type !== "relation" && value.type !== "threshold")) return fail("후보 상세");
  const head = { ...headOf(value), isLatest: value.isLatest === true, approval: approvalOf(value.approval) };
  if (value.type === "threshold") {
    return { ...head, type: "threshold", threshold: parseThreshold(value.threshold), ruleDraft: object(value.ruleDraft) ? value.ruleDraft : null };
  }
  const relation = parseRelation(value.relation);
  const raw = value.relation as Json;
  const pair = object(value.pairedPrecedes) ? value.pairedPrecedes : null;
  return {
    ...head, type: "relation",
    relation: { ...relation, violationReasons: tally(raw.violationReasons), undeterminedReasons: tally(raw.undeterminedReasons),
      violationsByTransactionStatus: tally(raw.violationsByTransactionStatus) },
    counterexamples: Array.isArray(value.counterexamples)
      ? value.counterexamples.filter(object).map((c) => ({ transactionId: str(c.transactionId), reason: str(c.reason), transactionStatus: str(c.transactionStatus), decisionId: str(c.decisionId) }))
      : [],
    pairedPrecedes: pair && str(pair.candidateId) && relationStatuses.includes(pair.status as string)
      ? { candidateId: pair.candidateId as string, status: pair.status as RelationStatus, approvable: pair.approvable === true,
          counts: countsOf(pair.counts, "짝 선행 후보"), satisfactionRate: num(pair.satisfactionRate) }
      : null,
    ruleDraft: object(value.ruleDraft) ? value.ruleDraft : fail("후보 상세"),
  };
};

export const readCandidateList = async (interfaceId: string | null, signal?: AbortSignal) =>
  parseCandidateList(await httpClient.get<unknown>("/api/rule-set/candidates", { signal, params: { interfaceId: interfaceId ?? undefined } }));
export const readCandidateDetail = async (candidateId: string, signal?: AbortSignal) =>
  parseCandidateDetail(await httpClient.get<unknown>(`/api/rule-set/candidates/${encodeURIComponent(candidateId)}`, { signal }));
