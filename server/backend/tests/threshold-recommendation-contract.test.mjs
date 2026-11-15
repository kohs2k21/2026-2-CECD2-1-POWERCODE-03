// 임계값 추천 접수 시험: 실제 라우터를 가짜 pg 위에서 돌려 접수 형식·거절 코드·재사용을 고정한다. DB 없이 돈다(실제 제약은 jobs.integration.mjs, 017 뒤).
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { after, before, beforeEach, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import express from 'express';
import jwt from 'jsonwebtoken';
import pg from 'pg';
import { startFakeLlmHealth } from './fake-llm-health.mjs';

const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-thresholds-'));
// LLM 작업 접수는 생성 서비스 가용성을 먼저 본다(llm-recommendation-job.md '생성 서비스 가용성' 절). 이 시험은 켜진 상태의 가짜 /health를 쓴다.
const llmHealth = await startFakeLlmHealth('ready');
Object.assign(process.env, {
  LLM_HEALTH_URL: llmHealth.url,
  JWT_SECRET: 'unit-only-secret', DATABASE_URL: 'postgresql://unit.invalid/never_connected', USER_DATA_PATH: path.join(temporary, 'users.json'),
  SEED_ADMIN_EMAIL: 'thresholds-admin@example.test', SEED_ADMIN_PASSWORD: 'unit-only-password',
  SEED_USER_EMAIL: 'thresholds-user@example.test', SEED_USER_PASSWORD: 'unit-only-password',
});
const { default: jobRouter } = await import('../dist/routes/jobRoutes.js');
const { UserRepository } = await import('../dist/repository/userRepository.js');
const { THRESHOLD_EVIDENCE_VERSION, THRESHOLD_MODEL_VERSION, THRESHOLD_PROMPT_VERSION, ThresholdRequestError, fixThresholdTargets, parseThresholdRequest } =
  await import('../dist/routes/thresholdRecommendationContract.js');

const contracts = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../contracts');
const examples = JSON.parse(await readFile(path.join(contracts, 'llm-recommendation-job.example.json'), 'utf8'));
const sha256 = (text) => crypto.createHash('sha256').update(text).digest('hex');
const SOURCE = '11111111-1111-4111-8111-111111111111';
const SNAPSHOT = '22222222-2222-4222-8222-222222222222';
const HASH = 'a'.repeat(64);
const rule = (field, threshold, suffix, enabled = true) => ({
  id: `soft:${field}-${suffix}`, name: `${field} rule`, field, operator: 'gt', threshold, unit: 'ms', severity: 'warning', enabled, createdAtRevision: 1, updatedAtRevision: 1,
});
const ELAPSED = rule('elapsed_ms', 5000, '3f9a2c1d');
const RATIO = rule('error_ratio', 0.01, '5d2e0b19');
const OFF = rule('retry_count', 3, '91ab77e0', false);

const body = (changes = {}) => ({
  kind: 'recommend_thresholds', sourceId: SOURCE, ruleSetRevision: 13, scope: { softRuleIds: null },
  data: { snapshotId: null, from: '2026-08-05T00:00:00.000Z', until: '2026-11-05T00:00:00.000Z', timeBasis: 'persisted_at' },
  mode: 'data_p95_with_llm_rationale', ...changes,
});
const period = (changes) => ({ data: { ...body().data, ...changes } });
/** 저장되는 대상 선택: 후보를 보내지 않으면 `candidateIds: null`(10차). */
const storedScope = (scope) => ({ candidateIds: null, ...scope });
const softTarget = ({ id, field, threshold }, scope = null) => ({ kind: 'soft_rule', id, field, scope, threshold });

// (10차) 처리 시간 후보: Trainer recommend_relations 성공 작업 결과(relation-candidates-experimental-2)의 thresholdCandidates.
const RELATION_JOB = '44444444-4444-4444-8444-444444444444';
const OTHER_JOB = '55555555-5555-4555-8555-555555555555';
const thresholdCandidate = (processId, { adapterType, status = 'candidate', threshold = '704' } = {}) => ({
  field: 'elapsed_ms', status, reason: null, scope: { processId, ...(adapterType ? { adapterType } : {}) }, display: { interfaceIds: ['IF-A'] },
  operator: threshold === null ? null : 'gt', threshold, displayThreshold: threshold, unit: 'ms', source: 'data_p95',
  evidence: { rows: 40, referenceRows: 40, n: 40, endTimeMissingReferenceRows: 0, unavailableReferenceRows: 0, p95: 704, min: 1, max: 900, quantileMethod: 'linear' },
});
const relationResult = (thresholdCandidates, snapshotId = SNAPSHOT) => ({
  relationContractVersion: 'relation-candidates-experimental-2', snapshotId, sourceId: SOURCE, candidates: [], thresholdCandidates,
  graph: { graph: {}, nodes: [], edges: [] }, data: { interfaceTransactions: {} }, parameters: {},
});
const CANDIDATES = [thresholdCandidate('P1', { adapterType: 'HTTP' }), thresholdCandidate('P2'), thresholdCandidate('P3', { status: 'insufficient_evidence', threshold: null })];
const candidateBody = (candidateIds, changes = {}) => body({
  scope: { softRuleIds: [], candidateIds }, data: { snapshotId: SNAPSHOT, from: null, until: null, timeBasis: null }, ...changes,
});

/** 라우터가 보내는 문장만 답하는 가짜 저장소. 모르는 문장은 오류라 503으로 드러난다. */
const database = {};
const reset = () => Object.assign(database, {
  sources: new Set([SOURCE]), revision: { revision: '13', contentHash: HASH, contentHashVersion: 2, snapshot: { hardRules: [], softRules: [ELAPSED, RATIO, OFF] } },
  snapshots: new Set(), jobs: [], queued: [], statements: [],
  relationJobs: new Map([[RELATION_JOB, relationResult(CANDIDATES)]]),
});
const rows = (list) => ({ rows: list, rowCount: list.length });
async function query(text, params = []) {
  database.statements.push(text);
  if (/^(BEGIN|COMMIT|ROLLBACK)$/.test(text) || text.includes('pg_advisory_xact_lock')) return rows([]);
  if (text.includes('FROM ops_sources')) return rows(database.sources.has(params[0]) ? [{ schema_version: 'v1', field_catalog: {} }] : []);
  if (text.includes('FROM ops_rule_set_revisions')) {
    if (!database.revision) return rows([]);
    return rows([{ note: null, changes: [], summary: {}, restoredFromRevision: null, actorId: 'system', savedAt: '2026-11-01T00:00:00.000000Z', ...database.revision }]);
  }
  if (text.includes('FROM data_snapshots')) return rows(database.snapshots.has(params[0]) ? [{}] : []);
  if (text.includes("kind::text = 'recommend_relations'") && text.includes('WHERE id = $1')) {
    const result = database.relationJobs.get(params[0]);
    return rows(result === undefined ? [] : [{ id: params[0], status: 'succeeded', errorCode: null, createdAt: '2026-11-01T00:00:00.000000Z',
      finishedAt: '2026-11-01T00:00:00.000000Z', inputSpec: { snapshotId: SNAPSHOT }, result }]);
  }
  if (text.includes('FROM ml_feature_sets')) return rows([{}]);
  if (text.includes('INSERT INTO run_jobs')) {
    const [id, kind, actor, role, key, requestHash, configVersion, snapshot, inputSpec, version, queue, producerKind, producerKey, llmSpec, decision] = params;
    if (database.jobs.some((job) => job.actor === actor && job.kind === kind && job.key === key)) return rows([]); // ON CONFLICT DO NOTHING
    const job = { id, kind, actor, role, key, requestHash, configVersion, snapshot, inputSpec, version, queue, producerKind, producerKey, llmSpec, decision, status: 'queued' };
    database.jobs.push(job);
    return rows([{ id, status: 'queued', kind, inputSpec: JSON.parse(inputSpec) }]);
  }
  if (text.includes('INSERT INTO powercode_queue.')) {
    database.queued.push({ table: /powercode_queue\.(\w+)/.exec(text)[1], payload: JSON.parse(params[1]) });
    return rows([]);
  }
  if (text.includes("llm_spec->>'reuse_key'")) {
    const found = database.jobs.filter((job) => job.actor === params[0] && JSON.parse(job.llmSpec).reuse_key === params[1] && job.status !== 'failed');
    return rows(found.map(({ id, status, kind, inputSpec }) => ({ id, status, kind, inputSpec: JSON.parse(inputSpec) })));
  }
  if (/idempotency_key\s*=\s*\$3/.test(text)) {
    const found = database.jobs.filter((job) => job.actor === params[0] && job.kind === params[1] && job.key === params[2]);
    return rows(found.map(({ id, status, kind, inputSpec, requestHash }) => ({ id, status, kind, inputSpec: JSON.parse(inputSpec), request_hash: requestHash })));
  }
  if (text.includes('result_summary AS "resultSummary"') && text.includes('WHERE id = $1')) {
    const found = database.jobs.filter((job) => job.id === params[0]);
    return rows(found.map(({ id, kind, status, errorCode, resultSummary }) => ({ id, kind, status, errorCode, resultSummary })));
  }
  if (text.includes('FROM run_job_attempts')) return rows([]); // 작업 상세 시도 기록(operations-ui.md)
  throw new Error(`unexpected statement: ${text}`);
}

let server;
let base;
let adminToken;
let userToken;
let adminId;
before(async () => {
  pg.Pool.prototype.connect = async () => ({ query, release() {} });
  pg.Pool.prototype.query = query;
  const users = UserRepository.getInstance();
  const token = async (email) => {
    const account = await users.getByEmail(email);
    return { id: account.id, token: jwt.sign({ id: account.id, email: account.email, userType: account.userType }, process.env.JWT_SECRET) };
  };
  ({ id: adminId, token: adminToken } = await token(process.env.SEED_ADMIN_EMAIL));
  ({ token: userToken } = await token(process.env.SEED_USER_EMAIL));
  const app = express();
  app.use(express.json());
  app.use('/api/jobs', jobRouter);
  await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});
beforeEach(reset);
after(async () => {
  await new Promise((resolve) => server.close(resolve));
  await llmHealth.close();
  await rm(temporary, { recursive: true, force: true });
});

async function post(payload, { token = adminToken, key } = {}) {
  const response = await fetch(`${base}/api/jobs`, {
    method: 'POST', body: JSON.stringify(payload),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(key ? { 'Idempotency-Key': key } : {}) },
  });
  return { status: response.status, body: await response.json() };
}
const rejected = async (payload, status, code, options) => {
  assert.deepEqual(await post(payload, options), { status, body: { code } }, JSON.stringify(payload));
  assert.equal(database.jobs.length, 0, 'a rejected request leaves no job');
  assert.equal(database.queued.length, 0, 'a rejected request leaves no queue row');
};
const lastSpec = () => JSON.parse(database.jobs.at(-1).llmSpec);

test('accepted request stores the fixed input, the six llm_spec keys (Rule v3 path) and one queue row', async () => {
  const created = await post(body(), { key: 'run-1' });
  assert.equal(created.status, 201);
  assert.deepEqual(Object.keys(created.body).sort(), ['id', 'inputSpec', 'kind', 'status']);
  assert.equal(created.body.kind, 'recommend_thresholds');
  assert.equal(created.body.status, 'queued');
  const [job] = database.jobs;
  assert.deepEqual(JSON.parse(job.inputSpec), {
    ...body(), scope: storedScope(body().scope), ruleSetContentHash: HASH, ruleSetContentHashVersion: 2,
    targets: [softTarget(ELAPSED), softTarget(RATIO)],
  }, 'targets are the enabled rules in the new shape: kind, id, field, scope and threshold');
  assert.deepEqual([job.configVersion, job.snapshot, job.decision], [null, null, null]);
  assert.deepEqual([job.version, job.queue, job.producerKind, job.producerKey, job.role, job.key], ['v03', 'llm_recommendation', 'user', adminId, 'admin', 'run-1']);
  assert.deepEqual(database.queued.map(({ table }) => table), ['llm_recommendation_jobs']);
  assert.deepEqual(Object.keys(database.queued[0].payload).sort(), ['enqueuedAt', 'jobId', 'schemaVersion']);
  assert.equal(database.queued[0].payload.jobId, job.id);
  assert.equal(job.requestHash, sha256(job.inputSpec), 'the request hash covers the fixed input');
  assert.deepEqual(JSON.parse(job.llmSpec), {
    evidence_version: 'soft-rule-threshold-v3', input_evidence_hash: job.requestHash, model_version: 'rule_v3', prompt_version: 'feature-rule-spec-rule-v3-ko',
    output_schema_version: 'threshold-recommendation-v1',
    reuse_key: sha256(JSON.stringify([adminId, JSON.parse(job.inputSpec), 'run-1', 'feature-rule-spec-rule-v3-ko'])),
  });
  assert.ok(database.statements.includes('COMMIT'));
});

test('the shared examples are what the API stores (server/worker/tests reads the same file)', async () => {
  for (const example of examples.examples) {
    reset();
    database.revision = {
      revision: String(example.currentRuleSet.revision), contentHash: example.currentRuleSet.contentHash,
      contentHashVersion: example.currentRuleSet.contentHashVersion, snapshot: { hardRules: [], softRules: example.currentRuleSet.softRules },
    };
    database.snapshots.add(example.request.data.snapshotId);
    // 후보 예시는 후보 작업 결과(고정값)를 함께 싣는다.
    if (example.candidateJob) database.relationJobs = new Map([[example.candidateJob.id, example.candidateJob.resultSummary]]);
    assert.equal(adminId, examples.actorId, 'the first seeded account owns the example');
    assert.equal((await post(example.request, { key: examples.idempotencyKey })).status, 201, example.name);
    const [job] = database.jobs;
    assert.deepEqual(JSON.parse(job.inputSpec), example.input_spec, example.name);
    assert.deepEqual(JSON.parse(job.llmSpec), example.llm_spec, example.name);
  }
});

test('a subset scope keeps only its enabled rules and off or unknown ids drop out', async () => {
  const scope = { softRuleIds: [RATIO.id, OFF.id, 'soft:total_count-00000000'] };
  assert.equal((await post(body({ scope }))).status, 201);
  const input = JSON.parse(database.jobs[0].inputSpec);
  assert.deepEqual(input.targets, [softTarget(RATIO)]);
  assert.deepEqual(input.scope, storedScope(scope));
});

test('a soft rule scope is written as its judging keys only, and a scoped rule is its own group', async () => {
  const scoped = { ...rule('elapsed_ms', 7000, 'aaaaaaaa'), scope: { processId: 'P9', adapterType: 'HTTP', interfaceId: 'IF-A' } };
  database.revision = { ...database.revision, contentHashVersion: 3, snapshot: { hardRules: [], softRules: [ELAPSED, scoped] } };
  assert.equal((await post(body())).status, 201);
  assert.deepEqual(JSON.parse(database.jobs[0].inputSpec).targets,
    [softTarget(ELAPSED), softTarget(scoped, { processId: 'P9', adapterType: 'HTTP' })], 'interfaceId is display only');
});

test('candidates: accepted with no soft rule, fixed after the soft rules in request order, value left to the worker', async () => {
  database.snapshots.add(SNAPSHOT);
  database.revision = { ...database.revision, snapshot: { hardRules: [], softRules: [OFF] } };
  const ids = [`${RELATION_JOB}.t1`, `${RELATION_JOB}.t0`];
  const created = await post(candidateBody(ids), { key: 'run-c' });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  const [job] = database.jobs;
  const input = JSON.parse(job.inputSpec);
  assert.deepEqual(input.scope, { softRuleIds: [], candidateIds: ids });
  assert.deepEqual(input.data, { snapshotId: SNAPSHOT, from: null, until: null, timeBasis: null });
  assert.deepEqual(input.targets, [
    { kind: 'candidate', id: ids[0], field: 'elapsed_ms', scope: { processId: 'P2' }, threshold: null },
    { kind: 'candidate', id: ids[1], field: 'elapsed_ms', scope: { processId: 'P1', adapterType: 'HTTP' }, threshold: null },
  ]);
  assert.equal(JSON.parse(job.llmSpec).evidence_version, 'soft-rule-threshold-v3');
  assert.equal(job.requestHash, sha256(job.inputSpec));
  // 켜진 소프트룰과 함께: 소프트룰(저장 순서) → 후보.
  reset();
  database.snapshots.add(SNAPSHOT);
  assert.equal((await post(candidateBody([ids[1]], { scope: { softRuleIds: null, candidateIds: [ids[1]] } }))).status, 201);
  assert.deepEqual(JSON.parse(database.jobs[0].inputSpec).targets.map(({ kind, id }) => [kind, id]),
    [['soft_rule', ELAPSED.id], ['soft_rule', RATIO.id], ['candidate', ids[1]]]);
});

test('candidate id shape and count use the job request code', async () => {
  database.snapshots.add(SNAPSHOT);
  const t = (index) => `${RELATION_JOB}.t${index}`;
  for (const candidateIds of [[], 'all', [1], [`${RELATION_JOB}.r0`], ['x.t0'], [`${RELATION_JOB}.t01`], [t(0), t(0)], [t(0), t(0).toUpperCase()],
    Array.from({ length: 33 }, (_, index) => t(index))]) {
    await rejected(candidateBody(candidateIds), 400, 'invalid_job_request');
  }
  await rejected(candidateBody([t(0)], { scope: { softRuleIds: [], candidateIds: [t(0)], extra: 1 } }), 400, 'invalid_job_request');
});

test('candidate lookup: one succeeded job, eligible candidates, the same snapshot', async () => {
  database.snapshots.add(SNAPSHOT);
  await rejected(candidateBody([`${RELATION_JOB}.t0`, `${OTHER_JOB}.t0`]), 422, 'rule_candidate_job_mixed');
  await rejected(candidateBody([`${OTHER_JOB}.t0`]), 404, 'rule_candidate_not_found');
  await rejected(candidateBody([`${RELATION_JOB}.t3`]), 404, 'rule_candidate_not_found');
  await rejected(candidateBody([`${RELATION_JOB}.t0`, `${RELATION_JOB}.t2`]), 422, `rule_candidate_not_eligible:${RELATION_JOB}.t2`);
  // 후보 결과와 다른 snapshot(그 snapshot은 준비됨), 기간만 보낸 요청 모두 409.
  const other = '66666666-6666-4666-8666-666666666666';
  database.snapshots.add(other);
  await rejected(candidateBody([`${RELATION_JOB}.t0`], { data: { snapshotId: other } }), 409, 'rule_candidate_snapshot_mismatch');
  await rejected(candidateBody([`${RELATION_JOB}.t0`], { data: body().data }), 409, 'rule_candidate_snapshot_mismatch');
  // 결과 머리가 다르면 목록과 같은 503 코드.
  database.relationJobs.set(RELATION_JOB, { ...relationResult(CANDIDATES), relationContractVersion: 'relation-candidates-experimental-1' });
  await rejected(candidateBody([`${RELATION_JOB}.t0`]), 503, 'rule_candidate_contract_unsupported');
  database.relationJobs.set(RELATION_JOB, relationResult([{ ...CANDIDATES[0], threshold: '7e2' }]));
  await rejected(candidateBody([`${RELATION_JOB}.t0`]), 503, 'rule_candidate_result_invalid');
  // 리비전 충돌은 후보 조회보다 먼저.
  await rejected(candidateBody([`${OTHER_JOB}.t0`], { ruleSetRevision: 12 }), 409, 'rule_set_revision_conflict');
  // 소프트룰도 후보도 없으면 현행 코드.
  await rejected(body({ scope: { softRuleIds: [], candidateIds: null } }), 400, 'soft_rules_empty');
});

test('groups: a candidate with the same field and scope as a soft rule shares its group; more than 32 groups is refused', async () => {
  database.snapshots.add(SNAPSHOT);
  const same = { ...rule('elapsed_ms', 9000, 'bbbbbbbb'), scope: { processId: 'P2' } };
  const many = Array.from({ length: 31 }, (_, index) => ({ ...rule('elapsed_ms', 9000, String(index).padStart(8, '0')), scope: { processId: `Q${index}` } }));
  database.revision = { ...database.revision, contentHashVersion: 3, snapshot: { hardRules: [], softRules: [same, ...many] } };
  // 32 묶음 = 31(Q) + 1(P2, 소프트룰·후보 함께) → 접수.
  assert.equal((await post(candidateBody([`${RELATION_JOB}.t1`], { scope: { softRuleIds: null, candidateIds: [`${RELATION_JOB}.t1`] } }))).status, 201);
  reset();
  database.snapshots.add(SNAPSHOT);
  database.revision = { ...database.revision, contentHashVersion: 3, snapshot: { hardRules: [], softRules: [same, ...many] } };
  await rejected(candidateBody([`${RELATION_JOB}.t0`], { scope: { softRuleIds: null, candidateIds: [`${RELATION_JOB}.t0`] } }), 400, 'soft_rule_limit_exceeded');
});

test('rule_v2 (rollback path) refuses candidates and scoped targets but keeps plain soft rules', () => {
  const current = { revision: 13, contentHash: HASH, contentHashVersion: 3, softRules: [ELAPSED] };
  const request = parseThresholdRequest(body(), SOURCE);
  assert.deepEqual(fixThresholdTargets(request, current, [], 'rule_v2').targets, [softTarget(ELAPSED)]);
  const code = (fn) => { try { fn(); } catch (error) { return error instanceof ThresholdRequestError ? error.code : String(error); } return 'accepted'; };
  assert.equal(code(() => fixThresholdTargets(request, current, [{ candidateId: `${RELATION_JOB}.t0`, field: 'elapsed_ms', scope: { processId: 'P1' } }], 'rule_v2')),
    'recommendation_targets_require_rule_v3');
  const scoped = { ...current, softRules: [{ ...ELAPSED, scope: { processId: 'P1' } }] };
  assert.equal(code(() => fixThresholdTargets(request, scoped, [], 'rule_v2')), 'recommendation_targets_require_rule_v3');
  assert.equal(code(() => fixThresholdTargets(request, scoped, [], 'rule_v3')), 'accepted');
});

test('stored hash and hash version pass through unchanged, including version 1 rows', async () => {
  database.revision = { ...database.revision, contentHash: 'b'.repeat(64), contentHashVersion: 1 };
  assert.equal((await post(body())).status, 201);
  const input = JSON.parse(database.jobs[0].inputSpec);
  assert.deepEqual([input.ruleSetContentHash, input.ruleSetContentHashVersion], ['b'.repeat(64), 1]);
});

test('admin only, on both checks', async () => {
  await rejected(body(), 403, 'admin_required', { token: userToken });
  await rejected({ kind: 'recommend_thresholds' }, 403, 'admin_required', { token: userToken });
});

test('shape violations use the job request code', async () => {
  const cases = [
    body({ extra: 1 }), body({ from: '2026-08-05T00:00:00Z' }), body({ featureSetId: SOURCE }), body({ snapshotId: SNAPSHOT }), body({ sourceId: 'x' }),
    body({ ruleSetRevision: undefined }), body({ ruleSetRevision: -1 }), body({ ruleSetRevision: 1.5 }), body({ ruleSetRevision: '13' }), body({ ruleSetRevision: null }),
    body({ scope: undefined }), body({ scope: null }), body({ scope: {} }), body({ scope: [] }), body({ scope: { softRuleIds: null, extra: 1 } }),
    body({ scope: { softRuleIds: 'all' } }), body({ scope: { softRuleIds: [1] } }), body({ scope: { softRuleIds: ['bad id'] } }),
    body({ scope: { softRuleIds: [ELAPSED.id, ELAPSED.id] } }),
    body({ scope: { softRuleIds: Array.from({ length: 33 }, (_, index) => `soft:elapsed_ms-${String(index).padStart(8, '0')}`) } }),
    body({ data: undefined }), body({ data: null }), body({ data: [] }), body(period({ extra: 1 })), body(period({ timeBasis: 'other' })), body(period({ timeBasis: 1 })),
    body({ mode: 'other' }), body({ mode: 1 }),
  ];
  for (const payload of cases) await rejected(payload, 400, 'invalid_job_request');
});

test('period and snapshot violations use the existing codes', async () => {
  const until = '2026-11-05T00:00:00.000Z';
  for (const data of [
    { from: null, until: null }, { from: '2026-08-05T00:00:00.000Z', until: null }, { from: null, until }, { from: until, until }, { from: '2026-11-06T00:00:00.000Z', until },
    { from: '2025-11-01T00:00:00.000Z', until }, { from: '2026-08-05', until }, { from: '2026-08-05T09:00:00+09:00', until }, { from: 1, until }, { from: '2026-02-30T00:00:00Z', until },
  ]) await rejected(body(period(data)), 400, 'invalid_time_range');
  await rejected(body({ data: {} }), 400, 'invalid_time_range');
  await rejected(body(period({ snapshotId: SNAPSHOT, from: '2026-08-05T00:00:00.000Z', until: null })), 400, 'invalid_time_range');
  await rejected(body(period({ snapshotId: 'x' })), 400, 'invalid_snapshot_reference');
  await rejected(body(period({ snapshotId: 1 })), 400, 'invalid_snapshot_reference');
  await rejected(body(period({ snapshotId: SNAPSHOT })), 409, 'snapshot_conditions_mismatch');
});

test('snapshot requests need no period and keep the snapshot off input_snapshot_id', async () => {
  database.snapshots.add(SNAPSHOT);
  assert.equal((await post(body({ data: { snapshotId: SNAPSHOT } }))).status, 201);
  const [job] = database.jobs;
  assert.deepEqual(JSON.parse(job.inputSpec).data, { snapshotId: SNAPSHOT, from: null, until: null, timeBasis: null });
  assert.equal(job.snapshot, null);
});

test('reserved mode and unsupported time basis end at acceptance with the worker codes', async () => {
  await rejected(body({ mode: 'ai_proposal' }), 422, 'recommendation_mode_not_available');
  await rejected(body(period({ timeBasis: 'esb_occurred_at' })), 422, 'recommendation_time_basis_unsupported');
});

test('a missing mode and time basis take the contract default', async () => {
  const { mode, ...withoutMode } = body();
  const { timeBasis, ...data } = withoutMode.data;
  assert.equal((await post({ ...withoutMode, data })).status, 201);
  const input = JSON.parse(database.jobs[0].inputSpec);
  assert.deepEqual([input.mode, input.data.timeBasis], [mode, timeBasis]);
});

test('rule set state decides acceptance', async () => {
  await rejected(body({ ruleSetRevision: 12 }), 409, 'rule_set_revision_conflict');
  await rejected(body({ ruleSetRevision: 99 }), 409, 'rule_set_revision_conflict');
  await rejected(body({ scope: { softRuleIds: [] } }), 400, 'soft_rules_empty');
  await rejected(body({ scope: { softRuleIds: [OFF.id] } }), 400, 'soft_rules_empty');
  await rejected(body({ scope: { softRuleIds: ['soft:total_count-00000000'] } }), 400, 'soft_rules_empty');
  database.revision = { ...database.revision, snapshot: { hardRules: [], softRules: [OFF] } };
  await rejected(body(), 400, 'soft_rules_empty');
  database.revision = { ...database.revision, snapshot: { hardRules: [], softRules: [rule('removed_field', 1, '00000000')] } };
  await rejected(body(), 422, 'soft_rule_field_unknown:removed_field');
  database.revision = null;
  await rejected(body(), 503, 'job_storage_unavailable');
  database.revision = { revision: '13', contentHash: HASH, contentHashVersion: 2, snapshot: { hardRules: [], softRules: [ELAPSED] } };
  database.sources.clear();
  await rejected(body(), 404, 'source_not_found');
});

test('the same key returns the same job; a new key is a new run', async () => {
  const first = await post(body(), { key: 'run-1' });
  const replay = await post(body(), { key: 'run-1' });
  assert.deepEqual([replay.status, replay.body.id], [200, first.body.id]);
  assert.equal(database.jobs.length, 1);
  assert.equal(database.queued.length, 1);
  const again = await post(body(), { key: 'run-2' });
  assert.equal(again.status, 201);
  assert.notEqual(again.body.id, first.body.id);
  assert.notEqual(JSON.parse(database.jobs[1].llmSpec).reuse_key, JSON.parse(database.jobs[0].llmSpec).reuse_key);
});

test('a failed job is not reused as a result: the same key shows the failure, a new key runs again', async () => {
  const first = await post(body(), { key: 'run-1' });
  database.jobs[0].status = 'failed';
  const replay = await post(body(), { key: 'run-1' });
  assert.deepEqual([replay.status, replay.body.id, replay.body.status], [200, first.body.id, 'failed']);
  assert.equal(database.queued.length, 1, 'no second queue row');
  assert.equal((await post(body(), { key: 'run-2' })).status, 201);
  assert.equal(database.jobs.length, 2);
});

test('every part of the fixed input changes the reuse key and the request hash', async () => {
  const keyed = async (setup, payload = body()) => {
    reset();
    setup();
    assert.equal((await post(payload, { key: 'run-1' })).status, 201);
    return [lastSpec().reuse_key, lastSpec().input_evidence_hash];
  };
  const revision = (changes) => () => { database.revision = { ...database.revision, ...changes }; };
  const rules = (softRules) => revision({ snapshot: { hardRules: [], softRules } });
  const variants = [
    await keyed(() => {}),
    await keyed(revision({ revision: '14' }), body({ ruleSetRevision: 14 })),
    await keyed(revision({ contentHash: 'b'.repeat(64) })),
    await keyed(revision({ contentHashVersion: 1 })),
    await keyed(() => {}, body(period({ from: '2026-08-06T00:00:00.000Z' }))),
    await keyed(() => {}, body(period({ until: '2026-11-04T00:00:00.000Z' }))),
    await keyed(rules([ELAPSED])),
    await keyed(rules([{ ...ELAPSED, threshold: 6000 }, RATIO])),
    await keyed(() => {}, body({ scope: { softRuleIds: [ELAPSED.id, RATIO.id] } })),
  ];
  assert.equal(new Set(variants.map(([key]) => key)).size, variants.length, 'reuse keys');
  assert.equal(new Set(variants.map(([, hash]) => hash)).size, variants.length, 'request hashes');
});

test('one key cannot carry a different request', async () => {
  assert.equal((await post(body(), { key: 'run-1' })).status, 201);
  assert.deepEqual(await post(body(period({ from: '2026-08-06T00:00:00.000Z' })), { key: 'run-1' }), { status: 409, body: { code: 'idempotency_payload_conflict' } });
  database.revision = { ...database.revision, snapshot: { hardRules: [], softRules: [ELAPSED] } };
  assert.deepEqual(await post(body(), { key: 'run-1' }), { status: 409, body: { code: 'idempotency_payload_conflict' } }, 'same body, different fixed targets');
  assert.equal(database.jobs.length, 1);
});

test('a failed job keeps its result summary on GET /api/jobs/:id', async () => {
  const created = await post(body());
  const resultSummary = { recommendationContractVersion: 'threshold-recommendation-v1', items: [{ field: 'elapsed_ms', status: 'rejected', rejectedReason: 'factual_review_failed' }] };
  Object.assign(database.jobs[0], { status: 'failed', errorCode: 'recommendation_all_rejected', resultSummary });
  const response = await fetch(`${base}/api/jobs/${created.body.id}`, { headers: { Authorization: `Bearer ${adminToken}` } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { id: created.body.id, kind: 'recommend_thresholds', status: 'failed', errorCode: 'recommendation_all_rejected', resultSummary, attempts: [] });
});

test('abolished kinds (recommend_rule, recommend_feature) are refused with 422 and leave no job', async () => {
  const featureSetId = '33333333-3333-4333-8333-333333333333';
  const { from, until } = body().data;
  for (const kind of ['recommend_rule', 'recommend_feature']) {
    await rejected({ kind, sourceId: SOURCE, from, until, featureSetId }, 422, 'job_kind_abolished', { key: `old-${kind}` });
    await rejected({ kind, sourceId: SOURCE, from, until, featureSetId, ruleSetRevision: 13 }, 422, 'job_kind_abolished');
    await rejected({ kind, sourceId: SOURCE, from, until, featureSetId }, 422, 'job_kind_abolished', { token: userToken });
  }
});

test('the contract names the value the API records', async () => {
  const contract = await readFile(path.join(contracts, 'llm-recommendation-job.md'), 'utf8');
  assert.ok(contract.includes(`prompt_version: '${THRESHOLD_PROMPT_VERSION}'`));
  assert.ok(contract.includes(`model_version: '${THRESHOLD_MODEL_VERSION}'`) && contract.includes(`evidence_version: '${THRESHOLD_EVIDENCE_VERSION}'`));
  // 새 대상 모양을 실행하는 유일한 조합이다(llm-recommendation-job.md 10차 llm_spec 행).
  assert.deepEqual([THRESHOLD_MODEL_VERSION, THRESHOLD_EVIDENCE_VERSION], ['rule_v3', 'soft-rule-threshold-v3']);
  assert.ok(examples.examples.every((example) => example.llm_spec.prompt_version === THRESHOLD_PROMPT_VERSION &&
    example.llm_spec.model_version === THRESHOLD_MODEL_VERSION && example.llm_spec.evidence_version === THRESHOLD_EVIDENCE_VERSION));
});
