// 운영 현황 읽기 API를 서버의 DB 연결이 런타임 역할 powercode_api일 때 HTTP로 실행해 database-roles.sql 권한을 확인한다.
// operations.integration.mjs는 소유자 연결로 돌아 권한 누락을 잡지 못한다. 로컬·CI의 qa_operations* DB만 허용. 역할 스크립트는 다른 시험과 같이 superuser로 적용한다.
// 확인: API 역할로 읽기 6개 경로 200(저장공간은 pg_database_size 포함), ops_storage_measurements는 API SELECT만,
// 권한을 빼면 서버가 503을 돌려 서버 연결이 실제로 역할 권한을 쓴다. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { seedOperationsFixture } from './operations-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_operations[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_operations database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const rolesSql = await readFile(path.join(backend, '../infra/scripts/database-roles.sql'), 'utf8');
// 서버는 DATABASE_URL을 그대로 쓰므로 연결 문자열의 options로 역할을 정한다.
const apiUrl = new URL(url);
apiUrl.searchParams.set('options', '-c role=powercode_api');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-operations-grants-'));
const admin = new pg.Pool({ connectionString: url });
const api = new pg.Pool({ connectionString: apiUrl.href });
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
async function stopChild() {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise((resolve) => child.once('exit', resolve));
  child.kill();
  await exited;
}

try {
  await admin.query(rolesSql);
  const { sourceId: sid } = await seedOperationsFixture(admin);
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api');

  const port = await freePort();
  const email = 'operations-grants@example.test', password = 'qa-operations-grants-local-only';
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, stdio: 'ignore', env: {
    PATH: process.env.PATH, NODE_ENV: 'test', DATABASE_URL: apiUrl.href, JWT_SECRET: 'qa-operations-grants-local-only-secret',
    SEED_USER_EMAIL: email, SEED_USER_PASSWORD: password, USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(port),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') } });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 서버 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const { token } = await (await fetch(`${base}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }) })).json();
  assert.ok(token);
  const read = async (route) => {
    const response = await fetch(`${base}${route}`, { headers: { Authorization: `Bearer ${token}` } });
    return { status: response.status, body: await response.json() };
  };

  const routes = ['/api/operations/overview', `/api/operations/collection?sourceId=${sid}`, `/api/operations/latency?sourceId=${sid}`,
    `/api/operations/source-fields?sourceId=${sid}&layer=P`, '/api/operations/storage', '/api/operations/decision-trend?days=3'];
  for (const route of routes) {
    const { status, body } = await read(route);
    assert.equal(status, 200, `${route} ${JSON.stringify(body)}`);
  }
  assert.ok((await read('/api/operations/storage')).body.databaseBytes > 0);

  // 측정 행은 API가 읽기만 하고 다른 런타임 역할은 접근하지 않는다.
  const { rows: privileges } = await admin.query(`SELECT r AS role,p AS privilege
    FROM unnest(ARRAY['powercode_api','powercode_collector','powercode_detector','powercode_trainer','powercode_llm']) r
    CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) p WHERE has_table_privilege(r,'ops_storage_measurements',p)`);
  assert.deepEqual(privileges, [{ role: 'powercode_api', privilege: 'SELECT' }]);

  // 역할은 다른 세션에서 볼 수 없으므로 권한을 빼서 확인한다. 서버 연결이 소유자로 돌면 여기서 200이 나온다.
  await admin.query('REVOKE SELECT ON ops_storage_measurements FROM powercode_api');
  const revoked = await read('/api/operations/storage');
  assert.deepEqual([revoked.status, revoked.body.code], [503, 'operations_storage_unavailable'], 'the server connection runs as powercode_api');
  await admin.query(rolesSql);
  assert.equal((await read('/api/operations/storage')).status, 200);
  console.log('operations grants passed: six read routes 200 through the server as powercode_api, storage measurements SELECT for API only, revoked grant gives 503');
} finally {
  await stopChild();
  await api.end();
  await admin.end();
  await rm(temporary, { recursive: true, force: true });
}
