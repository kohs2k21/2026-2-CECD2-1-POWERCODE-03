/**
 * 판정 상세 `evidenceSummary`(AI 판정 근거 요약 다섯 블록) — 저장된 판정 근거만으로 만든다. DB를 읽지 않는 순수 함수다.
 * 문구·틀은 evidenceSummaryTemplates.ts 한 곳, 값 표기·조사는 koreanText.ts, 기여도 판독은 mlAttribution.ts.
 * - 판정을 다시 계산하지 않는다. 저장값(evidence v4 composite·methodResults·protocolReview, 판정 관측 행 값, 고정 룰 세트 관계 규칙)을 옮겨 적고
 *   표기만 바꾼다(지수 펼침·천 단위 쉼표·비율 정수 % half-up). 표 값과 단위는 따로 싣는다(화면이 값 뒤에 단위를 붙인다)
 * - 예외를 던지지 않는다. v3·형식 어긋남·요약할 근거가 없으면 null(화면은 카드를 그리지 않는다)
 * - 블록 순서: 1 한 줄 요약(앞말 + 문구) · 2 문장(근거 방법 hard → soft → ml 최대 MAX_METHOD_SENTENCES + 넘침 + 미판정) · 3 표 · 4 확인할 것 · 5 고정 안내
 * - 주 근거(블록 1·4 첫 줄) = composite.risk.determinedBy 첫 방법, 없으면 basisMethods 첫 방법(요약할 수 없는 방법이면 다음). 블록 4 둘째 줄 = 미판정 줄, 없으면 다른 근거 방법의 첫 다른 줄
 * - valueTokens = 문장에 끼워 넣은 원천 문자열 값(상태·시각·응답 코드·인터페이스·단계 이름)을 문장에 나온 글자 그대로, 처음 나온 순. 숫자 값은 넣지 않는다
 * - stateWords = 문장에서 valueTokens·시각·이름표를 가린 뒤 판정 낱말이 나온 순서열
 */
import { displayNumber, expandExponent, percentText, plainNumber } from "./koreanText.js";
import { readAttribution, type Attribution, type AttributionPlayer } from "./mlAttribution.js";
import * as T from "./evidenceSummaryTemplates.js";

/** 표 이번 값 칸 하나: name(앞에 붙는 이름, 없으면 null) + value + unit(값 뒤 단위, 없으면 null). unitUnknown = 건수 계열(화면이 열 머리 툴팁으로 안내). */
export type SummaryObserved = { name: string | null; value: string; unit: string | null; unitUnknown: boolean };
export type SummaryRuleRow = { kind: "rule"; methodId: string; condition: string; observed: SummaryObserved[]; result: string };
/** usual = 평소 값 숫자, scale 1 구성원은 낱말 SCALE_UNIT_USUAL(평소 값 숫자를 보이지 않음). unit은 숫자 값에만 붙인다. */
export type SummaryMlMember = { name: string; actual: string; usual: string; unit: string | null; unitUnknown: boolean };
export type SummaryMlRow = { kind: "ml"; methodId: string; playerId: string; members: SummaryMlMember[]; impactPercent: string };
export type ContributionStatus = "computed" | "not_computed" | "skipped" | "failed" | "unreadable";
export type EvidenceSummary = {
  templateVersion: string;
  summaryLine: { prefix: string; prefixKind: "risk" | "grade" | "signal"; text: string; kind: string };
  sentences: string[];
  stateWords: string[];
  valueTokens: string[];
  table: (SummaryRuleRow | SummaryMlRow)[];
  checks: { code: string; text: string }[];
  notice: string;
  contributionStatus: ContributionStatus;
  /** 근거인 ML 방법의 기여도 표가 비었을 때 표 아래 상태 낱말(없으면 null). */
  contributionNote: string | null;
};
/** 판정 관측 행(PROCESS) 값. 숫자 열은 DB 문자열 그대로. revisionNo = 그 관측 행의 revision(판정 evidence observation.revisionNo와 같을 때만 쓴다). */
export type ObservedFields = {
  revisionNo?: number | string | null;
  status?: string | null;
  startTimeRaw?: string | null;
  endTimeRaw?: string | null;
  totalCount?: string | null;
  successCount?: string | null;
  errorCount?: string | null;
  retryCount?: string | null;
};
export type EvidenceSummaryInput = {
  /** 저장 evidence(v4만 요약한다). */
  evidence: unknown;
  /** 판정 관측 행 값(하드룰 시각·건수). 없거나 revision이 다르면 그 자리는 값 없음. */
  observedFields?: ObservedFields | null;
  /** 판정에 고정한 룰 세트 리비전 스냅샷의 relationRules[](관계 규칙 from·to·필수 단계 인터페이스). */
  relationRules?: unknown;
  /** 판정 항목 인터페이스(프로토콜 근거에 인터페이스가 없을 때만). */
  interfaceId?: string | null;
};

type Rec = Record<string, unknown>;
const record = (v: unknown): v is Rec => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const records = (v: unknown): Rec[] => (Array.isArray(v) ? v.filter(record) : []);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);
/** 값 또는 값 없음. */
const shown = (v: string | null | undefined) => v ?? T.NO_VALUE;
/** 숫자 표시(지수 펼침·자릿수·천 단위 쉼표, 단위는 붙이지 않음 — 자릿수만 단위로 고른다). 숫자가 아니면 원문, 없으면 값 없음. 표와 문장이 같은 글자를 쓴다. */
const numberValue = (v: unknown, unit: string | null = null): string => {
  const raw = typeof v === "number" && Number.isFinite(v) ? plainNumber(v) : str(v);
  return raw === null ? T.NO_VALUE : displayNumber(raw, unit);
};
/** 문장·조건 속 값: 숫자 표기 + 단위 한 칸(값이 없으면 단위도 없음). */
const withUnit = (value: string, unit: string | null) => (unit && value !== T.NO_VALUE ? `${value} ${unit}` : value);
const label = (field: string): T.FieldLabel => (Object.hasOwn(T.FIELD_LABELS, field) ? T.FIELD_LABELS[field] : { name: field, unitUnknown: false, unit: null });

const CATEGORY_RANK: Record<string, number> = { hard: 0, soft: 1, ml: 2 };
const isRelation = (id: string) => id.startsWith("hard:rel-");
const isProtocol = (id: string) => id.startsWith("hard:proto-") || id === "hard:unregistered-interface";
const methodName = (m: Rec): string => {
  const id = m.methodId as string;
  if (isRelation(id)) return T.METHOD_NAMES.relation;
  return T.METHOD_NAMES[m.category as "hard" | "soft" | "ml"];
};

/** 근거 방법 하나의 요약 재료. sentences 첫 문장은 반드시 있다(추가 문장 = ML 2위). tokens = 문장에 넣은 원천 값 후보. */
type Described = {
  methodId: string; kind: string; line: string; sentences: string[]; check: string | null; rows: (SummaryRuleRow | SummaryMlRow)[]; note: string | null; tokens: string[];
};
const obs = (value: string, name: string | null = null, unit: string | null = null, unitUnknown = false): SummaryObserved =>
  ({ name, value, unit: value === T.NO_VALUE ? null : unit, unitUnknown: value !== T.NO_VALUE && unitUnknown });
const ruleRow = (methodId: string, condition: string, observed: SummaryObserved[], result: string): SummaryRuleRow =>
  ({ kind: "rule", methodId, condition, observed, result });
/** tokens: 문장에 넣은 원천 값(값 없음은 문장에 들어가지 않으므로 뺀다). */
const fromLines = (methodId: string, kind: string, lines: T.Lines, rows: Described["rows"], tokens: string[] = []): Described =>
  ({ methodId, kind, line: lines.line, sentences: [lines.sentence], check: lines.check, rows, note: null, tokens: tokens.filter((t) => t !== T.NO_VALUE) });

// ---- 그래프 노드 id(`process:<거래>:<단계>`, `%`·`:` 이스케이프)에서 단계 id ----
const nodeProcess = (node: unknown): string | null => {
  const parts = typeof node === "string" ? node.split(":") : [];
  if (parts.length !== 3 || parts[0] !== "process" || !parts[2]) return null;
  return parts[2].replace(/%3A/g, ":").replace(/%25/g, "%");
};
const relationRuleOf = (rules: unknown, id: string): Rec | null => records(rules).find((r) => r.id === id) ?? null;
const processIdOf = (v: unknown) => (record(v) ? str(v.processId) : null);

/** 관측 행 값은 판정 evidence observation.revisionNo와 관측 행 revision이 같을 때만 쓴다(다르거나 알 수 없으면 값 없음). */
const observedOf = (input: EvidenceSummaryInput, evidence: Rec): ObservedFields => {
  const fields = record(input.observedFields) ? (input.observedFields as ObservedFields) : null;
  const decided = record(evidence.observation) ? evidence.observation.revisionNo : undefined;
  const revision = (v: unknown): string | null => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? String(v)
    : typeof v === "string" && /^(0|[1-9]\d{0,17})$/.test(v) ? v : null); // bigint 열은 숫자 또는 숫자 문자열로 온다
  const want = revision(decided);
  return fields && want !== null && revision(fields.revisionNo) === want ? fields : {};
};

// ---- 하드룰 ----
const describeHard = (m: Rec, input: EvidenceSummaryInput, evidence: Rec): Described | null => {
  const id = m.methodId as string;
  const first = records(m.evidence)[0] ?? {};
  const code = str(m.code);
  const observed = observedOf(input, evidence);
  const A = T.RESULT_WORDS.anomaly;
  const N = T.OBSERVED_NAMES;
  if (id === "hard:process-status") {
    const x = shown(str(first.observedStatus) ?? (code?.startsWith("PROCESS_STATUS_") ? code.slice("PROCESS_STATUS_".length) || null : null));
    return fromLines(id, "hard_process_status", T.hard.processStatus.lines(x), [ruleRow(id, T.hard.processStatus.condition, [obs(x)], A)], [x]);
  }
  if (id === "hard:negative-duration") {
    const s = shown(str(observed.startTimeRaw));
    const e = shown(str(observed.endTimeRaw));
    return fromLines(id, "hard_negative_duration", T.hard.negativeDuration.lines(s, e),
      [ruleRow(id, T.hard.negativeDuration.condition, [obs(s), obs(e)], A)], [s, e]);
  }
  if (id === "hard:success-with-error") {
    return fromLines(id, "hard_success_with_error", T.hard.successWithError.lines(),
      [ruleRow(id, T.hard.successWithError.condition, [obs("S", N.status), obs(numberValue(observed.errorCount), N.errorCount, null, true)], A)]);
  }
  if (id === "hard:count-conservation") {
    const values = [[N.totalCount, observed.totalCount], [N.successCount, observed.successCount], [N.errorCount, observed.errorCount]] as const;
    return fromLines(id, "hard_count_conservation", T.hard.countConservation.lines(),
      [ruleRow(id, T.hard.countConservation.condition, values.map(([name, v]) => obs(numberValue(v), name, null, true)), A)]);
  }
  if (id === "hard:transaction-failed") {
    const c = shown(str(first.responseCode));
    return fromLines(id, "hard_transaction_failed", T.hard.transactionFailed.lines(c), [ruleRow(id, T.hard.transactionFailed.condition, [obs("F"), obs(c)], A)], [c]);
  }
  const review = record(evidence.protocolReview) ? evidence.protocolReview : null;
  const iface = shown(str(review?.interfaceId) ?? str(input.interfaceId));
  if (id === "hard:unregistered-interface") {
    return fromLines(id, "hard_unregistered_interface", T.hard.unregisteredInterface.lines(iface),
      [ruleRow(id, T.hard.unregisteredInterface.condition, [obs(iface)], A)], [iface]);
  }
  if (id.startsWith("hard:proto-")) {
    const t = T.hard.protocolPath;
    const unmatched = records(review?.routes).filter((r) => r.matched === false && Array.isArray(r.channels))
      .map((r) => t.path((r.channels as unknown[]).map((c) => (typeof c === "string" ? c : T.NO_VALUE))));
    const path = unmatched.length ? t.paths(unmatched.slice(0, t.PATH_LIMIT), unmatched.length - t.PATH_LIMIT) : T.NO_VALUE;
    return fromLines(id, "hard_protocol_path", t.lines(iface), [ruleRow(id, t.condition, [obs(path)], A)], [iface]);
  }
  if (isRelation(id)) {
    const rule = relationRuleOf(input.relationRules, id);
    const from = shown(processIdOf(rule?.from) ?? nodeProcess(first.from));
    const to = shown(processIdOf(rule?.to) ?? nodeProcess(first.node));
    if (code === "REL_PRECEDES") {
      const violation = str(first.violation);
      const words = T.relation.precedes;
      const reason = violation && Object.hasOwn(words.violationReasons, violation) ? words.violationReasons[violation] : null;
      const word = violation && Object.hasOwn(words.violationWords, violation) ? words.violationWords[violation] : T.NO_VALUE;
      return fromLines(id, "relation_precedes", words.lines(from, reason), [ruleRow(id, words.condition(from, to), [obs(word)], A)], [from]);
    }
    if (code === "REL_MESSAGE_HANDOFF") {
      const t = T.relation.messageHandoff;
      return fromLines(id, "relation_message_handoff", t.lines(from), [ruleRow(id, t.condition(from, to), [obs(t.observed)], A)], [from]);
    }
    if (code === "REL_REQUIRED_STEP") {
      const step = shown(processIdOf(rule?.from) ?? nodeProcess(first.step));
      const stepIface = shown((record(rule?.scope) ? str(rule.scope.interfaceId) : null) ?? str(input.interfaceId));
      const t = T.relation.requiredStep;
      return fromLines(id, "relation_required_step", t.lines(step), [ruleRow(id, t.condition(stepIface, step), [obs(t.observed)], A)], [step]);
    }
  }
  return null;
};

// ---- 소프트룰 ----
const describeSoft = (m: Rec): Described | null => {
  const id = m.methodId as string;
  const condition = record(m.condition) ? m.condition : null;
  const field = str(condition?.field);
  if (!field) return null;
  const l = label(field);
  const unitUnknown = l.unitUnknown || condition?.unit === "count";
  const unit = l.unit ?? (condition?.unit === "ms" ? "ms" : null);
  const op = Object.hasOwn(T.OPERATOR_SIGNS, String(condition?.operator)) ? T.OPERATOR_SIGNS[String(condition?.operator)] : T.NO_VALUE;
  const t = withUnit(numberValue(condition?.threshold, unit), unit);
  const value = numberValue(m.observedValue, unit);
  const v = withUnit(value, unit);
  const rows = [ruleRow(id, T.soft.condition(l.name, op, t), [obs(value, null, unit, unitUnknown)], T.RESULT_WORDS.sign)];
  if (unitUnknown) return fromLines(id, "soft_count", T.soft.count(l.name, op), rows);
  if (field === "elapsed_ms") return fromLines(id, "soft_elapsed", T.soft.elapsed(l.name, op, v, t), rows);
  return fromLines(id, "soft_value", T.soft.value(l.name, op, v, t), rows);
};

// ---- 머신러닝 모델 ----
const contributionNoteOf = (a: Attribution): string | null => {
  if (a.status === "succeeded") return null; // 계산은 했으나 이상 쪽 참가자 없음: 표만 비고 낱말은 두지 않는다
  return T.CONTRIBUTION_NOTES[a.status];
};
const playerLabel = (p: AttributionPlayer) => T.ml.playerName(p.features.map((f) => label(f).name));
const impactOf = (p: AttributionPlayer) => (p.share === null ? null : percentText(p.share));
const describeMl = (m: Rec): Described => {
  const id = m.methodId as string;
  const a = readAttribution(m);
  if (a.status !== "succeeded" || a.topK.length === 0) {
    return { methodId: id, kind: "ml_no_contribution", line: T.mlNoContribution.line, sentences: [T.mlNoContribution.sentence], check: null, rows: [],
      note: contributionNoteOf(a), tokens: [] };
  }
  const byId = new Map(a.players.map((p) => [p.playerId, p]));
  const top = a.topK.map((pid) => byId.get(pid)).filter((p): p is AttributionPlayer => p !== undefined);
  const rows: SummaryMlRow[] = top.map((p) => ({
    kind: "ml", methodId: id, playerId: p.playerId, impactPercent: impactOf(p) ?? T.NO_VALUE,
    members: p.features.map((f) => {
      const l = label(f);
      const scale = p.scaleIsUnit[f] === true;
      const actual = numberValue(p.actual[f], l.unit);
      const usual = scale ? T.SCALE_UNIT_USUAL : numberValue(p.baseline[f], l.unit);
      return { name: l.name, actual, usual, unit: l.unit, unitUnknown: l.unitUnknown };
    }),
  }));
  const [first, second] = top;
  const impact = impactOf(first);
  let kind = "ml_value";
  let lines: T.Lines;
  const single = first.features.length === 1 ? first.features[0] : null;
  const scaleUnit = single !== null && first.scaleIsUnit[single] === true;
  const actual = single ? Number(expandExponent(first.actual[single])) : NaN;
  const baseline = single ? first.baseline[single] : NaN;
  // 표시 글자(반올림 뒤)가 같으면 숫자를 쓰는 틀을 쓰지 않는다(`800 ms를 평소 값 800 ms와 비교했고, 더 길어`처럼 읽히지 않게)
  const unit = single === "elapsed_ms" ? "ms" : null;
  const sameText = single !== null && numberValue(first.actual[single], unit) === numberValue(baseline, unit);
  const comparable = single !== null && !scaleUnit && Number.isFinite(actual) && actual !== baseline && !sameText;
  if (single === "elapsed_ms" && comparable) {
    const [av, bv] = [withUnit(numberValue(first.actual[single], "ms"), "ms"), withUnit(numberValue(baseline, "ms"), "ms")];
    kind = actual > baseline ? "ml_elapsed_longer" : "ml_elapsed_shorter";
    lines = actual > baseline ? T.ml.elapsedLonger(av, bv, impact) : T.ml.elapsedShorter(av, bv, impact);
  } else if (single === "error_ratio" && comparable) {
    kind = "ml_error_ratio";
    lines = T.ml.errorRatio(numberValue(first.actual[single]), numberValue(baseline), actual > baseline, impact);
  } else {
    if (first.features.length > 1) kind = "ml_group";
    else if (single && label(single).unitUnknown) kind = "ml_count";
    lines = T.ml.value(playerLabel(first), impact, single === "elapsed_ms");
  }
  const sentences = [lines.sentence, ...(second ? [T.ml.second(playerLabel(second), impactOf(second))] : [])];
  return { methodId: id, kind, line: lines.line, sentences, check: lines.check, rows, note: null, tokens: [] };
};

const describe = (m: Rec, input: EvidenceSummaryInput, evidence: Rec): Described | null => {
  if (m.category === "hard") return describeHard(m, input, evidence);
  if (m.category === "soft") return describeSoft(m);
  if (m.category === "ml") return describeMl(m);
  return null;
};

// ---- 미판정(not_ready·failed). 아직 오지 않은 결과(pending)와 관계·프로토콜 보류는 문장 없음 ----
const unresolvedRow = (m: Rec, input: EvidenceSummaryInput, evidence: Rec): SummaryRuleRow => {
  const id = m.methodId as string;
  const U = T.RESULT_WORDS.unresolved;
  const condition = (m.category === "soft" ? describeSoft(m) : m.category === "hard" ? describeHard({ ...m, code: null }, input, evidence) : null)?.rows[0];
  return ruleRow(id, condition?.kind === "rule" ? condition.condition : methodName(m), [obs(T.NO_VALUE)], U);
};

// ---- 판정 낱말·원천 값 ----
const TIME = /\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?/g;
const MASK = "\uE000"; // 사용 영역(Private Use Area) 한 글자: 숫자·라틴·한글이 아니다
/** 가림(원천 값 → 시각 → 이름표, 긴 것 먼저) 뒤 판정 낱말 순서열(중복 포함). */
export const judgmentWords = (text: string, valueTokens: string[]): string[] => {
  let masked = text;
  for (const token of [...valueTokens].sort((a, b) => b.length - a.length)) masked = masked.split(token).join(MASK);
  masked = masked.replace(TIME, MASK);
  const names = [...Object.values(T.FIELD_LABELS).map((l) => l.name), ...T.MASKED_NAMES].sort((a, b) => b.length - a.length);
  for (const name of names) masked = masked.split(name).join(MASK);
  const found: string[] = [];
  for (let i = 0; i < masked.length; ) {
    const word = T.STATE_WORDS.find((w) => masked.startsWith(w, i));
    if (word) {
      found.push(word);
      i += word.length;
    } else i += 1;
  }
  return found;
};
/** 문장에 실제로 들어간 원천 값만, 처음 나온 순, 중복 없이. 상한(개수·글자 수)을 넘는 값은 담지 않는다. */
const valueTokensOf = (text: string, candidates: string[]): string[] => {
  const unique = [...new Set(candidates)].filter((t) => t.length > 0 && t.length <= T.VALUE_TOKEN_MAX_LENGTH && text.includes(t));
  return unique.sort((a, b) => text.indexOf(a) - text.indexOf(b)).slice(0, T.VALUE_TOKEN_LIMIT);
};

/**
 * 기여도 상태(설명 워커 판별과 같은 우선순위): 기여도 키가 있는 ML 방법 결과마다 상태 → computed > failed > unreadable > skipped, 키가 하나도 없으면 not_computed.
 */
export const contributionStatusOf = (methods: Rec[]): ContributionStatus => {
  const statuses = new Set(methods.filter((m) => m.category === "ml" && "attribution" in m && m.attribution !== undefined).map((m) => readAttribution(m).status));
  if (statuses.has("succeeded")) return "computed";
  if (statuses.has("failed")) return "failed";
  if (statuses.has("unreadable")) return "unreadable";
  if (statuses.has("skipped")) return "skipped";
  return "not_computed";
};

const build = (input: EvidenceSummaryInput): EvidenceSummary | null => {
  const evidence = input.evidence;
  if (!record(evidence) || evidence.schemaVersion !== 4 || !Array.isArray(evidence.methodResults) || !record(evidence.composite)) return null;
  const composite = evidence.composite;
  const methods = records(evidence.methodResults).filter((m) => typeof m.methodId === "string" && typeof m.category === "string" && Object.hasOwn(CATEGORY_RANK, m.category));
  const ordered = [...methods].sort((a, b) => CATEGORY_RANK[a.category as string] - CATEGORY_RANK[b.category as string]);
  const sentences: string[] = [];
  const tokens: string[] = [];
  const table: EvidenceSummary["table"] = [];
  const checks: EvidenceSummary["checks"] = [];
  let summaryLine: EvidenceSummary["summaryLine"];
  let contributionNote: string | null = null;

  if (composite.anomalous === true) {
    const risk = record(composite.risk) ? composite.risk : null;
    const level = risk?.status === "rated" ? str(risk.level) : null;
    const grade = str(composite.grade);
    const prefix: Pick<EvidenceSummary["summaryLine"], "prefix" | "prefixKind"> =
      level && Object.hasOwn(T.RISK_PREFIX, level) ? { prefix: T.RISK_PREFIX[level], prefixKind: "risk" }
        : grade && Object.hasOwn(T.GRADE_PREFIX, grade) ? { prefix: T.GRADE_PREFIX[grade], prefixKind: "grade" }
          : { prefix: T.PREFIX_UNRATED, prefixKind: "risk" };
    const described = ordered.filter((m) => m.status === "succeeded" && m.anomalous === true)
      .map((m) => describe(m, input, evidence)).filter((d): d is Described => d !== null);
    if (!described.length) return null;
    const preferred = [...(Array.isArray(risk?.determinedBy) ? risk.determinedBy : []), ...(Array.isArray(composite.basisMethods) ? composite.basisMethods : [])];
    const primary = preferred.map((pid) => described.find((d) => d.methodId === pid)).find((d) => d !== undefined) ?? described[0];
    summaryLine = { ...prefix, text: primary.line, kind: primary.kind };
    let hidden = 0;
    // 주 근거(한 줄 요약을 정한 방법) 문장을 먼저 두고 나머지를 hard → soft → ml 순서로 채운 뒤 상한을 적용한다
    for (const d of [primary, ...described.filter((x) => x !== primary)]) {
      if (sentences.length >= T.MAX_METHOD_SENTENCES) {
        if (d.rows.length) hidden += 1;
        continue;
      }
      sentences.push(...d.sentences.slice(0, T.MAX_METHOD_SENTENCES - sentences.length));
      tokens.push(...d.tokens);
    }
    if (hidden) sentences.push(T.overflowSentence(hidden));
    for (const d of described) table.push(...d.rows);
    // 확인할 것: 주 근거 줄 + 다른 근거 방법의 첫 다른 줄(미판정 방법이 있으면 둘째 줄은 미판정 줄, 아래)
    for (const d of [primary, ...described.filter((x) => x !== primary)])
      if (d.check && !checks.some((c) => c.text === d.check)) checks.push({ code: d.kind, text: d.check });
    contributionNote = described.find((d) => d.note)?.note ?? null;
  } else if (composite.anomalous === false && composite.status === "complete") {
    summaryLine = { prefix: T.PREFIX_NORMAL, prefixKind: "signal", text: T.normal.complete.line, kind: "normal_complete" };
    sentences.push(T.normal.complete.sentence);
  } else {
    summaryLine = { prefix: T.PREFIX_UNCONFIRMED, prefixKind: "signal", text: T.normal.unconfirmed.line, kind: "normal_unconfirmed" };
  }

  if (summaryLine.kind !== "normal_complete") {
    const unresolvedMethods = ordered.filter((m) => (m.status === "not_ready" || m.status === "failed") && m.cause !== "pending"
      && !isRelation(m.methodId as string) && !isProtocol(m.methodId as string));
    if (unresolvedMethods.length) {
      const names = T.unresolved.names([...new Set(unresolvedMethods.map(methodName))]);
      sentences.push(T.unresolved.sentence(names));
      table.push(...unresolvedMethods.map((m) => unresolvedRow(m, input, evidence)));
      checks.splice(1, checks.length, { code: "unresolved", text: T.unresolved.check(names) });
    }
  }

  const text = sentences.join(" ");
  const valueTokens = valueTokensOf(text, tokens);
  return {
    templateVersion: T.TEMPLATE_VERSION,
    summaryLine,
    sentences,
    stateWords: judgmentWords(text, valueTokens),
    valueTokens,
    table,
    checks: checks.slice(0, T.MAX_CHECKS),
    notice: T.NOTICE,
    contributionStatus: contributionStatusOf(methods),
    contributionNote,
  };
};

/** 저장 근거 → evidenceSummary. 어떤 입력에서도 예외를 던지지 않는다(만들 수 없으면 null). */
export const buildEvidenceSummary = (input: EvidenceSummaryInput): EvidenceSummary | null => {
  try {
    return build(input);
  } catch {
    return null;
  }
};

/** 판정 상세 읽기가 추가로 읽을 값: 관측 행(시각·건수 하드룰 근거가 있을 때), 룰 세트 리비전(관계 규칙 근거가 있을 때). */
const OBSERVED_HARD_RULES = new Set(["hard:negative-duration", "hard:success-with-error", "hard:count-conservation"]);
export const evidenceSummaryNeeds = (evidence: unknown): { observed: boolean; ruleSetRevision: number | null } => {
  if (!record(evidence) || evidence.schemaVersion !== 4) return { observed: false, ruleSetRevision: null };
  const hits = records(evidence.methodResults).filter((m) => m.status === "succeeded" && m.anomalous === true && typeof m.methodId === "string");
  const revision = evidence.ruleSetRevision;
  const relation = hits.some((m) => isRelation(m.methodId as string));
  return {
    observed: hits.some((m) => OBSERVED_HARD_RULES.has(m.methodId as string)),
    ruleSetRevision: relation && typeof revision === "number" && Number.isSafeInteger(revision) && revision >= 0 ? revision : null,
  };
};
