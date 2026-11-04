import crypto from 'node:crypto';
import { HARD_RULES, type HardRuleDefinition, type HardRuleSeverity, type Severity } from './hardRuleCatalog.js';
import { SOFT_RULE, softRuleField, type SoftRuleOperator } from './softRuleCatalog.js';

/**
 * 룰 세트 계약(rule-set.md)의 순수 규칙: 요청 형식, 고정소수 변환, 내용 hash, 저장 검증 순서, 변경 요약.
 * DB를 읽지 않는다. 호출자가 현재 상태·되돌릴 스냅샷·활성 모델 수를 넘긴다.
 */
export const RULE_SET_CONTRACT = 'rule-set-v1';
/** 새 저장의 hash 버전. 1은 기존 행(r0 등) 대조용으로만 남는다(rule-set.md 리비전 형식). */
export const CONTENT_HASH_VERSION = 2;
export const THRESHOLD_MAX_LENGTH = 40;
export const SIGNIFICANT_DIGITS_MAX = 15;
const NOTE_MAX = 500;
const NAME_MAX = 120;
const CLIENT_REF_MAX = 40;
const DSL_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;

export class RuleSetError extends Error {
  constructor(readonly code: string, readonly status = 400, readonly detail: Record<string, unknown> = {}) { super(code); }
}
const rejected = (code: string) => new RuleSetError(code, 422);
const malformed = () => new RuleSetError('invalid_rule_set_request');

export type HardRuleState = HardRuleDefinition & { enabled: boolean };
/** 저장된 스냅샷의 이상 규칙 항목. 옛 행은 methodVersion·severity가 없거나 v1 값일 수 있다. */
export type StoredHardRule = { id: string; enabled: boolean; methodVersion?: string; severity?: unknown };
export type SoftRule = {
  id: string; name: string; field: string; operator: SoftRuleOperator; threshold: number; unit: string;
  severity: Severity; enabled: boolean; createdAtRevision: number; updatedAtRevision: number;
};
export type RuleSetSnapshot = { hardRules: HardRuleState[]; softRules: SoftRule[] };
export type StoredSnapshot = { hardRules: StoredHardRule[]; softRules: SoftRule[] };
export type SoftRuleRequest = {
  id: string | null; clientRef: string | null; name: string; field: string; operator: string;
  threshold: number; severity: string; enabled: boolean; unit?: string;
};
export type RuleSetRequest = {
  expectedRevision: number; confirmHardRuleDisable: boolean; restoreFromRevision: number | null; note: string | null;
  hardRules: { id: string; enabled: boolean; severity?: unknown }[]; softRules: SoftRuleRequest[];
};
export type RuleSetChange = { type: string; id: string | null; [key: string]: unknown };
export type RuleSetSummary = {
  hardEnabled: number; hardDisabled: number; hardUpdated: number; softAdded: number; softUpdated: number;
  softRemoved: number; softEnabled: number; softDisabled: number;
};

/** number의 최단 십진 표기를 숫자열과 소수점 위치로 편다(`Decimal(str(number))`와 같은 숫자열). */
const decimalParts = (value: number) => {
  const [mantissa, exponent = '0'] = String(Math.abs(value)).split('e');
  const [whole, fraction = ''] = mantissa.split('.');
  return { digits: whole + fraction, point: whole.length + Number(exponent) };
};
/** `format(Decimal(str(number)), "f")` + 후행 0·소수점 제거. 지수 표기 없음. -0은 0이다. */
export function decimalString(value: number): string {
  if (!Number.isFinite(value)) throw new RangeError('non_finite');
  if (value === 0) return '0';
  const { digits, point } = decimalParts(value);
  const padded = point <= 0 ? '0'.repeat(1 - point) + digits : digits.padEnd(point, '0');
  const split = point <= 0 ? 1 : point;
  const whole = padded.slice(0, split).replace(/^0+(?=\d)/, '');
  const fraction = padded.slice(split).replace(/0+$/, '');
  return `${value < 0 ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`;
}
export const significantDigits = (value: number) =>
  value === 0 ? 1 : decimalParts(value).digits.replace(/^0+/, '').replace(/0+$/, '').length;

/** 임계값 → hash·Detector 전달용 고정소수 문자열. 변환 불가면 null(저장 시 422 soft_rule_threshold_invalid). */
export function thresholdFixedDecimal(value: number, valueType: 'integer' | 'number'): string | null {
  if (!Number.isFinite(value) || significantDigits(value) > SIGNIFICANT_DIGITS_MAX) return null;
  if (valueType === 'integer' && !Number.isInteger(value)) return null;
  const text = decimalString(value);
  return text.length <= THRESHOLD_MAX_LENGTH && DSL_NUMBER.test(text) ? text : null;
}

const sortKeys = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortKeys((value as Record<string, unknown>)[key])]));
};
/** json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)의 UTF-8 SHA-256 hex. */
export const canonicalSha256 = (value: unknown) =>
  crypto.createHash('sha256').update(JSON.stringify(sortKeys(value)), 'utf8').digest('hex');
const byId = <T extends { id: string }>(left: T, right: T) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);

/** 정규 객체의 hash. v2는 이상 규칙에 severity를 넣고, v1은 r0 등 기존 행 대조용이다(직렬화는 같다). */
export function contentHash(snapshot: { hardRules: { id: string; enabled: boolean; severity?: unknown }[]; softRules: SoftRule[] }, version = CONTENT_HASH_VERSION): string {
  return canonicalSha256({
    hardRules: [...snapshot.hardRules].sort(byId).map(({ id, enabled, severity }) => (version === 1 ? { id, enabled } : { id, enabled, severity })),
    softRules: [...snapshot.softRules].sort(byId).map(({ id, field, operator, threshold, severity, enabled }) => ({
      id, field, operator, severity, enabled,
      threshold: thresholdFixedDecimal(threshold, softRuleField(field)?.valueType ?? 'number') ?? decimalString(threshold),
    })),
  });
}

const plainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const keysWithin = (value: Record<string, unknown>, required: string[], optional: string[]) =>
  required.every((key) => key in value) && Object.keys(value).every((key) => required.includes(key) || optional.includes(key));
/** PostgreSQL text·jsonb에 넣을 수 없는 문자(NUL, 짝 없는 서로게이트)가 없는 문자열. */
const storable = (value: string) => !/\u0000|\p{Cs}/u.test(value);
const revisionNumber = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;

function parseSoftRule(value: unknown): SoftRuleRequest {
  if (!plainObject(value) || !keysWithin(value, ['id', 'name', 'field', 'operator', 'threshold', 'severity', 'enabled'], ['clientRef', 'unit'])) throw malformed();
  const { id, clientRef, name, field, operator, threshold, severity, enabled, unit } = value;
  if ((id !== null && typeof id !== 'string') || typeof name !== 'string' || typeof field !== 'string' || typeof operator !== 'string' ||
      typeof threshold !== 'number' || typeof severity !== 'string' || typeof enabled !== 'boolean' ||
      (unit !== undefined && typeof unit !== 'string')) throw malformed();
  if (id === null ? typeof clientRef !== 'string' || !clientRef || [...clientRef].length > CLIENT_REF_MAX || !storable(clientRef) : clientRef !== undefined) throw malformed();
  return { id, clientRef: id === null ? clientRef as string : null, name, field, operator, threshold, severity, enabled, unit: unit as string | undefined };
}

/** 본문 키 집합·타입 검사(400). 값의 의미 검사는 planRuleSetChange. */
export function parseRuleSetRequest(body: unknown): RuleSetRequest {
  if (!plainObject(body) || !keysWithin(body, ['expectedRevision', 'hardRules', 'softRules'], ['confirmHardRuleDisable', 'restoreFromRevision', 'note'])) throw malformed();
  const { expectedRevision, confirmHardRuleDisable = false, restoreFromRevision = null, note = null, hardRules, softRules } = body;
  if (!revisionNumber(expectedRevision) || typeof confirmHardRuleDisable !== 'boolean' ||
      (restoreFromRevision !== null && !revisionNumber(restoreFromRevision)) ||
      (note !== null && (typeof note !== 'string' || [...note].length > NOTE_MAX || !storable(note))) ||
      !Array.isArray(hardRules) || !Array.isArray(softRules)) throw malformed();
  return {
    expectedRevision, confirmHardRuleDisable, restoreFromRevision: restoreFromRevision as number | null, note: note as string | null,
    hardRules: hardRules.map((rule) => {
      if (!plainObject(rule) || !keysWithin(rule, ['id', 'enabled'], ['severity']) || typeof rule.id !== 'string' || typeof rule.enabled !== 'boolean') throw malformed();
      return { id: rule.id, enabled: rule.enabled, ...(rule.severity === undefined ? {} : { severity: rule.severity }) };
    }),
    softRules: softRules.map(parseSoftRule),
  };
}

const editableSeverity = (rule: HardRuleDefinition) => rule.editable.includes('severity');
export const sameSeverity = (left: unknown, right: unknown) => JSON.stringify(sortKeys(left)) === JSON.stringify(sortKeys(right));
const LEVELS: readonly unknown[] = SOFT_RULE.severities;
/** 그 규칙의 내장 코드 전부를 정확히 담은 `{byCode}`면 내장 코드 순서로 돌려준다. 아니면 null. */
function normalizedByCode(rule: HardRuleDefinition, value: unknown): HardRuleSeverity | null {
  const codes = Object.keys((rule.severity as { byCode: Record<string, Severity> }).byCode);
  if (!plainObject(value) || !keysWithin(value, ['byCode'], []) || !plainObject(value.byCode)) return null;
  const byCode = value.byCode;
  if (Object.keys(byCode).length !== codes.length || !codes.every((code) => Object.hasOwn(byCode, code) && LEVELS.includes(byCode[code]))) return null;
  return { byCode: Object.fromEntries(codes.map((code) => [code, byCode[code] as Severity])) };
}
/**
 * 저장된 항목의 현재 해석(rule-set.md v1 항목 severity 공통 규칙). 조회·되돌리기 채움·무변경 비교가 모두 쓴다.
 * 스냅샷에 없으면 꺼짐·내장 기본값. 위험도는 현재 methodVersion 항목의 저장값만 읽고 그 밖은 내장 기본값이다.
 */
function resolveHardRule(rule: HardRuleDefinition, stored: StoredHardRule | undefined): HardRuleState {
  const saved = stored && editableSeverity(rule) && stored.methodVersion === rule.methodVersion ? normalizedByCode(rule, stored.severity) : null;
  return { ...rule, enabled: stored?.enabled ?? false, severity: saved ?? rule.severity };
}
export function projectHardRules(stored: StoredHardRule[]): HardRuleState[] {
  return HARD_RULES.map((rule) => resolveHardRule(rule, stored.find((item) => item.id === rule.id)));
}
/** 출처 항목의 위험도를 그대로 쓸 수 없는 경우(스냅샷에 없음·이전 methodVersion): 서버가 채우고 본문 값은 비교하지 않는다. */
const filledByServer = (rule: HardRuleDefinition, stored: StoredHardRule | undefined) => !stored || stored.methodVersion !== rule.methodVersion;

const softSame = (left: SoftRuleRequest, right: SoftRule) =>
  left.name === right.name && left.field === right.field && left.operator === right.operator &&
  left.threshold === right.threshold && left.severity === right.severity && left.enabled === right.enabled;

function checkRestore(request: RuleSetRequest, snapshot: StoredSnapshot) {
  const soft = new Map(snapshot.softRules.map((rule) => [rule.id, rule]));
  const sent = new Map(request.hardRules.map((rule) => [rule.id, rule]));
  const stored = new Map(snapshot.hardRules.map((rule) => [rule.id, rule]));
  const softSameAsSnapshot = request.softRules.length === snapshot.softRules.length &&
    new Set(request.softRules.map((rule) => rule.id)).size === snapshot.softRules.length &&
    request.softRules.every((rule) => rule.id !== null && soft.has(rule.id) && softSame(rule, soft.get(rule.id)!));
  // 스냅샷에 있으나 내장 목록에서 사라진 규칙은 켜기 값만 맞으면 아래 hard_rule_unknown으로 끝난다.
  const leftoverSame = snapshot.hardRules.every((rule) => sent.get(rule.id)?.enabled === rule.enabled || HARD_RULES.some((known) => known.id === rule.id));
  const builtInSame = HARD_RULES.every((rule) => {
    const body = sent.get(rule.id);
    const source = stored.get(rule.id);
    const filled = resolveHardRule(rule, source);
    if (!body) return !source;
    if (body.enabled !== filled.enabled) return false;
    if (body.severity === undefined) return true;
    // 이전 methodVersion 출처의 상태 규칙 위험도는 무시하고 서버가 채운다. 그 밖에는 채운 값과 같아야 한다(편집 불가 규칙도 이 검사가 먼저다).
    return (editableSeverity(rule) && source !== undefined && filledByServer(rule, source)) || sameSeverity(body.severity, filled.severity);
  });
  if (!softSameAsSnapshot || !leftoverSame || !builtInSame) throw rejected('restore_payload_mismatch');
}

function checkHardRules(request: RuleSetRequest, restore: StoredSnapshot | null) {
  const known = new Set(HARD_RULES.map((rule) => rule.id));
  const sent = request.hardRules.map((rule) => rule.id);
  const inSnapshot = new Set(restore?.hardRules.map((rule) => rule.id));
  for (const { id } of HARD_RULES) if (!sent.includes(id) && !(restore && !inSnapshot.has(id))) throw rejected(`hard_rule_missing:${id}`);
  for (const id of sent) if (!known.has(id)) throw rejected(`hard_rule_unknown:${id}`);
  sent.forEach((id, index) => { if (sent.indexOf(id) !== index) throw rejected(`hard_rule_duplicate:${id}`); });
}

/** 요청과 되돌릴 스냅샷으로 저장할 이상 규칙 상태를 만든다. 위험도 검사도 여기서 한다. */
function plannedHardRules(request: RuleSetRequest, restore: StoredSnapshot | null): HardRuleState[] {
  return HARD_RULES.map((rule) => {
    const body = request.hardRules.find((item) => item.id === rule.id);
    const source = restore?.hardRules.find((item) => item.id === rule.id);
    if (restore && (!body || (editableSeverity(rule) && filledByServer(rule, source)))) return resolveHardRule(rule, source);
    if (!editableSeverity(rule)) {
      if (body!.severity !== undefined && !sameSeverity(body!.severity, rule.severity)) throw rejected(`hard_rule_field_not_editable:${rule.id}`);
      return { ...rule, enabled: body!.enabled };
    }
    const severity = normalizedByCode(rule, body!.severity);
    if (!severity) throw rejected(`hard_rule_severity_invalid:${rule.id}`);
    return { ...rule, enabled: body!.enabled, severity };
  });
}

function checkSoftRules(request: RuleSetRequest, current: RuleSetSnapshot, restore: StoredSnapshot | null) {
  const existing = new Map(current.softRules.map((rule) => [rule.id, rule]));
  const restorable = new Set(restore?.softRules.map((rule) => rule.id));
  const reference = (rule: SoftRuleRequest) => rule.id ?? rule.clientRef!;
  const ids = request.softRules.flatMap((rule) => (rule.id === null ? [] : [rule.id]));
  for (const id of ids) if (!existing.has(id) && !restorable.has(id)) throw rejected(`soft_rule_unknown:${id}`);
  ids.forEach((id, index) => { if (ids.indexOf(id) !== index) throw rejected(`soft_rule_duplicate:${id}`); });
  const refs = request.softRules.flatMap((rule) => (rule.clientRef === null ? [] : [rule.clientRef]));
  if (new Set(refs).size !== refs.length) throw rejected('soft_rule_client_ref_duplicate');
  for (const rule of request.softRules) {
    const field = softRuleField(rule.field);
    if (!field || field.status !== 'supported') throw rejected(`soft_rule_field_unknown:${rule.field}`);
  }
  for (const rule of request.softRules) {
    if (rule.id !== null && existing.has(rule.id) && existing.get(rule.id)!.field !== rule.field) throw rejected(`soft_rule_field_immutable:${rule.id}`);
  }
  for (const rule of request.softRules) {
    if (rule.unit !== undefined && rule.unit !== softRuleField(rule.field)!.unit) throw rejected(`soft_rule_unit_mismatch:${reference(rule)}`);
  }
  for (const rule of request.softRules) {
    if (!(softRuleField(rule.field)!.operators as readonly string[]).includes(rule.operator)) throw rejected(`soft_rule_operator_invalid:${reference(rule)}`);
  }
  for (const rule of request.softRules) {
    if (thresholdFixedDecimal(rule.threshold, softRuleField(rule.field)!.valueType) === null) throw rejected(`soft_rule_threshold_invalid:${reference(rule)}`);
  }
  for (const rule of request.softRules) {
    if (!(SOFT_RULE.severities as readonly string[]).includes(rule.severity)) throw rejected(`soft_rule_severity_invalid:${reference(rule)}`);
  }
  const names = request.softRules.map((rule) => rule.name.trim());
  if (names.some((name) => !name || [...name].length > NAME_MAX || /[\u0000-\u001f\u007f]/.test(name) || !storable(name))) throw rejected('soft_rule_name_invalid');
  const folded = names.map((name) => name.toLowerCase());
  if (new Set(folded).size !== folded.length) throw rejected('soft_rule_name_duplicate');
  if (request.softRules.length > SOFT_RULE.maxRules) throw rejected('soft_rule_limit_exceeded');
}

const emptySummary = (): RuleSetSummary => ({ hardEnabled: 0, hardDisabled: 0, hardUpdated: 0, softAdded: 0, softUpdated: 0, softRemoved: 0, softEnabled: 0, softDisabled: 0 });
const SUMMARY_KEY: Record<string, keyof RuleSetSummary> = {
  hard_enabled: 'hardEnabled', hard_disabled: 'hardDisabled', hard_updated: 'hardUpdated', soft_added: 'softAdded', soft_updated: 'softUpdated',
  soft_removed: 'softRemoved', soft_enabled: 'softEnabled', soft_disabled: 'softDisabled',
};
const DIFF_KEYS = ['name', 'operator', 'threshold', 'severity', 'enabled'] as const;

export type RuleSetPlan = {
  snapshot: RuleSetSnapshot; contentHash: string; changes: RuleSetChange[]; summary: RuleSetSummary; hardDisabledAfterSave: string[];
};
type PlanInput = {
  request: RuleSetRequest; current: RuleSetSnapshot; restore: StoredSnapshot | null; activeModelCount: number; nextRevision: number;
  /** 새 규칙 id 배정. 미리보기는 저장하지 않으므로 null을 돌려준다. */
  assignId: (field: string, taken: Set<string>) => string | null;
};

/** rule-set.md 검증 순서(expectedRevision 다음부터)대로 검사하고 저장할 상태·변경 요약을 만든다. 첫 실패에서 RuleSetError. */
export function planRuleSetChange({ request, current, restore, activeModelCount, nextRevision, assignId }: PlanInput): RuleSetPlan {
  if (request.restoreFromRevision !== null) {
    if (!restore) throw rejected('restore_revision_unknown');
    checkRestore(request, restore);
  }
  checkHardRules(request, restore);
  const hardRules = plannedHardRules(request, restore);
  const before = new Map(current.hardRules.map((rule) => [rule.id, rule]));
  const changes: RuleSetChange[] = [];
  for (const rule of hardRules) {
    const previous = before.get(rule.id)!;
    if (previous.enabled !== rule.enabled) changes.push({ type: rule.enabled ? 'hard_enabled' : 'hard_disabled', id: rule.id, displayName: rule.displayName });
    if (!sameSeverity(previous.severity, rule.severity)) {
      changes.push({ type: 'hard_updated', id: rule.id, displayName: rule.displayName, diff: { severity: [previous.severity, rule.severity] } });
    }
  }
  if (changes.some((change) => change.type === 'hard_disabled') && !request.confirmHardRuleDisable) throw rejected('hard_rule_disable_confirmation_required');

  checkSoftRules(request, current, restore);
  const existing = new Map(current.softRules.map((rule) => [rule.id, rule]));
  const restorable = new Map(restore?.softRules.map((rule) => [rule.id, rule]));
  const taken = new Set([...existing.keys(), ...request.softRules.flatMap((rule) => (rule.id ? [rule.id] : []))]);
  const softRules: SoftRule[] = [];
  const softChanges: { key: string; change: RuleSetChange }[] = [];
  for (const rule of request.softRules) {
    const name = rule.name.trim();
    const values = {
      name, field: rule.field, operator: rule.operator as SoftRuleOperator, threshold: rule.threshold,
      unit: softRuleField(rule.field)!.unit, severity: rule.severity as Severity, enabled: rule.enabled,
    };
    const previous = rule.id ? existing.get(rule.id) : undefined;
    if (previous) {
      const diff = Object.fromEntries(DIFF_KEYS.filter((key) => previous[key] !== values[key]).map((key) => [key, [previous[key], values[key]]]));
      const keys = Object.keys(diff);
      softRules.push({ ...values, id: previous.id, createdAtRevision: previous.createdAtRevision, updatedAtRevision: keys.length ? nextRevision : previous.updatedAtRevision });
      if (keys.length === 1 && keys[0] === 'enabled') softChanges.push({ key: previous.id, change: { type: rule.enabled ? 'soft_enabled' : 'soft_disabled', id: previous.id, name } });
      else if (keys.length) softChanges.push({ key: previous.id, change: { type: 'soft_updated', id: previous.id, name, diff } });
      continue;
    }
    if (rule.id) {
      // 되돌리기: 그 사이 삭제된 규칙을 같은 id로 재생성(스냅샷 값 그대로, createdAtRevision 유지).
      softRules.push({ ...values, id: rule.id, createdAtRevision: restorable.get(rule.id)!.createdAtRevision, updatedAtRevision: nextRevision });
      softChanges.push({ key: rule.id, change: { type: 'soft_added', id: rule.id, name, restored: true } });
      continue;
    }
    const id = assignId(rule.field, taken);
    const key = id ?? `soft:${rule.field}-${rule.clientRef}`;
    taken.add(key);
    softRules.push({ ...values, id: key, createdAtRevision: nextRevision, updatedAtRevision: nextRevision });
    softChanges.push({ key, change: { type: 'soft_added', id, clientRef: rule.clientRef, name } });
  }
  softRules.sort(byId);
  softChanges.sort((left, right) => (left.key < right.key ? -1 : left.key > right.key ? 1 : 0));
  changes.push(...softChanges.map((item) => item.change));
  const kept = new Set(request.softRules.flatMap((rule) => (rule.id ? [rule.id] : [])));
  for (const rule of current.softRules) if (!kept.has(rule.id)) changes.push({ type: 'soft_removed', id: rule.id, name: rule.name });

  const enabledMethods = hardRules.filter((rule) => rule.enabled).length + softRules.filter((rule) => rule.enabled).length + activeModelCount;
  if (enabledMethods === 0) throw rejected('detection_methods_empty');
  const snapshot = { hardRules, softRules };
  const hash = contentHash(snapshot);
  const sameNames = softRules.every((rule) => existing.get(rule.id)?.name === rule.name);
  if (hash === contentHash(current) && sameNames) throw new RuleSetError('rule_set_unchanged', 409);
  const summary = emptySummary();
  for (const change of changes) summary[SUMMARY_KEY[change.type]] += 1;
  return { snapshot, contentHash: hash, changes, summary, hardDisabledAfterSave: hardRules.filter((rule) => !rule.enabled).map((rule) => rule.id) };
}
