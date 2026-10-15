import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

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
             '{}'::jsonb, '{}'::jsonb, clock_timestamp())`,
    [sourceId, `job-api-${sourceId}`],
  );
  const from = new Date(Date.now() - 3600000).toISOString();
  const until = new Date(Date.now() + 3600000).toISOString();
  const input = { kind: 'recommend_feature', sourceId, from, until };
  const key = crypto.randomUUID();
  const first = await request(base, '/api/jobs', 'POST', token, input, key);
  assert.equal(first.status, 201);
  const repeated = await request(base, '/api/jobs', 'POST', token, input, key);
  assert.equal(repeated.status, 200);
  assert.equal(repeated.body.id, first.body.id);
  const changed = await request(base, '/api/jobs', 'POST', token,
    { ...input, until: new Date(Date.now() + 7200000).toISOString() }, key);
  assert.equal(changed.status, 409);
  assert.equal((await request(base, `/api/jobs/${first.body.id}`, 'GET', token)).status, 200);
  assert.equal((await request(base, '/api/jobs', 'POST', userToken, input)).status, 403);
  assert.equal((await request(base, '/api/jobs', 'POST', userToken,
    { ...input, kind: 'train', featureSetId: crypto.randomUUID() })).status, 403);
  const queued = await pool.query(
    `SELECT count(*)::integer AS count FROM powercode_queue.llm_recommendation_jobs
      WHERE payload->>'jobId' = $1`, [first.body.id],
  );
  assert.equal(queued.rows[0].count, 1);
  const lineage = await pool.query('SELECT contract_version,llm_spec FROM run_jobs WHERE id=$1', [first.body.id]);
  assert.equal(lineage.rows[0].contract_version, 'v03');
  assert.equal(lineage.rows[0].llm_spec.model_version, 'feature_v5');
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
  console.log('job API integration passed: auth, idempotency, durable queue, role gate, feature-bound rule adoption');
} finally {
  if (child && child.exitCode === null) {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
  }
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
