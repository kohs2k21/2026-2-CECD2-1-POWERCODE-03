import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { decimalString } from '../dist/ruleset/ruleSetContract.js';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || parsed.pathname !== '/qa_pipeline') {
  throw new Error('Only a local qa_pipeline database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-jobs-'));
const pool = new pg.Pool({ connectionString: url });
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function request(base, route, method = 'GET', token, body, idempotencyKey) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers['Content-Type'] = 'application/json';
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  const response = await fetch(`${base}${route}`, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: await response.json() };
}

try {
  const port = await freePort();
  const environment = { PATH: process.env.PATH, NODE_ENV: 'test',
    DATABASE_URL: url, JWT_SECRET: 'qa-jobs-local-only-secret',
    SEED_USER_EMAIL: 'jobs-user@example.test',
    SEED_USER_PASSWORD: 'qa-jobs-local-only-password',
    SEED_ADMIN_EMAIL: 'jobs-admin@example.test',
    SEED_ADMIN_PASSWORD: 'qa-admin-local-only-password',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(port),
  };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], {
    cwd: temporary, env: environment, stdio: 'ignore',
  });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      if ((await fetch(`${base}/health`)).ok) { ready = true; break; }
    } catch { /* 서버 시작을 기다린다. */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const login = await request(base, '/api/auth/login', 'POST', undefined,
    { email: environment.SEED_ADMIN_EMAIL, password: environment.SEED_ADMIN_PASSWORD });
  assert.equal(login.status, 200);
  const token = login.body.token;
  const userLogin = await request(base, '/api/auth/login', 'POST', undefined,
    { email: environment.SEED_USER_EMAIL, password: environment.SEED_USER_PASSWORD });
  assert.equal(userLogin.status, 200);
  const userToken = userLogin.body.token;
  const actorId = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).id;
  const sourceId = crypto.randomUUID();
  await pool.query(
    `INSERT INTO ops_sources
     (id, dataset_key, display_name, ingestion_method, schema_version,
      clock_basis, field_catalog, created_at)
     VALUES ($1, $2, 'Job API test', 'synthetic-test', 'v1',
             '{}'::jsonb, '{"existing":"preserved"}'::jsonb, clock_timestamp())`,
    [sourceId, `job-api-${sourceId}`],
  );
  const profilesPath = `/api/jobs/sources/${sourceId}/synthetic-profiles`;
  const registration = { profileId: 'esb-process-research-v1', profileVersion: '1', sourceSchemaVersion: 'v1' };
  assert.equal((await request(base, profilesPath, 'PUT', userToken, registration)).status, 403);
  assert.equal((await request(base, profilesPath, 'PUT', token, { ...registration, ignored: true })).status, 400);
  assert.equal((await request(base, profilesPath, 'PUT', token, { ...registration, profileVersion: '2' })).status, 422);
  assert.equal((await request(base, profilesPath, 'PUT', token, { ...registration, sourceSchemaVersion: 'wrong' })).status, 409);
  const registered = await request(base, profilesPath, 'PUT', token, registration);
  assert.equal(registered.status, 201);
  assert.equal((await request(base, profilesPath, 'PUT', token, registration)).status, 200);
  assert.equal((await request(base, profilesPath, 'PUT', token, { ...registration, profileId: 'adbench-numeric-v1' })).status, 201);
  const bindingCatalog = (await pool.query('SELECT field_catalog FROM ops_sources WHERE id=$1', [sourceId])).rows[0].field_catalog;
  assert.equal(bindingCatalog.existing, 'preserved');
  assert.deepEqual(Object.keys(bindingCatalog.syntheticProfiles).sort(), ['adbench-numeric-v1', 'esb-process-research-v1']);
  const discovery = await request(base, '/api/jobs/capabilities', 'GET', token);
  assert.equal(discovery.status, 200);
  const esbProfile = discovery.body.syntheticProfiles.items.find(item => item.profileId === registration.profileId);
  assert.equal(esbProfile.staticSpecHash, registered.body.binding.staticSpecHash);
  assert.equal(esbProfile.unsupportedScenarios[0].scenario, 'P01');
  const snapshotId = crypto.randomUUID(), configVersionId = crypto.randomUUID();
  await pool.query(`INSERT INTO data_snapshots(id,source_id,status,observation_cutoff_at,selection_spec,split_spec,
    schema_version,created_by_actor_id,created_at) VALUES($1,$2,'ready',clock_timestamp(),'{}','{}','process-v1',$3,clock_timestamp())`,
    [snapshotId, sourceId, actorId]);
  await pool.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,
    created_by_actor_id,created_at) VALUES($1,$2,'{}','{}','qa-not-a-trained-config',$3,clock_timestamp())`,
    [configVersionId, sourceId, actorId]);
  const synthetic = { kind: 'evaluate', sourceId, snapshotId, configVersionId, purpose: 'test',
    syntheticEvaluation: { profileId: registration.profileId, profileVersion: '1', scenarios: [{ scenario: 'P04', severity: 1 }],
      seed: 11, repetitions: 1, targetRows: 10 } };
  const syntheticKey = crypto.randomUUID();
  const syntheticJob = await request(base, '/api/jobs', 'POST', token, synthetic, syntheticKey);
  assert.equal(syntheticJob.status, 201);
  assert.deepEqual(syntheticJob.body.inputSpec.syntheticProfileBinding, registered.body.binding);
  assert.equal((await request(base, '/api/jobs', 'POST', token, synthetic, syntheticKey)).body.id, syntheticJob.body.id);
  assert.equal((await request(base, '/api/jobs', 'POST', token,
    { ...synthetic, syntheticEvaluation: { ...synthetic.syntheticEvaluation, seed: 12 } }, syntheticKey)).status, 409);
  assert.equal((await request(base, '/api/jobs', 'POST', userToken, synthetic)).status, 403);
  assert.equal((await request(base, '/api/jobs', 'POST', token, { ...synthetic, purpose: 'train' })).status, 400);
  assert.equal((await request(base, '/api/jobs', 'POST', token, { ...synthetic, syntheticProfileBinding: registered.body.binding })).status, 400);
  assert.equal((await request(base, '/api/jobs', 'POST', token, { ...synthetic,
    syntheticEvaluation: { ...synthetic.syntheticEvaluation, scenarios: [{ scenario: 'P01', severity: 1 }] } })).status, 422);
  assert.deepEqual((await request(base, `/api/jobs/${syntheticJob.body.id}`, 'GET', token)).body.inputSpec.syntheticEvaluation, synthetic.syntheticEvaluation);
  assert.equal((await pool.query(`SELECT count(*)::int AS count FROM powercode_queue.training_jobs WHERE payload->>'jobId'=$1`,
    [syntheticJob.body.id])).rows[0].count, 1);
  await pool.query(`UPDATE ops_sources SET field_catalog=jsonb_set(field_catalog,
    '{syntheticProfiles,esb-process-research-v1,staticSpecHash}','"modified"'::jsonb) WHERE id=$1`, [sourceId]);
  assert.equal((await request(base, profilesPath, 'PUT', token, registration)).status, 409);
  assert.equal((await request(base, '/api/jobs', 'POST', token, synthetic)).status, 409);
  await pool.query('UPDATE ops_sources SET field_catalog=$2::jsonb WHERE id=$1', [sourceId, JSON.stringify(bindingCatalog)]);
  const from = new Date(Date.now() - 3600000).toISOString();
  const until = new Date(Date.now() + 3600000).toISOString();
  // 폐지된 작업 종류(llm-recommendation-job.md 폐지 표): 관리자·사용자 모두 422 job_kind_abolished, 작업·큐 메시지를 남기지 않는다.
  const queuedBefore = (await pool.query('SELECT count(*)::integer AS count FROM powercode_queue.llm_recommendation_jobs')).rows[0].count;
  for (const kind of ['recommend_feature', 'recommend_rule']) {
    for (const [who, label] of [[token, 'admin'], [userToken, 'user']]) {
      const refused = await request(base, '/api/jobs', 'POST', who, { kind, sourceId, from, until, featureSetId: crypto.randomUUID() }, crypto.randomUUID());
      assert.deepEqual([refused.status, refused.body.code], [422, 'job_kind_abolished'], `${kind} as ${label}`);
    }
  }
  assert.equal((await pool.query(`SELECT count(*)::integer AS count FROM run_jobs WHERE kind IN ('recommend_feature', 'recommend_rule')`)).rows[0].count, 0, 'no abolished job row');
  assert.equal((await pool.query('SELECT count(*)::integer AS count FROM powercode_queue.llm_recommendation_jobs')).rows[0].count, queuedBefore, 'no queue message');
  assert.equal((await request(base, '/api/jobs', 'POST', userToken,
    { kind: 'train', sourceId, from, until, featureSetId: crypto.randomUUID() })).status, 403);
  assert.equal((await request(base, '/api/pipeline/windows', 'GET', token)).status, 200);
  assert.equal((await request(base, '/api/pipeline/window-monitors', 'GET', userToken)).status, 403);
  const featureSetId = crypto.randomUUID();
  const featureVersionId = crypto.randomUUID();
  await pool.query(
    `INSERT INTO ml_feature_sets
     (id, schema_version, transform_version, output_schema, content_hash,
      created_by_actor_id, created_at)
     VALUES ($1, 'test-v1', 'test-v1', '["error_count"]'::jsonb, $2,
             $3, clock_timestamp())`,
    [featureSetId, crypto.randomUUID(), actorId],
  );
  await pool.query(
    `INSERT INTO ml_feature_versions
     (id, definition_key, version, display_name, definition_kind,
      expression, output_type, value_policy, availability_spec, requires_fit,
      content_hash, created_by_actor_id, created_at)
     VALUES ($1, $2, 1, 'Error count', 'test',
             '{"op":"field","field":"P.error_count"}'::jsonb, 'decimal',
             '{}'::jsonb, '{}'::jsonb, false, $3, $4, clock_timestamp())`,
    [featureVersionId, `test_error_${featureVersionId.replaceAll('-', '')}`,
      crypto.randomUUID(), actorId],
  );
  await pool.query(
    `INSERT INTO ml_feature_set_members
     (feature_set_id, feature_version_id, model_input, output_position,
      output_name, transform_settings)
     VALUES ($1, $2, true, 0, 'error_count', '{}'::jsonb)`,
    [featureSetId, featureVersionId],
  );
  const ruleJobId = crypto.randomUUID();
  const ruleKey = `suggested_${sourceId.replaceAll('-', '').slice(0, 8)}`;
  const suggestion = { ruleKey, displayName: 'Suggested error rule',
    condition: { field: 'F.error_count', operator: 'gt', value: '0' },
    outcome: { decisionCode: 'TEST_SUGGESTED_RULE', severity: 'warning' },
    evidenceSpec: { model: 'rule_v2', measurementId: 'test-measurement', meaningReviewRequired: true } };
  await pool.query(
    `INSERT INTO run_jobs
     (id, kind, status, requested_by_actor_id, requester_role, request_hash,
      input_spec, max_attempts, attempt_count, next_run_at, result_summary,
      created_at, finished_at)
     VALUES ($1, 'recommend_rule', 'succeeded', $2, 'user', 'test-request',
             $3::jsonb, 3, 1, clock_timestamp(), $4::jsonb,
             clock_timestamp(), clock_timestamp())`,
    [ruleJobId, actorId, JSON.stringify({ featureSetId }),
      JSON.stringify({ suggestions: [suggestion,
        { ...suggestion, ruleKey: `${ruleKey}_unknown`,
          condition: { field: 'F.unknown_count', operator: 'gt', value: '0' } }] })],
  );
  const adopted = await request(base, `/api/jobs/${ruleJobId}/recommendations/0/adopt`, 'POST', token);
  assert.equal(adopted.status, 201);
  assert.equal(adopted.body.ruleKey, ruleKey);
  assert.equal(adopted.body.inputSpec.featureSetId, featureSetId);
  assert.equal(adopted.body.evidenceSpec.recommendationJobId, ruleJobId);
  assert.equal(adopted.body.evidenceSpec.research.measurementId, 'test-measurement');
  const unknown = await request(base, `/api/jobs/${ruleJobId}/recommendations/1/adopt`, 'POST', token);
  assert.equal(unknown.status, 409);
  const current = (await request(base, '/api/rule-set', 'GET', token)).body;
  const saved = await request(base, '/api/rule-set', 'PUT', token, {
    expectedRevision: current.revision, hardRules: current.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })),
    softRules: [
      { id: null, clientRef: 'on', name: 'Slow process', field: 'elapsed_ms', operator: 'gt', threshold: 5000, severity: 'warning', enabled: true },
      { id: null, clientRef: 'off', name: 'Retries', field: 'retry_count', operator: 'gte', threshold: 3, severity: 'info', enabled: false },
    ],
  });
  assert.equal(saved.status, 200);
  const ruleSet = (await request(base, '/api/rule-set', 'GET', token)).body;
  const enabled = ruleSet.softRules.find(rule => rule.enabled);
  const thresholds = { kind: 'recommend_thresholds', sourceId, ruleSetRevision: ruleSet.revision, scope: { softRuleIds: null },
    data: { snapshotId: null, from, until, timeBasis: 'persisted_at' }, mode: 'data_p95_with_llm_rationale' };
  const thresholdKey = crypto.randomUUID();
  assert.equal((await request(base, '/api/jobs', 'POST', userToken, thresholds, thresholdKey)).status, 403);
  assert.equal((await request(base, '/api/jobs', 'POST', token, { ...thresholds, ruleSetRevision: ruleSet.revision - 1 }, thresholdKey)).body.code, 'rule_set_revision_conflict');
  const thresholdJob = await request(base, '/api/jobs', 'POST', token, thresholds, thresholdKey);
  assert.equal(thresholdJob.status, 201);
  assert.deepEqual(thresholdJob.body.inputSpec.targets, [{ id: enabled.id, field: 'elapsed_ms', threshold: 5000 }]);
  assert.equal(thresholdJob.body.inputSpec.ruleSetContentHash, ruleSet.contentHash);
  assert.equal(thresholdJob.body.inputSpec.ruleSetContentHashVersion, ruleSet.contentHashVersion);
  assert.equal((await request(base, '/api/jobs', 'POST', token, thresholds, thresholdKey)).body.id, thresholdJob.body.id);
  const stored = (await pool.query(`SELECT queue,contract_version,request_hash,llm_spec,input_config_version_id,input_snapshot_id,input_decision_id
    FROM run_jobs WHERE id=$1`, [thresholdJob.body.id])).rows[0];
  assert.deepEqual([stored.queue, stored.contract_version, stored.input_config_version_id, stored.input_snapshot_id, stored.input_decision_id],
    ['llm_recommendation', 'v03', null, null, null]);
  assert.equal(stored.llm_spec.input_evidence_hash, stored.request_hash);
  assert.equal(stored.llm_spec.prompt_version, 'feature-rule-design-v0.2');
  assert.equal((await pool.query(`SELECT count(*)::int AS count FROM powercode_queue.llm_recommendation_jobs WHERE payload->>'jobId'=$1`,
    [thresholdJob.body.id])).rows[0].count, 1);
  const failedSummary = { recommendationContractVersion: 'threshold-recommendation-v1', items: [{ field: 'elapsed_ms', status: 'rejected', rejectedReason: 'factual_review_failed' }] };
  await pool.query(`UPDATE run_jobs SET status='failed',error_code='recommendation_all_rejected',result_summary=$2::jsonb,finished_at=clock_timestamp() WHERE id=$1`,
    [thresholdJob.body.id, JSON.stringify(failedSummary)]);
  const failedJob = await request(base, `/api/jobs/${thresholdJob.body.id}`, 'GET', token);
  assert.deepEqual([failedJob.body.status, failedJob.body.errorCode, failedJob.body.resultSummary], ['failed', 'recommendation_all_rejected', failedSummary]);
  // 스냅샷을 지정한 요청: 워커와 같은 조건을 접수에서 확인한다.
  const otherSourceId = crypto.randomUUID();
  await pool.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,'Job API other source','synthetic-test','v1','{}'::jsonb,'{}'::jsonb,clock_timestamp())`, [otherSourceId, `job-api-${otherSourceId}`]);
  const addSnapshot = async ({ source = sourceId, status = 'ready', schema = 'process-v1' } = {}) => {
    const id = crypto.randomUUID();
    await pool.query(`INSERT INTO data_snapshots(id,source_id,status,observation_cutoff_at,selection_spec,split_spec,schema_version,created_by_actor_id,created_at)
      VALUES($1,$2,$3,clock_timestamp(),$4::jsonb,'{}',$5,$6,clock_timestamp())`, [id, source, status, JSON.stringify({ from, until }), schema, actorId]);
    return id;
  };
  const withSnapshot = (snapshotId, period = {}) => ({ ...thresholds, data: { snapshotId, ...period } });
  const readySnapshot = await addSnapshot();
  const shifted = new Date(Date.parse(from) - 1000).toISOString();
  for (const [name, payload] of [
    ['other source', withSnapshot(await addSnapshot({ source: otherSourceId }))],
    ['other schema', withSnapshot(await addSnapshot({ schema: 'prepared-features-v1' }))],
    ['not ready', withSnapshot(await addSnapshot({ status: 'building' }))],
    ['different from', withSnapshot(readySnapshot, { from: shifted, until })],
    ['different until', withSnapshot(readySnapshot, { from, until: new Date(Date.parse(until) + 1000).toISOString() })],
    ['missing', withSnapshot(crypto.randomUUID())],
  ]) {
    const refused = await request(base, '/api/jobs', 'POST', token, payload);
    assert.deepEqual([refused.status, refused.body.code], [409, 'snapshot_conditions_mismatch'], name);
  }
  for (const period of [{}, { from, until }]) {
    const accepted = await request(base, '/api/jobs', 'POST', token, withSnapshot(readySnapshot, period));
    assert.equal(accepted.status, 201);
    assert.equal(accepted.body.inputSpec.data.snapshotId, readySnapshot);
    assert.equal((await pool.query('SELECT input_snapshot_id FROM run_jobs WHERE id=$1', [accepted.body.id])).rows[0].input_snapshot_id, null);
  }
  // 워커 시험이 fixed_threshold 출력으로 고정한 값을 실제 저장 API에 그대로 넣는다.
  const thresholdCases = JSON.parse(await readFile(path.join(backend, '../contracts/threshold-values.example.json'), 'utf8')).cases.filter(item => item.threshold !== null);
  assert.ok(thresholdCases.length <= 32, 'one save holds every case');
  const beforeValues = (await request(base, '/api/rule-set', 'GET', token)).body;
  const savedValues = await request(base, '/api/rule-set', 'PUT', token, {
    expectedRevision: beforeValues.revision, hardRules: beforeValues.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })),
    softRules: thresholdCases.map((item, index) => ({ id: null, clientRef: `value-${index}`, name: item.name, field: item.field, operator: 'gt',
      threshold: Number(item.threshold), severity: 'info', enabled: false })),
  });
  assert.equal(savedValues.status, 200, JSON.stringify(savedValues.body));
  const reread = (await request(base, '/api/rule-set', 'GET', token)).body.softRules;
  for (const item of thresholdCases) assert.equal(decimalString(reread.find(rule => rule.name === item.name).threshold), item.threshold, item.name);
  console.log('job API integration passed: auth, idempotency, durable queue, role gate, feature-bound rule adoption');
} finally {
  if (child && child.exitCode === null) {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
  }
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
