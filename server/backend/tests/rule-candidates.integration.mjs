// 규칙 추천(contracts/rule-candidates-ui.md) 통합 시험. 로컬 새 QA DB(qa_analysis*)에 001~020(+ 선택 021)이 적용돼 있어야 한다.
// DB 상태를 먼저 본다: 021 전(enum 없음) · enum만 있음(CHECK에 값 없음) · 021 적용(enum 있음·CHECK에 값 있음).
// 확인: 관리자 전용(user 403·토큰 없음 401), 결과 없음 = result null(enum 값이 없어도 503이 아님), 스냅샷 409,
// 021 전이면 작업 요청 DB 준비 전 503 두 단계(enum 없음 → relation_job_kind_unavailable, enum만 있고 v03 CHECK 그대로 → relation_job_contract_unavailable)
// 뒤 CHECK를 시험 DB에서만 지운 "migration 뒤" 모의, 021 적용 DB면 503 단계를 건너뛰고 CHECK를 지우지 않은 채 실제 021 CHECK·트리거로:
// 201 + training 큐 메시지 + 같은 키 재요청 200(스냅샷이 바뀐 뒤에도) + 다른 본문 409,
// 결과 읽기(v03 성공 작업: 시도 행 + 성공 전환, 목록·상세·반례 → 거래별 판정 id, 판정이 많은 거래가 다른 거래를 밀어내지 않음),
// powercode_api 역할 SELECT만으로 읽기 경로 동작. 실행 전 `npm run build`.
// 주의: 021 전 경로의 enum 값 추가·CHECK 삭제는 이 시험 DB에만 한다(migration 아님, 되돌릴 수 없음 → 새 DB를 쓴다).
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_analysis[a-z_0-9]*$/.test(parsed.pathname) || parsed.search) {
  throw new Error('Only a local qa_analysis database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, '..');
const { readCandidateList, readCandidateDetail, RELATION_CONTRACT_VERSION } = await import('../dist/ruleset/ruleCandidates.js');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-rule-candidates-'));
const pool = new pg.Pool({ connectionString: url });
const api = new pg.Pool({ connectionString: url, options: '-c role=powercode_api' });
let child;

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
async function request(base, route, token, { method = 'GET', body, headers = {} } = {}) {
  const response = await fetch(`${base}${route}`, { method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...headers }, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text();
  let parsedBody = null;
  try { parsedBody = JSON.parse(text); } catch { /* not JSON */ }
  return { status: response.status, body: parsedBody };
}

/** 원본·구성·스냅샷·반례 거래의 PROCESS 판정(시험 전용 합성 값). */
async function seed(client) {
  const sourceId = id(), configId = id(), snapshotId = id();
  await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,'rule candidates qa','switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["T","P","M","B"]}',clock_timestamp())`,
  [sourceId, `rule-candidates-${sourceId}`]);
  await client.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
    VALUES($1,$2,'{}','{}',$3,'qa',clock_timestamp())`, [configId, sourceId, sha(configId)]);
  await client.query(`INSERT INTO data_snapshots(id,source_id,status,observation_cutoff_at,selection_spec,split_spec,schema_version,created_by_actor_id,created_at,ready_at)
    VALUES($1,$2,'ready',clock_timestamp(),'{"from":"2026-10-01T00:00:00Z","until":"2026-11-01T00:00:00Z","timeBasis":"persisted_at"}','{}','process-v1','qa',clock_timestamp(),clock_timestamp())`,
  [snapshotId, sourceId]);
  const decision = async (tx, processId, minutesAgo) => {
    const execution = id(), obs = id(), decisionId = id();
    await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
      VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [execution, sourceId, `exec-${execution}`]);
    await client.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,api_received_at,persisted_at,clock_status,
        parsed_times,allowed_values,quality_flags,missing_fields) VALUES($1,$2,'P',$3,1,$4,$5,clock_timestamp(),clock_timestamp(),'unknown','{}','{}','[]','[]')`,
    [obs, sourceId, execution, `P:${obs}`, sha(obs)]);
    await client.query(`INSERT INTO data_processes(observation_id,transaction_id,process_id,status) VALUES($1,$2,$3,'S')`, [obs, tx, processId]);
    await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_hash,status,rule_status,ml_status,
        decision_code,evidence,decided_at,latency_status) VALUES($1,$2,$3,$4,$5,'complete','succeeded','succeeded',NULL,'{}'::jsonb,clock_timestamp() - make_interval(mins => $6),'unavailable')`,
    [decisionId, execution, obs, configId, sha(obs), minutesAgo]);
    return decisionId;
  };
  const d = { p2: await decision('TX:1', 'P2', 10), p1: await decision('TX:1', 'P1', 1), other: await decision('TX-2', 'P1', 5),
    otherOld: await decision('TX-2', 'P3', 30) };
  // 판정이 많은 거래: TX:1의 P9 판정 520건(가장 최근). 옛 공용 LIMIT 500이면 TX:1의 P2·TX-2가 밀려났다.
  await client.query(`CREATE TEMP TABLE heavy ON COMMIT DROP AS SELECT gen_random_uuid() AS execution, gen_random_uuid() AS obs, gen_random_uuid() AS decision
    FROM generate_series(1, 520)`);
  await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
    SELECT execution,$1,'P','exec-'||execution,'{}','v1',clock_timestamp() FROM heavy`, [sourceId]);
  await client.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,api_received_at,persisted_at,clock_status,
      parsed_times,allowed_values,quality_flags,missing_fields)
    SELECT obs,$1,'P',execution,1,'P:'||obs,encode(sha256(obs::text::bytea),'hex'),clock_timestamp(),clock_timestamp(),'unknown','{}','{}','[]','[]' FROM heavy`, [sourceId]);
  await client.query(`INSERT INTO data_processes(observation_id,transaction_id,process_id,status) SELECT obs,'TX:1','P9','S' FROM heavy`);
  await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_hash,status,rule_status,ml_status,
      decision_code,evidence,decided_at,latency_status)
    SELECT decision,execution,obs,$1,encode(sha256(obs::text::bytea),'hex'),'complete','succeeded','succeeded',NULL,'{}'::jsonb,clock_timestamp(),'unavailable' FROM heavy`,
  [configId]);
  return { sourceId, snapshotId, d };
}
const resultFor = (jobId, sourceId, snapshotId) => {
  const nodeId = (pid) => `pattern_step:IF-A:${pid}:HTTP`;
  const counts = { applicable: 40, satisfied: 39, violated: 1, undetermined: 0 };
  return {
    relationContractVersion: RELATION_CONTRACT_VERSION, experimental: true, snapshotId, sourceId,
    period: { from: '2026-10-01T00:00:00Z', until: '2026-11-01T00:00:00Z', timeBasis: 'persisted_at' },
    parameters: { minApplicable: 30, candidatePercent: 99, reviewPercent: 95, maxCounterexamples: 5, excludeProcessIds: [], handoffMismatchSuffixPatterns: [] },
    data: { interfaceTransactions: { 'IF-A': 40 } },
    graph: { graph: { layer: 'pattern', id: snapshotId, generatedAt: '2026-11-12T12:00:00.000Z' },
      nodes: ['P1', 'P2'].map((pid) => ({ id: nodeId(pid), kind: 'pattern_step', attrs: { interfaceId: 'IF-A', processId: pid, adapterType: 'HTTP', transactions: 40 } })),
      edges: [{ id: `precedes:${nodeId('P1')}:${nodeId('P2')}`, kind: 'precedes', from: nodeId('P1'), to: nodeId('P2'), verification: null,
        evidence: { ...counts, satisfactionRate: 0.975, status: 'review_needed', observedTransactions: 40 } }] },
    candidates: [{ kind: 'precedes', category: 'hard', status: 'review_needed', approvable: true, scope: { adapterType: 'HTTP' }, from: { processId: 'P1' },
      to: { processId: 'P2' }, match: null, patternEdgeId: `precedes:${nodeId('P1')}:${nodeId('P2')}`, counts, satisfactionRate: 0.975,
      counterexampleRefs: [{ refs: ['transaction:TX%3A1', 'process:TX%3A1:P2'], reason: 'other_predecessor', transactionStatus: 'S' },
        { refs: ['transaction:TX-2'], reason: 'other_predecessor', transactionStatus: 'F' },
        { refs: ['transaction:TX-none'], reason: 'other_predecessor', transactionStatus: 'S' }],
      violationReasons: { other_predecessor: 1 }, display: { interfaceId: 'IF-A', toStep: nodeId('P2') }, evaluatedAt: 'successor',
      source: { jobId, snapshotId, patternEdgeId: null, counts, period: {} } }],
    relationNotes: [],
    thresholdCandidates: [{ field: 'elapsed_ms', status: 'insufficient_evidence', reason: null, scope: { processId: 'P1', adapterType: 'HTTP' },
      display: { interfaceIds: ['IF-A'], patternStepIds: [nodeId('P1')] }, operator: null, threshold: null, displayThreshold: null, unit: 'ms', source: 'data_p95',
      evidence: { rows: 40, referenceRows: 39, n: 9, min: 1, max: 9, p95: 8.6, quantileMethod: 'linear', endTimeMissingReferenceRows: 30, unavailableReferenceRows: 0 },
      limitation: 'END_TIME이 없으면 평가하지 않는다.' },
    // Trainer는 제안 임계값을 고정소수 문자열로 낸다(threshold_values.fixed_threshold). API는 number로 낸다.
    { field: 'elapsed_ms', status: 'candidate', reason: null, scope: { processId: 'P2', adapterType: 'HTTP' },
      display: { interfaceIds: ['IF-A'], patternStepIds: [nodeId('P2')] }, operator: 'gt', threshold: '704', displayThreshold: '704', unit: 'ms', source: 'data_p95',
      evidence: { rows: 40, referenceRows: 39, n: 39, min: 1, max: 900, p95: 703.6, quantileMethod: 'linear', endTimeMissingReferenceRows: 0, unavailableReferenceRows: 0 },
      limitation: 'END_TIME이 없으면 평가하지 않는다.' }],
  };
};

try {
  await pool.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  const { rows: [{ db }] } = await pool.query('SELECT current_database() AS db');
  assert.match(db, /^qa_analysis/, 'connected database must be a qa_analysis database');
  const { rows: [{ present }] } = await pool.query(`SELECT 'recommend_relations' = ANY(enum_range(NULL::job_kind)::text[]) AS present`);
  const { rows: [{ checkHas }] } = await pool.query(`SELECT coalesce(bool_or(pg_get_constraintdef(oid) LIKE '%recommend_relations%'), false) AS "checkHas"
    FROM pg_constraint WHERE conrelid = 'run_jobs'::regclass AND conname = 'run_jobs_v03_contract'`);
  const mode = !present ? 'pre-021' : checkHas ? '021' : 'enum-only';
  if (mode === 'pre-021') assert.equal(checkHas, false, 'CHECK lists recommend_relations without the enum value: unexpected DB');
  const { rows: [{ n: priorJobs }] } = await pool.query(`SELECT count(*)::int AS n FROM run_jobs WHERE kind::text='recommend_relations'`);
  assert.equal(priorJobs, 0, 'use a new qa_analysis database (no recommend_relations jobs yet)');
  console.log(`rule candidates integration: DB mode ${mode}`);
  const client = await pool.connect();
  let seeded;
  try {
    await client.query('BEGIN');
    seeded = await seed(client);
    await client.query('COMMIT');
  } catch (error) { await client.query('ROLLBACK'); throw error; } finally { client.release(); }

  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: url,
    JWT_SECRET: 'qa-rule-candidates-local-only-secret',
    SEED_USER_EMAIL: 'rule-candidates-user@example.test', SEED_USER_PASSWORD: 'qa-rule-candidates-local-only-password',
    SEED_ADMIN_EMAIL: 'rule-candidates-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-rule-candidates-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: 'ignore' });
  const base = `http://127.0.0.1:${environment.PORT}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const login = async (email, password) => (await (await fetch(`${base}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }) })).json()).token;
  const user = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  const admin = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const enqueue = (body, key) => request(base, '/api/rule-set/candidates/jobs', admin, { method: 'POST', body, headers: key ? { 'Idempotency-Key': key } : {} });

  // 1) 권한: 토큰 없음 401, user 403(읽기·요청 모두).
  assert.equal((await request(base, '/api/rule-set/candidates')).status, 401);
  for (const [route, options] of [['/api/rule-set/candidates', {}], [`/api/rule-set/candidates/${id()}.r0`, {}],
    ['/api/rule-set/candidates/jobs', { method: 'POST', body: { snapshotId: seeded.snapshotId } }]]) {
    const response = await request(base, route, user, options);
    assert.deepEqual([response.status, response.body?.code], [403, 'admin_required'], route);
  }

  // 2) 결과 없음 — enum 값이 없어도 kind::text 비교라 503이 아니라 result null.
  const empty = await request(base, '/api/rule-set/candidates', admin);
  assert.deepEqual([empty.status, empty.body], [200, { latestJob: null, approval: { available: false, reason: 'rule_set_hash_v3_pending', contentHashVersion: 2 }, result: null }]);

  // 3) 작업 요청: 형식 400, 스냅샷 409. 021 전: enum 없음 503 → enum만 추가(v03 CHECK 그대로) 503. 021 적용 DB는 503 단계 없음.
  assert.deepEqual([(await enqueue({ snapshotId: 'x' })).body.code], ['invalid_candidate_job_request']);
  assert.deepEqual(Object.values(await enqueue({ snapshotId: id() })), [409, { code: 'snapshot_not_ready' }]);
  if (mode === 'pre-021') {
    assert.deepEqual(Object.values(await enqueue({ snapshotId: seeded.snapshotId })), [503, { code: 'relation_job_kind_unavailable' }]);
    await pool.query(`ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'recommend_relations'`); // 시험 DB 전용
  }
  if (mode !== '021') {
    assert.deepEqual(Object.values(await enqueue({ snapshotId: seeded.snapshotId })), [503, { code: 'relation_job_contract_unavailable' }]);
    assert.equal(Number((await pool.query(`SELECT count(*) AS n FROM run_jobs WHERE kind::text='recommend_relations'`)).rows[0].n), 0, 'failed requests leave no job');
    // 4-전) migration 뒤 모의: 시험 DB에서만 v03 CHECK를 지운다.
    await pool.query('ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract');
  }

  // 4) 행 모양(트리거 erd_v03_validate_job 포함, 021 DB면 실제 021 CHECK)·큐 메시지·멱등.
  const created = await enqueue({ snapshotId: seeded.snapshotId, excludeProcessIds: ['IF_A_STUB'] }, 'qa-key-1');
  assert.equal(created.status, 201, JSON.stringify(created.body));
  assert.deepEqual([created.body.kind, created.body.status, created.body.inputSpec],
    ['recommend_relations', 'queued', { kind: 'recommend_relations', snapshotId: seeded.snapshotId, sourceId: seeded.sourceId, excludeProcessIds: ['IF_A_STUB'] }],
    'input keys are exactly the Trainer parse_input keys');
  const { rows: [row] } = await pool.query(`SELECT contract_version, queue::text AS queue, producer_kind::text AS producer, requester_role::text AS role, input_snapshot_id::text AS snapshot
    FROM run_jobs WHERE id=$1`, [created.body.id]);
  assert.deepEqual(row, { contract_version: 'v03', queue: 'training', producer: 'user', role: 'admin', snapshot: seeded.snapshotId });
  const { rows: messages } = await pool.query(`SELECT payload FROM powercode_queue.training_jobs WHERE payload->>'jobId' = $1`, [created.body.id]);
  assert.equal(messages.length, 1, 'one training queue message');
  const replay = await enqueue({ snapshotId: seeded.snapshotId, excludeProcessIds: ['IF_A_STUB'] }, 'qa-key-1');
  assert.deepEqual([replay.status, replay.body.id], [200, created.body.id], 'same key and body → same job');
  assert.deepEqual(Object.values(await enqueue({ snapshotId: seeded.snapshotId }, 'qa-key-1')), [409, { code: 'idempotency_payload_conflict' }]);
  // 스냅샷이 ready가 아니게 바뀐 뒤(시험 DB 전용): 같은 키·본문 재요청은 여전히 200 같은 작업, 새 키는 409 snapshot_not_ready.
  await pool.query(`UPDATE data_snapshots SET status='failed' WHERE id=$1`, [seeded.snapshotId]);
  const replayAfterChange = await enqueue({ snapshotId: seeded.snapshotId, excludeProcessIds: ['IF_A_STUB'] }, 'qa-key-1');
  assert.deepEqual([replayAfterChange.status, replayAfterChange.body.id], [200, created.body.id], 'idempotency lookup precedes the snapshot check');
  assert.deepEqual(Object.values(await enqueue({ snapshotId: seeded.snapshotId }, 'qa-key-2')), [409, { code: 'snapshot_not_ready' }]);
  await pool.query(`UPDATE data_snapshots SET status='ready' WHERE id=$1`, [seeded.snapshotId]);
  const queuedList = await request(base, '/api/rule-set/candidates', admin);
  assert.deepEqual([queuedList.body.latestJob.id, queuedList.body.latestJob.status, queuedList.body.result], [created.body.id, 'queued', null], 'queued job shown, no result yet');

  // 5) 결과 읽기: 성공 작업 1건(v03 training 행 → 시도 행 → 성공 전환, Trainer 결과 JSON). 021 CHECK는 legacy 모양 행을 막으므로 두 경로 모두 v03.
  const jobId = id(), attemptId = id();
  await pool.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash, input_snapshot_id, input_spec,
      max_attempts, attempt_count, next_run_at, created_at, contract_version, queue, producer_kind, producer_key)
    VALUES ($1,'recommend_relations','queued','qa','admin',$2,$3,$4,$5::jsonb,3,0,clock_timestamp(),clock_timestamp(),'v03','training','user','qa')`,
  [jobId, `qa-${jobId}`, sha(jobId), seeded.snapshotId, JSON.stringify({ kind: 'recommend_relations', snapshotId: seeded.snapshotId, sourceId: seeded.sourceId })]);
  await pool.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
    VALUES($1,$2,1,'running','qa-worker',$3,clock_timestamp() + interval '5 minutes',clock_timestamp())`, [attemptId, jobId, id()]);
  await pool.query(`UPDATE run_jobs SET status='succeeded', current_attempt_id=$2, attempt_count=1, finished_at=clock_timestamp(), result_summary=$3::jsonb WHERE id=$1`,
    [jobId, attemptId, JSON.stringify(resultFor(jobId, seeded.sourceId, seeded.snapshotId))]);
  const list = await request(base, '/api/rule-set/candidates', admin);
  assert.equal(list.status, 200, JSON.stringify(list.body));
  assert.deepEqual([list.body.result.job.id, list.body.result.selectedInterfaceId, list.body.result.relationCandidates.map((c) => c.candidateId),
    list.body.result.graph.edges.map((e) => e.evidence.satisfactionRate), list.body.result.thresholdCandidates[0].evidence.endTimeMissingReferenceRows],
  [jobId, 'IF-A', [`${jobId}.r0`], [0.975], 30]);
  assert.deepEqual(list.body.result.thresholdCandidates.map((c) => [c.candidateId, c.threshold]), [[`${jobId}.t0`, null], [`${jobId}.t1`, 704]], 'string threshold → number');
  const thresholdDetail = await request(base, `/api/rule-set/candidates/${jobId}.t1`, admin);
  assert.equal(thresholdDetail.status, 200, JSON.stringify(thresholdDetail.body));
  assert.deepEqual([thresholdDetail.body.threshold.threshold, thresholdDetail.body.ruleDraft.threshold], [704, 704], 'ruleDraft.threshold = PUT /api/rule-set number');
  assert.deepEqual(Object.values(await request(base, '/api/rule-set/candidates?interfaceId=IF-Z', admin)), [404, { code: 'rule_candidate_interface_not_found' }]);
  assert.deepEqual(Object.values(await request(base, '/api/rule-set/candidates?x=1', admin)), [400, { code: 'unknown_candidate_filter' }]);
  const detail = await request(base, `/api/rule-set/candidates/${jobId}.r0`, admin);
  assert.equal(detail.status, 200, JSON.stringify(detail.body));
  assert.deepEqual(detail.body.counterexamples, [
    { transactionId: 'TX:1', reason: 'other_predecessor', transactionStatus: 'S', decisionId: seeded.d.p2 },
    { transactionId: 'TX-2', reason: 'other_predecessor', transactionStatus: 'F', decisionId: seeded.d.other },
    { transactionId: 'TX-none', reason: 'other_predecessor', transactionStatus: 'S', decisionId: null },
  ], 'TX:1: target step P2 despite 521 newer decisions; TX-2 (no P2): latest of any step, not starved by TX:1; no decision: null');
  assert.deepEqual([detail.body.isLatest, detail.body.ruleDraft.scope], [true, { interfaceId: 'IF-A', adapterType: 'HTTP' }]);
  assert.deepEqual(Object.values(await request(base, `/api/rule-set/candidates/${jobId}.r5`, admin)), [404, { code: 'rule_candidate_not_found' }]);
  assert.deepEqual(Object.values(await request(base, `/api/rule-set/candidates/${created.body.id}.r0`, admin)), [404, { code: 'rule_candidate_not_found' }], 'queued job has no result');
  assert.deepEqual(Object.values(await request(base, '/api/rule-set/candidates/jobs', admin)), [400, { code: 'invalid_candidate_id' }]);

  // 6) API 역할(SELECT만)로 같은 읽기 경로.
  const viaApi = await readCandidateList(api, null);
  assert.equal(viaApi.result.job.id, jobId);
  const detailViaApi = await readCandidateDetail(api, `${jobId}.r0`);
  assert.equal(detailViaApi.counterexamples[0].decisionId, seeded.d.p2);
  console.log('rule candidates integration passed');
} finally {
  child?.kill();
  await api.end();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
