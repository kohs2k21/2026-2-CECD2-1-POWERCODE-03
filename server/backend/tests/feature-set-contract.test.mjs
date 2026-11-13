// 입력 변수 세트 등록 규칙 단위 시험: Trainer 시험 벡터(contracts/feature-set-hash-vectors.json) 전 사례·거절 요청을 문자열 그대로 대조한다.
// 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { DEFINITION_KIND, FEATURE_FIELDS, FIELD_LIST, FeatureFieldError, SCHEMA_VERSION, TRANSFORM_VERSION,
  canonicalJson, definitionsForFields, expressionHash, featureSetContentHash, versionColumns } from '../dist/routes/featureSetContract.js';
import { featureSetCapability } from '../dist/routes/trainerContract.js';

const contract = (name) => readFile(new URL(`../../contracts/${name}`, import.meta.url), 'utf8').then(JSON.parse);
const vectors = await contract('feature-set-hash-vectors.json');
const catalog = await contract('method-catalog.json');
const codeOf = (fields) => { try { definitionsForFields(fields); return null; } catch (error) { assert.ok(error instanceof FeatureFieldError); return error.code; } };

test('vector document matches the field list this API reads', () => {
  assert.equal(vectors.fieldsVersion, FEATURE_FIELDS.version);
  assert.equal(vectors.definitionKind, DEFINITION_KIND);
  assert.equal(vectors.cases.length, 4);
});

for (const vector of vectors.cases) {
  test(`vector ${vector.id}: definitions, canonical JSON, set hash, member hashes and columns`, () => {
    const definitions = definitionsForFields(vector.fields);
    assert.deepEqual(definitions, vector.definitions);
    assert.equal(canonicalJson(definitions), vector.canonicalDefinitionsJson);
    assert.equal(featureSetContentHash(definitions), vector.contentHash);
    assert.deepEqual({ schema_version: SCHEMA_VERSION, transform_version: TRANSFORM_VERSION, output_schema: definitions.map(x => x.name) }, vector.featureSet);
    assert.equal(definitions.length, vector.members.length);
    definitions.forEach(({ name, expression }, position) => {
      const member = vector.members[position];
      assert.equal(canonicalJson(expression), member.canonicalExpressionJson);
      assert.equal(expressionHash(expression), member.expressionHash);
      assert.deepEqual({
        name, canonicalExpressionJson: canonicalJson(expression), expressionHash: expressionHash(expression),
        definition_key: name, display_name: name, definition_kind: DEFINITION_KIND, ...versionColumns(expression),
        model_input: true, output_position: position, output_name: name, transform_settings: {},
      }, member);
    });
    // 등록한 세트는 train 접수 검사(featureSetCapability)를 통과한다.
    assert.deepEqual(featureSetCapability(definitions.map(({ name, expression }, position) => ({ name, position, expression, requiresFit: versionColumns(expression).requires_fit }))),
      { supported: true, reason: null });
  });
}

test('all six fields give the agreed content hash', () => {
  assert.equal(featureSetContentHash(definitionsForFields(['total_count', 'success_count', 'error_count', 'retry_count', 'elapsed_ms', 'error_ratio'])),
    '4a34bbfe81323afc89b6df344a393ebf9a115fdf1b1acfe2221244446fcc2a69');
});

test('vector rejections and check order', () => {
  for (const { fields, error } of vectors.invalidRequests) assert.equal(codeOf(fields), error, JSON.stringify(fields));
  assert.equal(codeOf(undefined), 'invalid_feature_count');
  assert.equal(codeOf('error_count'), 'invalid_feature_count');
  assert.equal(codeOf({ 0: 'error_count', length: 1 }), 'invalid_feature_count');
  assert.equal(codeOf(Array(33).fill('error_count')), 'invalid_feature_count');
  assert.equal(codeOf(Array(32).fill('error_count')), 'duplicate_feature_field');
  assert.equal(codeOf(['x', 'x']), 'unknown_feature_field');
  assert.equal(codeOf([1]), 'unknown_feature_field');
  assert.equal(codeOf([null]), 'unknown_feature_field');
  assert.equal(codeOf(['error_count', 'ERROR_COUNT']), 'unknown_feature_field');
  assert.equal(codeOf(['__proto__']), 'unknown_feature_field');
});

test('request order does not change definitions; returned expressions are copies', () => {
  const a = definitionsForFields(['error_ratio', 'total_count']);
  assert.deepEqual(a, definitionsForFields(['total_count', 'error_ratio']));
  a[1].expression.ast.op = 'changed';
  assert.equal(definitionsForFields(['error_ratio'])[0].expression.ast.op, 'divide');
});

test('canonical JSON follows Python json.dumps(sort_keys, compact, ensure_ascii)', () => {
  assert.equal(canonicalJson({ b: [1, { d: null, c: true }], a: 'x' }), '{"a":"x","b":[1,{"c":true,"d":null}]}');
  assert.equal(canonicalJson({ k: '처리\u007f"\n' }), '{"k":"\\ucc98\\ub9ac\\u007f\\"\\n"}');
  assert.throws(() => canonicalJson({ v: 0.1 }), /canonical_json_unsupported_number/);
});

test('field list shows the method catalog display names and units, without expressions', () => {
  const soft = new Map(catalog.softRuleFields.map(field => [field.fieldId, field]));
  assert.deepEqual(FIELD_LIST.fields.map(field => field.key), ['total_count', 'success_count', 'error_count', 'retry_count', 'elapsed_ms', 'error_ratio']);
  for (const field of FIELD_LIST.fields) {
    assert.equal(field.displayName, soft.get(field.key).displayName, field.key);
    assert.equal(field.unit, soft.get(field.key).unit, field.key);
    assert.equal('expression' in field, false);
  }
  assert.match(FIELD_LIST.fields.find(field => field.key === 'elapsed_ms').note, /END_TIME/);
  assert.equal(FIELD_LIST.fields.find(field => field.key === 'total_count').note, null);
});
