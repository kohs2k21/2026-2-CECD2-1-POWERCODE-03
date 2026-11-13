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
import { startFakeLlmHealth } from './fake-llm-health.mjs';

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
// LLM 생성 서비스 가용성(llm-recommendation-job.md '생성 서비스 가용성' 절): 꺼진 상태로 시작해 LLM 작업 접수 거절을 본 뒤 켠다.
const llmHealth = await startFakeLlmHealth('down');
// 프로토콜 경로 설명 접수 예시(API·워커 시험 공용, protocol-explanation-job.md 1절)와 Trainer 초안 고정값(protocol-drafts.test.mjs와 같음).
const protocolExample = JSON.parse(await readFile(path.join(backend, '../contracts/protocol-explanation-job.example.json'), 'utf8'));
const protocolDrafts = JSON.parse(await readFile(path.join(backend, 'tests/fixtures/protocol-path-drafts.json'), 'utf8'));

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
    LLM_HEALTH_URL: llmHealth.url,
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
    expectedRevision: current.revision, hardRules: current.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })), relationRules: [], protocolRules: [],
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
  // 생성 서비스 꺼짐: LLM 작업(recommend_thresholds·explain)만 503 generation_service_unavailable. 작업 행·큐 메시지·멱등키를 남기지 않는다.
  const llmRows = async () => (await pool.query(`SELECT
      (SELECT count(*)::int FROM run_jobs WHERE kind::text IN ('recommend_thresholds','explain','explain_protocol')) AS jobs,
      (SELECT count(*)::int FROM run_jobs WHERE idempotency_key=$1) AS keyed,
      (SELECT count(*)::int FROM powercode_queue.llm_recommendation_jobs) + (SELECT count(*)::int FROM powercode_queue.llm_explanation_jobs) AS queued`,
    [thresholdKey])).rows[0];
  const llmBefore = await llmRows();
  assert.deepEqual((await request(base, '/api/llm/status', 'GET')).status, 401, 'status needs a login');
  const down = await request(base, '/api/llm/status', 'GET', userToken);
  assert.equal(down.status, 200);
  assert.deepEqual([down.body.available, down.body.errorCode], [false, 'generation_service_unavailable']);
  assert.ok(Number.isFinite(Date.parse(down.body.checkedAt)));
  assert.deepEqual(await request(base, '/api/jobs', 'POST', token, thresholds, thresholdKey), { status: 503, body: { code: 'generation_service_unavailable' } });
  assert.deepEqual(await request(base, '/api/jobs', 'POST', userToken, { kind: 'explain', sourceId, decisionId: crypto.randomUUID() }, crypto.randomUUID()),
    { status: 503, body: { code: 'generation_service_unavailable' } }, 'explain is refused before the decision lookup');
  assert.equal((await request(base, '/api/jobs', 'POST', userToken, thresholds, thresholdKey)).status, 403, 'role gate still comes first');
  // explain_protocol: 역할(403)·형식(400)이 가용성(503)보다 먼저, 대상 조회 전에 503(판정 id는 아무 값이어도 됨).
  const protocolDown = { kind: 'explain_protocol', sourceId, target: { kind: 'alert', decisionId: crypto.randomUUID() } };
  assert.deepEqual(await request(base, '/api/jobs', 'POST', userToken, protocolDown, thresholdKey), { status: 403, body: { code: 'admin_required' } });
  assert.deepEqual(await request(base, '/api/jobs', 'POST', token, { ...protocolDown, extra: 1 }, thresholdKey), { status: 400, body: { code: 'invalid_protocol_explanation_request' } });
  assert.deepEqual(await request(base, '/api/jobs', 'POST', token, protocolDown, thresholdKey), { status: 503, body: { code: 'generation_service_unavailable' } },
    'explain_protocol is refused before the target lookup');
  assert.deepEqual(await llmRows(), llmBefore, 'refused LLM requests leave no job, queue message or idempotency key');
  assert.equal((await request(base, '/api/jobs', 'POST', token, synthetic, crypto.randomUUID())).status, 201, 'non-LLM jobs are not gated');
  assert.equal(llmHealth.state.hits, 1, 'one /health check served the status read and both refusals (10 s cache)');
  // 켜진 뒤 캐시(10초)가 지나면 같은 멱등키로 다시 요청할 수 있다(아래 201).
  llmHealth.state.mode = 'ready';
  await new Promise((resolve) => setTimeout(resolve, 10_200));
  const up = await request(base, '/api/llm/status', 'GET', token);
  assert.deepEqual([up.status, up.body.available, up.body.errorCode], [200, true, null]);
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
  assert.deepEqual([stored.llm_spec.model_version, stored.llm_spec.evidence_version, stored.llm_spec.prompt_version],
    ['rule_v3', 'soft-rule-threshold-v2', 'feature-rule-spec-rule-v3-ko']);
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
    expectedRevision: beforeValues.revision, hardRules: beforeValues.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })), relationRules: [], protocolRules: [],
    softRules: thresholdCases.map((item, index) => ({ id: null, clientRef: `value-${index}`, name: item.name, field: item.field, operator: 'gt',
      threshold: Number(item.threshold), severity: 'info', enabled: false })),
  });
  assert.equal(savedValues.status, 200, JSON.stringify(savedValues.body));
  const reread = (await request(base, '/api/rule-set', 'GET', token)).body.softRules;
  for (const item of thresholdCases) assert.equal(decimalString(reread.find(rule => rule.name === item.name).threshold), item.threshold, item.name);
  // 프로토콜 경로 설명 접수(protocol-explanation-job.md 1·10절). 합성 인터페이스·채널 값.
  const protocolRuleSet = (await request(base, '/api/rule-set', 'GET', token)).body;
  const protocolSaved = await request(base, '/api/rule-set', 'PUT', token, {
    expectedRevision: protocolRuleSet.revision, hardRules: protocolRuleSet.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })),
    softRules: [], relationRules: [],
    protocolRules: [{ id: null, clientRef: 'reg', interfaceId: 'IF_REGISTERED_ONLY', paths: [{ channels: ['CH_A', 'CH_B'] }], severity: 'warning', enabled: true }],
  });
  assert.equal(protocolSaved.status, 200, JSON.stringify(protocolSaved.body));
  const protocolRevision = (await request(base, '/api/rule-set', 'GET', token)).body.revision;
  const relationJob = async (summary, { kind = 'recommend_relations', status = 'succeeded' } = {}) => {
    const jobId = crypto.randomUUID(), attemptId = crypto.randomUUID();
    await pool.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash, input_snapshot_id, input_spec,
        max_attempts, attempt_count, next_run_at, created_at, contract_version, queue, producer_kind, producer_key)
      VALUES ($1,$2,'queued',$3,'admin',$4,$5,$6,$7::jsonb,3,0,clock_timestamp(),clock_timestamp(),'v03','training','user',$3)`,
    [jobId, kind, actorId, `qa-${jobId}`, crypto.randomUUID(), snapshotId, JSON.stringify({ kind, snapshotId, sourceId })]);
    if (status === 'succeeded') {
      await pool.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
        VALUES($1,$2,1,'running','qa-worker',$3,clock_timestamp() + interval '5 minutes',clock_timestamp())`, [attemptId, jobId, crypto.randomUUID()]);
      await pool.query(`UPDATE run_jobs SET status='succeeded', current_attempt_id=$2, attempt_count=1, finished_at=clock_timestamp(), result_summary=$3::jsonb WHERE id=$1`,
        [jobId, attemptId, JSON.stringify(summary)]);
    }
    return jobId;
  };
  const draftsJob = await relationJob({ sourceId, protocolPathDrafts: protocolDrafts });
  const oldResultJob = await relationJob({ sourceId }); // 초안 집계 전 Trainer 결과(protocolPathDrafts 키 없음)
  const draftsV2Job = await relationJob({ sourceId, protocolPathDrafts: { ...protocolDrafts, version: 2 } });
  const otherSourceJob = await relationJob({ sourceId: otherSourceId, protocolPathDrafts: protocolDrafts });
  const upperSourceJob = await relationJob({ sourceId: sourceId.toUpperCase(), protocolPathDrafts: protocolDrafts }); // 워커 _same_source: 같은 UUID
  const queuedRelationJob = await relationJob(null, { status: 'queued' });
  const decision = async (evidence) => {
    const execution = crypto.randomUUID(), observation = crypto.randomUUID(), decisionId = crypto.randomUUID();
    const hash = crypto.createHash('sha256').update(observation).digest('hex');
    await pool.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
      VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [execution, sourceId, `exec-${execution}`]);
    await pool.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,api_received_at,persisted_at,clock_status,
        parsed_times,allowed_values,quality_flags,missing_fields) VALUES($1,$2,'P',$3,1,$4,$5,clock_timestamp(),clock_timestamp(),'unknown','{}','{}','[]','[]')`,
    [observation, sourceId, execution, `P:${observation}`, hash]);
    await pool.query(`INSERT INTO data_processes(observation_id,transaction_id,process_id,status) VALUES($1,$2,'P1','S')`, [observation, `TX-${observation}`]);
    await pool.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_hash,status,rule_status,ml_status,
        decision_code,evidence,decided_at,latency_status) VALUES($1,$2,$3,$4,$5,'complete','succeeded','succeeded',NULL,$6::jsonb,clock_timestamp(),'unavailable')`,
    [decisionId, execution, observation, configVersionId, hash, JSON.stringify(evidence)]);
    return decisionId;
  };
  const review = { version: 1, kind: 'interface_unregistered', interfaceId: 'IF_SYN_NEW', protocolRuleId: null, ruleSetRevision: protocolRevision,
    routes: [{ channels: ['CH_N', 'CH_M'], pathKey: 'CH_N:CH_M', matched: false }], routesReason: null };
  const alertDecision = await decision({ protocolReview: review });
  const noReviewDecision = await decision({});
  const v2ReviewDecision = await decision({ protocolReview: { ...review, version: 2 } });
  const otherKindDecision = await decision({ protocolReview: { ...review, kind: 'path_matched' } });
  const blankInterfaceDecision = await decision({ protocolReview: { ...review, interfaceId: '' } });
  const observed = (target) => ({ ...protocolExample.observedPaths.request, sourceId,
    target: { ...protocolExample.observedPaths.request.target, jobId: draftsJob, interfaceId: 'IF_SYN_PAY', ruleSetRevision: protocolRevision, ...target } });
  const alert = (decisionId) => ({ ...protocolExample.alert.request, sourceId, target: { kind: 'alert', decisionId } });
  const protocolRows = async () => (await pool.query(`SELECT (SELECT count(*)::int FROM run_jobs WHERE kind::text='explain_protocol') AS jobs,
    (SELECT count(*)::int FROM powercode_queue.llm_explanation_jobs) AS queued`)).rows[0];
  const protocolBefore = await protocolRows();
  const refusedKey = crypto.randomUUID();
  const refuse = async (body, status, code, label, who = token) =>
    assert.deepEqual(await request(base, '/api/jobs', 'POST', who, body, refusedKey), { status, body: { code } }, label);
  await refuse(observed(), 403, 'admin_required', 'admin only', userToken);
  for (const [label, body] of [
    ['extra key', { ...observed(), decisionId: alertDecision }],
    ['extra target key', observed({ decisionId: alertDecision })],
    ['missing target key', alert(undefined)],
    ['unknown target kind', { ...observed(), target: { ...observed().target, kind: 'path' } }],
    ['bad sourceId', { ...observed(), sourceId: 'not-a-uuid' }],
    ['empty interfaceId', observed({ interfaceId: '' })],
    ['long interfaceId', observed({ interfaceId: 'I'.repeat(513) })],
    ['negative revision', observed({ ruleSetRevision: -1 })],
    ['fractional revision', observed({ ruleSetRevision: 1.5 })],
    ['string revision', observed({ ruleSetRevision: String(protocolRevision) })],
    ['NUL in interfaceId', observed({ interfaceId: 'IF\u0000A' })],
  ]) await refuse(body, 400, 'invalid_protocol_explanation_request', label);
  for (const [label, body] of [
    ['no such job', observed({ jobId: crypto.randomUUID() })],
    ['not recommend_relations', observed({ jobId: syntheticJob.body.id })],
    ['not succeeded', observed({ jobId: queuedRelationJob })],
    ['other source', observed({ jobId: otherSourceJob })],
    ['no such revision', observed({ ruleSetRevision: protocolRevision + 100 })],
    ['no such decision', alert(crypto.randomUUID())],
  ]) await refuse(body, 400, 'protocol_explanation_target_not_found', label);
  for (const [label, body] of [
    ['result without drafts', observed({ jobId: oldResultJob })],
    ['drafts version 2', observed({ jobId: draftsV2Job })],
    ['interface neither observed nor registered', observed({ interfaceId: 'IF_NOWHERE' })],
    ['interface case differs', observed({ interfaceId: 'if_syn_pay' })],
    ['registered only in another revision', observed({ interfaceId: 'IF_REGISTERED_ONLY', ruleSetRevision: protocolRuleSet.revision })],
    ['decision without protocolReview', alert(noReviewDecision)],
    ['protocolReview version 2', alert(v2ReviewDecision)],
    ['protocolReview other kind', alert(otherKindDecision)],
    ['protocolReview blank interface', alert(blankInterfaceDecision)],
  ]) await refuse(body, 400, 'protocol_explanation_facts_unavailable', label);
  assert.deepEqual(await protocolRows(), protocolBefore, 'refused explain_protocol requests leave no job or queue message');
  // 정상 접수 3가지: 초안에만 있음, 룰 세트에만 등록, 알림. 거절에 쓴 멱등키로 바로 201(키가 남지 않았음).
  const fixed = Object.fromEntries(Object.entries(protocolExample.llmSpec).filter(([key]) => !['input_evidence_hash', 'reuse_key'].includes(key)));
  for (const [label, body, decisionId, key] of [
    ['observed in drafts only', observed(), null, refusedKey],
    ['registered only', observed({ interfaceId: 'IF_REGISTERED_ONLY', jobId: upperSourceJob }), null, crypto.randomUUID()],
    ['alert', alert(alertDecision), alertDecision, crypto.randomUUID()],
  ]) {
    const created = await request(base, '/api/jobs', 'POST', token, body, key);
    assert.equal(created.status, 201, `${label}: ${JSON.stringify(created.body)}`);
    assert.deepEqual([created.body.kind, created.body.status, created.body.inputSpec], ['explain_protocol', 'queued', body], label);
    const row = (await pool.query(`SELECT queue::text AS queue,contract_version,requester_role,producer_kind,request_hash,llm_spec,
        input_config_version_id,input_snapshot_id,input_manifest_id,input_decision_id FROM run_jobs WHERE id=$1`, [created.body.id])).rows[0];
    assert.deepEqual([row.queue, row.contract_version, row.requester_role, row.producer_kind, row.input_config_version_id, row.input_snapshot_id,
      row.input_manifest_id, row.input_decision_id], ['llm_explanation', 'v03', 'admin', 'user', null, null, null, decisionId], label);
    const canonical = JSON.stringify({ kind: body.kind, sourceId: body.sourceId, target: body.target });
    const hash = crypto.createHash('sha256').update(canonical).digest('hex');
    assert.deepEqual(row.llm_spec, { ...fixed, input_evidence_hash: hash, reuse_key: crypto.createHash('sha256')
      .update(JSON.stringify([actorId, JSON.parse(canonical), null, fixed.prompt_version])).digest('hex') }, label);
    assert.equal(row.request_hash, hash, label);
    assert.equal((await pool.query(`SELECT count(*)::int AS count FROM powercode_queue.llm_explanation_jobs WHERE payload->>'jobId'=$1`, [created.body.id])).rows[0].count, 1, label);
    // 같은 대상 재요청(다른 멱등키) = 같은 작업 재사용 200, 같은 멱등키에 다른 대상 409.
    const again = await request(base, '/api/jobs', 'POST', token, body, crypto.randomUUID());
    assert.deepEqual([again.status, again.body.id], [200, created.body.id], `${label}: reuse`);
    assert.deepEqual(Object.values(await request(base, '/api/jobs', 'POST', token, observed({ interfaceId: 'IF_SYN_ORDER' }), key)),
      [409, { code: 'idempotency_payload_conflict' }], `${label}: idempotency`);
  }
  assert.deepEqual(await protocolRows(), { jobs: protocolBefore.jobs + 3, queued: protocolBefore.queued + 3 }, 'one job and one queue message per accepted target');
  const protocolList = await request(base, '/api/jobs?kind=explain_protocol', 'GET', token);
  assert.deepEqual([protocolList.status, protocolList.body.items.length], [200, 3], 'job list kind filter');
  console.log('job API integration passed: auth, idempotency, durable queue, role gate, feature-bound rule adoption, LLM availability gate, explain_protocol intake');
} finally {
  await llmHealth.close();
  if (child && child.exitCode === null) {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
  }
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
