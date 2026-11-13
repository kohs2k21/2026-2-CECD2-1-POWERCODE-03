import { record } from "./analysisApi";

/**
 * 판정 evidence 읽기. 저장된 값만 읽고 다시 계산하지 않는다(형식이 틀린 항목은 버림).
 * - v3(현행 Detector, `runner.py`): rules[]·models[]·checkers·anomalous·requestedMode·actualPaths·detectionPolicy·input·demo
 * - v4(detection-decision.md #92): methodResults[]·categoryResults.{hard,soft,ml}·composite(grade·risk). 없으면 null(v3 표시로 대체)
 *   정책 값(categoryResults.*.k·composite.policy·detectionPolicy.k·deadlineMs)은 화면에 보이지 않으므로 표시에 쓰지 않는다(C23·사용자 9.8)
 */
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
const oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | null =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : null;
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(record) : []);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((s): s is string => typeof s === "string" && s !== "") : []);

export type RuleEvidence = {
  ruleKey: string | null;
  version: string | null;
  result: "hit" | "no_hit" | "unavailable" | "not_applicable" | null;
  observedStatus: string | null;
  severity: string | null;
  reason: string | null;
};
export type ModelEvidence = { modelKey: string | null; anomalous: boolean | null; reason: string | null };
export type CheckerEvidence = { status: string | null; anomalous: boolean | null };
export type EvidenceV3 = {
  schemaVersion: number | null;
  anomalous: boolean | null;
  requestedMode: string | null;
  actualPaths: string[];
  rules: RuleEvidence[];
  models: ModelEvidence[];
  checkers: Partial<Record<"hr" | "sr" | "ml", CheckerEvidence>>;
  /** 판정 정책: 모드·조합만 읽는다. 기한(deadlineMs)·k는 정책 값이라 화면에 보이지 않으므로 읽지 않는다(와이어프레임 사용자 9.8). */
  policy: { mode: string | null; combination: string | null } | null;
  ruleSeverities: string[];
  input: { mode: "api" | "csv" | null; configurationScope: string | null } | null;
  demo: { runKey: string | null } | null;
};

export const readEvidence = (evidence: Record<string, unknown> | null | undefined): EvidenceV3 => {
  const e = evidence ?? {};
  const checkers: EvidenceV3["checkers"] = {};
  if (record(e.checkers))
    for (const key of ["hr", "sr", "ml"] as const) {
      const c = e.checkers[key];
      if (record(c)) checkers[key] = { status: text(c.status), anomalous: bool(c.anomalous) };
    }
  const policy = record(e.detectionPolicy) ? e.detectionPolicy : null;
  const input = record(e.input) ? e.input : null;
  return {
    schemaVersion: finite(e.schemaVersion),
    anomalous: bool(e.anomalous),
    requestedMode: text(e.requestedMode),
    actualPaths: strings(e.actualPaths),
    rules: list(e.rules).map((r) => ({
      ruleKey: text(r.ruleKey),
      version: text(r.version) ?? (finite(r.version) === null ? null : String(r.version)),
      result: oneOf(r.result, ["hit", "no_hit", "unavailable", "not_applicable"] as const),
      observedStatus: text(r.observedStatus),
      severity: text(r.severity),
      reason: text(r.reason),
    })),
    models: list(e.models).map((m) => ({ modelKey: text(m.modelKey), anomalous: bool(m.anomalous), reason: text(m.reason) })),
    checkers,
    policy: policy ? { mode: text(policy.mode), combination: text(policy.combination) } : null,
    ruleSeverities: strings(e.ruleSeverities),
    input: input ? { mode: oneOf(input.mode, ["api", "csv"] as const), configurationScope: text(input.configurationScope) } : null,
    demo: record(e.demo) ? { runKey: text(e.demo.runKey) } : null,
  };
};

export const grades = ["anomaly", "anomaly_sign", "normal", "undetermined"] as const;
export type Grade = (typeof grades)[number];
export type MethodCategory = "hard" | "soft" | "ml";
/**
 * 근거 구분(Detector v3 방법 id 형식, detection-decision.md 4.2·rule-set.md v3.4): 대분류 hard를 방법 id로 나눈다(저장값만, 재계산 없음).
 * - relation: 관계 규칙 `hard:rel-<8 hex>`(relation-rules.md 1절) / protocol: 프로토콜 항목 `hard:proto-<8 hex>` + 내장 `hard:unregistered-interface`(protocol-rules.md 4.1)
 * - hard: 그 밖의 내장 이상 규칙(`hard:transaction-failed` 포함), soft·ml은 대분류 그대로
 */
export type EvidenceGroup = "hard" | "relation" | "protocol" | "soft" | "ml";
export const evidenceGroups: readonly EvidenceGroup[] = ["hard", "relation", "protocol", "soft", "ml"];
export const isRelationRuleId = (id: string) => /^hard:rel-[0-9a-f]{8}$/.test(id);
export const isProtocolRuleId = (id: string) => /^hard:proto-[0-9a-f]{8}$/.test(id);
export const evidenceGroupOf = (methodId: string): EvidenceGroup | null => {
  const category = methodId.split(":")[0];
  if (category === "soft" || category === "ml") return category;
  if (category !== "hard") return null;
  if (isRelationRuleId(methodId)) return "relation";
  return isProtocolRuleId(methodId) || methodId === "hard:unregistered-interface" ? "protocol" : "hard";
};
/** 관계 규칙 판정 kind 셋(relation-rules.md 3절): methodVersion `relation-<kind>-v1` 또는 코드 `REL_<KIND>`. */
export const relationKinds = ["precedes", "message_handoff", "required_step"] as const;
export type RelationMethodKind = (typeof relationKinds)[number];
const relationKindOf = (version: string | null, code: string | null): RelationMethodKind | null => {
  const fromVersion = version ? /^relation-([a-z_]+)-v\d+$/.exec(version)?.[1] : undefined;
  const fromCode = code ? /^REL_([A-Z_]+)$/.exec(code)?.[1]?.toLowerCase() : undefined;
  return oneOf(fromVersion ?? fromCode, relationKinds);
};
export type MethodResult = {
  methodId: string;
  category: MethodCategory;
  group: EvidenceGroup;
  /** 관계 규칙만: 판정 kind. */
  relationKind: RelationMethodKind | null;
  /** 방법 evidence 첫 항목의 위반 기록(Detector relation_rules·hard_rules 저장값): violation 코드, 필수 단계 있음 여부, 인계 결과, 거래 응답 코드. */
  hit: { violation: string | null; present: boolean | null; handoffResults: string[]; responseCode: string | null };
  methodVersion: string | null;
  status: "succeeded" | "not_ready" | "failed" | null;
  anomalous: boolean | null;
  code: string | null;
  severity: string | null;
  riskLevel: string | null;
  riskLevelReason: string | null;
  cause: string | null;
  failureCode: string | null;
  elapsedMs: number | null;
  score: number | null;
  /** ML만: 원점수·판정선·판정선 대비(threshold_ratio). 판정선은 저장 문자열 그대로. */
  scoreDetail: { raw: number | null; threshold: string | null; ratio: number | null } | null;
  /** 판단 안 함(not_applicable) 등 방법 evidence 요약의 reason 코드. */
  reasons: string[];
  condition: { field: string | null; operator: string | null; threshold: string | null; unit: string | null } | null;
  observedValue: string | null;
};
export type CategoryResult = { n: number | null; k: number | null; result: string | null; status: string | null; anomalous: boolean | null };
export type EvidenceV4 = {
  grade: Grade | null;
  compositeStatus: string | null;
  anomalous: boolean | null;
  basisMethods: string[];
  risk: { status: string | null; level: string | null; levelTable: string | null; determinedBy: string[]; unratedMethods: string[] } | null;
  categoryResults: Partial<Record<MethodCategory, CategoryResult>>;
  methodResults: MethodResult[];
  /** 관계·프로토콜·거래 실패 규칙의 방법 결과 밖 기록(relation-rules.md 5절). 없으면 []. */
  relationNotes: RelationNote[];
  ruleSetRevision: number | null;
  elapsedMs: number | null;
};
/** relationNotes 항목 `{ruleId, kind, result, reason, waitingFor}`. result `held`(수집 미완료 보류)만 화면 "관계 확인 보류"(relation-rules.md 5절). */
export type RelationNote = {
  ruleId: string | null;
  kind: string | null;
  result: string | null;
  reason: string | null;
  waitingFor: { processId: string | null; direction: "IN" | "OUT" | null; layer: string | null } | null;
};

/** evidence(또는 중간 판정 기록 payload) v4 읽기. schemaVersion 4·composite가 없으면 null. */
export const readEvidenceV4 = (evidence: Record<string, unknown> | null | undefined): EvidenceV4 | null => {
  if (!evidence || evidence.schemaVersion !== 4 || !record(evidence.composite)) return null;
  const c = evidence.composite;
  const categoryResults: EvidenceV4["categoryResults"] = {};
  if (record(evidence.categoryResults))
    for (const key of ["hard", "soft", "ml"] as const) {
      const r = evidence.categoryResults[key];
      if (record(r)) categoryResults[key] = { n: finite(r.n), k: finite(r.k), result: text(r.result), status: text(r.status), anomalous: bool(r.anomalous) };
    }
  const risk = record(c.risk) ? c.risk : null;
  return {
    grade: oneOf(c.grade, grades),
    compositeStatus: text(c.status),
    anomalous: bool(c.anomalous),
    basisMethods: strings(c.basisMethods),
    risk: risk ? { status: text(risk.status), level: text(risk.level), levelTable: text(risk.levelTable),
      determinedBy: strings(risk.determinedBy), unratedMethods: strings(risk.unratedMethods) } : null,
    categoryResults,
    methodResults: list(evidence.methodResults).flatMap((m) => {
      const category = oneOf(m.category, ["hard", "soft", "ml"] as const);
      const methodId = text(m.methodId);
      if (!category || !methodId || !methodId.startsWith(`${category}:`)) return [];
      const group = evidenceGroupOf(methodId) as EvidenceGroup;
      const first = list(m.evidence)[0] ?? {};
      const condition = record(m.condition) ? m.condition : null;
      const detail = record(m.scoreDetail) ? m.scoreDetail : null;
      const normalized = detail && record(detail.normalized) && detail.normalized.kind === "threshold_ratio" ? finite(detail.normalized.value) : null;
      const threshold = detail ? (text(detail.threshold) ?? (finite(detail.threshold) === null ? null : String(detail.threshold))) : null;
      return [{
        methodId, category, group, methodVersion: text(m.methodVersion),
        relationKind: group === "relation" ? relationKindOf(text(m.methodVersion), text(m.code)) : null,
        hit: { violation: text(first.violation), present: bool(first.present),
          handoffResults: list(first.handoffs).flatMap((h) => text(h.result) ?? []), responseCode: text(first.responseCode) },
        status: oneOf(m.status, ["succeeded", "not_ready", "failed"] as const),
        anomalous: bool(m.anomalous), code: text(m.code), severity: text(m.severity), riskLevel: text(m.riskLevel),
        riskLevelReason: text(m.riskLevelReason),
        cause: text(m.cause), failureCode: text(m.failureCode), elapsedMs: finite(m.elapsedMs), score: finite(m.score),
        scoreDetail: detail ? { raw: finite(detail.raw), threshold, ratio: normalized } : null,
        reasons: list(m.evidence).flatMap((entry) => text(entry.reason) ?? []),
        condition: condition ? { field: text(condition.field), operator: text(condition.operator), threshold: text(condition.threshold), unit: text(condition.unit) } : null,
        observedValue: text(m.observedValue),
      }];
    }),
    relationNotes: list(evidence.relationNotes).map((n) => {
      const waiting = record(n.waitingFor) ? n.waitingFor : null;
      return { ruleId: text(n.ruleId), kind: text(n.kind), result: text(n.result), reason: text(n.reason),
        waitingFor: waiting ? { processId: text(waiting.processId), direction: oneOf(waiting.direction, ["IN", "OUT"] as const), layer: text(waiting.layer) } : null };
    }),
    ruleSetRevision: finite(evidence.ruleSetRevision),
    elapsedMs: finite(evidence.elapsedMs),
  };
};
