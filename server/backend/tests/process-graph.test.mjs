// 거래 내부 실행 그래프(층 A, contracts/process-graph.md) 단위 시험. DB 없음. 실행 전 `npm run build`.
// 공용 id 시험 벡터(contracts/process-graph-id-vectors.json)를 모두 확인한다(Trainer·Detector Python도 같은 파일을 쓴다).
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const { buildInstanceGraph, graphIds, patternGraphIds, graphValueAbsent, escapeIdComponent, pickTransactionRow } = await import('../dist/services/processGraph.js');
const vectors = JSON.parse(await readFile(path.join(here, '../../contracts/process-graph-id-vectors.json'), 'utf8'));

test('shared id vectors', () => {
  const build = {
    transaction: (c) => graphIds.transaction(c.transactionId),
    process: (c) => graphIds.process(c.transactionId, c.processId),
    message: (c) => graphIds.message(c.executionId),
    unkeyed: (c) => graphIds.unkeyed(c.observationId),
    missing: (c) => graphIds.missing(c.referencingNodeId, c.processId),
    contains: (c) => graphIds.edge('contains', c.from, c.to),
    precedes: (c) => graphIds.edge('precedes', c.from, c.to),
    hands_off: (c) => graphIds.handsOff(c.from, c.to, c.messageNodeId),
  };
  assert.ok(vectors.idVectors.length >= 17 && vectors.absenceVectors.length >= 26);
  for (const vector of vectors.idVectors) assert.equal(build[vector.kind](vector.components), vector.expectedId, vector.case);
  for (const vector of vectors.absenceVectors) {
    assert.equal(graphValueAbsent(vector.value, vector.field), vector.absent, vector.case);
    // field가 없는 벡터: NONE 규칙은 DEPEND_PROCESS_ID에만 있으므로 다른 열(PROCESS_ID)에서도 결과가 같다.
    if (!vector.field) assert.equal(graphValueAbsent(vector.value, 'PROCESS_ID'), vector.absent, `${vector.case} (PROCESS_ID)`);
  }
  assert.equal(escapeIdComponent('%3A:'), '%253A%3A');
});

test('shared layer B (pattern) id vectors', () => {
  const build = {
    pattern_step: (c) => patternGraphIds.patternStep(c.interfaceId, c.processId, c.adapterType),
    precedes: (c) => patternGraphIds.edge('precedes', c.from, c.to),
    branches_to: (c) => patternGraphIds.edge('branches_to', c.from, c.to),
    hands_off: (c) => patternGraphIds.edge('hands_off', c.from, c.to),
  };
  assert.equal(vectors.patternIdVectors.length, 8);
  for (const vector of vectors.patternIdVectors) {
    assert.equal(build[vector.kind](vector.components), vector.expectedId, vector.case);
    if (vector.expectedId === null) assert.equal(vector.outcome, 'undetermined', vector.case);
  }
  // 고정 구성 요소 수: 층 A 간선 id는 노드 id를 다시 이스케이프하지 않고 붙이므로, 가변 길이면 다른 끝점 쌍이 같은 id가 된다.
  const a = patternGraphIds.edge('precedes', patternGraphIds.patternStep('I', 'P', null), patternGraphIds.patternStep('pattern_step', 'X', 'Y'));
  const b = patternGraphIds.edge('precedes', patternGraphIds.patternStep('I', 'P', 'pattern_step'), patternGraphIds.patternStep('X', 'Y', null));
  assert.notEqual(a, b);
  assert.equal(patternGraphIds.patternStep('IF_A', '\\N', 'DB'), null, 'process absent -> no node');
});

let seq = 0;
const obs = () => `00000000-0000-4000-8000-${String(++seq).padStart(12, '0')}`;
const TX = (transactionId = 'TX-1', extra = {}) => ({ observationId: obs(), revisionNo: '1', transactionId, interfaceId: 'IF_A', channelId: 'CH',
  status: 'S', responseCode: '0000', processTimeMs: '10', processCount: '2', retries: [{ retryCount: '0', status: 'S', observationId: 'x' }], ...extra });
const P = (processId, dependsOn = null, extra = {}) => ({ observationId: obs(), executionId: obs(), revisionNo: '1', transactionId: 'TX-1',
  transactionObservationId: null, processId, dependsOn, status: 'S', responseCode: '0000', totalCount: '1', retryCount: '0', focus: false,
  decisionId: null, grade: null, ...extra });
const M = (processId, direction, messageId, extra = {}) => ({ observationId: obs(), executionId: obs(), revisionNo: '1', processObservationId: null,
  transactionId: 'TX-1', processId, messageId, direction, status: 'S', dataSize: '10', messageIndex: '1', ...extra });
const graph = (transaction, processes, messages = [], extra = {}) => buildInstanceGraph({ sourceId: 'src', transaction, processes, messages,
  processesTruncated: false, messagesTruncated: false, generatedAt: '2026-11-12T00:00:00.000Z', ...extra });
const node = (g, id) => g.nodes.find((n) => n.id === id);
const edge = (g, kind, from, to) => g.edges.filter((e) => e.kind === kind && e.from === from && e.to === to);
const pid = (p) => graphIds.process('TX-1', p);

test('header, contains, precedes direction and handoff match', () => {
  const tx = TX();
  const p1 = P('PR001', null, { transactionObservationId: tx.observationId });
  const p2 = P('PR002', 'PR001', { focus: true, decisionId: 'd-1', grade: 'anomaly' });
  const out = M('PR001', 'OUT', 'm-1', { processObservationId: p1.observationId });
  const inn = M('PR002', 'IN', 'm-1');
  const g = graph(tx, [p1, p2], [out, inn]);
  assert.deepEqual(g.graph, { layer: 'instance', id: 'TX-1', version: null, sourceId: 'src', snapshotId: null, period: null,
    ruleSetRevision: null, generatedAt: '2026-11-12T00:00:00.000Z' });
  assert.equal(edge(g, 'contains', 'transaction:TX-1', pid('PR001'))[0].verification, 'verified', 'FK set');
  assert.equal(edge(g, 'contains', 'transaction:TX-1', pid('PR002'))[0].verification, 'not_checked', 'string grouping');
  const precedes = edge(g, 'precedes', pid('PR001'), pid('PR002'));
  assert.equal(precedes.length, 1, 'arrow predecessor -> successor (reverse of DEPEND_PROCESS_ID column)');
  assert.equal(precedes[0].id, `precedes:${pid('PR001')}:${pid('PR002')}`);
  assert.deepEqual([precedes[0].verification, precedes[0].evidence], ['verified', { handoff: 'match', reason: null }]);
  const outId = graphIds.message(out.executionId), inId = graphIds.message(inn.executionId);
  assert.equal(edge(g, 'contains', pid('PR001'), outId)[0].verification, 'verified', 'message FK');
  assert.equal(edge(g, 'contains', pid('PR002'), inId)[0].verification, 'not_checked');
  const [handoff] = edge(g, 'hands_off', pid('PR001'), pid('PR002'));
  assert.equal(handoff.id, `hands_off:${pid('PR001')}:${pid('PR002')}:${outId}`);
  assert.deepEqual(handoff.evidence, { result: 'match', reason: null, outMessage: outId, inMessages: [inId] });
  assert.deepEqual([node(g, pid('PR002')).attrs.attempts[0].decisionId, node(g, pid('PR002')).attrs.attempts[0].grade], ['d-1', 'anomaly']);
  assert.equal(node(g, pid('PR001')).attrs.branch, false);
});

test('mismatch, excluded suffix, multiple messages, missing direction', () => {
  const mismatch = graph(TX(), [P('PR001'), P('PR002', 'PR001')], [M('PR001', 'OUT', 'a'), M('PR002', 'IN', 'b')]);
  assert.deepEqual(edge(mismatch, 'precedes', pid('PR001'), pid('PR002'))[0].evidence, { handoff: 'mismatch', reason: null });
  const tx2 = TX('TX-01');
  const at = (p) => ({ ...p, transactionId: 'TX-01' });
  const excluded = graph(tx2, [at(P('PR001')), at(P('PR002', 'PR001'))], [M('PR001', 'OUT', 'a', { transactionId: 'TX-01' }), M('PR002', 'IN', 'b', { transactionId: 'TX-01' })]);
  assert.deepEqual(excluded.edges.find((e) => e.kind === 'precedes').evidence, { handoff: 'undetermined', reason: 'excluded_transaction_suffix' });
  const multi = graph(TX(), [P('PR001'), P('PR002', 'PR001')], [M('PR001', 'OUT', 'a'), M('PR001', 'OUT', 'b'), M('PR002', 'IN', 'c')]);
  assert.deepEqual(edge(multi, 'precedes', pid('PR001'), pid('PR002'))[0].evidence, { handoff: 'undetermined', reason: 'multiple_messages' });
  assert.equal(edge(multi, 'hands_off', pid('PR001'), pid('PR002')).length, 2, 'one hands_off per OUT message');
  const noIn = graph(TX(), [P('PR001'), P('PR002', 'PR001')], [M('PR001', 'OUT', 'a'), M('PR002', null, 'a')]);
  assert.deepEqual(edge(noIn, 'precedes', pid('PR001'), pid('PR002'))[0].evidence, { handoff: 'undetermined', reason: 'no_in_message' });
  const noOut = graph(TX(), [P('PR001'), P('PR002', 'PR001')], []);
  assert.deepEqual(edge(noOut, 'precedes', pid('PR001'), pid('PR002'))[0].evidence, { handoff: 'undetermined', reason: 'no_out_message' });
  assert.equal(noOut.edges.filter((e) => e.kind === 'hands_off').length, 0);
  const truncated = graph(TX(), [P('PR001'), P('PR002', 'PR001')], [M('PR001', 'OUT', 'a'), M('PR002', 'IN', 'a')], { messagesTruncated: true });
  assert.deepEqual(truncated.edges.find((e) => e.kind === 'precedes').evidence, { handoff: 'undetermined', reason: 'messages_truncated' });
});

test('branch, 3-step chain, missing predecessor, absent dependency', () => {
  const g = graph(TX(), [P('PR001'), P('PR002', 'PR001'), P('PR003', 'PR001'), P('PR004', 'PR003'), P('PR005', 'PR009'),
    P('PR006', ' \\N '), P('PR007', '')]);
  assert.equal(node(g, pid('PR001')).attrs.branch, true, 'two successors');
  assert.equal(node(g, pid('PR003')).attrs.branch, false);
  assert.equal(edge(g, 'precedes', pid('PR003'), pid('PR004')).length, 1, 'arbitrary depth');
  const missingId = graphIds.missing(pid('PR005'), 'PR009');
  assert.deepEqual(node(g, missingId), { id: missingId, kind: 'missing', attrs: { processId: 'PR009', reason: 'not_logged' } });
  assert.deepEqual([edge(g, 'precedes', missingId, pid('PR005'))[0].verification, edge(g, 'precedes', missingId, pid('PR005'))[0].evidence],
    ['unmatched', { handoff: 'undetermined', reason: 'precedes_not_verified' }]);
  assert.equal(g.edges.filter((e) => e.kind === 'precedes' && (e.to === pid('PR006') || e.to === pid('PR007'))).length, 0, 'absent = start step');
  assert.equal(g.nodes.filter((n) => n.kind === 'missing').length, 1);
  const truncated = graph(TX(), [P('PR005', 'PR009')], [], { processesTruncated: true });
  assert.equal(truncated.nodes.find((n) => n.kind === 'missing').attrs.reason, 'not_loaded');
});

test('retries group into one node; duplicate retry count is ambiguous; unkeyed rows', () => {
  const first = P('PR002', 'PR001', { retryCount: '0', status: 'F' });
  const second = P('PR002', 'PR001', { retryCount: '1', status: 'S' });
  const g = graph(TX(), [second, P('PR001'), first]);
  const retried = node(g, pid('PR002'));
  assert.deepEqual(retried.attrs.attempts.map((a) => [a.retryCount, a.status]), [['0', 'F'], ['1', 'S']]);
  assert.deepEqual([retried.attrs.status, retried.attrs.verification], ['S', null], 'status = highest retry');
  assert.equal(edge(g, 'precedes', pid('PR001'), pid('PR002'))[0].verification, 'verified', 'retries are not ambiguity');
  const dup = graph(TX(), [P('PR001'), P('PR002', 'PR001', { retryCount: '0' }), P('PR002', 'PR001', { retryCount: '0' })]);
  assert.deepEqual([node(dup, pid('PR002')).attrs.verification, node(dup, pid('PR002')).attrs.status], ['ambiguous', null]);
  assert.equal(node(dup, pid('PR002')).attrs.attempts.length, 2, 'all attempts kept');
  assert.equal(edge(dup, 'precedes', pid('PR001'), pid('PR002'))[0].verification, 'ambiguous');
  const disagree = graph(TX(), [P('PR001'), P('PR003'), P('PR002', 'PR001', { retryCount: '0' }), P('PR002', 'PR003', { retryCount: '1' })]);
  assert.deepEqual(disagree.edges.filter((e) => e.kind === 'precedes').map((e) => e.verification), ['ambiguous', 'ambiguous']);
  const unkeyed = P(null, 'PR001');
  const u = graph(TX(), [P('PR001'), unkeyed, P('  ')], [M('PR001', 'OUT', 'a', { executionId: '\\N' })]);
  assert.equal(node(u, graphIds.unkeyed(unkeyed.observationId)).attrs.unkeyed, true);
  assert.equal(u.edges.filter((e) => e.kind === 'precedes').length, 0, 'no edges derived from unkeyed process');
  assert.equal(u.nodes.filter((n) => n.kind === 'process' && n.id.startsWith('unkeyed:')).length, 2);
  assert.ok(u.nodes.find((n) => n.kind === 'message').id.startsWith('unkeyed:'), 'message without execution id');
});

test('colon in source values does not collide and orphan message gets missing node', () => {
  const a = graph(TX('a:b'), [{ ...P('c'), transactionId: 'a:b' }]);
  const b = graph(TX('a'), [{ ...P('b:c'), transactionId: 'a' }]);
  assert.equal(a.nodes.find((n) => n.kind === 'process').id, 'process:a%3Ab:c');
  assert.equal(b.nodes.find((n) => n.kind === 'process').id, 'process:a:b%3Ac');
  assert.equal(a.nodes.find((n) => n.kind === 'process').attrs.transactionId, 'a:b', 'attrs keep raw values');
  const orphan = M('PR404', 'IN', 'x');
  const g = graph(TX(), [P('PR001')], [orphan]);
  const messageId = graphIds.message(orphan.executionId);
  const missingId = graphIds.missing(messageId, 'PR404');
  assert.equal(edge(g, 'contains', missingId, messageId)[0].verification, 'unmatched');
  const noTx = graph(null, [P('PR001')]);
  assert.equal(noTx.edges.filter((e) => e.kind === 'contains').length, 0, 'no transaction row -> no contains T->P');
  assert.equal(noTx.graph.id, null);
});

test('DEPEND_PROCESS_ID NONE is a start step; other spellings are values kept verbatim', () => {
  const g = graph(TX(), [P('PR001', 'NONE'), P('PR002', ' NONE '), P('PR003', 'none'), P('PR004', '﻿NONE'), P('PR005', 'PR001')]);
  assert.equal(edge(g, 'precedes', pid('PR001'), pid('PR005')).length, 1);
  for (const p of ['PR001', 'PR002']) {
    assert.equal(g.edges.filter((e) => e.kind === 'precedes' && e.to === pid(p)).length, 0, `${p}: start step`);
    assert.equal(node(g, pid(p)).attrs.dependsOn, null, `${p}: no dependency`);
  }
  assert.equal(node(g, pid('PR001')).attrs.attempts[0].dependsOn, 'NONE', 'attempt keeps the raw value');
  assert.deepEqual(g.nodes.filter((n) => n.kind === 'missing').map((n) => [n.id, n.attrs.processId]).sort(),
    [[graphIds.missing(pid('PR003'), 'none'), 'none'], [graphIds.missing(pid('PR004'), '﻿NONE'), '﻿NONE']].sort());
  // NONE은 DEPEND_PROCESS_ID에서만 없음: Process ID NONE은 값이다.
  assert.equal(node(graph(TX(), [P('NONE')]), pid('NONE')).attrs.unkeyed, false);
});

test('excluded suffix uses the resolved graph id when the TRANSACTION row is missing', () => {
  const at = (p) => ({ ...p, transactionId: 'TX-01' });
  const rows = () => [at(P('PR001')), at(P('PR002', 'PR001', { focus: true }))];
  const msgs = () => [M('PR001', 'OUT', 'a', { transactionId: 'TX-01' }), M('PR002', 'IN', 'b', { transactionId: 'TX-01' })];
  const noTx = graph(null, rows(), msgs());
  assert.equal(noTx.graph.id, 'TX-01', 'graph id falls back to the focus PROCESS');
  const precedes = noTx.edges.find((e) => e.kind === 'precedes');
  assert.deepEqual(precedes.evidence, { handoff: 'undetermined', reason: 'excluded_transaction_suffix' }, 'not mismatch');
  assert.equal(noTx.edges.find((e) => e.kind === 'hands_off').evidence.reason, 'excluded_transaction_suffix');
  const absentTxId = graph(TX('\\N'), rows(), msgs());
  assert.deepEqual(absentTxId.edges.find((e) => e.kind === 'precedes').evidence, { handoff: 'undetermined', reason: 'excluded_transaction_suffix' },
    'TRANSACTION row with an absent id also falls back');
  const plain = graph(null, [P('PR001'), P('PR002', 'PR001', { focus: true })], [M('PR001', 'OUT', 'a'), M('PR002', 'IN', 'b')]);
  assert.deepEqual(plain.edges.find((e) => e.kind === 'precedes').evidence, { handoff: 'mismatch', reason: null });
});

test('transaction retries truncation flag', () => {
  assert.equal(node(graph(TX(), [P('PR001')]), 'transaction:TX-1').attrs.retriesTruncated, false);
  assert.equal(node(graph(TX('TX-1', { retriesTruncated: true }), [P('PR001')]), 'transaction:TX-1').attrs.retriesTruncated, true);
});

test('graph read model skips the transaction retries lookup when the transaction id is absent', async () => {
  const { readDecisionGraph } = await import('../dist/services/analysisReadModel.js');
  // 가짜 Queryable: SQL 조각으로 응답을 고른다. 재시도 조회(layer='T')가 나가면 기록한다.
  const fakeDb = (transactionId) => {
    const retryCalls = [];
    const db = {
      async query(sql, params) {
        if (sql.includes('AS item FROM items')) return { rows: [{ item: { id: 'd1', jobId: 'j1', processObservationId: 'p1' } }] };
        if (sql.includes('SELECT p.observation_id,p.transaction_observation_id')) {
          return { rows: [{ observation_id: 'p1', transaction_observation_id: 't1', transaction_id: transactionId, source_id: 's1' }] };
        }
        if (sql.includes("o.layer='T'")) { retryCalls.push(params); return { rows: [{ retryCount: '0', status: 'S', observationId: 'other' }] }; }
        if (sql.includes('FROM data_transactions t')) {
          return { rows: [{ transactionId, interfaceId: 'IF_A', channelId: 'CH', status: 'S', responseCode: '0000', processTimeMs: '1',
            processCount: '1', _observationId: 't1', _revisionNo: '1', _sourceId: 's1' }] };
        }
        if (sql.includes('WITH candidates')) {
          return { rows: [{ observationId: 'p1', processId: 'PR001', dependsOn: null, status: 'S', responseCode: '0000', totalCount: '1',
            focus: true, _total: 1, _errors: 0, _executionId: 'e1', _revisionNo: '1', _transactionId: transactionId,
            _transactionObservationId: 't1', _retryCount: '0' }] };
        }
        return { rows: [] };
      },
    };
    return { db, retryCalls };
  };
  for (const absent of ['', '\\N', ' \t']) {
    const { db, retryCalls } = fakeDb(absent);
    const result = await readDecisionGraph(db, 'd1');
    assert.equal(retryCalls.length, 0, `no retries lookup for ${JSON.stringify(absent)}`);
    const tx = result.nodes.find((n) => n.kind === 'transaction');
    assert.ok(tx, `transaction node kept for ${JSON.stringify(absent)}`);
    assert.deepEqual([tx.attrs.retries, tx.attrs.retriesTruncated], [[], false]);
  }
  const { db, retryCalls } = fakeDb('TX-1');
  await readDecisionGraph(db, 'd1');
  assert.equal(retryCalls.length, 2, 'present id looks up retries and the representative row');
  assert.equal(retryCalls[0][0], 'TX-1');
});

test('transaction representative row: highest retry; tie, blank retry or missing execution id → ambiguous (Detector 7.2)', () => {
  const row = (retryCount, id = retryCount) => ({ retryCount, id });
  const pick = (executions, top, missing = {}) => pickTransactionRow({ executions, missingExecutionId: missing.execution ?? 0,
    missingRetryCount: missing.retry ?? 0, top });
  assert.deepEqual(pick(0, []), { row: null, ambiguous: false }, 'no rows: nothing to pick, not ambiguous');
  assert.deepEqual(pick(1, [row(null, 'only')]), { row: row(null, 'only'), ambiguous: false }, 'a single execution is used as is');
  assert.deepEqual(pick(3, [row('2'), row('1')]), { row: row('2'), ambiguous: false }, 'highest retry wins');
  assert.deepEqual(pick(2, [row('10'), row('9')]), { row: row('10'), ambiguous: false }, 'numeric, not string, order');
  assert.deepEqual(pick(3, [row('2', 'a'), row('2.0', 'b')]), { row: null, ambiguous: true }, 'highest retry shared');
  assert.deepEqual(pick(2, [row(null), row('1')], { retry: 1 }), { row: null, ambiguous: true }, 'blank retry count');
  assert.deepEqual(pick(2, [row('2'), row('1')], { execution: 1 }), { row: null, ambiguous: true }, 'a row without execution id');
  assert.deepEqual(pick(2, [row(' '), row('1')]), { row: null, ambiguous: true }, 'absent-looking retry text is blank');
});

test('ambiguous transaction node: no representative, status null, contains not verified even when FKs are NULL', () => {
  const tx = TX('TX-1', { observationId: null, revisionNo: null, status: null, responseCode: null, ambiguous: true });
  const g = graph(tx, [P('PR001', null, { transactionObservationId: null })]);
  const t = node(g, 'transaction:TX-1');
  assert.deepEqual([t.attrs.verification, t.attrs.status, t.attrs.observationId], ['ambiguous', null, null]);
  assert.equal(edge(g, 'contains', 'transaction:TX-1', pid('PR001'))[0].verification, 'not_checked');
  assert.equal(node(graph(TX(), [P('PR001')]), 'transaction:TX-1').attrs.verification, null, 'picked representative is not ambiguous');
});

test('graph read model uses the highest-retry TRANSACTION execution as the node, ambiguous on a tie', async () => {
  const { readDecisionGraph } = await import('../dist/services/analysisReadModel.js');
  const fake = (top) => ({
    async query(sql) {
      if (sql.includes('AS item FROM items')) return { rows: [{ item: { id: 'd1', jobId: 'j1', processObservationId: 'p1' } }] };
      if (sql.includes('SELECT p.observation_id,p.transaction_observation_id')) {
        return { rows: [{ observation_id: 'p1', transaction_observation_id: 't-first', transaction_id: 'TX-1', source_id: 's1' }] };
      }
      if (sql.includes('retry_count DESC NULLS FIRST')) return { rows: top };
      if (sql.includes("o.layer='T'")) return { rows: [] };
      if (sql.includes('FROM data_transactions t')) {
        return { rows: [{ transactionId: 'TX-1', interfaceId: 'IF_A', channelId: 'CH', status: 'F', responseCode: 'E-CONN', processTimeMs: '1',
          processCount: '1', _observationId: 't-first', _revisionNo: '1', _sourceId: 's1' }] };
      }
      if (sql.includes('WITH candidates')) {
        return { rows: [{ observationId: 'p1', processId: 'PR001', dependsOn: null, status: 'S', responseCode: '0000', totalCount: '1',
          focus: true, _total: 1, _errors: 0, _executionId: 'e1', _revisionNo: '1', _transactionId: 'TX-1',
          _transactionObservationId: 't-first', _retryCount: '0' }] };
      }
      return { rows: [] };
    },
  });
  const T = (observationId, retryCount, status) => ({ observationId, revisionNo: '1', retryCount, transactionId: 'TX-1', interfaceId: 'IF_A',
    channelId: 'CH', status, responseCode: status === 'S' ? '0000' : 'E-CONN', processTimeMs: '2', processCount: '1',
    _executions: 2, _missingExecutionId: 0, _missingRetryCount: 0 });
  const picked = (await readDecisionGraph(fake([T('t-retry', '1', 'S'), T('t-first', '0', 'F')]), 'd1')).nodes.find((n) => n.kind === 'transaction');
  assert.deepEqual([picked.attrs.observationId, picked.attrs.status, picked.attrs.verification], ['t-retry', 'S', null],
    'node status comes from the retry, not the FK row of the focus PROCESS');
  const g = await readDecisionGraph(fake([T('t-a', '1', 'S'), T('t-b', '1', 'F')]), 'd1');
  const tie = g.nodes.find((n) => n.kind === 'transaction');
  assert.deepEqual([tie.id, tie.attrs.observationId, tie.attrs.status, tie.attrs.verification], ['transaction:TX-1', null, null, 'ambiguous']);
  assert.equal(g.edges.find((e) => e.kind === 'contains' && e.from === 'transaction:TX-1').verification, 'not_checked');
});
