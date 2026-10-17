import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseSyntheticEvaluation, parseSyntheticRegistration, verifySyntheticBinding,
  SYNTHETIC_PROFILES, staticSpecHash } from '../dist/routes/syntheticEvaluationContract.js';

const request = () => ({ profileId: 'esb-process-research-v1', profileVersion: '1',
  scenarios: [{ scenario: 'P04', severity: 1 }], seed: 11, repetitions: 3, targetRows: 10 });
const reject = (value, code) => assert.throws(() => parseSyntheticEvaluation(value), error => error.code === code);

test('versioned request preserves only supported deterministic bounded settings', () => {
  assert.deepEqual(parseSyntheticEvaluation(request()), request());
  assert.deepEqual(parseSyntheticEvaluation({ ...request(), seed: 2147483647, repetitions: 5, targetRows: 100 }),
    { ...request(), seed: 2147483647, repetitions: 5, targetRows: 100 });
  for (const [key, value] of [['seed', true], ['seed', -1], ['seed', 2147483648], ['seed', NaN],
    ['repetitions', 0], ['repetitions', 6], ['targetRows', 0], ['targetRows', 101], ['targetRows', 1.5]]) {
    reject({ ...request(), [key]: value }, 'synthetic_invalid_bounds');
  }
});
test('unknown fields, unsupported profiles, ambiguous P01 and duplicates reject', () => {
  reject({ ...request(), ignored: true }, 'synthetic_invalid_request');
  reject({ ...request(), profileId: '__proto__' }, 'synthetic_unsupported_profile');
  reject({ ...request(), profileVersion: 1 }, 'synthetic_unsupported_profile');
  reject({ ...request(), scenarios: [{ scenario: 'P01', severity: 1 }] }, 'synthetic_P01_semantics_unconfirmed');
  reject({ ...request(), scenarios: [{ scenario: 'P04', severity: true }] }, 'synthetic_unsupported_scenario');
  reject({ ...request(), scenarios: [{ scenario: 'P04', severity: 1, ignored: 1 }] }, 'synthetic_invalid_scenario');
  reject({ ...request(), scenarios: [...request().scenarios, ...request().scenarios] }, 'synthetic_duplicate_scenario');
  reject({ ...request(), scenarios: [] }, 'synthetic_invalid_bounds');
  reject({ ...request(), scenarios: Array(5).fill(request().scenarios[0]) }, 'synthetic_invalid_bounds');
});
test('ADBench exposes numeric distribution scenarios with fixed severity', () => {
  const numeric = { ...request(), profileId: 'adbench-numeric-v1', scenarios: [{ scenario: 'local', severity: 1 }] };
  assert.deepEqual(parseSyntheticEvaluation(numeric), numeric);
  reject({ ...numeric, scenarios: [{ scenario: 'local', severity: 2 }] }, 'synthetic_unsupported_scenario');
  reject({ ...numeric, scenarios: request().scenarios }, 'synthetic_unsupported_scenario');
  assert.equal(SYNTHETIC_PROFILES[numeric.profileId].unit, 'numeric_feature_row');
});
test('source registration pins actual schema and complete immutable static specification', () => {
  const registration = parseSyntheticRegistration({ profileId: request().profileId, profileVersion: '1', sourceSchemaVersion: 'arrival-snapshot-v1' });
  const catalog = { existing: true, syntheticProfiles: { [registration.profileId]: registration.binding } };
  assert.deepEqual(verifySyntheticBinding(request(), 'arrival-snapshot-v1', catalog), registration.binding);
  for (const changed of [{}, [], null, { ...catalog, syntheticProfiles: [] },
    { ...catalog, syntheticProfiles: { [registration.profileId]: { ...registration.binding, injected: true } } },
    { ...catalog, syntheticProfiles: { [registration.profileId]: { ...registration.binding, staticSpecHash: '0'.repeat(64) } } }]) {
    assert.throws(() => verifySyntheticBinding(request(), 'arrival-snapshot-v1', changed), /synthetic_source_profile_mismatch/);
  }
  assert.throws(() => verifySyntheticBinding(request(), 'different-schema', catalog), /synthetic_source_profile_mismatch/);
  assert.throws(() => parseSyntheticRegistration({ ...registration, sourceSchemaVersion: 'arrival-snapshot-v1' }), /synthetic_invalid_registration/);
  assert.equal(staticSpecHash({ b: 2, a: 1 }), staticSpecHash({ a: 1, b: 2 }));
});
