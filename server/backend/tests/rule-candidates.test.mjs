// 규칙 추천(rule-candidates-ui.md) 단위 시험: Trainer 결과 해석·인터페이스 선택·반례 거래 id·판정 연결·짝 선행·저장 초안·요청 검사.
// 가짜 질의(SQL 문자열로 분기)만 쓴다. DB 경로(거래별 판정 고르기 SQL·enum 없음 503·v03 CHECK 503·관리자 권한)는 rule-candidates.integration.mjs.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CANDIDATE_ID, RELATION_CONTRACT_VERSION, approvalState, checkResult, parseCandidateJobRequest, parseCandidateListQuery,
  enqueueCandidateJob, readCandidateDetail, readCandidateList, relationItem, relationJobUnavailable, relationRuleDraft, softRuleDraft, thresholdItem,
  transactionOfRef,
} from '../dist/ruleset/ruleCandidates.js';
import { CONTENT_HASH_VERSION, RuleSetError } from '../dist/ruleset/ruleSetContract.js';
import crypto from 'node:crypto';
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');

const JOB = '8cc86810-673b-4886-87b1-e026856fe636';
const OLD_JOB = '11111111-2222-4333-8444-555555555555';
const SOURCE = '5452b033-42f5-436e-bac8-fc2fb6e4cf05';
const counts = (applicable, satisfied, undetermined = 0) => ({ applicable, satisfied, violated: applicable - satisfied, undetermined });
const candidate = (kind, iface, from, to, extra = {}) => ({
  kind, category: 'hard', status: 'candidate', approvable: true,
  scope: kind === 'required_step' ? { interfaceId: iface } : { adapterType: 'HTTP' },
  from: { processId: from }, to: to === null ? null : { processId: to },
  match: kind === 'message_handoff' ? { from: 'OUT', to: 'IN', key: 'message_id' } : null,
  patternEdgeId: to === null ? null : `${kind === 'message_handoff' ? 'hands_off' : 'precedes'}:pattern_step:${iface}:${from}:HTTP:pattern_step:${iface}:${to}:HTTP`,
  counts: counts(100, 99), satisfactionRate: 0.99, violationReasons: { other_predecessor: 1 }, violationsByTransactionStatus: { S: 1 },
  undeterminedReasons: {}, counterexampleRefs: [], fromAdapterTypes: [{ adapterType: 'HTTP', transactions: 99 }], evaluatedAt: to === null ? 'transaction' : 'successor',
  display: { interfaceId: iface, toStep: to === null ? null : `pattern_step:${iface}:${to}:HTTP` }, attrs: { nonStandardProcessId: false },
  source: { jobId: JOB, snapshotId: 's', patternEdgeId: null, patternNodeIds: [], counts: counts(100, 99), period: { from: null, until: null, timeBasis: null } },
  ...extra,
});
const threshold = (processId, interfaceIds, extra = {}) => ({
  field: 'elapsed_ms', category: 'soft', outputOnly: true, status: 'candidate', reason: null, scope: { processId, adapterType: 'HTTP' },
  display: { interfaceIds, patternStepIds: [] }, scopeSharedAcrossInterfaces: interfaceIds.length > 1, operator: 'gt', threshold: '812.5', displayThreshold: '812.5',
  unit: 'ms', source: 'data_p95', evidence: { rows: 120, referenceRows: 110, n: 100, min: 10, max: 900, p95: 812.5, quantileMethod: 'linear',
    exclusionRule: 'x', endTimeMissingReferenceRows: 10, unavailableReferenceRows: 0, note: 'n' },
  limitation: 'elapsed_ms = END_TIME - START_TIME. 끝나지 않고 멈춘 단계는 잡지 못한다.', nonStandardProcessId: false, ...extra,
});
const node = (iface, pid) => ({ id: `pattern_step:${iface}:${pid}:HTTP`, kind: 'pattern_step', attrs: { interfaceId: iface, processId: pid, adapterType: 'HTTP', transactions: 100, nonStandardProcessId: false } });
const result = () => ({
  relationContractVersion: RELATION_CONTRACT_VERSION, experimental: true, snapshotId: 'snap-1', sourceId: SOURCE,
  period: { from: '2026-10-01T00:00:00Z', until: '2026-11-01T00:00:00Z', timeBasis: 'persisted_at' },
  parameters: { minApplicable: 30, candidatePercent: 99, reviewPercent: 95, maxCounterexamples: 5, excludeProcessIds: [], handoffMismatchSuffixPatterns: ['-0[0-9]\\Z'] },
  data: { interfaceTransactions: { 'IF-A': 300, 'IF-B': 500, 'IF-C': 50 } },
  graph: { graph: { layer: 'pattern', id: 'snap-1', generatedAt: '2026-11-12T12:00:00.000Z' },
    nodes: [node('IF-A', 'P1'), node('IF-A', 'P2'), node('IF-B', 'P1')],
    edges: [{ id: 'precedes:a', kind: 'precedes', from: node('IF-A', 'P1').id, to: node('IF-A', 'P2').id, verification: null,
      evidence: { ...counts(100, 99), satisfactionRate: 0.99, status: 'candidate', observedTransactions: 100, violationReasons: {} } },
    { id: 'precedes:cross', kind: 'precedes', from: node('IF-A', 'P1').id, to: node('IF-B', 'P1').id, evidence: {} }] },
  candidates: [
    candidate('required_step', 'IF-A', 'P1', null, { counterexampleRefs: [{ refs: ['transaction:TX%3A9'], reason: 'step_absent', transactionStatus: 'F' }] }),
    candidate('precedes', 'IF-A', 'P1', 'P2', { status: 'not_candidate', approvable: false, listedForPairing: true }),
    candidate('message_handoff', 'IF-A', 'P1', 'P2', { status: 'review_needed', pairApprovable: false,
      pairedPrecedesEdgeId: 'precedes:pattern_step:IF-A:P1:HTTP:pattern_step:IF-A:P2:HTTP',
      counterexampleRefs: [{ refs: ['transaction:TX-1', 'process:TX-1:P2', 'process:TX-1:P1'], reason: 'message_id_mismatch', transactionStatus: 'S' },
        { refs: ['transaction:TX-2'], reason: 'message_id_mismatch', transactionStatus: 'S' }, { refs: ['bogus'], reason: 'x' }] }),
    candidate('required_step', 'IF-B', 'P1', null, { status: 'insufficient_evidence', approvable: false }),
  ],
  relationNotes: [],
  thresholdCandidates: [threshold('P1', ['IF-A', 'IF-B']), threshold('P2', ['IF-A'], { status: 'insufficient_evidence', threshold: null, displayThreshold: null, operator: null }),
    threshold('P9', [])],
});
const job = (id, extra = {}) => ({ id, status: 'succeeded', errorCode: null, createdAt: '2026-11-12T12:00:00.000000Z', finishedAt: '2026-11-12T12:01:00.000000Z',
  inputSpec: { kind: 'recommend_relations', snapshotId: 'snap-1' }, ...extra });
/** SQL 문자열로 분기하는 가짜 질의. */
const fakeDb = ({ latest = job(JOB), succeeded = { ...job(JOB), result: result() }, byId = {}, decisions = [] } = {}) => {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (sql.includes('FROM data_processes')) return { rows: decisions };
      if (sql.includes('WHERE id = $1')) return { rows: byId[params[0]] ? [byId[params[0]]] : [] };
      if (sql.includes("status = 'succeeded'")) return { rows: succeeded ? [succeeded] : [] };
      return { rows: latest ? [latest] : [] };
    },
  };
};
const rejects = async (promise, code, status) => {
  await assert.rejects(promise, (error) => error instanceof RuleSetError && error.code === code && (status === undefined || error.status === status));
};

test('approval is off until the API saves hash v3 (Detector relation-rules.md 8)', () => {
  assert.equal(CONTENT_HASH_VERSION, 2, 'this branch keeps v2 until Detector v3 deploys');
  assert.deepEqual(approvalState(), { available: false, reason: 'rule_set_hash_v3_pending', contentHashVersion: 2 });
  assert.deepEqual(approvalState(3), { available: true, reason: null, contentHashVersion: 3 });
});

test('result head and items are checked, not coerced', () => {
  assert.throws(() => checkResult({ ...result(), relationContractVersion: 'relation-candidates-experimental-1' }), { code: 'rule_candidate_contract_unsupported' });
  assert.throws(() => checkResult({ ...result(), candidates: null }), { code: 'rule_candidate_result_invalid' });
  assert.throws(() => relationItem(JOB, 0, { ...candidate('precedes', 'IF-A', 'P1', 'P2'), kind: 'allowed_branch' }), { code: 'rule_candidate_result_invalid' });
  assert.throws(() => relationItem(JOB, 0, { ...candidate('precedes', 'IF-A', 'P1', 'P2'), counts: { applicable: 1 } }), { code: 'rule_candidate_result_invalid' });
  assert.throws(() => relationItem(JOB, 0, { ...candidate('precedes', 'IF-A', 'P1', null) }), { code: 'rule_candidate_result_invalid' }, 'precedes needs to');
  assert.throws(() => relationItem(JOB, 0, { ...candidate('precedes', 'IF-A', 'P1', 'P2'), satisfactionRate: 1.5 }), { code: 'rule_candidate_result_invalid' });
  assert.throws(() => thresholdItem(JOB, 0, { ...threshold('P1', ['IF-A']), evidence: { n: -1 } }), { code: 'rule_candidate_result_invalid' });
  const handoff = relationItem(JOB, 2, result().candidates[2]);
  assert.deepEqual([handoff.candidateId, handoff.pairApprovable, handoff.interfaceId, handoff.listedForPairing], [`${JOB}.r2`, false, 'IF-A', false]);
  assert.equal(relationItem(JOB, 1, result().candidates[1]).listedForPairing, true);
  assert.equal(relationItem(JOB, 0, result().candidates[0]).pairApprovable, null, 'pairApprovable only for message_handoff');
  const item = thresholdItem(JOB, 0, result().thresholdCandidates[0]);
  assert.deepEqual([item.candidateId, item.scope, item.threshold, item.evidence.endTimeMissingReferenceRows, item.source], [`${JOB}.t0`, { processId: 'P1', adapterType: 'HTTP' }, 812.5, 10, 'data_p95']);
  const newer = thresholdItem(JOB, 0, threshold('P1', ['IF-A'], { limitation: '종료 시각이 없는 실행은 제외됩니다.', limitationRefs: ['methods/registry.py'] }));
  assert.deepEqual([newer.limitation, 'limitationRefs' in newer, 'note' in newer.evidence], ['종료 시각이 없는 실행은 제외됩니다.', false, false],
    'new Trainer shape: sentence kept, internal refs and evidence.note not passed to the screen');
  assert.equal(thresholdItem(JOB, 0, threshold('P1', ['IF-A'], { limitation: undefined })).limitation, null, 'missing limitation tolerated');
  // Trainer는 고정소수 문자열을 낸다(method-catalog thresholdSerialization, worker threshold_values.fixed_threshold). API는 검증 후 number로 낸다.
  for (const [text, value] of [['704', 704], ['812.5', 812.5], ['0', 0], ['-3.25', -3.25], ['0.0001', 0.0001], ['123456789012345', 123456789012345]]) {
    assert.equal(thresholdItem(JOB, 0, threshold('P1', ['IF-A'], { threshold: text, displayThreshold: text })).threshold, value, `string ${text}`);
  }
  assert.equal(thresholdItem(JOB, 0, threshold('P1', ['IF-A'], { threshold: 812.5 })).threshold, 812.5, 'old number shape still read');
  for (const bad of ['1e3', 'abc', '', '+1', ' 704', '704 ', '704.0', '07', '-0', '.5', '5.', '0x10', 'Infinity', 'NaN', '1234567890123456', `1.${'1'.repeat(40)}`, true, {}, [], Infinity, NaN]) {
    assert.throws(() => thresholdItem(JOB, 0, threshold('P1', ['IF-A'], { threshold: bad })), { code: 'rule_candidate_result_invalid' }, `rejects ${JSON.stringify(bad) ?? String(bad)}`);
  }
  assert.ok(CANDIDATE_ID.test(`${JOB}.r0`) && CANDIDATE_ID.test(`${JOB}.t12`) && !CANDIDATE_ID.test(`${JOB}.r01`) && !CANDIDATE_ID.test(`${JOB}.x1`));
});

test('transaction refs are unescaped exactly once (contract %25 / %3A)', () => {
  assert.equal(transactionOfRef('transaction:TX%3A9'), 'TX:9');
  assert.equal(transactionOfRef('transaction:A%253A'), 'A%3A', '%25 is decoded after %3A');
  assert.equal(transactionOfRef('transaction:a:b'), null, 'raw colon is not a transaction ref');
  assert.equal(transactionOfRef('transaction:bad%2'), null, 'broken escape');
  assert.equal(transactionOfRef('process:TX-1:P2'), null);
  assert.equal(transactionOfRef(null), null);
  assert.equal(transactionOfRef('transaction:'), null, 'empty body');
});

test('list: latest succeeded job, default interface = most approvable, graph and tables of that interface only', async () => {
  const db = fakeDb({ latest: job('99999999-2222-4333-8444-555555555555', { status: 'queued', finishedAt: null }) });
  const body = await readCandidateList(db, null);
  assert.deepEqual([body.latestJob.status, body.approval.available, body.result.job.id], ['queued', false, JOB]);
  assert.deepEqual(body.result.interfaces.map((item) => [item.interfaceId, item.approvable, item.transactions, item.thresholdCandidates]),
    [['IF-A', 2, 300, 2], ['IF-B', 0, 500, 1], ['IF-C', 0, 50, 0]]);
  assert.equal(body.result.selectedInterfaceId, 'IF-A');
  assert.deepEqual(body.result.graph.nodes.map((n) => n.id), [node('IF-A', 'P1').id, node('IF-A', 'P2').id]);
  assert.deepEqual(body.result.graph.edges.map((e) => [e.id, e.evidence.satisfactionRate]), [['precedes:a', 0.99]], 'cross-interface edge dropped');
  assert.deepEqual(body.result.relationCandidates.map((item) => item.candidateId), [`${JOB}.r0`, `${JOB}.r1`, `${JOB}.r2`]);
  assert.deepEqual(body.result.thresholdCandidates.map((item) => item.candidateId), [`${JOB}.t0`, `${JOB}.t1`]);
  assert.equal(body.result.thresholdsWithoutInterface, 1);
  assert.deepEqual(body.result.thresholdLimitations, ['elapsed_ms = END_TIME - START_TIME. 끝나지 않고 멈춘 단계는 잡지 못한다.']);
  assert.deepEqual(body.result.totals, { relationCandidates: 4, approvable: 2, thresholdCandidates: 3, thresholdApprovable: 2 });
  const b = await readCandidateList(fakeDb(), 'IF-B');
  assert.deepEqual([b.result.selectedInterfaceId, b.result.relationCandidates.map((item) => item.kind), b.result.thresholdCandidates.length], ['IF-B', ['required_step'], 1]);
  await rejects(readCandidateList(fakeDb(), 'IF-Z'), 'rule_candidate_interface_not_found', 404);
  const empty = await readCandidateList(fakeDb({ latest: null, succeeded: null }), null);
  assert.deepEqual(empty, { latestJob: null, approval: approvalState(), result: null }, 'no job: explicit null result, not an empty success');
  await rejects(readCandidateList(fakeDb({ succeeded: { ...job(JOB), result: { relationContractVersion: 'other' } } }), null), 'rule_candidate_contract_unsupported', 503);
  const malformed = { ...result(), thresholdCandidates: [threshold('P1', ['IF-A'], { threshold: '1e3' })] };
  await rejects(readCandidateList(fakeDb({ succeeded: { ...job(JOB), result: malformed } }), null), 'rule_candidate_result_invalid', 503);
  assert.ok(db.calls.every((call) => !/kind\s*=\s*'recommend_relations'/.test(call.sql) || call.sql.includes("kind::text = 'recommend_relations'")),
    'kind compared as text so a missing enum value is not a cast error');
});

test('detail: counterexamples link to the latest decision of the target step, pair precedes, rule draft', async () => {
  // 거래별 1건 고르기(대상 단계 우선 → 최근)는 SQL DISTINCT ON이 한다(통합 시험에서 확인). 가짜 질의는 고른 결과만 돌려준다.
  const decisions = [
    { transactionId: 'TX-1', decisionId: 'd-p2' },
    { transactionId: 'TX-2', decisionId: 'd-tx2' },
  ];
  const db = fakeDb({ byId: { [OLD_JOB]: { ...job(OLD_JOB), result: result() } }, decisions });
  const detail = await readCandidateDetail(db, `${OLD_JOB}.r2`);
  assert.deepEqual([detail.type, detail.isLatest, detail.approval.available, detail.relation.kind], ['relation', false, false, 'message_handoff']);
  assert.deepEqual(detail.counterexamples, [
    { transactionId: 'TX-1', reason: 'message_id_mismatch', transactionStatus: 'S', decisionId: 'd-p2' },
    { transactionId: 'TX-2', reason: 'message_id_mismatch', transactionStatus: 'S', decisionId: 'd-tx2' },
    { transactionId: null, reason: 'x', transactionStatus: null, decisionId: null },
  ], 'one decision per transaction; broken ref kept without a link');
  const lookup = db.calls.find((call) => call.sql.includes('FROM data_processes'));
  assert.deepEqual(lookup.params, [SOURCE, ['TX-1', 'TX-2'], 'P2'], 'target step (to) passed to the per-transaction pick');
  assert.match(lookup.sql, /DISTINCT ON \(p\.transaction_id\)/);
  assert.doesNotMatch(lookup.sql, /LIMIT/, 'no shared row limit that one heavy transaction could fill');
  assert.equal(db.calls.filter((call) => call.sql.includes('result_summary')).length, 1, 'detail reads the result JSON once (isLatest = id only)');
  assert.deepEqual(detail.pairedPrecedes, { candidateId: `${OLD_JOB}.r1`, status: 'not_candidate', approvable: false, counts: counts(100, 99), satisfactionRate: 0.99 });
  assert.deepEqual(detail.ruleDraft, { kind: 'message_handoff', scope: { interfaceId: 'IF-A', adapterType: 'HTTP' }, from: { processId: 'P1' }, to: { processId: 'P2' },
    match: { from: 'OUT', to: 'IN', key: 'message_id' }, enabled: true, source: result().candidates[2].source });
  assert.deepEqual(detail.relation.violationReasons, { other_predecessor: 1 });
  const required = await readCandidateDetail(fakeDb({ byId: { [JOB]: { ...job(JOB), result: result() } }, decisions: [{ transactionId: 'TX:9', processId: 'P5', decisionId: 'd-9' }] }), `${JOB}.r0`);
  assert.equal(required.isLatest, true);
  assert.deepEqual([required.isLatest, required.counterexamples[0].transactionId, required.counterexamples[0].decisionId, required.ruleDraft.scope, required.ruleDraft.to],
    [true, 'TX:9', 'd-9', { interfaceId: 'IF-A' }, null], 'required_step: any step of the transaction, scope interfaceId only');
  const t = await readCandidateDetail(fakeDb({ byId: { [JOB]: { ...job(JOB), result: result() } } }), `${JOB}.t0`);
  assert.deepEqual([t.type, t.ruleDraft], ['threshold', { field: 'elapsed_ms', operator: 'gt', threshold: 812.5, unit: 'ms', enabled: true, scope: { processId: 'P1', adapterType: 'HTTP' } }],
    'shared across two interfaces: no display interfaceId in scope');
  const single = await readCandidateDetail(fakeDb({ byId: { [JOB]: { ...job(JOB), result: { ...result(), thresholdCandidates: [threshold('P1', ['IF-A'])] } } } }), `${JOB}.t0`);
  assert.deepEqual(single.ruleDraft.scope, { processId: 'P1', adapterType: 'HTTP', interfaceId: 'IF-A' });
  assert.equal((await readCandidateDetail(fakeDb({ byId: { [JOB]: { ...job(JOB), result: result() } } }), `${JOB}.t1`)).ruleDraft, null, 'no threshold, no draft');
  await rejects(readCandidateDetail(fakeDb({ byId: { [JOB]: { ...job(JOB), result: result() } } }), `${JOB}.r9`), 'rule_candidate_not_found', 404);
  await rejects(readCandidateDetail(fakeDb(), `${JOB}.r0`), 'rule_candidate_not_found', 404);
  await rejects(readCandidateDetail(fakeDb(), 'nope'), 'invalid_candidate_id', 400);
  assert.deepEqual(relationRuleDraft(relationItem(JOB, 1, { ...result().candidates[1], scope: null }), null).scope, { interfaceId: 'IF-A' }, 'null scope: display interface only');
  assert.equal(softRuleDraft(thresholdItem(JOB, 1, result().thresholdCandidates[1])), null);
  // ruleDraft.threshold는 PUT /api/rule-set 소프트룰 threshold(number) 형식이다.
  assert.deepEqual(softRuleDraft(thresholdItem(JOB, 0, threshold('P1', ['IF-A'], { threshold: '704', displayThreshold: '704' }))),
    { field: 'elapsed_ms', operator: 'gt', threshold: 704, unit: 'ms', enabled: true, scope: { processId: 'P1', adapterType: 'HTTP', interfaceId: 'IF-A' } });
});

test('request parsing', () => {
  assert.equal(parseCandidateListQuery({}), null);
  assert.equal(parseCandidateListQuery({ interfaceId: 'IF-A' }), 'IF-A');
  assert.throws(() => parseCandidateListQuery({ interfaceId: ['a', 'b'] }), { code: 'invalid_interface_id' });
  assert.throws(() => parseCandidateListQuery({ interfaceId: '' }), { code: 'invalid_interface_id' });
  assert.throws(() => parseCandidateListQuery({ page: '1' }), { code: 'unknown_candidate_filter' });
  const id = '469339d8-147e-48e1-a087-d6c2f299b564';
  assert.deepEqual(parseCandidateJobRequest({ snapshotId: id.toUpperCase() }), { snapshotId: id });
  assert.deepEqual(parseCandidateJobRequest({ snapshotId: id, excludeProcessIds: ['X_STUB'], handoffMismatchSuffixPatterns: [] }),
    { snapshotId: id, excludeProcessIds: ['X_STUB'], handoffMismatchSuffixPatterns: [] });
  for (const body of [null, {}, { snapshotId: 'x' }, { snapshotId: id, extra: 1 }, { snapshotId: id, excludeProcessIds: ['a', 'a'] },
    { snapshotId: id, excludeProcessIds: [''] }, { snapshotId: id, handoffMismatchSuffixPatterns: Array(11).fill(0).map((_, i) => `-${i}`) },
    { snapshotId: id, handoffMismatchSuffixPatterns: ['x'.repeat(101)] }]) {
    assert.throws(() => parseCandidateJobRequest(body), { code: 'invalid_candidate_job_request' }, JSON.stringify(body)?.slice(0, 60));
  }
});

test('handoff pair: Trainer pairedPrecedesEdgeId wins, tuple match only for results without it', async () => {
  const other = candidate('precedes', 'IF-A', 'P1', 'P2', { status: 'candidate', patternEdgeId: 'precedes:other', scope: { adapterType: 'DB' } });
  const detailWith = async (handoffExtra) => {
    const base = result();
    const handoff = { ...base.candidates[2], ...handoffExtra };
    if (handoffExtra.pairedPrecedesEdgeId === undefined) delete handoff.pairedPrecedesEdgeId;
    const candidates = [base.candidates[0], base.candidates[1], handoff, other];
    return readCandidateDetail(fakeDb({ byId: { [JOB]: { ...job(JOB), result: { ...base, candidates } } } }), `${JOB}.r2`);
  };
  assert.equal((await detailWith({ pairedPrecedesEdgeId: 'precedes:other' })).pairedPrecedes.candidateId, `${JOB}.r3`, 'edge id, not tuple');
  assert.equal((await detailWith({ pairedPrecedesEdgeId: 'precedes:missing' })).pairedPrecedes, null, 'named pair not listed: null, no guess');
  assert.equal((await detailWith({ pairedPrecedesEdgeId: undefined })).pairedPrecedes.candidateId, `${JOB}.r1`, 'old result: same interface/from/to/scope');
  assert.equal((await detailWith({ pairedPrecedesEdgeId: null })).pairedPrecedes.candidateId, `${JOB}.r1`, 'explicit null = absent: tuple match');
  assert.equal((await detailWith({ pairedPrecedesEdgeId: 7 })).pairedPrecedes, null, 'non-string, non-null id: no guess');
});

test('503 mapping is limited to the DB-not-ready causes', () => {
  const pg = (fields) => Object.assign(new Error(fields.message ?? 'x'), fields);
  const enumIn = (message, extra = {}) => pg({ code: '22P02', routine: 'enum_in', message, ...extra });
  assert.equal(relationJobUnavailable(enumIn('invalid input value for enum job_kind: "recommend_relations"')).code, 'relation_job_kind_unavailable');
  // 서버 언어가 영어가 아니어도(메시지 문장이 달라도) routine·형식 이름으로 판단한다.
  assert.equal(relationJobUnavailable(enumIn('열거형 job_kind에 대한 입력 값이 잘못됨: "recommend_relations"')).code, 'relation_job_kind_unavailable');
  assert.equal(relationJobUnavailable(enumIn('x', { dataType: 'job_kind' })).code, 'relation_job_kind_unavailable', 'dataType field wins when present');
  assert.equal(relationJobUnavailable(enumIn('invalid input value for enum job_kind: "x"', { dataType: 'job_status' })), null);
  assert.equal(relationJobUnavailable(enumIn('invalid input value for enum job_status: "x"')), null, 'other enum');
  assert.equal(relationJobUnavailable(enumIn('invalid input value for enum my_job_kind_x: "x"')), null, 'type name must match whole');
  assert.equal(relationJobUnavailable(pg({ code: '22P02', message: 'invalid input value for enum job_kind: "x"' })), null, 'no enum_in routine: not mapped');
  assert.equal(relationJobUnavailable(pg({ code: '22P02', routine: 'string_to_uuid', message: 'invalid input syntax for type uuid: "x"' })), null);
  assert.equal(relationJobUnavailable(pg({ code: '23514', constraint: 'run_jobs_v03_contract' })).code, 'relation_job_contract_unavailable');
  assert.equal(relationJobUnavailable(pg({ code: '23514', constraint: 'run_jobs_attempts_check' })), null);
  assert.equal(relationJobUnavailable(pg({ code: '23514' })), null);
  assert.equal(relationJobUnavailable(null), null);
});

/** 작업 요청용 가짜 연결: SQL 문자열로 분기, 모든 질의를 기록. */
const fakePool = ({ existing = null, snapshot = { sourceId: SOURCE }, insertError = null } = {}) => {
  const calls = [];
  const client = {
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (sql.includes('FROM run_jobs') && sql.includes('idempotency_key')) return { rows: existing ? [existing] : [] };
      if (sql.includes('FROM data_snapshots')) return { rows: snapshot ? [snapshot] : [] };
      if (sql.includes('INSERT INTO run_jobs')) {
        if (insertError) throw insertError;
        const input = JSON.parse(params[6]);
        return { rowCount: 1, rows: [{ id: params[0], status: 'queued', kind: 'recommend_relations', inputSpec: input, requestHash: params[4] }] };
      }
      return { rows: [], rowCount: 0 };
    },
    release: () => undefined,
  };
  return { calls, connect: async () => client };
};
const SNAP = '469339d8-147e-48e1-a087-d6c2f299b564';

test('enqueue: idempotency lookup comes before the snapshot check', async () => {
  const first = fakePool();
  const created = await enqueueCandidateJob(first, { snapshotId: SNAP, excludeProcessIds: ['X'] }, 'admin-1', 'key-1');
  assert.equal(created.created, true);
  const insert = first.calls.find((call) => call.sql.includes('INSERT INTO run_jobs'));
  const hash = insert.params[4];
  assert.deepEqual(JSON.parse(insert.params[6]), { kind: 'recommend_relations', snapshotId: SNAP, sourceId: SOURCE, excludeProcessIds: ['X'] });
  const order = first.calls.map((call) => call.sql.includes('idempotency_key = $2') ? 'lookup' : call.sql.includes('data_snapshots') ? 'snapshot'
    : call.sql.includes('INSERT INTO run_jobs') ? 'insert' : call.sql.includes('training_jobs') ? 'queue' : call.sql.trim());
  assert.deepEqual(order, ['BEGIN', 'lookup', 'snapshot', 'insert', 'queue', 'COMMIT']);

  // 재요청: 스냅샷이 더 이상 ready가 아니어도 같은 작업 200, 스냅샷·삽입·큐 질의 없음.
  const job1 = { id: created.job.id, status: 'queued', kind: 'recommend_relations', inputSpec: created.job.inputSpec, request_hash: hash };
  const replay = fakePool({ existing: job1, snapshot: null });
  const again = await enqueueCandidateJob(replay, { snapshotId: SNAP, excludeProcessIds: ['X'] }, 'admin-1', 'key-1');
  assert.deepEqual([again.created, again.job.id, 'request_hash' in again.job], [false, created.job.id, false]);
  assert.ok(!replay.calls.some((call) => /data_snapshots|INSERT/.test(call.sql)), 'no snapshot read, no insert on replay');
  await rejects(enqueueCandidateJob(fakePool({ existing: job1 }), { snapshotId: SNAP }, 'admin-1', 'key-1'), 'idempotency_payload_conflict', 409);

  // 옛 구현(hash에 sourceId 포함)으로 만든 작업: request_hash = 입력 전체(sourceId 포함) sha256. 같은 본문 재요청은 200, 다른 본문은 409.
  const legacyHash = sha(JSON.stringify({ kind: 'recommend_relations', snapshotId: SNAP, sourceId: SOURCE, excludeProcessIds: ['X'] }));
  assert.notEqual(legacyHash, hash);
  const legacyJob = { ...job1, request_hash: legacyHash };
  const legacyReplay = await enqueueCandidateJob(fakePool({ existing: legacyJob, snapshot: null }), { snapshotId: SNAP, excludeProcessIds: ['X'] }, 'admin-1', 'key-1');
  assert.deepEqual([legacyReplay.created, legacyReplay.job.id], [false, created.job.id], 'old-hash job replays as the same job');
  await rejects(enqueueCandidateJob(fakePool({ existing: legacyJob }), { snapshotId: SNAP }, 'admin-1', 'key-1'), 'idempotency_payload_conflict', 409);
  await rejects(enqueueCandidateJob(fakePool({ existing: { ...legacyJob, inputSpec: { ...legacyJob.inputSpec, sourceId: 'other' } } }),
    { snapshotId: SNAP, excludeProcessIds: ['X'] }, 'admin-1', 'key-1'), 'idempotency_payload_conflict', 409);

  // 키 없음: 미리 찾지 않는다(새 키). 스냅샷 없음 409.
  const fresh = fakePool({ snapshot: null });
  await rejects(enqueueCandidateJob(fresh, { snapshotId: SNAP }, 'admin-1', null), 'snapshot_not_ready', 409);
  assert.ok(!fresh.calls.some((call) => call.sql.includes('idempotency_key = $2')));
  assert.equal(fresh.calls.at(-1).sql, 'ROLLBACK');
});

test('enqueue: unrelated DB errors are rethrown, not mapped to 503', async () => {
  const other = Object.assign(new Error('new row violates check constraint "run_jobs_attempts_check"'), { code: '23514', constraint: 'run_jobs_attempts_check' });
  await assert.rejects(enqueueCandidateJob(fakePool({ insertError: other }), { snapshotId: SNAP }, 'admin-1', null), (error) => error === other);
  const contract = Object.assign(new Error('violates check constraint'), { code: '23514', constraint: 'run_jobs_v03_contract' });
  await rejects(enqueueCandidateJob(fakePool({ insertError: contract }), { snapshotId: SNAP }, 'admin-1', null), 'relation_job_contract_unavailable', 503);
});
