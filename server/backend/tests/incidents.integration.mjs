// 사건 보기 읽기 API(contracts/incidents-ui.md) 통합 시험. 로컬 새 QA DB(qa_analysis*)에 001~021이 적용돼 있어야 한다.
// 확인: 인증·user 읽기, 흐름(병합 먼저 → 이어짐 3구간)·기간·기준 필터·커서, 거래 기준 건수·해제(실행별 최신 판정)·거래 ID 없음,
// 어긋난 연결(다른 기준 버전·group_key·원본) 무시+표시, 상세(병합·뒤 구간 id → 흐름 id), 거래별 구성원·인터페이스·커서, 400·404 code,
// powercode_api 역할 SELECT만으로 세 읽기 경로 동작·INSERT 거부. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
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
const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, '..');
const { readIncidentList, readIncidentDetail, readIncidentMembers } = await import('../dist/services/incidentReadModel.js');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-incidents-'));
const pool = new pg.Pool({ connectionString: url });
const api = new pg.Pool({ connectionString: url, options: '-c role=powercode_api' });
let child;

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
const T0 = Date.UTC(2026, 10, 10, 1, 0, 0);
const at = (minutes) => new Date(T0 + minutes * 60_000);
const iso = (minutes) => at(minutes).toISOString().replace('Z', '000Z');
const v4 = (grade) => ({ schemaVersion: 4, anomalous: grade === 'anomaly' || grade === 'anomaly_sign',
  composite: { status: 'complete', anomalous: grade === 'anomaly' || grade === 'anomaly_sign', grade, basisMethods: [] } });

/** 관측·거래·판정 쓰기(시험 전용 합성 값). */
function writers(client, configId) {
  const observation = async (source, layer, execution, revision) => {
    const obs = id();
    await client.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
        api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
      VALUES($1,$2,$3,$4,$5,$6,$7,clock_timestamp(),clock_timestamp(),'unknown','{}','{}','[]','[]')`,
    [obs, source, layer, execution, revision, `${layer}:${obs}`, sha(obs)]);
    return obs;
  };
  const transactions = new Set();
  /** TRANSACTION 행. execution 지정이 없으면 실행 식별 불가 행(execution_id·revision_no NULL). */
  const transaction = async (source, tx, iface, { code = 'E500', execution = null, revision = 1 } = {}) => {
    if (execution) {
      await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
        VALUES($1,$2,'T',$3,'{}','v1',clock_timestamp()) ON CONFLICT DO NOTHING`, [execution, source, `exec-${execution}`]);
    }
    const obs = await observation(source, 'T', execution, execution ? revision : null);
    await client.query(`INSERT INTO data_transactions(observation_id,transaction_id,interface_id,status,response_code) VALUES($1,$2,$3,'F',$4)`, [obs, tx, iface, code]);
  };
  /** 판정 1건: 새 실행(또는 같은 실행의 다음 리비전) PROCESS 관측 + 최종 판정(v4 등급). */
  const decision = async ({ tx, minutes, grade = 'anomaly', execution = null, revision = 1, source, iface = 'IF-INC-1', evidence = v4(grade),
    channel = 'CH:1', withTransaction = true }) => {
    if (withTransaction && tx.trim() && !transactions.has(`${source}/${tx}`)) { transactions.add(`${source}/${tx}`); await transaction(source, tx, iface); }
    const exec = execution ?? id();
    if (!execution) {
      await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
        VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [exec, source, `exec-${exec}`]);
    }
    const obs = await observation(source, 'P', exec, revision);
    await client.query(`INSERT INTO data_processes(observation_id,transaction_id,process_id,channel_id,status,response_code)
      VALUES($1,$2,$3,$4,'F','E500')`, [obs, tx, `P${String(minutes % 7).padStart(2, '0')}`, channel]);
    const decisionId = id();
    await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_hash,status,
        rule_status,ml_status,decision_code,evidence,decided_at,latency_status)
      VALUES($1,$2,$3,$4,$5,'complete','succeeded','succeeded',NULL,$6::jsonb,$7,'unavailable')`,
    [decisionId, exec, obs, configId, sha(obs), JSON.stringify(evidence), at(minutes + 0.5)]);
    return { id: decisionId, execution: exec };
  };
  return { transaction, decision };
}

async function seedIncidents(client, sourceId, configId, otherSourceId) {
  const { transaction, decision: write } = writers(client, configId);
  const decision = (spec) => write({ source: sourceId, ...spec });
  const D = {};
  const decide = async (name, spec) => { D[name] = await decision(spec); return D[name]; };
  await decide('d1', { tx: 'TX-A', minutes: 0 });
  await decide('d2', { tx: 'TX-A', minutes: 1 });
  await decide('d3', { tx: 'TX-B', minutes: 2, iface: 'IF-INC-1' });
  await transaction(sourceId, 'TX-B', 'IF-INC-2'); // 같은 거래 ID에 인터페이스 2개 → 인터페이스 확인 불가(null)
  await decide('d4', { tx: 'TX-C', minutes: 61 });
  await decide('d5', { tx: 'TX-D', minutes: 122 });
  await decide('d6', { tx: '  ', minutes: 123 }); // 거래 ID 없음(graph_value_absent)
  await decide('d14', { tx: 'TX-Z', minutes: 123, source: otherSourceId }); // 다른 원본 판정이 구성원(Detector 오류) → 세지 않고 표시
  await decide('d7', { tx: 'TX-E', minutes: 300 });
  await decide('d8', { tx: 'TX-F', minutes: 301 });
  await decide('d8new', { tx: 'TX-F', minutes: 305, grade: 'normal', execution: D.d8.execution, revision: 2 }); // 새 리비전 정상 → d8 해제
  await decide('d9', { tx: 'TX-G', minutes: 302, grade: 'anomaly_sign' });
  await decide('d9new', { tx: 'TX-G', minutes: 306, grade: 'anomaly_sign', execution: D.d9.execution, revision: 2 }); // 여전히 이상 징후 → 유지
  await decide('d10', { tx: 'TX-H', minutes: 298 });
  await decide('d11', { tx: 'TX-I', minutes: 600 });
  await decide('d12', { tx: 'TX-J', minutes: 540 });
  await decide('d13', { tx: 'TX-K', minutes: 580 });
  await decide('d18', { tx: 'TX-K', minutes: 581 }); // 같은 거래 재시도(새 실행): Detector 활성 실행 2, 화면 거래 1
  // 등급 모름(v3 evidence): d15는 자기 판정이 v3, d16은 같은 실행 새 리비전이 v3 → 최신 등급이 이상·이상 징후가 아니므로 둘 다 해제 + unknownGrade(Detector 8절).
  const v3 = { schemaVersion: 3, anomalous: true };
  await decide('d15', { tx: 'TX-L', minutes: 900, evidence: v3 });
  await decide('d16', { tx: 'TX-L', minutes: 901 });
  await decide('d16new', { tx: 'TX-L', minutes: 903, execution: D.d16.execution, revision: 2, evidence: v3 });
  await decide('d17', { tx: 'TX-M', minutes: 902 });

  const run = id();
  const I = Object.fromEntries(['I1', 'I2', 'I3', 'I4', 'I5', 'I6', 'I7', 'I8', 'I9'].map((name) => [name, id()]));
  const criteria = (version) => ({ version, gapSeconds: 300, maxSpanSeconds: 3600, keys: ['response_code', 'channel_id'] });
  const incident = (name, groupKey, minutes, { continuesFrom = null, version = 'incident-gap-v1' } = {}) =>
    client.query(`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,time_basis,opened_at,continues_from,added_by_run)
      VALUES($1,$2,$3::jsonb,$4,$5,'process_time_raw',$6,$7,$8)`, [I[name], sourceId, JSON.stringify(criteria(version)), version, groupKey, at(minutes), continuesFrom, run]);
  const member = (incidentName, decisionName, minutes, basis = 'process_time_raw') =>
    client.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,$3,$4,$5)`,
      [D[decisionName].id, I[incidentName], at(minutes), basis, run]);
  await incident('I1', 'response_code:E500', 0);
  await incident('I2', 'response_code:E500', 61, { continuesFrom: I.I1 });
  await incident('I3', 'response_code:E500', 122, { continuesFrom: I.I2 });
  await incident('I4', 'channel_id:CH%3A1', 300);
  await incident('I5', 'channel_id:CH%3A1', 298);
  await incident('I7', 'response_code:E900', 540, { version: 'incident-gap-v0' });
  await incident('I8', 'response_code:E901', 580);
  await incident('I6', 'response_code:E900', 600, { continuesFrom: I.I7 });
  await incident('I9', 'response_code:E777', 900);
  for (const [i, d, m, basis] of [['I1', 'd1', 0], ['I1', 'd2', 1], ['I1', 'd3', 2], ['I2', 'd4', 61], ['I3', 'd5', 122], ['I3', 'd6', 123],
    ['I3', 'd14', 123], ['I4', 'd7', 300, 'decided_at'], ['I4', 'd8', 301], ['I4', 'd9', 302], ['I5', 'd10', 298], ['I6', 'd11', 600], ['I7', 'd12', 540],
    ['I8', 'd13', 580], ['I8', 'd18', 581], ['I9', 'd15', 900, 'decided_at'], ['I9', 'd16', 901, 'decided_at'], ['I9', 'd17', 902, 'decided_at']]) await member(i, d, m, basis);
  for (const [from, to] of [['I5', 'I4'], ['I6', 'I8']]) {
    await client.query(`INSERT INTO run_incident_events(incident_id,kind,target_incident_id,added_by_run) VALUES($1,'merged_into',$2,$3)`, [I[from], I[to], run]);
  }
  await client.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
    VALUES($1,$2,'incident-gap-v1',0,0,18,9,2,clock_timestamp())`, [run, sourceId]);
  return { D, I };
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
async function request(base, route, token) {
  const response = await fetch(`${base}${route}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const text = await response.text();
  let body = null;
  try { body = JSON.parse(text); } catch { /* not JSON */ }
  return { status: response.status, body };
}

try {
  await pool.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM run_incidents')).rows[0].n), 0, 'use a new qa_analysis database');
  const fixture = await seedAnalysisFixture(pool);
  const other = await seedAnalysisFixture(pool, { key: `incidents-other-${id()}` });
  const client = await pool.connect();
  let seeded;
  try {
    await client.query('BEGIN');
    seeded = await seedIncidents(client, fixture.sourceId, fixture.configId, other.sourceId);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally { client.release(); }
  const { D, I } = seeded;

  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: url,
    JWT_SECRET: 'qa-incidents-local-only-secret',
    SEED_USER_EMAIL: 'incidents-user@example.test', SEED_USER_PASSWORD: 'qa-incidents-local-only-password',
    SEED_ADMIN_EMAIL: 'incidents-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-incidents-admin-local-only',
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
  const ok = async (route) => {
    const response = await request(base, route, user);
    assert.equal(response.status, 200, `${route} → ${JSON.stringify(response.body)}`);
    return response.body;
  };
  const code = async (route, status, expected) => {
    const response = await request(base, route, user);
    assert.deepEqual([response.status, response.body?.code], [status, expected], route);
  };

  // 1) 인증: 토큰 없음·잘못된 토큰 401, user 200.
  for (const route of ['/api/analysis/incidents', `/api/analysis/incidents/${I.I1}`, `/api/analysis/incidents/${I.I1}/members`]) {
    assert.equal((await request(base, route)).status, 401);
    assert.equal((await request(base, route, 'not-a-token')).status, 401);
    await ok(route);
  }

  // 2) 목록: 흐름 6개(시작 최근 순), 병합 먼저·이어짐 3구간·어긋난 연결 무시+원인별 표시, 거래 기준 건수.
  const list = await ok('/api/analysis/incidents');
  assert.equal(list.truncated, false);
  assert.equal(list.total, 6);
  assert.deepEqual(list.items.map((f) => f.id), [I.I9, I.I6, I.I8, I.I7, I.I4, I.I1]);
  const flow = Object.fromEntries(list.items.map((f) => [f.id, f]));
  assert.deepEqual(flow[I.I1], { id: I.I1, sourceId: fixture.sourceId, criteria: { kind: 'response_code', value: 'E500', longValue: false, version: 'incident-gap-v1' },
    startAt: iso(0), endAt: iso(123), durationMs: 123 * 60_000,
    counts: { transactions: 4, releasedTransactions: 0, decisions: 6, releasedDecisions: 0, missingTransactionId: 1, unknownGrade: 0 },
    occurredBasis: { processTimeRaw: 7, decidedAt: 0 },
    chain: { length: 3, mergedIncidents: 0 }, flags: ['member_source_mismatch'], truncated: false }, 'other-source member is not counted but flagged');
  assert.deepEqual([flow[I.I4].criteria, flow[I.I4].startAt, flow[I.I4].endAt, flow[I.I4].counts, flow[I.I4].chain, flow[I.I4].occurredBasis],
    [{ kind: 'channel_id', value: 'CH:1', longValue: false, version: 'incident-gap-v1' }, iso(298), iso(302),
      { transactions: 3, releasedTransactions: 1, decisions: 3, releasedDecisions: 1, missingTransactionId: 0, unknownGrade: 0 }, { length: 1, mergedIncidents: 1 },
      { processTimeRaw: 3, decidedAt: 1 }],
  'merged incident joins the target; released = latest decision of the execution is no longer anomalous; basis counts include released members');
  assert.deepEqual(flow[I.I6].flags, ['merge_mismatch', 'continuation_mismatch'], 'cross-version continuation and other-key merge are not used');
  assert.deepEqual([flow[I.I7].criteria.version, flow[I.I7].flags], ['incident-gap-v0', []]);
  assert.deepEqual([flow[I.I9].counts, flow[I.I9].occurredBasis],
    [{ transactions: 1, releasedTransactions: 1, decisions: 1, releasedDecisions: 2, missingTransactionId: 0, unknownGrade: 2 }, { processTimeRaw: 0, decidedAt: 3 }],
    'unknown latest grade (v3 evidence, own or newer revision) is not anomaly -> released (Detector 8), counted in unknownGrade');
  assert.deepEqual([flow[I.I8].counts.transactions, flow[I.I8].counts.decisions], [1, 2], 'retried transaction: 2 executions, 1 transaction');
  assert.ok(list.items.every((f) => !('document' in f.criteria)), 'list does not carry the criteria document');

  // 3) 필터·커서.
  const ids = (body) => body.items.map((f) => f.id);
  assert.deepEqual(ids(await ok('/api/analysis/incidents?criteria=channel_id')), [I.I4]);
  const atLeastTwo = await ok('/api/analysis/incidents?minTransactions=2');
  // 해제되지 않은 거래만 센다: I8(TX-K 재시도 2실행) = 1 → 숨김, I9(TX-L 해제·TX-M 활성) = 1 → 숨김, I4(TX-F 해제, 활성 3) = 3 → 보임.
  assert.deepEqual([ids(atLeastTwo), atLeastTwo.total], [[I.I4, I.I1], 2], 'minTransactions counts active (not released) distinct transaction ids');
  assert.deepEqual(ids(await ok('/api/analysis/incidents?minTransactions=4')), [I.I1], 'released TX-F does not count toward I4 (3 active)');
  assert.deepEqual(ids(await ok('/api/analysis/incidents?minTransactions=5')), []);
  assert.deepEqual(ids(await ok(`/api/analysis/incidents?from=${encodeURIComponent(at(200).toISOString())}`)), [I.I9, I.I6, I.I8, I.I7, I.I4]);
  assert.deepEqual(ids(await ok(`/api/analysis/incidents?to=${encodeURIComponent(at(100).toISOString())}`)), [I.I1]);
  assert.deepEqual(ids(await ok(`/api/analysis/incidents?from=${at(130).toISOString()}&to=${at(290).toISOString()}`)), []);
  assert.deepEqual(ids(await ok(`/api/analysis/incidents?from=2026-11-10T03:03:00%2B00:00`)), [I.I9, I.I6, I.I8, I.I7, I.I4, I.I1], 'end boundary is inclusive (+hh:mm)');
  assert.deepEqual(ids(await ok(`/api/analysis/incidents?sourceId=${other.sourceId}`)), []);
  let page = await ok('/api/analysis/incidents?limit=2'), seen = [];
  for (let guard = 0; guard < 5; guard += 1) {
    seen.push(...ids(page));
    assert.equal(page.total, 6);
    if (!page.nextCursor) break;
    page = await ok(`/api/analysis/incidents?limit=2&cursor=${page.nextCursor}`);
  }
  assert.deepEqual(seen, [I.I9, I.I6, I.I8, I.I7, I.I4, I.I1], 'cursor pages cover every flow once');
  page = await ok('/api/analysis/incidents?limit=1&minTransactions=2'); seen = [];
  for (let guard = 0; guard < 5; guard += 1) {
    seen.push(...ids(page));
    if (!page.nextCursor) break;
    page = await ok(`/api/analysis/incidents?limit=1&minTransactions=2&cursor=${page.nextCursor}`);
  }
  assert.deepEqual(seen, [I.I4, I.I1], 'keyset paging with the transaction filter');

  // 4) 상세: 뒤 구간·병합 id도 흐름 id로, 구간별 이어짐·건수.
  const detail = await ok(`/api/analysis/incidents/${I.I2}`);
  assert.deepEqual([detail.id, detail.requestedId, detail.criteria.groupKey, detail.criteria.timeBasis, detail.criteria.document.gapSeconds, detail.criteria.value,
    detail.criteria.longValue, detail.occurredBasis], [I.I1, I.I2, 'response_code:E500', 'process_time_raw', 300, 'E500', false, { processTimeRaw: 7, decidedAt: 0 }]);
  assert.deepEqual(detail.segments.map((s) => s.occurredBasis), [{ processTimeRaw: 3, decidedAt: 0 }, { processTimeRaw: 1, decidedAt: 0 }, { processTimeRaw: 3, decidedAt: 0 }]);
  assert.deepEqual(detail.segments.map((s) => [s.id, s.openedAt, s.endAt, s.continuesFrom, s.counts.transactions, s.counts.decisions]),
    [[I.I1, iso(0), iso(2), [], 2, 3], [I.I2, iso(61), iso(61), [I.I1], 1, 1], [I.I3, iso(122), iso(123), [I.I2], 1, 2]]);
  assert.equal(detail.truncated, false);
  const merged = await ok(`/api/analysis/incidents/${I.I5}`);
  assert.deepEqual([merged.id, merged.requestedId, merged.segments.map((s) => [s.id, s.mergedIncidentIds])], [I.I4, I.I5, [[I.I4, [I.I5]]]]);

  // 5) 구성원: 거래별(첫 시각 순), 거래 ID 없음은 판정마다, 인터페이스는 하나일 때만, 해제·최신 판정, 커서.
  const members = await ok(`/api/analysis/incidents/${I.I3}/members`);
  assert.equal(members.id, I.I1);
  assert.equal(members.total, 5);
  assert.deepEqual(members.items.map((m) => [m.transactionId, m.interfaceId, m.decisionCount, m.released, m.firstOccurredAt]),
    [['TX-A', 'IF-INC-1', 2, false, iso(0)], ['TX-B', null, 1, false, iso(2)], ['TX-C', 'IF-INC-1', 1, false, iso(61)],
      ['TX-D', 'IF-INC-1', 1, false, iso(122)], [null, null, 1, false, iso(123)]]);
  assert.deepEqual(members.items[0].decisions.map((d) => [d.decisionId, d.incidentId, d.segmentId, d.grade, d.released, d.latestDecisionId]),
    [[D.d1.id, I.I1, I.I1, 'anomaly', false, D.d1.id], [D.d2.id, I.I1, I.I1, 'anomaly', false, D.d2.id]]);
  assert.ok(!JSON.stringify(members).includes('TX-Z'), 'other-source member is not listed');
  const channel = await ok(`/api/analysis/incidents/${I.I4}/members`);
  const byTx = Object.fromEntries(channel.items.map((m) => [m.transactionId, m]));
  assert.deepEqual(channel.items.map((m) => m.transactionId), ['TX-H', 'TX-E', 'TX-F', 'TX-G']);
  assert.deepEqual([byTx['TX-F'].released, byTx['TX-F'].decisions[0].latestDecisionId, byTx['TX-F'].decisions[0].latestGrade], [true, D.d8new.id, 'normal']);
  assert.deepEqual([byTx['TX-G'].released, byTx['TX-G'].decisions[0].latestGrade, byTx['TX-H'].decisions[0].incidentId], [false, 'anomaly_sign', I.I5]);
  assert.deepEqual(channel.items.map((m) => [m.transactionId, m.occurredBasis]), [['TX-H', 'process_time_raw'], ['TX-E', 'decided_at'], ['TX-F', 'process_time_raw'],
    ['TX-G', 'process_time_raw']], 'row time basis: decided_at rows are decision times');
  assert.equal(byTx['TX-E'].decisions[0].occurredBasis, 'decided_at');
  let memberPage = await ok(`/api/analysis/incidents/${I.I1}/members?limit=2`), keys = [];
  for (let guard = 0; guard < 5; guard += 1) {
    keys.push(...memberPage.items.map((m) => m.transactionId));
    if (!memberPage.nextCursor) break;
    memberPage = await ok(`/api/analysis/incidents/${I.I1}/members?limit=2&cursor=${memberPage.nextCursor}`);
  }
  assert.deepEqual(keys, ['TX-A', 'TX-B', 'TX-C', 'TX-D', null]);
  const unknown = await ok(`/api/analysis/incidents/${I.I9}/members`);
  assert.deepEqual(unknown.items.map((m) => [m.transactionId, m.released, m.occurredBasis, m.decisions.map((d) => [d.decisionId, d.grade, d.latestGrade, d.gradeUnknown, d.released])]),
    [['TX-L', true, 'decided_at', [[D.d15.id, null, null, true, true], [D.d16.id, 'anomaly', null, true, true]]],
      ['TX-M', false, 'decided_at', [[D.d17.id, 'anomaly', 'anomaly', false, false]]]],
    'unknown latest grade is exposed per decision and released (not anomaly)');

  // 5b) 읽기 상한: 목록 골격 상한 밖 앞 구간을 가리키면 이어짐 대상 없음으로 표시하지 않고 그 흐름을 truncated로. 상한 밖 사건의 상세는 사슬을 직접 읽어 200.
  const capped = await readIncidentList(pool, { from: null, to: null, criteria: null, sourceId: fixture.sourceId, minTransactions: 1, limit: 20, cursor: null },
    { scanLimit: 7 });
  const cappedById = Object.fromEntries(capped.items.map((f) => [f.id, f]));
  assert.equal(capped.truncated, true);
  assert.ok(!cappedById[I.I1], 'I1 is outside the 7-row window');
  assert.deepEqual([cappedById[I.I3].flags, cappedById[I.I3].truncated], [['member_source_mismatch'], true], 'boundary link is truncated, not continuation_target_missing');
  assert.equal(cappedById[I.I4].truncated, false);
  const outside = await readIncidentDetail(api, I.I1);
  assert.deepEqual([outside.id, outside.truncated, outside.segments.length], [I.I1, false, 3], 'incident outside the list window still resolves its whole chain');
  const chainCapped = await readIncidentDetail(api, I.I3, 2);
  assert.deepEqual([chainCapped.id, chainCapped.truncated, chainCapped.segments.map((s) => s.id), chainCapped.flags], [I.I2, true, [I.I2, I.I3], ['member_source_mismatch']],
    'chain beyond the walk limit: 200 with truncated, no false continuation flag');
  assert.equal((await readIncidentMembers(api, I.I3, 50, null, 2)).total, 3, 'members of the capped chain (TX-C, TX-D, no-transaction row)');

  // 6) 요청 오류·없음.
  await code('/api/analysis/incidents/not-a-uuid', 400, 'invalid_incident_id');
  await code(`/api/analysis/incidents/${id()}`, 404, 'incident_not_found');
  await code(`/api/analysis/incidents/${id()}/members`, 404, 'incident_not_found');
  await code(`/api/analysis/incidents/${I.I1}?x=1`, 400, 'unknown_incident_filter');
  await code('/api/analysis/incidents?page=1', 400, 'unknown_incident_filter');
  await code('/api/analysis/incidents?from=yesterday', 400, 'invalid_from');
  await code('/api/analysis/incidents?to=2026-13-01T00:00:00Z', 400, 'invalid_to');
  await code(`/api/analysis/incidents?from=${at(10).toISOString()}&to=${at(0).toISOString()}`, 400, 'invalid_period');
  await code('/api/analysis/incidents?criteria=process_hub_id', 400, 'invalid_criteria');
  for (const value of ['0', '1001', 'x']) await code(`/api/analysis/incidents?minTransactions=${value}`, 400, 'invalid_min_transactions');
  await code('/api/analysis/incidents?sourceId=x', 400, 'invalid_source_id');
  for (const limit of ['0', '101', 'x', '1&limit=2']) await code(`/api/analysis/incidents?limit=${limit}`, 400, 'invalid_limit');
  await code(`/api/analysis/incidents/${I.I1}/members?limit=201`, 400, 'invalid_limit');
  for (const cursor of ['%%%', 'abc', Buffer.from('["x","y"]').toString('base64url')]) await code(`/api/analysis/incidents?cursor=${cursor}`, 400, 'invalid_cursor');
  await code(`/api/analysis/incidents/${I.I1}/members?cursor=${list.items.length && (await ok('/api/analysis/incidents?limit=1')).nextCursor}`, 400, 'invalid_cursor');

  // 7) 권한: powercode_api 역할 SELECT만으로 세 읽기 경로, 쓰기 거부.
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api');
  const apiList = await readIncidentList(api, { from: null, to: null, criteria: null, sourceId: null, minTransactions: 2, limit: 20, cursor: null });
  assert.deepEqual(apiList.items.map((f) => f.id), [I.I4, I.I1]);
  const apiAll = await readIncidentList(api, { from: null, to: null, criteria: null, sourceId: null, minTransactions: 1, limit: 20, cursor: null });
  assert.deepEqual(apiAll.items.map((f) => f.id), list.items.map((f) => f.id));
  assert.equal((await readIncidentDetail(api, I.I3)).id, I.I1);
  assert.equal((await readIncidentMembers(api, I.I1, 50, null)).total, 5);
  const denied = async (sql, params) => {
    const c = await api.connect();
    try { await c.query(sql, params); return 'allowed'; } catch (error) { return error.code; } finally { c.release(); }
  };
  assert.equal(await denied(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
    VALUES($1,$2,'x',0,0,0,0,0,now())`, [id(), fixture.sourceId]), '42501');
  assert.equal(await denied('DELETE FROM run_incident_members', []), '42501');

  // 8) 긴 기준값(Detector 4.2 `<키>:sha256:<hex>`): 화면 값은 구성원 원천 행의 원래 값(해시가 맞을 때만), 못 읽으면 null + longValue(해시를 값으로 내보내지 않음).
  //    별도 원본(위 목록 단언과 섞이지 않게), powercode_api 역할로 읽는다.
  const third = await seedAnalysisFixture(pool, { key: `incidents-long-${id()}` });
  const longChannel = `CH-${'L'.repeat(600)}`, longCode = `E-${'9'.repeat(600)}`, staleCode = `E-${'8'.repeat(600)}`;
  const L = { channel: id(), code: id(), missing: id(), stale: id() };
  const writer = await pool.connect();
  try {
    await writer.query('BEGIN');
    const { transaction, decision } = writers(writer, fixture.configId);
    const run = id();
    // 채널: PROCESS 행 channel_id. 응답코드: TRANSACTION 실행 1개·리비전 2개 중 큰 리비전 값. 실행 식별 불가 T 행만 있음 → 못 읽음.
    // stale: 해시는 옛 값인데 원천 행의 최신 리비전 값이 다름 → 해시 불일치라 보이지 않음.
    const dChannel = await decision({ tx: 'TX-LONG-1', minutes: 2000, source: third.sourceId, channel: longChannel });
    const txExec = id();
    await transaction(third.sourceId, 'TX-LONG-2', 'IF-L', { code: 'E-old', execution: txExec, revision: 1 });
    await transaction(third.sourceId, 'TX-LONG-2', 'IF-L', { code: longCode, execution: txExec, revision: 2 });
    const dCode = await decision({ tx: 'TX-LONG-2', minutes: 2010, source: third.sourceId, withTransaction: false });
    const dMissing = await decision({ tx: 'TX-LONG-3', minutes: 2020, source: third.sourceId }); // 실행 식별 불가 T 행(E500)
    const staleExec = id();
    await transaction(third.sourceId, 'TX-LONG-4', 'IF-L', { code: staleCode, execution: staleExec, revision: 1 });
    await transaction(third.sourceId, 'TX-LONG-4', 'IF-L', { code: 'E-new', execution: staleExec, revision: 2 });
    const dStale = await decision({ tx: 'TX-LONG-4', minutes: 2030, source: third.sourceId, withTransaction: false });
    const crit = JSON.stringify({ version: 'incident-gap-v1', gapSeconds: 300, maxSpanSeconds: 3600 });
    for (const [incidentId, groupKey, minutes, d] of [[L.channel, `channel_id:sha256:${sha(longChannel)}`, 2000, dChannel],
      [L.code, `response_code:sha256:${sha(longCode)}`, 2010, dCode], [L.missing, `response_code:sha256:${sha(longCode)}`, 2020, dMissing],
      [L.stale, `response_code:sha256:${sha(staleCode)}`, 2030, dStale]]) {
      await writer.query(`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,time_basis,opened_at,added_by_run)
        VALUES($1,$2,$3::jsonb,'incident-gap-v1',$4,'process_time_raw',$5,$6)`, [incidentId, third.sourceId, crit, groupKey, at(minutes), run]);
      await writer.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,$3,'process_time_raw',$4)`,
        [d.id, incidentId, at(minutes), run]);
    }
    await writer.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-gap-v1',0,0,4,4,0,clock_timestamp())`, [run, third.sourceId]);
    await writer.query('COMMIT');
  } catch (error) {
    await writer.query('ROLLBACK');
    throw error;
  } finally { writer.release(); }
  const longList = await readIncidentList(api, { from: null, to: null, criteria: null, sourceId: third.sourceId, minTransactions: 1, limit: 20, cursor: null });
  assert.deepEqual(longList.items.map((f) => [f.id, f.criteria.kind, f.criteria.value, f.criteria.longValue]),
    [[L.stale, 'response_code', null, true], [L.missing, 'response_code', null, true], [L.code, 'response_code', longCode, true], [L.channel, 'channel_id', longChannel, true]],
    'long values come from source rows (channel = PROCESS, response code = latest TRANSACTION revision); unreadable or hash-mismatched -> null');
  assert.ok(!JSON.stringify(longList.items).includes('sha256:'), 'the list never carries the hash as a value');
  const longDetail = await ok(`/api/analysis/incidents/${L.code}`);
  assert.deepEqual([longDetail.criteria.value, longDetail.criteria.longValue, longDetail.criteria.groupKey], [longCode, true, `response_code:sha256:${sha(longCode)}`]);
  assert.equal((await ok(`/api/analysis/incidents/${L.missing}`)).criteria.value, null);

  console.log('incidents integration passed: auth, 6 flows (merge first, 3-segment chain, cross-version/other-key links flagged by cause), '
    + 'unknown latest grade released (unknownGrade, Detector 8), minTransactions filter (active transactions: retry and released excluded) with keyset paging, read caps (list window truncated without false flags, '
    + 'detail beyond the window, chain walk limit), '
    + 'period/criteria/source filters and cursor, transaction counts with released and missing transaction id, detail id resolution, '
    + 'members by transaction with interface and cursor, 400/404 codes, powercode_api SELECT-only paths, occurred basis counts/rows, '
    + 'long group key values from source rows (hash-checked, unreadable -> null)');
} finally {
  child?.kill();
  await api.end();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
