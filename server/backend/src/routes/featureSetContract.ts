import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import { validateFeatureExpression } from './trainerFeatureValidation.js';

/**
 * 입력 변수 세트 등록(`operator_defined`) 규칙. 정본은 Trainer가 정한 trainer-service.md "입력 변수 세트 등록 규칙"과
 * 생성물 server/contracts/ml-feature-fields.json·feature-set-hash-vectors.json이고, 이 모듈은 그 규칙을 그대로 옮긴다.
 * 필드 목록 파일은 methodCatalog.ts와 같이 개발(src)·빌드(dist)에서 세 단계 위이고, 배포 이미지는 backend/Dockerfile이 /contracts에 넣는다.
 * 파일이 없거나 형식이 다르면 시작에서 실패한다(값을 코드에서 대신 채우지 않는다).
 */
export type FeatureField = { key: string; displayName: string; unit: string; expression: Record<string, unknown>; note?: string };
export type FeatureDefinition = { name: string; expression: Record<string, unknown> };
type FieldsDocument = { version: string; maxFields: number; definitionKind: string; fields: FeatureField[] };

export const FIELDS_VERSION = 'ml-feature-fields-v1';
export const DEFINITION_KIND = 'operator_defined';
export const SCHEMA_VERSION = 'numeric-process-v2';
export const TRANSFORM_VERSION = 'safe-dsl-v2';
export const VALUE_POLICY = { missing: 'null', zeroDivision: 'null' } as const;
export const AVAILABILITY_SPEC = { layer: 'P', asOf: 'observation' } as const;
/** Trainer `catalog.FEATURE_SET_LOCK`과 같은 키. 같은 거래 안에서 먼저 잡는다. */
export const FEATURE_SET_LOCK = [81732, 1] as const;

const NAME = /^[a-z][a-z0-9_]{1,63}$/;

function readFields(): FieldsDocument {
  const value = JSON.parse(readFileSync(new URL('../../../contracts/ml-feature-fields.json', import.meta.url), 'utf8'));
  if (value?.version !== FIELDS_VERSION || value.definitionKind !== DEFINITION_KIND || value.featureName !== 'key' ||
      !Number.isSafeInteger(value.maxFields) || !Array.isArray(value.fields) || !value.fields.length) {
    throw new Error('ml_feature_fields_invalid');
  }
  const keys = new Set<string>();
  for (const field of value.fields) {
    if (typeof field?.key !== 'string' || !NAME.test(field.key) || keys.has(field.key) ||
        typeof field.displayName !== 'string' || typeof field.unit !== 'string' ||
        !field.expression || typeof field.expression !== 'object' || Array.isArray(field.expression)) {
      throw new Error('ml_feature_fields_invalid');
    }
    // 등록한 세트가 train 접수 검사를 통과하지 못하면 고를 수 없는 필드다. 시작에서 막는다.
    validateFeatureExpression(field.expression);
    keys.add(field.key);
  }
  return value;
}

export const FEATURE_FIELDS = readFields();
const ORDER = FEATURE_FIELDS.fields.map(field => field.key);
const EXPRESSIONS = new Map(FEATURE_FIELDS.fields.map(field => [field.key, field.expression]));

export class FeatureFieldError extends Error {
  constructor(readonly code: 'invalid_feature_count' | 'unknown_feature_field' | 'duplicate_feature_field') { super(code); }
}

/**
 * 고른 필드 → 정의 배열. 검사 순서는 ① 목록이 아니거나 0·33개 이상 → ② 문자열이 아니거나 목록 밖 → ③ 중복(Trainer `definitions_for_fields`).
 * 요청 순서와 무관하게 목록 순서로 정렬한다. 식은 복사본이다.
 */
export function definitionsForFields(fields: unknown): FeatureDefinition[] {
  if (!Array.isArray(fields) || fields.length < 1 || fields.length > FEATURE_FIELDS.maxFields) throw new FeatureFieldError('invalid_feature_count');
  if (fields.some(item => typeof item !== 'string' || !EXPRESSIONS.has(item))) throw new FeatureFieldError('unknown_feature_field');
  if (new Set(fields).size !== fields.length) throw new FeatureFieldError('duplicate_feature_field');
  const chosen = new Set(fields as string[]);
  return ORDER.filter(key => chosen.has(key)).map(key => ({ name: key, expression: structuredClone(EXPRESSIONS.get(key)!) }));
}

/** Python `json.dumps(value, sort_keys=True, separators=(",", ":"))`와 같은 문자열. 키는 코드 단위 순, 0x7F 이상은 `\uXXXX`(소문자 hex). */
export function canonicalJson(value: unknown): string {
  const ascii = (text: string) => JSON.stringify(text).replace(/[\u007f-￿]/g, ch => `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`);
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') return ascii(value);
  if (typeof value === 'number') {
    // 목록의 식에는 숫자가 없다. 정수 밖 표기는 Python과 다를 수 있어 받지 않는다.
    if (!Number.isSafeInteger(value)) throw new Error('canonical_json_unsupported_number');
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') {
    const entries = Object.keys(value as object).sort().map(key => `${ascii(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`);
    return `{${entries.join(',')}}`;
  }
  throw new Error('canonical_json_unsupported_value');
}

const sha256 = (text: string) => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
/** 세트 hash = sha256(`operator_defined:` + 정규 JSON(정의 배열)). */
export const featureSetContentHash = (definitions: FeatureDefinition[]) => sha256(`${DEFINITION_KIND}:${canonicalJson(definitions)}`);
/** 변수 버전 hash = sha256(정규 JSON(식)), 종류 접두 없음. */
export const expressionHash = (expression: Record<string, unknown>) => sha256(canonicalJson(expression));

/** 변수 버전 행 열 값(trainer-service.md 고정 열 값). `requires_fit`은 Trainer `feature_fit.requires_fit`과 같다(목록의 식은 모두 false). */
export const versionColumns = (expression: Record<string, unknown>) => ({
  output_type: typeof expression.outputType === 'string' ? expression.outputType : 'decimal',
  output_unit: typeof expression.unit === 'string' ? expression.unit : null,
  value_policy: VALUE_POLICY,
  availability_spec: AVAILABILITY_SPEC,
  requires_fit: ['group_median', 'group_centered'].includes(String(expression.op)),
});

/** 화면 표시용 필드 목록(`GET /api/jobs/feature-sets/fields`). 식은 싣지 않는다. */
export const FIELD_LIST = {
  version: FEATURE_FIELDS.version,
  maxFields: FEATURE_FIELDS.maxFields,
  fields: FEATURE_FIELDS.fields.map(({ key, displayName, unit, note }) => ({ key, displayName, unit, note: note ?? null })),
};
