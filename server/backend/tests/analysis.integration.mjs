// 이상 분석 읽기 API(analysis-ui.md) 통합 시험: 인증·분류 매핑·건수·검색·필터·정렬·페이지·상세·중간 판정 기록·설명 작업 연결·503.
// 로컬 전용 새 QA 데이터베이스(qa_analysis*)만 허용한다. 분류 건수는 DB 전체 기준이므로 판정이 없는 새 DB가 필요하다.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { seedAnalysisFixture } from './analysis-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_analysis[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_analysis database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-analysis-'));
const pool = new pg.Pool({ connectionString: url });
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
async function request(base, route, method = 'GET', token, body) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${base}${route}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  let parsedBody = null;
  try { parsedBody = JSON.parse(text); } catch { /* Express 기본 404는 JSON이 아니다. */ }
  return { status: response.status, body: parsedBody };
}
const isoTime = (value) => typeof value === 'string' && !Number.isNaN(Date.parse(value)) && value.endsWith('Z');
async function startServer(environment) {
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: 'ignore' });
  const base = `http://127.0.0.1:${environment.PORT}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 서버 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  return base;
}

try {
  const existing = await pool.query('SELECT (SELECT count(*) FROM run_process_decisions)+(SELECT count(*) FROM run_jobs) AS n');
  assert.equal(Number(existing.rows[0].n), 0, 'analysis counts are global: use a new qa_analysis database');
  const fixture = await seedAnalysisFixture(pool);
  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: url,
    JWT_SECRET: 'qa-analysis-local-only-secret',
    SEED_USER_EMAIL: 'analysis-user@example.test', SEED_USER_PASSWORD: 'qa-analysis-local-only-password',
    SEED_ADMIN_EMAIL: 'analysis-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-analysis-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  const base = await startServer(environment);
  const login = async (email, password) => (await request(base, '/api/auth/login', 'POST', undefined, { email, password })).body.token;
  const admin = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const user = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  assert.ok(admin && user);
  const D = fixture.decisions, J = fixture.jobs;
  const list = async (query = '', token = user) => {
    const response = await request(base, `/api/analysis/decisions${query}`, 'GET', token);
    assert.equal(response.status, 200, `${query} → ${JSON.stringify(response.body)}`);
    return response.body;
  };
  const ids = (body) => body.items.map((item) => item.id).sort();

  // 1) 인증 없음·잘못된 토큰 401, user/admin 모두 읽기 200, 쓰기 경로 없음.
  for (const route of ['/api/analysis/decisions', `/api/analysis/decisions/${D.normal.id}`, `/api/analysis/decisions/${D.normal.id}/interims`]) {
    assert.equal((await request(base, route)).status, 401, `unauthenticated ${route}`);
    assert.equal((await request(base, route, 'GET', 'not-a-token')).status, 401, `invalid token ${route}`);
    assert.equal((await request(base, route, 'GET', user)).status, 200, `user read ${route}`);
    assert.equal((await request(base, route, 'GET', admin)).status, 200, `admin read ${route}`);
  }
  assert.equal((await request(base, '/api/analysis/decisions', 'POST', admin, {})).status, 404);

  // 2) 분류 건수(DB 전체 기준)와 매핑: 운영 리포트와 같은 이상 신호 규칙 + 작업·중간 기록 상태.
  const all = await list('?pageSize=20');
  assert.deepEqual(all.counts, { all: 13, anomaly: 4, in_progress: 2, undetermined: 3, failed_replaced: 3 });
  assert.deepEqual(Object.keys(all.counts), ['all', 'anomaly', 'in_progress', 'undetermined', 'failed_replaced'], 'wireframe sidebar order, no normal category');
  assert.equal(all.total, 13);
  assert.equal(all.items.length, 13);
  assert.ok(isoTime(all.observedAt) && isoTime(all.latestDecidedAt));
  assert.match(all.latestEventId, /^[1-9]\d*$/, 'SSE cursor for live updates');
  const byId = new Map(all.items.map((item) => [item.id, item]));
  const expect = (itemId, kind, stage, signal) => {
    const item = byId.get(itemId);
    assert.ok(item, `item ${itemId}`);
    assert.deepEqual([item.kind, item.stage, item.signal], [kind, stage, signal], `item ${itemId}`);
    return item;
  };
  expect(D.anomaly.id, 'final', 'final', 'yes');
  expect(D.anomalyPartial.id, 'final', 'final', 'yes');
  expect(D.normal.id, 'final', 'final', 'no');
  expect(D.partialFalse.id, 'final', 'final', 'unknown');
  expect(D.unavailable.id, 'final', 'final', 'unknown');
  expect(D.noAnomalousKey.id, 'final', 'final', 'unknown');
  expect(D.demo.id, 'final', 'final', 'no');
  const replaced = expect(D.replaced.id, 'final', 'replaced', 'no');
  assert.equal(replaced.jobId, J.replaced.id);
  expect(D.v4Final.id, 'final', 'final', 'yes');
  const interimItem = expect(J.interim.id, 'interim', 'updated', 'yes');
  assert.equal(interimItem.decisionId, null);
  assert.equal(interimItem.evidence.composite.grade, 'anomaly', 'latest open interim payload');
  assert.equal(interimItem.jobStatus, 'running');
  const pendingItem = expect(J.pending.id, 'pending', 'pending', 'pending');
  assert.equal(pendingItem.evidence, null);
  assert.equal(pendingItem.status, null, 'no completeness before a final decision');
  expect(J.noFinal.id, 'failed', 'failed', 'failed');
  expect(J.failed.id, 'failed', 'failed', 'failed');
  // 문맥 필드: PROCESS·TRANSACTION 관측에서 읽음.
  const normal = byId.get(D.normal.id);
  assert.deepEqual([normal.transactionId, normal.processId, normal.interfaceId, normal.channelId, normal.processStatus, normal.responseCode],
    [D.normal.tx, D.normal.processId, D.normal.iface, 'CH-01', 'S', '0000']);
  assert.deepEqual([normal.status, normal.ruleStatus, normal.mlStatus, normal.configVersionId, normal.sourceId],
    ['complete', 'succeeded', 'succeeded', fixture.configId, fixture.sourceId]);
  assert.ok(isoTime(normal.decidedAt) && normal.decidedAt === normal.occurredAt);
  assert.equal(byId.get(J.pending.id).transactionId, J.pending.tx, 'job items carry the manifest PROCESS context');

  // 3) 정렬: 최신순(기본)·과거순, 같은 시각은 id로 고정.
  const order = (items, direction) => items.every((item, index) => index === 0 ||
    direction * (Date.parse(item.occurredAt) - Date.parse(items[index - 1].occurredAt)) >= 0);
  assert.ok(order(all.items, -1), 'newest first');
  assert.equal(all.items.at(-1).id, D.anomaly.id, 'oldest decision is last');
  const oldest = await list('?pageSize=20&sort=oldest');
  assert.ok(order(oldest.items, 1));
  assert.deepEqual(oldest.items.map((item) => item.id), [...all.items].reverse().map((item) => item.id));

  // 4) 페이지: 5/10/20, 범위를 넘으면 빈 페이지 + 전체 건수.
  const page1 = await list('?pageSize=5');
  const page3 = await list('?pageSize=5&page=3');
  assert.equal(page1.items.length, 5);
  assert.equal(page3.items.length, 3);
  assert.deepEqual([...page1.items, ...(await list('?pageSize=5&page=2')).items, ...page3.items].map((item) => item.id),
    all.items.map((item) => item.id));
  const beyond = await list('?pageSize=5&page=9');
  assert.deepEqual([beyond.items.length, beyond.total, beyond.page], [0, 13, 9]);
  assert.equal((await list()).pageSize, 10, 'default page size');

  // 5) 분류·필터 결과 집합.
  assert.deepEqual(ids(await list('?category=anomaly&pageSize=20')), [D.anomaly.id, D.anomalyPartial.id, D.v4Final.id, J.interim.id].sort());
  assert.deepEqual(ids(await list('?signal=no&pageSize=20')), [D.normal.id, D.demo.id, D.replaced.id].sort(), 'normal decisions via the signal filter');
  assert.deepEqual(ids(await list('?category=undetermined&pageSize=20')), [D.partialFalse.id, D.unavailable.id, D.noAnomalousKey.id].sort());
  assert.deepEqual(ids(await list('?category=in_progress&pageSize=20')), [J.interim.id, J.pending.id].sort());
  assert.deepEqual(ids(await list('?category=failed_replaced&pageSize=20')), [D.replaced.id, J.noFinal.id, J.failed.id].sort());
  assert.deepEqual(ids(await list('?signal=yes&completeness=partial&pageSize=20')), [D.anomalyPartial.id, D.v4Final.id].sort());
  assert.deepEqual(ids(await list('?completeness=unavailable')), [D.unavailable.id]);
  assert.deepEqual(ids(await list('?category=anomaly&signal=no')), []);
  const filtered = await list('?category=undetermined&signal=no');
  assert.deepEqual([filtered.total, filtered.counts.all], [0, 13], 'counts ignore filters and search');

  // 6) 검색: 판정 ID·작업 ID·거래 ID·프로세스 ID·인터페이스 ID 부분 일치(대소문자 무시), 특수 문자는 그대로.
  assert.deepEqual(ids(await list(`?q=${encodeURIComponent(D.normal.tx.toLowerCase())}`)), [D.normal.id]);
  assert.deepEqual(ids(await list(`?q=${D.normal.id.slice(0, 13).toUpperCase()}`)), [D.normal.id]);
  assert.deepEqual(ids(await list(`?q=${J.interim.id}`)), [J.interim.id]);
  assert.deepEqual(ids(await list(`?q=${J.v4Final.id}`)), [D.v4Final.id], 'final decisions are found by their detect job id');
  assert.equal((await list('?q=if-ord-&pageSize=20')).total, 13);
  assert.equal((await list(`?q=${encodeURIComponent(D.normal.processId)}&pageSize=20`)).items.every((item) => item.processId === D.normal.processId), true);
  assert.equal((await list('?q=%25')).total, 0, 'percent is not a wildcard');
  assert.equal((await list('?q=%20%20')).total, 13, 'blank search is no search');

  // 7) 입력 검증 400.
  const bad = async (query, code) => {
    const response = await request(base, `/api/analysis/decisions${query}`, 'GET', user);
    assert.deepEqual([response.status, response.body?.code], [400, code], query);
  };
  await bad('?x=1', 'unknown_analysis_filter');
  await bad('?category=risk', 'invalid_category');
  await bad('?category=normal', 'invalid_category');
  await bad('?signal=sign', 'invalid_signal');
  await bad('?completeness=done', 'invalid_completeness');
  await bad('?sort=severity', 'invalid_sort');
  await bad('?page=0', 'invalid_page');
  await bad('?page=1.5', 'invalid_page');
  await bad('?pageSize=50', 'invalid_page_size');
  await bad('?pageSize=05', 'invalid_page_size');
  await bad(`?q=${'a'.repeat(101)}`, 'invalid_q');
  await bad('?q=a&q=b', 'invalid_q');
  await bad("?q=a%0Ab", "invalid_q");

  // 8) 상세: 판정 ID / 작업 ID(최종 판정이 생기면 판정으로 해석) / 작업 항목 / 404·400.
  const detail = async (itemId, token = user) => (await request(base, `/api/analysis/decisions/${itemId}`, 'GET', token));
  const normalDetail = (await detail(D.normal.id)).body;
  assert.equal(normalDetail.id, D.normal.id);
  assert.deepEqual(normalDetail.configuration.models, [{ modelKey: 'iforest', adapterName: 'sklearn-iforest' }]);
  assert.deepEqual([normalDetail.configuration.ruleCount, normalDetail.configuration.detectionMode, normalDetail.configuration.id],
    [1, 'parallel', fixture.configId]);
  assert.equal(normalDetail.job, null, 'legacy decision has no detect job');
  assert.equal(normalDetail.explanation, null);
  assert.equal(normalDetail.evidence.schemaVersion, 3);
  const v4Detail = (await detail(D.v4Final.id)).body;
  assert.deepEqual([v4Detail.job.id, v4Detail.job.status, v4Detail.job.attemptCount, v4Detail.job.maxAttempts], [J.v4Final.id, 'succeeded', 1, 3]);
  assert.equal(v4Detail.evidence.composite.grade, 'anomaly_sign');
  assert.equal((await detail(J.v4Final.id)).body.id, D.v4Final.id, 'job id resolves to its final decision');
  const pendingDetail = (await detail(J.pending.id)).body;
  assert.deepEqual([pendingDetail.kind, pendingDetail.job.status, pendingDetail.job.attemptCount], ['pending', 'queued', 0]);
  const failedDetail = (await detail(J.noFinal.id)).body;
  assert.deepEqual([failedDetail.kind, failedDetail.job.status, failedDetail.job.errorCode], ['failed', 'failed', 'detection_processing_failed']);
  assert.equal(failedDetail.evidence, null, 'failed job shows no provisional evidence');
  assert.deepEqual([(await detail(crypto.randomUUID())).status, (await detail(crypto.randomUUID())).body.code], [404, 'decision_not_found']);
  assert.deepEqual([(await detail('not-a-uuid')).status, (await detail('not-a-uuid')).body.code], [400, 'invalid_decision_id']);
  assert.equal((await request(base, `/api/analysis/decisions/${D.normal.id}?x=1`, 'GET', user)).body.code, 'unknown_analysis_filter');

  // 9) 중간 판정 기록: 시도·순번 순, 대체됨·최종 없음은 DB 트리거가 기록.
  const interims = async (itemId) => (await request(base, `/api/analysis/decisions/${itemId}/interims`, 'GET', user)).body;
  const replacedHistory = await interims(D.replaced.id);
  assert.equal(replacedHistory.jobId, J.replaced.id);
  assert.deepEqual(replacedHistory.items.map((item) => [item.attemptNo, item.seq, item.kind, item.supersededByAttemptNo]),
    [[1, 1, 'first_anomaly', null], [1, 2, 'superseded', 2]]);
  assert.equal(replacedHistory.items[0].payload.composite.grade, 'anomaly_sign');
  assert.deepEqual(replacedHistory.items[1].payload, {});
  assert.ok(replacedHistory.items.every((item) => isoTime(item.createdAt)));
  assert.deepEqual((await interims(J.noFinal.id)).items.map((item) => [item.kind, item.reasonCode]),
    [['first_anomaly', null], ['no_final', 'detection_processing_failed']]);
  assert.deepEqual((await interims(J.interim.id)).items.map((item) => [item.seq, item.kind, item.evidenceVersion]),
    [[1, 'first_anomaly', 'detector-evidence-v4'], [2, 'evidence_added', 'detector-evidence-v4']]);
  assert.deepEqual(await interims(D.normal.id), { id: D.normal.id, jobId: null, items: [] });
  assert.deepEqual((await interims(J.pending.id)).items, []);
  assert.equal((await request(base, `/api/analysis/decisions/${crypto.randomUUID()}/interims`, 'GET', user)).status, 404);

  // 10) 설명 작업: 요청자 본인의 최근 explain 작업만 상세에 연결(다른 계정에는 보이지 않음).
  const job = await request(base, '/api/jobs', 'POST', user, { kind: 'explain', sourceId: fixture.sourceId, decisionId: D.normal.id });
  assert.equal(job.status, 201);
  const linked = (await detail(D.normal.id)).body.explanation;
  assert.deepEqual([linked.jobId, linked.status], [job.body.id, 'queued']);
  assert.equal((await detail(D.normal.id, admin)).body.explanation, null);
  // 기존 판정 API·SSE 경로는 그대로.
  assert.equal((await request(base, '/api/pipeline/decisions?limit=3', 'GET', user)).body.items.length, 3);

  // 11) 저장소 실패는 503(성공·빈 목록 대체 없음).
  await stopChild();
  const failingBase = await startServer({ ...environment, DATABASE_URL: 'postgresql://nobody:none@127.0.0.1:1/qa_analysis_missing' });
  for (const route of ['/api/analysis/decisions', `/api/analysis/decisions/${D.normal.id}`, `/api/analysis/decisions/${D.normal.id}/interims`]) {
    const failed = await request(failingBase, route, 'GET', user);
    assert.deepEqual([failed.status, failed.body.code], [503, 'analysis_storage_unavailable'], route);
  }
  console.log('analysis read API integration passed: 401/user+admin read, category mapping and counts, sort, pages, filters, search, '
    + '400 validation, detail resolution, interim history, own explain job link, 503');
} finally {
  await stopChild();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}

async function stopChild() {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise((resolve) => child.once('exit', resolve));
  child.kill();
  await exited;
}
