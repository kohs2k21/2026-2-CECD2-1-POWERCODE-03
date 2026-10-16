import assert from 'node:assert/strict';
import { test } from 'node:test';
import { featureSetCapability, TRAINER_CAPABILITIES } from '../dist/routes/trainerContract.js';

const field = { op: 'field', field: 'P.total_count' };
const member = (expression = field, requiresFit = false) => ({ name: 'count_value', position: 0, expression, requiresFit });
const accepted = value => assert.equal(featureSetCapability([value]).supported, true);
const rejected = value => assert.equal(featureSetCapability([value]).supported, false);

test('catalog lists exactly the supported recipe and four evaluation families', () => {
  assert.deepEqual(TRAINER_CAPABILITIES.models.map(x => x.key), ['iforest']);
  assert.deepEqual(TRAINER_CAPABILITIES.evaluationFamilies, ['rule_ml', 'synthetic', 'execution_cost', 'operational']);
});
test('plain, arithmetic, fitted, and typed derived expressions are discoverable', () => {
  accepted(member());
  accepted(member({ op: 'ratio', left: field, right: field }));
  for (const op of ['group_median', 'group_centered']) accepted(member({ op, groupBy: 'P.adapter_type', value: field }, true));
  accepted(member({ op: 'derived', outputType: 'boolean', unit: 'boolean', provenance: {},
    ast: { op: 'is_missing', args: [{ op: 'column', table: 'EC_PROCESS_MONITORING', column: 'END_TIME' }] } }));
});
test('invalid source, AST, group, and requiresFit never claim support', () => {
  for (const expression of [
    { op: 'field', field: 'P.password' },
    { op: 'field', field: 'P.total_count', ignored: true },
    { op: 'group_median', groupBy: 'P.adapter_type' },
    { op: 'add', left: { op: 'execute' }, right: field },
    { op: 'group_centered', groupBy: 'P.channel_id', value: field },
    { op: 'add', left: { op: 'group_median', groupBy: 'P.adapter_type', value: field }, right: field },
  ]) rejected(member(expression, expression.op.startsWith('group_')));
  rejected(member(field, true));
  rejected(member({ op: 'group_median', groupBy: 'P.adapter_type', value: field }, false));
});
test('invalid feature ordering, duplicate output names, empty and oversized sets reject', () => {
  for (const members of [[], [{ ...member(), position: 1 }], [{ ...member(), name: 'bad-name' }], [{ ...member(), name: 'valid_name\n' }],
    [member(), { ...member(), position: 1 }], Array.from({ length: 33 }, (_, i) => ({ ...member(), name: `value_${i}`, position: i }))]) {
    assert.equal(featureSetCapability(members).supported, false);
  }
});
test('derived units and bounded tree depth are enforced', () => {
  const time = column => ({ op: 'column', table: 'EC_PROCESS_MONITORING', column });
  const ast = { op: 'datetime_diff', args: [time('END_TIME'), time('START_TIME')], unit: 'ms' };
  accepted(member({ op: 'derived', ast, outputType: 'number', unit: 'ms', provenance: {} }));
  rejected(member({ op: 'derived', ast, outputType: 'number', unit: 's', provenance: {} }));
  let expression = field;
  for (let i = 0; i < 4; i++) expression = { op: 'add', left: expression, right: field };
  rejected(member(expression));
});
