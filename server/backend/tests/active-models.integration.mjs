// 활성 모델 집합(model-version.md 활성 집합 절)·운영 상태 일관 읽기·방법별 운영 상태(operations-ui.md) 통합 시험:
// 인증·권한, gate(평가·실행 가능·산출물·v3), apply·replace·retire·restore, 리비전 충돌, 같은 modelKey 1개, 탐지 방법 0개, 추천 수락 불일치,
// 멱등키, 이력, v1·v2 경로 유지, 방법별 집계(성공·미판정·사유·기한 초과·첫 실행 대기 기준 시각·모델 버전 구분·옛 판정), 503.
// 로컬 전용 새 QA 데이터베이스(qa_active_models*)만 허용한다. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { addProcess, pushActiveModels, seedRuleSetSource } from './rule-set-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_active_models[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_active_models database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-active-models-'));
const pool = new pg.Pool({ connectionString: url });
const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(String(value)).digest('hex');
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
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
async function stopChild() {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise((resolve) => child.once('exit', resolve));
  child.kill();
  await exited;
}

/** 모델 버전 1개: 산출물(model·fit) + 학습 작업 + (선택) 적격 검증 평가. 합성 값만 쓴다. */
async function seedVersion(sourceId, { modelKey = 'iforest', evaluation = true, artifacts = true, schemaVersion = 2, name = null } = {}) {
  const configId = id(), featureSet = id(), snapshot = id(), job = id(), model = id(), fit = id();
  await pool.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
    VALUES($1,1,'t1','{}',$2,'fixture',clock_timestamp())`, [featureSet, sha(featureSet)]);
  await pool.query(`INSERT INTO data_snapshots(id,source_id,status,observation_cutoff_at,selection_spec,split_spec,schema_version,created_by_actor_id,created_at,ready_at)
    VALUES($1,$2,'ready',clock_timestamp(),'{}','{}','v1','fixture',clock_timestamp(),clock_timestamp())`, [snapshot, sourceId]);
  await pool.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
    VALUES($1,$2,$3::jsonb,'{}',$4,'fixture',clock_timestamp())`, [configId, sourceId, JSON.stringify({ schemaVersion, displayName: name }), sha(configId)]);
  await pool.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,result_config_version_id,created_at,finished_at)
    VALUES($1,'train','succeeded','fixture','admin',$2,'{}',1,1,clock_timestamp(),$3,clock_timestamp(),clock_timestamp())`, [job, sha(job), configId]);
  if (artifacts) {
    await pool.query(`INSERT INTO ml_artifacts(id,kind,relative_path,content_hash,byte_size,snapshot_id,feature_set_id,created_job_id,format_version,runtime_version,metadata,created_at)
      VALUES($1,'fit',$2,$3,1,$4,$5,$6,'v1','t','{}',clock_timestamp())`, [fit, `fixture/${fit}`, sha(fit), snapshot, featureSet, job]);
    await pool.query(`INSERT INTO ml_artifacts(id,kind,relative_path,content_hash,byte_size,snapshot_id,feature_set_id,fit_artifact_id,created_job_id,format_version,runtime_version,metadata,created_at)
      VALUES($1,'model',$2,$3,1,$4,$5,$6,$7,'v1','t','{}',clock_timestamp())`, [model, `fixture/${model}`, sha(model), snapshot, featureSet, fit, job]);
  }
  await pool.query(`INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,adapter_spec,training_settings,threshold_spec)
    VALUES($1,$2,$3,$4,$5,'{}','{}','{}')`, [configId, modelKey, featureSet, artifacts ? model : null, artifacts ? fit : null]);
  let evaluationId = null;
  if (evaluation) {
    const evalJob = id();
    evaluationId = id();
    await pool.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,input_config_version_id,max_attempts,attempt_count,next_run_at,created_at,finished_at)
      VALUES($1,'evaluate','succeeded','fixture','admin',$2,'{}',$3,1,1,clock_timestamp(),clock_timestamp(),clock_timestamp())`, [evalJob, sha(evalJob), configId]);
    await pool.query(`INSERT INTO run_evaluations(id,job_id,config_version_id,snapshot_id,purpose,evaluation_spec_version,evaluation_spec_hash,evaluation_spec,applicability,
        population_count,scored_count,unavailable_count,metrics,reason_codes,completed_at)
      VALUES($1,$2,$3,$4,'validation','v1','h','{}','valid',10,10,0,'{}','[]',clock_timestamp())`, [evaluationId, evalJob, configId, snapshot]);
  }
  return { configId, evaluationId };
}

try {
  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: url,
    JWT_SECRET: 'qa-active-models-local-only-secret',
    SEED_USER_EMAIL: 'active-models-user@example.test', SEED_USER_PASSWORD: 'qa-active-models-local-only-password',
    SEED_ADMIN_EMAIL: 'active-models-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-active-models-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  // 운영과 같은 권한으로: 소유자 연결에서 역할 스크립트를 적용하고 API는 powercode_api 역할로 붙는다(model-versions 시험과 같은 방식).
  await pool.query(await readFile(path.join(backend, '../infra/scripts/database-roles.sql'), 'utf8'));
  const runtimeUrl = new URL(url);
  runtimeUrl.searchParams.set('options', '-c role=powercode_api');
  environment.DATABASE_URL = runtimeUrl.toString();
  let base = await startServer(environment);
  assert.equal((await pool.query(`SELECT has_table_privilege('powercode_api','ops_active_model_set_revisions','INSERT') AS ok`)).rows[0].ok, true);
  const call = async (route, { method = 'GET', token, body, headers = {} } = {}) => {
    const response = await fetch(`${base}${route}`, {
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
  const apply = (configId, body, token = admin, headers = {}) => call(`/api/configurations/${configId}/applications`, { method: 'POST', token, body, headers });
  const stateOf = async () => (await call('/api/operational-state', { token: admin })).body;
  const revision = async () => (await stateOf()).activeModelSet.revision;
  const code = (result) => [result.status, result.body?.code];

  // 1) 인증·권한
  assert.equal((await call('/api/operational-state')).status, 401);
  assert.equal((await call('/api/active-models/history')).status, 401);
  assert.equal((await call('/api/operations/method-status')).status, 401);
  const userState = await call('/api/operational-state', { token: user });
  assert.equal(userState.status, 200, 'any signed-in user reads the operational state');
  assert.equal((await call('/api/active-models/restore', { method: 'POST', token: user, body: {} })).status, 403);

  // 2) 운영 상태: 세 리비전을 한 번에. 정책 행이 없으면 리비전 0의 내장 기본 정책.
  const initial = userState.body;
  assert.deepEqual(Object.keys(initial).sort(), ['activeModelSet', 'detectionPolicy', 'readAt', 'ruleSet']);
  assert.deepEqual([initial.activeModelSet.revision, initial.activeModelSet.models], [0, []]);
  assert.deepEqual(initial.detectionPolicy, { revision: 0, isDefault: true, policy: { version: 2, policyKey: 'category-k-of-n-or-v1', k: { hard: 1, soft: 1, ml: 1 }, deadlineMs: 3000 } });
  assert.match(initial.ruleSet.contentHash, /^[0-9a-f]{64}$/);
  assert.equal((await call('/api/operational-state?x=1', { token: admin })).body.code, 'unknown_operational_state_filter');

  const sourceId = await seedRuleSetSource(pool, 'active-models');
  const a = await seedVersion(sourceId, { name: 'A' });
  const b = await seedVersion(sourceId, { name: 'B' });
  const noEvaluation = await seedVersion(sourceId, { evaluation: false });
  const noArtifacts = await seedVersion(sourceId, { artifacts: false });
  const research = await seedVersion(sourceId, { modelKey: 'minibatch_kmeans' });
  const v3 = await seedVersion(sourceId, { schemaVersion: 3 });

  // 3) 형식·gate 거절은 집합을 바꾸지 않는다.
  assert.deepEqual(code(await apply(a.configId, { operation: 'apply', expectedRevision: 0 })), [400, 'invalid_application_request'], 'evaluationId is required');
  assert.deepEqual(code(await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0, extra: 1 })), [400, 'invalid_application_request']);
  assert.deepEqual(code(await apply(a.configId, { operation: 'swap', expectedRevision: 0 })), [400, 'invalid_application_request']);
  assert.deepEqual(code(await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0 }, user)), [403, 'admin_required']);
  assert.deepEqual(code(await apply(id(), { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0 })), [404, 'config_not_found']);
  assert.deepEqual(code(await apply(noEvaluation.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0 })), [409, 'evaluation_gate_not_met']);
  assert.deepEqual(code(await apply(a.configId, { operation: 'apply', evaluationId: b.evaluationId, expectedRevision: 0 })), [409, 'evaluation_gate_not_met'], 'another version evaluation');
  assert.deepEqual(code(await apply(noArtifacts.configId, { operation: 'apply', evaluationId: noArtifacts.evaluationId, expectedRevision: 0 })), [409, 'model_artifacts_not_ready']);
  assert.deepEqual(code(await apply(research.configId, { operation: 'apply', evaluationId: research.evaluationId, expectedRevision: 0 })), [409, 'method_not_executable:ml:minibatch_kmeans']);
  assert.deepEqual(code(await apply(v3.configId, { operation: 'apply', evaluationId: v3.evaluationId, expectedRevision: 0 })), [422, 'config_schema_not_supported_yet']);
  const conflict = await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 5 });
  assert.deepEqual(code(conflict), [409, 'active_set_revision_conflict']);
  assert.equal(conflict.body.currentRevision, 0);
  assert.equal(await revision(), 0);
  assert.deepEqual(code(await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0,
    acceptedRecommendation: { comparisonJobId: id(), modelKey: 'iforest', targetVersionId: a.configId } })), [400, 'accepted_recommendation_mismatch'], 'no stored comparison');

  // 4) apply: 추가 운영. 멱등키 재전송은 같은 응답, 다른 본문은 409.
  const key = { 'Idempotency-Key': 'apply-a-1' };
  const applied = await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0 }, admin, key);
  assert.equal(applied.status, 200, JSON.stringify(applied.body));
  assert.deepEqual([applied.body.revision, applied.body.previousRevision, applied.body.appliesFrom], [1, 0, 'next_decision']);
  assert.deepEqual(applied.body.models, [{ configVersionId: a.configId, modelKey: 'iforest', displayName: 'A', activatedAtRevision: 1 }]);
  assert.deepEqual(applied.body.changes, [{ type: 'activated', configVersionId: a.configId, modelKey: 'iforest' }], 'gate evidence stays in storage');
  assert.deepEqual((await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 0 }, admin, key)).body, applied.body);
  assert.deepEqual(code(await apply(b.configId, { operation: 'apply', evaluationId: b.evaluationId, expectedRevision: 0 }, admin, key)), [409, 'idempotency_payload_conflict']);
  const stored = (await pool.query('SELECT changes FROM ops_active_model_set_revisions WHERE revision=1')).rows[0].changes[0].gate;
  assert.equal(stored.evaluationId, a.evaluationId);
  assert.deepEqual(Object.keys(stored.ruleSet).sort(), ['contentHash', 'contentHashVersion', 'revision']);
  assert.deepEqual(stored.activeModelSet, { before: 0, after: 1 });
  assert.deepEqual(code(await apply(b.configId, { operation: 'apply', evaluationId: b.evaluationId, expectedRevision: 1 })), [422, 'active_model_key_duplicate:iforest']);
  const view = (await pool.query('SELECT active_model_set_revision AS r,active_models AS m FROM ops_operational_state')).rows[0];
  assert.deepEqual([Number(view.r), view.m.map((model) => model.configVersionId)], [1, [a.configId]], 'Detector reads the same set through the view');
  const listed = (await call(`/api/configurations/${a.configId}`, { token: admin })).body;
  assert.deepEqual([listed.active, listed.activeSince.revision], [true, 1]);

  // 5) replace: 같은 modelKey 운영 버전과 교환.
  assert.deepEqual(code(await apply(b.configId, { operation: 'replace', evaluationId: b.evaluationId, expectedRevision: 1, expectedActiveVersionId: b.configId })), [409, 'active_version_mismatch']);
  assert.deepEqual(code(await apply(a.configId, { operation: 'replace', evaluationId: a.evaluationId, expectedRevision: 1, expectedActiveVersionId: a.configId })), [409, 'active_model_set_unchanged']);
  const replaced = await apply(b.configId, { operation: 'replace', evaluationId: b.evaluationId, expectedRevision: 1, expectedActiveVersionId: a.configId });
  assert.equal(replaced.status, 200, JSON.stringify(replaced.body));
  assert.deepEqual(replaced.body.models.map((model) => [model.configVersionId, model.activatedAtRevision]), [[b.configId, 2]]);
  assert.deepEqual(replaced.body.changes, [{ type: 'replaced', configVersionId: b.configId, modelKey: 'iforest', previousConfigVersionId: a.configId }]);

  // 6) retire: 확인 필수, 운영 중인 버전만, 결과가 탐지 방법 0개면 거절.
  assert.deepEqual(code(await apply(b.configId, { operation: 'retire', expectedRevision: 2, confirmRetire: false })), [422, 'retire_confirmation_required']);
  assert.deepEqual(code(await apply(a.configId, { operation: 'retire', expectedRevision: 2, confirmRetire: true })), [422, 'not_active']);
  const rules = (await call('/api/rule-set', { token: admin })).body;
  const offAll = await call('/api/rule-set', { method: 'PUT', token: admin, body: { expectedRevision: rules.revision, confirmHardRuleDisable: true, softRules: [], relationRules: [], protocolRules: [],
    hardRules: rules.hardRules.map(({ id: ruleId, severity, editable }) => ({ id: ruleId, enabled: false, ...(editable.includes('severity') ? { severity } : {}) })) } });
  assert.equal(offAll.status, 200, 'rule save counts the active model set');
  assert.deepEqual(code(await apply(b.configId, { operation: 'retire', expectedRevision: 2, confirmRetire: true })), [422, 'detection_methods_empty']);
  const on = await call('/api/rule-set', { method: 'PUT', token: admin, body: { expectedRevision: offAll.body.revision, confirmHardRuleDisable: true,
    softRules: [], relationRules: [], protocolRules: [], hardRules: rules.hardRules.map(({ id: ruleId, enabled, severity, editable }) => ({ id: ruleId, enabled, ...(editable.includes('severity') ? { severity } : {}) })) } });
  assert.equal(on.status, 200, JSON.stringify(on.body));
  const retired = await apply(b.configId, { operation: 'retire', expectedRevision: 2, confirmRetire: true, note: '점검' });
  assert.equal(retired.status, 200, JSON.stringify(retired.body));
  assert.deepEqual([retired.body.revision, retired.body.models, retired.body.changes[0].type], [3, [], 'retired']);

  // 7) restore: 과거 집합을 새 리비전으로. 경로 :id는 쓰지 않는다. 빠지는 버전이 있으면 확인 필수.
  assert.deepEqual(code(await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: 3, targetRevision: 0 } })), [409, 'active_model_set_unchanged']);
  assert.deepEqual(code(await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: 3, targetRevision: 3 } })), [422, 'invalid_target_revision']);
  const restoredA = await apply(id(), { operation: 'restore', expectedRevision: 3, targetRevision: 1 });
  assert.equal(restoredA.status, 200, JSON.stringify(restoredA.body));
  assert.deepEqual(restoredA.body.models.map((model) => [model.configVersionId, model.activatedAtRevision]), [[a.configId, 4]]);
  assert.deepEqual(restoredA.body.changes, [{ type: 'restored', configVersionId: a.configId, modelKey: 'iforest' }]);
  assert.deepEqual(code(await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: 4, targetRevision: 2 } })), [422, 'retire_confirmation_required']);
  const restoredB = await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: 4, targetRevision: 2, confirmRetire: true } });
  assert.equal(restoredB.status, 200, JSON.stringify(restoredB.body));
  assert.deepEqual(restoredB.body.changes, [{ type: 'restored', configVersionId: b.configId, modelKey: 'iforest', previousConfigVersionId: a.configId }]);

  // 8) 이력: 최신순, 변경마다 표시 이름·평가 ID, 리비전마다 집합. 상세 404.
  const history = (await call('/api/active-models/history?limit=2', { token: user })).body;
  assert.deepEqual(history.items.map((item) => item.revision), [5, 4]);
  assert.equal(history.items[0].restoredFromRevision, 2);
  assert.deepEqual(history.items[0].changes[0], { type: 'restored', configVersionId: b.configId, modelKey: 'iforest', previousConfigVersionId: a.configId,
    displayName: 'B', previousDisplayName: 'A', evaluationId: b.evaluationId });
  assert.deepEqual(history.items[0].snapshot.models.map((model) => model.displayName), ['B']);
  assert.deepEqual(history.items[0].summary, { activated: 0, replaced: 0, retired: 0, restored: 1 });
  const next = (await call(`/api/active-models/history?cursor=${history.pagination.nextCursor}`, { token: user })).body;
  assert.deepEqual(next.items.map((item) => item.revision), [3, 2, 1, 0]);
  assert.equal(next.items[0].note, '점검');
  assert.deepEqual(code(await call('/api/active-models/history/99', { token: user })), [404, 'active_model_set_revision_not_found']);
  assert.deepEqual(code(await call('/api/active-models/history?limit=x', { token: user })), [400, 'invalid_limit']);
  assert.equal((await call('/api/active-models/history/5', { token: user })).body.revision, 5);
  await assert.rejects(pool.query('UPDATE ops_active_model_set_revisions SET note=NULL WHERE revision=5'), 'append-only');

  // 9) v1·v2 단일 구성 경로는 그대로(본문에 expectedActiveVersionId가 있는 apply·restore).
  assert.deepEqual(code(await apply(a.configId, { operation: 'apply', evaluationId: a.evaluationId, expectedRevision: 99, expectedActiveVersionId: null })), [409, 'active_config_changed']);

  // 10) 방법별 운영 상태: 지금 켜진 규칙·활성 모델마다 기간 안 v4 결과를 센다. 이전 모델 버전 결과·옛 판정은 따로.
  const state = await stateOf();
  const enabledHard = (await call('/api/rule-set', { token: admin })).body.hardRules.filter((rule) => rule.enabled).map((rule) => rule.id);
  const decide = async (methodResults, schemaVersion = 4, minutesAgo = 5) => {
    const { observation, execution } = await addProcess(pool, sourceId, { occurredAt: new Date().toISOString() });
    await pool.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_hash,status,rule_status,ml_status,evidence,decided_at,latency_status)
      VALUES($1,$2,$3,$4,$5,'complete','succeeded','succeeded',$6::jsonb,clock_timestamp()-make_interval(mins=>$7),'unavailable')`,
    [id(), execution, observation, b.configId, sha(observation), JSON.stringify(schemaVersion === 4 ? { schemaVersion, methodResults } : { schemaVersion, rules: [] }), minutesAgo]);
  };
  const ok = (methodId, extra = {}) => ({ methodId, category: methodId.split(':')[0], status: 'succeeded', anomalous: false, cause: null, elapsedMs: 2, ...extra });
  const ml = (version, extra = {}) => ok('ml:iforest', { modelVersionId: version, ...extra });
  await decide([ok(enabledHard[0], { anomalous: true }), ok(enabledHard[1], { status: 'not_ready', anomalous: null, cause: 'input_missing' }), ml(b.configId, { elapsedMs: 100 })]);
  await decide([ok(enabledHard[0]), ok(enabledHard[1], { status: 'failed', anomalous: null, cause: 'input_invalid' }), ml(b.configId, { status: 'not_ready', anomalous: null, cause: 'timeout', elapsedMs: 3001 })]);
  await decide([ok(enabledHard[0]), ok(enabledHard[1]), ml(a.configId)]);
  await decide([], 3); // 옛 형식(v3) 판정: methodResults가 없다
  await decide([ok(enabledHard[0])], 4, 120);
  const report = await call('/api/operations/method-status?period=1h', { token: user });
  assert.equal(report.status, 200, JSON.stringify(report.body));
  assert.deepEqual([report.body.period, report.body.firstRunWaitMs, report.body.decisionCount, report.body.legacyDecisionCount], ['1h', 3600000, 3, 1]);
  assert.deepEqual([report.body.ruleSetRevision, report.body.activeModelSetRevision], [state.ruleSet.revision, 5]);
  assert.deepEqual(report.body.failedWithoutDecision, { count: 0, byCode: [] });
  const of = (methodId) => report.body.methods.find((item) => item.methodId === methodId);
  assert.deepEqual(report.body.methods.map((item) => item.methodId), [...enabledHard, 'ml:iforest'], 'only methods in operation now');
  assert.deepEqual(['runs', 'succeeded', 'anomalous'].map((field) => of(enabledHard[0])[field]), [3, 3, 1]);
  assert.deepEqual(['runs', 'succeeded', 'notReady', 'failed'].map((field) => of(enabledHard[1])[field]), [3, 1, 1, 1]);
  assert.deepEqual(of(enabledHard[1]).causes, [{ cause: 'input_invalid', count: 1 }, { cause: 'input_missing', count: 1 }]);
  const model = of('ml:iforest');
  assert.deepEqual([model.modelVersionId, model.runs, model.notReady, model.timeouts], [b.configId, 2, 1, 1], 'the earlier version result is not counted for the current version');
  assert.equal(model.enabledSince, restoredB.body.changedAt);
  assert.ok(model.elapsedP95Ms > 100 && model.elapsedP95Ms <= 3001);
  assert.ok(of(enabledHard[0]).enabledSince, 'continuous on since time');
  const day = (await call('/api/operations/method-status?period=24h', { token: admin })).body;
  assert.equal(day.decisionCount, 4);
  assert.deepEqual(code(await call('/api/operations/method-status?period=7d', { token: admin })), [400, 'invalid_period']);
  assert.deepEqual(code(await call('/api/operations/method-status?x=1', { token: admin })), [400, 'unknown_operations_filter']);

  // 10b) restore는 스냅샷의 모든 버전(그대로 남는 버전 포함)에 gate를 다시 건다. 실행 불가 모델이 남는 복원은 거절, 집합 불변.
  const r6 = await pushActiveModels(pool, [{ configVersionId: research.configId, modelKey: 'minibatch_kmeans' }, { configVersionId: a.configId, modelKey: 'iforest' }]);
  const r7 = await pushActiveModels(pool, [{ configVersionId: research.configId, modelKey: 'minibatch_kmeans' }, { configVersionId: b.configId, modelKey: 'iforest' }]);
  assert.deepEqual(code(await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: r7, targetRevision: r6, confirmRetire: true } })),
    [409, 'method_not_executable:ml:minibatch_kmeans'], 'a kept version is gated too');
  assert.equal(await revision(), r7);
  const dropResearch = await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: r7, targetRevision: 5, confirmRetire: true } });
  assert.equal(dropResearch.status, 200, JSON.stringify(dropResearch.body));
  assert.deepEqual(dropResearch.body.models.map((model) => model.configVersionId), [b.configId], 'the kept version passes the gate again');
  assert.deepEqual(dropResearch.body.changes, [{ type: 'retired', configVersionId: research.configId, modelKey: 'minibatch_kmeans' }]);

  // 11) 저장소 실패는 503(빈 집합·성공 대체 없음).
  await stopChild();
  base = await startServer({ ...environment, DATABASE_URL: 'postgresql://postgres:x@127.0.0.1:1/qa_active_models_unreachable', PORT: String(await freePort()) });
  assert.deepEqual(code(await call('/api/operational-state', { token: admin })), [503, 'active_model_set_storage_unavailable']);
  assert.deepEqual(code(await call('/api/active-models/history', { token: admin })), [503, 'active_model_set_storage_unavailable']);
  assert.deepEqual(code(await call('/api/operations/method-status', { token: admin })), [503, 'operations_storage_unavailable']);
  assert.deepEqual(code(await call('/api/active-models/restore', { method: 'POST', token: admin, body: { operation: 'restore', expectedRevision: 5, targetRevision: 1, confirmRetire: true } })), [503, 'active_model_set_storage_unavailable']);
  console.log('active-models integration passed: auth, operational state, gate, apply, replace, retire, restore, conflict, idempotency, history, legacy path, method status, 503');
} finally {
  await stopChild();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
