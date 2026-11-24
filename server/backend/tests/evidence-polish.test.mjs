// AI 판정 근거 요약 다듬기 접수(explain, summary-sentences-v1): 접수 입력 조립·정본 JSON·입력 hash·재사용 키(요청자 없음)·결과 판독·상태.
// 고정 hash는 Python json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False) + SHA-256으로 같은 값을 확인했다(설명용 값).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  POLISH_EVIDENCE_VERSION, POLISH_FAILURE_COOLDOWN_MINUTES, POLISH_LIMITS, POLISH_MAX_ATTEMPTS, POLISH_OUTPUT_SCHEMA_VERSION, POLISH_PROMPT_VERSION,
  buildPolishInput, canonicalJson, polishEvidenceHash, polishLlmSpec, polishStateOf, readPolished,
} from '../dist/services/evidencePolish.js';
import { buildEvidenceSummary } from '../dist/services/evidenceSummary.js';

const DECISION = { sourceId: '00000000-0000-4000-8000-000000000001', decisionId: '00000000-0000-4000-8000-000000000002', configVersionId: null };
const SENTENCE = '이상 규칙이 이번 처리 상태 F를 성공 상태 S와 비교했고, 달라 이상으로 판단했습니다.';
const summary = (over = {}) => ({ templateVersion: 'evidence-template-v1', sentences: [SENTENCE], stateWords: ['이상'], valueTokens: ['F'], ...over });

test('contract constants', () => {
  assert.deepEqual([POLISH_EVIDENCE_VERSION, POLISH_PROMPT_VERSION, POLISH_OUTPUT_SCHEMA_VERSION, POLISH_FAILURE_COOLDOWN_MINUTES, POLISH_MAX_ATTEMPTS],
    ['summary-sentences-v1', 'evidence-polish-v1', 'evidence-polish-result-v1', 10, 1]);
  assert.deepEqual(POLISH_LIMITS, { sentences: 8, sentenceLength: 300, totalLength: 1200, valueTokens: 16, valueTokenLength: 120 });
});

test('canonical JSON: sorted keys (code points), no spaces, non-ASCII as is, undefined dropped', () => {
  assert.equal(canonicalJson({ b: 1, a: [true, null, '한글'], c: undefined, A: { z: 'x', y: 'y' } }), '{"A":{"y":"y","z":"x"},"a":[true,null,"한글"],"b":1}');
  assert.equal(canonicalJson({ '\u{10400}': 1, '\u{FF21}': 2 }), '{"Ａ":2,"𐐀":1}', 'code point order, not UTF-16 order');
});

test('input, hash and reuse key: fixed example, no requester, sourceId outside the hash', () => {
  const input = buildPolishInput(DECISION, summary());
  assert.deepEqual(input, { kind: 'explain', ...DECISION, templateVersion: 'evidence-template-v1', sentences: [SENTENCE], stateWords: ['이상'], valueTokens: ['F'] });
  const spec = polishLlmSpec(input, 'fixture-polish-model');
  assert.deepEqual(spec, {
    evidence_version: 'summary-sentences-v1',
    input_evidence_hash: 'a6170143ce92e684c1a8d2adefa3e0e5605fba6f11bcf40cd324b90f005391be',
    model_version: 'fixture-polish-model', prompt_version: 'evidence-polish-v1', output_schema_version: 'evidence-polish-result-v1',
    reuse_key: 'e8963048ad477df067d6a160c1ea330d1b72c18e101ea5bcdae783345d0ad945',
  });
  assert.equal(polishLlmSpec({ ...input, sourceId: '00000000-0000-4000-8000-0000000000ff' }, 'fixture-polish-model').reuse_key, spec.reuse_key, 'sourceId is not in the key');
  assert.equal(polishLlmSpec.length, 2, 'the key has no requester argument');
  assert.notEqual(polishLlmSpec(input, 'other-model').reuse_key, spec.reuse_key, 'model version splits the key');
  assert.equal(polishLlmSpec(input, 'other-model').input_evidence_hash, spec.input_evidence_hash, 'model version is not in the input hash');
  assert.notEqual(polishEvidenceHash({ ...input, templateVersion: 'evidence-template-v2' }), spec.input_evidence_hash);
  assert.notEqual(polishEvidenceHash({ ...input, configVersionId: '00000000-0000-4000-8000-000000000003' }), spec.input_evidence_hash);
});

test('input limits: no sentence, too many/long sentences, value token missing from the text → not accepted', () => {
  const long = '가'.repeat(301);
  for (const over of [{ sentences: [] }, { sentences: Array(9).fill(SENTENCE) }, { sentences: [long] }, { sentences: Array(5).fill('가'.repeat(250)) },
    { valueTokens: Array(17).fill('F') }, { valueTokens: ['없는값'] }, { valueTokens: [''] }, { valueTokens: ['F'.repeat(121)] }])
    assert.equal(buildPolishInput(DECISION, summary(over)), null, JSON.stringify(over).slice(0, 60));
  assert.ok(buildPolishInput(DECISION, summary({ sentences: Array(4).fill('가'.repeat(300)), valueTokens: [] })), '1,200 code points is the limit');
  // 공용 벡터의 모든 이상 요약은 접수 입력이 된다(상한 안)
  const vectors = JSON.parse(fs.readFileSync(fileURLToPath(new URL('../../contracts/evidence-summary-vectors.json', import.meta.url)), 'utf8'));
  for (const c of vectors.cases) {
    const s = buildEvidenceSummary(c.input);
    if (s && c.input.evidence.composite.anomalous === true) assert.ok(buildPolishInput(DECISION, s), c.id);
  }
});

test('result reading: new schema, same decision and template only; old fact results are not shown', () => {
  const expected = { decisionId: DECISION.decisionId, templateVersion: 'evidence-template-v1' };
  const output = (over = {}) => ({ outputSchemaVersion: 'evidence-polish-result-v1', decisionId: DECISION.decisionId, templateVersion: 'evidence-template-v1',
    codeText: SENTENCE, paragraph: { text: '다듬은 문단입니다.', source: 'llm' }, check: { status: 'passed', code: null }, modelVersion: 'm-1', ...over });
  assert.deepEqual(readPolished(output(), expected), { text: '다듬은 문단입니다.', source: 'llm', check: { status: 'passed', code: null }, modelVersion: 'm-1' });
  assert.deepEqual(readPolished(output({ paragraph: { text: SENTENCE, source: 'code' }, check: { status: 'failed', code: 'polish_number_changed' }, modelVersion: undefined }), expected, 'col-model'),
    { text: SENTENCE, source: 'code', check: { status: 'failed', code: 'polish_number_changed' }, modelVersion: 'col-model' });
  for (const bad of [null, 'x', { facts: [], nextChecks: [] }, output({ outputSchemaVersion: 'fact-ids-checks-v1' }), output({ decisionId: 'other' }),
    output({ templateVersion: 'evidence-template-v0' }), output({ paragraph: { text: '', source: 'llm' } }), output({ paragraph: { text: 'x', source: 'model' } }), output({ paragraph: 'x' })])
    assert.equal(readPolished(bad, expected), null, JSON.stringify(bad)?.slice(0, 60));
});

test('polish state: done when a result exists, pending for a running job, none otherwise', () => {
  const polished = { text: 'x', source: 'llm', check: null, modelVersion: null };
  assert.deepEqual([polishStateOf(polished, true), polishStateOf(polished, false), polishStateOf(null, true), polishStateOf(null, false)], ['done', 'done', 'pending', 'none']);
});

// 워커 상수와 같은 값인지: 워커 파일이 같은 저장소에 있으면 직접 대조한다(없으면 건너뜀 — 워커 변경이 병합된 뒤 잡힌다).
test('versions match the worker constants when the worker file is present', (t) => {
  const workerFile = fileURLToPath(new URL('../../worker/evidence_polish.py', import.meta.url));
  if (!fs.existsSync(workerFile)) { t.skip('worker evidence_polish.py not in this checkout'); return; }
  const source = fs.readFileSync(workerFile, 'utf8');
  const constant = (name) => new RegExp(`^${name} = "([^"]+)"`, 'm').exec(source)?.[1];
  assert.deepEqual([constant('EVIDENCE_VERSION'), constant('PROMPT_VERSION'), constant('OUTPUT_SCHEMA_VERSION')],
    [POLISH_EVIDENCE_VERSION, POLISH_PROMPT_VERSION, POLISH_OUTPUT_SCHEMA_VERSION]);
  const keys = /INPUT_KEYS = frozenset\(\{([^}]*)\}\)/.exec(source)?.[1].match(/"([^"]+)"/g).map((k) => k.slice(1, -1)).sort();
  assert.deepEqual(keys, Object.keys(buildPolishInput(DECISION, summary())).sort(), 'input keys are exactly the worker keys');
});

test('worker-rejected input is filtered before intake (one function for intake and detail)', async () => {
  const { polishInputProblem } = await import('../dist/services/evidencePolish.js');
  assert.equal(polishInputProblem([SENTENCE], ['F']), null);
  for (const [sentences, tokens, code] of [[[], [], 'sentence_count'], [['  '], [], 'sentence_length'], [[`${SENTENCE}\n`], [], 'sentence_character'],
    [['가 나'], [], 'sentence_character'], [['가\u0085나'], [], 'sentence_character'], [['가\u000b나'], [], 'sentence_character'], [['가나'], [], 'sentence_character'],
    [['가나'], [], 'sentence_character'], [['앞 문장.', '뒤 문장.'], ['문장. 뒤'], 'value_token'], [[SENTENCE], Array(17).fill('F'), 'value_token_count']])
    assert.equal(polishInputProblem(sentences, tokens), code, JSON.stringify(sentences));
  assert.equal(buildPolishInput(DECISION, summary({ sentences: ['가나'], stateWords: [], valueTokens: [] })), null);
});

test('polishRequestable: only a polishable decision with state none and a ready polish model', async () => {
  const { polishInputFor, polishRequestableOf } = await import('../dist/services/evidencePolish.js');
  const item = (over = {}) => ({ kind: 'final', signal: 'yes', decisionId: DECISION.decisionId, sourceId: DECISION.sourceId, configVersionId: null, evidence: { schemaVersion: 4 }, ...over });
  const input = polishInputFor(item(), summary());
  assert.ok(input);
  assert.equal(polishRequestableOf(input, 'none', true), true);
  assert.deepEqual([polishRequestableOf(input, 'none', false), polishRequestableOf(input, 'pending', true), polishRequestableOf(input, 'done', true)], [false, false, false],
    'unknown/unavailable model, pending, done');
  for (const over of [{ signal: 'no' }, { signal: 'unknown' }, { kind: 'interim' }, { decisionId: null }, { evidence: { schemaVersion: 4, demo: { runKey: 'demo-1' } } }])
    assert.equal(polishRequestableOf(polishInputFor(item(over), summary()), 'none', true), false, JSON.stringify(over));
  assert.equal(polishRequestableOf(polishInputFor(item(), null), 'none', true), false, 'no summary');
  assert.equal(polishRequestableOf(polishInputFor(item(), summary({ sentences: ['가\n나'], stateWords: [], valueTokens: [] })), 'none', true), false, 'worker-rejected input');
});
