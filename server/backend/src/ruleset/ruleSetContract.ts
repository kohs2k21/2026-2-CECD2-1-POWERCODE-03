import crypto from 'node:crypto';
import { HARD_RULES, type HardRuleDefinition, type HardRuleSeverity, type Severity } from './hardRuleCatalog.js';
import { SOFT_RULE, softRuleField, type SoftRuleOperator } from './methodCatalog.js';

/**
 * 룰 세트 계약(rule-set.md)의 순수 규칙: 요청 형식, 고정소수 변환, 내용 hash, 저장 검증 순서, 변경 요약.
 * DB를 읽지 않는다. 호출자가 현재 상태·되돌릴 스냅샷·활성 모델 수를 넘긴다.
 * v3(rule-set.md v3.1~v3.8): 관계 규칙(`relationRules`)·프로토콜 규칙(`protocolRules`)·소프트룰 `scope`·내장 하드룰 6개.
 */
export const RULE_SET_CONTRACT = 'rule-set-v1';
/**
 * 새 저장의 hash 버전(rule-set.md v3.7: API v3 배포 뒤 모든 저장은 3). 1·2는 기존 행 대조용으로만 남는다.
 * 배포 시점 상수이고 실행 중 전환 스위치는 없다. Detector v3 배포 확인 뒤에만 이 값의 API를 연다.
 */
export const CONTENT_HASH_VERSION = 3;
export const THRESHOLD_MAX_LENGTH = 40;
export const SIGNIFICANT_DIGITS_MAX = 15;
const NOTE_MAX = 500;
const NAME_MAX = 120;
const CLIENT_REF_MAX = 40;
const DSL_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
/** rule-set.md v3.6 상한. 경로 상한 64/64는 판정 경로 상한(protocol-rules.md 3.2)과 같은 값. */
export const RELATION_RULE_MAX = 200;
export const PROTOCOL_RULE_MAX = 500;
export const PROTOCOL_PATHS_MAX = 64;
export const PROTOCOL_PATH_STEPS_MAX = 64;
/** 원천 값 문자열 길이(코드 포인트). ruleCandidates.ts INTERFACE_MAX와 같다. */
export const SOURCE_VALUE_MAX = 512;
const BY_CODE_KEYS_MAX = 200;
const BY_CODE_KEY = /^[\x21-\x7e]{1,64}$/;
/** 스냅샷 JSON(UTF-8) 상한. 계약은 API·DB에 맡긴다(rule-set.md v3.6) — 저장 본문 상한(라우터 2MB)보다 넉넉하게. */
export const SNAPSHOT_BYTES_MAX = 4 * 1024 * 1024;
/** `detection_methods_empty`에서 세지 않는 내장 하드룰(거래 행 도착 때만 평가, rule-set.md v3.6). */
const TRANSACTION_LEVEL_HARD_RULES = new Set(['hard:transaction-failed', 'hard:unregistered-interface']);
export const UNREGISTERED_INTERFACE_RULE = 'hard:unregistered-interface';
const RELATION_KINDS = ['precedes', 'message_handoff', 'required_step', 'allowed_branch'] as const;
export type RelationKind = (typeof RELATION_KINDS)[number];
/** 경고 문장. 관계 문장은 relation-rules.md 4절 그대로, 프로토콜 문장은 WebUI 결정(protocol-settings-ui.md). */
export const WARNING_MESSAGES = {
  relation_handoff_without_precedes: '짝 선행 규칙이 없어 선행이 다르면 인계를 확인하지 않습니다.',
  protocol_unregistered_without_entries: '켜진 프로토콜 항목이 없어 등록되지 않은 인터페이스 규칙이 아무것도 판정하지 않습니다.',
} as const;

export class RuleSetError extends Error {
  constructor(readonly code: string, readonly status = 400, readonly detail: Record<string, unknown> = {}) { super(code); }
}
const rejected = (code: string) => new RuleSetError(code, 422);
const malformed = () => new RuleSetError('invalid_rule_set_request');

export type HardRuleState = HardRuleDefinition & { enabled: boolean };
/** 저장된 스냅샷의 이상 규칙 항목. 옛 행은 methodVersion·severity가 없거나 v1 값일 수 있다. */
export type StoredHardRule = { id: string; enabled: boolean; methodVersion?: string; severity?: unknown };
/** 소프트룰 적용 범위(relation-rules.md 4절). interfaceId는 표시용(hash·비교 밖). */
export type SoftRuleScope = { processId: string; adapterType?: string; processHubId?: string; interfaceId?: string } | null;
export type SoftRule = {
  id: string; name: string; field: string; operator: SoftRuleOperator; threshold: number; unit: string;
  severity: Severity; enabled: boolean; createdAtRevision: number; updatedAtRevision: number;
  /** v3 스냅샷은 항상 키를 둔다(전역 = null). v1·v2 행에는 없다(= null로 읽음). */
  scope?: SoftRuleScope;
};
export type ProcessRef = { processId: string };
export type RelationMatch = { from: 'OUT'; to: 'IN'; key: 'message_id' };
export type RelationRule = {
  id: string; kind: RelationKind; scope: Record<string, string> | null; from: ProcessRef; to: ProcessRef | null; match: RelationMatch | null;
  severity: Severity; enabled: boolean; source: unknown; createdAtRevision: number; updatedAtRevision: number;
};
export type ProtocolPath = { channels: string[]; source: unknown };
export type ProtocolRule = {
  id: string; interfaceId: string; paths: ProtocolPath[]; severity: Severity; enabled: boolean; source: unknown;
  createdAtRevision: number; updatedAtRevision: number;
};
export type RuleSetSnapshot = { hardRules: HardRuleState[]; softRules: SoftRule[]; relationRules: RelationRule[]; protocolRules: ProtocolRule[] };
export type StoredSnapshot = { hardRules: StoredHardRule[]; softRules: SoftRule[]; relationRules: RelationRule[]; protocolRules: ProtocolRule[] };
export type SoftRuleRequest = {
  id: string | null; clientRef: string | null; name: string; field: string; operator: string;
  threshold: number; severity: string; enabled: boolean; unit?: string;
  /** 생략 = null(전역). 모양 검사는 planRuleSetChange(422 soft_rule_scope_invalid). */
  scope: unknown;
};
/** 관계·프로토콜 항목 요청. scope·from·to·match·channels의 모양은 422 단계에서 본다(rule-set.md v3.6 표). */
export type RelationRuleRequest = {
  id: string | null; clientRef: string | null; kind: string; scope: unknown; from: unknown; to: unknown; match: unknown;
  severity: string; enabled: boolean; source?: unknown;
};
export type ProtocolPathRequest = { channels: unknown; source?: unknown };
export type ProtocolRuleRequest = {
  id: string | null; clientRef: string | null; interfaceId: string; paths: ProtocolPathRequest[]; severity: string; enabled: boolean; source?: unknown;
};
export type RuleSetRequest = {
  expectedRevision: number; confirmHardRuleDisable: boolean; restoreFromRevision: number | null; note: string | null;
  hardRules: { id: string; enabled: boolean; severity?: unknown }[]; softRules: SoftRuleRequest[];
  relationRules: RelationRuleRequest[]; protocolRules: ProtocolRuleRequest[];
};
export type RuleSetChange = { type: string; id: string | null; [key: string]: unknown };
export type RuleSetSummary = {
  hardEnabled: number; hardDisabled: number; hardUpdated: number; softAdded: number; softUpdated: number;
  softRemoved: number; softEnabled: number; softDisabled: number;
  relationAdded: number; relationRemoved: number; relationUpdated: number; relationEnabled: number; relationDisabled: number;
  protocolAdded: number; protocolRemoved: number; protocolUpdated: number; protocolEnabled: number; protocolDisabled: number;
};
export type RuleSetWarning = { code: keyof typeof WARNING_MESSAGES; ruleId: string | null; message: string; clientRef?: string };

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

/**
 * 키를 정렬한 객체로 다시 만든다. 키가 모두 고정 ASCII 이름일 때만 Python `sort_keys`와 같은 문자열이 된다 —
 * 정수처럼 생긴 키("9"·"10")는 JS가 숫자 순으로 먼저 내보낸다(rule-set.md v3.3). 그래서 v3 정규 객체의 `byCode`는 배열로 바꾼 뒤 넣는다.
 */
const sortKeys = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortKeys((value as Record<string, unknown>)[key])]));
};
/** json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)의 UTF-8 SHA-256 hex. */
export const canonicalJson = (value: unknown) => JSON.stringify(sortKeys(value));
export const canonicalSha256 = (value: unknown) => crypto.createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');
const byId = <T extends { id: string }>(left: T, right: T) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);

/**
 * 유니코드 코드 포인트 순 비교(Python `str` 비교와 같음). JS 기본 비교는 UTF-16 단위 순이라 U+E000~U+FFFF와 U+10000 이상이 섞이면 다르다.
 * 다른 첫 단위에서 서로게이트(U+D800~U+DFFF)를 U+FFFF 위로 올려 비교한다(짝 맞는 문자열 전제 — 저장 검증이 짝 없는 서로게이트를 막는다).
 */
export function compareCodePoints(left: string, right: string): number {
  const length = Math.min(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    let a = left.charCodeAt(index);
    let b = right.charCodeAt(index);
    if (a === b) continue;
    if (a >= 0xd800) a = a >= 0xe000 ? a - 0x800 : a + 0x2000;
    if (b >= 0xd800) b = b >= 0xe000 ? b - 0x800 : b + 0x2000;
    return a - b;
  }
  return left.length - right.length;
}
/** pathKey(protocol-rules.md 5.3): 채널마다 `%` → `%25` 다음 `:` → `%3A`(이 순서) 뒤 `:`로 잇는다. 쪼개 읽지 않는다. */
export const pathKey = (channels: readonly string[]) => channels.map((channel) => channel.replaceAll('%', '%25').replaceAll(':', '%3A')).join(':');
const byPathKey = (left: readonly string[], right: readonly string[]) => compareCodePoints(pathKey(left), pathKey(right));

const plainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const keysWithin = (value: Record<string, unknown>, required: string[], optional: string[]) =>
  required.every((key) => key in value) && Object.keys(value).every((key) => required.includes(key) || optional.includes(key));
const exactKeys = (value: Record<string, unknown>, keys: string[]) => keysWithin(value, keys, []);
/** PostgreSQL text·jsonb에 넣을 수 없는 문자(NUL, 짝 없는 서로게이트)가 없는 문자열. */
const storable = (value: string) => !/\u0000|\p{Cs}/u.test(value);
const revisionNumber = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const LEVELS: readonly unknown[] = SOFT_RULE.severities;
const isLevel = (value: unknown): value is Severity => LEVELS.includes(value);

/** process-graph.md `graph_value_absent`: ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 `""`·`\N`. */
export const graphValueAbsent = (value: string) => {
  const trimmed = value.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '');
  return trimmed === '' || trimmed === '\\N';
};
/** rule-set.md v3.6 문자열 공통 규칙: 원래 값 그대로(trim 없음), 없음 값 거절, 1~512 코드 포인트, storable. */
export const sourceValueValid = (value: unknown): value is string =>
  typeof value === 'string' && !graphValueAbsent(value) && [...value].length <= SOURCE_VALUE_MAX && storable(value);

// ---------------------------------------------------------------------------------------------------------------
// 정규 객체(hash 입력) — rule-set.md 리비전 형식 절(v1·v2)과 v3.3

/** v3 `byCode`: `[[code, level], …]` 코드 포인트 순. `default`가 있으면 함께. 단일 문자열은 그대로. */
const canonicalSeverityV3 = (severity: unknown): unknown => {
  if (!plainObject(severity) || !plainObject(severity.byCode)) return severity;
  const byCode = severity.byCode;
  const entries = Object.keys(byCode).sort(compareCodePoints).map((code) => [code, byCode[code]]);
  return { byCode: entries, ...('default' in severity ? { default: severity.default } : {}) };
};
/** 소프트룰 scope 정규형: 판정 키(processId·adapterType·processHubId) 중 값 있는 키만, 없으면 null. interfaceId는 표시용이라 뺀다. */
export const canonicalSoftScope = (scope: unknown): unknown => {
  if (scope === null || scope === undefined) return null;
  if (!plainObject(scope)) return scope;
  const kept = Object.fromEntries(['processId', 'adapterType', 'processHubId'].filter((key) => scope[key] !== undefined).map((key) => [key, scope[key]]));
  return Object.keys(kept).length ? kept : null;
};
/** 관계 규칙 scope 정규형: required_step은 `{interfaceId}`(판정 키), 그 밖은 `adapterType`만(없으면 null). */
export const canonicalRelationScope = (kind: unknown, scope: unknown): unknown => {
  if (!plainObject(scope)) return scope ?? null;
  if (kind === 'required_step') return { interfaceId: scope.interfaceId };
  return scope.adapterType === undefined ? null : { adapterType: scope.adapterType };
};
const processRefOf = (value: unknown) => (plainObject(value) ? { processId: value.processId } : value ?? null);
/** 관계 규칙 조건(kind·scope·from·to·match)의 정규형 — 조건 불변·중복 비교와 hash의 공통 부분. */
const relationCondition = (rule: { kind: unknown; scope: unknown; from: unknown; to: unknown; match: unknown }) => ({
  kind: rule.kind, scope: canonicalRelationScope(rule.kind, rule.scope), from: processRefOf(rule.from), to: processRefOf(rule.to),
  match: plainObject(rule.match) ? { from: rule.match.from, to: rule.match.to, key: rule.match.key } : rule.match ?? null,
});
const relationConditionKey = (rule: Parameters<typeof relationCondition>[0]) => canonicalJson(relationCondition(rule));
const channelsOf = (path: unknown): unknown => (plainObject(path) ? path.channels : path);
const sortedChannelLists = (paths: unknown[]) => {
  const lists = paths.map(channelsOf);
  return lists.every((list) => Array.isArray(list) && list.every((item) => typeof item === 'string'))
    ? [...(lists as string[][])].sort(byPathKey) : lists;
};

type HashSnapshot = {
  hardRules: { id: string; enabled: boolean; severity?: unknown }[];
  softRules: Pick<SoftRule, 'id' | 'field' | 'operator' | 'threshold' | 'severity' | 'enabled' | 'scope'>[];
  relationRules?: Pick<RelationRule, 'id' | 'kind' | 'scope' | 'from' | 'to' | 'match' | 'severity' | 'enabled'>[];
  protocolRules?: { id: string; interfaceId: string; paths: unknown[]; severity: string; enabled: boolean }[];
};
const thresholdText = (rule: { field: string; threshold: number }) =>
  thresholdFixedDecimal(rule.threshold, softRuleField(rule.field)?.valueType ?? 'number') ?? decimalString(rule.threshold);

/** 정규 객체. v1은 이상 규칙 `{id, enabled}`, v2는 `severity`를 더하고, v3는 네 배열·`byCode` 배열·scope(rule-set.md v3.3). */
export function canonicalObject(snapshot: HashSnapshot, version = CONTENT_HASH_VERSION): unknown {
  const softRules = [...snapshot.softRules].sort(byId).map((rule) => ({
    id: rule.id, field: rule.field, operator: rule.operator, severity: rule.severity, enabled: rule.enabled, threshold: thresholdText(rule),
    ...(version >= 3 ? { scope: canonicalSoftScope(rule.scope) } : {}),
  }));
  const hardRules = [...snapshot.hardRules].sort(byId).map(({ id, enabled, severity }) => (
    version === 1 ? { id, enabled } : { id, enabled, severity: version >= 3 ? canonicalSeverityV3(severity) : severity }));
  if (version < 3) return { hardRules, softRules };
  return {
    hardRules, softRules,
    relationRules: [...(snapshot.relationRules ?? [])].sort(byId).map((rule) => ({
      id: rule.id, ...relationCondition(rule), severity: rule.severity, enabled: rule.enabled,
    })),
    protocolRules: [...(snapshot.protocolRules ?? [])].sort(byId).map((rule) => ({
      id: rule.id, interfaceId: rule.interfaceId, paths: sortedChannelLists(rule.paths), severity: rule.severity, enabled: rule.enabled,
    })),
  };
}
/** 정규 객체의 hash. 같은 `contentHashVersion`끼리만 비교한다. */
export const contentHash = (snapshot: HashSnapshot, version = CONTENT_HASH_VERSION): string => canonicalSha256(canonicalObject(snapshot, version));

// ---------------------------------------------------------------------------------------------------------------
// 요청 파싱(400) — 본문 키 집합·타입

const clientRefValid = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && [...value].length <= CLIENT_REF_MAX && storable(value);
/** 새 항목(id null)은 clientRef 필수, 기존 항목은 clientRef 금지(소프트룰과 같음). */
const refOf = (id: unknown, clientRef: unknown) => {
  if (id === null ? !clientRefValid(clientRef) : clientRef !== undefined) throw malformed();
  return id === null ? clientRef as string : null;
};

function parseSoftRule(value: unknown): SoftRuleRequest {
  if (!plainObject(value) || !keysWithin(value, ['id', 'name', 'field', 'operator', 'threshold', 'severity', 'enabled'], ['clientRef', 'unit', 'scope'])) throw malformed();
  const { id, clientRef, name, field, operator, threshold, severity, enabled, unit, scope = null } = value;
  if ((id !== null && typeof id !== 'string') || typeof name !== 'string' || typeof field !== 'string' || typeof operator !== 'string' ||
      typeof threshold !== 'number' || typeof severity !== 'string' || typeof enabled !== 'boolean' ||
      (unit !== undefined && typeof unit !== 'string')) throw malformed();
  return { id, clientRef: refOf(id, clientRef), name, field, operator, threshold, severity, enabled, unit: unit as string | undefined, scope };
}
function parseRelationRule(value: unknown): RelationRuleRequest {
  if (!plainObject(value) || !keysWithin(value, ['id', 'kind', 'scope', 'from', 'to', 'match', 'severity', 'enabled'], ['clientRef', 'source'])) throw malformed();
  const { id, clientRef, kind, scope, from, to, match, severity, enabled, source } = value;
  if ((id !== null && typeof id !== 'string') || typeof kind !== 'string' || typeof severity !== 'string' || typeof enabled !== 'boolean') throw malformed();
  return { id, clientRef: refOf(id, clientRef), kind, scope, from, to, match, severity, enabled, ...(source === undefined ? {} : { source }) };
}
function parseProtocolRule(value: unknown): ProtocolRuleRequest {
  if (!plainObject(value) || !keysWithin(value, ['id', 'interfaceId', 'paths', 'severity', 'enabled'], ['clientRef', 'source'])) throw malformed();
  const { id, clientRef, interfaceId, paths, severity, enabled, source } = value;
  if ((id !== null && typeof id !== 'string') || typeof interfaceId !== 'string' || !Array.isArray(paths) ||
      typeof severity !== 'string' || typeof enabled !== 'boolean') throw malformed();
  return {
    id, clientRef: refOf(id, clientRef), interfaceId, severity, enabled, ...(source === undefined ? {} : { source }),
    paths: paths.map((path) => {
      if (!plainObject(path) || !keysWithin(path, ['channels'], ['source'])) throw malformed();
      return { channels: path.channels, ...(path.source === undefined ? {} : { source: path.source }) };
    }),
  };
}

/** 본문 키 집합·타입 검사(400). v3는 `relationRules`·`protocolRules` 필수(rule-set.md v3.6 — 없음 = 유지로 두지 않음). 값의 의미 검사는 planRuleSetChange. */
export function parseRuleSetRequest(body: unknown): RuleSetRequest {
  if (!plainObject(body) || !keysWithin(body, ['expectedRevision', 'hardRules', 'softRules', 'relationRules', 'protocolRules'],
    ['confirmHardRuleDisable', 'restoreFromRevision', 'note'])) throw malformed();
  const { expectedRevision, confirmHardRuleDisable = false, restoreFromRevision = null, note = null, hardRules, softRules, relationRules, protocolRules } = body;
  if (!revisionNumber(expectedRevision) || typeof confirmHardRuleDisable !== 'boolean' ||
      (restoreFromRevision !== null && !revisionNumber(restoreFromRevision)) ||
      (note !== null && (typeof note !== 'string' || [...note].length > NOTE_MAX || !storable(note))) ||
      !Array.isArray(hardRules) || !Array.isArray(softRules) || !Array.isArray(relationRules) || !Array.isArray(protocolRules)) throw malformed();
  return {
    expectedRevision, confirmHardRuleDisable, restoreFromRevision: restoreFromRevision as number | null, note: note as string | null,
    hardRules: hardRules.map((rule) => {
      if (!plainObject(rule) || !keysWithin(rule, ['id', 'enabled'], ['severity']) || typeof rule.id !== 'string' || typeof rule.enabled !== 'boolean') throw malformed();
      return { id: rule.id, enabled: rule.enabled, ...(rule.severity === undefined ? {} : { severity: rule.severity }) };
    }),
    softRules: softRules.map(parseSoftRule),
    relationRules: relationRules.map(parseRelationRule),
    protocolRules: protocolRules.map(parseProtocolRule),
  };
}

// ---------------------------------------------------------------------------------------------------------------
// 이상 규칙 위험도 해석

const editableSeverity = (rule: HardRuleDefinition) => rule.editable.includes('severity');
export const sameSeverity = (left: unknown, right: unknown) => canonicalJson(left) === canonicalJson(right);
/**
 * 그 규칙이 저장할 수 있는 위험도면 정규화해 돌려준다. 아니면 null.
 * - 단일 문자열 규칙(`hard:unregistered-interface`): 3단계 문자열
 * - `{byCode, default}` 규칙(`hard:transaction-failed`, rule-set.md v3.6): 키 정확히 둘, `byCode` 키 인쇄 가능 ASCII 1~64자·200개 이하, 값·default 3단계
 * - `{byCode}` 규칙(`hard:process-status`): 그 규칙의 내장 코드 전부를 정확히(내장 코드 순서로)
 */
function validSeverity(rule: HardRuleDefinition, value: unknown): HardRuleSeverity | null {
  const builtIn = rule.severity;
  if (typeof builtIn === 'string') return isLevel(value) ? value : null;
  if (!plainObject(value) || !plainObject(value.byCode)) return null;
  const byCode = value.byCode;
  if (builtIn.default !== undefined) {
    const codes = Object.keys(byCode);
    if (!exactKeys(value, ['byCode', 'default']) || !isLevel(value.default) || codes.length > BY_CODE_KEYS_MAX ||
        !codes.every((code) => BY_CODE_KEY.test(code) && isLevel(byCode[code]))) return null;
    return { byCode: Object.fromEntries(codes.sort(compareCodePoints).map((code) => [code, byCode[code] as Severity])), default: value.default };
  }
  const codes = Object.keys(builtIn.byCode);
  if (!exactKeys(value, ['byCode']) || Object.keys(byCode).length !== codes.length ||
      !codes.every((code) => Object.hasOwn(byCode, code) && isLevel(byCode[code]))) return null;
  return { byCode: Object.fromEntries(codes.map((code) => [code, byCode[code] as Severity])) };
}
/**
 * 저장된 항목의 현재 해석(rule-set.md v1 항목 severity 공통 규칙). 조회·되돌리기 채움·무변경 비교가 모두 쓴다.
 * 스냅샷에 없으면 꺼짐·내장 기본값. 위험도는 현재 methodVersion 항목의 저장값만 읽고 그 밖은 내장 기본값이다.
 */
function resolveHardRule(rule: HardRuleDefinition, stored: StoredHardRule | undefined): HardRuleState {
  const saved = stored && editableSeverity(rule) && stored.methodVersion === rule.methodVersion ? validSeverity(rule, stored.severity) : null;
  return { ...rule, enabled: stored?.enabled ?? false, severity: saved ?? rule.severity };
}
export function projectHardRules(stored: StoredHardRule[]): HardRuleState[] {
  return HARD_RULES.map((rule) => resolveHardRule(rule, stored.find((item) => item.id === rule.id)));
}
/** 출처 항목의 위험도를 그대로 쓸 수 없는 경우(스냅샷에 없음·이전 methodVersion): 서버가 채우고 본문 값은 비교하지 않는다. */
const filledByServer = (rule: HardRuleDefinition, stored: StoredHardRule | undefined) => !stored || stored.methodVersion !== rule.methodVersion;

// ---------------------------------------------------------------------------------------------------------------
// 되돌리기 비교 — v3 정규 객체(채운 뒤 기준) + 소프트룰 name. source·표시용 interfaceId는 비교 밖(rule-set.md v3.6 되돌리기)

const softSame = (left: SoftRuleRequest, right: SoftRule) =>
  left.name === right.name && left.field === right.field && left.operator === right.operator &&
  left.threshold === right.threshold && left.severity === right.severity && left.enabled === right.enabled &&
  canonicalJson(canonicalSoftScope(left.scope)) === canonicalJson(canonicalSoftScope(right.scope ?? null));
const relationKey = (rule: RelationRuleRequest | RelationRule) => canonicalJson({ ...relationCondition(rule), severity: rule.severity, enabled: rule.enabled });
const protocolKey = (rule: ProtocolRuleRequest | ProtocolRule) =>
  canonicalJson({ interfaceId: rule.interfaceId, paths: sortedChannelLists(rule.paths), severity: rule.severity, enabled: rule.enabled });
/** 요청 배열이 스냅샷 배열과 id 집합·내용이 같은가(id 중복·없는 id·null id면 다름). */
function sameItems<R extends { id: string | null }, S extends { id: string }>(requested: R[], snapshot: S[], same: (left: R, right: S) => boolean) {
  const byKey = new Map(snapshot.map((item) => [item.id, item]));
  return requested.length === snapshot.length && new Set(requested.map((item) => item.id)).size === snapshot.length &&
    requested.every((item) => item.id !== null && byKey.has(item.id) && same(item, byKey.get(item.id)!));
}

function checkRestore(request: RuleSetRequest, snapshot: StoredSnapshot) {
  const sent = new Map(request.hardRules.map((rule) => [rule.id, rule]));
  const stored = new Map(snapshot.hardRules.map((rule) => [rule.id, rule]));
  const softSameAsSnapshot = sameItems(request.softRules, snapshot.softRules, softSame);
  const relationSame = sameItems(request.relationRules, snapshot.relationRules, (left, right) => relationKey(left) === relationKey(right));
  const protocolSame = sameItems(request.protocolRules, snapshot.protocolRules, (left, right) => protocolKey(left) === protocolKey(right));
  // 스냅샷에 있으나 내장 목록에서 사라진 규칙은 켜기 값만 맞으면 아래 hard_rule_unknown으로 끝난다.
  const leftoverSame = snapshot.hardRules.every((rule) => sent.get(rule.id)?.enabled === rule.enabled || HARD_RULES.some((known) => known.id === rule.id));
  const builtInSame = HARD_RULES.every((rule) => {
    const body = sent.get(rule.id);
    const source = stored.get(rule.id);
    const filled = resolveHardRule(rule, source);
    if (!body) return !source;
    if (body.enabled !== filled.enabled) return false;
    if (body.severity === undefined) return true;
    // 이전 methodVersion 출처의 편집 가능 규칙 위험도는 무시하고 서버가 채운다. 그 밖에는 채운 값과 같아야 한다(편집 불가 규칙도 이 검사가 먼저다).
    if (editableSeverity(rule) && source !== undefined && filledByServer(rule, source)) return true;
    return sameSeverity(canonicalSeverityV3(validSeverity(rule, body.severity) ?? body.severity), canonicalSeverityV3(filled.severity));
  });
  if (!softSameAsSnapshot || !relationSame || !protocolSame || !leftoverSame || !builtInSame) throw rejected('restore_payload_mismatch');
}

// ---------------------------------------------------------------------------------------------------------------
// 이상 규칙 검사

function checkHardRules(request: RuleSetRequest, restore: StoredSnapshot | null) {
  const known = new Set(HARD_RULES.map((rule) => rule.id));
  const sent = request.hardRules.map((rule) => rule.id);
  const inSnapshot = new Set(restore?.hardRules.map((rule) => rule.id));
  for (const { id } of HARD_RULES) if (!sent.includes(id) && !(restore && !inSnapshot.has(id))) throw rejected(`hard_rule_missing:${id}`);
  for (const id of sent) if (!known.has(id)) throw rejected(`hard_rule_unknown:${id}`);
  sent.forEach((id, index) => { if (sent.indexOf(id) !== index) throw rejected(`hard_rule_duplicate:${id}`); });
}

/** v2부터 있던 위험도 편집 규칙(`hard:process-status`)은 v2 표 자리에서, v3 새 규칙 2개는 v3 표 자리(소프트룰 검사 다음)에서 검사한다. */
const checkedAfterSoftRules = (rule: HardRuleDefinition) => TRANSACTION_LEVEL_HARD_RULES.has(rule.id);

/** 요청과 되돌릴 스냅샷으로 저장할 이상 규칙 상태를 만든다. 위험도가 틀린 v3 새 규칙은 `deferred`에 담아 나중에 거절한다. */
function plannedHardRules(request: RuleSetRequest, restore: StoredSnapshot | null) {
  const deferred: string[] = [];
  const rules = HARD_RULES.map((rule): HardRuleState => {
    const body = request.hardRules.find((item) => item.id === rule.id);
    const source = restore?.hardRules.find((item) => item.id === rule.id);
    if (restore && (!body || (editableSeverity(rule) && filledByServer(rule, source)))) return resolveHardRule(rule, source);
    if (!editableSeverity(rule)) {
      if (body!.severity !== undefined && !sameSeverity(body!.severity, rule.severity)) throw rejected(`hard_rule_field_not_editable:${rule.id}`);
      return { ...rule, enabled: body!.enabled };
    }
    const severity = validSeverity(rule, body!.severity);
    if (!severity) {
      if (!checkedAfterSoftRules(rule)) throw rejected(`hard_rule_severity_invalid:${rule.id}`);
      deferred.push(rule.id);
      return { ...rule, enabled: body!.enabled };
    }
    return { ...rule, enabled: body!.enabled, severity };
  });
  return { rules, deferred };
}

// ---------------------------------------------------------------------------------------------------------------
// 이상 징후 규칙 검사

const softScopeValid = (scope: unknown) => scope === null || (plainObject(scope) && 'processId' in scope &&
  Object.keys(scope).every((key) => ['processId', 'adapterType', 'processHubId', 'interfaceId'].includes(key)) &&
  Object.values(scope).every(sourceValueValid));

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
/** v3 표: scope 모양, 기존 규칙의 scope 변경(정규형 기준 — 표시용 interfaceId는 비교 밖). */
function checkSoftScopes(request: RuleSetRequest, current: RuleSetSnapshot) {
  for (const rule of request.softRules) if (!softScopeValid(rule.scope)) throw rejected(`soft_rule_scope_invalid:${rule.id ?? rule.clientRef}`);
  const existing = new Map(current.softRules.map((rule) => [rule.id, rule]));
  for (const rule of request.softRules) {
    const before = rule.id === null ? undefined : existing.get(rule.id);
    if (before && canonicalJson(canonicalSoftScope(before.scope ?? null)) !== canonicalJson(canonicalSoftScope(rule.scope))) {
      throw rejected(`soft_rule_scope_immutable:${rule.id}`);
    }
  }
}

// ---------------------------------------------------------------------------------------------------------------
// 관계 규칙 검사(rule-set.md v3.6 표, relation-rules.md 2절)

const processRefValid = (value: unknown) => plainObject(value) && exactKeys(value, ['processId']) && sourceValueValid(value.processId);
function relationShapeValid(rule: RelationRuleRequest) {
  const kind = rule.kind as RelationKind;
  if (!processRefValid(rule.from)) return false;
  if (kind === 'required_step' ? rule.to !== null : !processRefValid(rule.to)) return false;
  // 자기 자신이 선행인 규칙(from = to)은 비교할 간선이 없다(rule-set.md v3.6 — Detector는 invalid_relation_rule로 시도 실패).
  if (kind !== 'required_step' && (rule.to as ProcessRef).processId === (rule.from as ProcessRef).processId) return false;
  if (kind === 'message_handoff') {
    const match = rule.match;
    if (!plainObject(match) || !exactKeys(match, ['from', 'to', 'key']) || match.from !== 'OUT' || match.to !== 'IN' || match.key !== 'message_id') return false;
  } else if (rule.match !== null) return false;
  const scope = rule.scope;
  if (kind === 'required_step') return plainObject(scope) && exactKeys(scope, ['interfaceId']) && sourceValueValid(scope.interfaceId);
  return scope === null || (plainObject(scope) && Object.keys(scope).length > 0 &&
    Object.keys(scope).every((key) => key === 'interfaceId' || key === 'adapterType') && Object.values(scope).every(sourceValueValid));
}

function checkRelationRules(request: RuleSetRequest, current: RuleSetSnapshot, restore: StoredSnapshot | null) {
  const existing = new Map(current.relationRules.map((rule) => [rule.id, rule]));
  const restorable = new Set(restore?.relationRules.map((rule) => rule.id));
  const reference = (rule: RelationRuleRequest) => rule.id ?? rule.clientRef!;
  const ids = request.relationRules.flatMap((rule) => (rule.id === null ? [] : [rule.id]));
  for (const id of ids) if (!existing.has(id) && !restorable.has(id)) throw rejected(`relation_rule_unknown:${id}`);
  ids.forEach((id, index) => { if (ids.indexOf(id) !== index) throw rejected(`relation_rule_duplicate:${id}`); });
  const refs = request.relationRules.flatMap((rule) => (rule.clientRef === null ? [] : [rule.clientRef]));
  if (new Set(refs).size !== refs.length) throw rejected('relation_rule_client_ref_duplicate');
  for (const rule of request.relationRules) {
    if (!(RELATION_KINDS as readonly string[]).includes(rule.kind)) throw rejected(`relation_rule_kind_unknown:${reference(rule)}`);
  }
  for (const rule of request.relationRules) if (!relationShapeValid(rule)) throw rejected(`relation_rule_shape_invalid:${reference(rule)}`);
  for (const rule of request.relationRules) {
    const before = rule.id === null ? undefined : existing.get(rule.id);
    if (before && relationConditionKey(before) !== relationConditionKey(rule)) throw rejected(`relation_rule_condition_immutable:${rule.id}`);
  }
  const seen = new Set<string>();
  for (const rule of request.relationRules) {
    const key = relationConditionKey(rule);
    if (seen.has(key)) throw rejected(`relation_rule_condition_duplicate:${reference(rule)}`);
    seen.add(key);
  }
  for (const rule of request.relationRules) if (!isLevel(rule.severity)) throw rejected(`relation_rule_severity_invalid:${reference(rule)}`);
  if (request.relationRules.length > RELATION_RULE_MAX) throw rejected('relation_rule_limit_exceeded');
}

// ---------------------------------------------------------------------------------------------------------------
// 프로토콜 규칙 검사(rule-set.md v3.6 표, protocol-rules.md 4절)

const channelListValid = (channels: unknown): channels is string[] =>
  Array.isArray(channels) && channels.length > 0 && channels.length <= PROTOCOL_PATH_STEPS_MAX && channels.every(sourceValueValid);

function checkProtocolRules(request: RuleSetRequest, current: RuleSetSnapshot, restore: StoredSnapshot | null) {
  const existing = new Map(current.protocolRules.map((rule) => [rule.id, rule]));
  const restorable = new Set(restore?.protocolRules.map((rule) => rule.id));
  const reference = (rule: ProtocolRuleRequest) => rule.id ?? rule.clientRef!;
  const ids = request.protocolRules.flatMap((rule) => (rule.id === null ? [] : [rule.id]));
  for (const id of ids) if (!existing.has(id) && !restorable.has(id)) throw rejected(`protocol_rule_unknown:${id}`);
  ids.forEach((id, index) => { if (ids.indexOf(id) !== index) throw rejected(`protocol_rule_duplicate:${id}`); });
  const refs = request.protocolRules.flatMap((rule) => (rule.clientRef === null ? [] : [rule.clientRef]));
  if (new Set(refs).size !== refs.length) throw rejected('protocol_rule_client_ref_duplicate');
  for (const rule of request.protocolRules) if (!sourceValueValid(rule.interfaceId)) throw rejected(`protocol_rule_interface_invalid:${reference(rule)}`);
  for (const rule of request.protocolRules) {
    const before = rule.id === null ? undefined : existing.get(rule.id);
    if (before && before.interfaceId !== rule.interfaceId) throw rejected(`protocol_rule_interface_immutable:${rule.id}`);
  }
  const interfaces = new Set<string>();
  for (const rule of request.protocolRules) {
    if (interfaces.has(rule.interfaceId)) throw rejected(`protocol_rule_interface_duplicate:${reference(rule)}`);
    interfaces.add(rule.interfaceId);
  }
  for (const rule of request.protocolRules) {
    if (!rule.paths.every((path) => channelListValid(path.channels))) throw rejected(`protocol_path_invalid:${reference(rule)}`);
  }
  for (const rule of request.protocolRules) {
    const keys = rule.paths.map((path) => JSON.stringify(path.channels));
    if (new Set(keys).size !== keys.length) throw rejected(`protocol_path_duplicate:${reference(rule)}`);
  }
  for (const rule of request.protocolRules) {
    if (rule.paths.length === 0 || rule.paths.length > PROTOCOL_PATHS_MAX) throw rejected(`protocol_path_limit_exceeded:${reference(rule)}`);
  }
  for (const rule of request.protocolRules) if (!isLevel(rule.severity)) throw rejected(`protocol_rule_severity_invalid:${reference(rule)}`);
  if (request.protocolRules.length > PROTOCOL_RULE_MAX) throw rejected('protocol_rule_limit_exceeded');
}

// ---------------------------------------------------------------------------------------------------------------
// 변경 요약

const emptySummary = (): RuleSetSummary => ({
  hardEnabled: 0, hardDisabled: 0, hardUpdated: 0, softAdded: 0, softUpdated: 0, softRemoved: 0, softEnabled: 0, softDisabled: 0,
  relationAdded: 0, relationRemoved: 0, relationUpdated: 0, relationEnabled: 0, relationDisabled: 0,
  protocolAdded: 0, protocolRemoved: 0, protocolUpdated: 0, protocolEnabled: 0, protocolDisabled: 0,
});
const camel = (type: string) => type.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase()) as keyof RuleSetSummary;
const DIFF_KEYS = ['name', 'operator', 'threshold', 'severity', 'enabled'] as const;
const byKey = (left: { key: string }, right: { key: string }) => (left.key < right.key ? -1 : left.key > right.key ? 1 : 0);

export type RuleSetPlan = {
  snapshot: RuleSetSnapshot; contentHash: string; changes: RuleSetChange[]; summary: RuleSetSummary; hardDisabledAfterSave: string[];
  warnings: RuleSetWarning[];
};
type PlanInput = {
  request: RuleSetRequest; current: RuleSetSnapshot; restore: StoredSnapshot | null; activeModelCount: number; nextRevision: number;
  /**
   * 새 항목 id 배정. prefix는 `soft:<field>-`·`hard:rel-`·`hard:proto-`(뒤에 소문자 hex 8자리). taken과 겹치지 않아야 한다.
   * 미리보기는 저장하지 않으므로 null을 돌려준다(응답 changes[]의 id도 null).
   */
  assignId: (prefix: string, taken: Set<string>) => string | null;
};

function planSoftRules(request: RuleSetRequest, current: RuleSetSnapshot, restore: StoredSnapshot | null, nextRevision: number, assignId: PlanInput['assignId']) {
  const existing = new Map(current.softRules.map((rule) => [rule.id, rule]));
  const restorable = new Map(restore?.softRules.map((rule) => [rule.id, rule]));
  const taken = new Set([...existing.keys(), ...request.softRules.flatMap((rule) => (rule.id ? [rule.id] : []))]);
  const softRules: SoftRule[] = [];
  const changes: { key: string; change: RuleSetChange }[] = [];
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
      // scope는 불변이라 저장값 그대로(표시용 interfaceId 포함).
      softRules.push({ ...values, id: previous.id, scope: previous.scope ?? null, createdAtRevision: previous.createdAtRevision,
        updatedAtRevision: keys.length ? nextRevision : previous.updatedAtRevision });
      if (keys.length === 1 && keys[0] === 'enabled') changes.push({ key: previous.id, change: { type: rule.enabled ? 'soft_enabled' : 'soft_disabled', id: previous.id, name } });
      else if (keys.length) changes.push({ key: previous.id, change: { type: 'soft_updated', id: previous.id, name, diff } });
      continue;
    }
    const scope = rule.scope as SoftRuleScope;
    if (rule.id) {
      // 되돌리기: 그 사이 삭제된 규칙을 같은 id로 재생성(스냅샷 값 그대로, createdAtRevision 유지).
      const source = restorable.get(rule.id)!;
      softRules.push({ ...values, id: rule.id, scope: source.scope ?? null, createdAtRevision: source.createdAtRevision, updatedAtRevision: nextRevision });
      changes.push({ key: rule.id, change: { type: 'soft_added', id: rule.id, name, restored: true } });
      continue;
    }
    const id = assignId(`soft:${rule.field}-`, taken);
    const key = id ?? `soft:${rule.field}-${rule.clientRef}`;
    taken.add(key);
    softRules.push({ ...values, id: key, scope, createdAtRevision: nextRevision, updatedAtRevision: nextRevision });
    changes.push({ key, change: { type: 'soft_added', id, clientRef: rule.clientRef, name } });
  }
  softRules.sort(byId);
  changes.sort(byKey);
  const kept = new Set(request.softRules.flatMap((rule) => (rule.id ? [rule.id] : [])));
  const removed = current.softRules.filter((rule) => !kept.has(rule.id)).map((rule): RuleSetChange => ({ type: 'soft_removed', id: rule.id, name: rule.name }));
  return { softRules, changes: [...changes.map((item) => item.change), ...removed] };
}

/** 관계 규칙 변경 기록에 쓰는 표시 값(조건). */
const relationLabel = (rule: Pick<RelationRule, 'kind' | 'scope' | 'from' | 'to'>) => ({
  kind: rule.kind, interfaceId: rule.scope?.interfaceId ?? null, from: rule.from.processId, to: rule.to?.processId ?? null,
});

function planRelationRules(request: RuleSetRequest, current: RuleSetSnapshot, restore: StoredSnapshot | null, nextRevision: number, assignId: PlanInput['assignId']) {
  const existing = new Map(current.relationRules.map((rule) => [rule.id, rule]));
  const restorable = new Map(restore?.relationRules.map((rule) => [rule.id, rule]));
  const taken = new Set([...existing.keys(), ...request.relationRules.flatMap((rule) => (rule.id ? [rule.id] : []))]);
  const rules: RelationRule[] = [];
  const changes: { key: string; change: RuleSetChange }[] = [];
  const fresh = new Map<string, { clientRef: string; assigned: boolean }>();
  for (const rule of request.relationRules) {
    const severity = rule.severity as Severity;
    const previous = rule.id ? existing.get(rule.id) : undefined;
    if (previous) {
      // 조건·source는 승인 때 고정(서버 저장값 유지), 편집은 enabled·severity만.
      const changed = previous.severity !== severity || previous.enabled !== rule.enabled;
      rules.push({ ...previous, severity, enabled: rule.enabled, updatedAtRevision: changed ? nextRevision : previous.updatedAtRevision });
      if (previous.enabled !== rule.enabled) {
        changes.push({ key: previous.id, change: { type: rule.enabled ? 'relation_enabled' : 'relation_disabled', id: previous.id, ...relationLabel(previous) } });
      }
      if (previous.severity !== severity) {
        changes.push({ key: `${previous.id}\u0001`, change: { type: 'relation_updated', id: previous.id, ...relationLabel(previous), diff: { severity: [previous.severity, severity] } } });
      }
      continue;
    }
    const values = {
      kind: rule.kind as RelationKind, scope: rule.scope as Record<string, string> | null, from: rule.from as ProcessRef,
      to: rule.to as ProcessRef | null, match: rule.match as RelationMatch | null, severity, enabled: rule.enabled,
    };
    if (rule.id) {
      const source = restorable.get(rule.id)!;
      rules.push({ ...values, scope: source.scope, id: rule.id, source: source.source ?? null, createdAtRevision: source.createdAtRevision, updatedAtRevision: nextRevision });
      changes.push({ key: rule.id, change: { type: 'relation_added', id: rule.id, ...relationLabel(values), restored: true } });
      continue;
    }
    const id = assignId('hard:rel-', taken);
    const key = id ?? `hard:rel-${rule.clientRef}`;
    taken.add(key);
    fresh.set(key, { clientRef: rule.clientRef!, assigned: id !== null });
    rules.push({ ...values, id: key, source: rule.source ?? null, createdAtRevision: nextRevision, updatedAtRevision: nextRevision });
    changes.push({ key, change: { type: 'relation_added', id, clientRef: rule.clientRef, ...relationLabel(values) } });
  }
  rules.sort(byId);
  changes.sort(byKey);
  const kept = new Set(request.relationRules.flatMap((rule) => (rule.id ? [rule.id] : [])));
  const removed = current.relationRules.filter((rule) => !kept.has(rule.id))
    .map((rule): RuleSetChange => ({ type: 'relation_removed', id: rule.id, ...relationLabel(rule) }));
  return { relationRules: rules, changes: [...changes.map((item) => item.change), ...removed], fresh };
}

/** 경로는 pathKey 코드 포인트 순으로 저장한다(rule-set.md v3.2). 기존 경로의 source는 저장값을 유지한다. */
function plannedPaths(paths: ProtocolPathRequest[], before: ProtocolPath[]): ProtocolPath[] {
  const stored = new Map(before.map((path) => [JSON.stringify(path.channels), path]));
  return paths.map((path) => {
    const channels = path.channels as string[];
    const kept = stored.get(JSON.stringify(channels));
    return { channels: [...channels], source: kept ? kept.source ?? null : path.source ?? null };
  }).sort((left, right) => byPathKey(left.channels, right.channels));
}

function planProtocolRules(request: RuleSetRequest, current: RuleSetSnapshot, restore: StoredSnapshot | null, nextRevision: number, assignId: PlanInput['assignId']) {
  const existing = new Map(current.protocolRules.map((rule) => [rule.id, rule]));
  const restorable = new Map(restore?.protocolRules.map((rule) => [rule.id, rule]));
  const taken = new Set([...existing.keys(), ...request.protocolRules.flatMap((rule) => (rule.id ? [rule.id] : []))]);
  const rules: ProtocolRule[] = [];
  const changes: { key: string; change: RuleSetChange }[] = [];
  for (const rule of request.protocolRules) {
    const severity = rule.severity as Severity;
    const previous = rule.id ? existing.get(rule.id) : undefined;
    if (previous) {
      const paths = plannedPaths(rule.paths, previous.paths);
      const beforeKeys = new Set(previous.paths.map((path) => JSON.stringify(path.channels)));
      const afterKeys = new Set(paths.map((path) => JSON.stringify(path.channels)));
      const pathsAdded = paths.filter((path) => !beforeKeys.has(JSON.stringify(path.channels))).map((path) => path.channels);
      const pathsRemoved = [...previous.paths].sort((left, right) => byPathKey(left.channels, right.channels))
        .filter((path) => !afterKeys.has(JSON.stringify(path.channels))).map((path) => path.channels);
      const updated = pathsAdded.length > 0 || pathsRemoved.length > 0 || previous.severity !== severity;
      const changed = updated || previous.enabled !== rule.enabled;
      rules.push({ ...previous, paths, severity, enabled: rule.enabled, updatedAtRevision: changed ? nextRevision : previous.updatedAtRevision });
      if (previous.enabled !== rule.enabled) {
        changes.push({ key: previous.id, change: { type: rule.enabled ? 'protocol_enabled' : 'protocol_disabled', id: previous.id, interfaceId: previous.interfaceId } });
      }
      if (updated) {
        changes.push({ key: `${previous.id}\u0001`, change: { type: 'protocol_updated', id: previous.id, interfaceId: previous.interfaceId,
          diff: { pathsAdded, pathsRemoved, ...(previous.severity !== severity ? { severity: [previous.severity, severity] } : {}) } } });
      }
      continue;
    }
    if (rule.id) {
      const source = restorable.get(rule.id)!;
      rules.push({ id: rule.id, interfaceId: rule.interfaceId, paths: plannedPaths(rule.paths, source.paths), severity, enabled: rule.enabled,
        source: source.source ?? null, createdAtRevision: source.createdAtRevision, updatedAtRevision: nextRevision });
      changes.push({ key: rule.id, change: { type: 'protocol_added', id: rule.id, interfaceId: rule.interfaceId, paths: rule.paths.length, restored: true } });
      continue;
    }
    const id = assignId('hard:proto-', taken);
    const key = id ?? `hard:proto-${rule.clientRef}`;
    taken.add(key);
    rules.push({ id: key, interfaceId: rule.interfaceId, paths: plannedPaths(rule.paths, []), severity, enabled: rule.enabled, source: rule.source ?? null,
      createdAtRevision: nextRevision, updatedAtRevision: nextRevision });
    changes.push({ key, change: { type: 'protocol_added', id, clientRef: rule.clientRef, interfaceId: rule.interfaceId, paths: rule.paths.length } });
  }
  rules.sort(byId);
  changes.sort(byKey);
  const kept = new Set(request.protocolRules.flatMap((rule) => (rule.id ? [rule.id] : [])));
  const removed = current.protocolRules.filter((rule) => !kept.has(rule.id))
    .map((rule): RuleSetChange => ({ type: 'protocol_removed', id: rule.id, interfaceId: rule.interfaceId }));
  return { protocolRules: rules, changes: [...changes.map((item) => item.change), ...removed] };
}

/**
 * 저장은 허용하는 경고(rule-set.md v3.6). 응답 최상위 `warnings[]`.
 * 새 관계 규칙은 `fresh`(저장 키 → clientRef)로 알아본다: 미리보기는 id를 배정하지 않으므로 ruleId null + clientRef.
 */
function warningsOf(snapshot: RuleSetSnapshot, fresh: Map<string, { clientRef: string; assigned: boolean }>): RuleSetWarning[] {
  const warnings: RuleSetWarning[] = [];
  const enabled = snapshot.relationRules.filter((rule) => rule.enabled);
  for (const handoff of enabled.filter((rule) => rule.kind === 'message_handoff')) {
    const scope = canonicalJson(canonicalRelationScope(handoff.kind, handoff.scope));
    const paired = enabled.some((rule) => rule.kind === 'precedes' && rule.from.processId === handoff.from.processId &&
      rule.to?.processId === handoff.to?.processId && [scope, 'null'].includes(canonicalJson(canonicalRelationScope(rule.kind, rule.scope))));
    if (paired) continue;
    const added = fresh.get(handoff.id);
    warnings.push({ code: 'relation_handoff_without_precedes', ruleId: added && !added.assigned ? null : handoff.id,
      message: WARNING_MESSAGES.relation_handoff_without_precedes, ...(added ? { clientRef: added.clientRef } : {}) });
  }
  const unregistered = snapshot.hardRules.find((rule) => rule.id === UNREGISTERED_INTERFACE_RULE);
  if (unregistered?.enabled && !snapshot.protocolRules.some((rule) => rule.enabled)) {
    warnings.push({ code: 'protocol_unregistered_without_entries', ruleId: UNREGISTERED_INTERFACE_RULE, message: WARNING_MESSAGES.protocol_unregistered_without_entries });
  }
  return warnings;
}

/** scope 없는 켜진 방법 수(rule-set.md v3.6 `detection_methods_empty` 정의 확장). 관계·프로토콜 규칙은 세지 않는다. */
export const unscopedMethods = (snapshot: Pick<RuleSetSnapshot, 'hardRules' | 'softRules'>, activeModelCount: number) =>
  snapshot.hardRules.filter((rule) => rule.enabled && !TRANSACTION_LEVEL_HARD_RULES.has(rule.id)).length +
  snapshot.softRules.filter((rule) => rule.enabled && canonicalSoftScope(rule.scope ?? null) === null).length + activeModelCount;

/** rule-set.md 검증 순서(expectedRevision 다음부터, v3.6 표는 v2 표 다음)대로 검사하고 저장할 상태·변경 요약을 만든다. 첫 실패에서 RuleSetError. */
export function planRuleSetChange({ request, current, restore, activeModelCount, nextRevision, assignId }: PlanInput): RuleSetPlan {
  if (request.restoreFromRevision !== null) {
    if (!restore) throw rejected('restore_revision_unknown');
    checkRestore(request, restore);
  }
  checkHardRules(request, restore);
  const { rules: hardRules, deferred } = plannedHardRules(request, restore);
  const before = new Map(current.hardRules.map((rule) => [rule.id, rule]));
  const hardChanges: RuleSetChange[] = [];
  for (const rule of hardRules) {
    const previous = before.get(rule.id)!;
    if (previous.enabled !== rule.enabled) hardChanges.push({ type: rule.enabled ? 'hard_enabled' : 'hard_disabled', id: rule.id, displayName: rule.displayName });
    if (!deferred.includes(rule.id) && !sameSeverity(previous.severity, rule.severity)) {
      hardChanges.push({ type: 'hard_updated', id: rule.id, displayName: rule.displayName, diff: { severity: [previous.severity, rule.severity] } });
    }
  }
  if (hardChanges.some((change) => change.type === 'hard_disabled') && !request.confirmHardRuleDisable) throw rejected('hard_rule_disable_confirmation_required');

  checkSoftRules(request, current, restore);
  if (deferred.length) throw rejected(`hard_rule_severity_invalid:${deferred[0]}`);
  checkSoftScopes(request, current);
  checkRelationRules(request, current, restore);
  checkProtocolRules(request, current, restore);

  const soft = planSoftRules(request, current, restore, nextRevision, assignId);
  const relation = planRelationRules(request, current, restore, nextRevision, assignId);
  const protocol = planProtocolRules(request, current, restore, nextRevision, assignId);
  const snapshot: RuleSetSnapshot = { hardRules, softRules: soft.softRules, relationRules: relation.relationRules, protocolRules: protocol.protocolRules };
  if (unscopedMethods(snapshot, activeModelCount) === 0) throw rejected('detection_methods_empty');
  // 무변경: v3 정규 객체 + 소프트룰 이름. 현재 행이 v1·v2여도 같은 해석(빈 관계·프로토콜, scope null, 없는 내장 규칙 꺼짐)으로 비교한다.
  const hash = contentHash(snapshot);
  const existing = new Map(current.softRules.map((rule) => [rule.id, rule]));
  const sameNames = soft.softRules.every((rule) => existing.get(rule.id)?.name === rule.name);
  if (hash === contentHash(current) && sameNames) throw new RuleSetError('rule_set_unchanged', 409);
  if (Buffer.byteLength(JSON.stringify(snapshot), 'utf8') > SNAPSHOT_BYTES_MAX) throw rejected('rule_set_snapshot_too_large');
  const changes = [...hardChanges, ...soft.changes, ...relation.changes, ...protocol.changes];
  const summary = emptySummary();
  for (const change of changes) summary[camel(change.type)] += 1;
  return {
    snapshot, contentHash: hash, changes, summary, hardDisabledAfterSave: hardRules.filter((rule) => !rule.enabled).map((rule) => rule.id),
    warnings: warningsOf(snapshot, relation.fresh),
  };
}
