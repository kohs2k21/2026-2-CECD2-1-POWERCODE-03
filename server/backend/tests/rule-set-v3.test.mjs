// 룰 세트 v3 순수 규칙 단위 시험(rule-set.md v3.1~v3.8): hash v3 기준값 (c)~(h)·Python 독립 구현 대조, 코드 포인트 정렬·pathKey,
// 저장 검증 순서·422 코드(관계·프로토콜·scope·새 내장 하드룰 위험도), id 발급, 변경 요약, 경고, detection_methods_empty, 무변경, 되돌리기.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  CONTENT_HASH_VERSION, RuleSetError, canonicalJson, canonicalObject, compareCodePoints, contentHash, parseRuleSetRequest, pathKey,
  planRuleSetChange, projectHardRules,
} from '../dist/ruleset/ruleSetContract.js';
import { HARD_RULES } from '../dist/ruleset/hardRuleCatalog.js';
import { approvalState } from '../dist/ruleset/ruleCandidates.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const contractText = await readFile(path.join(here, '../../contracts/rule-set.md'), 'utf8');
// rule-set.md v3.8 기준값(2026-11-13 Python 3.10·Node 22 계산). 문서에서 옮긴 값 그대로.
const V3 = {
  c: '01875d1309dbe56a94158de82f4c5b94664dc25651c77ddf70a6cec96b2960b2',
  d: 'fbb708690f49cbdab80da1f2fe4e890a9e577314dc4d89c07a13168d28091c6b',
  e: '8f258136115b5a944b016232c1224050b6a11d8efc06e375c450020d36b91523',
  f: '63da99508a76748d7c384a32c2bd3067ab4eff79129cc3cfb97a07a22d93e2a0',
  g: '0fb1055a061185dcee5b8d79c7645a9be1513cf388859a1d3151687dcbf91351',
  h: '73045b14ab8d6c81d814e164283f5432a54f7a9d997ec0c636b6d6f6de4ab895',
};
const H_CANONICAL = '{"hardRules":[],"protocolRules":[{"enabled":true,"id":"hard:proto-0000000b","interfaceId":"IF_D","paths":[["A%3A"],["A:"]],"severity":"warning"}],"relationRules":[],"softRules":[]}';
const STATUS_DEFAULT = { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_Z: 'warning', PROCESS_STATUS_H: 'info', PROCESS_STATUS_N: 'info' } };

/** v3.8 각 항목의 직렬화 문자열과 hash(문서 문장에서 읽음). */
const contractValue = (letter) => {
  const section = contractText.slice(contractText.indexOf('### v3.8'), contractText.indexOf('## 저장소 요구사항'));
  const line = section.split('\n').find((item) => item.startsWith(`- (${letter})`));
  const match = /직렬화 문자열 `([^`]+)` → SHA-256 `([0-9a-f]{64})`/.exec(line ?? '');
  assert.ok(match, `(${letter}) is in rule-set.md v3.8`);
  return { canonical: match[1], sha256: match[2] };
};

const builtIn = (overrides = {}) => HARD_RULES.map((rule) => ({ ...rule, enabled: !['hard:transaction-failed'].includes(rule.id), ...(overrides[rule.id] ?? {}) }));
const source = { jobId: 'job-1', snapshotId: 'snap-1', period: { from: 'a', to: 'b' } };
const inputs = {
  c: { hardRules: builtIn(), softRules: [], relationRules: [], protocolRules: [] },
  d: {
    hardRules: builtIn({
      'hard:transaction-failed': { enabled: true, severity: { byCode: { E901: 'critical' }, default: 'warning' } },
      'hard:unregistered-interface': { severity: 'warning' },
    }),
    softRules: [{ id: 'soft:elapsed_ms-3f9a2c1d', name: '처리 시간', field: 'elapsed_ms', operator: 'gt', threshold: 8000, unit: 'ms', severity: 'warning', enabled: true,
      createdAtRevision: 1, updatedAtRevision: 1, scope: { processId: 'IF_A_PR002', adapterType: 'HTTP', interfaceId: 'IF_A' } }],
    relationRules: [
      { id: 'hard:rel-3f9a2c1d', kind: 'precedes', scope: { interfaceId: 'IF_A', adapterType: 'HTTP' }, from: { processId: 'IF_A_PR001' }, to: { processId: 'IF_A_PR002' },
        match: null, severity: 'critical', enabled: true, source: { ...source, counts: { applicable: 100, satisfied: 97, violated: 2, undetermined: 1 } } },
      { id: 'hard:rel-0a1b2c3d', kind: 'required_step', scope: { interfaceId: 'IF_A' }, from: { processId: 'IF_A_PR001' }, to: null, match: null,
        severity: 'warning', enabled: true, source },
    ],
    // 저장 순서가 정렬 순서와 반대, 한글 채널 포함.
    protocolRules: [{ id: 'hard:proto-5c1e9a07', interfaceId: 'IF_A', severity: 'critical', enabled: true, source,
      paths: [{ channels: ['CH_A', '채널C', 'CH_B'], source: { kind: 'alert', decisionId: 'd-1' } }, { channels: ['CH_A', 'CH_B'], source: { kind: 'draft', jobId: 'j-1' } }] }],
  },
  e: { hardRules: [], softRules: [], protocolRules: [{ id: 'hard:proto-00000000', interfaceId: 'IF_B', severity: 'info', enabled: false,
    paths: [{ channels: ['\u{10400}'] }, { channels: ['\u{FF21}'] }] }] },
  f: { hardRules: [{ id: 'hard:transaction-failed', enabled: true, severity: { byCode: { 9: 'critical', 10: 'warning', '0100': 'critical', 8888: 'info', E901: 'warning' }, default: 'warning' } }],
    softRules: [] },
  g: { hardRules: [], softRules: [], protocolRules: [{ id: 'hard:proto-0000000a', interfaceId: 'IF_C', severity: 'warning', enabled: true,
    paths: [['A', 'B'], ['A-'], ['A:B'], ['A%']].map((channels) => ({ channels })) }] },
  h: { hardRules: [], softRules: [], protocolRules: [{ id: 'hard:proto-0000000b', interfaceId: 'IF_D', severity: 'warning', enabled: true,
    paths: [{ channels: ['A:'] }, { channels: ['A%3A'] }] }] },
};

test('new saves use hash v3 and rule candidates approval opens with it', () => {
  assert.equal(CONTENT_HASH_VERSION, 3);
  assert.deepEqual(approvalState(), { available: true, reason: null, contentHashVersion: 3 });
});

test('v3 reference values (c)-(h) match the contract string and hash', () => {
  for (const letter of ['c', 'd', 'e', 'f', 'g', 'h']) {
    const expected = contractValue(letter);
    assert.equal(expected.sha256, V3[letter], `(${letter}) pinned hash is the contract value`);
    assert.equal(canonicalJson(canonicalObject(inputs[letter], 3)), expected.canonical, `(${letter}) canonical string`);
    assert.equal(contentHash(inputs[letter], 3), V3[letter], `(${letter}) hash`);
  }
  assert.equal(contractValue('h').canonical, H_CANONICAL);
  assert.equal(contentHash(inputs.h), V3.h, 'the default version is 3');
});

test('v3 hash ignores source, display interfaceId, audit fields and stored order', () => {
  const shuffled = structuredClone(inputs.d);
  shuffled.relationRules.reverse();
  shuffled.relationRules[0].source = { other: true };
  shuffled.relationRules[1].scope = { adapterType: 'HTTP', interfaceId: 'IF_OTHER' };
  shuffled.protocolRules[0].paths.reverse();
  shuffled.protocolRules[0].source = null;
  shuffled.softRules[0].scope = { processId: 'IF_A_PR002', adapterType: 'HTTP' };
  shuffled.softRules[0].name = '다른 이름';
  shuffled.hardRules.reverse();
  assert.equal(contentHash(shuffled), V3.d);
  const requiredStepInterface = structuredClone(inputs.d);
  requiredStepInterface.relationRules[1].scope = { interfaceId: 'IF_B' };
  assert.notEqual(contentHash(requiredStepInterface), V3.d, 'required_step interfaceId is a judgement key');
});

test('numeric-looking byCode keys: the array form avoids the JS object key order trap', () => {
  const keys = ['9', '10', '0100', '8888', 'E901'];
  const object = Object.fromEntries(keys.map((key) => [key, 'info']));
  assert.deepEqual(Object.keys(object), ['9', '10', '8888', '0100', 'E901'], 'JS reorders integer-like keys');
  assert.deepEqual([...keys].sort(compareCodePoints), ['0100', '10', '8888', '9', 'E901']);
  assert.match(canonicalJson(canonicalObject(inputs.f, 3)), /\["0100","critical"\],\["10","warning"\],\["8888","info"\],\["9","critical"\],\["E901","warning"\]/);
});

test('code point order differs from UTF-16 order and pathKey escapes % before :', () => {
  assert.ok('\u{10400}' < '\u{FF21}', 'JS default comparison puts the astral character first');
  assert.ok(compareCodePoints('\u{FF21}', '\u{10400}') < 0);
  assert.ok(compareCodePoints('\u{E000}', '\u{10000}') < 0 && compareCodePoints('\u{D7FF}', '\u{E000}') < 0);
  assert.equal(compareCodePoints('ab', 'ab'), 0);
  assert.ok(compareCodePoints('a', 'ab') < 0);
  assert.equal(pathKey(['A:']), 'A%3A');
  assert.equal(pathKey(['A%3A']), 'A%253A');
  assert.equal(pathKey(['A%', 'B:C']), 'A%25:B%3AC');
  assert.notEqual(pathKey(['A:B']), pathKey(['A', 'B']));
});

test('Python independent implementation gives the same canonical string and hash', () => {
  const extra = [
    { hardRules: [{ id: 'hard:transaction-failed', enabled: false, severity: { byCode: { 0: 'info', '00': 'warning', 1: 'critical', 2: 'info', 10: 'info', 100: 'warning', '-1': 'info', a: 'info', A: 'info' }, default: 'info' } }],
      softRules: [{ id: 'soft:error_ratio-0000000b', field: 'error_ratio', operator: 'gte', threshold: 0.0100, severity: 'info', enabled: false, scope: { processId: 'P', processHubId: 'H' } }],
      protocolRules: [{ id: 'hard:proto-0000000c', interfaceId: '인터페이스', severity: 'critical', enabled: true,
        paths: [['\u{FFFD}'], ['\u{1F600}', 'x'], ['%:'], [':%'], ['é'], ['é']].map((channels) => ({ channels })) }],
      relationRules: [{ id: 'hard:rel-0000000d', kind: 'message_handoff', scope: null, from: { processId: 'A' }, to: { processId: 'B' },
        match: { from: 'OUT', to: 'IN', key: 'message_id' }, severity: 'info', enabled: false }, { id: 'hard:rel-0000000c', kind: 'allowed_branch',
        scope: { interfaceId: 'X' }, from: { processId: 'A' }, to: { processId: 'C' }, match: null, severity: 'warning', enabled: true }] },
  ];
  const cases = [...Object.values(inputs), ...extra];
  let run = null;
  for (const python of ['python', 'python3']) {
    const result = spawnSync(python, ['-I', path.join(here, 'rule-set-v3-parity.py')], { input: JSON.stringify(cases), encoding: 'utf8' });
    if (!result.error) { run = result; break; }
  }
  // 건너뛰지 않는다(CI에 python3가 있다). 없으면 실패로 알린다.
  assert.ok(run, 'python is required for the Python/Node hash parity check');
  assert.equal(run.status, 0, run.stderr);
  const outputs = JSON.parse(run.stdout);
  cases.forEach((snapshot, index) => {
    assert.equal(canonicalJson(canonicalObject(snapshot, 3)), outputs[index].canonical, `case ${index}`);
    assert.equal(contentHash(snapshot, 3), outputs[index].sha256, `case ${index}`);
  });
  assert.deepEqual(outputs.slice(0, 6).map((item) => item.sha256), Object.values(V3), 'Python reproduces (c)-(h)');
});

// ---------------------------------------------------------------------------------------------------------------
// 저장 검증

const hardBody = (overrides = {}) => HARD_RULES.map((rule) => ({ id: rule.id, enabled: true, ...(rule.editable.includes('severity') ? { severity: rule.severity } : {}), ...(overrides[rule.id] ?? {}) }));
const empty = () => ({ hardRules: projectHardRules(HARD_RULES.map((rule) => ({ ...rule, enabled: true }))), softRules: [], relationRules: [], protocolRules: [] });
const body = (overrides = {}) => parseRuleSetRequest({ expectedRevision: 1, hardRules: hardBody(), softRules: [], relationRules: [], protocolRules: [], ...overrides });
let counter = 0;
const plan = (request, { state = empty(), restore = null, models = 0, preview = false } = {}) => planRuleSetChange({
  request, current: state, restore, activeModelCount: models, nextRevision: 9,
  assignId: preview ? () => null : (prefix) => `${prefix}${(counter += 1).toString(16).padStart(8, '0')}`,
});
const code = (fn) => { try { fn(); } catch (error) { assert.ok(error instanceof RuleSetError, String(error)); return error.code; } return 'passed'; };
const precedes = (overrides = {}) => ({ id: null, clientRef: 'r1', kind: 'precedes', scope: { interfaceId: 'IF_A', adapterType: 'HTTP' },
  from: { processId: 'P1' }, to: { processId: 'P2' }, match: null, severity: 'critical', enabled: true, ...overrides });
const handoff = (overrides = {}) => precedes({ clientRef: 'r2', kind: 'message_handoff', match: { from: 'OUT', to: 'IN', key: 'message_id' }, ...overrides });
const required = (overrides = {}) => ({ id: null, clientRef: 'r3', kind: 'required_step', scope: { interfaceId: 'IF_A' }, from: { processId: 'P1' }, to: null,
  match: null, severity: 'warning', enabled: true, ...overrides });
const protocol = (overrides = {}) => ({ id: null, clientRef: 'p1', interfaceId: 'IF_A', paths: [{ channels: ['CH_B', 'CH_A'] }, { channels: ['CH_A', 'CH_B'] }],
  severity: 'critical', enabled: true, ...overrides });
const scoped = (overrides = {}) => ({ id: null, clientRef: 's1', name: '단계 처리 시간', field: 'elapsed_ms', operator: 'gt', threshold: 704, severity: 'warning',
  enabled: true, scope: { processId: 'P2', adapterType: 'HTTP', interfaceId: 'IF_A' }, ...overrides });

test('relation and protocol items are parsed strictly (400)', () => {
  const base = { expectedRevision: 1, hardRules: hardBody(), softRules: [], relationRules: [], protocolRules: [] };
  for (const bad of [
    { ...base, relationRules: [{ ...precedes(), extra: 1 }] }, { ...base, relationRules: [{ ...precedes(), clientRef: undefined }] },
    { ...base, relationRules: [{ ...precedes(), id: 'hard:rel-00000001' }] }, { ...base, relationRules: [{ ...precedes(), enabled: 'yes' }] },
    { ...base, relationRules: [{ ...precedes(), severity: 3 }] }, { ...base, relationRules: [{ ...precedes(), kind: undefined }] },
    { ...base, protocolRules: [{ ...protocol(), paths: 'CH_A' }] }, { ...base, protocolRules: [{ ...protocol(), paths: [['CH_A']] }] },
    { ...base, protocolRules: [{ ...protocol(), paths: [{ channels: ['CH_A'], extra: 1 }] }] }, { ...base, protocolRules: [{ ...protocol(), interfaceId: 1 }] },
    { ...base, protocolRules: [{ ...protocol(), clientRef: 'x'.repeat(41) }] }, { ...base, softRules: [{ ...scoped(), other: 1 }] },
  ]) assert.equal(code(() => parseRuleSetRequest(bad)), 'invalid_rule_set_request', JSON.stringify(bad).slice(0, 200));
  assert.equal(parseRuleSetRequest({ ...base, softRules: [{ ...scoped(), scope: undefined }] }).softRules[0].scope, null, 'omitted soft scope = global');
});

test('new built-in severities follow the v3.6 table', () => {
  const failed = (severity) => body({ hardRules: hardBody({ 'hard:transaction-failed': { severity } }) });
  const invalid = 'hard_rule_severity_invalid:hard:transaction-failed';
  for (const bad of [undefined, 'warning', { byCode: {} }, { default: 'warning' }, { byCode: {}, default: 'high' }, { byCode: { E1: 'high' }, default: 'info' },
    { byCode: { 'E 1': 'info' }, default: 'info' }, { byCode: { '': 'info' }, default: 'info' }, { byCode: { ['x'.repeat(65)]: 'info' }, default: 'info' },
    { byCode: { 'É': 'info' }, default: 'info' }, { byCode: {}, default: 'info', extra: 1 }, { byCode: [], default: 'info' },
    { byCode: Object.fromEntries(Array.from({ length: 201 }, (_, index) => [`C${index}`, 'info'])), default: 'info' }]) {
    assert.equal(code(() => plan(failed(bad))), invalid, JSON.stringify(bad)?.slice(0, 80));
  }
  const many = Object.fromEntries(Array.from({ length: 200 }, (_, index) => [`C${index}`, 'info']));
  assert.equal(code(() => plan(failed({ byCode: { ...many }, default: 'critical' }))), 'passed', '200 printable ASCII codes');
  const edited = plan(failed({ byCode: { 9: 'critical', '0100': 'warning', '~!': 'info' }, default: 'critical' }));
  assert.deepEqual(edited.changes, [{ type: 'hard_updated', id: 'hard:transaction-failed', displayName: '거래 실패',
    diff: { severity: [{ byCode: {}, default: 'warning' }, { byCode: { 9: 'critical', '0100': 'warning', '~!': 'info' }, default: 'critical' }] } }]);
  const unregistered = (severity) => body({ hardRules: hardBody({ 'hard:unregistered-interface': { severity } }) });
  for (const bad of [undefined, { byCode: {} }, 'high', null]) {
    assert.equal(code(() => plan(unregistered(bad))), 'hard_rule_severity_invalid:hard:unregistered-interface', String(bad));
  }
  assert.deepEqual(plan(unregistered('info')).changes.map((change) => change.type), ['hard_updated']);
  // v3 새 규칙의 위험도 검사는 소프트룰 검사 다음 자리(v3.6 표). v2 규칙은 그대로 앞자리.
  assert.equal(code(() => plan(body({ hardRules: hardBody({ 'hard:transaction-failed': { severity: 'x' } }), softRules: [scoped({ field: 'nope' })] }))), 'soft_rule_field_unknown:nope');
  assert.equal(code(() => plan(body({ hardRules: hardBody({ 'hard:transaction-failed': { severity: 'x' }, 'hard:process-status': { severity: 'x' } }) }))),
    'hard_rule_severity_invalid:hard:process-status');
});

test('soft rule scope: shape and immutability', () => {
  for (const bad of [{}, 'P2', [], { adapterType: 'HTTP' }, { processId: '' }, { processId: ' \\N ' }, { processId: 'P', other: 'x' }, { processId: 'P', adapterType: 3 },
    { processId: 'x'.repeat(513) }, { processId: 'a\u0000' }]) {
    assert.equal(code(() => plan(body({ softRules: [scoped({ scope: bad })] }))), 'soft_rule_scope_invalid:s1', JSON.stringify(bad));
  }
  assert.equal(code(() => plan(body({ softRules: [scoped({ scope: { processId: ' P ' } })] }))), 'passed', 'surrounding spaces are kept, not refused');
  const saved = plan(body({ softRules: [scoped()] }));
  assert.deepEqual(saved.snapshot.softRules[0].scope, { processId: 'P2', adapterType: 'HTTP', interfaceId: 'IF_A' });
  const state = { ...empty(), softRules: saved.snapshot.softRules };
  const existing = ({ createdAtRevision, updatedAtRevision, unit, ...rule }) => rule;
  const again = existing(saved.snapshot.softRules[0]);
  assert.equal(code(() => plan(body({ softRules: [{ ...again, scope: { processId: 'P3', adapterType: 'HTTP' } }] }), { state })), `soft_rule_scope_immutable:${again.id}`);
  assert.equal(code(() => plan(body({ softRules: [{ ...again, scope: null }] }), { state })), `soft_rule_scope_immutable:${again.id}`);
  // 표시용 interfaceId는 비교 밖이고 저장값을 유지한다.
  const renamed = plan(body({ softRules: [{ ...again, name: '새 이름', scope: { processId: 'P2', adapterType: 'HTTP', interfaceId: 'IF_Z' } }] }), { state });
  assert.deepEqual(renamed.snapshot.softRules[0].scope, { processId: 'P2', adapterType: 'HTTP', interfaceId: 'IF_A' });
  assert.deepEqual(renamed.changes.map((change) => change.type), ['soft_updated']);
  assert.equal(code(() => plan(body({ softRules: [again] }), { state })), 'rule_set_unchanged');
});

test('relation rule checks run in the v3.6 order', () => {
  const state = empty();
  assert.equal(code(() => plan(body({ relationRules: [precedes({ id: 'hard:rel-deadbeef', clientRef: undefined })] }))), 'relation_rule_unknown:hard:rel-deadbeef');
  assert.equal(code(() => plan(body({ relationRules: [precedes(), precedes({ to: { processId: 'P3' } })] }))), 'relation_rule_client_ref_duplicate');
  assert.equal(code(() => plan(body({ relationRules: [precedes({ kind: 'follows', from: 3 })] }))), 'relation_rule_kind_unknown:r1', 'kind before shape');
  for (const shape of [
    { from: { processId: 'P1', extra: 'x' } }, { from: null }, { to: null }, { to: { processId: '' } }, { match: { from: 'OUT', to: 'IN', key: 'message_id' } },
    { scope: {} }, { scope: { interfaceId: 'IF_A', processId: 'P' } }, { scope: { adapterType: '\\N' } }, { scope: 'IF_A' }, { to: { processId: 'P1' } },
  ]) assert.equal(code(() => plan(body({ relationRules: [precedes(shape)] }))), 'relation_rule_shape_invalid:r1', JSON.stringify(shape));
  // from = to(자기 자신이 선행, rule-set.md v3.6): 순서 kind 모두 모양 오류이고 위험도·조건 검사보다 앞(같은 표 행).
  assert.equal(code(() => plan(body({ relationRules: [handoff({ to: { processId: 'P1' }, severity: 'high' })] }))), 'relation_rule_shape_invalid:r2');
  assert.equal(code(() => plan(body({ relationRules: [precedes({ kind: 'allowed_branch', to: { processId: 'P1' } })] }))), 'relation_rule_shape_invalid:r1');
  assert.equal(code(() => plan(body({ relationRules: [precedes({ to: { processId: 'P1 ' } })] }))), 'passed', 'raw strings: a trailing space is another step');
  for (const shape of [{ to: { processId: 'P2' } }, { scope: null }, { scope: { interfaceId: 'IF_A', adapterType: 'HTTP' } }, { scope: {} }]) {
    assert.equal(code(() => plan(body({ relationRules: [required(shape)] }))), 'relation_rule_shape_invalid:r3', JSON.stringify(shape));
  }
  for (const shape of [{ match: null }, { match: { from: 'IN', to: 'OUT', key: 'message_id' } }, { match: { from: 'OUT', to: 'IN', key: 'message_id', extra: 1 } }]) {
    assert.equal(code(() => plan(body({ relationRules: [handoff(shape)] }))), 'relation_rule_shape_invalid:r2', JSON.stringify(shape));
  }
  assert.equal(code(() => plan(body({ relationRules: [precedes({ scope: null }), precedes({ clientRef: 'r9', scope: { interfaceId: 'IF_B' } })] }))),
    'relation_rule_condition_duplicate:r9', 'display interfaceId does not make a different rule');
  assert.equal(code(() => plan(body({ relationRules: [precedes({ severity: 'high' })] }))), 'relation_rule_severity_invalid:r1');
  const many = Array.from({ length: 201 }, (_, index) => precedes({ clientRef: `r${index}`, from: { processId: `Q${index}` } }));
  assert.equal(code(() => plan(body({ relationRules: many }))), 'relation_rule_limit_exceeded');
  assert.equal(code(() => plan(body({ relationRules: many.slice(0, 200) }))), 'passed');
  const saved = plan(body({ relationRules: [precedes({ source: { jobId: 'j' } }), required()] }), { state });
  assert.ok(saved.snapshot.relationRules.every((rule) => /^hard:rel-[0-9a-f]{8}$/.test(rule.id)), 'server issues hard:rel-<8hex>');
  const stored = { ...state, relationRules: saved.snapshot.relationRules };
  const asSent = (rule, extra = {}) => ({ id: rule.id, kind: rule.kind, scope: rule.scope, from: rule.from, to: rule.to, match: rule.match, severity: rule.severity, enabled: rule.enabled, ...extra });
  const [first, second] = saved.snapshot.relationRules;
  assert.equal(code(() => plan(body({ relationRules: [asSent(first, { from: { processId: 'P9' } }), asSent(second)] }), { state: stored })), `relation_rule_condition_immutable:${first.id}`);
  assert.equal(code(() => plan(body({ relationRules: [asSent(first), asSent(second), asSent(first)] }), { state: stored })), `relation_rule_duplicate:${first.id}`);
  // 재승인(source만 다름) = 무변경. 표시용 scope.interfaceId·source는 서버 저장값 유지.
  assert.equal(code(() => plan(body({ relationRules: [asSent(first, { source: { jobId: 'other' } }), asSent(second)] }), { state: stored })), 'rule_set_unchanged');
  const sourceKept = plan(body({ relationRules: [asSent(first, { enabled: false, severity: 'info', source: { jobId: 'other' } }), asSent(second)] }), { state: stored });
  const kept = sourceKept.snapshot.relationRules.find((rule) => rule.id === first.id);
  assert.deepEqual([kept.source, kept.updatedAtRevision, kept.enabled, kept.severity], [first.source, 9, false, 'info']);
  assert.deepEqual(sourceKept.changes.map((change) => change.type).sort(), ['relation_disabled', 'relation_updated']);
  assert.deepEqual(sourceKept.changes.find((change) => change.type === 'relation_updated').diff, { severity: [first.severity, 'info'] });
  assert.equal(sourceKept.summary.relationDisabled + sourceKept.summary.relationUpdated, 2);
  const removed = plan(body({ relationRules: [asSent(second)] }), { state: stored });
  assert.deepEqual(removed.changes, [{ type: 'relation_removed', id: first.id, kind: first.kind, interfaceId: 'IF_A', from: 'P1', to: 'P2' }]);
});

test('protocol rule checks run in the v3.6 order and paths are stored by pathKey', () => {
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ id: 'hard:proto-deadbeef', clientRef: undefined })] }))), 'protocol_rule_unknown:hard:proto-deadbeef');
  assert.equal(code(() => plan(body({ protocolRules: [protocol(), protocol({ interfaceId: 'IF_B' })] }))), 'protocol_rule_client_ref_duplicate');
  for (const bad of ['', ' ', '\\N', 'x'.repeat(513), '\ud800']) {
    assert.equal(code(() => plan(body({ protocolRules: [protocol({ interfaceId: bad })] }))), 'protocol_rule_interface_invalid:p1', JSON.stringify(bad));
  }
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ interfaceId: 'x'.repeat(512) })] }))), 'passed', '512 code points');
  assert.equal(code(() => plan(body({ protocolRules: [protocol(), protocol({ clientRef: 'p2' })] }))), 'protocol_rule_interface_duplicate:p2');
  for (const channels of [[], 'CH', [1], ['CH', ''], ['\\N'], Array.from({ length: 65 }, (_, index) => `C${index}`)]) {
    assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: [{ channels }] })] }))), 'protocol_path_invalid:p1', JSON.stringify(channels).slice(0, 40));
  }
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: [{ channels: Array.from({ length: 64 }, (_, index) => `C${index}`) }] })] }))), 'passed', '64 steps');
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: [{ channels: ['A'] }, { channels: ['A'] }] })] }))), 'protocol_path_duplicate:p1');
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: [{ channels: ['A'] }, { channels: ['A '] }] })] }))), 'passed', 'raw strings, no trim');
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: [] })] }))), 'protocol_path_limit_exceeded:p1');
  const paths65 = Array.from({ length: 65 }, (_, index) => ({ channels: [`C${index}`] }));
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: paths65 })] }))), 'protocol_path_limit_exceeded:p1');
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ paths: paths65.slice(0, 64) })] }))), 'passed');
  assert.equal(code(() => plan(body({ protocolRules: [protocol({ severity: 'high' })] }))), 'protocol_rule_severity_invalid:p1');
  const many = Array.from({ length: 501 }, (_, index) => protocol({ clientRef: `p${index}`, interfaceId: `IF_${index}` }));
  assert.equal(code(() => plan(body({ protocolRules: many }))), 'protocol_rule_limit_exceeded');
  assert.equal(code(() => plan(body({ protocolRules: many.slice(0, 500) }))), 'passed');

  const saved = plan(body({ protocolRules: [protocol({ source: { jobId: 'j' }, paths: [['A:'], ['A%3A']].map((channels) => ({ channels, source: { kind: 'draft' } })) })] }));
  const [entry] = saved.snapshot.protocolRules;
  assert.match(entry.id, /^hard:proto-[0-9a-f]{8}$/);
  assert.deepEqual(entry.paths.map((item) => item.channels), [['A%3A'], ['A:']], 'stored in pathKey code point order (h)');
  assert.deepEqual(saved.changes, [{ type: 'protocol_added', id: entry.id, clientRef: 'p1', interfaceId: 'IF_A', paths: 2 }]);
  const state = { ...empty(), protocolRules: saved.snapshot.protocolRules };
  const asSent = (rule, extra = {}) => ({ id: rule.id, interfaceId: rule.interfaceId, paths: rule.paths.map(({ channels }) => ({ channels })), severity: rule.severity, enabled: rule.enabled, ...extra });
  assert.equal(code(() => plan(body({ protocolRules: [asSent(entry, { interfaceId: 'IF_B' })] }), { state })), `protocol_rule_interface_immutable:${entry.id}`);
  assert.equal(code(() => plan(body({ protocolRules: [asSent(entry, { paths: [...asSent(entry).paths].reverse() })] }), { state })), 'rule_set_unchanged', 'path order is not a change');
  const edited = plan(body({ protocolRules: [asSent(entry, { severity: 'warning', enabled: false, paths: [{ channels: ['A:'] }, { channels: ['B', 'C'], source: { kind: 'alert' } }] })] }), { state });
  assert.deepEqual(edited.changes, [
    { type: 'protocol_disabled', id: entry.id, interfaceId: 'IF_A' },
    { type: 'protocol_updated', id: entry.id, interfaceId: 'IF_A', diff: { pathsAdded: [['B', 'C']], pathsRemoved: [['A%3A']], severity: ['critical', 'warning'] } },
  ]);
  assert.deepEqual(edited.snapshot.protocolRules[0].paths, [{ channels: ['A:'], source: { kind: 'draft' } }, { channels: ['B', 'C'], source: { kind: 'alert' } }],
    'kept paths keep their stored source');
  assert.deepEqual(plan(body(), { state }).changes, [{ type: 'protocol_removed', id: entry.id, interfaceId: 'IF_A' }]);
});

test('preview does not issue ids; save issues prefixed ids that avoid taken ones', () => {
  const preview = plan(body({ relationRules: [precedes()], protocolRules: [protocol()], softRules: [scoped()] }), { preview: true });
  assert.deepEqual(preview.changes.map((change) => [change.type, change.id]), [['soft_added', null], ['relation_added', null], ['protocol_added', null]]);
  const ids = [];
  const once = planRuleSetChange({ request: body({ relationRules: [precedes()] }), current: empty(), restore: null, activeModelCount: 0, nextRevision: 2,
    assignId: (prefix, taken) => { ids.push([prefix, taken.size]); return `${prefix}0badc0de`; } });
  assert.deepEqual(ids, [['hard:rel-', 0]]);
  assert.equal(once.snapshot.relationRules[0].id, 'hard:rel-0badc0de');
});

test('warnings: handoff without an enabled precedes pair, unregistered rule without entries', () => {
  const pairless = plan(body({ relationRules: [handoff()], protocolRules: [protocol()] }), { preview: true });
  assert.deepEqual(pairless.warnings, [{ code: 'relation_handoff_without_precedes', ruleId: null, clientRef: 'r2', message: '짝 선행 규칙이 없어 선행이 다르면 인계를 확인하지 않습니다.' }]);
  assert.deepEqual(plan(body({ relationRules: [handoff(), precedes({ scope: null })], protocolRules: [protocol()] })).warnings, [], 'precedes with null scope pairs');
  assert.equal(plan(body({ relationRules: [handoff(), precedes({ enabled: false })], protocolRules: [protocol()] })).warnings.length, 1, 'a disabled precedes does not pair');
  assert.equal(plan(body({ relationRules: [handoff(), precedes({ scope: { adapterType: 'TCP' } })], protocolRules: [protocol()] })).warnings.length, 1);
  const saved = plan(body({ relationRules: [handoff()], protocolRules: [protocol()] }));
  assert.match(saved.warnings[0].ruleId, /^hard:rel-[0-9a-f]{8}$/);
  const unregisteredOff = { ...empty(), hardRules: projectHardRules(HARD_RULES.map((rule) => ({ ...rule, enabled: rule.id !== 'hard:unregistered-interface' }))) };
  const noEntries = plan(body(), { state: unregisteredOff });
  assert.deepEqual(noEntries.warnings.map((warning) => [warning.code, warning.ruleId]), [['protocol_unregistered_without_entries', 'hard:unregistered-interface']]);
  assert.equal(plan(body({ protocolRules: [protocol({ enabled: false })] })).warnings.length, 1, 'all entries off = feature off');
  assert.deepEqual(plan(body({ hardRules: hardBody({ 'hard:unregistered-interface': { enabled: false } }), confirmHardRuleDisable: true })).warnings, []);
});

test('detection_methods_empty ignores transaction-level rules, scoped soft rules, relation and protocol rules', () => {
  const only = (ids) => hardBody(Object.fromEntries(HARD_RULES.map((rule) => [rule.id, { enabled: ids.includes(rule.id) }])));
  const extras = { relationRules: [precedes()], protocolRules: [protocol()], confirmHardRuleDisable: true };
  assert.equal(code(() => plan(body({ hardRules: only(['hard:transaction-failed', 'hard:unregistered-interface']), ...extras }))), 'detection_methods_empty');
  assert.equal(code(() => plan(body({ hardRules: only([]), softRules: [scoped()], ...extras }))), 'detection_methods_empty');
  assert.equal(code(() => plan(body({ hardRules: only([]), softRules: [scoped({ scope: null })], ...extras }))), 'passed');
  assert.equal(code(() => plan(body({ hardRules: only(['hard:negative-duration']), ...extras }))), 'passed');
  assert.equal(code(() => plan(body({ hardRules: only([]), ...extras }), { models: 1 })), 'passed');
});

test('rule_set_unchanged compares v3 content even when the current row is v2', () => {
  // v2 행: 관계·프로토콜 없음, scope 없음, 새 내장 규칙은 스냅샷에 없음 = 꺼짐.
  const v2Stored = HARD_RULES.slice(0, 4).map((rule) => ({ ...rule, enabled: true }));
  const v2State = { hardRules: projectHardRules(v2Stored), softRules: [{ id: 'soft:elapsed_ms-1', name: 'a', field: 'elapsed_ms', operator: 'gt', threshold: 1, unit: 'ms',
    severity: 'info', enabled: true, createdAtRevision: 1, updatedAtRevision: 1, scope: null }], relationRules: [], protocolRules: [] };
  const same = body({ hardRules: hardBody({ 'hard:transaction-failed': { enabled: false }, 'hard:unregistered-interface': { enabled: false } }),
    softRules: [{ id: 'soft:elapsed_ms-1', name: 'a', field: 'elapsed_ms', operator: 'gt', threshold: 1, severity: 'info', enabled: true }] });
  assert.equal(code(() => plan(same, { state: v2State })), 'rule_set_unchanged');
  const first = plan(body({ hardRules: hardBody({ 'hard:transaction-failed': { enabled: false } }),
    softRules: [{ id: 'soft:elapsed_ms-1', name: 'a', field: 'elapsed_ms', operator: 'gt', threshold: 1, severity: 'info', enabled: true }] }), { state: v2State });
  assert.deepEqual(first.changes.map((change) => [change.type, change.id]), [['hard_enabled', 'hard:unregistered-interface']]);
  assert.notEqual(first.contentHash, contentHash(v2State, 2), 'saved as v3');
  assert.equal(first.contentHash, contentHash(first.snapshot, 3));
});

test('restore: v3 snapshot recreates relation and protocol items with the same id; v1/v2 snapshot means none', () => {
  const created = plan(body({ relationRules: [precedes()], protocolRules: [protocol()], softRules: [scoped()] }));
  const snapshot = created.snapshot;
  const asRestore = (overrides = {}) => body({
    restoreFromRevision: 7,
    hardRules: snapshot.hardRules.map(({ id, enabled, severity, editable }) => ({ id, enabled, ...(editable.includes('severity') ? { severity } : {}) })),
    softRules: snapshot.softRules.map(({ id, name, field, operator, threshold, severity, enabled, scope }) => ({ id, name, field, operator, threshold, severity, enabled, scope })),
    relationRules: snapshot.relationRules.map(({ id, kind, scope, from, to, match, severity, enabled }) => ({ id, kind, scope, from, to, match, severity, enabled })),
    protocolRules: snapshot.protocolRules.map(({ id, interfaceId, paths, severity, enabled }) => ({ id, interfaceId, paths: paths.map(({ channels }) => ({ channels })), severity, enabled })),
    ...overrides,
  });
  const restored = plan(asRestore(), { restore: snapshot });
  assert.deepEqual(restored.changes.map((change) => [change.type, change.id, change.restored]), [
    ['soft_added', snapshot.softRules[0].id, true], ['relation_added', snapshot.relationRules[0].id, true], ['protocol_added', snapshot.protocolRules[0].id, true],
  ]);
  assert.equal(restored.contentHash, created.contentHash, 'same v3 content, same hash');
  assert.deepEqual(restored.snapshot.relationRules[0].createdAtRevision, snapshot.relationRules[0].createdAtRevision);
  assert.deepEqual(restored.snapshot.protocolRules[0].paths, snapshot.protocolRules[0].paths, 'path sources come from the snapshot');
  const tweakedPaths = asRestore();
  tweakedPaths.protocolRules[0].paths = tweakedPaths.protocolRules[0].paths.slice(1);
  assert.equal(code(() => plan(tweakedPaths, { restore: snapshot })), 'restore_payload_mismatch');
  const tweakedRelation = asRestore();
  tweakedRelation.relationRules[0].severity = 'info';
  assert.equal(code(() => plan(tweakedRelation, { restore: snapshot })), 'restore_payload_mismatch');
  const tweakedScope = asRestore();
  tweakedScope.softRules[0].scope = null;
  assert.equal(code(() => plan(tweakedScope, { restore: snapshot })), 'restore_payload_mismatch');
  const reordered = asRestore();
  reordered.protocolRules[0].paths.reverse();
  reordered.relationRules[0].scope = { adapterType: 'HTTP' };
  assert.equal(code(() => plan(reordered, { restore: snapshot })), 'passed', 'path order and display interfaceId are outside the comparison');
  assert.equal(code(() => plan(asRestore({ restoreFromRevision: null }))), `soft_rule_unknown:${snapshot.softRules[0].id}`, 'exception is restore only');
  assert.equal(code(() => plan(asRestore({ restoreFromRevision: null, softRules: [] }))), `relation_rule_unknown:${snapshot.relationRules[0].id}`);
  assert.equal(code(() => plan(asRestore({ restoreFromRevision: null, softRules: [], relationRules: [] }))), `protocol_rule_unknown:${snapshot.protocolRules[0].id}`);

  // v2 출처: 관계·프로토콜·scope 없음으로 읽는다. 현재 항목은 모두 삭제되고, 새 내장 규칙 2개는 꺼짐으로 채운다.
  const v2Source = { hardRules: HARD_RULES.slice(0, 4).map((rule) => ({ ...rule, enabled: true })), softRules: [], relationRules: [], protocolRules: [] };
  const current = { ...empty(), relationRules: snapshot.relationRules, protocolRules: snapshot.protocolRules };
  const v2Body = (overrides = {}) => body({ restoreFromRevision: 3, confirmHardRuleDisable: true, hardRules: hardBody().slice(0, 4), ...overrides });
  assert.equal(code(() => plan(v2Body({ relationRules: [precedes()] }), { state: current, restore: v2Source })), 'restore_payload_mismatch');
  const back = plan(v2Body(), { state: current, restore: v2Source });
  assert.deepEqual(back.changes.map((change) => change.type), ['hard_disabled', 'hard_disabled', 'relation_removed', 'protocol_removed']);
  assert.deepEqual(back.snapshot.hardRules.slice(4).map((rule) => [rule.id, rule.enabled, rule.severity]),
    [['hard:transaction-failed', false, { byCode: {}, default: 'warning' }], ['hard:unregistered-interface', false, 'critical']]);
  assert.equal(code(() => plan(v2Body({ confirmHardRuleDisable: false }), { state: current, restore: v2Source })), 'hard_rule_disable_confirmation_required');
});

test('stored v3 severities are projected; other methodVersions use the built-in default', () => {
  const custom = { byCode: { 8888: 'critical' }, default: 'info' };
  const projected = projectHardRules([
    { id: 'hard:transaction-failed', enabled: true, methodVersion: 'transaction-failed-v1', severity: custom },
    { id: 'hard:unregistered-interface', enabled: true, methodVersion: 'unregistered-interface-v1', severity: 'info' },
  ]);
  assert.deepEqual(projected.slice(4).map((rule) => rule.severity), [custom, 'info']);
  const unknownVersion = projectHardRules([{ id: 'hard:transaction-failed', enabled: true, methodVersion: 'transaction-failed-v0', severity: custom }]);
  assert.deepEqual(unknownVersion[4].severity, { byCode: {}, default: 'warning' });
  assert.deepEqual(projectHardRules([{ id: 'hard:process-status', enabled: true, methodVersion: 'process-status-hr-v2', severity: STATUS_DEFAULT }])[0].severity, STATUS_DEFAULT);
});
