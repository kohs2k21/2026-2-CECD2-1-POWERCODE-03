// 사건 보기(층 C, contracts/incidents-ui.md) 순수 로직 시험: 병합 먼저 → 이어짐 사슬, 어긋난 연결은 쓰지 않고 표시. DB 없음.
import assert from 'node:assert/strict';
import test from 'node:test';

const { buildIncidentFlows, parseGroupKey, INCIDENT_FLAGS } = await import('../dist/services/incidentReadModel.js');

const S = '00000000-0000-4000-8000-000000000001';
const at = (minutes) => new Date(Date.UTC(2026, 10, 10, 1, 0, 0) + minutes * 60_000).toISOString().replace('Z', '000Z');
const row = (id, minutes, extra = {}) => ({ id, sourceId: S, criteriaVersion: 'incident-gap-v1',
  groupKey: 'response_code:E500', timeBasis: 'process_time_raw', openedAt: at(minutes), continuesFrom: null, mergedInto: null,
  lastMemberAt: at(minutes + 2), ...extra });
const shape = (flows) => flows.map((f) => ({ id: f.id, segments: f.segments.map((s) => [s.id, s.mergedIncidentIds, s.continuesFrom]), flags: f.flags,
  ...(f.truncated ? { truncated: true } : {}) }));

test('group key: kind and unescaped value (%3A then %25), no prefix → kind null', () => {
  assert.deepEqual(parseGroupKey('channel_id:CH%3A1%2541'), { kind: 'channel_id', value: 'CH:1%41', valueHash: null });
  assert.deepEqual(parseGroupKey('response_code:E-CONN'), { kind: 'response_code', value: 'E-CONN', valueHash: null });
  assert.deepEqual(parseGroupKey('nokey'), { kind: null, value: 'nokey', valueHash: null });
});

test('long group key (Detector 4.2 `<key>:sha256:<hex>`): value unknown (null), hash kept for lookup, never shown as the value', () => {
  const hex = 'a'.repeat(64);
  assert.deepEqual(parseGroupKey(`channel_id:sha256:${hex}`), { kind: 'channel_id', value: null, valueHash: hex });
  const flows = buildIncidentFlows([row('a', 0, { groupKey: `response_code:sha256:${hex}` })]);
  assert.deepEqual([flows[0].criteria.value, flows[0].criteria.longValue, flows[0].criteria.groupKey], [null, true, `response_code:sha256:${hex}`]);
  assert.equal(buildIncidentFlows([row('a', 0)])[0].criteria.longValue, false);
});

test('continuation chain of 3 is one flow ordered by opened_at; start/end span the chain', () => {
  const flows = buildIncidentFlows([row('c', 122, { continuesFrom: 'b' }), row('a', 0), row('b', 61, { continuesFrom: 'a' })]);
  assert.deepEqual(shape(flows), [{ id: 'a', segments: [['a', [], []], ['b', [], ['a']], ['c', [], ['b']]], flags: [] }]);
  assert.equal(flows[0].startAt, at(0));
  assert.equal(flows[0].endAt, at(124));
  assert.deepEqual([flows[0].criteria.kind, flows[0].criteria.value], ['response_code', 'E500']);
});

test('merge is resolved first: merged incident joins the target segment; root keeps its id even if opened later', () => {
  const flows = buildIncidentFlows([row('t', 300), row('m', 298, { mergedInto: 't', lastMemberAt: at(298) }), row('m2', 299, { mergedInto: 'm' })]);
  assert.deepEqual(shape(flows), [{ id: 't', segments: [['t', ['m', 'm2'], []]], flags: [] }]);
  assert.equal(flows[0].startAt, at(298), 'segment start = earliest opened_at in the merge group');
  assert.equal(flows[0].endAt, at(302));
});

test('continues_from of any incident in a merge group links the group (Detector 8절)', () => {
  const flows = buildIncidentFlows([row('a', 0), row('b', 61), row('m', 60, { mergedInto: 'b', continuesFrom: 'a' })]);
  assert.deepEqual(shape(flows), [{ id: 'a', segments: [['a', [], []], ['b', ['m'], ['a']]], flags: [] }]);
});

test('cross-version, cross-source, other group key and missing targets are ignored and flagged by cause', () => {
  const flows = buildIncidentFlows([
    row('x', 600, { continuesFrom: 'old', mergedInto: 'other' }),
    row('old', 540, { criteriaVersion: 'incident-gap-v0' }),
    row('other', 580, { groupKey: 'response_code:E901' }),
    row('y', 700, { continuesFrom: 'foreign' }),
    row('foreign', 650, { sourceId: '00000000-0000-4000-8000-000000000002' }),
    row('z', 800, { mergedInto: 'not-loaded' }),
  ]);
  const byId = Object.fromEntries(shape(flows).map((f) => [f.id, f]));
  assert.deepEqual(Object.keys(byId).sort(), ['foreign', 'old', 'other', 'x', 'y', 'z']);
  assert.deepEqual(byId.x.flags, ['merge_mismatch', 'continuation_mismatch']);
  assert.deepEqual(byId.y.flags, ['continuation_mismatch']);
  assert.deepEqual(byId.z.flags, ['merge_target_missing']);
  assert.deepEqual(byId.old.flags, []);
  assert.ok(flows.every((f) => f.truncated === false));
});

test('merge cycle: links in the cycle are dropped (each becomes its own segment), flagged', () => {
  const flows = buildIncidentFlows([row('p', 0, { mergedInto: 'q' }), row('q', 1, { mergedInto: 'p' }), row('r', 2, { mergedInto: 'p' })]);
  assert.deepEqual(shape(flows), [
    { id: 'p', segments: [['p', ['r'], []]], flags: ['merge_cycle'] },
    { id: 'q', segments: [['q', [], []]], flags: ['merge_cycle'] },
  ]);
  assert.deepEqual(shape(buildIncidentFlows([row('s', 0, { mergedInto: 's' })])).map((f) => f.flags), [['merge_cycle']], 'self merge is a cycle');
});

test('partial read (cap): links to rows outside the read window are not flagged missing; the flow is marked truncated', () => {
  const rows = [row('b', 61, { continuesFrom: 'a-old' }), row('c', 122, { continuesFrom: 'b' }), row('m', 125, { mergedInto: 't-old' }), row('n', 130)];
  const flows = buildIncidentFlows(rows, { partial: true });
  assert.deepEqual(shape(flows), [
    { id: 'b', segments: [['b', [], []], ['c', [], ['b']]], flags: [], truncated: true },
    { id: 'm', segments: [['m', [], []]], flags: [], truncated: true },
    { id: 'n', segments: [['n', [], []]], flags: [] },
  ]);
  const full = buildIncidentFlows(rows);
  assert.deepEqual(full.map((f) => f.flags), [['continuation_target_missing'], ['merge_target_missing'], []], 'without partial the same rows are flagged');
});

test('branching and a continuation that resolves into its own group are flagged, the component stays one flow', () => {
  // a ← b, a ← c(m은 c로 병합되며 a를 가리킴: b·c 두 뒤 구간) / d가 자기 묶음 사건을 가리키면 버림.
  const flows = buildIncidentFlows([row('a', 0), row('b', 61, { continuesFrom: 'a' }), row('c', 62), row('m', 63, { mergedInto: 'c', continuesFrom: 'a' }),
    row('d', 200), row('e', 201, { mergedInto: 'd', continuesFrom: 'd' })]);
  const byId = Object.fromEntries(shape(flows).map((f) => [f.id, f]));
  assert.deepEqual(byId.a.segments.map((s) => s[0]), ['a', 'b', 'c']);
  assert.deepEqual(byId.a.flags, ['continuation_branched']);
  assert.deepEqual(byId.d.flags, ['continuation_within_segment']);
});

test('directed continuation cycle (DB does not forbid) is one flow flagged continuation_cycle', () => {
  const flows = buildIncidentFlows([row('a', 0, { continuesFrom: 'b' }), row('b', 61, { continuesFrom: 'a' })]);
  assert.deepEqual(shape(flows), [{ id: 'a', segments: [['a', [], ['b']], ['b', [], ['a']]], flags: ['continuation_cycle'] }]);
  assert.ok(INCIDENT_FLAGS.includes('continuation_cycle'));
});

test('incident without members ends at its opened_at', () => {
  const [flow] = buildIncidentFlows([row('a', 0, { lastMemberAt: null })]);
  assert.equal(flow.endAt, flow.startAt);
});
