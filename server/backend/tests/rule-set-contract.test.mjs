// 룰 세트 순수 규칙 단위 시험: 계약 hash 기준값, threshold 고정소수 변환, 내장 상수와 계약 문서·예시의 값 일치, 검증 순서.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  RuleSetError, canonicalSha256, contentHash, decimalString, parseRuleSetRequest, planRuleSetChange, projectHardRules,
  significantDigits, thresholdFixedDecimal,
} from '../dist/ruleset/ruleSetContract.js';
import { HARD_RULES } from '../dist/ruleset/hardRuleCatalog.js';
import { METHOD_CATALOG, SOFT_RULE, SOFT_RULE_FIELDS } from '../dist/ruleset/methodCatalog.js';
import { displayThreshold, parseThresholdStatsRequest } from '../dist/ruleset/thresholdStats.js';
import { TRAINER_CAPABILITIES } from '../dist/routes/trainerContract.js';

const contracts = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../contracts');
const contract = (name) => readFile(path.join(contracts, name), 'utf8');
const REFERENCE_HASH = '5b15237724b48fcc4f18335a374ad7d5d8d7bcbaa7b5f851120f4d17b23dd8aa';
const V2_ALL_FOUR_HASH = 'de6a2ad8d0dcee189f36852a279263d3408f826632b95b4f14a97406b801bed0';
const V2_DEFAULT_HASH = 'b6a9c84b21e7deb8df0d22bb6a11a5cd5fc80779eb4797f195505842bf605f3d';
const V2_REFERENCE_HASH = '4cf61820400fb0de6eb41dca009cd1d20d8f29eccfcf609db8e4c6dd8098fe3d';
const STATUS_DEFAULT = { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_Z: 'warning', PROCESS_STATUS_H: 'info', PROCESS_STATUS_N: 'info' } };
const R0_HASH = '80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89';

test('contract v1 reference object hashes to the pinned value', () => {
  const canonical = {
    hardRules: [{ id: 'hard:negative-duration', enabled: false }, { id: 'hard:process-status', enabled: true }],
    softRules: [{ id: 'soft:elapsed_ms-3f9a2c1d', field: 'elapsed_ms', operator: 'gt', threshold: '8000', severity: 'warning', enabled: true }],
  };
  assert.equal(canonicalSha256(canonical), REFERENCE_HASH);
  assert.equal(contentHash({
    hardRules: [{ id: 'hard:process-status', enabled: true }, { id: 'hard:negative-duration', enabled: false }],
    softRules: [{ id: 'soft:elapsed_ms-3f9a2c1d', name: '이름은 hash 입력이 아님', field: 'elapsed_ms', operator: 'gt', threshold: 8000, unit: 'ms', severity: 'warning', enabled: true, createdAtRevision: 1, updatedAtRevision: 2 }],
  }, 1), REFERENCE_HASH, 'number threshold, name and audit fields do not change the hash');
});
test('contract v2 reference objects hash to the pinned values', () => {
  const hardRules = (negativeOn) => [{ id: 'hard:process-status', enabled: true, severity: STATUS_DEFAULT }, { id: 'hard:negative-duration', enabled: negativeOn, severity: 'critical' }];
  assert.equal(contentHash({ hardRules: hardRules(true), softRules: [] }), V2_DEFAULT_HASH, '(a\u2032)');
  assert.equal(contentHash({
    hardRules: hardRules(false),
    softRules: [{ id: 'soft:elapsed_ms-3f9a2c1d', name: '이름은 hash 입력이 아님', field: 'elapsed_ms', operator: 'gt', threshold: 8000, unit: 'ms', severity: 'warning', enabled: true, createdAtRevision: 1, updatedAtRevision: 2 }],
  }), V2_REFERENCE_HASH, '(b)');
  assert.equal(contentHash({ hardRules: projectHardRules(hard()), softRules: [] }), V2_ALL_FOUR_HASH, '(a) all four built-in rules on: the content of the DB 016 system row r1');
  assert.equal(contentHash({ hardRules: projectHardRules(hard()).slice(0, 2), softRules: [] }), V2_DEFAULT_HASH, 'built-in defaults of the first two rules are the (a\u2032) input');
});
test('r0 keeps the fixed v1 hash the migration inserted', () => {
  assert.equal(contentHash({ hardRules: hard().slice(0, 2).map(({ id, enabled }) => ({ id, enabled })), softRules: [] }, 1), R0_HASH);
});
test('hash key order follows sort_keys and array order follows id', async () => {
  const left = { b: 1, a: { d: [1, { z: 1, y: 2 }], c: 'ㄱ' } };
  const right = { a: { c: 'ㄱ', d: [1, { y: 2, z: 1 }] }, b: 1 };
  assert.equal(canonicalSha256(left), canonicalSha256(right));
  const rule = (id) => ({ id, field: 'retry_count', operator: 'gte', threshold: 3, severity: 'info', enabled: true });
  assert.equal(contentHash({ hardRules: [], softRules: [rule('soft:b'), rule('soft:a')] }), contentHash({ hardRules: [], softRules: [rule('soft:a'), rule('soft:b')] }));
});

test('threshold fixed-decimal conversion matches the contract rule', () => {
  const cases = [
    [8000, '8000'], [8000.0, '8000'], [0.01, '0.01'], [0.0100, '0.01'], [0.00003125, '0.00003125'], [1e-7, '0.0000001'], [1.5e-7, '0.00000015'],
    [1e21, '1000000000000000000000'], [1.5e21, '1500000000000000000000'], [-2.5, '-2.5'], [-0.001, '-0.001'], [0, '0'], [-0, '0'], [7210.5, '7210.5'],
  ];
  for (const [value, text] of cases) assert.equal(thresholdFixedDecimal(value, 'number'), text, String(value));
  for (const value of [3, 0, -4, 1e21]) assert.match(thresholdFixedDecimal(value, 'integer'), /^-?\d+$/);
});
test('threshold conversion rejects what the DSL or precision rule forbids', () => {
  assert.equal(thresholdFixedDecimal(0.1 + 0.2, 'number'), null, 'more than 15 significant digits');
  assert.equal(thresholdFixedDecimal(1234567890123456, 'number'), null, '16 significant digits');
  assert.equal(thresholdFixedDecimal(123456789012345, 'number'), '123456789012345', 'exactly 15 digits');
  assert.equal(thresholdFixedDecimal(1.5, 'integer'), null);
  assert.equal(thresholdFixedDecimal(Infinity, 'number'), null);
  assert.equal(thresholdFixedDecimal(Number.NaN, 'number'), null);
  assert.equal(thresholdFixedDecimal(1e40, 'number'), null, 'longer than 40 characters');
  assert.equal(thresholdFixedDecimal(1e-39, 'number'), null, 'longer than 40 characters');
  assert.equal(thresholdFixedDecimal(1e39, 'number'), '1' + '0'.repeat(39), 'exactly 40 characters');
  assert.equal(significantDigits(1200), 2);
  assert.equal(significantDigits(0.00120), 2);
  assert.throws(() => decimalString(Infinity), RangeError);
});
test('threshold text is never exponential', () => {
  for (const value of [1e-5, 2.5e-10, 3e25, -6e-8]) assert.doesNotMatch(decimalString(value), /e/i);
});

test('built-in rule constants equal the rule-set contract', async () => {
  const text = await contract('rule-set.md');
  const example = JSON.parse(text.split('```json')[1].split('```')[0].replace(/"3b1f…"/, '"x"'));
  assert.deepEqual(HARD_RULES.map(({ id }) => id), ['hard:process-status', 'hard:negative-duration', 'hard:success-with-error', 'hard:count-conservation']);
  // detection-decision.md 4.3 대조는 #117(정본 갱신) 병합 뒤에 다시 넣는다. 지금 이 저장소의 그 문서는 이전 판이다.
  assert.deepEqual(example.hardRules.map(({ enabled, ...rule }) => rule), HARD_RULES);
  assert.ok(example.hardRules.every((rule) => rule.enabled === true));
  for (const hash of [REFERENCE_HASH, V2_ALL_FOUR_HASH, V2_DEFAULT_HASH, V2_REFERENCE_HASH]) assert.ok(text.includes(hash), `${hash} is the contract value`);
});
test('soft rule field constants equal the method-catalog example', async () => {
  const text = await contract('method-catalog.md');
  const example = JSON.parse(text.slice(text.indexOf('## 예시 카탈로그 JSON')).split('```json')[1].split('```')[0]);
  assert.deepEqual(JSON.parse(JSON.stringify(SOFT_RULE_FIELDS)), example.softRuleFields);
  assert.equal(SOFT_RULE_FIELDS.length, 6);
  assert.deepEqual(JSON.parse(JSON.stringify(SOFT_RULE)), example.softRule);
  for (const key of ['catalogVersion', 'categoryDisplayNames', 'hardRules']) assert.deepEqual(METHOD_CATALOG[key], example[key], key);
  assert.equal(METHOD_CATALOG.generatedFrom.gitRef, example.generatedFrom.gitRef);
  assert.equal(TRAINER_CAPABILITIES.methodCatalog, METHOD_CATALOG, 'screen reads fields through GET /api/jobs/capabilities');
});

function hard(enabled = true) {
  return HARD_RULES.map((rule) => ({ id: rule.id, enabled, ...(rule.editable.includes('severity') ? { severity: rule.severity } : {}) }));
}
const stored = (id, overrides = {}) => ({ id, name: '처리 시간 5초 초과', field: 'elapsed_ms', operator: 'gt', threshold: 5000, unit: 'ms', severity: 'warning', enabled: true, createdAtRevision: 1, updatedAtRevision: 1, ...overrides });
const current = (softRules = [], enabled = true) => ({ hardRules: projectHardRules(hard(enabled)), softRules });
const sent = ({ createdAtRevision, updatedAtRevision, unit, ...rule }) => rule;
const added = (overrides = {}) => ({ id: null, clientRef: 'new-1', name: '재시도 3회 이상', field: 'retry_count', operator: 'gte', threshold: 3, severity: 'info', enabled: true, ...overrides });
const request = (overrides = {}) => parseRuleSetRequest({ expectedRevision: 1, hardRules: hard(), softRules: [], ...overrides });
const plan = (body, { state = current(), restore = null, models = 0 } = {}) => planRuleSetChange({
  request: body, current: state, restore, activeModelCount: models, nextRevision: 2,
  assignId: (field) => `soft:${field}-0000000a`,
});
const code = (fn) => { try { fn(); } catch (error) { assert.ok(error instanceof RuleSetError, String(error)); return error.code; } return 'passed'; };

test('request shape errors are invalid_rule_set_request', () => {
  const body = { expectedRevision: 1, hardRules: hard(), softRules: [] };
  for (const bad of [null, [], { ...body, extra: 1 }, { ...body, expectedRevision: -1 }, { ...body, expectedRevision: 1.5 }, { hardRules: [], softRules: [] },
    { ...body, confirmHardRuleDisable: 'yes' }, { ...body, restoreFromRevision: -1 }, { ...body, note: 3 }, { ...body, hardRules: [{ id: 'x' }] },
    { ...body, softRules: [{ ...added(), threshold: '3' }] }, { ...body, softRules: [added({ clientRef: undefined })] },
    { ...body, softRules: [added({ clientRef: 'x'.repeat(41) })] }, { ...body, softRules: [{ ...added(), id: 'soft:a' }] },
    { ...body, note: 'a\u0000b' }, { ...body, note: 'a\ud83d' }, { ...body, softRules: [added({ clientRef: 'a\u0000' })] }, { ...body, softRules: [added({ clientRef: '\udc00' })] }]) {
    assert.equal(code(() => parseRuleSetRequest(bad)), 'invalid_rule_set_request', JSON.stringify(bad));
  }
});
test('validation stops at the first failure in contract order', () => {
  const oneBad = { hardRules: [], softRules: [added({ field: 'nope' })] };
  assert.equal(code(() => plan(request(oneBad))), 'hard_rule_missing:hard:process-status', 'hard list before soft rules');
  assert.equal(code(() => plan(request({ hardRules: [...hard(), { id: 'hard:x', enabled: true }] }))), 'hard_rule_unknown:hard:x');
  assert.equal(code(() => plan(request({ hardRules: [...hard(), hard()[0]] }))), 'hard_rule_duplicate:hard:process-status');
  assert.equal(code(() => plan(request({ hardRules: hard(false), softRules: [added({ field: 'nope' })] }))), 'hard_rule_disable_confirmation_required', 'confirmation before soft rules');
  assert.equal(code(() => plan(request({ softRules: [added({ id: 'soft:gone', clientRef: undefined, field: 'nope' })] }))), 'soft_rule_unknown:soft:gone', 'unknown id before field');
  const state = current([stored('soft:elapsed_ms-1')]);
  const existing = sent(state.softRules[0]);
  assert.equal(code(() => plan(request({ softRules: [existing, existing] }), { state })), 'soft_rule_duplicate:soft:elapsed_ms-1');
  assert.equal(code(() => plan(request({ softRules: [added(), added({ name: 'x' })] }))), 'soft_rule_client_ref_duplicate');
  assert.equal(code(() => plan(request({ softRules: [added({ field: 'nope' })] }))), 'soft_rule_field_unknown:nope');
  assert.equal(code(() => plan(request({ softRules: [{ ...existing, field: 'retry_count', threshold: 1.5 }] }), { state })), 'soft_rule_field_immutable:soft:elapsed_ms-1', 'immutable before operator and threshold');
  assert.equal(code(() => plan(request({ softRules: [added({ unit: 'ms' })] }))), 'soft_rule_unit_mismatch:new-1');
  assert.equal(code(() => plan(request({ softRules: [added({ operator: 'eq' })] }))), 'soft_rule_operator_invalid:new-1');
  assert.equal(code(() => plan(request({ softRules: [added({ threshold: 1.5 })] }))), 'soft_rule_threshold_invalid:new-1');
  assert.equal(code(() => plan(request({ softRules: [added({ severity: 'high' })] }))), 'soft_rule_severity_invalid:new-1');
  assert.equal(code(() => plan(request({ softRules: [added({ name: '   ' })] }))), 'soft_rule_name_invalid');
  assert.equal(code(() => plan(request({ softRules: [added({ name: 'x'.repeat(121) })] }))), 'soft_rule_name_invalid');
  assert.equal(code(() => plan(request({ softRules: [added({ name: 'a\ud83d' })] }))), 'soft_rule_name_invalid');
  assert.equal(code(() => plan(request({ softRules: [added({ name: '규칙 😀' })] }))), 'passed', 'paired surrogates are normal text');
  assert.equal(code(() => plan(request({ softRules: [added({ name: 'Abc' }), added({ clientRef: 'new-2', name: 'aBC' })] }))), 'soft_rule_name_duplicate');
  const many = Array.from({ length: 33 }, (_, index) => added({ clientRef: `n${index}`, name: `n${index}` }));
  assert.equal(code(() => plan(request({ softRules: many }))), 'soft_rule_limit_exceeded');
  assert.equal(code(() => plan(request({ softRules: many.slice(0, 32) }))), 'passed', '32 rules are allowed');
});
test('detection_methods_empty counts the save result and active models', () => {
  const body = request({ hardRules: hard(false), confirmHardRuleDisable: true });
  assert.equal(code(() => plan(body)), 'detection_methods_empty');
  assert.equal(code(() => plan(body, { models: 1 })), 'passed', 'an active model keeps a detection method');
  assert.equal(code(() => plan(request({ hardRules: hard(false), confirmHardRuleDisable: true, softRules: [added()] }))), 'passed');
  assert.equal(code(() => plan(request({ hardRules: hard(false), confirmHardRuleDisable: true, softRules: [added({ enabled: false })] }))), 'detection_methods_empty');
});
test('unchanged content is refused only when hash and names both match', () => {
  const state = current([stored('soft:elapsed_ms-1')]);
  const same = sent(state.softRules[0]);
  assert.equal(code(() => plan(request({ softRules: [same] }), { state })), 'rule_set_unchanged');
  const renamed = plan(request({ softRules: [{ ...same, name: '새 이름' }] }), { state });
  assert.equal(renamed.contentHash, contentHash(state), 'a name-only save keeps the hash');
  assert.deepEqual(renamed.changes, [{ type: 'soft_updated', id: 'soft:elapsed_ms-1', name: '새 이름', diff: { name: ['처리 시간 5초 초과', '새 이름'] } }]);
});
test('confirmation applies only to rules newly turned off', () => {
  const off = current([stored('soft:elapsed_ms-1')], false);
  const body = request({ hardRules: hard(false), softRules: [{ ...sent(off.softRules[0]), threshold: 6000 }] });
  assert.equal(code(() => plan(body, { state: off })), 'passed', 'already off rules need no flag');
});
test('change classification and summary follow the contract', () => {
  const state = current([stored('soft:elapsed_ms-1'), stored('soft:elapsed_ms-2', { name: 'b' }), stored('soft:elapsed_ms-3', { name: 'c' }), stored('soft:retry_count-4', { name: 'd', field: 'retry_count', unit: 'count', operator: 'gte', threshold: 3 })]);
  const [first, second, third] = state.softRules.map(sent);
  const result = plan(request({
    hardRules: [hard()[0], { id: 'hard:negative-duration', enabled: false }, ...hard().slice(2)], confirmHardRuleDisable: true,
    softRules: [{ ...first, threshold: 8000, enabled: false }, { ...second, enabled: false }, third, added({ name: '새 규칙' })],
  }), { state });
  assert.deepEqual(result.changes.map((change) => change.type), ['hard_disabled', 'soft_updated', 'soft_disabled', 'soft_added', 'soft_removed']);
  assert.deepEqual(result.changes[1].diff, { threshold: [5000, 8000], enabled: [true, false] }, 'mixed enabled change stays soft_updated');
  assert.deepEqual(result.summary, { hardEnabled: 0, hardDisabled: 1, hardUpdated: 0, softAdded: 1, softUpdated: 1, softRemoved: 1, softEnabled: 0, softDisabled: 1 });
  assert.deepEqual(result.snapshot.softRules.map((rule) => rule.id), ['soft:elapsed_ms-1', 'soft:elapsed_ms-2', 'soft:elapsed_ms-3', 'soft:retry_count-0000000a']);
  assert.deepEqual(result.snapshot.softRules.map((rule) => rule.updatedAtRevision), [2, 2, 1, 2], 'untouched rule keeps its revision');
  assert.equal(result.snapshot.softRules[3].unit, 'count', 'server fills the catalog unit');
  assert.deepEqual(result.hardDisabledAfterSave, ['hard:negative-duration']);
});
test('restore fills rules and severity per the contract', () => {
  const deleted = stored('soft:elapsed_ms-1', { createdAtRevision: 5, updatedAtRevision: 6 });
  const v1Status = { id: 'hard:process-status', enabled: true, methodVersion: 'process-status-hr-v1', severity: { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_N: 'warning' } } };
  const restore = { hardRules: [v1Status], softRules: [deleted] };
  const state = current([]);
  const statusOnly = [{ id: 'hard:process-status', enabled: true }];
  const body = (overrides = {}) => request({ restoreFromRevision: 3, hardRules: statusOnly, softRules: [sent(deleted)], ...overrides });
  const filledOff = hard().slice(1).map(({ id }) => ({ id, enabled: false }));
  assert.equal(code(() => plan(body())), 'restore_revision_unknown');
  // 스냅샷에 없는 규칙은 꺼짐으로 채우고, 현재 켜져 있으면 새로 꺼지는 것이라 확인이 필요하다.
  assert.equal(code(() => plan(body(), { state, restore })), 'hard_rule_disable_confirmation_required');
  const restored = plan(body({ confirmHardRuleDisable: true }), { state, restore });
  assert.deepEqual(restored.changes.map((change) => [change.type, change.id]), [
    ['hard_disabled', 'hard:negative-duration'], ['hard_disabled', 'hard:success-with-error'], ['hard_disabled', 'hard:count-conservation'], ['soft_added', 'soft:elapsed_ms-1'],
  ]);
  assert.equal(restored.changes.at(-1).restored, true);
  assert.deepEqual([restored.snapshot.softRules[0].createdAtRevision, restored.snapshot.softRules[0].updatedAtRevision], [5, 2]);
  const rule = (id) => restored.snapshot.hardRules.find((item) => item.id === id);
  assert.deepEqual([rule('hard:negative-duration').enabled, rule('hard:negative-duration').severity], [false, 'critical'], 'missing rule is off with the built-in severity');
  assert.deepEqual([rule('hard:process-status').methodVersion, rule('hard:process-status').severity], ['process-status-hr-v2', STATUS_DEFAULT], 'v1 source is filled with current defaults');
  assert.notEqual(restored.contentHash, contentHash({ hardRules: restore.hardRules, softRules: [deleted] }, 1), 'a restored v1 revision gets a v2 hash');
  // v1 출처의 위험도는 보내도 무시한다.
  const ignored = plan(body({ confirmHardRuleDisable: true, hardRules: [{ ...statusOnly[0], severity: v1Status.severity }] }), { state, restore });
  assert.deepEqual(ignored.snapshot.hardRules.find((item) => item.id === 'hard:process-status').severity, STATUS_DEFAULT);
  // 본문이 채움값과 다르면 거절.
  assert.equal(code(() => plan(body({ hardRules: [...statusOnly, { id: 'hard:negative-duration', enabled: true }] }), { state, restore })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(body({ confirmHardRuleDisable: true, hardRules: [...statusOnly, ...filledOff] }), { state, restore })), 'passed');
  // 채움 규칙의 위험도도 채움값과 같아야 하고, 편집 불가 규칙의 다른 위험도는 계약 순서대로 mismatch가 먼저다.
  assert.equal(code(() => plan(body({ confirmHardRuleDisable: true, hardRules: [...statusOnly, { ...filledOff[0], severity: 'info' }, ...filledOff.slice(1)] }), { state, restore })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(body({ confirmHardRuleDisable: true, hardRules: [...statusOnly, { ...filledOff[0], severity: 'critical' }, ...filledOff.slice(1)] }), { state, restore })), 'passed');
  const noStatus = { hardRules: HARD_RULES.slice(1).map((rule) => ({ ...rule, enabled: true })), softRules: [] };
  const statusBody = (severity) => request({ restoreFromRevision: 3, confirmHardRuleDisable: true, hardRules: [{ id: 'hard:process-status', enabled: false, severity }, ...hard().slice(1)] });
  assert.equal(code(() => plan(statusBody({ byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_F: 'info' } }), { state, restore: noStatus })), 'restore_payload_mismatch', 'a status rule missing from the snapshot is compared to the filled default');
  assert.equal(code(() => plan(statusBody(STATUS_DEFAULT), { state, restore: noStatus })), 'passed');
  const whole = { hardRules: HARD_RULES.map((rule) => ({ ...rule, enabled: true })), softRules: [] };
  const wrongLevel = request({ restoreFromRevision: 3, hardRules: hard().map((rule) => (rule.id === 'hard:negative-duration' ? { ...rule, severity: 'info' } : rule)) });
  assert.equal(code(() => plan(wrongLevel, { state: current([stored('soft:elapsed_ms-9')]), restore: whole })), 'restore_payload_mismatch', 'mismatch comes before hard_rule_field_not_editable on restore');
  assert.equal(code(() => plan(body({ softRules: [{ ...sent(deleted), threshold: 1 }] }), { state, restore })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(body({ softRules: [] }), { state, restore })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(body({ hardRules: [{ id: 'hard:process-status', enabled: false }] }), { state, restore })), 'restore_payload_mismatch');
  // v2 출처의 위험도는 저장값 그대로여야 한다.
  const edited = { byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_N: 'critical' } };
  const v2 = { hardRules: [{ ...HARD_RULES[0], enabled: true, severity: edited }, ...HARD_RULES.slice(1).map((rule) => ({ ...rule, enabled: true }))], softRules: [] };
  const v2Body = (severity) => request({ restoreFromRevision: 3, hardRules: [{ id: 'hard:process-status', enabled: true, ...(severity ? { severity } : {}) }, ...hard().slice(1)] });
  const updated = plan(v2Body(edited), { state, restore: v2 });
  assert.deepEqual(updated.changes, [{ type: 'hard_updated', id: 'hard:process-status', displayName: '상태가 S가 아님', diff: { severity: [STATUS_DEFAULT, edited] } }]);
  assert.equal(code(() => plan(v2Body(STATUS_DEFAULT), { state, restore: v2 })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(v2Body(null), { state, restore: v2 })), 'hard_rule_severity_invalid:hard:process-status');
  const legacy = { hardRules: [{ id: 'hard:old', enabled: true }, ...hard()], softRules: [] };
  assert.equal(code(() => plan(request({ restoreFromRevision: 3, hardRules: legacy.hardRules }), { state, restore: legacy })), 'hard_rule_unknown:hard:old');
  assert.equal(code(() => plan(request({ softRules: [sent(deleted)] }), { state })), 'soft_rule_unknown:soft:elapsed_ms-1', 'exception is restore only');
});
test('stored rules are projected by the current methodVersion', () => {
  const v1 = { id: 'hard:process-status', enabled: true, methodVersion: 'process-status-hr-v1', severity: { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_N: 'warning' } } };
  const edited = { byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_H: 'critical' } };
  const [status, negative, successWithError, countConservation] = projectHardRules([v1]);
  assert.deepEqual([status.methodVersion, status.severity, status.enabled, status.editable], ['process-status-hr-v2', STATUS_DEFAULT, true, ['enabled', 'severity']], 'v1 values are not shown');
  assert.deepEqual([negative.enabled, negative.severity, negative.editable], [false, 'critical', ['enabled']], 'not in the snapshot means off');
  assert.deepEqual([successWithError.enabled, countConservation.enabled], [false, false]);
  assert.deepEqual(projectHardRules([{ id: 'hard:process-status', enabled: false, methodVersion: 'process-status-hr-v2', severity: edited }])[0].severity, edited, 'current methodVersion keeps the saved value');
  assert.deepEqual(projectHardRules([{ id: 'hard:process-status', enabled: true }])[0].severity, STATUS_DEFAULT, 'no methodVersion uses the default');
  assert.deepEqual(projectHardRules([{ ...v1, severity: edited }])[0].severity, STATUS_DEFAULT, 'a v1 item is ignored even with five valid codes');
  assert.deepEqual(projectHardRules([{ id: 'hard:process-status', enabled: true, methodVersion: 'process-status-hr-v2', severity: { byCode: {} } }])[0].severity, STATUS_DEFAULT);
});
test('severity is editable only on the status rule', () => {
  const invalid = 'hard_rule_severity_invalid:hard:process-status';
  const others = hard().slice(1);
  const withStatus = (severity) => request({ hardRules: [{ id: 'hard:process-status', enabled: true, ...(severity === undefined ? {} : { severity }) }, ...others] });
  const { PROCESS_STATUS_N: _dropped, ...fourCodes } = STATUS_DEFAULT.byCode;
  for (const bad of [undefined, null, 'critical', {}, { byCode: fourCodes }, { byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_X: 'info' } },
    { byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_N: 'high' } }, { ...STATUS_DEFAULT, extra: 1 }]) {
    assert.equal(code(() => plan(withStatus(bad))), invalid, JSON.stringify(bad));
  }
  const notEditable = request({ hardRules: [hard()[0], { id: 'hard:negative-duration', enabled: true, severity: 'info' }, ...others.slice(1)] });
  assert.equal(code(() => plan(notEditable)), 'hard_rule_field_not_editable:hard:negative-duration');
  assert.equal(code(() => plan(request({ hardRules: [hard()[0], { id: 'hard:negative-duration', enabled: true, severity: 'critical' }, ...others.slice(1)], softRules: [added()] }))), 'passed', 'the built-in value may be sent');
  const edited = { byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_N: 'warning' } };
  const changed = plan(withStatus(edited));
  assert.deepEqual(changed.changes, [{ type: 'hard_updated', id: 'hard:process-status', displayName: '상태가 S가 아님', diff: { severity: [STATUS_DEFAULT, edited] } }]);
  assert.equal(changed.summary.hardUpdated, 1);
  assert.notEqual(changed.contentHash, V2_DEFAULT_HASH, 'severity is a v2 hash input');
  const both = plan(request({ confirmHardRuleDisable: true, hardRules: [{ id: 'hard:process-status', enabled: false, severity: edited }, ...others] }));
  assert.deepEqual(both.changes.map((change) => change.type), ['hard_disabled', 'hard_updated'], 'enabled and severity are two entries');
  const reordered = { byCode: Object.fromEntries(Object.entries(edited.byCode).reverse()) };
  assert.equal(code(() => plan(withStatus(reordered), { state: { ...current(), hardRules: projectHardRules([{ ...HARD_RULES[0], enabled: true, severity: edited }, ...HARD_RULES.slice(1).map((rule) => ({ ...rule, enabled: true }))]) } })), 'rule_set_unchanged', 'key order is not a change');
  // r0 현재 상태: 내장 기본값으로 해석한 상태와 같으면 무변경이다.
  const r0 = { hardRules: projectHardRules([{ id: 'hard:process-status', enabled: true, methodVersion: 'process-status-hr-v1' }, { id: 'hard:negative-duration', enabled: true }]), softRules: [] };
  const asR0 = hard().map((rule, index) => ({ ...rule, enabled: index < 2 }));
  assert.equal(code(() => plan(request({ hardRules: asR0 }), { state: r0 })), 'rule_set_unchanged');
  assert.deepEqual(plan(request(), { state: r0 }).changes.map((change) => change.type), ['hard_enabled', 'hard_enabled'], 'turning on rules r0 does not know is a change');
});

test('threshold-stats request parsing follows the contract', () => {
  const source = '11111111-1111-4111-8111-111111111111';
  const body = (period, extra = {}) => ({ field: 'elapsed_ms', sourceId: source, period, ...extra });
  const custom = { mode: 'custom', from: '2026-10-01T00:00:00+09:00', until: '2026-11-01T00:00:00+09:00' };
  assert.equal(parseThresholdStatsRequest(body(custom)).statistic, 'p95');
  assert.equal(parseThresholdStatsRequest(body(custom)).from, '2026-09-30T15:00:00.000Z', 'custom times are normalized to UTC');
  assert.equal(parseThresholdStatsRequest(body({ ...custom, from: '2024-02-29T00:00:00+20:00' })).from, '2024-02-28T04:00:00.000Z', 'leap day and any offset the database cannot parse are normalized');
  assert.equal(parseThresholdStatsRequest(body({ mode: 'recent_1m' })).timezone, 'Asia/Seoul');
  for (const bad of [null, body(custom, { extra: 1 }), body(custom, { statistic: 'p50' }), body(custom, { sourceId: 'x' }), body({ mode: 'other' }), body({ mode: 'custom', from: custom.from }),
    body({ ...custom, from: '2026-10-01T00:00:00' }), body({ ...custom, from: custom.until, until: custom.from }), body({ mode: 'recent_1m', from: custom.from }),
    body({ mode: 'active_model_training_start', until: custom.until }), body({ mode: 'recent_3m', timezone: 'Mars/Base' }), body({ mode: 'recent_3m', timeBasis: 'other' }),
    body(custom, { excludedIntervals: [{ from: custom.from }] }), body(custom, { excludedIntervals: [{ ...custom, reasonCode: 'x' }] }),
    ...['toString', 'constructor', '__proto__', 'hasOwnProperty'].flatMap((name) => [body(custom, { statistic: name }), body({ ...custom, timeBasis: name })]),
    body({ ...custom, from: '2026-02-30T00:00:00Z' }), body({ ...custom, until: '2026-11-31T00:00:00+09:00' }), body({ ...custom, from: '2026-13-01T00:00:00Z' }),
    body({ ...custom, from: '2026-10-01T24:00:00Z' }), body(custom, { excludedIntervals: [{ from: '2026-02-30T00:00:00Z', until: custom.until }] })]) {
    assert.equal(code(() => parseThresholdStatsRequest(bad)), 'invalid_threshold_stats_request', JSON.stringify(bad));
  }
});
test('display threshold follows the catalog precision without exponents', () => {
  const field = (id) => SOFT_RULE_FIELDS.find((item) => item.fieldId === id);
  assert.equal(displayThreshold(7210.5, field('elapsed_ms')), '7211');
  assert.equal(displayThreshold(0.00003125, field('error_ratio')), '0.00003125');
  assert.equal(displayThreshold(0.123456, field('error_ratio')), '0.1235');
  assert.equal(displayThreshold(2.4, field('retry_count')), '2');
});
