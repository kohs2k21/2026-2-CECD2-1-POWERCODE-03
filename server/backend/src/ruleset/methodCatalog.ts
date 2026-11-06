import { readFileSync } from 'node:fs';

/**
 * 방법 카탈로그. 정본은 레지스트리 생성물 server/contracts/method-catalog.json(method-catalog.md)이고 이 모듈은 그 파일을 읽기만 한다.
 * 파일은 개발(src)·빌드(dist) 모두 이 모듈에서 세 단계 위이며, 배포 이미지는 backend/Dockerfile이 같은 상대 위치(/contracts)에 넣는다.
 * 파일이 없거나 형식이 다르면 시작에서 실패한다(값을 코드에서 대신 채우지 않는다).
 */
export type SoftRuleOperator = 'gte' | 'gt' | 'lte' | 'lt';
type ColumnNode = { op: 'column'; table: 'EC_PROCESS_MONITORING'; column: string };
type DerivedAst = { op: 'datetime_diff'; unit: 'ms'; args: [ColumnNode, ColumnNode] } | { op: 'divide'; args: [ColumnNode, ColumnNode] };
export type SoftRuleField = {
  fieldId: string; displayName: string; description?: string; valueType: 'integer' | 'number'; unit: string;
  source: { kind: 'column'; column: string; ruleDslField: string } | { kind: 'derived'; ast: DerivedAst };
  operators: readonly SoftRuleOperator[]; displayPrecision: number; displayPrecisionMode?: 'fixed' | 'significant';
  status: 'supported'; sourceMissingPolicy?: Record<string, 'not_applicable' | 'unavailable'>;
  sourceRef: { path: string; lines: string; commit: string }; notes?: string;
};
type ParameterRule = {
  type?: 'integer' | 'number' | 'string' | 'boolean'; const?: unknown; enum?: unknown[];
  minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number;
};
export type MlMethod = {
  methodId: string; kind: 'ml'; displayName: string; status: string; methodVersion: string;
  executable: { process: boolean; window: boolean }; executableReason?: { process?: string; window?: string };
  parameterSchema: { additionalProperties?: boolean; required?: string[]; properties: Record<string, ParameterRule> };
  defaults: Record<string, unknown> | null; inputRequirements: Record<string, unknown>;
};
type MethodCatalog = {
  catalogVersion: string; generatedFrom: { gitRef: string }; categoryDisplayNames: Record<'hard' | 'soft' | 'ml', string>;
  hardRules: Record<string, unknown>;
  softRule: { displayName: string; instanceIdPattern: string; operators: readonly SoftRuleOperator[]; severities: readonly string[]; maxRules: number; thresholdSerialization: string };
  softRuleFields: readonly SoftRuleField[]; methods: readonly MlMethod[];
};

function readCatalog(): MethodCatalog {
  const value = JSON.parse(readFileSync(new URL('../../../contracts/method-catalog.json', import.meta.url), 'utf8'));
  if (typeof value?.catalogVersion !== 'string' || !Array.isArray(value.softRuleFields) || !Array.isArray(value.methods) || !value.softRule) {
    throw new Error('method_catalog_invalid');
  }
  return value;
}

/** `GET /api/jobs/capabilities`의 `methodCatalog`(method-catalog.md 조회 API). */
export const METHOD_CATALOG = readCatalog();
export const CATALOG_VERSION = METHOD_CATALOG.catalogVersion;
export const SOFT_RULE = METHOD_CATALOG.softRule;
export const SOFT_RULE_FIELDS = METHOD_CATALOG.softRuleFields;
export const ML_METHODS = METHOD_CATALOG.methods;
export const softRuleField = (fieldId: string) => SOFT_RULE_FIELDS.find((field) => field.fieldId === fieldId);

/** `ml:<model_key>`의 `<model_key>`. */
export const modelKeyOf = (method: Pick<MlMethod, 'methodId'>) => method.methodId.slice(method.methodId.indexOf(':') + 1);
export const mlMethod = (modelKey: unknown) =>
  typeof modelKey === 'string' ? ML_METHODS.find((method) => modelKeyOf(method) === modelKey) : undefined;

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
/** 요청의 `parameters`를 기본값 위에 얹어 `parameterSchema` 검사. worker `methods.registry.validate_parameters`와 같은 부분집합이다. */
export function parametersValid(method: MlMethod, parameters: unknown): boolean {
  if (parameters === undefined || parameters === null) return true;
  if (typeof parameters !== 'object' || Array.isArray(parameters)) return false;
  const schema = method.parameterSchema;
  const merged: Record<string, unknown> = { ...(method.defaults ?? {}), ...(parameters as Record<string, unknown>) };
  if (schema.additionalProperties === false && Object.keys(merged).some((key) => !Object.hasOwn(schema.properties, key))) return false;
  if ((schema.required ?? []).some((key) => !Object.hasOwn(merged, key))) return false;
  return Object.entries(schema.properties).every(([key, rule]) => {
    if (!Object.hasOwn(merged, key)) return true;
    const value = merged[key];
    if ('const' in rule && value !== rule.const) return false;
    if (rule.enum && !rule.enum.includes(value)) return false;
    if (rule.type === 'integer' && !Number.isSafeInteger(value)) return false;
    if (rule.type === 'number' && !finite(value)) return false;
    if (rule.type === 'string' && typeof value !== 'string') return false;
    if (rule.type === 'boolean' && typeof value !== 'boolean') return false;
    if (rule.type !== 'integer' && rule.type !== 'number') return true;
    const number = value as number;
    return !(rule.minimum !== undefined && number < rule.minimum) && !(rule.maximum !== undefined && number > rule.maximum) &&
      !(rule.exclusiveMinimum !== undefined && number <= rule.exclusiveMinimum) && !(rule.exclusiveMaximum !== undefined && number >= rule.exclusiveMaximum);
  });
}
