// 비교 평가 v1(config-comparison-evaluation.md v1 구현 범위, evaluations-ui.md) 통합 시험: 평가 세트 접수·조회, compare 접수 검증·기준선 고정,
// 비교 목록·상세(완료 전·완료 뒤·추천 계산 전 결과의 저장 사유·추천 원소와 decision), 비교가 만든 평가로 운영 적용 gate 통과(모델 교체 3단계).
// API는 운영과 같은 powercode_api 역할로 붙는다. Trainer 쪽 처리는 comparison-fixture.mjs가 흉내 낸다(시험 전용).
// 로컬 전용 새 QA 데이터베이스(qa_comparisons*)만 허용한다. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { completeComparison, failSet, pushActiveModels, readySet, seedSnapshot, seedSource, seedVersion } from './comparison-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_comparisons[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_comparisons database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-comparisons-'));
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
    JWT_SECRET: 'qa-comparisons-local-only-secret',
    SEED_USER_EMAIL: 'comparisons-user@example.test', SEED_USER_PASSWORD: 'qa-comparisons-local-only-password',
    SEED_ADMIN_EMAIL: 'comparisons-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-comparisons-admin-local-only',
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
  const call = async (route, { method = 'GET', token, body, headers = {} } = {}) => {
    const response = await fetch(`${origin}${route}`, {
      method, body: body === undefined ? undefined : JSON.stringify(body),
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
    });
    const text = await response.text();
    let value = null;
    try { value = JSON.parse(text); } catch { /* 본문 없음 */ }
    return { status: response.status, body: value };
  };
  const login = async (email, password) => (await call('/api/auth/login', { method: 'POST', body: { email, password } })).body.token;
  const admin = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const user = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  assert.ok(admin && user);
  const code = (result) => [result.status, result.body?.code];
  const createSet = (body, headers = {}, token = admin) => call('/api/evaluation-sets', { method: 'POST', token, body, headers });
  const compare = (body, headers = {}, token = admin) => call('/api/jobs', { method: 'POST', token, body: { kind: 'compare', ...body }, headers });

  const sourceId = await seedSource(pool);
  const otherSource = await seedSource(pool, 'comparison-other');
  const a = await seedVersion(pool, sourceId, { name: 'A' });
  const b = await seedVersion(pool, sourceId, { name: 'B' });
  const eif = await seedVersion(pool, sourceId, { modelKey: 'eif', name: 'EIF' });
  const ruleOnly = await seedVersion(pool, sourceId, { modelKey: null });
  const unknownSchema = await seedVersion(pool, sourceId, { schemaVersion: 4 });
  const foreign = await seedVersion(pool, otherSource, { name: 'other' });
  const prepared = await seedSnapshot(pool, sourceId, { schemaVersion: 'prepared-features-v1' });
  const building = await seedSnapshot(pool, sourceId, { status: 'building' });

  // 1) 인증·권한: 관리자 전용
  assert.equal((await call('/api/evaluation-sets')).status, 401);
  assert.deepEqual(code(await call('/api/evaluation-sets', { token: user })), [403, 'admin_required']);
  assert.deepEqual(code(await call('/api/comparisons', { token: user })), [403, 'admin_required']);
  assert.deepEqual(code(await createSet({ sourceId, snapshotId: b.snapshotId, purpose: 'validation', displayName: 'x', synthetic: null }, {}, user)), [403, 'admin_required']);
  assert.deepEqual(code(await compare({ sourceId, evaluationSetId: id(), configVersionIds: [b.configId] }, {}, user)), [403, 'admin_required']);

  // 2) 평가 세트 접수 검증
  const setBody = { sourceId, snapshotId: b.snapshotId, purpose: 'validation', displayName: '  B 검증 분할  ', synthetic: null };
  assert.deepEqual(code(await createSet({ ...setBody, extra: 1 })), [400, 'invalid_evaluation_set_request']);
  assert.deepEqual(code(await createSet({ ...setBody, purpose: 'train' })), [400, 'invalid_evaluation_set_request']);
  assert.deepEqual(code(await createSet({ ...setBody, displayName: '   ' })), [400, 'invalid_evaluation_set_request']);
  assert.deepEqual(code(await createSet({ ...setBody, displayName: 'x'.repeat(121) })), [400, 'invalid_evaluation_set_request']);
  assert.deepEqual(code(await createSet({ ...setBody, synthetic: { profileId: 'esb-process-research-v1' } })), [400, 'synthetic_set_unsupported']);
  assert.deepEqual(code(await createSet({ ...setBody, sourceId: id() })), [404, 'source_not_found']);
  assert.deepEqual(code(await createSet({ ...setBody, snapshotId: id() })), [404, 'snapshot_not_found']);
  assert.deepEqual(code(await createSet({ ...setBody, snapshotId: foreign.snapshotId })), [404, 'snapshot_not_found'], 'snapshot of another source');
  assert.deepEqual(code(await createSet({ ...setBody, snapshotId: prepared })), [400, 'prepared_not_comparable']);
  assert.deepEqual(code(await createSet({ ...setBody, snapshotId: building })), [409, 'snapshot_not_ready']);
  const otherSchema = await seedSnapshot(pool, sourceId, { schemaVersion: 'v1' });
  assert.deepEqual(code(await createSet({ ...setBody, snapshotId: otherSchema })), [409, 'evaluation_set_snapshot_unavailable']);
  assert.deepEqual(code(await createSet(setBody, { 'Idempotency-Key': 'bad key!' })), [400, 'invalid_idempotency_key']);

  // 3) 평가 세트 접수: 작업 행·세트 행·큐 메시지가 함께. 작업 입력은 Trainer 키만.
  const queueBefore = Number((await pool.query('SELECT count(*) FROM powercode_queue.training_jobs')).rows[0].count);
  const setKey = { 'Idempotency-Key': 'set-b-1' };
  const created = await createSet(setBody, setKey);
  assert.equal(created.status, 201, JSON.stringify(created.body));
  assert.deepEqual(Object.keys(created.body).sort(), ['id', 'jobId', 'status']);
  assert.equal(created.body.status, 'queued');
  const setId = created.body.id;
  const { rows: [setJob] } = await pool.query(`SELECT kind::text,queue::text,contract_version,producer_kind::text,requester_role::text,input_snapshot_id::text,input_spec,status::text FROM run_jobs WHERE id=$1`, [created.body.jobId]);
  assert.deepEqual(setJob, { kind: 'evaluation_set', queue: 'training', contract_version: 'v03', producer_kind: 'user', requester_role: 'admin',
    input_snapshot_id: b.snapshotId, input_spec: { kind: 'evaluation_set', evaluationSetId: setId, sourceId, snapshotId: b.snapshotId, purpose: 'validation' }, status: 'queued' });
  assert.equal(Number((await pool.query('SELECT count(*) FROM powercode_queue.training_jobs')).rows[0].count), queueBefore + 1);
  const { rows: [setRow] } = await pool.query('SELECT display_name,status,synthetic_request,created_by_actor_id FROM data_evaluation_sets WHERE id=$1', [setId]);
  assert.deepEqual([setRow.display_name, setRow.status, setRow.synthetic_request], ['B 검증 분할', 'queued', null]);
  const replay = await createSet(setBody, setKey);
  assert.deepEqual([replay.status, replay.body], [200, created.body], 'same key + same body returns the same set');
  assert.deepEqual(code(await createSet({ ...setBody, displayName: '다른 이름' }, setKey)), [409, 'idempotency_payload_conflict']);
  assert.equal(Number((await pool.query('SELECT count(*) FROM powercode_queue.training_jobs')).rows[0].count), queueBefore + 1, 'replay does not enqueue');

  // 4) 세트 조회: 생성 전은 generation null
  const queuedDetail = await call(`/api/evaluation-sets/${setId}`, { token: admin });
  assert.equal(queuedDetail.status, 200);
  assert.equal(queuedDetail.body.status, 'queued');
  assert.equal(queuedDetail.body.generation, null);
  assert.equal(queuedDetail.body.jobId, created.body.jobId);
  assert.deepEqual(code(await call(`/api/evaluation-sets/${id()}`, { token: admin })), [404, 'evaluation_set_not_found']);
  assert.deepEqual(code(await call('/api/evaluation-sets/x', { token: admin })), [400, 'invalid_evaluation_set_id']);
  assert.deepEqual(code(await call('/api/evaluation-sets?foo=1', { token: admin })), [400, 'unknown_evaluation_set_filter']);
  const snapshots = await call(`/api/evaluation-sets/snapshots?sourceId=${sourceId}`, { token: admin });
  assert.equal(snapshots.status, 200);
  const bSnapshot = snapshots.body.items.find((item) => item.id === b.snapshotId);
  assert.deepEqual(bSnapshot.configVersionIds, [b.configId], 'training snapshot lists the version trained on it');
  assert.ok(!snapshots.body.items.some((item) => item.id === prepared || item.id === building || item.id === foreign.snapshotId));

  // 5) 준비 전 세트로 비교 요청은 409
  assert.deepEqual(code(await compare({ sourceId, evaluationSetId: setId, configVersionIds: [b.configId] })), [409, 'evaluation_set_not_ready']);

  // 6) Trainer가 세트를 고정(흉내). 빈 세트 실패 경로도 확인.
  const contentHash = await readySet(pool, setId, { rowCount: 1200, truthCount: 20 });
  const readyDetail = (await call(`/api/evaluation-sets/${setId}`, { token: admin })).body;
  assert.deepEqual(readyDetail.generation, { rowCount: 1200, originalRowCount: 1200, syntheticRowCount: 0, truthCount: 20, contentHash, artifactRef: null });
  assert.ok(readyDetail.readyAt);
  const emptySet = await createSet({ ...setBody, displayName: '빈 세트' });
  await failSet(pool, emptySet.body.id);
  const emptyDetail = (await call(`/api/evaluation-sets/${emptySet.body.id}`, { token: admin })).body;
  assert.deepEqual([emptyDetail.status, emptyDetail.errorCode, emptyDetail.generation], ['failed', 'evaluation_set_empty', null]);
  const list = await call(`/api/evaluation-sets?sourceId=${sourceId}&limit=1`, { token: admin });
  assert.equal(list.body.items.length, 1);
  assert.equal(list.body.items[0].id, emptySet.body.id, 'newest first');
  const next = await call(`/api/evaluation-sets?sourceId=${sourceId}&limit=1&cursor=${list.body.pagination.nextCursor}`, { token: admin });
  assert.equal(next.body.items[0].id, setId);
  assert.equal(next.body.pagination.hasMore, false);

  // 7) compare 접수 검증
  const base = { sourceId, evaluationSetId: setId };
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [] })), [400, 'comparison_configs_required']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: Array.from({ length: 9 }, id) })), [400, 'comparison_too_many_configs']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [b.configId, b.configId] })), [400, 'invalid_comparison_request']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [b.configId], extra: 1 })), [400, 'invalid_comparison_request']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [id()] })), [404, 'config_not_found']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [foreign.configId] })), [400, 'config_source_mismatch']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [ruleOnly.configId] })), [400, 'comparison_config_not_comparable']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [unknownSchema.configId] })), [400, 'comparison_config_not_comparable']);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [b.configId, eif.configId] })), [400, 'comparison_model_key_mixed']);
  assert.deepEqual(code(await compare({ ...base, evaluationSetId: emptySet.body.id, configVersionIds: [b.configId] })), [409, 'evaluation_set_not_ready']);
  assert.deepEqual(code(await compare({ ...base, evaluationSetId: id(), configVersionIds: [b.configId] })), [404, 'evaluation_set_not_found']);
  assert.deepEqual(code(await compare({ sourceId: otherSource, evaluationSetId: setId, configVersionIds: [foreign.configId] })), [400, 'evaluation_set_source_mismatch']);

  // 8) compare 접수: 같은 종류 운영 버전을 기준선으로 고정. 리비전·정책도 고정.
  const activeRevision = await pushActiveModels(pool, [{ configVersionId: a.configId, modelKey: 'iforest' }]);
  const state = (await call('/api/operational-state', { token: admin })).body;
  const compareKey = { 'Idempotency-Key': 'compare-b-1' };
  const accepted = await compare({ ...base, configVersionIds: [b.configId] }, compareKey);
  assert.equal(accepted.status, 201, JSON.stringify(accepted.body));
  assert.equal(accepted.body.kind, 'compare');
  assert.deepEqual(accepted.body.inputSpec, {
    kind: 'compare', sourceId, evaluationSetId: setId, configVersionIds: [a.configId, b.configId].sort(),
    baselines: [{ modelKey: 'iforest', configVersionId: a.configId }], activeModelSetRevision: activeRevision,
    ruleSetRevision: state.ruleSet.revision, ruleSetContentHash: state.ruleSet.contentHash,
    detectionPolicyRevision: 0, detectionPolicy: null,
  });
  const jobId = accepted.body.id;
  const { rows: [compareJob] } = await pool.query(`SELECT queue::text,contract_version,input_snapshot_id,input_config_version_id FROM run_jobs WHERE id=$1`, [jobId]);
  assert.deepEqual(compareJob, { queue: 'training', contract_version: 'v03', input_snapshot_id: null, input_config_version_id: null });
  const again = await compare({ ...base, configVersionIds: [b.configId] }, compareKey);
  assert.deepEqual([again.status, again.body.id], [200, jobId]);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [a.configId] }, compareKey)), [409, 'idempotency_payload_conflict']);
  const noBaseline = await compare({ ...base, configVersionIds: [eif.configId] });
  assert.equal(noBaseline.status, 201);
  assert.deepEqual([noBaseline.body.inputSpec.baselines, noBaseline.body.inputSpec.configVersionIds], [[], [eif.configId]], 'no active eif: no baseline');
  const activeOnly = await compare({ ...base, configVersionIds: [a.configId] });
  assert.deepEqual(activeOnly.body.inputSpec.configVersionIds, [a.configId], 'active requested explicitly is merged');

  // 9) 완료 전 조회: 묶음 없음(comparisonId null), 접수 항목만, 추천은 아직 없음(사유 없음)
  const pending = await call(`/api/comparisons/${jobId}`, { token: admin });
  assert.equal(pending.status, 200, JSON.stringify(pending.body));
  assert.equal(pending.body.comparisonId, null);
  assert.equal(pending.body.status, 'queued');
  assert.deepEqual(pending.body.items.map((item) => [item.configVersionId, item.role, item.status, item.modelKey]).sort(),
    [[a.configId, 'active', 'pending', 'iforest'], [b.configId, 'candidate', 'pending', 'iforest']].sort());
  assert.deepEqual([pending.body.replacementRecommendations, pending.body.replacementRecommendationsReason], [null, null]);
  const pendingList = (await call(`/api/comparisons?evaluationSetId=${setId}`, { token: admin })).body;
  const pendingRow = pendingList.items.find((item) => item.jobId === jobId);
  assert.deepEqual([pendingRow.comparisonId, pendingRow.status, pendingRow.itemCount, pendingRow.recommendations], [null, 'queued', 2, null]);

  // 10) Trainer가 결과 기록(흉내): 묶음 + 항목 평가 + 작업 성공. 추천 계산 전(11/15 이전) 결과 모양: null + 저장 사유.
  const { comparisonId, items } = await completeComparison(pool, jobId, { [a.configId]: { tp: 9, fn: 11 }, [b.configId]: { tp: 14, fn: 6 } });
  const detail = await call(`/api/comparisons/${comparisonId}`, { token: admin });
  assert.equal(detail.status, 200);
  assert.deepEqual([detail.body.comparisonId, detail.body.jobId, detail.body.status, detail.body.comparisonContractVersion], [comparisonId, jobId, 'succeeded', 'config-comparison-v1']);
  assert.deepEqual([detail.body.replacementRecommendations, detail.body.replacementRecommendationsReason], [null, 'not_implemented_v1']);
  assert.equal(detail.body.items.length, 2);
  assert.equal(detail.body.items.find((item) => item.configVersionId === b.configId).categoryResults.ml.measurementStatus, 'measured');
  assert.deepEqual((await call(`/api/comparisons/${jobId}`, { token: admin })).body, detail.body, 'job id resolves to the same comparison');
  const jobResult = (await call(`/api/jobs/${jobId}`, { token: admin })).body;
  assert.equal(jobResult.resultSummary.comparisonId, comparisonId);
  const doneRow = (await call(`/api/comparisons?evaluationSetId=${setId}&modelKey=iforest`, { token: admin })).body.items.find((item) => item.jobId === jobId);
  assert.deepEqual([doneRow.comparisonId, doneRow.status, doneRow.succeededCount, doneRow.failedCount, doneRow.recommendations, doneRow.recommendationsReason],
    [comparisonId, 'succeeded', 2, 0, null, 'not_implemented_v1']);
  assert.ok(doneRow.completedAt);
  assert.ok(!(await call(`/api/comparisons?modelKey=eif`, { token: admin })).body.items.some((item) => item.jobId === jobId), 'modelKey filter');
  assert.ok((await call('/api/comparisons?scope=current', { token: admin })).body.items.some((item) => item.jobId === jobId));
  assert.deepEqual(code(await call('/api/comparisons?scope=x', { token: admin })), [400, 'invalid_scope']);
  assert.deepEqual(code(await call(`/api/comparisons/${id()}`, { token: admin })), [404, 'comparison_not_found']);
  assert.deepEqual(code(await call(`/api/comparisons/${created.body.jobId}`, { token: admin })), [404, 'comparison_not_found'], 'evaluation_set job is not a comparison');

  // 11) 모든 항목 실패 → 작업 실패, 상세는 실패 상태
  const failedRun = await completeComparison(pool, noBaseline.body.id, { [eif.configId]: { error: 'training_evaluation_overlap' } });
  const failedDetail = (await call(`/api/comparisons/${noBaseline.body.id}`, { token: admin })).body;
  assert.deepEqual([failedDetail.comparisonId, failedDetail.status, failedDetail.errorCode, failedDetail.items[0].errorCode],
    [failedRun.comparisonId, 'failed', 'comparison_all_items_failed', 'training_evaluation_overlap']);
  assert.ok(!(await call('/api/comparisons?scope=current', { token: admin })).body.items.some((item) => item.jobId === noBaseline.body.id), 'failed is not in current scope');

  // 11-1) 접수 뒤 없어진 구성(config_not_found)은 항목 표에 없고 summary에만 있다: 수는 summary 기준
  await completeComparison(pool, activeOnly.body.id, { [a.configId]: { error: 'config_not_found' } });
  const goneRow = (await call(`/api/comparisons?evaluationSetId=${setId}`, { token: admin })).body.items.find((item) => item.jobId === activeOnly.body.id);
  assert.deepEqual([goneRow.status, goneRow.itemCount, goneRow.succeededCount, goneRow.failedCount], ['failed', 1, 0, 1]);
  assert.equal((await call(`/api/comparisons/${activeOnly.body.id}`, { token: admin })).body.items[0].errorCode, 'config_not_found');

  // 11-2) 모델 버전 상세 평가 이력: configVersionId = 그 버전이 항목(접수 때 넣은 기준 운영 버전 포함)인 비교만, 항목마다 그 버전 결과
  const historyOf = async (configId) => (await call(`/api/comparisons?configVersionId=${configId}`, { token: admin })).body.items;
  assert.ok(!('versionResult' in pendingList.items[0]), 'no versionResult without the filter');
  const aHistory = await historyOf(a.configId.toUpperCase());
  assert.deepEqual(aHistory.map((item) => item.jobId), [activeOnly.body.id, jobId], 'baseline version: both comparisons, newest first');
  assert.deepEqual(aHistory.map((item) => item.versionResult), [
    { configVersionId: a.configId, role: 'active', status: 'failed', errorCode: 'config_not_found', measurementStatus: null },
    { configVersionId: a.configId, role: 'active', status: 'succeeded', errorCode: null, measurementStatus: 'measured' },
  ]);
  assert.deepEqual((await historyOf(b.configId)).map((item) => [item.jobId, item.versionResult.role, item.versionResult.status, item.versionResult.measurementStatus]),
    [[jobId, 'candidate', 'succeeded', 'measured']]);
  const eifPending = await compare({ ...base, configVersionIds: [eif.configId] });
  assert.equal(eifPending.status, 201);
  assert.deepEqual((await historyOf(eif.configId)).map((item) => [item.jobId, item.status, item.versionResult]), [
    [eifPending.body.id, 'queued', { configVersionId: eif.configId, role: 'candidate', status: 'pending', errorCode: null, measurementStatus: null }],
    [noBaseline.body.id, 'failed', { configVersionId: eif.configId, role: 'candidate', status: 'failed', errorCode: 'training_evaluation_overlap', measurementStatus: null }],
  ]);
  // 묶음 없이 작업이 실패하면(Trainer 실행 실패) 작업 실패 코드
  await pool.query(`UPDATE run_jobs SET status='failed',error_code='comparison_execution_failed',finished_at=clock_timestamp() WHERE id=$1`, [eifPending.body.id]);
  assert.deepEqual((await historyOf(eif.configId))[0].versionResult, { configVersionId: eif.configId, role: 'candidate', status: 'failed', errorCode: 'comparison_execution_failed', measurementStatus: null });
  assert.deepEqual(await historyOf(ruleOnly.configId), [], 'never compared');
  assert.deepEqual((await call(`/api/comparisons?configVersionId=${b.configId}&modelKey=eif`, { token: admin })).body.items, [], 'filters combine');
  assert.deepEqual(code(await call('/api/comparisons?configVersionId=x', { token: admin })), [400, 'invalid_config_version_id']);
  // 모델 버전 조회 latestComparison: 그 버전이 들어간 가장 최근 비교(목록 상태 규칙 그대로). v1은 추천이 없어 recommendation null
  const latestOf = async (configId) => (await call(`/api/configurations/${configId}`, { token: admin })).body.latestComparison;
  const aLatest = await latestOf(a.configId);
  assert.deepEqual({ ...aLatest, completedAt: typeof aLatest.completedAt }, { comparisonJobId: activeOnly.body.id, evaluationSetId: setId, status: 'failed', completedAt: 'string', auto: false, recommendation: null });
  assert.deepEqual([(await latestOf(b.configId)).comparisonJobId, (await latestOf(b.configId)).status], [jobId, 'succeeded']);
  assert.equal((await latestOf(ruleOnly.configId)), null);
  const listed = (await call(`/api/configurations?sourceId=${sourceId}`, { token: admin })).body.items;
  assert.deepEqual(listed.find((item) => item.id === eif.configId).latestComparison.comparisonJobId, eifPending.body.id, 'list carries the same value');
  assert.equal(listed.find((item) => item.id === unknownSchema.configId).latestComparison, null);

  // 12) 모델 교체 3단계: 비교가 만든 B 평가가 운영 적용 gate 근거가 된다(B에는 다른 평가가 없다).
  const bEvaluation = items.find((item) => item.configVersionId === b.configId).evaluationId;
  const bVersion = (await call(`/api/configurations/${b.configId}`, { token: admin })).body;
  assert.deepEqual(bVersion.applicationEligibility, { eligible: true, evaluationId: bEvaluation, reason: null });
  const replaced = await call(`/api/configurations/${b.configId}/applications`, { method: 'POST', token: admin,
    body: { operation: 'replace', evaluationId: bEvaluation, expectedRevision: activeRevision, expectedActiveVersionId: a.configId } });
  assert.equal(replaced.status, 200, JSON.stringify(replaced.body));
  assert.deepEqual(replaced.body.models.map((model) => model.configVersionId), [b.configId]);
  assert.ok(!(await call('/api/comparisons?scope=current', { token: admin })).body.items.some((item) => item.jobId === jobId), 'active set changed: no longer current');

  // 12-1) 추천 계산 뒤 결과 모양(Trainer replacement_recommendation): 원소 그대로 + 조회 때 상태, 목록은 decision 포함.
  // 운영 버전이 B로 바뀐 뒤 A를 후보로 비교 → 기준선 B. 추천 원소는 Trainer 저장값 모양(필드 일부)으로 흉내 낸다.
  const recompare = await compare({ ...base, configVersionIds: [a.configId] });
  assert.equal(recompare.status, 201, JSON.stringify(recompare.body));
  const replacedRevision = recompare.body.inputSpec.activeModelSetRevision;
  const element = (comparisonId, recItems, decision, target) => ({
    modelKey: 'iforest', ruleVersion: 'replacement-recommendation-v1', decision, comparisonId, comparisonJobId: recompare.body.id,
    evaluationSetId: setId, baselineConfigVersionId: b.configId,
    baselineEvaluationId: recItems.find((entry) => entry.configVersionId === b.configId).evaluationId, baselineActiveRevision: replacedRevision,
    targetVersionId: target, targetEvaluationId: target ? recItems.find((entry) => entry.configVersionId === target).evaluationId : null,
    summary: target ? 'Isolation Forest 버전 aaaaaaaa이 현재 운영 Isolation Forest 버전 bbbbbbbb보다 모델 재현율 14/20(0.700) > 9/20(0.450)' : '현재 운영 버전보다 모든 근거 지표에서 같거나 나은 후보가 없습니다.',
    reason: target ? 'better_than_active_on_all_basis_metrics' : null, noRecommendationReason: target ? null : 'no_candidate_better_than_active',
    comparedRowCount: 1200, comparedLabelCounts: { anomalyLabelCount: 20, normalLabelCount: 0 }, basis: [], excludedMetrics: [], checks: null, costs: [],
    candidates: [{ configVersionId: a.configId, modelKey: 'iforest', outcome: target ? 'selected' : 'excluded', excludedReason: target ? null : 'worse_on:ml.completeOnly.recall', lostAt: null }],
    references: [], unlabeledEvidence: { interpretation: 'checker_agreement_not_accuracy', pairs: [] }, thresholds: {}, generatedAt: '2026-11-15T00:00:00.000Z',
  });
  const recommended = await completeComparison(pool, recompare.body.id, { [b.configId]: { tp: 9, fn: 11 }, [a.configId]: { tp: 14, fn: 6 } },
    { recommendations: (comparisonId, recItems) => [element(comparisonId, recItems, 'recommend_replace', a.configId)] });
  const recDetail = (await call(`/api/comparisons/${recompare.body.id}`, { token: admin })).body;
  assert.equal(recDetail.replacementRecommendationsReason, null);
  assert.deepEqual(recDetail.replacementRecommendations, [{ ...element(recommended.comparisonId, recommended.items, 'recommend_replace', a.configId), recommendationState: 'current' }]);
  const recRow = (await call(`/api/comparisons?evaluationSetId=${setId}`, { token: admin })).body.items.find((item) => item.jobId === recompare.body.id);
  assert.deepEqual([recRow.recommendations, recRow.recommendationsReason],
    [[{ modelKey: 'iforest', targetVersionId: a.configId, state: 'current', decision: 'recommend_replace' }], null]);
  assert.deepEqual((await latestOf(a.configId)).recommendation, { modelKey: 'iforest', targetVersionId: a.configId, state: 'current' }, 'model version summary reads the same element');
  // 추천 없음 원소(유지): 상태 none, 목록 decision keep_current. 예전 결과(10번)의 목록 칸은 그대로 null + 저장 사유.
  const keepJob = await compare({ ...base, configVersionIds: [a.configId] });
  await completeComparison(pool, keepJob.body.id, { [b.configId]: { tp: 9, fn: 11 }, [a.configId]: { tp: 5, fn: 15 } },
    { recommendations: (comparisonId, recItems) => [{ ...element(comparisonId, recItems, 'keep_current', null), comparisonJobId: keepJob.body.id }] });
  const keepRows = (await call(`/api/comparisons?evaluationSetId=${setId}`, { token: admin })).body.items;
  assert.deepEqual(keepRows.find((item) => item.jobId === keepJob.body.id).recommendations,
    [{ modelKey: 'iforest', targetVersionId: null, state: 'none', decision: 'keep_current' }]);
  assert.deepEqual((await call(`/api/comparisons/${keepJob.body.id}`, { token: admin })).body.replacementRecommendations[0].recommendationState, 'none');
  const oldRow = keepRows.find((item) => item.jobId === jobId);
  assert.deepEqual([oldRow.recommendations, oldRow.recommendationsReason], [null, 'not_implemented_v1'], 'stored v1 placeholder result stays readable');

  // 13) 활성 집합이 가리키는 기준선 버전이 없으면 접수 때 409(Trainer는 작업 실패로 끝낸다)
  await pushActiveModels(pool, [{ configVersionId: id(), modelKey: 'iforest' }]);
  assert.deepEqual(code(await compare({ ...base, configVersionIds: [b.configId] })), [409, 'comparison_baseline_not_found']);

  console.log('comparisons integration passed');
} finally {
  if (child && child.exitCode === null) {
    const exited = new Promise((resolve) => child.once('exit', resolve));
    child.kill();
    await exited;
  }
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
