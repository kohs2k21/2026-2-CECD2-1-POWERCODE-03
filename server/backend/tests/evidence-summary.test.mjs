// AI 판정 근거 요약(evidenceSummary): 공용 시험 벡터 전부 대조 + 값 표기·조사 유틸 + 기여도 판독 + 깨진 근거에서 예외 없음.
// 벡터는 server/contracts/evidence-summary-vectors.json(설명용 합성 수치).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildEvidenceSummary, contributionStatusOf, evidenceSummaryNeeds, judgmentWords } from '../dist/services/evidenceSummary.js';
import { NOTICE, RESPONSE_MEANING_CHECK_LIMIT, STATUS_LABELS, TEMPLATE_VERSION, shortMeaning, statusText } from '../dist/services/evidenceSummaryTemplates.js';
import { because, displayNumber, expandExponent, groupDigits, objectParticle, percentText, roundHalfUp, subject, topic, toward, withWord } from '../dist/services/koreanText.js';
import { readAttribution } from '../dist/services/mlAttribution.js';

const vectors = JSON.parse(fs.readFileSync(fileURLToPath(new URL('../../contracts/evidence-summary-vectors.json', import.meta.url)), 'utf8'));

test('vectors: template version and notice match the template file', () => {
  assert.equal(vectors.templateVersion, TEMPLATE_VERSION);
  assert.equal(vectors.notice, NOTICE);
  assert.equal(new Set(vectors.cases.map((c) => c.id)).size, vectors.cases.length, 'case ids are unique');
});

for (const c of vectors.cases) {
  test(`vector ${c.id}`, () => {
    const actual = buildEvidenceSummary(c.input);
    if (c.expected === null) return assert.equal(actual, null);
    const { templateVersion, notice, ...rest } = actual;
    assert.equal(templateVersion, vectors.templateVersion);
    assert.equal(notice, vectors.notice);
    assert.deepEqual(rest, c.expected);
  });
}

test('vectors cover every summary kind of the template appendix', () => {
  const kinds = new Set(vectors.cases.flatMap((c) => (c.expected ? [c.expected.summaryLine.kind] : [])));
  for (const kind of ['hard_process_status', 'hard_negative_duration', 'hard_success_with_error', 'hard_count_conservation', 'hard_transaction_failed',
    'hard_unregistered_interface', 'hard_protocol_path', 'relation_precedes', 'relation_message_handoff', 'relation_required_step', 'soft_elapsed', 'soft_count',
    'soft_value', 'ml_value', 'ml_elapsed_longer', 'ml_elapsed_shorter', 'ml_count', 'ml_error_ratio', 'ml_group', 'ml_no_contribution', 'normal_complete', 'normal_unconfirmed'])
    assert.ok(kinds.has(kind), kind);
  assert.ok(vectors.cases.some((c) => c.expected?.checks.some((k) => k.code === 'unresolved')), 'unresolved method case');
  assert.ok(vectors.cases.some((c) => c.expected?.sentences.some((s) => s.startsWith('그 밖의 근거'))), 'overflow case');
});

test('screen words: no internal codes or banned labels in any shown string', () => {
  // `건` 낱말 자체는 이름표(`오류 건수(ERROR_COUNT)`)에 쓴다(v2 — 금지어 `건` 해제). 숫자 뒤 단위 `3건`은 여전히 쓰지 않는다.
  // v2에서 없앤 군말·옛 말(`비교했고`·`이번 `·`원천 시스템`·`오류 비율 값`·`(영향 n%)`)도 나오지 않는다
  const banned = [/hard:/, /soft:/, /ml:/, /PROCESS_STATUS_/, /higher_is_anomaly/, /Shapley/i, /리비전/, /phi/, /총 건수/, /오류율/, /재시도 수/, /많다|적다/, /\d ?건/,
    /비교했고/, /이번 (?!값\))/, /원천 시스템/, /오류 비율 값/, /영향 \d/];
  for (const c of vectors.cases) {
    const s = buildEvidenceSummary(c.input);
    if (!s) continue;
    const shown = [s.summaryLine.prefix, s.summaryLine.text, ...s.sentences, ...s.checks.map((k) => k.text), s.notice, s.contributionNote ?? '',
      ...s.table.flatMap((r) => (r.kind === 'rule' ? [r.condition, r.result, ...r.observed.flatMap((o) => [o.name ?? '', o.value, o.unit ?? ''])]
        : [r.impactPercent, ...r.members.flatMap((m) => [m.name, m.actual, m.usual, m.unit ?? ''])]))];
    for (const text of shown) for (const pattern of banned) assert.ok(!pattern.test(text), `${c.id}: ${pattern} in ${text}`);
    for (const text of shown) assert.ok(!text.includes('(단위 미확인)'), `${c.id}: table values carry no unit note`);
  }
});

test('valueTokens: source strings only, verbatim in the sentences, within limits', () => {
  for (const c of vectors.cases) {
    const s = buildEvidenceSummary(c.input);
    if (!s) continue;
    const text = s.sentences.join(' ');
    assert.ok(s.valueTokens.length <= 16, c.id);
    for (const token of s.valueTokens) {
      assert.ok(token.length <= 120 && text.includes(token), `${c.id}: ${token}`);
      assert.ok(!/^-?\d[\d,]*(\.\d+)?$/.test(token), `${c.id}: numbers are not value tokens (${token})`);
    }
  }
  // 상한을 넘는 값은 담지 않는다(문장에는 그대로 있음)
  const long = 'P'.repeat(121);
  const evidence = { schemaVersion: 4, composite: { anomalous: true, grade: 'anomaly', risk: { status: 'rated', level: 'critical', determinedBy: ['hard:unregistered-interface'] } },
    methodResults: [{ methodId: 'hard:unregistered-interface', category: 'hard', status: 'succeeded', anomalous: true, code: 'PROTO_INTERFACE_UNREGISTERED', evidence: [] }],
    protocolReview: { interfaceId: long } };
  const s = buildEvidenceSummary({ evidence });
  assert.ok(s.sentences[0].includes(long));
  assert.deepEqual(s.valueTokens, []);
});

test('judgment words: masked names and value tokens are not judgment words, sequence keeps duplicates', () => {
  assert.deepEqual(judgmentWords('이상 규칙이 확인했고 이상으로 판단했습니다. 이상 징후 규칙이 이상 징후로 판단했습니다.', []), ['이상', '이상 징후']);
  assert.deepEqual(judgmentWords('관계 규칙이 정상단계와 비교했고 이상으로 판단했습니다.', ['정상단계']), ['이상']);
  assert.deepEqual(judgmentWords('켠 검사가 모두 정상으로 끝났습니다. 일부 검사(머신러닝 모델)는 평가 불가입니다.', []), ['정상', '평가 불가']);
});

// ---- 값 표기·조사(이전 화면 유틸을 옮김) ----
test('number text: exponent expansion, thousands, half-up, negative zero', () => {
  assert.deepEqual(['9.5E+3', '1E-7', '-1.25E+2', '0E-10', '1.23456789012345678901E+25', '120', 'abc', '1E+x', '-0.5'].map(groupDigits),
    ['9,500', '0.0000001', '-125', '0.0000000000', '12,345,678,901,234,567,890,100,000', '120', 'abc', '1E+x', '-0.5']);
  assert.equal(expandExponent('1.5E-3'), '0.0015');
  assert.equal(groupDigits('10485760'), '10,485,760');
  assert.deepEqual(['-0', '-0.0', '-0.000'].map(groupDigits), ['0', '0.0', '0.000'], 'zero has no sign');
  assert.deepEqual([roundHalfUp('0.0005', 3), roundHalfUp('2.675', 2), roundHalfUp('-0.0004', 3), roundHalfUp('0.62', 3)], ['0.001', '2.68', '0.000', '0.620']);
  assert.deepEqual([roundHalfUp('-0.0005', 3), roundHalfUp('-0', 3), roundHalfUp('1e-05', 5), roundHalfUp('6.2E-1', 3), roundHalfUp('abc', 3), roundHalfUp('1E+999', 3)],
    ['-0.001', '0.000', '0.00001', '0.620', 'abc', '1E+999']);
  assert.deepEqual([percentText(0.125), percentText(0.0484), percentText(0.005), percentText(1.04), percentText(0.62)], ['13%', '5%', '1%', '104%', '62%']);
});

test('display digits: ms one decimal, others 4 significant digits, shorter values unchanged, no exponent', () => {
  assert.deepEqual(['800.0000000000001', '1234.56', '1234.04', '4200', '0.05', '12.25', '-0.04'].map((v) => displayNumber(v, 'ms')),
    ['800', '1,234.6', '1,234', '4,200', '0.1', '12.3', '0'], 'ms: one decimal half-up, trailing zero and point removed, zero has no sign');
  assert.deepEqual(['0.3333333333333333333333333333', '0.125', '0.10', '12.34567', '12345.678', '0.000123456', '1E-7', '9.5E+3', '0', '10000000', 'abc'].map((v) => displayNumber(v)),
    ['0.3333', '0.125', '0.10', '12.35', '12,346', '0.0001235', '0.0000001', '9,500', '0', '10,000,000', 'abc']);
});

test('particles: final consonant, numbers read aloud, Latin L·M·N·NG, units, trailing parentheses', () => {
  assert.deepEqual(['0.630', '0.512', '0.517', '0.516', '10', '1,000', '4,200 ms', '0.500', 'N', 'F', 'E901'].map(toward),
    ['0.630으로', '0.512로', '0.517로', '0.516으로', '10으로', '1,000으로', '4,200 ms로', '0.500으로', 'N으로', 'F로', 'E901로']);
  assert.deepEqual(['처리 시간', 'ERROR_COUNT', 'LEVEL', 'SCORE_M', 'BAR', 'SONG', 'ERROR_COUNT(단위 미확인)', '800 ms', 'E901'].map(objectParticle),
    ['을', '를', '을', '을', '를', '을', '를', '를', '을']);
  assert.deepEqual(['처리 시간', 'IF_NEW', 'IF_A_PR009', '처리량(TOTAL_COUNT)'].map(subject), ['처리 시간이', 'IF_NEW가', 'IF_A_PR009가', '처리량(TOTAL_COUNT)이']);
  assert.deepEqual(['오류 건수(ERROR_COUNT)', '재시도 횟수(RETRY_COUNT)', 'E101(예시 뜻)', '9999(예시 뜻)'].map(subject),
    ['오류 건수(ERROR_COUNT)가', '재시도 횟수(RETRY_COUNT)가', 'E101(예시 뜻)이', '9999(예시 뜻)가'], 'particle follows the word before the parentheses');
  assert.deepEqual(['이상 규칙', '관계 규칙', '이상 징후 규칙', '머신러닝 모델', '이상 징후 규칙·머신러닝 모델'].map(topic),
    ['이상 규칙은', '관계 규칙은', '이상 징후 규칙은', '머신러닝 모델은', '이상 징후 규칙·머신러닝 모델은']);
  // 상태 조사는 괄호 앞 코드 기준: F·P·H·Z·X는 받침 없음, N은 받침
  assert.deepEqual(['F', 'P', 'N', 'H', 'Z', 'X'].map((code) => because(statusText(code))),
    ['F(실패)여서', 'P(처리중)여서', 'N(미처리)이어서', 'H(부분성공)여서', 'Z(데이터없음)여서', 'X여서']);
  assert.deepEqual(['F', 'N'].map((code) => toward(statusText(code))), ['F(실패)로', 'N(미처리)으로']);
  assert.deepEqual(STATUS_LABELS, ['S(성공)', 'F(실패)', 'P(처리중)', 'N(미처리)', 'H(부분성공)', 'Z(데이터없음)']);
  assert.deepEqual(['800 ms', '3,000 ms', '0.1', 'IF_A_PR001', '정해진 앞 단계'].map(withWord), ['800 ms와', '3,000 ms와', '0.1과', 'IF_A_PR001과', '정해진 앞 단계와']);
});

// ---- 기여도 판독(이전 화면 판독을 옮김) ----
const player = (playerId, features, actual, baseline, phi, share, extra = {}) => ({ playerId, features, actual, baseline, phi, share, thresholdGapShare: null, ...extra });
const T = player('total_count', ['total_count'], { total_count: '120' }, { total_count: 100 }, 0.015, 0.0484);
const E = player('error_count', ['error_count'], { error_count: '15' }, { error_count: 0 }, 0.175, 0.5645);
const L = player('elapsed_ms', ['elapsed_ms'], { elapsed_ms: '4200' }, { elapsed_ms: 800 }, 0.12, 0.3871);
const base = (over = {}) => ({
  version: 'baseline-shapley-v1', status: 'succeeded', baselineKind: 'train_median_robust_scaler_center', grouping: 'source-field-union-v1',
  scoreActual: 0.71, scoreBaseline: 0.4, threshold: '0.62', direction: 'higher_is_anomaly', baselineWarning: false, efficiencyError: 0,
  players: [T, E, L], topK: ['error_count', 'elapsed_ms', 'total_count'],
  counterfactual: [
    { replaced: ['error_count'], scoreAfter: 0.51, warningAfterReplacement: false },
    { replaced: ['error_count', 'elapsed_ms'], scoreAfter: 0.41, warningAfterReplacement: false },
    { replaced: ['error_count', 'elapsed_ms', 'total_count'], scoreAfter: 0.4, warningAfterReplacement: false },
  ],
  clearedAtK: 1, pins: {}, computeMs: 0.8, ...over,
});
const read = (attribution) => readAttribution({ methodId: 'ml:iforest', category: 'ml', attribution });

test('attribution reader: absent, skipped, failed, malformed', () => {
  assert.deepEqual(readAttribution({ methodId: 'ml:iforest' }), { status: 'absent' });
  assert.deepEqual(read({ version: 'baseline-shapley-v1', status: 'skipped', reason: 'too_many_players' }), { status: 'skipped', reason: 'too_many_players' });
  assert.deepEqual(read({ version: 'baseline-shapley-v1', status: 'skipped', reason: 'new_reason' }), { status: 'skipped', reason: null });
  assert.deepEqual(read({ version: 'baseline-shapley-v1', status: 'failed', failureCode: 'score_not_reproduced', computeMs: 1 }), { status: 'failed', failureCode: 'score_not_reproduced' });
  for (const bad of [null, 'x', { status: 'succeeded' }, { version: 'baseline-shapley-v2', status: 'skipped', reason: 'not_anomalous' }, { version: 'baseline-shapley-v1', status: 'done' },
    base({ scoreActual: '0.71' }), base({ threshold: 0.62 }), base({ topK: ['missing'] }), base({ clearedAtK: 2 }), base({ counterfactual: [] }),
    base({ players: [{ ...T, actual: { total_count: 120 } }, E, L] }), base({ players: [{ ...T, share: undefined }, E, L] }),
    base({ counterfactual: base().counterfactual.map((c) => ({ ...c, replaced: ['error_count'] })) })])
    assert.equal(read(bad).status, 'unreadable', JSON.stringify(bad).slice(0, 80));
  for (const threshold of ['0x10', 'Infinity', ' 0.62', '0.62 ', '1e', '.5', 'NaN']) assert.equal(read(base({ threshold })).status, 'unreadable', threshold);
  assert.equal(read(base({ threshold: '6.2E-1' })).status, 'succeeded');
  assert.equal(read(base({ players: [{ ...T, scaleIsUnit: 'x' }, E, L] })).status, 'succeeded', 'malformed scaleIsUnit is ignored');
  assert.deepEqual(read(base({ players: [{ ...T, scaleIsUnit: { total_count: true, x: 'y' } }, E, L] })).players[0].scaleIsUnit, { total_count: true });
});

test('contribution status precedence: computed > failed > unreadable > skipped, none = not_computed', () => {
  const m = (attribution) => ({ methodId: 'ml:a', category: 'ml', attribution });
  assert.equal(contributionStatusOf([]), 'not_computed');
  assert.equal(contributionStatusOf([{ methodId: 'ml:a', category: 'ml' }, { methodId: 'hard:process-status', category: 'hard', attribution: base() }]), 'not_computed');
  assert.equal(contributionStatusOf([m({ version: 'baseline-shapley-v1', status: 'skipped', reason: 'not_anomalous' }), m('bad')]), 'unreadable');
  assert.equal(contributionStatusOf([m('bad'), m({ version: 'baseline-shapley-v1', status: 'failed', failureCode: 'exception' })]), 'failed');
  assert.equal(contributionStatusOf([m({ version: 'baseline-shapley-v1', status: 'failed', failureCode: 'exception' }), m(base())]), 'computed');
});

// ---- 견고성: 어떤 근거에서도 예외 없이 요약 또는 null ----
test('robustness: broken evidence never throws', () => {
  const sample = vectors.cases.find((c) => c.id === 'example_c_soft_elapsed_and_ml').input.evidence;
  const clone = () => structuredClone(sample);
  const variants = [undefined, null, 'x', 3, [], {}, { schemaVersion: 3 }, { schemaVersion: 4 }, { schemaVersion: 4, methodResults: {}, composite: {} },
    { schemaVersion: 4, methodResults: [], composite: null }, { schemaVersion: 4, methodResults: [null, 1, 'x', []], composite: { anomalous: true } }];
  for (const mutate of [
    (e) => { e.composite.risk = 'x'; }, (e) => { e.composite.basisMethods = 'x'; }, (e) => { e.composite.risk.determinedBy = [null, 3]; },
    (e) => { e.methodResults[0].condition = null; }, (e) => { e.methodResults[0].condition.operator = 'between'; }, (e) => { e.methodResults[0].observedValue = { v: 1 }; },
    (e) => { e.methodResults[1].attribution.players = 'x'; }, (e) => { e.methodResults[1].attribution.topK = ['nobody']; },
    (e) => { e.methodResults[1].attribution.players[0].actual = { elapsed_ms: 'abc' }; }, (e) => { e.methodResults[1].category = 'unknown'; },
    (e) => { e.methodResults[0].methodId = 'soft:'; e.methodResults[0].condition.field = 'constructor'; },
    (e) => { e.methodResults.push({ methodId: 'hard:rel-00000000', category: 'hard', status: 'succeeded', anomalous: true, code: 'REL_PRECEDES', evidence: 'x' }); },
    (e) => { e.methodResults.push({ methodId: 'hard:future-rule', category: 'hard', status: 'succeeded', anomalous: true, code: 'HARD_FUTURE' }); },
    (e) => { e.protocolReview = { routes: 'x' }; e.methodResults.push({ methodId: 'hard:proto-00000000', category: 'hard', status: 'succeeded', anomalous: true }); },
    (e) => { e.methodResults[0].category = 'constructor'; }, (e) => { e.methodResults[0].category = 'toString'; e.methodResults[1].category = '__proto__'; },
    (e) => { e.composite.anomalous = 'yes'; }, (e) => { e.methodResults = e.methodResults.map((m) => ({ ...m, anomalous: false })); },
  ]) { const e = clone(); mutate(e); variants.push(e); }
  for (const evidence of variants) {
    for (const extra of [{}, { observedFields: 'x', relationRules: 'x', interfaceId: 3 }, { observedFields: { errorCount: {} }, relationRules: [null, { id: 3 }] }]) {
      let result;
      assert.doesNotThrow(() => { result = buildEvidenceSummary({ evidence, ...extra }); }, JSON.stringify(evidence)?.slice(0, 80));
      if (result !== null) {
        assert.equal(typeof result.summaryLine.text, 'string');
        assert.ok(Array.isArray(result.sentences) && result.sentences.every((s) => typeof s === 'string'));
        assert.ok(result.checks.length <= 2);
      }
    }
  }
  // 상속 속성 이름의 대분류(constructor·toString·__proto__)는 모르는 대분류로 빠진다(함수 글자가 문장에 실리지 않음)
  for (const category of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
    const e = clone();
    e.methodResults = e.methodResults.map((m) => ({ ...m, category }));
    const s = buildEvidenceSummary({ evidence: e });
    assert.equal(s, null, category);
    const mixed = clone();
    mixed.methodResults.push({ ...mixed.methodResults[0], methodId: 'x:1', category });
    const text = JSON.stringify(buildEvidenceSummary({ evidence: mixed }));
    assert.ok(!/native code|function/.test(text), `${category}: ${text.slice(0, 80)}`);
  }
  // 요약할 근거 방법이 하나도 없는 이상 = null(카드 없음), 모르는 하드룰만 있는 이상도 null
  const unknownOnly = { schemaVersion: 4, composite: { anomalous: true, grade: 'anomaly', risk: { status: 'rated', level: 'critical', determinedBy: ['hard:future-rule'] } },
    methodResults: [{ methodId: 'hard:future-rule', category: 'hard', status: 'succeeded', anomalous: true, code: 'HARD_FUTURE' }] };
  assert.equal(buildEvidenceSummary({ evidence: unknownOnly }), null);
  // 모르는 위험 단계·등급 = 위험도 미정
  const unrated = clone();
  unrated.composite.risk = { status: 'rated', level: 'severe' };
  unrated.composite.grade = 'odd';
  assert.deepEqual([buildEvidenceSummary({ evidence: unrated }).summaryLine.prefix, buildEvidenceSummary({ evidence: unrated }).summaryLine.prefixKind], ['위험도 미정', 'risk']);
});

test('detail read needs: observation row only for time/count hard rules, rule set revision only for relation rules', () => {
  const hit = (methodId) => ({ methodId, status: 'succeeded', anomalous: true });
  const none = { observed: false, ruleSetRevision: null, responseCode: null };
  assert.deepEqual(evidenceSummaryNeeds(null), none);
  assert.deepEqual(evidenceSummaryNeeds({ schemaVersion: 4, ruleSetRevision: 3, methodResults: [hit('hard:process-status'), hit('soft:x-1')] }), none);
  assert.deepEqual(evidenceSummaryNeeds({ schemaVersion: 4, ruleSetRevision: 3, methodResults: [hit('hard:negative-duration'), hit('hard:rel-0a1b2c3d')] }),
    { ...none, observed: true, ruleSetRevision: 3 });
  assert.deepEqual(evidenceSummaryNeeds({ schemaVersion: 4, ruleSetRevision: '3', methodResults: [hit('hard:rel-0a1b2c3d'), { ...hit('hard:count-conservation'), anomalous: false }] }), none);
  // 응답 코드 사전 뜻: 거래 실패가 근거일 때만, 근거 첫 항목의 코드(사전 코드 형식 밖이면 읽지 않음)
  const failed = (responseCode, over = {}) => ({ ...hit('hard:transaction-failed'), evidence: [{ result: 'hit', responseCode }], ...over });
  assert.deepEqual(evidenceSummaryNeeds({ schemaVersion: 4, methodResults: [failed('E101')] }), { ...none, responseCode: 'E101' });
  for (const methods of [[failed('E101', { anomalous: false })], [failed(null)], [failed('')], [failed(' E101')], [failed('E 101')], [failed('가나')], [failed('E'.repeat(65))],
    [{ ...hit('hard:transaction-failed'), evidence: 'x' }]])
    assert.deepEqual(evidenceSummaryNeeds({ schemaVersion: 4, methodResults: methods }), none, JSON.stringify(methods).slice(0, 80));
});

test('response code meaning: check line capped at 40 code points with an ellipsis, table in full, paragraph unchanged', () => {
  assert.equal(RESPONSE_MEANING_CHECK_LIMIT, 40);
  const forty = '가'.repeat(40);
  const wide = '\u{1F600}';
  assert.deepEqual([shortMeaning(forty), shortMeaning(`${forty}나`), shortMeaning(wide.repeat(41))], [forty, `${forty}…`, `${wide.repeat(40)}…`]);
  const base = vectors.cases.find((c) => c.id === 'hard_transaction_failed').input;
  const plain = buildEvidenceSummary(base);
  const meant = buildEvidenceSummary({ ...base, responseCode: { code: 'E901', message: `${forty}나` } });
  assert.equal(meant.checks[0].text, `해당 업무 시스템에서 응답 코드 E901(${forty}…)이 나온 경위를 확인하세요(화면: 위 표 이번 값).`);
  assert.deepEqual(meant.table[0].observed.map((o) => o.value), ['F(실패)', `E901(${forty}나)`]);
  assert.deepEqual([meant.sentences, meant.stateWords, meant.valueTokens, meant.summaryLine], [plain.sentences, plain.stateWords, plain.valueTokens, plain.summaryLine],
    'polish input (sentences, state words, value tokens) does not depend on the dictionary');
  for (const responseCode of [null, 'x', { code: 'E901', message: null }, { code: 'E901', message: 3 }, { code: 'E902', message: '뜻' }, { code: 'E901', message: '' }])
    assert.equal(buildEvidenceSummary({ ...base, responseCode }).checks[0].text, plain.checks[0].text, JSON.stringify(responseCode));
});

test('soft rule comparison words: picked once from stored value vs threshold, symbols only in the fallback', () => {
  const soft = (operator, observedValue, threshold = 3000) => {
    const input = structuredClone(vectors.cases.find((c) => c.id === 'example_c_soft_elapsed_and_ml').input);
    input.evidence.methodResults = input.evidence.methodResults.filter((m) => m.category === 'soft');
    Object.assign(input.evidence.methodResults[0], { observedValue });
    input.evidence.methodResults[0].condition = { ...input.evidence.methodResults[0].condition, operator, threshold };
    input.evidence.composite.basisMethods = [input.evidence.methodResults[0].methodId];
    return buildEvidenceSummary(input).sentences[0];
  };
  const word = (sentence) => (/넘어|이르러|못 미쳐|해당해/.exec(sentence) ?? [null])[0];
  const table = [['gt', 4200, '넘어'], ['gt', 3000, '해당해'], ['gt', 1000, '해당해'], ['gte', 4200, '넘어'], ['gte', 3000, '이르러'], ['gte', 2999.9, '해당해'],
    ['lte', 1000, '못 미쳐'], ['lte', 3000, '이르러'], ['lte', 3000.1, '해당해'], ['lt', 1000, '못 미쳐'], ['lt', 3000, '해당해'], ['gte', '3.0E+3', '이르러'],
    ['gte', '3000.000', '이르러'], ['gte', null, '해당해'], ['gte', 'abc', '해당해'], ['between', 4200, '해당해']];
  for (const [operator, value, expected] of table) assert.equal(word(soft(operator, value)), expected, `${operator} ${value}`);
  assert.equal(soft('gte', 3000), '이상 징후 규칙은 처리 시간이 3,000 ms로 기준 3,000 ms에 이르러 이상 징후로 판단했습니다.');
  assert.equal(soft('gt', 1000), '이상 징후 규칙은 처리 시간이 기준(> 3,000 ms)에 해당해 이상 징후로 판단했습니다.', 'contradiction: symbol fallback');
  assert.equal(soft('between', 4200), '이상 징후 규칙은 처리 시간이 기준에 해당해 이상 징후로 판단했습니다.', 'unknown operator: no symbol, no threshold');
  assert.equal(word(soft('gte', '3000.04')), '넘어', 'same display text, raw value relation decides');
  assert.equal(word(soft('gte', '100000000000000000000000000001', '100000000000000000000000000000')), '넘어', 'exact decimal comparison');
});

test('same display text after rounding: no number template (table keeps the values)', () => {
  for (const id of ['ml_elapsed_same_display', 'ml_error_ratio_same_display']) {
    const s = buildEvidenceSummary(vectors.cases.find((c) => c.id === id).input);
    assert.equal(s.summaryLine.kind, 'ml_value', id);
    assert.ok(!/\d/.test(s.sentences[0].replace(/\(\d+%\)/, '')), `${id}: ${s.sentences[0]}`);
    const member = s.table[0].members[0];
    assert.equal(member.actual, member.usual, 'table shows the same display text as is');
  }
});
