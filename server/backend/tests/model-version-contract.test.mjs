// 머신러닝 모델 API의 DB 없는 시험: 방법 카탈로그 원본 대조, 파라미터 검사, 모델 버전 투영, 조회 조건·거래량 조건. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { METHOD_CATALOG, ML_METHODS, SOFT_RULE_FIELDS, mlMethod, modelKeyOf, parametersValid } from '../dist/ruleset/methodCatalog.js';
import { TRAINER_CAPABILITIES, TRAINING_EXCLUSION_SUPPORTED } from '../dist/routes/trainerContract.js';
import { ConfigurationQueryError, parseConfigurationQuery, projectVersion } from '../dist/services/configurationReadModel.js';
import { VolumeRangeError, parseVolumeFilter, readVolume } from '../dist/services/volumeReport.js';
import { parseThresholdStatsRequest } from '../dist/ruleset/thresholdStats.js';
import { RuleSetError } from '../dist/ruleset/ruleSetContract.js';
import { ReportRequestError } from '../dist/services/operationalReport.js';

// jobRoutes는 인증 설정을 불러오므로 시험용 값을 먼저 둔다(요청 파싱만 쓴다).
process.env.JWT_SECRET ??= 'qa-model-version-contract-only';
const { JobRequestError, parseRequest } = await import('../dist/routes/jobRoutes.js');

const contractFile = (name) => readFile(new URL(`../../contracts/${name}`, import.meta.url), 'utf8');
const iforest = mlMethod('iforest');

test('the catalog is the generated contract file, not a copy in code', async () => {
  assert.deepEqual(METHOD_CATALOG, JSON.parse(await contractFile('method-catalog.json')));
  assert.equal(TRAINER_CAPABILITIES.methodCatalog, METHOD_CATALOG);
  assert.equal(SOFT_RULE_FIELDS, METHOD_CATALOG.softRuleFields);
});
test('models[] derives from methods[] with supported = executable.process', () => {
  assert.deepEqual(TRAINER_CAPABILITIES.models, [
    { key: 'iforest', displayName: 'Isolation Forest', supported: true },
    { key: 'minibatch_kmeans', displayName: 'MiniBatch K-Means', supported: false },
    { key: 'eif', displayName: 'Extended Isolation Forest', supported: false },
  ]);
  assert.deepEqual(ML_METHODS.map(modelKeyOf), TRAINER_CAPABILITIES.models.map((model) => model.key));
  assert.equal(mlMethod('ml:iforest'), undefined, 'modelKey has no prefix');
  assert.equal(mlMethod(42), undefined);
});
test('every catalog default passes its own parameterSchema', () => {
  for (const method of ML_METHODS) assert.equal(parametersValid(method, {}), true, method.methodId);
});
test('parameters follow the same subset as the worker registry', () => {
  for (const value of [undefined, null, {}, { n_estimators: 16 }, { n_estimators: 1024 }, { max_samples: 64, random_state: 0 }, { contamination: 'auto' }]) {
    assert.equal(parametersValid(iforest, value), true, JSON.stringify(value));
  }
  for (const value of [[], 'x', 5, { n_estimators: 15 }, { n_estimators: 1025 }, { n_estimators: 1.5 }, { n_estimators: '128' }, { n_estimators: true },
    { max_samples: 63 }, { contamination: 0.1 }, { thresholdPolicy: 'other' }, { random_state: -1 }, { random_state: 2 ** 60 }, { unknown: 1 }, JSON.parse('{"__proto__":{"x":1}}')]) {
    assert.equal(parametersValid(iforest, value), false, JSON.stringify(value));
  }
  assert.equal(parametersValid(mlMethod('minibatch_kmeans'), { quantile: 0.5 }), true, 'number type allows decimals');
  assert.equal(parametersValid(mlMethod('minibatch_kmeans'), { quantile: 1.5 }), false);
});
test('training exclusion stays closed until the Trainer applies it', () => {
  assert.equal(TRAINING_EXCLUSION_SUPPORTED, false);
  assert.equal(TRAINER_CAPABILITIES.snapshot.timeBasis, 'persisted_at');
  assert.deepEqual(TRAINER_CAPABILITIES.training, { excludedIntervals: TRAINING_EXCLUSION_SUPPORTED }, 'the screen reads the same value the API enforces');
});

const trainBody = {
  kind: 'train', sourceId: '22222222-2222-4222-8222-222222222222', from: '2026-09-30T15:00:00Z', until: '2026-10-31T15:00:00Z', featureSetId: '33333333-3333-4333-8333-333333333333',
  detectionPolicy: { mode: 'ml_only', deadlineMs: 3000, combination: 'selected_path' },
};
const opened = { timeBasis: 'esb_occurred_at', excludedIntervals: true };
const refusedScope = (body, support, status, code) => assert.throws(() => parseRequest({ ...trainBody, ...body }, support), (error) => error instanceof JobRequestError && error.status === status && error.code === code);
test('once the Trainer supports them, accepted intervals and time basis reach the job input', () => {
  const input = parseRequest({ ...trainBody, timeBasis: 'esb_occurred_at', excludedIntervals: [
    { from: '2026-10-03T02:00:00+09:00', until: '2026-10-03T06:30:00+09:00', reasonCode: 'known_incident', note: 'DB 장애' },
    { from: '2026-10-20T22:00:00+09:00', until: '2026-10-21T01:00:00+09:00' },
  ] }, opened);
  assert.equal(input.timeBasis, 'esb_occurred_at');
  assert.deepEqual(input.excludedIntervals, [
    { from: '2026-10-02T17:00:00.000Z', until: '2026-10-02T21:30:00.000Z', reasonCode: 'known_incident', note: 'DB 장애' },
    { from: '2026-10-20T13:00:00.000Z', until: '2026-10-20T16:00:00.000Z', reasonCode: 'other', note: null },
  ], 'UTC, default reason, nothing dropped');
  assert.deepEqual(parseRequest({ ...trainBody, excludedIntervals: [] }, { timeBasis: 'persisted_at', excludedIntervals: true }).excludedIntervals, [], 'an empty list is kept: it selects policy v2');
  assert.equal('timeBasis' in parseRequest({ ...trainBody, timeBasis: 'persisted_at' }, { timeBasis: 'persisted_at', excludedIntervals: true }), false);
  assert.equal('excludedIntervals' in parseRequest(trainBody, opened), false, 'no key, no intervals');
  refusedScope({ excludedIntervals: [{ from: '2026-09-01T00:00:00Z', until: '2026-09-02T00:00:00Z' }] }, opened, 400, 'excluded_interval_out_of_range');
  refusedScope({ excludedIntervals: [{ from: '2026-10-03T00:00:00Z', until: '2026-10-04T00:00:00Z' }, { from: '2026-10-03T12:00:00Z', until: '2026-10-05T00:00:00Z' }] }, opened, 400, 'excluded_interval_overlap');
});
test('note: null belongs to the training request only', () => {
  const interval = { from: '2026-10-03T00:00:00Z', until: '2026-10-04T00:00:00Z', note: null };
  assert.equal(parseRequest({ ...trainBody, excludedIntervals: [interval] }, opened).excludedIntervals[0].note, null);
  const stats = { field: 'elapsed_ms', sourceId: trainBody.sourceId, period: { mode: 'custom', from: trainBody.from, until: trainBody.until } };
  assert.equal(parseThresholdStatsRequest({ ...stats, excludedIntervals: [{ ...interval, note: 'x' }] }).excludedIntervals.length, 1);
  assert.throws(() => parseThresholdStatsRequest({ ...stats, excludedIntervals: [interval] }), (error) => error instanceof RuleSetError && error.code === 'invalid_threshold_stats_request');
});
test('until then both are refused, not dropped', () => {
  const inside = { from: '2026-10-03T00:00:00Z', until: '2026-10-04T00:00:00Z' };
  refusedScope({ excludedIntervals: [inside] }, undefined, 422, 'config_schema_not_supported_yet');
  refusedScope({ timeBasis: 'esb_occurred_at' }, undefined, 422, 'config_schema_not_supported_yet');
  refusedScope({ excludedIntervals: [inside] }, { timeBasis: 'esb_occurred_at', excludedIntervals: false }, 422, 'config_schema_not_supported_yet');
});

const row = (spec, overrides = {}) => ({
  id: '11111111-1111-4111-8111-111111111111', sourceId: '22222222-2222-4222-8222-222222222222', spec, contentHash: 'h', createdAt: '2026-11-01T00:00:00Z',
  modelKeys: ['iforest'], trainJobId: '33333333-3333-4333-8333-333333333333', trainInput: { from: '2026-10-01T00:00:00Z', until: '2026-11-01T00:00:00Z' },
  modelSummary: { featureNames: ['error_count', 'retry_count'], parameters: { n_estimators: 64 }, thresholdValue: '0.58' },
  latestEvaluation: null, eligibleEvaluationId: null, unavailableEvaluation: false, ...overrides,
});
test('v1 rows project to the contract shape without rewriting them', () => {
  const version = projectVersion(row({ schemaVersion: 1, detectionPolicy: { mode: 'ml_only' }, statusRulePolicy: {}, ruleVersionIds: ['r1'], trainingSelection: { policyVersion: 'train-success-only-v1', selectedCount: 7 } }), new Map());
  assert.equal(version.requestedMode, 'ml_only');
  assert.equal(version.contentHashVersion, 1);
  assert.equal(version.modelKey, 'iforest');
  assert.deepEqual(version.createdFrom, { kind: 'train', trainJobId: '33333333-3333-4333-8333-333333333333', recommendationJobId: null });
  assert.deepEqual(version.methods, [{ methodId: 'ml:iforest', kind: 'ml', enabled: true, status: 'supported', executable: { process: true, window: true }, executableReason: null }]);
  assert.deepEqual(version.trainingSelection, { policyVersion: 'train-success-only-v1', selectedCount: 7,
    trainingPeriod: { from: '2026-10-01T00:00:00Z', until: '2026-11-01T00:00:00Z', timeBasis: 'persisted_at' }, excludedIntervalCount: 0 });
  assert.deepEqual(version.legacyRules, { statusRule: true, ruleVersionIds: ['r1'] });
  assert.equal(version.active, false);
  assert.equal(version.activeSince, null);
  assert.equal(version.latestComparison, null);
});
test('modelSummary reports stored values only', () => {
  const summary = (spec, modelSummary) => projectVersion(row(spec, { modelSummary }), new Map()).modelSummary;
  assert.deepEqual(summary({ schemaVersion: 1 }, row({}).modelSummary), { featureCount: 2, parameters: { n_estimators: 64 }, thresholdValue: '0.58' });
  assert.deepEqual(summary({ schemaVersion: 1 }, { featureNames: null, parameters: null, thresholdValue: null }), { featureCount: null, parameters: null, thresholdValue: null }, 'older rows stay empty');
  assert.equal(summary({ schemaVersion: 2 }, null), null, 'no model, no summary');
  assert.deepEqual(summary({ schemaVersion: 3, methods: [{ methodId: 'ml:iforest', enabled: true, parameters: { n_estimators: 512 } }] }, row({}).modelSummary).parameters, { n_estimators: 512 }, 'v3 reads the spec');
  assert.equal(summary({ schemaVersion: 1 }, { thresholdValue: 0.5 }).thresholdValue, null, 'a threshold outside the stored string form is not guessed');
});
test('a missing train job means an unknown period, never a guessed one', () => {
  assert.equal(projectVersion(row({ schemaVersion: 1 }, { trainInput: null, trainJobId: null }), new Map()).trainingSelection.trainingPeriod, null);
  assert.equal(projectVersion(row({ schemaVersion: 1 }), new Map()).requestedMode, 'parallel', 'older rows without a policy ran as parallel');
});
test('rule-only rows have no model and are not executable', () => {
  const version = projectVersion(row({ schemaVersion: 2, detectionPolicy: { mode: 'rule_only' } }, { modelKeys: [] }), new Map());
  assert.equal(version.modelKey, null);
  assert.equal(version.executable, false);
  assert.equal(version.applicationEligibility.reason, 'not_executable');
});
test('v3 rows read the spec and recompute executable from the catalog', () => {
  const version = projectVersion(row({ schemaVersion: 3, contentHashVersion: 2, displayName: 'IF 10월', methods: [{ methodId: 'ml:minibatch_kmeans', enabled: true }],
    createdFrom: { kind: 'train', trainJobId: 'j', recommendationJobId: null },
    trainingSelection: { trainingPeriod: { from: 'a', until: 'b', timeBasis: 'esb_occurred_at' }, excludedIntervalCount: 2 } }), new Map());
  assert.equal(version.requestedMode, 'ml');
  assert.equal(version.modelKey, 'minibatch_kmeans');
  assert.equal(version.executable, false);
  assert.equal(version.methods[0].executableReason, 'server_adapter_missing');
  assert.equal(version.trainingSelection.excludedIntervalCount, 2);
  assert.equal(version.legacyRules, undefined);
});
test('a model outside the catalog is shown as not executable', () => {
  const version = projectVersion(row({ schemaVersion: 1 }, { modelKeys: ['removed_model'] }), new Map());
  assert.equal(version.executable, false);
  assert.deepEqual(version.methods[0].executable, { process: false, window: false });
  assert.equal(version.methods[0].status, 'unknown');
});
test('application eligibility reports the first failed gate', () => {
  const eligible = (spec, overrides) => projectVersion(row({ schemaVersion: 1, ...spec }, overrides), new Map()).applicationEligibility;
  assert.deepEqual(eligible({}, { eligibleEvaluationId: 'e1' }), { eligible: true, evaluationId: 'e1', reason: null });
  assert.equal(eligible({}, {}).reason, 'no_eligible_evaluation');
  assert.equal(eligible({}, { unavailableEvaluation: true }).reason, 'method_results_unavailable');
  assert.equal(eligible({ operationalEligibility: { eligible: false, reason: 'prepared_detector_reproduction_unverified' } }, { eligibleEvaluationId: 'e1' }).reason, 'operational_ineligible');
  assert.equal(eligible({}, { modelKeys: ['minibatch_kmeans'], eligibleEvaluationId: 'e1' }).reason, 'not_executable');
});
test('active state fills active and activeSince', () => {
  const version = projectVersion(row({ schemaVersion: 1 }), new Map([['11111111-1111-4111-8111-111111111111', { revision: 7, at: '2026-11-01T02:00:00.000Z' }]]));
  assert.equal(version.active, true);
  assert.deepEqual(version.activeSince, { revision: 7, at: '2026-11-01T02:00:00.000Z' });
});

const rejected = (query, code) => assert.throws(() => parseConfigurationQuery(query), (error) => error instanceof ConfigurationQueryError && error.code === code);
test('configuration list query', () => {
  assert.deepEqual(parseConfigurationQuery({}), { sourceId: null, limit: 50, after: null, createdFrom: null, modelKey: null, executable: null, active: null });
  assert.equal(parseConfigurationQuery({ limit: '100' }).limit, 100);
  assert.equal(parseConfigurationQuery({ executable: 'false' }).executable, false);
  rejected({ limit: '0' }, 'invalid_limit');
  rejected({ limit: '101' }, 'invalid_limit');
  rejected({ sourceId: 'x' }, 'invalid_source_id');
  rejected({ cursor: 'not base64!' }, 'invalid_cursor');
  rejected({ cursor: Buffer.from('{"id":"x"}').toString('base64url') }, 'invalid_cursor');
  const cursorAt = (createdAt) => Buffer.from(JSON.stringify({ createdAt, id: '11111111-1111-4111-8111-111111111111' })).toString('base64url');
  for (const time of ['2026-13-99T00:00:00.000000Z', '2026-02-30T00:00:00.000000Z', '0000-01-01T00:00:00.000000Z', '2026-01-01T25:00:00.000000Z']) rejected({ cursor: cursorAt(time) }, 'invalid_cursor');
  assert.equal(parseConfigurationQuery({ cursor: cursorAt('2026-02-28T23:59:59.999999Z') }).after.createdAt, '2026-02-28T23:59:59.999999Z');
  rejected({ createdFrom: 'other' }, 'invalid_created_from');
  rejected({ modelKey: 'Bad Key' }, 'invalid_model_key');
  rejected({ executable: '1' }, 'invalid_executable');
  assert.equal(parseConfigurationQuery({ active: 'true' }).active, true);
  assert.equal(parseConfigurationQuery({ active: 'false' }).active, false);
  rejected({ active: '1' }, 'invalid_active');
  rejected({ other: 'true' }, 'unknown_configuration_filter');
});

const volume = { sourceId: '22222222-2222-4222-8222-222222222222', from: '2026-10-01T00:00:00+09:00', until: '2026-10-02T00:00:00+09:00', bucket: 'hour', timeBasis: 'esb_occurred_at' };
const volumeCode = (query, code) => assert.throws(() => parseVolumeFilter(query), (error) => error instanceof ReportRequestError && error.code === code);
test('volume filter normalizes to UTC and uses the contract range limits', () => {
  assert.deepEqual(parseVolumeFilter(volume), { ...volume, from: '2026-09-30T15:00:00.000Z', until: '2026-10-01T15:00:00.000Z', timezone: 'Asia/Seoul' });
  const days = (count) => ({ from: '2024-01-01T00:00:00Z', until: new Date(Date.parse('2024-01-01T00:00:00Z') + count * 86400000).toISOString() });
  for (const [bucket, maxDays] of [['minute', 7], ['hour', 92], ['day', 1096]]) {
    assert.ok(parseVolumeFilter({ ...volume, bucket, ...days(maxDays) }));
    assert.throws(() => parseVolumeFilter({ ...volume, bucket, ...days(maxDays + 1) }), (error) => error instanceof VolumeRangeError && error.bucket === bucket && error.maxDays === maxDays);
  }
  assert.ok(parseVolumeFilter({ ...volume, bucket: 'month', ...days(5000) }), 'month has no range limit');
});
test('volume filter rejects malformed input', () => {
  volumeCode({ ...volume, extra: '1' }, 'unknown_report_filter');
  volumeCode({ ...volume, sourceId: 'x' }, 'invalid_sourceId');
  volumeCode({ ...volume, from: volume.until }, 'invalid_report_period');
  volumeCode({ ...volume, from: '2026-02-30T00:00:00Z' }, 'invalid_report_period');
  volumeCode({ ...volume, from: '2026-10-01' }, 'invalid_report_period');
  volumeCode({ ...volume, bucket: 'week' }, 'invalid_report_bucket');
  volumeCode({ ...volume, bucket: undefined }, 'invalid_report_bucket');
  volumeCode({ ...volume, timeBasis: 'decided_at' }, 'invalid_report_time_basis');
  volumeCode({ ...volume, timezone: 'Mars/Base' }, 'invalid_report_timezone');
});
test('a volume query that hits the statement timeout fails instead of returning partial numbers', async () => {
  const issued = [];
  let released = 0;
  const client = {
    query: async (sql) => {
      issued.push(String(sql));
      if (String(sql).startsWith('SELECT 1')) return { rowCount: 1, rows: [{}] };
      if (String(sql).startsWith('WITH candidates')) throw Object.assign(new Error('canceling statement due to statement timeout'), { code: '57014' });
      return { rowCount: 0, rows: [] };
    },
    release: () => { released += 1; },
  };
  await assert.rejects(readVolume(() => ({ connect: async () => client }), parseVolumeFilter(volume)), (error) => error.code === '57014');
  assert.ok(issued.includes('SET LOCAL statement_timeout=10000'));
  assert.equal(issued.at(-1), 'ROLLBACK');
  assert.equal(released, 1);
});
test('the deploy image carries the catalog where the module reads it', async () => {
  const dockerfile = await readFile(new URL('../Dockerfile', import.meta.url), 'utf8');
  assert.match(dockerfile, /^COPY contracts\/method-catalog\.json \/contracts\/method-catalog\.json$/m);
  assert.doesNotMatch(await readFile(new URL('../../.dockerignore', import.meta.url), 'utf8'), /contracts/);
});
