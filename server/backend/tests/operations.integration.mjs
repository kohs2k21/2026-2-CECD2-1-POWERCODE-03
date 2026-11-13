// 운영 현황 읽기 API(operations-ui.md) 통합 시험: 인증·권한·집계 필드·null 의미.
// 로컬 전용 새 QA 데이터베이스(qa_operations*)만 허용한다. ops_system_state(전역 운영 구성)를 바꾸므로 공용 DB 금지.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { seedDetectJobs, seedOperationsFixture, seedV04Decisions } from './operations-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_operations[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_operations database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-operations-'));
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

try {
  const fixture = await seedOperationsFixture(pool);
  const port = await freePort();
  const environment = { PATH: process.env.PATH, NODE_ENV: 'test', DATABASE_URL: url,
    JWT_SECRET: 'qa-operations-local-only-secret',
    SEED_USER_EMAIL: 'operations-user@example.test', SEED_USER_PASSWORD: 'qa-operations-local-only-password',
    SEED_ADMIN_EMAIL: 'operations-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-operations-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(port),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: 'ignore' });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 서버 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const login = async (email, password) => (await request(base, '/api/auth/login', 'POST', undefined, { email, password })).body.token;
  const admin = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const user = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  assert.ok(admin && user);
  const sid = fixture.sourceId;
  const routes = ['/api/operations/overview', `/api/operations/collection?sourceId=${sid}`, `/api/operations/latency?sourceId=${sid}`,
    `/api/operations/source-fields?sourceId=${sid}&layer=P`, '/api/operations/storage', '/api/operations/decision-trend?days=3'];

  // 1) 인증 없음 401, 잘못된 토큰 401. 일반 사용자·관리자 모두 읽기 200.
  for (const route of routes) {
    assert.equal((await request(base, route)).status, 401, `unauthenticated ${route}`);
    assert.equal((await request(base, route, 'GET', 'not-a-token')).status, 401, `invalid token ${route}`);
    assert.equal((await request(base, route, 'GET', user)).status, 200, `user read ${route}`);
    assert.equal((await request(base, route, 'GET', admin)).status, 200, `admin read ${route}`);
  }
  // 변경은 관리자 전용 /api/inputs 그대로: 일반 사용자 403, 읽기 전용 API에는 쓰기 경로 없음.
  const modeBody = { mode: 'paused', expectedRevision: 7 };
  assert.equal((await request(base, `/api/inputs/${sid}/mode`, 'POST', user, modeBody)).status, 403);
  assert.equal((await request(base, `/api/inputs?sourceId=${sid}`, 'GET', user)).status, 403);
  assert.equal((await request(base, '/api/operations/overview', 'POST', user, {})).status, 404);
  assert.equal((await request(base, '/api/operations/overview', 'POST', admin, {})).status, 404);

  // 2) 입력 검증: 잘못된 원천·계층·기간·알 수 없는 조건은 400, 없는 원천 404.
  assert.equal((await request(base, '/api/operations/collection', 'GET', user)).body.code, 'invalid_source_id');
  assert.equal((await request(base, `/api/operations/collection?sourceId=${sid}&x=1`, 'GET', user)).body.code, 'unknown_operations_filter');
  assert.equal((await request(base, `/api/operations/source-fields?sourceId=${sid}&layer=X`, 'GET', user)).body.code, 'invalid_layer');
  assert.equal((await request(base, '/api/operations/decision-trend?days=0', 'GET', user)).body.code, 'invalid_days');
  assert.equal((await request(base, '/api/operations/decision-trend?days=15', 'GET', user)).status, 400);
  assert.equal((await request(base, `/api/operations/collection?sourceId=${crypto.randomUUID()}`, 'GET', user)).status, 404);
  assert.equal((await request(base, `/api/operations/latency?sourceId=${crypto.randomUUID()}`, 'GET', user)).status, 404);

  // 3) 개요: 계약 필수 필드, 실측 단계 값, 미계측 항목은 키 자체 없음.
  const overview = (await request(base, '/api/operations/overview', 'GET', user)).body;
  assert.ok(isoTime(overview.observedAt));
  assert.equal(overview.maxAgeMs, 60000);
  assert.equal(overview.activeConfigurationVersion, fixture.configId);
  assert.deepEqual(overview.activeConfiguration.models, [{ modelKey: 'iforest', adapterName: 'sklearn-iforest' }]);
  assert.equal(overview.activeConfiguration.ruleCount, 2);
  assert.deepEqual(overview.activeConfiguration.ruleSeverityCounts, { warning: 1, critical: 1 });
  assert.equal(overview.activeConfiguration.detectionMode, 'parallel');
  assert.ok(overview.stages.observationQueue.pendingCount >= 3);
  assert.ok(overview.stages.observationQueue.oldestWaitMs >= 2000);
  assert.equal(overview.stages.detector.pendingCount, 0);
  for (const stage of ['collector', 'detector', 'judgmentStore']) assert.ok(isoTime(overview.stages[stage].lastProcessedAt), stage);
  assert.equal('redaction' in overview.stages, false);
  assert.equal('queueWaitP95Ms' in overview, false, 'no detect job samples → omitted');
  for (const field of ['endToEndLatencyMs', 'cpuPercent', 'memoryPercent', 'diskPercent', 'alertDeliverySuccessPercent']) {
    assert.equal(field in overview, false, `${field} is not measured`);
  }
  assert.ok(overview.collectorEps > 0);

  // 4) 수집: 입력 모드(읽기), 계층별 건수·최근 1분·revision 재도착, 시간별 T, 이력. 오류 원문·설정 원본 없음.
  const collection = (await request(base, `/api/operations/collection?sourceId=${sid}`, 'GET', user)).body;
  assert.equal(collection.source.id, sid);
  assert.equal(collection.input.mode, 'csv');
  assert.equal(collection.input.revision, 7);
  assert.equal(collection.input.status, 'running');
  assert.deepEqual(collection.input.csv, { replayKey: 'synthetic-s1', recordCount: 3000, rate: 10, repeat: 2, position: 1248, total: 6000 });
  assert.equal(collection.input.api, null);
  assert.equal('lastError' in collection.input, false);
  const layer = (name) => collection.layers.find((item) => item.layer === name);
  assert.deepEqual(collection.layers.map((item) => item.layer), ['T', 'P', 'M', 'B']);
  assert.equal(layer('T').storedCount, fixture.transactions);
  assert.equal(layer('P').storedCount, fixture.processes + 1);
  assert.equal(layer('P').lastMinuteCount, 5);
  assert.equal(layer('P').revisionArrivalCount, 1);
  assert.equal(layer('M').storedCount, 5);
  assert.equal(layer('B').storedCount, 1);
  assert.ok(isoTime(layer('P').lastPersistedAt));
  assert.equal(collection.hourly.items.length, 24);
  assert.equal(collection.hourly.items.reduce((sum, item) => sum + item.count, 0), fixture.transactions);
  assert.ok(Date.parse(collection.hourly.items[23].hourStart) <= Date.now());
  assert.deepEqual(collection.history.map((item) => [item.mode, item.status]), [['csv', 'running'], ['csv', 'completed'], ['api', 'paused']]);
  assert.equal(collection.history[0].replayKey, 'synthetic-s1');

  // 5) 단계별 지연: 계측된 단계만 수치, 판정 저장·원천 발생→판정은 null.
  const latency = (await request(base, `/api/operations/latency?sourceId=${sid}`, 'GET', user)).body;
  const stage = (name) => latency.stages.find((item) => item.id === name);
  assert.deepEqual(latency.stages.map((item) => item.id), ['collector', 'persist', 'detectionQueue', 'detector', 'judgmentStore']);
  assert.equal(stage('collector').samples, 3);
  assert.equal(stage('collector').p50Ms, 60);
  assert.equal(stage('collector').recentFailures, 1);
  assert.equal(stage('persist').p50Ms, 5);
  assert.equal(stage('persist').recentFailures, null);
  assert.equal(stage('detectionQueue').queuePending, 3, 'legacy payload without sourceId is scoped by its observation');
  assert.equal(stage('detectionQueue').samples, 0);
  assert.equal(stage('detectionQueue').p95Ms, null);
  assert.equal(stage('detector').samples, fixture.decisions);
  assert.ok(stage('detector').p95Ms > stage('detector').p50Ms);
  assert.equal(stage('detector').recentFailures, 0);
  assert.equal(stage('judgmentStore').p50Ms, null);
  assert.equal(latency.endToEnd.p50Ms, null);
  assert.equal(latency.endToEnd.reason, 'source_clock_unverified');

  // 6) 원본 필드: 이름·제공/결측 건수만, 값 없음.
  const fields = (await request(base, `/api/operations/source-fields?sourceId=${sid}&layer=T`, 'GET', user)).body;
  assert.equal(fields.sampleSize, fixture.transactions);
  const retry = fields.fields.find((item) => item.name === 'retry_count');
  assert.deepEqual(retry, { name: 'retry_count', presentCount: 0, nullCount: fixture.transactions, missingCount: 0 });
  assert.equal(fields.fields.find((item) => item.name === 'response_message').missingCount, fixture.transactions);
  assert.deepEqual(fields.qualityFlags, [{ flag: 'invalid_numeric:retry_count', count: 1 }]);
  assert.equal(JSON.stringify(fields).includes('TX-'), false, 'field values are never returned');

  // 7) 저장공간: DB 크기 실측, 디스크 측정 없으면 null.
  const storage = (await request(base, '/api/operations/storage', 'GET', user)).body;
  assert.ok(storage.databaseBytes > 0);
  assert.equal(storage.disk, null);

  // 8) 판정 추이: 전 원천의 일별 판정 수·비율(운영 리포트 구성 생략 경로 재사용). 판정 없는 날도 나온다.
  const trend = (await request(base, '/api/operations/decision-trend?days=3', 'GET', user)).body;
  assert.equal('configVersionId' in trend, false);
  assert.equal(trend.items.length, 3);
  const today = trend.items.at(-1);
  assert.equal(trend.items.reduce((sum, item) => sum + item.decisionCount, 0), fixture.decisions);
  assert.equal(today.decisionCount > 0, true);
  const total = trend.items.reduce((acc, item) => acc + item.decisionCount, 0);
  assert.equal(total, fixture.decisions);
  const decidable = fixture.decisions - 1;
  if (trend.items.length === 1) {
    assert.equal(today.anomalySignalRatio, 2 / decidable);
    assert.equal(today.unavailableRatio, 1 / fixture.decisions);
  }

  // 운영 보고 요청: 구성을 주면 v03 결과 그대로라는 시험의 기준값을 v04 행을 넣기 전에 받아 둔다.
  const period = { from: new Date(Date.now() - 3600000).toISOString(), until: new Date(Date.now() + 120000).toISOString(), timezone: 'Asia/Seoul' };
  const report = async (query) => {
    const response = await request(base, `/api/pipeline/reports/operational?${new URLSearchParams({ scope: 'replay', ...period, ...query })}`, 'GET', admin);
    assert.equal(response.status, 200, JSON.stringify(response.body));
    return response.body;
  };
  const decisionMetrics = (body) => [body.metricVersion, body.policy.decision, body.decisionCount, body.excluded, body.executionCost.metrics,
    body.items.map(({ day, processCount, alarmProcessCount, alarmRatio, unavailableRate, failedProcessCount, lastProcessedAt, comparisons }) =>
      ({ day, processCount, alarmProcessCount, alarmRatio, unavailableRate, failedProcessCount, lastProcessedAt, comparisons }))];
  const v03Report = await report({ sourceId: sid, configVersionId: fixture.configId });
  assert.deepEqual([v03Report.decisionCount, v03Report.policy.decision], [fixture.decisions, 'latest_same_config_on_selected_revision_before_cutoff']);

  // 8b) detect 작업의 원천은 입력 manifest로 찾는다: v04(구성 없음)도 대기·실패에 들어가고, 다른 원천 작업은 섞이지 않는다.
  const { otherSource } = await seedDetectJobs(pool, fixture);
  const latencyOf = async (source) => {
    const body = (await request(base, `/api/operations/latency?sourceId=${source}`, 'GET', user)).body;
    return { queue: body.stages.find((item) => item.id === 'detectionQueue'), detector: body.stages.find((item) => item.id === 'detector') };
  };
  const mixed = await latencyOf(sid);
  assert.deepEqual([mixed.queue.samples, Math.round(mixed.queue.p50Ms), Math.round(mixed.queue.p95Ms)], [4, 5000, 7700], 'waits of 2, 4 (v03) and 6, 8 (v04) seconds');
  assert.equal(mixed.detector.recentFailures, 2, 'one v03 and one v04 failure; the failure two hours ago is outside the window');
  assert.equal(mixed.detector.samples, fixture.decisions + 1, 'a detector measurement without a configuration still counts');
  const other = await latencyOf(otherSource);
  assert.deepEqual([other.queue.samples, Math.round(other.queue.p50Ms), other.detector.recentFailures], [2, 4000, 2]);
  assert.equal(Math.round((await request(base, '/api/operations/overview', 'GET', user)).body.queueWaitP95Ms), 7500, 'overview covers every source: 2, 3, 4, 5, 6, 8 seconds');
  // v03 회귀: manifest로 찾은 v03 작업 집합이 구성으로 찾던 집합과 같다.
  for (const source of [sid, otherSource]) {
    const viaConfig = await pool.query(`SELECT j.id FROM run_jobs j JOIN ml_config_versions c ON c.id=j.input_config_version_id
      WHERE j.kind='detect' AND c.source_id=$1 ORDER BY j.id`, [source]);
    const viaManifest = await pool.query(`SELECT j.id FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
      WHERE j.kind='detect' AND m.source_id=$1 AND j.contract_version='v03' ORDER BY j.id`, [source]);
    assert.ok(viaConfig.rowCount > 0);
    assert.deepEqual(viaManifest.rows, viaConfig.rows, 'the same v03 jobs as the configuration join');
  }
  // manifest 없는 detect 작업은 DB가 받지 않으므로 원천 판별에서 빠질 작업이 없다.
  await assert.rejects(pool.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,created_at)
    VALUES($1,'detect','queued','legacy','user','legacy','{}',3,0,clock_timestamp(),clock_timestamp())`, [crypto.randomUUID()]), { constraint: 'run_jobs_v03_contract' });

  // 8c) 운영 보고: 구성을 주면 v03 판정 집계가 그대로이고, 생략하면 구성 없는 v04 판정·작업이 들어간다.
  const { ruleOnlySource } = await seedV04Decisions(pool, fixture);
  const sum = (body, key) => body.items.reduce((total, item) => total + (item[key].value ?? 0), 0);
  const sameConfig = await report({ sourceId: sid, configVersionId: fixture.configId });
  assert.deepEqual(decisionMetrics(sameConfig), decisionMetrics(v03Report), 'v04 rows do not change the report of a configuration');
  assert.deepEqual([sameConfig.queue.queued.value, sameConfig.queue.running.value, sameConfig.queue.failedJobsInPeriod.value, sameConfig.queue.running.denominator], [0, 1, 1, 2],
    'only the two v03 jobs of this configuration');
  const bySource = await report({ sourceId: sid });
  assert.deepEqual([bySource.metricVersion, bySource.policy.decision, 'configVersionId' in bySource.filter],
    ['operational-v1', 'latest_any_config_on_selected_revision_before_cutoff', false]);
  assert.equal(bySource.decisionCount, fixture.decisions + 1, 'the v04 decision on a judged observation replaces it, the new observation adds one');
  assert.equal(sum(bySource, 'alarmProcessCount'), 3, 'two v03 alarms and the v04 alarm that is now the latest decision');
  assert.deepEqual([bySource.queue.queued.value, bySource.queue.running.value, bySource.queue.failedJobsInPeriod.value, bySource.queue.running.denominator], [0, 2, 2, 7],
    'v03 and v04 jobs of this source');
  assert.equal(sameConfig.queue.atCutoff.running.value, 1, 'cutoff config filter');
  assert.equal(bySource.queue.atCutoff.running.value, 2);
  // 교차표: checkers가 있는 v04 판정 2건만 비교 가능하다(상태 규칙 이상·ML 정상 1건, 둘 다 정상 1건). v04에 SR 경로는 없다.
  const crossTab = (body, path) => body.items.reduce((total, item) => {
    const pair = item.comparisons[path];
    return { comparable: total.comparable + pair.comparable, both: total.both + (pair.both.value ?? 0), ruleOnly: total.ruleOnly + (pair.ruleOnly.value ?? 0),
      mlOnly: total.mlOnly + (pair.mlOnly.value ?? 0), neither: total.neither + (pair.neither.value ?? 0) };
  }, { comparable: 0, both: 0, ruleOnly: 0, mlOnly: 0, neither: 0 });
  assert.deepEqual(crossTab(bySource, 'hrMl'), { comparable: 2, both: 0, ruleOnly: 1, mlOnly: 0, neither: 1 });
  assert.deepEqual(crossTab(bySource, 'srMl'), { comparable: 0, both: 0, ruleOnly: 0, mlOnly: 0, neither: 0 });
  assert.ok(bySource.items.filter((item) => item.processCount.value).every((item) => item.comparisons.srMl.both.reason === 'checker_evidence_unavailable'));
  assert.equal(crossTab(sameConfig, 'hrMl').comparable, 0, 'v03 fixture decisions carry no checkers');
  // 실행 비용: 측정값이 있는 v04 판정 2건, 대상은 판정 모집단 전체.
  const { totalMs, inferenceMs } = bySource.executionCost.metrics;
  assert.deepEqual([totalMs.samples, totalMs.targets, totalMs.sum.value, totalMs.p50.value, totalMs.p50.coverage], [2, fixture.decisions + 1, 41, 20.5, 2 / (fixture.decisions + 1)]);
  assert.deepEqual([inferenceMs.samples, inferenceMs.sum.value], [2, 9]);
  assert.deepEqual([sameConfig.executionCost.metrics.totalMs.samples, sameConfig.executionCost.metrics.totalMs.p50.reason], [0, 'telemetry_unavailable']);
  // 구성이 하나도 없는 원천(룰만 운영): 생략하면 200, 구성을 주면 기존처럼 404, 없는 원천은 404.
  const ruleOnly = await report({ sourceId: ruleOnlySource, scope: 'live' });
  assert.deepEqual([ruleOnly.decisionCount, sum(ruleOnly, 'alarmProcessCount')], [1, 1]);
  assert.equal(crossTab(ruleOnly, 'hrMl').comparable, 0, 'no ML checker to compare with');
  assert.deepEqual([ruleOnly.executionCost.metrics.totalMs.samples, ruleOnly.executionCost.metrics.inferenceMs.samples, ruleOnly.executionCost.metrics.inferenceMs.p50.reason],
    [1, 0, 'telemetry_unavailable'], 'no model, no inference time');
  const reportRoute = (query) => `/api/pipeline/reports/operational?${new URLSearchParams({ scope: 'live', ...period, ...query })}`;
  assert.equal((await request(base, reportRoute({ sourceId: ruleOnlySource, configVersionId: fixture.configId }), 'GET', admin)).status, 404);
  const noSource = await request(base, reportRoute({ sourceId: crypto.randomUUID() }), 'GET', admin);
  assert.deepEqual([noSource.status, noSource.body.code], [404, 'source_configuration_not_found']);
  assert.equal((await request(base, reportRoute({ sourceId: sid }), 'GET', user)).status, 403);
  // 판정 추이: 전 원천 합산, 운영 구성이 없어도 같다.
  const allSources = (await request(base, '/api/operations/decision-trend?days=3', 'GET', user)).body;
  assert.equal(allSources.items.reduce((total, item) => total + item.decisionCount, 0), fixture.decisions + 2, 'both sources, live and replay');
  await pool.query('UPDATE ops_system_state SET active_config_version_id=NULL WHERE id=1');
  const unconfigured = (await request(base, '/api/operations/overview', 'GET', user)).body;
  assert.equal(unconfigured.activeConfiguration, null);
  // 구성 없이(v04) 운영되는 룰 세트·활성 모델 집합 버전은 구성과 무관하게 함께 온다(시드: 룰 세트 버전 1(016)·활성 집합 버전 0(015, 모델 없음)).
  assert.deepEqual(unconfigured.operationalState, { ruleSetRevision: 1, activeModelSetRevision: 0, models: [] });
  await pool.query(`INSERT INTO ops_rule_set_revisions(revision,content_hash,content_hash_version,snapshot,changes,summary,saved_by_actor_id)
    SELECT 2,content_hash,content_hash_version,snapshot,'[]','{}','operations-fixture' FROM ops_rule_set_revisions WHERE revision=1`);
  await pool.query(`INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,changed_by_actor_id)
    VALUES(1,'{"models":[{"configVersionId":"${fixture.configId}","modelKey":"iforest","activatedAtRevision":1}]}','[]','operations-fixture')`);
  assert.deepEqual((await request(base, '/api/operations/overview', 'GET', user)).body.operationalState,
    { ruleSetRevision: 2, activeModelSetRevision: 1, models: [{ modelKey: 'iforest' }] }, 'latest revisions');
  const noActive = (await request(base, '/api/operations/decision-trend?days=3', 'GET', user)).body;
  assert.deepEqual(noActive.items, allSources.items, 'the trend does not depend on the active configuration');
  assert.equal('reason' in noActive, false);

  // 9) 저장소 실패는 503(성공·빈 값 대체 없음).
  await stopChild();
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary,
    env: { ...environment, DATABASE_URL: 'postgresql://nobody:none@127.0.0.1:1/qa_operations_missing' }, stdio: 'ignore' });
  ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 서버 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const failed = await request(base, '/api/operations/overview', 'GET', user);
  assert.equal(failed.status, 503);
  assert.equal(failed.body.code, 'operations_storage_unavailable');
  console.log('operations read API integration passed: 401/user read/admin-only mutation, overview contract, collection, latency, fields, storage, trend across sources, v03/v04 detect jobs by input source, report with and without a configuration, 503');
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
