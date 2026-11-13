// 사용자별 설정·알림 API(contracts/notifications.md) 통합 시험. 로컬 새 QA DB(qa_notifications*)에 001~022가 적용돼 있어야 한다.
// API 프로세스는 powercode_api 역할로 접속한다(연결 options role). 확인: 인증·400 code, 설정 기본값·저장·계정 분리, 알림 항목(묶이지 않은 판정·사건 흐름),
// 판정 기준 3택·위험도 하한·미정 포함 필터, 알림 사유(opened·risk_raised·withdrawn·grew), 미확인·항목/모두 읽음·다시 알림, 커서, 조회 범위. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { fixtureWriters } from './analysis-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_notifications[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_notifications database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, '..');
const { readNotifications, readUnreadCount } = await import('../dist/services/notificationReadModel.js');
const { readIncidentDetail } = await import('../dist/services/incidentReadModel.js');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-notifications-'));
const pool = new pg.Pool({ connectionString: url });
const apiUrl = new URL(url);
apiUrl.searchParams.set('options', '-c role=powercode_api');
const api = new pg.Pool({ connectionString: apiUrl.toString() });
let child;

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
/** v4 evidence(시험 전용 합성 값): 등급·위험도는 저장값 그대로 읽힌다. */
const v4 = ({ anomalous = true, grade = 'anomaly', level = null, unrated = [] } = {}) => ({
  schemaVersion: 4, anomalous,
  composite: { status: 'complete', anomalous, grade, basisMethods: [],
    risk: anomalous ? { policy: 'risk-max-v1', status: level ? 'rated' : 'unrated', level, determinedBy: [], unratedMethods: unrated }
      : { policy: 'risk-max-v1', status: 'not_applicable', level: null, determinedBy: [], unratedMethods: [] } },
});

async function seedBase(client) {
  const sourceId = id(), configId = id(), actor = 'notifications-fixture';
  await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,$2,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["T","P","M","B"]}',clock_timestamp())`, [sourceId, `notif-${sourceId}`]);
  await client.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
    VALUES($1,$2,'{}','{}',$3,$4,clock_timestamp())`, [configId, sourceId, sha(configId), actor]);
  return { sourceId, configId };
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

try {
  await pool.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM run_decision_events')).rows[0].n), 0, 'use a new qa_notifications database');
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api', 'API connection string runs as the runtime role');

  // 1) 시험 데이터: 작업 없는 판정 4건, detect 작업 3건(중간 기록 → 근거 추가 → 최종 / 중간 기록 → 최종 없음 / 최종이 사건 구성원), 사건 흐름 1개.
  const client = await pool.connect();
  const out = { decisions: {}, jobs: {} };
  const D = {}, J = {}, I = {};
  let base;
  try {
    await client.query('BEGIN');
    base = await seedBase(client);
    const w = fixtureWriters(client, base.sourceId, base.configId, out);
    D.critical = await w.legacyDecision('critical', 50, 'complete', v4({ level: 'critical' }));
    D.infoPartial = await w.legacyDecision('infoPartial', 49, 'partial', v4({ grade: 'anomaly_sign', level: 'info' }));
    D.mlOnly = await w.legacyDecision('mlOnly', 48, 'complete', v4({ grade: 'anomaly_sign', unrated: ['ml:iforest'] }));
    D.normal = await w.legacyDecision('normal', 47, 'complete', v4({ anomalous: false, grade: 'normal' }));
    // J1: 주의 → 위험(근거 추가) → 최종 위험(완료).
    J.raised = await w.detectJob('raised', 40);
    const a1 = await w.attempt(J.raised, 1);
    await w.interim(J.raised, a1, 'first_anomaly', v4({ level: 'warning' }));
    await w.interim(J.raised, a1, 'evidence_added', v4({ level: 'critical' }));
    D.raisedFinal = await w.jobDecision(J.raised, a1, 'complete', v4({ level: 'critical' }));
    // J2: 중간 기록(주의) 뒤 실패 → 최종 없음(012 트리거가 no_final 기록).
    J.noFinal = await w.detectJob('noFinal', 39);
    const a2 = await w.attempt(J.noFinal, 1);
    await w.interim(J.noFinal, a2, 'first_anomaly', v4({ level: 'warning' }));
    await w.failJob(J.noFinal, a2);
    // 사건 구성원: 작업 없는 판정 3건 + J3 최종 판정(J3 작업 알림은 사건에 묶여 따로 나오지 않음).
    D.m1 = await w.legacyDecision('m1', 30, 'complete', v4({ level: 'warning' }));
    J.bundled = await w.detectJob('bundled', 29);
    const a3 = await w.attempt(J.bundled, 1);
    await w.interim(J.bundled, a3, 'first_anomaly', v4({ level: 'critical' }));
    D.m2 = await w.jobDecision(J.bundled, a3, 'complete', v4({ level: 'critical' }));
    D.m3 = await w.legacyDecision('m3', 28, 'complete', v4({ level: 'info' }));
    D.m4 = await w.legacyDecision('m4', 27, 'partial', v4({ grade: 'anomaly_sign', unrated: ['ml:iforest'] }));
    const run = id();
    Object.assign(I, { a: id(), b: id(), c: id() });
    const incident = (key, groupKey, minutes, continuesFrom = null) => client.query(`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,
        time_basis,opened_at,continues_from,added_by_run) VALUES($1,$2,'{"gapSeconds":300}','incident-gap-v1',$3,'decided_at',clock_timestamp()-($4::double precision*interval '1 minute'),$5,$6)`,
    [I[key], base.sourceId, groupKey, minutes, continuesFrom, run]);
    await incident('a', 'response_code:E500', 31);
    await incident('b', 'response_code:E500', 20, I.a); // 다음 1시간 구간(이어짐)
    await incident('c', 'response_code:E500', 30); // a에 병합
    const member = (key, decisionId) => client.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run)
      VALUES($1,$2,clock_timestamp(),'decided_at',$3)`, [decisionId, I[key], run]);
    await member('a', D.m1); await member('c', D.m2); await member('b', D.m3); await member('b', D.m4);
    await client.query(`INSERT INTO run_incident_events(incident_id,kind,target_incident_id,added_by_run) VALUES($1,'merged_into',$2,$3)`, [I.c, I.a, run]);
    await client.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-gap-v1',0,0,4,3,1,clock_timestamp())`, [run, base.sourceId]);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally { client.release(); }
  const seqOf = async (decisionId) => (await pool.query('SELECT id::text AS n FROM run_decision_events WHERE decision_id=$1', [decisionId])).rows[0].n;
  const interimSeqs = async (jobId) => (await pool.query(`SELECT e.id::text AS n,e.event_kind AS kind FROM run_decision_events e JOIN run_decision_interims i ON i.id=e.interim_id
    WHERE i.job_id=$1 ORDER BY e.id`, [jobId])).rows;

  // 2) API 프로세스(powercode_api 역할).
  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: apiUrl.toString(),
    JWT_SECRET: 'qa-notifications-local-only-secret',
    SEED_USER_EMAIL: 'notifications-user@example.test', SEED_USER_PASSWORD: 'qa-notifications-local-only-password',
    SEED_ADMIN_EMAIL: 'notifications-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-notifications-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: 'ignore' });
  const base_ = `http://127.0.0.1:${environment.PORT}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base_}/health`)).ok; } catch { /* 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const login = async (email, password) => (await (await fetch(`${base_}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }) })).json()).token;
  const user = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  const admin = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const call = async (method, route, token, body, contentType = 'application/json') => {
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    if (body !== undefined) headers['content-type'] = contentType;
    const response = await fetch(`${base_}${route}`, { method, headers, body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body) });
    const text = await response.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status, body: json };
  };
  const ok = async (method, route, token = user, body) => {
    const response = await call(method, route, token, body);
    assert.equal(response.status, 200, `${method} ${route} → ${response.status} ${JSON.stringify(response.body)}`);
    return response.body;
  };
  const code = async (method, route, status, expected, body, token = user, contentType) => {
    const response = await call(method, route, token, body, contentType);
    assert.deepEqual([response.status, response.body?.code], [status, expected], `${method} ${route} ${JSON.stringify(body)}`);
  };
  const list = (query = 'view=all&limit=50', token = user) => ok('GET', `/api/notifications?${query}`, token);
  const byKey = (body) => Object.fromEntries(body.items.map((item) => [item.key, item]));

  // 3) 인증·형식 오류.
  for (const [method, route] of [['GET', '/api/me/settings'], ['PUT', '/api/me/settings'], ['GET', '/api/notifications'], ['GET', '/api/notifications/unread-count'],
    ['POST', '/api/notifications/read']]) {
    assert.equal((await call(method, route)).status, 401, `${method} ${route} without token`);
    assert.equal((await call(method, route, 'not-a-token')).status, 401, `${method} ${route} bad token`);
  }
  await code('GET', '/api/notifications?view=new', 400, 'invalid_view');
  await code('GET', '/api/notifications?limit=0', 400, 'invalid_limit');
  await code('GET', '/api/notifications?limit=51', 400, 'invalid_limit');
  await code('GET', '/api/notifications?cursor=%%%', 400, 'invalid_cursor');
  await code('GET', `/api/notifications?cursor=${Buffer.from('["1","flow:x"]').toString('base64url')}`, 400, 'invalid_cursor');
  await code('GET', '/api/notifications?sort=old', 400, 'unknown_notification_filter');
  await code('GET', '/api/notifications/unread-count?view=all', 400, 'unknown_notification_filter');
  await code('GET', '/api/me/settings?x=1', 400, 'unknown_notification_filter');
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', {});
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { through: 5 });
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { through: '-1' });
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { through: '1', items: [] });
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { items: [] });
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { items: [{ key: `job:${id().toUpperCase()}`, seq: '1' }] });
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { items: [{ key: `job:${J.raised.id}`, seq: '1' }, { key: `job:${J.raised.id}`, seq: '2' }] });
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', { items: Array.from({ length: 51 }, () => ({ key: `job:${id()}`, seq: '1' })) });
  await code('POST', '/api/notifications/read', 400, 'invalid_json', '{"through":', user);
  await code('POST', '/api/notifications/read', 400, 'invalid_read_request', 'through=1', user, 'application/x-www-form-urlencoded');
  await code('PUT', '/api/me/settings', 400, 'invalid_settings_request', {});
  await code('PUT', '/api/me/settings', 400, 'invalid_settings_request', { theme: 'dark' });
  await code('PUT', '/api/me/settings', 400, 'invalid_notification_settings', { notifications: { decisionBasis: 'final', minRiskLevel: 'info', includeUnrated: true } });
  await code('PUT', '/api/me/settings', 400, 'invalid_notification_settings', { notifications: { decisionBasis: 'interim', minRiskLevel: 'info' } });
  await code('PUT', '/api/me/settings', 400, 'invalid_notification_settings', { notifications: { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: 'yes' } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: { version: 2, items: [{ i: 'flow', x: 0, y: 0, w: 2 }] } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: { version: 2, items: [{ i: 'flow', x: 0, y: 0, w: 2, h: 3 }, { i: 'flow', x: 2, y: 0, w: 2, h: 3 }] } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: { version: 2, items: [{ i: 'Flow', x: 0.5, y: 0, w: 2, h: 3 }] } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: null });
  await code('PUT', '/api/me/settings', 413, 'request_too_large', { widgetLayout: { version: 2, items: [], pad: 'x'.repeat(17000) } });
  console.log('PASS auth and request validation');

  // 4) 설정: 기본값 → 저장 → 다시 읽기, 계정 분리.
  const defaults = await ok('GET', '/api/me/settings');
  assert.deepEqual(defaults, { notifications: { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, isDefault: true, updatedAt: null },
    widgetLayout: { layout: null, updatedAt: null } });
  const layout = { version: 2, items: [{ i: 'flow', x: 0, y: 0, w: 2, h: 3 }, { i: 'storage', x: 2, y: 0, w: 2, h: 2 }] };
  const savedLayout = await ok('PUT', '/api/me/settings', admin, { widgetLayout: layout });
  assert.deepEqual(savedLayout.widgetLayout.layout, layout);
  assert.equal(savedLayout.notifications.isDefault, true, 'saving the layout leaves notification settings at their defaults');
  assert.deepEqual((await ok('GET', '/api/me/settings')).widgetLayout, { layout: null, updatedAt: null }, 'layouts are per account');
  console.log('PASS settings defaults, partial save and per-account isolation');

  // 5) 기본 설정(interim·info·미정 포함): 항목 6개. 정상 판정과 사건에 묶인 작업(J3)은 없다.
  const all = await list();
  const items = byKey(all);
  const keys = Object.keys(items).sort();
  const wanted = [`decision:${D.critical}`, `decision:${D.infoPartial}`, `decision:${D.mlOnly}`, `job:${J.raised.id}`, `job:${J.noFinal.id}`, `incident:${I.a}`].sort();
  assert.deepEqual(keys, wanted, JSON.stringify(all.items.map((item) => item.key)));
  assert.equal(all.total, 6);
  assert.equal(all.unreadCount, 6);
  assert.equal(all.readThrough, '0');
  assert.equal(all.window.truncated, false);
  assert.deepEqual(all.settings, { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true });
  const raisedEvents = await interimSeqs(J.raised.id);
  const raised = items[`job:${J.raised.id}`];
  assert.equal(raised.reason, 'risk_raised', 'warning interim → critical evidence_added raises risk');
  assert.equal(raised.seq, raisedEvents.find((event) => event.kind === 'evidence_added').n, 'the final (same risk) does not move the notification');
  assert.deepEqual([raised.decision.stage, raised.decision.decisionId, raised.decision.detailId, raised.decision.riskLevel, raised.decision.completeness],
    ['final', D.raisedFinal, D.raisedFinal, 'critical', 'complete']);
  assert.ok(raised.decision.transactionId?.startsWith('TX-raised-') && raised.decision.processId);
  const noFinal = items[`job:${J.noFinal.id}`];
  const noFinalEvents = await interimSeqs(J.noFinal.id);
  assert.deepEqual([noFinal.reason, noFinal.decision.stage, noFinal.decision.decisionId, noFinal.decision.detailId, noFinal.seq],
    ['withdrawn', 'no_final', null, J.noFinal.id, noFinalEvents.find((event) => event.kind === 'no_final').n]);
  assert.ok(noFinal.decision.transactionId?.startsWith('TX-noFinal-'), 'job without a decision reads its PROCESS row through the manifest');
  assert.deepEqual([items[`decision:${D.mlOnly}`].decision.riskStatus, items[`decision:${D.mlOnly}`].decision.hasUnratedEvidence, items[`decision:${D.mlOnly}`].decision.grade],
    ['unrated', true, 'anomaly_sign']);
  const flow = items[`incident:${I.a}`];
  assert.deepEqual([flow.type, flow.incident.id, flow.incident.memberDecisions, flow.incident.riskLevel, flow.incident.criteria, flow.reason, flow.seq],
    ['incident', I.a, 4, 'critical', { kind: 'response_code', value: 'E500', longValue: false }, 'grew', await seqOf(D.m4)],
    'merged (c) and continuing (b) incidents notify as the flow head a; last member m4 grows it');
  // 순서: 순번 내림차순.
  assert.deepEqual(all.items.map((item) => BigInt(item.seq)), [...all.items.map((item) => BigInt(item.seq))].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0)));
  console.log('PASS default filter: unbundled decisions, jobs (risk_raised, withdrawn), incident flow, bundled job hidden');

  // 6) 커서: limit 2로 끝까지 → 같은 항목, 같은 순서.
  const walked = [];
  let next = null;
  do {
    const page = await list(`view=all&limit=2${next ? `&cursor=${next}` : ''}`);
    assert.ok(page.items.length <= 2);
    walked.push(...page.items.map((item) => item.key));
    next = page.nextCursor;
  } while (next);
  assert.deepEqual(walked, all.items.map((item) => item.key));
  console.log('PASS keyset cursor walk');

  // 7) 읽음: 항목 1개 → 미확인 5, unread 보기에서 빠짐. 모두 읽음 → 0. 사용자별 분리.
  // 범위에 없는 항목 키는 저장하지 않는다(행 수 상한), 요청 순번은 그 항목의 현재 순번 이하로 줄인다.
  const ghostKey = `job:${id()}`;
  const ghost = await ok('POST', '/api/notifications/read', user, { items: [{ key: ghostKey, seq: raised.seq }] });
  assert.equal(ghost.unreadCount, 6);
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_notification_reads WHERE item_key=$1', [ghostKey])).rows[0].n), 0, 'unknown item key ignored');
  const one = await ok('POST', '/api/notifications/read', user, { items: [{ key: raised.key, seq: '9223372036854775807' }] });
  assert.equal((await pool.query('SELECT read_seq::text AS n FROM ops_user_notification_reads WHERE item_key=$1', [raised.key])).rows[0].n, raised.seq,
    'item read sequence is capped at the item notification sequence');
  assert.deepEqual([one.unreadCount, one.readThrough], [5, '0']);
  const unreadView = await list('view=unread&limit=50');
  assert.equal(unreadView.items.some((item) => item.key === raised.key), false);
  assert.equal(unreadView.total, 5);
  assert.equal((await list()).items.find((item) => item.key === raised.key).unread, false);
  assert.equal((await ok('GET', '/api/notifications/unread-count', admin)).unreadCount, 6, 'read state is per account');
  const allRead = await ok('POST', '/api/notifications/read', user, { through: all.latestSeq });
  assert.deepEqual([allRead.unreadCount, allRead.readThrough], [0, all.latestSeq]);
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_notification_reads')).rows[0].n), 0, 'read marks at or below the cursor are pruned');
  const back = await ok('POST', '/api/notifications/read', user, { through: '1' });
  assert.equal(back.readThrough, all.latestSeq, 'read cursor never moves back');
  const beyond = await ok('POST', '/api/notifications/read', user, { through: '9223372036854775807' });
  assert.equal(beyond.readThrough, all.latestSeq, 'read cursor is capped at the latest event');
  console.log('PASS item read, read-all, per-account state, cursor monotonic and capped');

  // 8) 다시 알림: 사건에 위험 구성원 추가 → 사건 미확인(risk_raised 아님: 이미 위험), 새 판정 → 새 항목.
  const more = await pool.connect();
  try {
    await more.query('BEGIN');
    const w = fixtureWriters(more, base.sourceId, base.configId, out);
    D.m5 = await w.legacyDecision('m5', 5, 'complete', v4({ level: 'critical' }));
    D.late = await w.legacyDecision('late', 4, 'complete', v4({ level: 'warning' }));
    const run = id();
    await more.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,clock_timestamp(),'decided_at',$3)`, [D.m5, I.b, run]);
    await more.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-gap-v1',0,0,1,0,0,clock_timestamp())`, [run, base.sourceId]);
    await more.query('COMMIT');
  } finally { more.release(); }
  const again = await list('view=unread&limit=50');
  assert.deepEqual(again.items.map((item) => [item.key, item.reason]).sort(), [[`decision:${D.late}`, 'opened'], [`incident:${I.a}`, 'grew']].sort());
  assert.equal(again.items.find((item) => item.type === 'incident').incident.memberDecisions, 5);
  console.log('PASS re-notification after a new member and a new decision');

  // 9) 판정 기준·위험도 하한·미정 포함 필터(설정 저장 → 같은 계정 목록에 바로 적용).
  const keysFor = async (notifications) => {
    await ok('PUT', '/api/me/settings', admin, { notifications });
    return (await list('view=all&limit=50', admin)).items.map((item) => item.key).sort();
  };
  assert.deepEqual(await keysFor({ decisionBasis: 'final_complete', minRiskLevel: 'warning', includeUnrated: false }),
    [`decision:${D.critical}`, `decision:${D.late}`, `job:${J.raised.id}`, `incident:${I.a}`].sort(), 'final_complete+warning: no partial, no info, no unrated, no interim-only job');
  const strict = byKey(await list('view=all&limit=50', admin));
  assert.equal(strict[`job:${J.raised.id}`].reason, 'opened', 'final-only basis opens at the final decision');
  assert.equal(strict[`job:${J.raised.id}`].seq, await seqOf(D.raisedFinal));
  assert.equal(strict[`incident:${I.a}`].incident.memberDecisions, 3, 'members m1 (warning), m2 (critical), m5 (critical) pass; m3 info and m4 partial do not');
  assert.deepEqual(await keysFor({ decisionBasis: 'final_any', minRiskLevel: 'critical', includeUnrated: true }),
    [`decision:${D.critical}`, `decision:${D.mlOnly}`, `job:${J.raised.id}`, `incident:${I.a}`].sort(), 'final_any+critical+unrated: ml-only joins, info partial does not');
  const unratedFlow = byKey(await list('view=all&limit=50', admin))[`incident:${I.a}`];
  assert.equal(unratedFlow.incident.memberDecisions, 3, 'm2, m5 (critical) and m4 (partial, unrated) pass');
  assert.deepEqual(await keysFor({ decisionBasis: 'final_any', minRiskLevel: 'info', includeUnrated: false }),
    [`decision:${D.critical}`, `decision:${D.infoPartial}`, `decision:${D.late}`, `job:${J.raised.id}`, `incident:${I.a}`].sort());
  const settings = await ok('GET', '/api/me/settings', admin);
  assert.deepEqual([settings.notifications.decisionBasis, settings.notifications.isDefault, typeof settings.notifications.updatedAt, settings.widgetLayout.layout],
    ['final_any', false, 'string', layout], 'notification save keeps the stored layout');
  console.log('PASS decision basis, risk floor and unrated filters');

  // 10) 조회 범위: 최근 이벤트 몇 개만 보면 범위 밖 항목은 빠지고 truncated. 서비스 함수를 powercode_api 역할 풀로 직접 부른다.
  const latest = BigInt(again.latestSeq);
  const windowed = await readNotifications(api, 'window-check', { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true },
    { view: 'all', limit: 50, cursor: null, windowEvents: 2 });
  assert.deepEqual([windowed.window.truncated, windowed.window.fromSeq, windowed.items.map((item) => item.key).sort()],
    [true, String(latest - 1n), [`decision:${D.late}`, `incident:${I.a}`].sort()]);
  assert.equal(windowed.items.find((item) => item.type === 'incident').incident.memberDecisions, 1, 'only in-window members count');
  assert.equal((await readUnreadCount(api, 'window-check', windowed.settings, 2)).unreadCount, 2);
  console.log('PASS event window bound');

  // 11) 재시도 대체(012 트리거는 새 시도 최종 뒤에 이전 시도 superseded를 기록): 단계는 마지막 이벤트가 아니라 최종 판정으로 정한다.
  //     사건 흐름 id: b가 a를 잇고 b가 n에 병합(n은 이어짐 없음, n이 가장 이름) → 사건 보기 흐름 id(n)와 같은 항목 1개.
  const late = await pool.connect();
  const R = {};
  try {
    await late.query('BEGIN');
    const w = fixtureWriters(late, base.sourceId, base.configId, out);
    const retried = async (name, finalEvidence) => {
      const job = await w.detectJob(name, 2);
      const first = await w.attempt(job, 1);
      await w.interim(job, first, 'first_anomaly', v4({ level: 'warning' }));
      await late.query(`UPDATE run_job_attempts SET status='expired' WHERE id=$1`, [first.id]);
      const decision = await w.jobDecision(job, await w.attempt(job, 2), 'complete', finalEvidence);
      return { job, decision };
    };
    R.cleared = await retried('retryNormal', v4({ anomalous: false, grade: 'normal' }));
    R.final = await retried('retryAnomaly', v4({ level: 'warning' }));
    R.da = await w.legacyDecision('flowA', 2, 'complete', v4({ level: 'warning' }));
    R.dn = await w.legacyDecision('flowN', 1, 'complete', v4({ level: 'critical' }));
    const run = id();
    Object.assign(I, { fa: id(), fb: id(), fn: id() });
    const incident = (key, groupKey, minutes, continuesFrom = null) => late.query(`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,
        time_basis,opened_at,continues_from,added_by_run) VALUES($1,$2,'{"gapSeconds":300}','incident-gap-v1',$3,'decided_at',clock_timestamp()-($4::double precision*interval '1 minute'),$5,$6)`,
    [I[key], base.sourceId, groupKey, minutes, continuesFrom, run]);
    await incident('fa', 'channel_id:CH-FLOW', 100);
    await incident('fb', 'channel_id:CH-FLOW', 50, I.fa);
    await incident('fn', 'channel_id:CH-FLOW', 120);
    await late.query(`INSERT INTO run_incident_events(incident_id,kind,target_incident_id,added_by_run) VALUES($1,'merged_into',$2,$3)`, [I.fb, I.fn, run]);
    for (const [key, decisionId] of [['fa', R.da], ['fn', R.dn]]) {
      await late.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,clock_timestamp(),'decided_at',$3)`,
        [decisionId, I[key], run]);
    }
    await late.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-gap-v1',0,0,2,3,1,clock_timestamp())`, [run, base.sourceId]);
    await late.query('COMMIT');
  } finally { late.release(); }
  for (const { job } of [R.cleared, R.final]) {
    const kinds = (await pool.query(`SELECT event_kind FROM run_decision_events e LEFT JOIN run_decision_interims i ON i.id=e.interim_id
      LEFT JOIN run_process_decisions d ON d.id=e.decision_id WHERE coalesce(i.job_id,d.job_id)=$1 ORDER BY e.id`, [job.id])).rows.map((row) => row.event_kind);
    assert.deepEqual(kinds, ['first_anomaly', 'final', 'superseded'], 'fixture reproduces the trigger order (superseded after the new final)');
  }
  const retriedItems = byKey(await list('view=all&limit=50'));
  const cleared = retriedItems[`job:${R.cleared.job.id}`];
  assert.deepEqual([cleared.reason, cleared.decision.stage, cleared.decision.decisionId, cleared.decision.finalSignal],
    ['withdrawn', 'cleared', R.cleared.decision, 'no'], 'normal retry final withdraws the interim notification');
  const anomalous = retriedItems[`job:${R.final.job.id}`];
  assert.deepEqual([anomalous.reason, anomalous.decision.stage, anomalous.decision.finalSignal, anomalous.decision.riskLevel],
    ['opened', 'final', 'yes', 'warning'], 'anomalous retry final without its own interim is a final, not superseded');
  const flowId = (await readIncidentDetail(api, I.fn)).id;
  assert.equal(flowId, (await readIncidentDetail(api, I.fa)).id, 'a and n are one flow on the incidents screen');
  assert.equal(flowId, I.fn, 'earliest segment is the merge group of n');
  const flowKeys = Object.keys(retriedItems).filter((key) => [I.fa, I.fb, I.fn].some((incidentId) => key.endsWith(incidentId)));
  assert.deepEqual(flowKeys, [`incident:${flowId}`], 'one item keyed by the incidents screen flow id');
  assert.equal(retriedItems[`incident:${flowId}`].incident.memberDecisions, 2);
  console.log('PASS retry supersede stage and incident flow id parity');
  console.log('PASS notifications integration');
} finally {
  child?.kill();
  await pool.end();
  await api.end();
  await rm(temporary, { recursive: true, force: true });
}
