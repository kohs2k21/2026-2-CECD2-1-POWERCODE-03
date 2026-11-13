// 이상 분석 API(analysis-ui.md) 통합 시험: 인증·분류 매핑·건수(판정 결과별·위험도별·분류함)·검색·필터·정렬·페이지·상세·중간 판정 기록·
// 설명 작업 연결·처리 상태·조사·조치 기록·읽음·Process Flow·SSE 중간 판정 이벤트·v04(구성 없는 작업·판정)·503.
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
import { seedAnalysisFixture, seedAnalysisV04Fixture, seedParentlessDecisions } from './analysis-fixture.mjs';

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

  // 1) 인증 없음·잘못된 토큰 401, user/admin 모두 읽기 200. 목록 쓰기 경로 없음.
  for (const route of ['/api/analysis/decisions', `/api/analysis/decisions/${D.normal.id}`, `/api/analysis/decisions/${D.normal.id}/interims`,
    `/api/analysis/decisions/${D.normal.id}/flow`, `/api/analysis/decisions/${D.normal.id}/graph`]) {
    assert.equal((await request(base, route)).status, 401, `unauthenticated ${route}`);
    assert.equal((await request(base, route, 'GET', 'not-a-token')).status, 401, `invalid token ${route}`);
    assert.equal((await request(base, route, 'GET', user)).status, 200, `user read ${route}`);
    assert.equal((await request(base, route, 'GET', admin)).status, 200, `admin read ${route}`);
  }
  assert.equal((await request(base, '/api/analysis/decisions', 'POST', admin, {})).status, 404);
  for (const [route, method, body] of [[`/api/analysis/decisions/${D.normal.id}/workflow`, 'PUT', { state: 'open', expectedState: 'detected' }],
    [`/api/analysis/decisions/${D.normal.id}/read`, 'PUT', undefined], [`/api/analysis/decisions/${D.normal.id}/feedback`, 'GET', undefined],
    [`/api/analysis/decisions/${D.normal.id}/feedback`, 'POST', { kind: 'action', reason: 'x' }]]) {
    assert.equal((await request(base, route, method, undefined, body)).status, 401, `unauthenticated ${method} ${route}`);
    assert.equal((await request(base, route, method, 'not-a-token', body)).status, 401, `invalid token ${method} ${route}`);
  }

  // 2) 분류 건수(DB 전체 기준)와 매핑: 운영 리포트와 같은 이상 신호 규칙 + 작업·중간 기록 상태.
  const all = await list('?pageSize=20');
  const counts0 = { all: 15, anomaly: 3, anomaly_sign: 3, normal: 3, in_progress: 2, undetermined: 3, failed_replaced: 3,
    risk_all: 6, risk_critical: 1, risk_warning: 1, risk_info: 1, risk_unrated: 3, workflow_open: 0, workflow_resolved: 0 };
  assert.deepEqual(all.counts, counts0);
  assert.deepEqual(Object.keys(all.counts), Object.keys(counts0), 'sidebar order (result view with normal after anomaly sign, risk view, workflow box)');
  assert.equal(all.total, 15);
  assert.equal(all.items.length, 15);
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
  expect(D.v4Info.id, 'final', 'final', 'yes');
  expect(D.v4MlOnly.id, 'final', 'final', 'yes');
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
  // 목록 이상 원인(causes, 사용자 11/13): 이상 신호 항목의 저장 evidence에서 anomalous=true 방법만, 이름은 방법 카탈로그. 그 밖은 [].
  const cause = (overrides) => ({ category: 'hard', methodId: null, ruleKey: null, code: null, observedStatus: null, fieldName: null, operator: null,
    threshold: null, unit: null, observedValue: null, modelName: null, score: null, scoreThreshold: null, ...overrides });
  const statusCause = (extra = {}) => cause({ methodId: 'hard:process-status', observedStatus: 'F', ...extra });
  assert.deepEqual(byId.get(D.anomaly.id).causes, [statusCause()], 'v3 status rule hit');
  assert.deepEqual(byId.get(D.v4Info.id).causes, [cause({ category: 'soft', methodId: 'soft:retry_count-1a2b3c4d', code: 'SOFT_RETRY_COUNT_1A2B3C4D',
    fieldName: '재시도 수', operator: 'gte', threshold: '3', observedValue: '4' })], 'v4 soft rule with catalog field name');
  assert.deepEqual(byId.get(D.v4MlOnly.id).causes, [cause({ category: 'ml', methodId: 'ml:iforest', code: 'IF_ANOMALY', modelName: 'Isolation Forest',
    score: 0.71, scoreThreshold: '0.61' })], 'v4 ML with catalog model name');
  const elapsedCause = cause({ category: 'soft', methodId: 'soft:elapsed_ms-3f9a2c1d', code: 'SOFT_ELAPSED_MS_3F9A2C1D', fieldName: '처리 시간',
    operator: 'gt', threshold: '5000', unit: 'ms', observedValue: '8120' });
  assert.deepEqual(byId.get(J.interim.id).causes, [cause({ methodId: 'hard:process-status', code: 'PROCESS_STATUS_F' }), elapsedCause],
    'interim payload causes in basis order');
  assert.deepEqual(byId.get(D.v4Final.id).causes, [elapsedCause]);
  for (const other of [D.normal, D.partialFalse, D.unavailable, D.replaced, J.pending, J.failed, J.noFinal])
    assert.deepEqual(byId.get(other.id).causes, [], `no causes without an anomaly signal ${other.id}`);

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
  assert.equal(page3.items.length, 5);
  assert.deepEqual([...page1.items, ...(await list('?pageSize=5&page=2')).items, ...page3.items].map((item) => item.id),
    all.items.map((item) => item.id));
  const beyond = await list('?pageSize=5&page=9');
  assert.deepEqual([beyond.items.length, beyond.total, beyond.page], [0, 15, 9]);
  assert.equal((await list()).pageSize, 10, 'default page size');

  // 5) 분류·필터 결과 집합.
  assert.deepEqual(ids(await list('?category=anomaly&pageSize=20')), [D.anomaly.id, D.anomalyPartial.id, J.interim.id].sort(),
    'anomaly = signal yes without a stored anomaly_sign grade (v3 grade null stays 이상)');
  assert.deepEqual(ids(await list('?category=anomaly_sign&pageSize=20')), [D.v4Final.id, D.v4Info.id, D.v4MlOnly.id].sort());
  assert.deepEqual(ids(await list('?signal=no&pageSize=20')), [D.normal.id, D.demo.id, D.replaced.id].sort(), 'normal decisions via the signal filter');
  // 정상 분류(사용자 11/13) = 이상 신호 없음(complete ∧ anomalous=false). 부분+false·판정불가·작업 항목은 들지 않는다.
  assert.deepEqual(ids(await list('?category=normal&pageSize=20')), [D.normal.id, D.demo.id, D.replaced.id].sort(), 'normal category');
  assert.deepEqual(ids(await list('?category=normal&signal=yes')), [], 'normal and anomaly signal never overlap');
  assert.deepEqual(ids(await list('?category=undetermined&pageSize=20')), [D.partialFalse.id, D.unavailable.id, D.noAnomalousKey.id].sort());
  assert.deepEqual(ids(await list('?category=in_progress&pageSize=20')), [J.interim.id, J.pending.id].sort());
  assert.deepEqual(ids(await list('?category=failed_replaced&pageSize=20')), [D.replaced.id, J.noFinal.id, J.failed.id].sort());
  assert.deepEqual(ids(await list('?signal=yes&completeness=partial&pageSize=20')), [D.anomalyPartial.id, D.v4Final.id].sort());
  assert.deepEqual(ids(await list('?signal=anomaly&completeness=partial&pageSize=20')), [D.anomalyPartial.id]);
  assert.deepEqual(ids(await list('?signal=anomaly_sign&pageSize=20')), [D.v4Final.id, D.v4Info.id, D.v4MlOnly.id].sort());
  assert.deepEqual(ids(await list('?completeness=unavailable')), [D.unavailable.id]);
  assert.deepEqual(ids(await list('?category=anomaly&signal=no')), []);
  const filtered = await list('?category=undetermined&signal=no');
  assert.deepEqual([filtered.total, filtered.counts.all], [0, 15], 'counts ignore filters and search');

  // 6) 검색: 판정 ID·작업 ID·거래 ID·프로세스 ID·인터페이스 ID 부분 일치(대소문자 무시), 특수 문자는 그대로.
  assert.deepEqual(ids(await list(`?q=${encodeURIComponent(D.normal.tx.toLowerCase())}`)), [D.normal.id]);
  assert.deepEqual(ids(await list(`?q=${D.normal.id.slice(0, 13).toUpperCase()}`)), [D.normal.id]);
  assert.deepEqual(ids(await list(`?q=${J.interim.id}`)), [J.interim.id]);
  assert.deepEqual(ids(await list(`?q=${J.v4Final.id}`)), [D.v4Final.id], 'final decisions are found by their detect job id');
  assert.equal((await list('?q=if-ord-&pageSize=20')).total, 15);
  assert.equal((await list(`?q=${encodeURIComponent(D.normal.processId)}&pageSize=20`)).items.every((item) => item.processId === D.normal.processId), true);
  assert.equal((await list('?q=%25')).total, 0, 'percent is not a wildcard');
  assert.equal((await list('?q=%20%20')).total, 15, 'blank search is no search');

  // 7) 입력 검증 400.
  const bad = async (query, code) => {
    const response = await request(base, `/api/analysis/decisions${query}`, 'GET', user);
    assert.deepEqual([response.status, response.body?.code], [400, code], query);
  };
  await bad('?x=1', 'unknown_analysis_filter');
  await bad('?category=risk', 'invalid_category');
  await bad('?category=no', 'invalid_category');
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

  // 12) 등급·위험도: 저장된 v4 composite.grade·composite.risk만(재계산 없음). v3 이상 = 등급 null·위험도 미정, 이상 아님 = 해당 없음.
  const riskOf = (itemId) => { const item = byId.get(itemId); return [item.grade, item.riskStatus, item.riskLevel]; };
  assert.deepEqual(riskOf(D.anomaly.id), [null, 'unrated', null], 'v3 anomaly: grade null, risk unrated (U6)');
  assert.deepEqual(riskOf(D.normal.id), [null, 'not_applicable', null]);
  assert.deepEqual(riskOf(D.unavailable.id), [null, 'not_applicable', null]);
  assert.deepEqual(riskOf(D.v4Final.id), ['anomaly_sign', 'rated', 'warning']);
  assert.deepEqual(riskOf(D.v4Info.id), ['anomaly_sign', 'rated', 'info']);
  assert.deepEqual(riskOf(D.v4MlOnly.id), ['anomaly_sign', 'unrated', null], 'stored unrated (ML only) stays unrated');
  assert.deepEqual(riskOf(J.interim.id), ['anomaly', 'rated', 'critical'], 'interim item uses the latest open payload');
  assert.deepEqual(riskOf(J.pending.id), [null, 'not_applicable', null]);
  assert.deepEqual(riskOf(D.replaced.id), [null, 'not_applicable', null], 'withdrawn interim risk is not carried to the final decision');
  assert.deepEqual(ids(await list('?category=risk_all&pageSize=20')),
    [D.anomaly.id, D.anomalyPartial.id, D.v4Final.id, D.v4Info.id, D.v4MlOnly.id, J.interim.id].sort());
  assert.deepEqual(ids(await list('?category=risk_critical')), [J.interim.id]);
  assert.deepEqual(ids(await list('?category=risk_warning')), [D.v4Final.id]);
  assert.deepEqual(ids(await list('?category=risk_info')), [D.v4Info.id]);
  assert.deepEqual(ids(await list('?category=risk_unrated')), [D.anomaly.id, D.anomalyPartial.id, D.v4MlOnly.id].sort());
  assert.ok(all.items.every((item) => item.workflowState === 'detected' && item.unread === true), 'initial state: detected, unread');

  // 13) 처리 상태: 감지됨 → 보류 → 완료 → 원래 상태로 복원, 요청자·시각 기록. 작업 항목은 작업 ID로 저장되어 최종 판정으로 이어진다.
  const workflow = (itemId, body, token = user, raw) => raw
    ? fetch(`${base}/api/analysis/decisions/${itemId}/workflow`, { method: 'PUT', headers: { Authorization: `Bearer ${token}`, ...raw.headers }, body: raw.body })
      .then(async (response) => ({ status: response.status, body: await response.json().catch(() => null) }))
    : request(base, `/api/analysis/decisions/${itemId}/workflow`, 'PUT', token, body);
  const opened = await workflow(D.anomaly.id, { state: 'open', expectedState: 'detected' });
  assert.equal(opened.status, 200, JSON.stringify(opened.body));
  assert.deepEqual([opened.body.id, opened.body.workflow.state], [D.anomaly.id, 'open']);
  assert.ok(isoTime(opened.body.workflow.changedAt));
  const anomalyDetail = (await detail(D.anomaly.id)).body;
  assert.deepEqual(anomalyDetail.workflow, { state: 'open', changedAt: opened.body.workflow.changedAt });
  assert.equal(anomalyDetail.evidence.schemaVersion, 3, 'decision values unchanged');
  assert.deepEqual(ids(await list('?category=workflow_open')), [D.anomaly.id]);
  assert.deepEqual([(await workflow(D.anomaly.id, { state: 'resolved', expectedState: 'detected' })).body.code], ['workflow_state_conflict'],
    'stale expected state');
  assert.equal((await workflow(D.anomaly.id, { state: 'resolved', expectedState: 'open' }, admin)).body.workflow.state, 'resolved', 'admin may change too');
  const notAllowed = await workflow(D.anomaly.id, { state: 'open', expectedState: 'resolved' });
  assert.deepEqual([notAllowed.status, notAllowed.body.code], [409, 'workflow_transition_not_allowed'], 'resolved → open is not a wireframe action');
  assert.deepEqual([(await workflow(D.anomaly.id, { state: 'resolved', expectedState: 'resolved' })).body.code], ['workflow_transition_not_allowed']);
  assert.equal((await workflow(D.anomaly.id, { state: 'detected', expectedState: 'resolved' })).body.workflow.state, 'detected', 'restore');
  const history = await pool.query(`SELECT seq,from_state,to_state,actor_id,job_id,decision_id FROM run_decision_workflow_events
    WHERE decision_id=$1 ORDER BY seq`, [D.anomaly.id]);
  const [userId, adminId] = await Promise.all([user, admin].map(async (token) => (await request(base, '/api/auth/me', 'GET', token)).body.user.id));
  assert.deepEqual(history.rows.map((row) => [row.seq, row.from_state, row.to_state, row.actor_id, row.job_id]),
    [[1, 'detected', 'open', userId, null], [2, 'open', 'resolved', adminId, null], [3, 'resolved', 'detected', userId, null]], 'append-only history with actors');
  // 작업 항목: 작업 ID 대상. 그 작업의 최종 판정을 판정 ID로 바꿔도 같은 대상.
  assert.equal((await workflow(D.v4Final.id, { state: 'open', expectedState: 'detected' })).status, 200);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM run_decision_workflow_events WHERE job_id=$1', [J.v4Final.id])).rows[0].n, 1,
    'final decision with a detect job is stored by job id');
  assert.equal((await detail(J.v4Final.id)).body.workflow.state, 'open');
  assert.equal((await workflow(J.pending.id, { state: 'resolved', expectedState: 'detected' })).body.workflow.state, 'resolved');
  const workflowCounts = (await list()).counts;
  assert.deepEqual([workflowCounts.workflow_open, workflowCounts.workflow_resolved], [1, 1]);
  assert.deepEqual(ids(await list('?category=workflow_resolved')), [J.pending.id]);
  // 동시 변경: 같은 기대 상태로 두 요청 → 하나만 성공, 나머지 409.
  const raced = await Promise.all([1, 2, 3].map(() => workflow(D.normal.id, { state: 'open', expectedState: 'detected' })));
  assert.deepEqual(raced.map((r) => r.status).sort(), [200, 409, 409], JSON.stringify(raced.map((r) => r.body)));
  assert.ok(raced.filter((r) => r.status === 409).every((r) => r.body.code === 'workflow_state_conflict'));
  // 입력 검증 400·404.
  const badWorkflow = async (body, code = 'invalid_workflow_request', raw) => {
    const response = await workflow(D.normal.id, body, user, raw);
    assert.deepEqual([response.status, response.body?.code], [400, code], JSON.stringify(body ?? raw));
  };
  await badWorkflow({ state: 'open' });
  await badWorkflow({ state: 'open', expectedState: 'detected', note: 'x' });
  await badWorkflow({ state: 'closed', expectedState: 'open' });
  await badWorkflow({ state: 'Open', expectedState: 'open' });
  await badWorkflow({ state: 1, expectedState: 'open' });
  await badWorkflow(undefined, 'invalid_workflow_request', { headers: {}, body: undefined });
  await badWorkflow(undefined, 'invalid_workflow_request', { headers: { 'Content-Type': 'text/plain' }, body: '{"state":"detected","expectedState":"open"}' });
  await badWorkflow(undefined, 'invalid_json', { headers: { 'Content-Type': 'application/json' }, body: '{"state":' });
  // 본문 해석 단계의 다른 요청 오류도 4xx 그대로(503 저장소 오류로 바꾸지 않음).
  const charset = await workflow(D.normal.id, undefined, user, { headers: { 'Content-Type': 'application/json; charset=latin1' }, body: '{"state":"open","expectedState":"detected"}' });
  assert.deepEqual([charset.status, charset.body?.code], [415, 'unsupported_charset']);
  const encoding = await workflow(D.normal.id, undefined, user, { headers: { 'Content-Type': 'application/json', 'Content-Encoding': 'x-unknown' }, body: '{}' });
  assert.deepEqual([encoding.status, encoding.body?.code], [415, 'unsupported_encoding']);
  await badWorkflow(undefined, 'invalid_workflow_request', { headers: { 'Content-Type': 'application/json' }, body: '["open"]' });
  const tooLarge = await workflow(D.normal.id, undefined, user, { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state: 'x'.repeat(20_000) }) });
  assert.deepEqual([tooLarge.status, tooLarge.body.code], [413, 'request_too_large']);
  assert.deepEqual([(await workflow(crypto.randomUUID(), { state: 'open', expectedState: 'detected' })).status], [404]);
  assert.deepEqual([(await workflow('nope', { state: 'open', expectedState: 'detected' })).body.code], ['invalid_decision_id']);

  // 14) 조사·조치 기록: 관리자만 읽기·쓰기(와이어프레임 feedbackBox는 관리자에게만), append-only, 판정 값 불변.
  const feedbackRoute = (itemId) => `/api/analysis/decisions/${itemId}/feedback`;
  const forbidden = await request(base, feedbackRoute(D.anomaly.id), 'POST', user, { kind: 'action', reason: '조치함' });
  assert.deepEqual([forbidden.status, forbidden.body.code], [403, 'admin_required']);
  assert.deepEqual([(await request(base, feedbackRoute(D.anomaly.id), 'GET', user)).status], [403]);
  const evidenceBefore = (await pool.query('SELECT evidence,status FROM run_process_decisions WHERE id=$1', [D.anomaly.id])).rows[0];
  const saved = await request(base, feedbackRoute(D.anomaly.id), 'POST', admin, { kind: 'suspected-false-positive', reason: '  배치 재처리로 생긴 F 상태\n확인함  ' });
  assert.equal(saved.status, 201, JSON.stringify(saved.body));
  assert.deepEqual([saved.body.id, saved.body.item.kind, saved.body.item.reason, saved.body.item.actorId, saved.body.item.actorEmail],
    [D.anomaly.id, 'suspected-false-positive', '배치 재처리로 생긴 F 상태\n확인함', adminId, environment.SEED_ADMIN_EMAIL]);
  assert.ok(isoTime(saved.body.item.createdAt));
  const longest = 'ㄱ'.repeat(2000);
  assert.equal((await request(base, feedbackRoute(D.anomaly.id), 'POST', admin, { kind: 'action', reason: longest })).status, 201, '2000 characters allowed');
  assert.deepEqual((await pool.query('SELECT evidence,status FROM run_process_decisions WHERE id=$1', [D.anomaly.id])).rows[0], evidenceBefore,
    'feedback does not change the decision');
  const feedbackList = (await request(base, feedbackRoute(D.anomaly.id), 'GET', admin)).body;
  assert.deepEqual(feedbackList.items.map((item) => [item.kind, item.reason.length]), [['action', 2000], ['suspected-false-positive', 19]], 'newest first');
  assert.deepEqual((await request(base, feedbackRoute(D.normal.id), 'GET', admin)).body, { id: D.normal.id, items: [] });
  // 작업 항목 기록은 작업 ID 대상(최종 판정이 생겨도 이어짐).
  assert.equal((await request(base, feedbackRoute(J.v4Final.id), 'POST', admin, { kind: 'insufficient-evidence', reason: '모델 근거만 있음' })).body.id, D.v4Final.id);
  assert.equal((await request(base, feedbackRoute(D.v4Final.id), 'GET', admin)).body.items.length, 1);
  for (const body of [{ kind: 'action' }, { kind: 'action', reason: '   ' }, { kind: 'action', reason: 'ㄱ'.repeat(2001) }, { kind: 'label', reason: 'x' },
    { kind: 'action', reason: 'x', label: true }, { kind: 'action', reason: 7 }, { kind: 'action', reason: 'a\u0000b' }]) {
    const response = await request(base, feedbackRoute(D.anomaly.id), 'POST', admin, body);
    assert.deepEqual([response.status, response.body?.code], [400, 'invalid_feedback_request'], JSON.stringify(body).slice(0, 60));
  }
  assert.equal((await request(base, feedbackRoute(crypto.randomUUID()), 'POST', admin, { kind: 'action', reason: 'x' })).status, 404);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM run_decision_feedback')).rows[0].n, 3, 'rejected requests store nothing');
  // 확정 라벨·학습 입력으로 쓰이지 않음: 라벨 근거 표에 아무것도 생기지 않는다.
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM data_label_evidence')).rows[0].n, 0);

  // 15) 사용자별 읽음: 상세를 열면(PUT …/read {occurredAt}) 그 사용자에게만 화면이 본 항목 시각까지 확인됨.
  //     그 뒤 생긴 중간 판정·최종 판정(더 늦은 occurredAt)은 미확인으로 남는다(열람 경합에서도).
  const listed = async (itemId, token = user) => (await list('?pageSize=20', token)).items.find((item) => item.id === itemId);
  const unread = async (itemId, token) => (await listed(itemId, token)).unread;
  const markRead = (itemId, body, token = user) => request(base, `/api/analysis/decisions/${itemId}/read`, 'PUT', token, body);
  const normalSeen = (await listed(D.normal.id)).occurredAt;
  const read = await markRead(D.normal.id, { occurredAt: normalSeen });
  assert.equal(read.status, 200, JSON.stringify(read.body));
  assert.ok(read.body.id === D.normal.id && isoTime(read.body.readAt));
  assert.equal(read.body.readAt, normalSeen, 'read time = the item time the screen saw');
  assert.deepEqual([await unread(D.normal.id, user), await unread(D.normal.id, admin), await unread(D.anomaly.id, user)], [false, true, true]);
  const older = await markRead(D.normal.id, { occurredAt: '2020-01-01T00:00:00Z' });
  assert.equal(older.body.readAt, normalSeen, 'an older screen never moves the read time back');
  const future = await markRead(D.normal.id, { occurredAt: '2999-01-01T00:00:00.000000Z' });
  assert.ok(Date.parse(future.body.readAt) <= Date.now() + 1000, 'a future time is capped at now');
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM run_decision_reads WHERE decision_id=$1', [D.normal.id])).rows[0].n, 1);
  // 작업 항목: 작업 ID 대상. 화면이 이전 중간 판정 시각을 보고 연 경우(그 뒤 근거 추가가 저장됨) → 여전히 미확인.
  const interimSeen = (await listed(J.interim.id)).occurredAt;
  const firstInterim = (await pool.query(`SELECT to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS t FROM run_decision_interims
    WHERE job_id=$1 AND kind='first_anomaly'`, [J.interim.id])).rows[0].t;
  assert.ok(firstInterim < interimSeen);
  assert.equal((await markRead(J.interim.id, { occurredAt: firstInterim })).status, 200);
  assert.equal(await unread(J.interim.id, user), true, 'a newer interim than the one the screen saw stays unread');
  assert.equal((await markRead(J.interim.id, { occurredAt: interimSeen })).status, 200);
  assert.equal(await unread(J.interim.id, user), false);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM run_decision_reads WHERE job_id=$1', [J.interim.id])).rows[0].n, 1);
  for (const body of [undefined, {}, { at: 'x' }, { occurredAt: 'yesterday' }, { occurredAt: '2026-11-03' }, { occurredAt: '2026-11-03T01:02:03' },
    { occurredAt: 1762131723 }, { occurredAt: normalSeen, extra: 1 }, { occurredAt: '2026-02-30T00:00:00Z' }]) {
    const response = await markRead(D.normal.id, body);
    assert.deepEqual([response.status, response.body?.code], [400, 'invalid_read_request'], JSON.stringify(body));
  }
  assert.equal((await markRead(crypto.randomUUID(), { occurredAt: normalSeen })).status, 404);
  assert.deepEqual((await list()).counts.all, 15, 'read marks do not change counts');

  // 16) Process Flow: 같은 원본 안 같은 거래 ID의 TRANSACTION·PROCESS·MESSAGE(실행마다 최신 리비전). BODY·응답 메시지 원문 없음.
  const flowOf = async (itemId, token = user) => (await request(base, `/api/analysis/decisions/${itemId}/flow`, 'GET', token));
  const flow = (await flowOf(D.anomaly.id)).body;
  assert.equal(flow.id, D.anomaly.id);
  assert.deepEqual(flow.transaction, { transactionId: D.anomaly.tx, interfaceId: D.anomaly.iface, channelId: null, status: 'S', responseCode: null,
    processTimeMs: null, processCount: null });
  assert.deepEqual(flow.processes.map((p) => [p.processId, p.status, p.focus, p.dependsOn, p.totalCount]),
    [['P99', 'S', false, D.anomaly.processId, '5'], [D.anomaly.processId, 'F', true, null, null]], 'latest revision per execution');
  assert.ok(flow.processes.find((p) => p.focus).observationId === D.anomaly.obs);
  assert.deepEqual(flow.messages.map((m) => [m.processId, m.messageId, m.direction, m.status, m.dataSize]), [
    [D.anomaly.processId, `MSG-${D.anomaly.processId}-REQ`, 'REQ', 'S', '512'],
    [D.anomaly.processId, `MSG-${D.anomaly.processId}-RES`, 'RES', 'F', '128'],
    ['P99', 'MSG-P99-REQ', 'REQ', 'S', '64'],
  ], 'other source rows excluded, latest message revision');
  assert.equal(flow.truncated, false);
  assert.deepEqual(flow.counts, { processes: 2, messages: 3, errors: 1 }, 'true totals for the flow header');
  const flowText = JSON.stringify(flow);
  assert.ok(!/fixture response text|body|message_in|message_out|startTime/i.test(flowText), 'no BODY, response message or raw times');
  // Flow 응답 형식 불변(그래프 추가 전과 같은 필드·순서): 그래프 전용 열·추가 조회 결과가 섞이지 않는다.
  assert.deepEqual(Object.keys(flow), ['id', 'jobId', 'transaction', 'processes', 'messages', 'truncated', 'counts']);
  assert.deepEqual(Object.keys(flow.transaction), ['transactionId', 'interfaceId', 'channelId', 'status', 'responseCode', 'processTimeMs', 'processCount']);
  assert.ok(flow.processes.every((p) => JSON.stringify(Object.keys(p))
    === JSON.stringify(['observationId', 'processId', 'dependsOn', 'status', 'responseCode', 'totalCount', 'focus'])), 'process fields and order');
  assert.ok(flow.messages.every((m) => JSON.stringify(Object.keys(m))
    === JSON.stringify(['observationId', 'messageId', 'processId', 'direction', 'status', 'dataSize'])), 'message fields and order');
  assert.equal((await flowOf(D.anomaly.id, admin)).body.messages.length, 3, 'admin sees the same fields (BODY is never read)');
  const pendingFlow = (await flowOf(J.pending.id)).body;
  assert.deepEqual([pendingFlow.transaction.transactionId, pendingFlow.processes.length, pendingFlow.processes[0].focus, pendingFlow.messages],
    [J.pending.tx, 1, true, []], 'job items use the manifest PROCESS');
  assert.equal((await flowOf(crypto.randomUUID())).status, 404);
  assert.equal((await flowOf('bad')).body.code, 'invalid_decision_id');

  // 16-1) 실행 그래프(층 A, contracts/process-graph.md): Flow와 같은 조회 결과로 만든 nodes·edges.
  const graphOf = async (itemId, token = user) => (await request(base, `/api/analysis/decisions/${itemId}/graph`, 'GET', token));
  const graph = (await graphOf(D.anomaly.id)).body;
  assert.deepEqual([graph.id, graph.truncated, graph.counts], [D.anomaly.id, false, { processes: 2, messages: 3, errors: 1 }]);
  assert.deepEqual([graph.graph.layer, graph.graph.id, graph.graph.version, graph.graph.sourceId], ['instance', D.anomaly.tx, null, fixture.sourceId]);
  assert.ok(isoTime(graph.graph.generatedAt));
  const graphKinds = graph.nodes.reduce((acc, n) => ({ ...acc, [n.kind]: (acc[n.kind] ?? 0) + 1 }), {});
  assert.deepEqual(graphKinds, { transaction: 1, process: 2, message: 3 });
  const txNode = `transaction:${D.anomaly.tx}`, focusNode = `process:${D.anomaly.tx}:${D.anomaly.processId}`, p99Node = `process:${D.anomaly.tx}:P99`;
  const edgeOf = (kind, from, to) => graph.edges.find((e) => e.kind === kind && e.from === from && e.to === to);
  assert.equal(edgeOf('contains', txNode, focusNode).verification, 'verified', 'focus PROCESS has the transaction FK');
  assert.equal(edgeOf('contains', txNode, p99Node).verification, 'not_checked', 'string grouping only');
  assert.deepEqual([edgeOf('precedes', focusNode, p99Node).verification, edgeOf('precedes', focusNode, p99Node).evidence],
    ['verified', { handoff: 'undetermined', reason: 'no_out_message' }], 'P99 depends on the focus PROCESS; REQ/RES are not OUT/IN');
  const p99 = graph.nodes.find((n) => n.id === p99Node);
  assert.deepEqual([p99.attrs.attempts.length, p99.attrs.attempts[0].revisionNo, p99.attrs.status, p99.attrs.verification], [1, '2', 'S', null],
    'a newer revision of the same execution is not a second attempt');
  const focusAttempt = graph.nodes.find((n) => n.id === focusNode).attrs.attempts[0];
  assert.deepEqual([focusAttempt.observationId, focusAttempt.decisionId, focusAttempt.focus], [D.anomaly.obs, D.anomaly.id, true]);
  const graphTx = graph.nodes.find((n) => n.id === txNode).attrs;
  assert.deepEqual([graphTx.retries.length, graphTx.retries[0].observationId, graphTx.retriesTruncated], [1, graphTx.observationId, false],
    'transaction retries come from the graph-only lookup');
  // 재시도 상한: 상한을 넘으면 앞쪽만 두고 retriesTruncated=true(서비스 함수 직접 호출, 상한 0).
  const { readDecisionGraph } = await import('../dist/services/analysisReadModel.js');
  const cappedRetries = await readDecisionGraph(pool, D.anomaly.id, undefined, undefined, 0);
  const cappedTx = cappedRetries.nodes.find((n) => n.id === txNode).attrs;
  assert.deepEqual([cappedTx.retries, cappedTx.retriesTruncated], [[], true]);
  assert.equal(cappedRetries.nodes.find((n) => n.id === focusNode).attrs.attempts[0].decisionId, D.anomaly.id);
  assert.ok(graph.edges.filter((e) => e.kind === 'contains' && e.to.startsWith('message:')).every((e) => e.verification === 'not_checked'));
  assert.ok(!/fixture response text|body|startTime|start_time/i.test(JSON.stringify(graph)), 'graph exposes no BODY, response message or raw times');
  assert.equal((await graphOf(crypto.randomUUID())).status, 404);
  assert.equal((await graphOf('bad')).body.code, 'invalid_decision_id');

  // 17) SSE: 기본은 최종 판정 이벤트만(`event: decision`, 기존 소비자 형식 + eventKind), include=interims면 중간 판정 기록도 `event: interim`.
  const readStream = async (query, after = '0') => {
    const controller = new AbortController();
    const response = await fetch(`${base}/api/pipeline/events${query}`, { headers: { Authorization: `Bearer ${user}`, 'Last-Event-ID': after }, signal: controller.signal });
    if (response.status !== 200) return { status: response.status, body: await response.json() };
    const reader = response.body.getReader();
    let text = '';
    const deadline = Date.now() + 3000;
    while (Date.now() < deadline && !text.includes(': keepalive')) {
      const chunk = await reader.read();
      if (chunk.done) break;
      text += new TextDecoder().decode(chunk.value);
    }
    controller.abort();
    const frames = text.split('\n\n').filter((frame) => frame.startsWith('id: ')).map((frame) => {
      const [, eventId, name, data] = /^id: (\d+)\nevent: (\w+)\ndata: (.*)$/s.exec(frame);
      return { eventId, name, data: JSON.parse(data) };
    });
    return { status: 200, frames };
  };
  const finals = await readStream('');
  assert.equal(finals.status, 200);
  assert.ok(finals.frames.length === 11 && finals.frames.every((f) => f.name === 'decision' && f.data.eventKind === 'final'
    && ['complete', 'partial', 'unavailable'].includes(f.data.status) && typeof f.data.id === 'string'), 'default stream: final decisions only');
  const withInterims = await readStream('?include=interims');
  const kinds = withInterims.frames.map((f) => `${f.name}:${f.data.eventKind}`);
  assert.equal(withInterims.frames.length, 17);
  assert.deepEqual(kinds.filter((k) => k.startsWith('interim:')).sort(), ['interim:evidence_added', 'interim:first_anomaly', 'interim:first_anomaly',
    'interim:first_anomaly', 'interim:no_final', 'interim:superseded'].sort());
  assert.ok(withInterims.frames.every((f, index) => index === 0 || BigInt(f.eventId) > BigInt(withInterims.frames[index - 1].eventId)), 'one cursor order');
  const interimFrame = withInterims.frames.find((f) => f.data.eventKind === 'evidence_added');
  assert.deepEqual([interimFrame.data.jobId, interimFrame.data.attemptNo, interimFrame.data.seq, interimFrame.data.sourceId,
    interimFrame.data.processObservationId, interimFrame.data.evidenceVersion], [J.interim.id, 1, 2, fixture.sourceId, J.interim.obs, 'detector-evidence-v4']);
  assert.ok(!('payload' in interimFrame.data) && isoTime(new Date(interimFrame.data.createdAt).toISOString()));
  const resumed = await readStream('?include=interims', interimFrame.eventId);
  assert.ok(resumed.frames.every((f) => BigInt(f.eventId) > BigInt(interimFrame.eventId)) && resumed.frames.length === 17 - withInterims.frames.indexOf(interimFrame) - 1,
    'resume after an interim cursor');
  assert.deepEqual(await readStream('?include=all'), { status: 400, body: { code: 'invalid_event_include' } });

  // 18) v04 작업·판정(구성 없음, 016): 목록·분류·검색·상세·기록·Process Flow·처리 상태·읽음·SSE가 v03 항목과 같게 동작한다.
  const beforeV04 = await list('?pageSize=20');
  const { decisions: D4, jobs: J4 } = await seedAnalysisV04Fixture(pool, fixture);
  const afterV04 = await list('?pageSize=20');
  assert.equal(afterV04.total, 20, 'five v04 items join the list');
  assert.deepEqual(Object.fromEntries(Object.entries(afterV04.counts).map(([key, value]) => [key, value - beforeV04.counts[key]]).filter(([, value]) => value)),
    { all: 5, anomaly: 1, anomaly_sign: 1, normal: 1, in_progress: 2, failed_replaced: 2, risk_all: 2, risk_critical: 1, risk_warning: 1 });
  const v04ById = new Map(afterV04.items.map((item) => [item.id, item]));
  const shape = (item) => [item.kind, item.stage, item.signal, item.jobStatus, item.status, item.grade, item.riskStatus, item.riskLevel];
  // v03 fixture의 같은 상태 항목과 분류·단계·등급·위험도가 같고, 구성만 비어 있다.
  for (const [v04Id, v03Id] of [[D4.final.id, D.v4Final.id], [J4.interim.id, J.interim.id], [J4.pending.id, J.pending.id], [J4.noFinal.id, J.noFinal.id]]) {
    assert.deepEqual(shape(v04ById.get(v04Id)), shape(byId.get(v03Id)), `v04 item ${v04Id}`);
    assert.deepEqual([v04ById.get(v04Id).configVersionId, v04ById.get(v04Id).sourceId], [null, fixture.sourceId]);
    assert.equal(v04ById.get(v03Id).configVersionId, fixture.configId, 'v03 items keep their configuration');
  }
  // 정상 판정: v04는 저장 등급 normal이 있고 v3는 등급이 없다. 신호·단계·위험도는 같다.
  assert.deepEqual(shape(v04ById.get(D4.replaced.id)), ['final', 'replaced', 'no', null, 'complete', 'normal', 'not_applicable', null]);
  assert.deepEqual(shape(byId.get(D.replaced.id)), ['final', 'replaced', 'no', null, 'complete', null, 'not_applicable', null]);
  assert.equal(v04ById.get(D4.replaced.id).configVersionId, null);
  // v04 Detector 표본 evidence에서도 같은 원인 요약(상태 규칙 관측 상태는 방법 evidence에서).
  const retryCause = cause({ category: 'soft', methodId: 'soft:retry_count-3f9a2c1d', code: 'SOFT_RETRY_COUNT_3F9A2C1D', fieldName: '재시도 수',
    operator: 'gte', threshold: '3', unit: 'count', observedValue: '4' });
  assert.deepEqual(v04ById.get(D4.final.id).causes, [retryCause]);
  assert.deepEqual(v04ById.get(J4.interim.id).causes, [statusCause({ code: 'PROCESS_STATUS_F' }), retryCause]);
  assert.deepEqual(v04ById.get(D4.replaced.id).causes, []);
  assert.deepEqual(ids(await list('?category=in_progress&pageSize=20')), [J.interim.id, J.pending.id, J4.interim.id, J4.pending.id].sort());
  assert.deepEqual(ids(await list('?category=normal&pageSize=20')), [D.normal.id, D.demo.id, D.replaced.id, D4.replaced.id].sort(),
    'v04 normal decision (no configuration) joins the normal category');
  assert.deepEqual(ids(await list('?category=failed_replaced&pageSize=20')), [D.replaced.id, J.noFinal.id, J.failed.id, D4.replaced.id, J4.noFinal.id].sort());
  assert.deepEqual(ids(await list('?signal=anomaly_sign&completeness=partial&pageSize=20')), [D.v4Final.id, D4.final.id].sort());
  assert.deepEqual(ids(await list(`?q=${J4.final.id}`)), [D4.final.id], 'v04 final decisions are found by their detect job id');
  assert.deepEqual(ids(await list(`?q=${encodeURIComponent(J4.pending.tx)}`)), [J4.pending.id]);
  const v04Final = (await detail(D4.final.id)).body;
  assert.deepEqual([v04Final.configVersionId, v04Final.configuration, v04Final.job.id, v04Final.job.status, v04Final.evidence.composite.grade],
    [null, null, J4.final.id, 'succeeded', 'anomaly_sign'], 'no configuration summary, everything else stays');
  // 기한 초과 ML: 부분 판정, ML 미완료, 점수 없음. SR 경로는 없다.
  assert.deepEqual([v04Final.status, v04Final.ruleStatus, v04Final.mlStatus, v04Final.anomalyScore, v04Final.evidence.schemaVersion, v04Final.evidence.checkers.sr.status],
    ['partial', 'succeeded', 'not_ready', null, 4, 'skipped']);
  assert.ok(!v04Final.evidence.methodResults.some((method) => method.cause === 'pending'), 'a final decision has no pending method');
  const v04Normal = (await detail(D4.replaced.id)).body;
  assert.deepEqual([v04Normal.evidence.schemaVersion, v04Normal.mlStatus, Number(v04Normal.anomalyScore)], [4, 'succeeded', 0.3]);
  assert.equal((await detail(J4.final.id)).body.id, D4.final.id, 'job id resolves to its final decision');
  const v04Pending = (await detail(J4.pending.id)).body;
  assert.deepEqual([v04Pending.kind, v04Pending.configuration, v04Pending.job.status], ['pending', null, 'queued']);
  assert.deepEqual((await interims(D4.replaced.id)).items.map((item) => [item.attemptNo, item.kind, item.supersededByAttemptNo]),
    [[1, 'first_anomaly', null], [1, 'superseded', 2]]);
  assert.deepEqual((await interims(J4.noFinal.id)).items.map((item) => [item.kind, item.reasonCode]),
    [['first_anomaly', null], ['no_final', 'detection_processing_failed']]);
  assert.equal((await flowOf(J4.pending.id)).body.transaction.transactionId, J4.pending.tx, 'job items use the manifest PROCESS');
  assert.equal((await workflow(D4.final.id, { state: 'open', expectedState: 'detected' })).body.workflow.state, 'open');
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM run_decision_workflow_events WHERE job_id=$1', [J4.final.id])).rows[0].n, 1);
  assert.equal((await markRead(J4.interim.id, { occurredAt: v04ById.get(J4.interim.id).occurredAt })).status, 200);
  assert.equal(await unread(J4.interim.id, user), false);
  // 판정 설명: 구성이 빈 판정도 같은 경로로 요청한다. 작업 구성은 판정과 같게 비고, 같은 근거는 재사용한다.
  const v04Explain = await request(base, '/api/jobs', 'POST', user, { kind: 'explain', sourceId: fixture.sourceId, decisionId: D4.final.id });
  assert.equal(v04Explain.status, 201, JSON.stringify(v04Explain.body));
  assert.deepEqual([v04Explain.body.inputSpec.configVersionId, v04Explain.body.inputSpec.savedEvidence.configVersionId, v04Explain.body.inputSpec.savedEvidence.decisionId],
    [null, null, D4.final.id]);
  assert.deepEqual([v04Explain.body.inputSpec.savedEvidence.mlStatus, v04Explain.body.inputSpec.savedEvidence.anomalyScore], ['not_ready', null]);
  assert.ok(v04Explain.body.inputSpec.savedEvidence.rules.length > 0);
  const explainRow = (await pool.query(`SELECT contract_version,queue,input_config_version_id,input_decision_id,llm_spec FROM run_jobs WHERE id=$1`, [v04Explain.body.id])).rows[0];
  assert.deepEqual([explainRow.contract_version, explainRow.queue, explainRow.input_config_version_id, explainRow.input_decision_id],
    ['v03', 'llm_explanation', null, D4.final.id]);
  const v04Reused = await request(base, '/api/jobs', 'POST', user, { kind: 'explain', sourceId: fixture.sourceId, decisionId: D4.final.id });
  assert.deepEqual([v04Reused.status, v04Reused.body.id], [200, v04Explain.body.id], 'same saved evidence reuses the job');
  assert.equal((await pool.query(`SELECT count(*)::int AS n FROM powercode_queue.llm_explanation_jobs WHERE payload->>'jobId'=$1`, [v04Explain.body.id])).rows[0].n, 1);
  const v04Linked = (await detail(D4.final.id)).body.explanation;
  assert.deepEqual([v04Linked.jobId, v04Linked.status], [v04Explain.body.id, 'queued']);
  // LLM 작업자가 쓰는 순서(trainer/runner.py `_finish`)대로 결과를 저장한다: 결과 구성도 NULL로 받아야 한다.
  const explainAttempt = crypto.randomUUID(), explainLease = crypto.randomUUID(), spec = explainRow.llm_spec;
  await pool.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
    VALUES($1,$2,1,'running','fixture-llm',$3,clock_timestamp()+interval '60 seconds',clock_timestamp())`, [explainAttempt, v04Explain.body.id, explainLease]);
  await pool.query(`UPDATE run_jobs SET status='running',current_attempt_id=$2,attempt_count=1 WHERE id=$1`, [v04Explain.body.id, explainAttempt]);
  await pool.query(`INSERT INTO run_llm_results(id,job_id,kind,decision_id,config_version_id,evidence_version,input_evidence_hash,model_version,prompt_version,
      output_schema_version,reuse_key,output,created_attempt_id,lease_token,completed_at)
    VALUES($1,$2,'explain',$3,NULL,$4,$5,$6,$7,$8,$9,'{"facts":[]}',$10,$11,clock_timestamp())`,
  [crypto.randomUUID(), v04Explain.body.id, D4.final.id, spec.evidence_version, spec.input_evidence_hash, spec.model_version, spec.prompt_version,
    spec.output_schema_version, spec.reuse_key, explainAttempt, explainLease]);
  await pool.query(`UPDATE run_job_attempts SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [explainAttempt]);
  await pool.query(`UPDATE run_jobs SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [v04Explain.body.id]);
  assert.equal((await request(base, `/api/jobs/${v04Explain.body.id}`, 'GET', user)).body.status, 'succeeded');
  // 실시간 흐름·기존 판정 API: 구성이 null인 판정을 빠뜨리거나 거절하지 않는다.
  const v04Stream = await readStream('?include=interims', beforeV04.latestEventId);
  assert.deepEqual(v04Stream.frames.map((f) => `${f.name}:${f.data.eventKind}`).sort(), ['decision:final', 'decision:final', 'interim:evidence_added', 'interim:first_anomaly',
    'interim:first_anomaly', 'interim:first_anomaly', 'interim:no_final', 'interim:superseded']);
  const v04Finals = v04Stream.frames.filter((f) => f.name === 'decision');
  assert.deepEqual(v04Finals.map((f) => f.data.id).sort(), [D4.final.id, D4.replaced.id].sort());
  assert.ok(v04Finals.every((f) => f.data.configVersionId === null && f.data.sourceId === fixture.sourceId));
  assert.deepEqual((await readStream('', beforeV04.latestEventId)).frames.map((f) => f.data.id).sort(), [D4.final.id, D4.replaced.id].sort());
  const saved04 = (await request(base, '/api/pipeline/decisions?limit=100', 'GET', user)).body.items.find((item) => item.id === D4.final.id);
  assert.deepEqual([saved04.configVersionId, saved04.sourceId], [null, fixture.sourceId]);

  // 10-1) 부모 연결(FK)이 없는 PROCESS: 인터페이스 ID는 같은 원천·같은 거래 ID의 대표 TRANSACTION 값(실행 그래프 거래 노드와 같음).
  const P = await seedParentlessDecisions(pool, fixture);
  for (const [name, expected] of [['noLink', 'IF-NOLINK-R2'], ['retried', 'IF-RETRY-1'], ['tied', null]]) {
    const item = (await detail(P[name].id)).body;
    assert.deepEqual([item.transactionId, item.interfaceId], [P[name].tx, expected], `parentless ${name} detail`);
    const listed = (await list(`?q=${encodeURIComponent(P[name].tx)}`)).items;
    assert.deepEqual(listed.map((row) => [row.id, row.interfaceId]), [[P[name].id, expected]], `parentless ${name} list`);
    const graphTx = (await request(base, `/api/analysis/decisions/${P[name].id}/graph`, 'GET', user)).body.nodes.find((node) => node.kind === 'transaction');
    assert.equal(graphTx.attrs.interfaceId, expected, `parentless ${name} graph transaction node`);
  }
  assert.deepEqual((await list('?q=if-retry-1')).items.map((row) => row.id), [P.retried.id], 'search matches the representative interface only');
  // 검색어 인터페이스 일치는 묶음 계산(SEARCH_INTERFACE_CTE)이다: 대표가 아닌 행(이전 재시도·이전 리비전)·모호(null)는 일치하지 않는다.
  for (const [q, expected] of [['if-retry-0', []], ['if-retry', [P.retried.id]], ['if-nolink-r1', []], ['if-nolink-r2', [P.noLink.id]], ['if-tied', []]]) {
    assert.deepEqual(ids(await list(`?q=${q}`)), expected, `parentless search ${q}`);
  }

  // 11) 저장소 실패는 503(성공·빈 목록 대체 없음).
  await stopChild();
  const failingBase = await startServer({ ...environment, DATABASE_URL: 'postgresql://nobody:none@127.0.0.1:1/qa_analysis_missing' });
  for (const [route, method = 'GET', token = user, body] of [['/api/analysis/decisions'], [`/api/analysis/decisions/${D.normal.id}`],
    [`/api/analysis/decisions/${D.normal.id}/interims`], [`/api/analysis/decisions/${D.normal.id}/flow`], [`/api/analysis/decisions/${D.normal.id}/graph`],
    [`/api/analysis/decisions/${D.normal.id}/workflow`, 'PUT', user, { state: 'resolved', expectedState: 'open' }],
    [`/api/analysis/decisions/${D.normal.id}/read`, 'PUT', user, { occurredAt: '2026-11-03T00:00:00Z' }], [`/api/analysis/decisions/${D.normal.id}/feedback`, 'GET', admin],
    [`/api/analysis/decisions/${D.normal.id}/feedback`, 'POST', admin, { kind: 'action', reason: 'x' }]]) {
    const failed = await request(failingBase, route, method, token, body);
    assert.deepEqual([failed.status, failed.body.code], [503, 'analysis_storage_unavailable'], `${method} ${route}`);
  }
  console.log('analysis API integration passed: 401/user+admin read, category mapping and counts (result/risk/workflow), sort, pages, filters, '
    + 'search, 400 validation, detail resolution, interim history, own explain job link, stored grade/risk, workflow transitions/conflicts, '
    + 'admin-only feedback, per-user read marks, process flow without BODY, SSE interim events, v04 items and explanations without a configuration, 503');
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
