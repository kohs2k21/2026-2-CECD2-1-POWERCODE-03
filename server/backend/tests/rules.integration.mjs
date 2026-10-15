import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const databaseUrl = process.env.RULE_TEST_DATABASE_URL;
if (!databaseUrl) throw new Error('RULE_TEST_DATABASE_URL is required');
const parsedUrl = new URL(databaseUrl);
if (!['localhost', '127.0.0.1'].includes(parsedUrl.hostname) || parsedUrl.pathname !== '/qa_rules_api') {
  throw new Error('Only the local qa_rules_api database is permitted');
}

const pool = new pg.Pool({ connectionString: databaseUrl });
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-rules-'));
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function request(base, route, method = 'GET', token, body, ifMatch) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers['Content-Type'] = 'application/json';
  if (ifMatch) headers['If-Match'] = ifMatch;
  const response = await fetch(`${base}${route}`, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, etag: response.headers.get('etag'), body: await response.json() };
}

try {
  // 전용 테스트 DB만 재사용한다. 최초 실행에는 실제 migration을 적용한다.
  const existing = await pool.query("SELECT to_regclass('public.ml_rule_versions') AS table_name");
  if (existing.rows[0].table_name) {
    await pool.query('TRUNCATE TABLE ml_config_rules, ml_rule_versions');
  } else {
    for (const filename of ['002_core_erd.sql', '003_rule_versions_immutable.sql']) {
      const sql = await readFile(path.resolve(backend, '../infra/migrations', filename), 'utf8');
      await pool.query(sql);
    }
  }

  const port = await freePort();
  const environment = { PATH: process.env.PATH, NODE_ENV: 'test',
    DATABASE_URL: databaseUrl,
    JWT_SECRET: 'qa-rules-local-only-secret',
    SEED_USER_EMAIL: 'rule-user@example.test',
    SEED_USER_PASSWORD: 'qa-rules-local-only-password',
    USER_DATA_PATH: path.join(temporary, 'users.json'),
    PORT: String(port),
  };
  delete environment.SEED_ADMIN_EMAIL;
  delete environment.SEED_ADMIN_PASSWORD;
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], {
    cwd: temporary, env: environment, stdio: 'ignore',
  });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`${base}/health`);
      if (response.ok) { ready = true; break; }
    } catch { /* 시작 대기 */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true, 'backend must start');
  assert.equal((await request(base, '/api/rules')).status, 401);
  const login = await request(base, '/api/auth/login', 'POST', undefined,
    { email: environment.SEED_USER_EMAIL, password: environment.SEED_USER_PASSWORD });
  assert.equal(login.status, 200);
  const token = login.body.token;
  const draft = {
    ruleKey: 'process_errors', displayName: 'Process errors',
    condition: { field: 'P.error_count', operator: 'gt', value: '0' },
    outcome: { decisionCode: 'PROCESS_ERROR', severity: 'warning' },
  };
  const created = await request(base, '/api/rules', 'POST', token, draft);
  assert.equal(created.status, 201);
  assert.equal(created.etag, '"1"');
  assert.equal(created.body.ruleSpec.state, 'draft');
  assert.equal((await request(base, '/api/rules', 'POST', token, draft)).status, 409);
  const invalid = await request(base, '/api/rules', 'POST', token,
    { ...draft, ruleKey: 'body_field', condition: { field: 'P.message_body', operator: 'eq', value: 'secret' } });
  assert.equal(invalid.status, 400);
  assert.equal((await request(base, '/api/rules/process_errors', 'PUT', token, draft)).status, 428);
  const concurrent = await Promise.all([
    request(base, '/api/rules/process_errors', 'PUT', token,
      { ...draft, ruleKey: undefined, displayName: 'First editor' }, '"1"'),
    request(base, '/api/rules/process_errors', 'PUT', token,
      { ...draft, ruleKey: undefined, displayName: 'Second editor' }, '"1"'),
  ]);
  assert.deepEqual(concurrent.map((result) => result.status).sort(), [200, 412]);
  const updateBody = { ...draft, displayName: 'Updated process errors' };
  delete updateBody.ruleKey;
  const updated = await request(base, '/api/rules/process_errors', 'PUT', token, updateBody, '"2"');
  assert.equal(updated.status, 200);
  assert.equal(updated.etag, '"3"');
  assert.equal((await request(base, '/api/rules/process_errors', 'PUT', token, updateBody, '"1"')).status, 412);
  const retired = await request(base, '/api/rules/process_errors', 'DELETE', token, undefined, '"3"');
  assert.equal(retired.status, 200);
  assert.equal(retired.body.ruleSpec.state, 'retired');
  assert.equal((await request(base, '/api/rules', 'GET', token)).body.items.length, 0);
  assert.equal((await request(base, '/api/rules/process_errors/versions', 'GET', token)).body.items.length, 4);
  const original = await request(base, '/api/rules/process_errors/versions/1', 'GET', token);
  assert.equal(original.status, 200);
  assert.equal(original.body.ruleSpec.state, 'draft');
  assert.equal(original.body.ruleSpec.displayName, 'Process errors');
  await assert.rejects(pool.query('DELETE FROM ml_rule_versions WHERE id = $1', [created.body.id]));
  console.log('rule API integration QA passed');
} finally {
  if (child) {
    if (child.exitCode === null) {
      child.kill();
      await new Promise((resolve) => child.once('exit', resolve));
    }
  }
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
