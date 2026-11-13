// 작업 목록·작업 상세(operations-ui.md '작업 목록·작업 상세') 통합 시험: 종류·상태 필터, 최신순 커서, 결과 연결 값, 시도 기록, 권한·형식 오류.
// API는 운영과 같은 powercode_api 역할로 붙는다. 작업·시도 행은 소유자 연결로 직접 넣는다(Trainer 흉내, 시험 전용).
// 로컬 전용 새 QA 데이터베이스(qa_jobs_list*)만 허용한다. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_jobs_list[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_jobs_list database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-job-list-'));
const pool = new pg.Pool({ connectionString: url });
const id = () => crypto.randomUUID();
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

try {
  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test',
    JWT_SECRET: 'qa-job-list-local-only-secret',
    SEED_USER_EMAIL: 'job-list-user@example.test', SEED_USER_PASSWORD: 'qa-job-list-local-only-password',
    SEED_ADMIN_EMAIL: 'job-list-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-job-list-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  await pool.query(await readFile(path.join(backend, '../infra/scripts/database-roles.sql'), 'utf8'));
  const runtimeUrl = new URL(url);
  runtimeUrl.searchParams.set('options', '-c role=powercode_api');
  environment.DATABASE_URL = runtimeUrl.toString();
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: ['ignore', 'ignore', 'inherit'] });
  const origin = `http://127.0.0.1:${environment.PORT}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${origin}/health`)).ok; } catch { /* 서버 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const call = async (route, token) => {
    const response = await fetch(`${origin}${route}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    return { status: response.status, body: await response.json().catch(() => null) };
  };
  const login = async (email, password) => {
    const response = await fetch(`${origin}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
    return (await response.json()).token;
  };
  const admin = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const user = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  assert.ok(admin && user);
  const actorOf = (token) => JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).id;
  const adminId = actorOf(admin), userId = actorOf(user);
  const code = (result) => [result.status, result.body?.code];

  // 시각을 정해 넣어 순서를 고정한다(분 단위 과거). 같은 시각 두 행은 id 내림차순이다.
  const at = (minutesAgo) => new Date(Date.UTC(2026, 10, 13, 12, 0) - minutesAgo * 60_000).toISOString();
  const v03 = async (kind, minutesAgo, input = {}, jobId = id()) => {
    await pool.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash,
        input_spec, max_attempts, attempt_count, next_run_at, created_at, contract_version, queue, producer_kind, producer_key)
      VALUES ($1, $2::text::job_kind, 'queued', $3, 'admin', $4, $5, $6::jsonb, 3, 0, $7, $7, 'v03', 'training', 'user', $3)`,
    [jobId, kind, adminId, id(), id(), JSON.stringify({ kind, ...input }), at(minutesAgo)]);
    return jobId;
  };
  const legacy = async (kind, minutesAgo, actor, role) => {
    const jobId = id();
    await pool.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, request_hash, input_spec, max_attempts, attempt_count, next_run_at, created_at)
      VALUES ($1, $2, 'queued', $3, $4, $5, '{}', 1, 0, $6, $6)`, [jobId, kind, actor, role, id(), at(minutesAgo)]);
    return jobId;
  };
  const attempt = async (jobId, attemptNo, status, errorCode = null) => {
    const attemptId = id();
    await pool.query(`INSERT INTO run_job_attempts (id, job_id, attempt_no, status, worker_id, lease_token, lease_until, started_at, heartbeat_at, finished_at, error_code)
      VALUES ($1, $2, $3, $4::text::attempt_status, 'trainer-test', $5, clock_timestamp() + interval '10 minutes', clock_timestamp(), clock_timestamp(),
              CASE WHEN $4::text = 'running' THEN NULL ELSE clock_timestamp() END, $6)`, [attemptId, jobId, attemptNo, status, id(), errorCode]);
    await pool.query('UPDATE run_jobs SET attempt_count = $2, current_attempt_id = $3 WHERE id = $1', [jobId, attemptNo, attemptId]);
    return attemptId;
  };

  const trainDone = await v03('train', 50, { sourceId: id(), from: '2026-10-01T00:00:00Z', until: '2026-11-01T00:00:00Z', modelKey: 'iforest' });
  const configId = id();
  await attempt(trainDone, 1, 'running');
  await pool.query(`UPDATE run_jobs SET status = 'running' WHERE id = $1`, [trainDone]);
  await pool.query(`UPDATE run_jobs SET status = 'succeeded', finished_at = clock_timestamp(), result_summary = $2::jsonb WHERE id = $1`,
    [trainDone, JSON.stringify({ configVersionId: configId, modelManifest: { modelKey: 'iforest' } })]);
  await pool.query(`UPDATE run_job_attempts SET status = 'succeeded', finished_at = clock_timestamp() WHERE job_id = $1`, [trainDone]);

  const trainFailed = await v03('train', 40);
  await attempt(trainFailed, 1, 'failed', 'trainer_unavailable');
  await attempt(trainFailed, 2, 'failed', 'insufficient_complete_training_rows');
  await pool.query(`UPDATE run_jobs SET status = 'failed', error_code = 'insufficient_complete_training_rows', finished_at = clock_timestamp() WHERE id = $1`, [trainFailed]);

  const relationRunning = await v03('recommend_relations', 30, { snapshotId: id() });
  await attempt(relationRunning, 1, 'running');
  await pool.query(`UPDATE run_jobs SET status = 'running' WHERE id = $1`, [relationRunning]);

  // 같은 시각 두 작업: id 내림차순으로 나온다.
  const [sameA, sameB] = [id(), id()].sort();
  await v03('train', 20, {}, sameA);
  await v03('train', 20, {}, sameB);
  const legacyTrain = await legacy('train', 10, adminId, 'admin');
  const userJob = await legacy('preview', 5, userId, 'user');

  // 1) 종류 필터: v03 작업만(옛 모양 행 제외), 최신순, 결과 원문 대신 연결 값만.
  const list = await call('/api/jobs?kind=train,recommend_relations', admin);
  assert.equal(list.status, 200);
  assert.deepEqual(list.body.items.map((item) => item.id), [sameB, sameA, relationRunning, trainFailed, trainDone]);
  assert.deepEqual(list.body.pagination, { limit: 50, hasMore: false, nextCursor: null });
  const done = list.body.items.at(-1);
  assert.equal(done.resultSummary, undefined, 'list carries no raw result');
  assert.deepEqual(done.result, { configVersionId: configId, evaluationId: null, evaluationSetId: null, comparisonId: null, itemCount: null, failedCount: null });
  assert.equal(done.status, 'succeeded');
  assert.equal(done.attemptCount, 1);
  assert.equal(list.body.items[0].result, null, 'no result yet');
  assert.equal(list.body.items.find((item) => item.id === trainFailed).errorCode, 'insufficient_complete_training_rows');

  // 2) 상태 필터(쉼표 목록)·종류 하나.
  assert.deepEqual((await call('/api/jobs?kind=train,recommend_relations&status=failed', admin)).body.items.map((item) => item.id), [trainFailed]);
  assert.deepEqual((await call('/api/jobs?kind=recommend_relations&status=queued,running', admin)).body.items.map((item) => item.id), [relationRunning]);
  assert.deepEqual((await call('/api/jobs?kind=compare', admin)).body.items, []);

  // 3) 커서: 2개씩 끝까지 읽으면 같은 순서·중복 없음, 마지막 쪽 nextCursor null.
  const seen = [];
  let cursor = null;
  for (let page = 0; page < 5; page += 1) {
    const result = await call(`/api/jobs?kind=train,recommend_relations&limit=2${cursor ? `&cursor=${cursor}` : ''}`, admin);
    assert.equal(result.status, 200);
    seen.push(...result.body.items.map((item) => item.id));
    cursor = result.body.pagination.nextCursor;
    assert.equal(result.body.pagination.hasMore, cursor !== null);
    if (!cursor) break;
  }
  assert.deepEqual(seen, [sameB, sameA, relationRunning, trainFailed, trainDone]);

  // 4) 필터 없음: 기존처럼 볼 수 있는 모든 작업(옛 모양 포함). 사용자는 본인 작업만.
  const all = await call('/api/jobs', admin);
  assert.deepEqual(all.body.items.slice(0, 2).map((item) => item.id), [userJob, legacyTrain]);
  assert.deepEqual((await call('/api/jobs', user)).body.items.map((item) => item.id), [userJob]);
  assert.deepEqual((await call('/api/jobs?kind=train', user)).body.items, [], 'user sees only own jobs');

  // 5) 형식 오류·권한.
  assert.deepEqual(code(await call('/api/jobs?sourceId=x', admin)), [400, 'unknown_job_filter']);
  assert.deepEqual(code(await call('/api/jobs?kind=detect', admin)), [400, 'invalid_job_kind']);
  assert.deepEqual(code(await call('/api/jobs?kind=train,train', admin)), [400, 'invalid_job_kind']);
  assert.deepEqual(code(await call('/api/jobs?kind=train,', admin)), [400, 'invalid_job_kind']);
  assert.deepEqual(code(await call('/api/jobs?status=done', admin)), [400, 'invalid_job_status']);
  assert.deepEqual(code(await call('/api/jobs?limit=0', admin)), [400, 'invalid_limit']);
  assert.deepEqual(code(await call('/api/jobs?limit=101', admin)), [400, 'invalid_limit']);
  assert.deepEqual(code(await call('/api/jobs?cursor=not-a-cursor', admin)), [400, 'invalid_cursor']);
  assert.deepEqual(code(await call('/api/jobs?kind=train&kind=compare', admin)), [400, 'invalid_job_kind'], 'repeated key is not a list');
  assert.equal((await call('/api/jobs')).status, 401);

  // 6) 상세: 시도 기록(번호 순, 작업자·lease 값 없음), 결과 원문 유지.
  const failed = await call(`/api/jobs/${trainFailed}`, admin);
  assert.equal(failed.status, 200);
  assert.deepEqual(failed.body.attempts.map((item) => [item.attemptNo, item.status, item.errorCode]),
    [[1, 'failed', 'trainer_unavailable'], [2, 'failed', 'insufficient_complete_training_rows']]);
  assert.deepEqual(Object.keys(failed.body.attempts[0]).sort(), ['attemptNo', 'errorCode', 'finishedAt', 'heartbeatAt', 'startedAt', 'status']);
  assert.equal(failed.body.errorCode, 'insufficient_complete_training_rows');
  const succeeded = await call(`/api/jobs/${trainDone}`, admin);
  assert.equal(succeeded.body.resultSummary.configVersionId, configId);
  assert.equal(succeeded.body.attempts[0].status, 'succeeded');
  assert.equal(succeeded.body.resultConfigVersionId, null);
  assert.deepEqual((await call(`/api/jobs/${sameA}`, admin)).body.attempts, []);
  assert.deepEqual(code(await call(`/api/jobs/${trainDone}`, user)), [404, 'job_not_found']);
  assert.deepEqual(code(await call('/api/jobs/not-a-job', admin)), [400, 'invalid_job_id']);
  assert.equal((await call(`/api/jobs/${userJob}`, user)).body.attempts.length, 0, 'own job readable');

  console.log('job list integration tests passed: kind/status filters, cursor, result links, attempts, permissions');
} finally {
  if (child && child.exitCode === null) {
    const exited = new Promise((resolve) => child.once('exit', resolve));
    child.kill();
    await exited;
  }
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
