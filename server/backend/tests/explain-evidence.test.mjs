// 설명 작업 접수 입력의 저장 판정 근거(savedEvidence): 기존 키는 그대로, 기여도가 있는 ML 방법 결과만 methodResults 네 키로 더한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';

const { attributionMethodResults, buildSavedEvidence } = await import('../dist/services/explainEvidence.js');

const KEYS = ['decisionId', 'configVersionId', 'status', 'ruleStatus', 'mlStatus', 'decisionCode', 'anomalyScore', 'inputManifestHash', 'rules', 'models'];
const row = (evidence, over = {}) => ({ id: 'd-1', config_version_id: null, status: 'partial', rule_status: 'succeeded', ml_status: 'succeeded',
  decision_code: 'SOFT_RETRY_COUNT_3F9A2C1D', anomaly_score: 0.71, evidence, input_manifest_hash: 'h'.repeat(64), ...over });
/** 변경 전 조립식 그대로(바이트 비교 기준). */
const legacy = (decision) => ({decisionId: decision.id, configVersionId: decision.config_version_id,
  status: decision.status, ruleStatus: decision.rule_status, mlStatus: decision.ml_status,
  decisionCode: decision.decision_code, anomalyScore: decision.anomaly_score === null ? null : String(decision.anomaly_score),
  inputManifestHash: decision.input_manifest_hash,
  rules: decision.evidence.rules ?? [], models: decision.evidence.models ?? []});
const attribution = { version: 'baseline-shapley-v1', status: 'skipped', reason: 'not_anomalous' };
const rules = [{ ruleKey: 'process-status', result: 'no_hit' }];
const models = [{ modelKey: 'iforest', anomalous: false }];

test('decisions without attribution keep the exact savedEvidenceJson', () => {
  for (const evidence of [
    { schemaVersion: 3, rules, models },
    { schemaVersion: 4, rules, models, methodResults: [{ methodId: 'ml:iforest', category: 'ml', status: 'succeeded', score: 0.3 }] },
    { schemaVersion: 4, rules, models, methodResults: [{ methodId: 'hard:process-status', category: 'hard', attribution }] },
    { schemaVersion: 4, methodResults: 'broken' },
    { schemaVersion: 4, methodResults: [null, 1, 'x', [], { category: 'ml' }] },
    {},
  ]) {
    const decision = row(evidence);
    const saved = buildSavedEvidence(decision);
    assert.deepEqual(Object.keys(saved), KEYS, JSON.stringify(evidence));
    assert.equal(JSON.stringify(saved), JSON.stringify(legacy(decision)), 'byte-identical JSON');
  }
  assert.equal(buildSavedEvidence(row({ rules }, { anomaly_score: null })).anomalyScore, null);
});

test('only ML method results with an attribution key are carried, as four keys, values as stored', () => {
  const succeeded = { version: 'baseline-shapley-v1', status: 'succeeded', scoreActual: 0.71, threshold: '0.62', players: [{ playerId: 'error_count', phi: 0.175, share: 0.5645161290322581 }] };
  const evidence = { schemaVersion: 4, rules, models, methodResults: [
    { methodId: 'hard:process-status', category: 'hard', status: 'succeeded', anomalous: false, attribution },
    { methodId: 'ml:iforest', category: 'ml', status: 'succeeded', anomalous: true, score: 0.7100000000000001, scoreDetail: { raw: 0.71 }, attribution: succeeded },
    { methodId: 'ml:old', category: 'ml', status: 'succeeded', anomalous: false, score: 0.3 },
    { methodId: 'ml:late', category: 'ml', status: 'not_ready', anomalous: null, score: null, attribution: { version: 'baseline-shapley-v1', status: 'skipped', reason: 'method_unresolved' } },
    { methodId: 'ml:noscore', category: 'ml', status: 'failed', attribution: { version: 'baseline-shapley-v1', status: 'failed', failureCode: 'exception', computeMs: 1.25 } },
    'not an object',
  ] };
  const saved = buildSavedEvidence(row(evidence));
  assert.deepEqual(Object.keys(saved), [...KEYS, 'methodResults'], 'methodResults goes after the existing keys');
  assert.deepEqual(saved.methodResults, [
    { methodId: 'ml:iforest', category: 'ml', score: 0.7100000000000001, attribution: succeeded },
    { methodId: 'ml:late', category: 'ml', score: null, attribution: evidence.methodResults[3].attribution },
    { methodId: 'ml:noscore', category: 'ml', attribution: evidence.methodResults[4].attribution },
  ]);
  assert.deepEqual(saved.methodResults.map((m) => Object.keys(m)), [['methodId', 'category', 'score', 'attribution'], ['methodId', 'category', 'score', 'attribution'],
    ['methodId', 'category', 'attribution']], 'score kept as stored (null stays null), absent score key stays absent');
  assert.equal(saved.methodResults[0].attribution, succeeded, 'attribution is the stored object, not a copy with changed values');
  assert.ok(JSON.stringify(saved).includes('"share":0.5645161290322581'), 'numbers are not rounded');
  const before = JSON.stringify(legacy(row(evidence)));
  assert.equal(JSON.stringify(saved).slice(0, before.length - 1), before.slice(0, -1), 'existing keys serialize exactly as before');
  assert.deepEqual(attributionMethodResults(null), []);
});
