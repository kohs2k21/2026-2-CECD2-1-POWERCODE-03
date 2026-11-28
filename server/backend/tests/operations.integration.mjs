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
import { seedCollectorMetrics, seedDetectJobs, seedOperationsFixture, seedV04Decisions } from './operations-fixture.mjs';

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
  // 추가 필드 methodName·frame: 카탈로그 표시 이름·틀(operations-ui.md)
  assert.deepEqual(overview.activeConfiguration.models, [{ modelKey: 'iforest', adapterName: 'sklearn-iforest', methodName: 'Isolation Forest', frame: 'row' }]);
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
  // actualRate: 실행 running → 최근 60초 저장 관측(T 3 + P 5, M·B는 1분 이상 전)/60.
  assert.deepEqual(collection.input.csv, { replayKey: 'synthetic-s1', recordCount: 3000, rate: 10, repeat: 2, position: 1248, total: 6000,
    actualRate: 8 / 60 });
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
  for (const name of ['T', 'P', 'M', 'B']) assert.deepEqual([layer(name).sourceTimeKnownCount, layer(name).lastSourceEventAt], [0, null], `030 이전 모양 ${name}`);
  assert.equal(collection.hourly.basis, 'persisted_at');
  assert.equal('timeUnknownCount' in collection.hourly, false);
  assert.equal(collection.hourly.items.length, 24);
  assert.equal(collection.hourly.items.reduce((sum, item) => sum + item.count, 0), fixture.transactions);
  assert.ok(Date.parse(collection.hourly.items[23].hourStart) <= Date.now());
  assert.deepEqual(collection.history.map((item) => [item.mode, item.status]), [['csv', 'running'], ['csv', 'completed'], ['api', 'paused']]);
  assert.equal(collection.history[0].replayKey, 'synthetic-s1');
  // hourlyBasis=source_event: 원천 발생 시각 칸(모두 0 = 원천 시각 없음), 원천 시각 없는 T는 timeUnknownCount. 그 밖의 값은 400.
  const bySourceEvent = (await request(base, `/api/operations/collection?sourceId=${sid}&hourlyBasis=source_event`, 'GET', user)).body;
  assert.deepEqual([bySourceEvent.hourly.layer, bySourceEvent.hourly.basis, bySourceEvent.hourly.items.length], ['T', 'source_log_occurred_at', 24]);
  assert.equal(bySourceEvent.hourly.items.reduce((sum, item) => sum + item.count, 0), 0);
  assert.equal(bySourceEvent.hourly.timeUnknownCount, fixture.transactions);
  assert.equal((await request(base, `/api/operations/collection?sourceId=${sid}&hourlyBasis=persisted_at`, 'GET', user)).body.hourly.basis, 'persisted_at');
  for (const value of ['source', 'SOURCE_EVENT', '']) {
    const bad = await request(base, `/api/operations/collection?sourceId=${sid}&hourlyBasis=${value}`, 'GET', user);
    assert.deepEqual([bad.status, bad.body.code], [400, 'unknown_operations_filter'], `hourlyBasis=${value}`);
  }

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
  // 판정 저장 표본(031) 없음 → samples 0·null. measured 판정 없음 → 원천 발생 → 판정 null.
  assert.deepEqual([stage('judgmentStore').samples, stage('judgmentStore').p50Ms, stage('judgmentStore').recentFailures], [0, null, null]);
  assert.deepEqual(latency.endToEnd, { window: '1h', samples: 0, p50Ms: null, p95Ms: null, skewCount: 0, reason: 'source_time_unavailable' });
  // CSV 재생 원천: API 입력 후보가 없어 원천 → 저장은 no_live_api_samples, 시간대 확인 표본 0.
  assert.deepEqual(latency.sourceToStored, { window: '1h', samples: 0, p50Ms: null, p95Ms: null, skewCount: 0,
    excludedCounts: { unparsed: 0, absent: 0, legacyUnknown: 0 }, reason: 'no_live_api_samples' });
  assert.deepEqual(latency.clockCheck, { window: '1h', assumedTimezone: 'Asia/Seoul', sourceClockBasisStatus: 'unknown',
    sampleCount: 0, p50Ms: null, p95Ms: null, negativeRatio: null });

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
  assert.deepEqual(storage.disks, ['database_data', 'artifacts'].map((storageKey) => ({ storageKey, status: 'unavailable', filesystemKey: null,
    capacityBytes: null, usedBytes: null, availableBytes: null, warningState: 'unknown', reasonCode: 'not_measured', measuredAt: null })));

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
    { ruleSetRevision: 2, activeModelSetRevision: 1, models: [{ modelKey: 'iforest', methodName: 'Isolation Forest', frame: 'row' }] }, 'latest revisions');
  const noActive = (await request(base, '/api/operations/decision-trend?days=3', 'GET', user)).body;
  assert.deepEqual(noActive.items, allSources.items, 'the trend does not depend on the active configuration');
  assert.equal('reason' in noActive, false);

  // 8d) Collector·Detector 운영 계측(collector-metrics.md 2~4절, detection-decision.md 18·19절). 새 원천 2개라 위 집계와 섞이지 않는다.
  const { metricsSource, skewSource } = await seedCollectorMetrics(pool);
  const metricsCollection = (await request(base, `/api/operations/collection?sourceId=${metricsSource}`, 'GET', user)).body;
  assert.equal(metricsCollection.input.mode, 'api');
  assert.equal(metricsCollection.input.csv, null);
  const api = metricsCollection.input.api;
  assert.deepEqual(Object.keys(api).sort(), ['lastAttemptAt', 'lastSuccessAt', 'pollSeconds', 'sinceLastSuccessMs'], 'no error text or code');
  assert.ok(isoTime(api.lastSuccessAt) && isoTime(api.lastAttemptAt) && Date.parse(api.lastAttemptAt) > Date.parse(api.lastSuccessAt));
  assert.ok(api.sinceLastSuccessMs >= 3000 && api.sinceLastSuccessMs < 60000, `since last success ${api.sinceLastSuccessMs}`);
  const metricsLayer = (name) => metricsCollection.layers.find((item) => item.layer === name);
  assert.deepEqual([metricsLayer('P').storedCount, metricsLayer('P').sourceTimeKnownCount], [10, 7], 'P: unparsed·absent·legacy have no source time');
  assert.deepEqual([metricsLayer('T').storedCount, metricsLayer('T').sourceTimeKnownCount], [3, 2]);
  assert.ok(Date.parse(metricsLayer('T').lastSourceEventAt) < Date.now() - 29 * 60000, 'latest source event, not persisted time');
  const metricsHourly = (await request(base, `/api/operations/collection?sourceId=${metricsSource}&hourlyBasis=source_event`, 'GET', user)).body.hourly;
  assert.equal(metricsHourly.items.reduce((sum, item) => sum + item.count, 0), 1, 'source event 30 minutes ago; the one 3 days ago is outside 24 hours');
  assert.equal(metricsHourly.timeUnknownCount, 1);

  const metricsLatency = (await request(base, `/api/operations/latency?sourceId=${metricsSource}`, 'GET', user)).body;
  const metricsStage = (name) => metricsLatency.stages.find((item) => item.id === name);
  // 판정 저장 소요 2·4·10 ms(2시간 전 500 ms 제외): p95 = 4 + 0.9 × 6.
  assert.deepEqual([metricsStage('judgmentStore').samples, metricsStage('judgmentStore').p50Ms, metricsStage('judgmentStore').recentFailures], [3, 4, null]);
  assert.ok(Math.abs(metricsStage('judgmentStore').p95Ms - 9.4) < 1e-9);
  // 원천 발생 → 판정: measured 1000·3000·5000, 음수 -500은 skewCount, unavailable·2시간 전은 빠진다.
  const { p95Ms: endToEndP95, ...endToEnd } = metricsLatency.endToEnd;
  assert.deepEqual(endToEnd, { window: '1h', samples: 3, p50Ms: 3000, skewCount: 1, reason: null });
  assert.ok(Math.abs(endToEndP95 - 4800) < 1e-6, `p95 = 3000 + 0.9 × 2000, got ${endToEndP95}`);
  // 원천 → 저장: live·첫 revision·API·1시간·시각 있음 2000·4000·6000(assumed 포함), -1000은 skewCount. initial·revision 2·2시간 전은 아예 빠진다.
  const toStored = metricsLatency.sourceToStored;
  assert.deepEqual([toStored.samples, toStored.skewCount, toStored.excludedCounts, toStored.reason],
    [3, 1, { unparsed: 1, absent: 1, legacyUnknown: 1 }, null]);
  assert.ok(Math.abs(toStored.p50Ms - 4000) < 1 && Math.abs(toStored.p95Ms - 5800) < 1, JSON.stringify(toStored));
  // 시간대 확인: assumed_tz 6000·-1000(음수 포함 그대로), 원천 설정 상태는 verified.
  const check = metricsLatency.clockCheck;
  assert.deepEqual([check.assumedTimezone, check.sourceClockBasisStatus, check.sampleCount, check.negativeRatio], ['Asia/Seoul', 'verified', 2, 0.5]);
  assert.ok(Math.abs(check.p50Ms - 2500) < 1 && Math.abs(check.p95Ms - 5650) < 1, JSON.stringify(check));
  // 음수만 있는 원천: 분위수 null + source_clock_skew(0으로 자르지 않음).
  const skewLatency = (await request(base, `/api/operations/latency?sourceId=${skewSource}`, 'GET', user)).body;
  assert.deepEqual(skewLatency.endToEnd, { window: '1h', samples: 0, p50Ms: null, p95Ms: null, skewCount: 1, reason: 'source_clock_skew' });
  assert.deepEqual([skewLatency.sourceToStored.samples, skewLatency.sourceToStored.p50Ms, skewLatency.sourceToStored.skewCount,
    skewLatency.sourceToStored.reason], [0, null, 1, 'source_clock_skew']);
  assert.deepEqual([skewLatency.clockCheck.sampleCount, skewLatency.clockCheck.p50Ms, skewLatency.clockCheck.negativeRatio,
    skewLatency.clockCheck.sourceClockBasisStatus], [1, -2000, 1, 'unknown'], 'clock check keeps the sign');
  // 원천 시각 없는 live 후보만 있으면 source_time_unavailable(새 원천에 해석 실패 live 관측 하나).
  const blindSource = crypto.randomUUID();
  await pool.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,$2,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["P"]}',clock_timestamp())`, [blindSource, `operations-blind-${blindSource}`]);
  const blindRun = crypto.randomUUID(), blindExecution = crypto.randomUUID(), blindObservation = crypto.randomUUID();
  await pool.query(`INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status,next_position,accepted_count,created_at)
    VALUES($1,$2,'api',NULL,'{"pollSeconds":2}','running',1,1,clock_timestamp())`, [blindRun, blindSource]);
  await pool.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
    VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [blindExecution, blindSource, `exec-${blindExecution}`]);
  await pool.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,api_received_at,persisted_at,
      clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
    VALUES($1,$2,'P',$3,1,$4,$5,clock_timestamp(),clock_timestamp(),'unparsed','{"rule":"source-time-v1"}','{}','[]','[]')`,
  [blindObservation, blindSource, blindExecution, `P:${blindObservation}`, blindObservation]);
  await pool.query(`INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace,ingest_phase) VALUES($1,$2,'api',NULL,'fixture','live')`,
    [blindObservation, blindRun]);
  const blindLatency = (await request(base, `/api/operations/latency?sourceId=${blindSource}`, 'GET', user)).body;
  assert.deepEqual([blindLatency.sourceToStored.samples, blindLatency.sourceToStored.excludedCounts.unparsed, blindLatency.sourceToStored.reason],
    [0, 1, 'source_time_unavailable']);

  // 개요 원천 발생 → 판정: 전 원천 measured 중 0 이상만의 중앙값(1000·3000·5000 → 3000, 음수 -500·-300 제외).
  const measuredOverview = (await request(base, '/api/operations/overview', 'GET', user)).body;
  assert.equal(measuredOverview.endToEndLatencyMs, 3000);
  for (const field of ['cpuPercent', 'memoryPercent', 'diskPercent']) assert.equal(field in measuredOverview, false, `${field}: no resource sample yet`);
  // /api/pipeline/metrics 단계 목록은 그대로(judgment_store는 운영 latency API에서만).
  const pipelineMetrics = await request(base, '/api/pipeline/metrics', 'GET', admin);
  assert.equal(pipelineMetrics.status, 200);
  assert.ok(pipelineMetrics.body.stages.every((item) => ['feature', 'detector'].includes(item.stage)), JSON.stringify(pipelineMetrics.body.stages));
  assert.ok(pipelineMetrics.body.stages.some((item) => item.stage === 'detector'));

  // 8e) 자원·저장공간 표본(collector-metrics.md 4.3): 신선한(180초) 최신 행만, 측정 불가·오래됨은 값 없음, database_data만 disk·diskPercent.
  const resource = (secondsAgo, cpu, memoryUsed) => pool.query(`INSERT INTO ops_resource_measurements(id,scope,cpu_status,cpu_percent,cpu_reason_code,
      sample_interval_ms,memory_status,memory_used_bytes,memory_total_bytes,memory_reason_code,measured_at)
    VALUES($1,'host_proc_unverified',$2::measurement_status,$3::double precision,$4,$5::double precision,$6::measurement_status,$7::bigint,$8::bigint,$9,
      clock_timestamp()-make_interval(secs=>$10::double precision))`,
  [crypto.randomUUID(), cpu === null ? 'unavailable' : 'measured', cpu, cpu === null ? 'cpu_first_sample' : null, cpu === null ? null : 60000,
    memoryUsed === null ? 'unavailable' : 'measured', memoryUsed, memoryUsed === null ? null : 4096, memoryUsed === null ? 'meminfo_unavailable' : null, secondsAgo]);
  const disk = (key, secondsAgo, used, available, reason = null) => pool.query(`INSERT INTO ops_storage_measurements(id,storage_key,filesystem_key,target_kind,
      status,capacity_bytes,used_bytes,available_bytes,database_bytes,writer_access_verified,warning_spec_version,warning_state,reason_code,measured_at)
    VALUES($1,$2,$3,'filesystem',$4::measurement_status,$5::bigint,$6::bigint,$7::bigint,NULL,false,'disk-warning-v1',$8,$9,
      clock_timestamp()-make_interval(secs=>$10::double precision))`,
  [crypto.randomUUID(), key, reason ? null : `dev:${key.length}`, reason ? 'unavailable' : 'measured', reason ? null : 1000, used, available,
    reason ? 'unknown' : 'warning', reason, secondsAgo]);
  await resource(600, 99, 4000);
  await resource(30, 37.5, 1024);
  await disk('database_data', 90, 100, 900);
  await disk('database_data', 30, 600, 200);
  await disk('artifacts', 600, 10, 990);
  const withResources = (await request(base, '/api/operations/overview', 'GET', user)).body;
  assert.deepEqual([withResources.cpuPercent, withResources.memoryPercent, withResources.diskPercent], [37.5, 25, 75],
    'latest fresh rows; disk uses used/(used+available)');
  const measuredStorage = (await request(base, '/api/operations/storage', 'GET', user)).body;
  assert.deepEqual(measuredStorage.disk, { storageKey: 'database_data', capacityBytes: 1000, usedBytes: 600, availableBytes: 200,
    warningState: 'warning', measuredAt: measuredStorage.disks[0].measuredAt });
  assert.deepEqual(measuredStorage.disks.map((item) => [item.storageKey, item.status, item.reasonCode, item.usedBytes]),
    [['database_data', 'measured', null, 600], ['artifacts', 'unavailable', 'measurement_stale', null]]);
  assert.ok(isoTime(measuredStorage.disks[1].measuredAt), 'stale row keeps its own time');
  assert.equal(measuredStorage.disks[0].filesystemKey, 'dev:13');
  // 최신 행이 측정 불가면 그 사유 그대로, 이전 measured 행으로 거슬러 가지 않는다. CPU 첫 표본은 CPU만 측정 불가.
  await resource(5, null, 2048);
  await disk('database_data', 5, null, null, 'mount_not_visible');
  const unmeasured = (await request(base, '/api/operations/overview', 'GET', user)).body;
  assert.deepEqual(['cpuPercent' in unmeasured, unmeasured.memoryPercent, 'diskPercent' in unmeasured], [false, 50, false]);
  const unmeasuredStorage = (await request(base, '/api/operations/storage', 'GET', user)).body;
  assert.equal(unmeasuredStorage.disk, null);
  assert.deepEqual([unmeasuredStorage.disks[0].status, unmeasuredStorage.disks[0].reasonCode, unmeasuredStorage.disks[0].usedBytes],
    ['unavailable', 'mount_not_visible', null]);
  // 미래 시각(DB 시계 + 5초 초과) 행은 신선하지 않다.
  await pool.query(`UPDATE ops_resource_measurements SET measured_at=clock_timestamp()+interval '1 minute' WHERE measured_at>=clock_timestamp()-interval '10 seconds'`);
  assert.equal((await request(base, '/api/operations/overview', 'GET', user)).body.cpuPercent, 37.5, 'future row skipped, previous fresh row used');

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
  console.log('operations read API integration passed: 401/user read/admin-only mutation, overview contract, collection, latency, fields, storage, trend across sources, v03/v04 detect jobs by input source, report with and without a configuration, source-time latency (judgment store, end-to-end, source-to-stored, clock check, skew), resources and disks, 503');
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
