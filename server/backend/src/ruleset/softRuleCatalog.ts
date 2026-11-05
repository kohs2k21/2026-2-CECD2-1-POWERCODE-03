/**
 * 이상 징후 규칙(소프트룰) 등록 필드와 인스턴스 규칙. 정본은 method-catalog.md `softRuleFields[]`·`softRule`이다.
 * 계약의 생성 JSON(server/contracts/method-catalog.json)이 생기면 이 파일만 교체한다(값이 문서 예시와 같은지는
 * tests/rule-set-contract.test.mjs가 확인). ML `methods[]`는 이 모듈 범위가 아니다.
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

const OPERATORS: readonly SoftRuleOperator[] = ['gte', 'gt', 'lte', 'lt'];
const column = (name: string): ColumnNode => ({ op: 'column', table: 'EC_PROCESS_MONITORING', column: name });
const countField = (fieldId: string, displayName: string, sourceColumn: string): SoftRuleField => ({
  fieldId, displayName, valueType: 'integer', unit: 'count',
  source: { kind: 'column', column: sourceColumn, ruleDslField: `P.${fieldId}` },
  operators: OPERATORS, displayPrecision: 0, status: 'supported',
  sourceRef: { path: 'server/worker/derived_expression.py', lines: '9-11', commit: '7f24e03' },
});

export const SOFT_RULE_FIELDS: readonly SoftRuleField[] = [
  countField('total_count', '총 건수', 'TOTAL_COUNT'),
  countField('success_count', '성공 건수', 'SUCCESS_COUNT'),
  countField('error_count', '오류 건수', 'ERROR_COUNT'),
  countField('retry_count', '재시도 수', 'RETRY_COUNT'),
  {
    fieldId: 'elapsed_ms', displayName: '처리 시간', description: 'END_TIME - START_TIME (ms)', valueType: 'number', unit: 'ms',
    source: { kind: 'derived', ast: { op: 'datetime_diff', unit: 'ms', args: [column('END_TIME'), column('START_TIME')] } },
    operators: OPERATORS, displayPrecision: 0, displayPrecisionMode: 'fixed', status: 'supported',
    sourceMissingPolicy: { END_TIME: 'not_applicable', START_TIME: 'unavailable' },
    sourceRef: { path: 'server/worker/derived_expression.py', lines: '50-53, 89-94', commit: '7f24e03' },
    notes: 'END_TIME 누락은 값 null → 룰 평가 안 함(이상 아님, 11/02 9.4). START_TIME 누락·시각 파싱 실패 시 unavailable. 음수(START_TIME > END_TIME)는 하드룰 영역',
  },
  {
    fieldId: 'error_ratio', displayName: '오류 비율', description: 'ERROR_COUNT / TOTAL_COUNT', valueType: 'number', unit: 'ratio',
    source: { kind: 'derived', ast: { op: 'divide', args: [column('ERROR_COUNT'), column('TOTAL_COUNT')] } },
    operators: OPERATORS, displayPrecision: 4, displayPrecisionMode: 'significant', status: 'supported',
    sourceRef: { path: 'server/worker/derived_expression.py', lines: '101-102', commit: '7f24e03' },
    notes: 'TOTAL_COUNT = 0 이면 unavailable. p95가 1e-4 미만일 수 있어 고정소수 직렬화 필수, 표시는 유효숫자 4',
  },
];

export const CATALOG_VERSION = 'method-catalog-v2';
export const SOFT_RULE = {
  displayName: '이상 징후 규칙', instanceIdPattern: '^soft:[a-z][a-z0-9_]{1,53}-[0-9a-f]{8}$', operators: OPERATORS,
  severities: ['info', 'warning', 'critical'], maxRules: 32, thresholdSerialization: 'fixed-decimal-string',
} as const;
export const CATEGORY_DISPLAY_NAMES = { hard: '이상 규칙', soft: '이상 징후 규칙', ml: '머신러닝 모델' } as const;
export const HARD_RULES_REFERENCE = {
  definedBy: 'detection-decision.md', displayName: '이상 규칙', contractVersion: 'detection-decision-v1',
  idPattern: '^hard:[a-z][a-z0-9_-]{1,63}$',
} as const;

/** `GET /api/jobs/capabilities`의 `methodCatalog`(method-catalog.md 조회 API). ML `methods[]`는 레지스트리가 생길 때 붙인다. */
export const METHOD_CATALOG = {
  catalogVersion: CATALOG_VERSION, generatedFrom: { gitRef: '7f24e03' }, categoryDisplayNames: CATEGORY_DISPLAY_NAMES,
  hardRules: HARD_RULES_REFERENCE, softRule: SOFT_RULE, softRuleFields: SOFT_RULE_FIELDS,
};
export const softRuleField = (fieldId: string) => SOFT_RULE_FIELDS.find((field) => field.fieldId === fieldId);
