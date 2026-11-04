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
import { METHOD_CATALOG, SOFT_RULE, SOFT_RULE_FIELDS } from '../dist/ruleset/softRuleCatalog.js';
import { displayThreshold, parseThresholdStatsRequest } from '../dist/ruleset/thresholdStats.js';
import { TRAINER_CAPABILITIES } from '../dist/routes/trainerContract.js';

const contracts = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../contracts');
const contract = (name) => readFile(path.join(contracts, name), 'utf8');
const REFERENCE_HASH = '5b15237724b48fcc4f18335a374ad7d5d8d7bcbaa7b5f851120f4d17b23dd8aa';
const R0_HASH = '80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89';

test('contract reference object hashes to the pinned value', () => {
  const canonical = {
    hardRules: [{ id: 'hard:negative-duration', enabled: false }, { id: 'hard:process-status', enabled: true }],
    softRules: [{ id: 'soft:elapsed_ms-3f9a2c1d', field: 'elapsed_ms', operator: 'gt', threshold: '8000', severity: 'warning', enabled: true }],
  };
  assert.equal(canonicalSha256(canonical), REFERENCE_HASH);
  assert.equal(contentHash({
    hardRules: [{ id: 'hard:process-status', enabled: true }, { id: 'hard:negative-duration', enabled: false }],
    softRules: [{ id: 'soft:elapsed_ms-3f9a2c1d', name: '이름은 hash 입력이 아님', field: 'elapsed_ms', operator: 'gt', threshold: 8000, unit: 'ms', severity: 'warning', enabled: true, createdAtRevision: 1, updatedAtRevision: 2 }],
  }), REFERENCE_HASH, 'number threshold, name and audit fields do not change the hash');
});
test('r0 default state has the fixed hash the migration inserts', () => {
  assert.equal(contentHash({ hardRules: projectHardRules([]), softRules: [] }), R0_HASH);
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

test('built-in rule constants equal detection-decision 4.3 and the rule-set example', async () => {
  const example = JSON.parse((await contract('rule-set.md')).split('```json')[1].split('```')[0].replace(/"3b1f…"/, '"x"'));
  assert.deepEqual(HARD_RULES.map(({ id }) => id), ['hard:process-status', 'hard:negative-duration']);
  assert.deepEqual(example.hardRules.map(({ enabled, editable, ...rule }) => rule), HARD_RULES);
  const decision = await contract('detection-decision.md');
  const table = decision.slice(decision.indexOf('### 4.3'), decision.indexOf('## 5. '));
  for (const rule of HARD_RULES) {
    const row = table.split('\n').find((line) => line.startsWith(`| \`${rule.id}\``));
    assert.ok(row, rule.id);
    assert.ok(row.includes(`\`${rule.methodVersion}\``) && row.includes(rule.displayName) && row.includes(rule.conditionText), rule.id);
    assert.ok(row.includes(typeof rule.severity === 'string' ? `\`"${rule.severity}"\`` : `\`${JSON.stringify(rule.severity).replaceAll(',', ', ').replaceAll(':', ': ')}\``), `${rule.id} severity`);
  }
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

const hard = (enabled = true) => projectHardRules([]).map((rule) => ({ id: rule.id, enabled }));
const current = (softRules = [], enabled = true) => ({ hardRules: projectHardRules(hard(enabled)), softRules });
const stored = (id, overrides = {}) => ({ id, name: '처리 시간 5초 초과', field: 'elapsed_ms', operator: 'gt', threshold: 5000, unit: 'ms', severity: 'warning', enabled: true, createdAtRevision: 1, updatedAtRevision: 1, ...overrides });
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
    hardRules: [{ id: 'hard:process-status', enabled: true }, { id: 'hard:negative-duration', enabled: false }], confirmHardRuleDisable: true,
    softRules: [{ ...first, threshold: 8000, enabled: false }, { ...second, enabled: false }, third, added({ name: '새 규칙' })],
  }), { state });
  assert.deepEqual(result.changes.map((change) => change.type), ['hard_disabled', 'soft_updated', 'soft_disabled', 'soft_added', 'soft_removed']);
  assert.deepEqual(result.changes[1].diff, { threshold: [5000, 8000], enabled: [true, false] }, 'mixed enabled change stays soft_updated');
  assert.deepEqual(result.summary, { hardEnabled: 0, hardDisabled: 1, softAdded: 1, softUpdated: 1, softRemoved: 1, softEnabled: 0, softDisabled: 1 });
  assert.deepEqual(result.snapshot.softRules.map((rule) => rule.id), ['soft:elapsed_ms-1', 'soft:elapsed_ms-2', 'soft:elapsed_ms-3', 'soft:retry_count-0000000a']);
  assert.deepEqual(result.snapshot.softRules.map((rule) => rule.updatedAtRevision), [2, 2, 1, 2], 'untouched rule keeps its revision');
  assert.equal(result.snapshot.softRules[3].unit, 'count', 'server fills the catalog unit');
  assert.deepEqual(result.hardDisabledAfterSave, ['hard:negative-duration']);
});
test('restore branches follow the contract exceptions', () => {
  const deleted = stored('soft:elapsed_ms-1', { createdAtRevision: 5, updatedAtRevision: 6 });
  const restore = { hardRules: [{ id: 'hard:process-status', enabled: true }], softRules: [deleted] };
  const state = current([]);
  const body = (overrides = {}) => request({ restoreFromRevision: 3, hardRules: [{ id: 'hard:process-status', enabled: true }], softRules: [sent(deleted)], ...overrides });
  assert.equal(code(() => plan(body())), 'restore_revision_unknown');
  const restored = plan(body(), { state, restore });
  assert.deepEqual(restored.changes, [{ type: 'soft_added', id: 'soft:elapsed_ms-1', name: deleted.name, restored: true }], 'new built-in rule filled silently');
  assert.deepEqual([restored.snapshot.softRules[0].createdAtRevision, restored.snapshot.softRules[0].updatedAtRevision], [5, 2]);
  assert.equal(restored.snapshot.hardRules.find((rule) => rule.id === 'hard:negative-duration').enabled, true, 'default on');
  assert.equal(code(() => plan(body({ softRules: [{ ...sent(deleted), threshold: 1 }] }), { state, restore })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(body({ softRules: [] }), { state, restore })), 'restore_payload_mismatch');
  assert.equal(code(() => plan(body({ hardRules: [{ id: 'hard:process-status', enabled: false }] }), { state, restore })), 'restore_payload_mismatch');
  const newRuleOff = body({ hardRules: [{ id: 'hard:process-status', enabled: true }, { id: 'hard:negative-duration', enabled: false }] });
  assert.equal(code(() => plan(newRuleOff, { state, restore })), 'hard_rule_disable_confirmation_required', 'body value wins over the default');
  assert.deepEqual(plan({ ...newRuleOff, confirmHardRuleDisable: true }, { state, restore }).changes.map((change) => change.type), ['hard_disabled', 'soft_added']);
  const legacy = { hardRules: [{ id: 'hard:old', enabled: true }, { id: 'hard:process-status', enabled: true }, { id: 'hard:negative-duration', enabled: true }], softRules: [] };
  assert.equal(code(() => plan(request({ restoreFromRevision: 3, hardRules: legacy.hardRules }), { state, restore: legacy })), 'hard_rule_unknown:hard:old');
  assert.equal(code(() => plan(request({ softRules: [sent(deleted)] }), { state })), 'soft_rule_unknown:soft:elapsed_ms-1', 'exception is restore only');
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
