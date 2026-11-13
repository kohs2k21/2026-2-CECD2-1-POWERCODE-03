// 프로토콜 초안 읽기(protocol-settings-ui.md 2절) 단위 시험: Trainer 산출물 모양 검사(엄격), 키 없음 = protocol_drafts_unavailable,
// 버전·모양 오류 503, 인터페이스 요약·상세. 고정값 fixtures/protocol-path-drafts.json은 Trainer PR 코드(protocol_paths.py)를 합성 거래로 실행한 출력이다.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { RuleSetError } from '../dist/ruleset/ruleSetContract.js';
import { parseProtocolDraftQuery, parseProtocolDrafts, readProtocolDrafts } from '../dist/ruleset/protocolDrafts.js';
import { RELATION_CONTRACT_VERSION } from '../dist/ruleset/ruleCandidates.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const drafts = JSON.parse(await readFile(path.join(here, 'fixtures/protocol-path-drafts.json'), 'utf8'));
const JOB = '11111111-2222-4333-8444-555555555555';
const job = (extra = {}) => ({ id: JOB, status: 'succeeded', errorCode: null, createdAt: '2026-11-13T01:00:00.000000Z', finishedAt: '2026-11-13T01:01:00.000000Z',
  inputSpec: { kind: 'recommend_relations', snapshotId: 'snap-1' }, ...extra });
const result = (extra = {}) => ({ relationContractVersion: RELATION_CONTRACT_VERSION, snapshotId: 'snap-1', sourceId: 'src-1',
  period: { from: '2026-10-01T00:00:00Z', until: '2026-11-01T00:00:00Z' }, candidates: [], protocolPathDrafts: structuredClone(drafts), ...extra });
const fakeDb = ({ latest = job(), succeeded = { ...job(), result: result() } } = {}) => ({
  query: async (sql) => ({ rows: sql.includes("status = 'succeeded'") ? (succeeded ? [succeeded] : []) : (latest ? [latest] : []) }),
});
const mutate = (fn) => { const copy = structuredClone(drafts); fn(copy); return copy; };
const code = (fn) => { try { fn(); } catch (error) { assert.ok(error instanceof RuleSetError, String(error)); return error.code; } return 'passed'; };

test('the Trainer output parses unchanged', () => {
  const parsed = parseProtocolDrafts(drafts);
  assert.deepEqual(parsed.interfaces.map((item) => [item.interfaceId, item.status, item.paths.length]),
    [['IF_SYN_NOTICE', 'insufficient_evidence', 1], ['IF_SYN_ORDER', 'draft', 5], ['IF_SYN_PAY', 'draft', 2]]);
  const order = parsed.interfaces[1];
  assert.deepEqual(order.paths[0], { channels: ['CH_GW', 'CH_ORDER', 'CH_DB'], pathKey: 'CH_GW:CH_ORDER:CH_DB', transactions: 120,
    ratio: drafts.interfaces[1].paths[0].ratio, rare: false, fromBranching: 0, exampleTransactionIds: ['TX000001', 'TX000002', 'TX000003', 'TX000004', 'TX000005'] });
  assert.equal(order.paths.at(-1).rare, true);
  assert.deepEqual(parsed.excludedReasons, drafts.excludedReasons);
  assert.equal(parsed.parameters.maxOutputPaths, 64);
  assert.equal(parsed.interfaces[2].paths[0].pathKey, 'CH_GW:CH_PAY:CH_BANK%3A01:CH_PAY', 'escaped pathKey is checked, not split');
});

test('shape errors are 503 protocol_drafts_invalid, version errors 503 protocol_drafts_version_unsupported', () => {
  assert.equal(code(() => parseProtocolDrafts(mutate((d) => { d.version = 2; }))), 'protocol_drafts_version_unsupported');
  for (const [label, fn] of [
    ['not an object', (d) => { d.interfaces = null; }],
    ['pathKey not rebuilt from channels', (d) => { d.interfaces[1].paths[0].pathKey = 'CH_GW:CH_ORDER'; }],
    ['unknown reason', (d) => { d.excludedReasons.made_up = 1; }],
    ['negative count', (d) => { d.interfaces[0].transactions = -1; }],
    ['status', (d) => { d.interfaces[0].status = 'candidate'; }],
    ['empty channels', (d) => { d.interfaces[0].paths[0].channels = []; }],
    ['non-string channel', (d) => { d.interfaces[0].paths[0].channels = [1]; }],
    ['ratio above 1', (d) => { d.interfaces[0].paths[0].ratio = 1.5; }],
    ['rare not boolean', (d) => { d.interfaces[0].paths[0].rare = 'no'; }],
    ['example refs', (d) => { d.interfaces[0].paths[0].exampleRefs = [{ refs: [1], steps: [] }]; }],
    ['endpoints', (d) => { delete d.interfaces[0].endpoints.notCompared; }],
    ['omittedPaths', (d) => { d.interfaces[0].omittedPaths = { paths: 0 }; }],
    ['duplicate interface', (d) => { d.interfaces.push(d.interfaces[0]); }],
    ['duplicate path', (d) => { d.interfaces[1].paths.push(d.interfaces[1].paths[0]); }],
    ['too many paths', (d) => { d.parameters.maxOutputPaths = 1; }],
    ['steps above the cap', (d) => { d.parameters.maxPathSteps = 2; }],
    ['transactionIdMissingRows', (d) => { d.transactionIdMissingRows = '1'; }],
  ]) assert.equal(code(() => parseProtocolDrafts(mutate(fn))), 'protocol_drafts_invalid', label);
});

test('read: summary list, one interface on request, explicit unavailable states', async () => {
  const list = await readProtocolDrafts(fakeDb(), null);
  assert.equal(list.state, 'available');
  assert.equal('interface' in list, false);
  assert.deepEqual(list.interfaces.map((item) => [item.interfaceId, item.listedPaths, 'paths' in item]), [['IF_SYN_NOTICE', 1, false], ['IF_SYN_ORDER', 5, false], ['IF_SYN_PAY', 2, false]]);
  assert.deepEqual([list.job.id, list.snapshotId, list.period.from], [JOB, 'snap-1', '2026-10-01T00:00:00Z']);
  const one = await readProtocolDrafts(fakeDb(), 'IF_SYN_PAY');
  assert.deepEqual(one.interface.paths.map((item) => item.channels), [['CH_GW', 'CH_PAY', 'CH_BANK:01', 'CH_PAY'], ['CH_GW', 'CH_PAY%', 'CH_BANK:01', 'CH_PAY']]);
  assert.equal((await readProtocolDrafts(fakeDb(), 'IF_NOT_SEEN')).interface, null, 'not observed in the period: null, not an error');
  const { protocolPathDrafts: _drop, ...older } = result();
  assert.deepEqual(await readProtocolDrafts(fakeDb({ succeeded: { ...job(), result: older } }), null),
    { state: 'protocol_drafts_unavailable', reason: 'not_in_result', latestJob: list.latestJob, job: list.job }, 'older Trainer result: explicit state, no mock');
  assert.deepEqual(await readProtocolDrafts(fakeDb({ latest: null, succeeded: null }), null), { state: 'protocol_drafts_unavailable', reason: 'no_result', latestJob: null });
  await assert.rejects(readProtocolDrafts(fakeDb({ succeeded: { ...job(), result: result({ relationContractVersion: 'x' }) } }), null), { code: 'rule_candidate_contract_unsupported' });
  await assert.rejects(readProtocolDrafts(fakeDb({ succeeded: { ...job(), result: result({ protocolPathDrafts: null }) } }), null), { code: 'protocol_drafts_invalid' });
});

test('query parsing', () => {
  assert.equal(parseProtocolDraftQuery({}), null);
  assert.equal(parseProtocolDraftQuery({ interfaceId: 'IF A' }), 'IF A');
  assert.equal(code(() => parseProtocolDraftQuery({ other: '1' })), 'unknown_protocol_draft_filter');
  for (const bad of ['', 'x'.repeat(513), ['a']]) assert.equal(code(() => parseProtocolDraftQuery({ interfaceId: bad })), 'invalid_interface_id');
});
