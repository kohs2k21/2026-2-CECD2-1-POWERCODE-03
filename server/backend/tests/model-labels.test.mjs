import assert from 'node:assert/strict';
import { test } from 'node:test';
import { frameOfMethod, mlMethod, modelLabel } from '../dist/ruleset/methodCatalog.js';
import { decisionModelLabels } from '../dist/services/analysisReadModel.js';

// 모델 이름표(analysis-ui.md `mlModels`, operations-ui.md `models[].methodName·frame`): 틀은 카탈로그 inputRequirements.unit으로만 정한다.
test('frame comes from inputRequirements.unit only', () => {
  assert.equal(frameOfMethod(mlMethod('iforest')), 'row', 'process_row → row');
  assert.equal(frameOfMethod(mlMethod('minibatch_kmeans')), 'row', 'executable.window=true does not make it a trend model');
  assert.equal(frameOfMethod(mlMethod('eif')), 'trend', 'aggregated_window → trend');
  assert.equal(frameOfMethod(undefined), null);
  assert.equal(frameOfMethod({ inputRequirements: { unit: 'other' } }), null);
});

test('modelLabel adds catalog name and frame, unknown keys stay null', () => {
  assert.deepEqual(modelLabel('iforest'), { modelKey: 'iforest', methodName: 'Isolation Forest', frame: 'row' });
  assert.deepEqual(modelLabel('lof'), { modelKey: 'lof', methodName: null, frame: null });
});

test('decisionModelLabels collects ML keys from stored evidence once, in order', () => {
  const v4 = { schemaVersion: 4, methodResults: [
    { methodId: 'hard:process-status', category: 'hard' },
    { methodId: 'ml:iforest', category: 'ml' },
    { methodId: 'ml:lof', category: 'ml' },
    { methodId: 'ml:', category: 'ml' },
  ], models: [{ modelKey: 'iforest' }] };
  assert.deepEqual(decisionModelLabels(v4), [
    { modelKey: 'iforest', methodName: 'Isolation Forest', frame: 'row' },
    { modelKey: 'lof', methodName: null, frame: null },
  ]);
  assert.deepEqual(decisionModelLabels({ schemaVersion: 3, models: [{ modelKey: 'iforest', anomalous: false }, { modelKey: '' }] }),
    [{ modelKey: 'iforest', methodName: 'Isolation Forest', frame: 'row' }], 'v3 models[]');
  assert.deepEqual(decisionModelLabels(null), []);
  assert.deepEqual(decisionModelLabels({ schemaVersion: 4, methodResults: [] }), []);
});
