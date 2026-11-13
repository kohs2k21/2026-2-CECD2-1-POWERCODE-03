// 머신러닝 모델 API를 실제 서버·실제 PostgreSQL로 확인한다. 서버는 런타임 역할 powercode_api로 연결해 권한도 함께 본다.
// 대상: GET /api/configurations(목록·커서·필터·상세), GET /api/pipeline/reports/volume, POST /api/jobs kind=train(modelKey·parameters·미지원 값 거절), capabilities.
// 로컬·CI의 qa_rule_set* DB만 허용. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { activateConfig, addProcess, seedRuleSetSource } from './rule-set-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_rule_set[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_rule_set database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-models-'));
const pool = new pg.Pool({ connectionString: url });
const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}

try {
  await pool.query(await readFile(path.join(backend, '../infra/scripts/database-roles.sql'), 'utf8'));
  const runtimeUrl = new URL(url);
  runtimeUrl.searchParams.set('options', '-c role=powercode_api');
  const port = await freePort();
  const env = {
    PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: runtimeUrl.toString(), JWT_SECRET: 'qa-models-local-only-secret',
    SEED_USER_EMAIL: 'models-user@example.test', SEED_USER_PASSWORD: 'qa-models-local-only-password',
    SEED_ADMIN_EMAIL: 'models-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-models-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(port),
  };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env, stdio: 'ignore' });
  const base = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch(`${base}/health`)).ok) break; } catch { /* 서버 시작 대기 */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const call = async (route, { method = 'GET', token, body, key } = {}) => {
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body) headers['Content-Type'] = 'application/json';
    if (key) headers['Idempotency-Key'] = key;
    const response = await fetch(`${base}${route}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
    return { status: response.status, body: await response.json() };
  };
  const login = async (email, password) => (await call('/api/auth/login', { method: 'POST', body: { email, password } })).body.token;
  const admin = await login(env.SEED_ADMIN_EMAIL, env.SEED_ADMIN_PASSWORD);
  const user = await login(env.SEED_USER_EMAIL, env.SEED_USER_PASSWORD);
  assert.ok(admin && user);
  const get = (route, token = admin) => call(route, { token });

  // capabilities: 카탈로그 원본 그대로, 소프트룰 등록 필드도 같은 원본
  const catalog = JSON.parse(await readFile(path.join(backend, '../contracts/method-catalog.json'), 'utf8'));
  const capabilities = (await get('/api/jobs/capabilities')).body;
  assert.deepEqual(capabilities.methodCatalog, catalog);
  assert.deepEqual(capabilities.models.map((model) => [model.key, model.supported]), [['iforest', true], ['minibatch_kmeans', false], ['eif', false]]);
  assert.deepEqual(capabilities.training, { excludedIntervals: false });

  // 거래량: ESB 시각 기준, 실행별 최신 revision, 시각 없음 분리, 상태별 건수, 빈 버킷 0
  const sourceId = await seedRuleSetSource(pool, 'models');
  const at = (text) => `${text}+09:00`;
  await addProcess(pool, sourceId, { occurredAt: at('2026-10-01T09:10:00'), status: 'S' });
  await addProcess(pool, sourceId, { occurredAt: at('2026-10-01T09:20:00'), status: 'F' });
  await addProcess(pool, sourceId, { occurredAt: at('2026-10-01T11:00:00'), status: 'Z' });
  await addProcess(pool, sourceId, { occurredAt: at('2026-10-02T00:00:00'), status: 'S' }); // 끝 시각은 포함하지 않는다
  await addProcess(pool, sourceId, { occurredAt: null, status: 'S', persistedAt: at('2026-10-01T12:00:00') });
  const execution = id();
  await addProcess(pool, sourceId, { occurredAt: at('2026-10-01T10:00:00'), status: 'P', execution, revision: 1 });
  await addProcess(pool, sourceId, { occurredAt: at('2026-10-01T10:05:00'), status: 'S', execution, revision: 2 });
  const query = (extra = {}) => `/api/pipeline/reports/volume?${new URLSearchParams({ sourceId, from: '2026-09-30T15:00:00Z', until: '2026-10-01T15:00:00Z', bucket: 'hour', timeBasis: 'esb_occurred_at', ...extra })}`;
  const hourly = await get(query());
  assert.equal(hourly.status, 200, JSON.stringify(hourly.body));
  assert.equal(hourly.body.buckets.length, 24);
  assert.equal(hourly.body.totalCount, 4);
  assert.equal(hourly.body.timeUnknownCount, 1);
  assert.equal(hourly.body.timezone, 'Asia/Seoul');
  const bucket = (time) => hourly.body.buckets.find((item) => item.bucketStart === time);
  assert.deepEqual(bucket('2026-10-01T00:00:00Z'), { bucketStart: '2026-10-01T00:00:00Z', processCount: 2, statusCounts: { S: 1, F: 1, P: 0, N: 0, other: 0 } });
  assert.deepEqual(bucket('2026-10-01T01:00:00Z').statusCounts, { S: 1, F: 0, P: 0, N: 0, other: 0 }, 'an execution counts once, latest revision');
  assert.equal(bucket('2026-10-01T02:00:00Z').statusCounts.other, 1);
  assert.equal(bucket('2026-09-30T15:00:00Z').processCount, 0, 'empty buckets stay in the series');
  for (const [unit, count] of [['day', 1], ['minute', 1440], ['month', 1]]) {
    const report = await get(query({ bucket: unit }));
    assert.equal(report.status, 200);
    assert.equal(report.body.buckets.length, count, unit);
    assert.equal(report.body.totalCount, 4, unit);
  }
  assert.equal((await get(query({ bucket: 'month' }))).body.buckets[0].bucketStart, '2026-09-30T15:00:00Z', 'month starts on the local first day');
  const partial = await get(query({ from: '2026-10-01T00:15:00Z', until: '2026-10-01T00:45:00Z', bucket: 'hour' }));
  assert.equal(partial.body.totalCount, 1, 'counts stay inside the requested range');
  assert.equal(partial.body.buckets[0].bucketStart, '2026-10-01T00:00:00Z');
  const persisted = await get(query({ timeBasis: 'persisted_at', from: '2026-10-01T03:00:00Z', until: '2026-10-01T04:00:00Z' }));
  assert.equal(persisted.body.totalCount, 1, 'persisted basis counts the row with no ESB time');
  assert.equal(persisted.body.timeUnknownCount, 0);
  const tooLarge = await get(query({ bucket: 'minute', from: '2026-09-01T00:00:00Z' }));
  assert.equal(tooLarge.status, 400);
  assert.deepEqual(tooLarge.body, { code: 'volume_range_too_large', bucket: 'minute', maxDays: 7 });
  assert.equal((await get(query({ sourceId: id() }))).status, 404);
  assert.equal((await get(query({ bucket: 'week' }))).status, 400);

  // 일광절약 전환: 버킷 수·건수가 실제 시각과 맞아야 한다(America/New_York 2026-03-08 23시간, 2026-11-01 25시간)
  const dstSource = await seedRuleSetSource(pool, 'models-dst');
  const dst = (extra) => `/api/pipeline/reports/volume?${new URLSearchParams({ sourceId: dstSource, bucket: 'hour', timeBasis: 'esb_occurred_at', timezone: 'America/New_York', ...extra })}`;
  await addProcess(pool, dstSource, { occurredAt: '2026-11-01T05:30:00Z', status: 'S' }); // 01:30 EDT
  await addProcess(pool, dstSource, { occurredAt: '2026-11-01T06:30:00Z', status: 'F' }); // 01:30 EST
  await addProcess(pool, dstSource, { occurredAt: '2026-03-08T06:59:00Z', status: 'S' }); // 01:59 EST
  await addProcess(pool, dstSource, { occurredAt: '2026-03-08T07:30:00Z', status: 'S' }); // 03:30 EDT
  const fall = await get(dst({ from: '2026-11-01T04:00:00Z', until: '2026-11-02T05:00:00Z' }));
  assert.equal(fall.status, 200, JSON.stringify(fall.body));
  assert.equal(fall.body.buckets.length, 25, 'the fall-back day has 25 hours');
  assert.equal(fall.body.totalCount, 2);
  assert.equal(new Set(fall.body.buckets.map((item) => item.bucketStart)).size, 25);
  assert.equal(fall.body.buckets.find((item) => item.bucketStart === '2026-11-01T05:00:00Z').statusCounts.S, 1);
  assert.equal(fall.body.buckets.find((item) => item.bucketStart === '2026-11-01T06:00:00Z').statusCounts.F, 1, 'repeated local hours stay separate');
  const spring = await get(dst({ from: '2026-03-08T05:00:00Z', until: '2026-03-09T04:00:00Z' }));
  assert.equal(spring.body.buckets.length, 23, 'the spring-forward day has 23 hours');
  assert.equal(spring.body.totalCount, 2);
  assert.equal(spring.body.buckets.reduce((sum, item) => sum + item.processCount, 0), 2);
  for (const [bucket, from, until, count] of [['day', '2026-11-01T04:00:00Z', '2026-11-02T05:00:00Z', 1], ['day', '2026-03-08T05:00:00Z', '2026-03-09T04:00:00Z', 1],
    ['month', '2026-03-01T05:00:00Z', '2026-11-30T05:00:00Z', 9], ['minute', '2026-11-01T04:00:00Z', '2026-11-02T05:00:00Z', 1500]]) {
    const report = await get(dst({ bucket, from, until }));
    assert.equal(report.status, 200, bucket);
    assert.equal(report.body.buckets.length, count, `${bucket} ${from}`);
    assert.equal(report.body.buckets.reduce((sum, item) => sum + item.processCount, 0), report.body.totalCount);
  }
  assert.equal((await get(dst({ bucket: 'month', from: '2026-03-01T05:00:00Z', until: '2026-11-30T05:00:00Z' }))).body.totalCount, 4);
  const halfHour = await get(dst({ timezone: 'Asia/Kolkata', from: '2026-11-01T05:30:00Z', until: '2026-11-01T07:30:00Z' }));
  assert.deepEqual(halfHour.body.buckets.map((item) => item.bucketStart), ['2026-11-01T05:30:00Z', '2026-11-01T06:30:00Z'], 'half-hour zones keep their local hour');
  assert.equal(halfHour.body.totalCount, 2);
  const ragged = await get(query({ from: '2026-10-01T00:00:30Z', until: '2026-10-01T01:00:10Z', bucket: 'hour' }));
  assert.deepEqual(ragged.body.buckets.map((item) => item.bucketStart), ['2026-10-01T00:00:00Z', '2026-10-01T01:00:00Z'], 'a short last bucket is not skipped');
  assert.equal((await get(query(), user)).status, 403);

  // 모델 버전 목록: 활성 집합·v1 단일 활성·룰 전용 행, 최신순 커서, 필터
  const version = async (spec, modelKey, { job = true, createdAt } = {}) => {
    const configId = id();
    const featureSet = id();
    await pool.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
      VALUES($1,1,'t1','{}',$2,'fixture',clock_timestamp())`, [featureSet, sha(featureSet)]);
    await pool.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,$3::jsonb,'{}',$4,'fixture',$5)`, [configId, sourceId, JSON.stringify(spec), sha(configId), createdAt]);
    if (modelKey) {
      await pool.query(`INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,adapter_spec,training_settings,threshold_spec)
        VALUES($1,$2,$3,'{}',$4::jsonb,$5::jsonb)`, [configId, modelKey, featureSet,
        JSON.stringify({ featureNames: ['error_count', 'retry_count'], methodVersion: 'process-iforest-v1', parameters: { n_estimators: 64 } }), JSON.stringify({ value: '0.5' })]);
    }
    if (job) {
      await pool.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,
          result_config_version_id,created_at,finished_at)
        VALUES($1,'train','succeeded','fixture','admin',$2,$3::jsonb,1,1,clock_timestamp(),$4,clock_timestamp(),clock_timestamp())`,
      [id(), sha(configId), JSON.stringify({ from: '2026-10-01T00:00:00Z', until: '2026-10-31T00:00:00Z' }), configId]);
    }
    return configId;
  };
  const first = await version({ schemaVersion: 1, trainingSelection: { policyVersion: 'train-success-only-v1', selectedCount: 40 } }, 'iforest', { createdAt: '2026-10-01T00:00:00Z' });
  const second = await version({ schemaVersion: 2, detectionPolicy: { mode: 'rule_only' } }, null, { job: false, createdAt: '2026-10-02T00:00:00Z' });
  const third = await version({ schemaVersion: 1 }, 'minibatch_kmeans', { createdAt: '2026-10-03T00:00:00Z' });
  await pool.query(`INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,changed_by_actor_id) VALUES(5,$1::jsonb,'[]','fixture')`,
    [JSON.stringify({ models: [{ configVersionId: first, modelKey: 'iforest', activatedAtRevision: 5 }] })]);
  const list = (extra = {}) => `/api/configurations?${new URLSearchParams({ sourceId, ...extra })}`;
  const page = await get(list({ limit: '2' }));
  assert.equal(page.status, 200, JSON.stringify(page.body));
  assert.deepEqual(page.body.items.map((item) => item.id), [third, second]);
  assert.equal(page.body.pagination.hasMore, true);
  assert.deepEqual((await get(list({ limit: '3' }))).body.pagination, { limit: 3, hasMore: false, nextCursor: null }, 'a full last page has no next cursor');
  const next = await get(list({ limit: '2', cursor: page.body.pagination.nextCursor }));
  assert.deepEqual(next.body.items.map((item) => item.id), [first]);
  assert.deepEqual(next.body.pagination, { limit: 2, hasMore: false, nextCursor: null });
  const [item] = next.body.items;
  assert.equal(item.active, true);
  assert.equal(item.activeSince.revision, 5);
  assert.equal(item.modelKey, 'iforest');
  assert.equal(item.trainingSelection.trainingPeriod.timeBasis, 'persisted_at');
  assert.equal(page.body.items[1].modelKey, null);
  assert.equal(page.body.items[0].executable, false);
  assert.deepEqual((await get(list({ modelKey: 'iforest' }))).body.items.map((entry) => entry.id), [first]);
  assert.deepEqual((await get(list({ executable: 'true' }))).body.items.map((entry) => entry.id), [first]);
  assert.deepEqual((await get(list({ executable: 'false' }))).body.items.map((entry) => entry.id), [third, second]);
  assert.equal((await get(list({ createdFrom: 'compose' }))).body.items.length, 0);
  assert.equal((await get(list({ createdFrom: 'train' }))).body.items.length, 3);
  assert.deepEqual(item.modelSummary, { featureCount: 2, parameters: { n_estimators: 64 }, thresholdValue: '0.5' });
  assert.equal(page.body.items[1].modelSummary, null, 'a rule-only row has no model summary');
  const activeOnly = await get(list({ active: 'true' }));
  assert.deepEqual(activeOnly.body.items.map((entry) => entry.id), [first], 'the filter finds a version beyond the first page without scanning');
  assert.deepEqual((await get(list({ active: 'false' }))).body.items.map((entry) => entry.id), [third, second]);
  assert.deepEqual((await get(list({ active: 'true', modelKey: 'minibatch_kmeans' }))).body.items, []);
  const activeFirst = await get(list({ active: 'false', limit: '1' }));
  assert.deepEqual((await get(list({ active: 'false', limit: '1', cursor: activeFirst.body.pagination.nextCursor }))).body.items.map((entry) => entry.id), [second], 'the cursor keeps the filter');
  assert.equal((await get(list({ active: 'yes' }))).body.code, 'invalid_active');
  assert.equal((await get(list({ limit: 'x' }))).body.code, 'invalid_limit');
  assert.equal((await get(list({ limit: 'x' }))).status, 400);
  const badCursor = Buffer.from(JSON.stringify({ createdAt: '2026-02-30T00:00:00.000000Z', id: first })).toString('base64url');
  const refusedCursor = await get(list({ cursor: badCursor }));
  assert.equal(refusedCursor.status, 400, 'an impossible date is a bad request, not a storage failure');
  assert.equal(refusedCursor.body.code, 'invalid_cursor');
  assert.equal((await get(list({ limit: '1' }), user)).status, 200, 'any signed-in user reads the list, like GET /:id');

  // v1·v2 운영의 단일 구성(ops_system_state)은 운영 중으로 보지 않는다: 활성 모델 집합만 정본(운영 설정 하위 7)
  const legacy = await activateConfig(pool, sourceId, { trainFrom: '2026-09-01T00:00:00Z' });
  const running = (await get(`/api/configurations/${legacy}`)).body;
  assert.deepEqual([running.active, running.activeSince], [false, null]);
  assert.deepEqual((await get(list({ active: 'true' }))).body.items.map((entry) => entry.id), [first], 'the filter uses the same active rule as the field');

  // 상세: 기존 키 유지 + 모델·파라미터·입력 변수·산출물
  const detail = await get(`/api/configurations/${first}`);
  assert.equal(detail.status, 200);
  assert.equal(detail.body.id, first);
  assert.equal(detail.body.contentHash, sha(first));
  assert.equal(detail.body.configurationSpec.schemaVersion, 1);
  assert.deepEqual(detail.body.methods[0].parameters, { n_estimators: 64 });
  assert.equal(detail.body.methods[0].methodVersion, 'process-iforest-v1');
  assert.deepEqual(detail.body.models[0].trainingSettings.featureNames, ['error_count', 'retry_count']);
  assert.deepEqual(detail.body.models[0].thresholdSpec, { value: '0.5' });
  assert.deepEqual(detail.body.rules, []);
  assert.equal((await get(`/api/configurations/${id()}`)).status, 404);
  assert.equal((await get('/api/configurations/not-a-uuid')).status, 400);
  assert.equal((await get('/api/configurations/active')).status, 200, 'fixed paths keep working before :id');
  assert.equal((await call('/api/configurations')).status, 401);

  // 학습 요청: 카탈로그 기준 접수 검증, parameters 전달, 미지원 값은 거절
  const featureSetId = id(), featureVersionId = id();
  await pool.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
    VALUES($1,'t','t','["error_count"]',$2,'fixture',clock_timestamp())`, [featureSetId, sha(featureSetId)]);
  await pool.query(`INSERT INTO ml_feature_versions(id,definition_key,version,display_name,definition_kind,expression,output_type,value_policy,availability_spec,requires_fit,content_hash,created_by_actor_id,created_at)
    VALUES($1,$2,1,'Error count','test','{"op":"field","field":"P.error_count"}','decimal','{}','{}',false,$3,'fixture',clock_timestamp())`, [featureVersionId, `k_${featureVersionId.replaceAll('-', '')}`, sha(featureVersionId)]);
  await pool.query(`INSERT INTO ml_feature_set_members(feature_set_id,feature_version_id,model_input,output_position,output_name,transform_settings)
    VALUES($1,$2,true,0,'error_count','{}')`, [featureSetId, featureVersionId]);
  const train = { kind: 'train', sourceId, from: '2026-09-30T15:00:00.000Z', until: '2026-10-01T15:00:00.000Z', featureSetId,
    detectionPolicy: { mode: 'ml_only', deadlineMs: 3000, combination: 'selected_path' }, modelKey: 'iforest', parameters: { n_estimators: 64, random_state: 7 } };
  const post = (body, token = admin, key) => call('/api/jobs', { method: 'POST', token, body, key });
  const refused = async (extra, status, code) => {
    const response = await post({ ...train, ...extra });
    assert.equal(response.status, status, JSON.stringify(extra));
    assert.equal(response.body.code, code);
  };
  await refused({ modelKey: 'kmeans' }, 422, 'unsupported_model');
  await refused({ modelKey: null }, 422, 'unsupported_model');
  await refused({ modelKey: 'minibatch_kmeans' }, 422, 'method_not_executable:ml:minibatch_kmeans');
  await refused({ modelKey: 'eif', parameters: undefined }, 422, 'method_not_executable:ml:eif');
  await refused({ parameters: { n_estimators: 1 } }, 422, 'method_parameters_invalid:ml:iforest');
  await refused({ parameters: { unknown: 1 } }, 422, 'method_parameters_invalid:ml:iforest');
  await refused({ parameters: 'x' }, 422, 'method_parameters_invalid:ml:iforest');
  await refused({ detectionPolicy: { mode: 'rule_only', deadlineMs: 3000, combination: 'selected_path' } }, 422, 'model_not_selected');
  await refused({ modelKey: undefined, detectionPolicy: { mode: 'rule_only', deadlineMs: 3000, combination: 'selected_path' }, parameters: { n_estimators: 1 } }, 422, 'method_parameters_invalid:ml:iforest');
  await refused({ timeBasis: 'decided_at' }, 400, 'invalid_job_request');
  await refused({ timeBasis: 'esb_occurred_at' }, 422, 'config_schema_not_supported_yet');
  const inside = { from: '2026-10-01T00:00:00Z', until: '2026-10-01T01:00:00Z' };
  await refused({ excludedIntervals: [inside] }, 422, 'config_schema_not_supported_yet');
  await refused({ excludedIntervals: [] }, 422, 'config_schema_not_supported_yet');
  await refused({ excludedIntervals: [{ ...inside, reasonCode: 'known_incident', note: null }] }, 422, 'config_schema_not_supported_yet');
  await refused({ excludedIntervals: [{ from: '2026-09-01T00:00:00Z', until: '2026-09-02T00:00:00Z' }] }, 400, 'excluded_interval_out_of_range');
  await refused({ excludedIntervals: [inside, { from: '2026-10-01T00:30:00Z', until: '2026-10-01T02:00:00Z' }] }, 400, 'excluded_interval_overlap');
  await refused({ excludedIntervals: [{ ...inside, reasonCode: 'other_reason' }] }, 400, 'invalid_job_request');
  await refused({ excludedIntervals: [{ from: inside.until, until: inside.from }] }, 400, 'invalid_job_request');
  assert.equal((await post({ ...train, kind: 'recommend_feature', featureSetId: undefined, detectionPolicy: undefined, modelKey: undefined })).status, 400, 'parameters belong to train only');
  assert.equal((await post(train, user)).status, 403);
  const before = (await pool.query(`SELECT count(*)::int AS count FROM run_jobs WHERE kind='train' AND status='queued'`)).rows[0].count;
  assert.equal(before, 0, 'refused requests leave no job');

  const key = id();
  const accepted = await post(train, admin, key);
  assert.equal(accepted.status, 201, JSON.stringify(accepted.body));
  assert.equal(accepted.body.status, 'queued');
  assert.deepEqual(accepted.body.inputSpec.parameters, train.parameters);
  assert.equal(accepted.body.inputSpec.modelKey, 'iforest');
  assert.equal((await post(train, admin, key)).body.id, accepted.body.id, 'the same key and body return the same job');
  assert.equal((await post({ ...train, parameters: { n_estimators: 65 } }, admin, key)).status, 409);
  assert.equal((await pool.query(`SELECT count(*)::int AS count FROM powercode_queue.training_jobs WHERE payload->>'jobId'=$1`, [accepted.body.id])).rows[0].count, 1);
  const read = await get(`/api/jobs/${accepted.body.id}`);
  assert.equal(read.body.status, 'queued');
  assert.equal(read.body.attemptCount, 0);
  const explicit = await post({ ...train, timeBasis: 'persisted_at', parameters: null });
  assert.equal(explicit.status, 201);
  assert.equal('timeBasis' in explicit.body.inputSpec, false, 'persisted_at equals the omitted key');
  assert.equal('parameters' in explicit.body.inputSpec, false);
  console.log('model version API integration passed: catalog, volume, version list and detail, training request under powercode_api');
} finally {
  if (child && child.exitCode === null) {
    child.kill();
    await new Promise((resolve) => child.once('exit', resolve));
  }
  await rm(temporary, { recursive: true, force: true });
  await pool.end();
}
