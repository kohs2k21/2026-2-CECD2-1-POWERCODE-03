import type { Severity } from '../ruleset/hardRuleCatalog.js';

/**
 * 응답 코드 사전 API 공통 규칙(contracts/response-codes-ui.md 2·3절). DB CHECK(migration 025)와 같은 상한.
 * 코드 형식은 룰 세트 byCode 키와 같다(rule-set.md v3.6: 인쇄 가능한 ASCII 1~64자).
 */
export const RESPONSE_CODE_PATTERN = /^[\x21-\x7e]{1,64}$/;
export const RESPONSE_CODE_LIMITS = { enumName: 128, codeType: 64, httpStatus: 64, messageKey: 256, message: 500 } as const;
/** 위험도를 코드별로 저장하는 내장 하드룰(rule-set.md v3.5). */
export const TRANSACTION_FAILED_RULE = 'hard:transaction-failed';

/** 제어 문자(줄바꿈 포함)·짝 없는 서로게이트. 메시지는 한 줄 글자만 받는다. */
export const hasControlCharacter = (value: string) => /\p{Cc}|\p{Cs}/u.test(value);

export class ResponseCodeError extends Error {
  constructor(readonly code: string, readonly status = 400, readonly detail: Record<string, unknown> = {}) { super(code); }
}

export type CodeSeverity = { level: Severity; source: 'code' | 'default' };
export type FailedRuleSeverity = { byCode: Record<string, Severity>; default: Severity };

/** 그 코드의 위험도: byCode에 있으면 그 단계(source code), 없으면 기본 단계(source default). */
export const severityFor = (code: string, severity: FailedRuleSeverity): CodeSeverity =>
  Object.hasOwn(severity.byCode, code) ? { level: severity.byCode[code], source: 'code' } : { level: severity.default, source: 'default' };

const LEVELS = new Set(['critical', 'warning', 'info']);
/** 저장된 변경 기록의 위험도 값이 `{byCode, default}` 모양이면 그대로, 아니면 null(그 기록은 비교하지 않는다). */
export function failedSeverityOf(value: unknown): FailedRuleSeverity | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const { byCode, default: level } = value as { byCode?: unknown; default?: unknown };
  if (typeof byCode !== 'object' || byCode === null || Array.isArray(byCode) || typeof level !== 'string' || !LEVELS.has(level)) return null;
  if (!Object.values(byCode).every((item) => typeof item === 'string' && LEVELS.has(item))) return null;
  return { byCode: byCode as Record<string, Severity>, default: level as Severity };
}

export const LIST_LIMITS = { default: 50, max: 200 } as const;
const QUERY_MAX = 100;

const single = (query: Record<string, unknown>, key: string, code: string) => {
  const value = query[key];
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new ResponseCodeError(code);
  return value;
};

export type ListQuery = { q: string | null; type: string | null; limit: number; after: string | null };
/** 목록 조회 조건: q(코드·코드명·메시지 포함 검색, 대소문자 무시, 1~100자), type(유형 정확히), limit(1~200), cursor(앞 쪽 마지막 코드). */
export function parseListQuery(query: Record<string, unknown>): ListQuery {
  if (Object.keys(query).some((key) => !['q', 'type', 'limit', 'cursor'].includes(key))) throw new ResponseCodeError('unknown_response_code_filter');
  const q = single(query, 'q', 'invalid_query')?.trim() ?? '';
  if ([...q].length > QUERY_MAX || hasControlCharacter(q)) throw new ResponseCodeError('invalid_query');
  const type = single(query, 'type', 'invalid_type');
  if (type !== undefined && (type === '' || [...type].length > RESPONSE_CODE_LIMITS.codeType || hasControlCharacter(type))) throw new ResponseCodeError('invalid_type');
  const limit = single(query, 'limit', 'invalid_limit');
  if (limit !== undefined && (!/^[1-9]\d{0,2}$/.test(limit) || Number(limit) > LIST_LIMITS.max)) throw new ResponseCodeError('invalid_limit');
  const cursor = single(query, 'cursor', 'invalid_cursor');
  let after: string | null = null;
  if (cursor !== undefined) {
    try {
      if (!/^[A-Za-z0-9_-]{1,256}$/.test(cursor)) throw new Error();
      const parsed = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8')) as { code?: unknown };
      if (Object.keys(parsed).join() !== 'code' || typeof parsed.code !== 'string' || !RESPONSE_CODE_PATTERN.test(parsed.code)) throw new Error();
      after = parsed.code;
    } catch { throw new ResponseCodeError('invalid_cursor'); }
  }
  return { q: q === '' ? null : q.toLowerCase(), type: type ?? null, limit: limit === undefined ? LIST_LIMITS.default : Number(limit), after };
}
export const encodeCursor = (code: string) => Buffer.from(JSON.stringify({ code })).toString('base64url');

/** 경로의 코드(express가 퍼센트 해독한 값). */
export function parseCodeParam(value: unknown): string {
  if (typeof value !== 'string' || !RESPONSE_CODE_PATTERN.test(value)) throw new ResponseCodeError('invalid_response_code');
  return value;
}

export type MessageEdit = { message: string; expectedRevision: number | null };
/** 메시지 편집 본문 {message, expectedRevision}: message 앞뒤 공백 제거 1~500자·제어 문자 없음, expectedRevision = 본 행 수정 번호(사전에 없는 코드는 null). */
export function parseMessageEdit(body: unknown): MessageEdit {
  const invalid = new ResponseCodeError('invalid_response_code_edit');
  if (typeof body !== 'object' || body === null || Array.isArray(body) || Object.keys(body).sort().join() !== 'expectedRevision,message') throw invalid;
  const { message, expectedRevision } = body as { message: unknown; expectedRevision: unknown };
  if (typeof message !== 'string') throw invalid;
  const trimmed = message.trim();
  if (!trimmed || [...trimmed].length > RESPONSE_CODE_LIMITS.message || hasControlCharacter(trimmed)) throw new ResponseCodeError('response_code_message_invalid');
  if (expectedRevision !== null && (!Number.isSafeInteger(expectedRevision) || (expectedRevision as number) < 1)) throw invalid;
  return { message: trimmed, expectedRevision: expectedRevision as number | null };
}
