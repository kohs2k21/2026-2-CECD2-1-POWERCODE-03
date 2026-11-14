// 사용자별 설정·알림 API(contracts/notifications.md) 통합 시험. 로컬 새 QA DB(qa_notifications*)에 001~028이 적용돼 있어야 한다.
// API 프로세스는 powercode_api 역할로 접속한다(연결 options role). 확인: 인증·400 code, 설정 기본값·저장·계정 분리, 알림 항목(묶이지 않은 판정·사건 흐름),
// 판정 기준 3택·위험도 하한·미정 포함 필터, 알림 사유(opened·risk_raised·withdrawn·grew), 미확인(종류별 수 포함)·항목/모두 읽음·다시 알림, 커서, 조회 범위(이벤트 수·조회 기간 일),
// 작업 알림(028: 학습·비교 평가 종료, 관리자만·설정 includeJobs, 종료 시각 기준 기간·읽음). 실행 전 `npm run build`.
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
const { readNotifications, readUnreadCount, markNotificationsRead } = await import('../dist/services/notificationReadModel.js');
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
  /** 목록(전체 보기, 한 쪽에 다 들어가는 크기)의 종류별 미확인 수 — 배지 unreadByType과 비교한다. */
  const unreadTypes = (body) => Object.fromEntries(['decision', 'incident', 'protocol', 'job'].map((type) => [type, body.items.filter((item) => item.unread && item.type === type).length]));
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
  // 알림 조회 기간(026): 1~30 정수만. 0·31·소수·문자열·null 거절.
  for (const windowDays of [0, 31, 7.5, '7', null, -1]) {
    await code('PUT', '/api/me/settings', 400, 'invalid_notification_settings', { notifications: { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, windowDays } });
  }
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: { version: 2, items: [{ i: 'flow', x: 0, y: 0, w: 2 }] } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: { version: 2, items: [{ i: 'flow', x: 0, y: 0, w: 2, h: 3 }, { i: 'flow', x: 2, y: 0, w: 2, h: 3 }] } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: { version: 2, items: [{ i: 'Flow', x: 0.5, y: 0, w: 2, h: 3 }] } });
  await code('PUT', '/api/me/settings', 400, 'invalid_widget_layout', { widgetLayout: null });
  await code('PUT', '/api/me/settings', 413, 'request_too_large', { widgetLayout: { version: 2, items: [], pad: 'x'.repeat(17000) } });
  console.log('PASS auth and request validation');

  // 4) 설정: 기본값 → 저장 → 다시 읽기, 계정 분리.
  const defaults = await ok('GET', '/api/me/settings');
  assert.deepEqual(defaults, { notifications: { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, windowDays: 7, includeJobs: true, isDefault: true, updatedAt: null },
    widgetLayout: { layout: null, updatedAt: null } });
  const layout = { version: 2, items: [{ i: 'flow', x: 0, y: 0, w: 2, h: 3 }, { i: 'storage', x: 2, y: 0, w: 2, h: 2 }] };
  const savedLayout = await ok('PUT', '/api/me/settings', admin, { widgetLayout: layout });
  assert.deepEqual(savedLayout.widgetLayout.layout, layout);
  assert.equal(savedLayout.notifications.isDefault, true, 'saving the layout leaves notification settings at their defaults');
  assert.deepEqual((await ok('GET', '/api/me/settings')).widgetLayout, { layout: null, updatedAt: null }, 'layouts are per account');
  // 조회 기간 경계 1·30 저장, windowDays 생략은 저장값 유지(026 이전 화면 호환), 계정 분리. 관리자 값은 끝에 기본 7로 되돌린다.
  const notif = (windowDays) => ({ decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, ...(windowDays === undefined ? {} : { windowDays }) });
  assert.equal((await ok('PUT', '/api/me/settings', admin, { notifications: notif(1) })).notifications.windowDays, 1);
  assert.equal((await ok('PUT', '/api/me/settings', admin, { notifications: notif(30) })).notifications.windowDays, 30);
  assert.equal((await ok('PUT', '/api/me/settings', admin, { notifications: notif() })).notifications.windowDays, 30, 'omitted windowDays keeps the stored value');
  assert.equal((await ok('GET', '/api/me/settings', admin)).notifications.windowDays, 30);
  assert.equal((await ok('GET', '/api/me/settings')).notifications.windowDays, 7, 'window days are per account');
  assert.equal((await ok('PUT', '/api/me/settings', admin, { notifications: notif(7) })).notifications.windowDays, 7);
  console.log('PASS settings defaults, partial save, window days bounds and per-account isolation');

  // 5) 기본 설정(interim·info·미정 포함): 항목 6개. 정상 판정과 사건에 묶인 작업(J3)은 없다.
  const all = await list();
  const items = byKey(all);
  const keys = Object.keys(items).sort();
  const wanted = [`decision:${D.critical}`, `decision:${D.infoPartial}`, `decision:${D.mlOnly}`, `job:${J.raised.id}`, `job:${J.noFinal.id}`, `incident:${I.a}`].sort();
  assert.deepEqual(keys, wanted, JSON.stringify(all.items.map((item) => item.key)));
  assert.equal(all.total, 6);
  assert.equal(all.unreadCount, 6);
  const typed = await ok('GET', '/api/notifications/unread-count');
  assert.deepEqual([typed.unreadCount, typed.unreadByType], [6, { decision: 5, incident: 1, protocol: 0, job: 0 }], 'badge per-type counts: 5 unbundled decisions + 1 incident flow');
  assert.equal(all.readThrough, '0');
  assert.deepEqual([all.window.truncated, all.window.truncatedBy, all.window.days, all.window.fromSeq], [false, null, 7, '1']);
  assert.deepEqual(all.settings, { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, windowDays: 7, includeJobs: true });
  assert.equal(all.latestJobAt, null, 'no job notifications for a user account');
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
  assert.deepEqual(one.unreadByType, { decision: 4, incident: 1, protocol: 0, job: 0 }, 'read response carries per-type counts');
  const unreadView = await list('view=unread&limit=50');
  assert.equal(unreadView.items.some((item) => item.key === raised.key), false);
  assert.equal(unreadView.total, 5);
  assert.equal((await list()).items.find((item) => item.key === raised.key).unread, false);
  assert.equal((await ok('GET', '/api/notifications/unread-count', admin)).unreadCount, 6, 'read state is per account');
  const allRead = await ok('POST', '/api/notifications/read', user, { through: all.latestSeq });
  assert.deepEqual([allRead.unreadCount, allRead.readThrough], [0, all.latestSeq]);
  assert.deepEqual(allRead.unreadByType, { decision: 0, incident: 0, protocol: 0, job: 0 });
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
  const windowed = await readNotifications(api, 'window-check', { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, windowDays: 7 },
    { view: 'all', limit: 50, cursor: null, windowEvents: 2 });
  assert.deepEqual([windowed.window.truncated, windowed.window.truncatedBy, windowed.window.fromSeq, windowed.items.map((item) => item.key).sort()],
    [true, 'events', String(latest - 1n), [`decision:${D.late}`, `incident:${I.a}`].sort()]);
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
  // 12) 프로토콜 알림(contracts/protocol-settings-ui.md 5절, 합성 인터페이스·채널): evidence.protocolReview가 있는 최종 판정에서 (인터페이스, 경로)마다 항목 1개,
  //     처음 나온 판정 1번만 알림, [이상 유지] 뒤 다시 알리지 않음(목록에는 남음), 경로 없는 미등록 인터페이스는 인터페이스 단위, 현재 룰 세트에 있으면 추가됨.
  const review = (kind, interfaceId, routes, extra = {}) => ({ version: 1, kind, interfaceId, protocolRuleId: kind === 'path_unmatched' ? 'hard:proto-0000000a' : null,
    ruleSetRevision: 3, transactionObservationId: id(), routes, routesReason: null, endpoints: { start: null, end: null, matchesRoutes: null, ambiguous: false }, ...extra });
  const route = (channels, matched) => ({ channels, pathKey: channels.map((c) => c.replaceAll('%', '%25').replaceAll(':', '%3A')).join(':'), matched });
  const unmatched = review('path_unmatched', 'IF_SYN_A', [route(['CH_A', 'CH:X'], false), route(['CH_A', 'CH_B'], true)]);
  const P = {};
  const writeProtocol = async (name, minutesAgo, protocolReview, evidence = v4({ level: 'critical' })) => {
    const tx = await pool.connect();
    try {
      await tx.query('BEGIN');
      const w = fixtureWriters(tx, base.sourceId, base.configId, out);
      P[name] = await w.legacyDecision(name, minutesAgo, 'complete', { ...evidence, protocolReview });
      await tx.query('COMMIT');
    } catch (error) { await tx.query('ROLLBACK'); throw error; } finally { tx.release(); }
  };
  await writeProtocol('p1', 1.5, unmatched);
  await writeProtocol('p2', 1.4, unmatched);
  await writeProtocol('p3', 1.3, review('interface_unregistered', 'IF_SYN_NEW', [], { routesReason: 'chain_broken' }));
  await writeProtocol('p4', 1.2, { ...unmatched, version: 2 });
  await writeProtocol('p5', 1.1, { ...unmatched, interfaceId: 'IF_SYN_OK', routes: [route(['CH_A'], true)] });
  const protocolItems = () => list('view=all&limit=50', admin).then((body) => body.items.filter((item) => item.type === 'protocol'));
  const items1 = await protocolItems();
  assert.deepEqual(items1.map((item) => [item.protocol.interfaceId, item.protocol.pathKey, item.protocol.kind, item.protocol.decisions, item.reason, item.unread]),
    [['IF_SYN_NEW', null, 'interface_unregistered', 1, 'first_seen', true], ['IF_SYN_A', 'CH_A:CH%3AX', 'path_unmatched', 2, 'first_seen', true]],
    'one item per (interface, unmatched path); version 2 and fully matched reviews make none');
  const pathItem = items1.find((item) => item.protocol.interfaceId === 'IF_SYN_A');
  const firstSeq = (await pool.query('SELECT id::text AS id FROM run_decision_events WHERE decision_id=$1', [P.p1])).rows[0].id;
  assert.deepEqual([pathItem.seq, pathItem.protocol.decisionId, pathItem.protocol.channels, pathItem.protocol.protocolRuleId, pathItem.protocol.resolution],
    [firstSeq, P.p2, ['CH_A', 'CH:X'], 'hard:proto-0000000a', null], 'seq = first occurrence, decision = latest');
  assert.match(pathItem.key, /^protocol:[0-9a-f]{64}$/);
  const newItem = items1.find((item) => item.protocol.interfaceId === 'IF_SYN_NEW');
  assert.deepEqual([newItem.protocol.channels, newItem.protocol.routesReason], [null, 'chain_broken']);
  // 관리자만: 일반 사용자 목록·배지·읽음 요청에는 프로토콜 항목이 없다.
  const userBefore = (await ok('GET', '/api/notifications/unread-count', user)).unreadCount;
  assert.equal((await ok('GET', '/api/notifications/unread-count', user)).unreadByType.protocol, 0, 'user badge has no protocol count');
  const adminTyped = await ok('GET', '/api/notifications/unread-count', admin);
  assert.deepEqual(adminTyped.unreadByType, unreadTypes(await list('view=all&limit=50', admin)), 'admin per-type counts match the unread list items');
  assert.equal(adminTyped.unreadByType.protocol, 2, 'admin badge counts both protocol items');
  assert.equal(adminTyped.unreadCount, adminTyped.unreadByType.decision + adminTyped.unreadByType.incident + adminTyped.unreadByType.protocol);
  assert.equal((await list('view=all&limit=50', user)).items.filter((item) => item.type === 'protocol').length, 0, 'protocol items are admin only');
  await ok('POST', '/api/notifications/read', user, { items: [{ key: pathItem.key, seq: pathItem.seq }] });
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_protocol_alert_reads')).rows[0].n), 0, 'a user cannot mark a protocol item');
  assert.equal((await ok('GET', '/api/notifications/unread-count', user)).unreadCount, userBefore);
  // 항목 읽음: 023 표에 저장, 022 표에는 넣지 않는다.
  await ok('POST', '/api/notifications/read', admin, { items: [{ key: pathItem.key, seq: pathItem.seq }] });
  assert.equal((await list('view=all&limit=50', admin)).items.find((item) => item.key === pathItem.key).unread, false);
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_protocol_alert_reads WHERE item_key=$1', [pathItem.key])).rows[0].n), 1);
  // [이상 유지]: 관리자만, 본문 검사, 범위에 없는 묶음 404, 두 번째는 처음 기록 유지.
  await code('POST', '/api/notifications/protocol-alerts/keep', 403, 'admin_required', { interfaceId: 'IF_SYN_A', pathKey: 'CH_A:CH%3AX' }, user);
  for (const bad of [{}, { interfaceId: '', pathKey: null }, { interfaceId: 'IF_SYN_A' }, { interfaceId: 'IF_SYN_A', pathKey: '' }, { interfaceId: 'IF_SYN_A', pathKey: 3 },
    { interfaceId: 'IF_SYN_A', pathKey: null, extra: 1 }]) {
    await code('POST', '/api/notifications/protocol-alerts/keep', 400, 'invalid_protocol_keep_request', bad, admin);
  }
  await code('POST', '/api/notifications/protocol-alerts/keep', 404, 'protocol_alert_not_found', { interfaceId: 'IF_SYN_A', pathKey: 'CH_A:CH_B' }, admin);
  const kept = await ok('POST', '/api/notifications/protocol-alerts/keep', admin, { interfaceId: 'IF_SYN_A', pathKey: 'CH_A:CH%3AX' });
  assert.equal(kept.unreadByType.protocol, unreadTypes(await list('view=all&limit=50', admin)).protocol, 'keep response carries per-type counts');
  assert.deepEqual([kept.key, kept.alreadyKept, kept.keptBy], [pathItem.key, false, (await pool.query('SELECT kept_by_actor_id AS a FROM ops_protocol_alert_keeps')).rows[0].a]);
  assert.equal((await ok('POST', '/api/notifications/protocol-alerts/keep', admin, { interfaceId: 'IF_SYN_A', pathKey: 'CH_A:CH%3AX' })).alreadyKept, true);
  await writeProtocol('p6', 1.0, unmatched);
  const afterKeep = (await protocolItems()).find((item) => item.key === pathItem.key);
  assert.deepEqual([afterKeep.seq, afterKeep.unread, afterKeep.protocol.decisions, afterKeep.protocol.resolution], [firstSeq, false, 3, 'kept'],
    'kept: same pair again does not re-notify, the item stays listed');
  // 범위가 밀린 뒤(B4): 유지 시점 판정이 범위 밖으로 나가고 범위 정리 DELETE로 읽음 행이 지워져도, 같은 묶음이 다시 나오면 미확인이 아니고 배지도 그대로.
  const adminId = kept.keptBy;
  const adminSettings = { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, windowDays: 7 };
  const badgeBefore = (await ok('GET', '/api/notifications/unread-count', admin)).unreadCount;
  // 판정 자체의 알림(묶이지 않은 판정)이 배지를 바꾸지 않도록 이 판정은 정상 저장값으로 둔다(protocolReview만 봄 — 합성 값).
  await writeProtocol('p7', 0.9, unmatched, v4({ anomalous: false, grade: 'normal' }));
  for (let index = 0; index < 3; index += 1) {
    const tx = await pool.connect();
    try {
      await tx.query('BEGIN');
      await fixtureWriters(tx, base.sourceId, base.configId, out).legacyDecision(`filler${index}`, 0.8 - index * 0.01, 'complete', v4({ anomalous: false, grade: 'normal' }));
      await tx.query('COMMIT');
    } catch (error) { await tx.query('ROLLBACK'); throw error; } finally { tx.release(); }
  }
  assert.equal((await ok('GET', '/api/notifications/unread-count', admin)).unreadCount, badgeBefore, 'same pair again after keep: badge unchanged');
  const { rows: [{ top: topSeq }] } = await pool.query('SELECT max(id)::bigint AS top FROM run_decision_events');
  const narrowWindow = Number(BigInt(topSeq) - BigInt(kept.keptThroughSeq));
  assert.ok(narrowWindow >= 2, 'p6 and p7 stay inside the narrow window, the kept occurrence is outside');
  await markNotificationsRead(api, adminId, adminSettings, { through: '0' }, narrowWindow, true);
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_protocol_alert_reads WHERE actor_id=$1 AND item_key=$2', [adminId, pathItem.key])).rows[0].n), 0,
    'window cleanup deleted the read mark of the kept item');
  const narrow = await readNotifications(api, adminId, adminSettings, { view: 'all', limit: 50, cursor: null, windowEvents: narrowWindow, protocol: true });
  const slid = narrow.items.find((item) => item.key === pathItem.key);
  assert.ok(slid && BigInt(slid.seq) <= BigInt(kept.keptThroughSeq), `window slid: seq ${slid?.seq} <= kept ${kept.keptThroughSeq}`);
  assert.deepEqual([slid.unread, slid.protocol.resolution], [false, 'kept'], 'kept item outside the window counts as read');
  const narrowBadge = await readUnreadCount(api, adminId, adminSettings, narrowWindow, true);
  assert.equal(narrowBadge.unreadCount, narrow.items.filter((item) => item.unread).length);
  assert.ok(!narrow.items.some((item) => item.key === pathItem.key && item.unread));
  // 추가됨: 현재 룰 세트에 그 인터페이스 항목이 있으면(경로 없는 알림) resolution added.
  const { rows: [top] } = await pool.query('SELECT max(revision)::bigint AS r FROM ops_rule_set_revisions');
  await pool.query(`INSERT INTO ops_rule_set_revisions(revision,content_hash,content_hash_version,snapshot,changes,summary,saved_by_actor_id,saved_at)
    VALUES($1,$2,3,$3::jsonb,'[]','{}','fixture',clock_timestamp())`, [Number(top.r) + 1, sha('protocol-fixture'),
    JSON.stringify({ hardRules: [], softRules: [], relationRules: [], protocolRules: [{ id: 'hard:proto-0000000b', interfaceId: 'IF_SYN_NEW', paths: [{ channels: ['CH_N'], source: null }],
      severity: 'critical', enabled: true, source: null, createdAtRevision: 1, updatedAtRevision: 1 }] })]);
  assert.equal((await protocolItems()).find((item) => item.protocol.interfaceId === 'IF_SYN_NEW').protocol.resolution, 'added');
  console.log('PASS protocol alerts: grouping, first-seen seq, read marks, keep (admin, once, no re-notify), added');

  // 13) 조회 기간(026): 앞 이벤트들의 시각을 과거로 옮긴다(시험 DB 소유자만, 불변 트리거를 잠깐 끔 — 순번·내용은 그대로).
  //     D.critical까지 20일 전, 그 뒤 D.mlOnly까지 3일 전. 기간 1일: 셋 다 빠짐 / 7일: critical만 빠짐 / 30일: 모두 보임.
  const [seqCritical, seqInfoPartial, seqMlOnly] = [await seqOf(D.critical), await seqOf(D.infoPartial), await seqOf(D.mlOnly)];
  assert.ok(BigInt(seqCritical) < BigInt(seqInfoPartial) && BigInt(seqInfoPartial) < BigInt(seqMlOnly), 'fixture order critical < infoPartial < mlOnly');
  const aged = await pool.connect();
  try {
    await aged.query('BEGIN');
    await aged.query('ALTER TABLE run_decision_events DISABLE TRIGGER decision_event_immutable');
    await aged.query(`UPDATE run_decision_events SET created_at=created_at-interval '20 days' WHERE id<=$1`, [seqCritical]);
    await aged.query(`UPDATE run_decision_events SET created_at=created_at-interval '3 days' WHERE id>$1 AND id<=$2`, [seqCritical, seqMlOnly]);
    await aged.query('ALTER TABLE run_decision_events ENABLE TRIGGER decision_event_immutable');
    await aged.query('COMMIT');
  } catch (error) { await aged.query('ROLLBACK'); throw error; } finally { aged.release(); }
  const early = [`decision:${D.critical}`, `decision:${D.infoPartial}`, `decision:${D.mlOnly}`];
  const periodFor = async (windowDays) => {
    await ok('PUT', '/api/me/settings', user, { notifications: notif(windowDays) });
    const body = await list('view=all&limit=50', user);
    return { body, shown: early.filter((key) => body.items.some((item) => item.key === key)) };
  };
  const day1 = await periodFor(1);
  assert.deepEqual(day1.shown, [], 'window 1 day: events older than a day are excluded');
  assert.deepEqual([day1.body.window.days, day1.body.window.truncated, day1.body.window.truncatedBy, day1.body.window.fromSeq, day1.body.settings.windowDays],
    [1, true, 'days', String(BigInt(seqMlOnly) + 1n), 1]);
  const day7 = await periodFor(7);
  assert.deepEqual(day7.shown, [`decision:${D.infoPartial}`, `decision:${D.mlOnly}`], 'window 7 days: the 20-day-old event is excluded, the 3-day-old ones stay');
  assert.deepEqual([day7.body.window.truncatedBy, day7.body.window.fromSeq], ['days', String(BigInt(seqCritical) + 1n)]);
  const day30 = await periodFor(30);
  assert.deepEqual(day30.shown, early, 'window 30 days: all three are back');
  assert.deepEqual([day30.body.window.truncated, day30.body.window.truncatedBy, day30.body.window.fromSeq], [false, null, '1']);
  // 미확인 수·읽음 요청도 같은 범위: 기간 밖 항목은 세지 않고, 읽음 요청에서는 무시된다(행 없음).
  await ok('PUT', '/api/me/settings', admin, { notifications: notif(1) });
  const adminDay1 = await list('view=all&limit=50', admin);
  const adminDay1Count = await ok('GET', '/api/notifications/unread-count', admin);
  assert.equal(adminDay1Count.unreadCount, adminDay1.items.filter((item) => item.unread).length, 'badge = unread items in the window');
  assert.deepEqual(adminDay1Count.unreadByType, unreadTypes(adminDay1), 'per-type counts use the same window');
  assert.equal(adminDay1.items.some((item) => early.includes(item.key)), false);
  await ok('POST', '/api/notifications/read', admin, { items: [{ key: `decision:${D.critical}`, seq: seqCritical }] });
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_notification_reads WHERE item_key=$1', [`decision:${D.critical}`])).rows[0].n), 0,
    'an item outside the window is not stored as read');
  console.log('PASS notification window days: 1/7/30 days, truncatedBy, badge and read requests use the same window');

  // 14) 작업 알림(028, contracts/notifications.md "작업 알림" 절): v03 training 큐 train·compare가 succeeded·failed로 끝난 것만, 관리자 ∧ includeJobs,
  //     기간 = 종료 시각, 읽음 = 종료 시각 기준(모두 읽음은 latestJobAt을 jobsThrough로). 합성 작업 행(시험 전용 값).
  const jobSettings = (over = {}) => ({ decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true, windowDays: 7, includeJobs: true, ...over });
  await ok('PUT', '/api/me/settings', admin, { notifications: jobSettings() });
  const adminActor = adminId;
  const jobRow = async (kind, { status, errorCode = null, summary = null, version = null, finishedAgo = null }) => {
    const jobId = id();
    await pool.query(`INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash,
        input_spec, max_attempts, attempt_count, next_run_at, created_at, contract_version, queue, producer_kind, producer_key)
      VALUES ($1, $2::text::job_kind, 'queued', $3, 'admin', $4, $5, $6::jsonb, 3, 0, clock_timestamp(), clock_timestamp(), 'v03', 'training', 'user', $3)`,
    [jobId, kind, adminActor, id(), id(), JSON.stringify({ kind })]);
    if (status === 'queued') {
      if (errorCode) await pool.query('UPDATE run_jobs SET error_code=$2 WHERE id=$1', [jobId, errorCode]);
      return jobId;
    }
    const attemptId = id();
    await pool.query(`INSERT INTO run_job_attempts (id, job_id, attempt_no, status, worker_id, lease_token, lease_until, started_at)
      VALUES ($1, $2, 1, 'running', 'trainer-test', $3, clock_timestamp() + interval '10 minutes', clock_timestamp())`, [attemptId, jobId, id()]);
    await pool.query(`UPDATE run_jobs SET status='running', attempt_count=1, current_attempt_id=$2 WHERE id=$1`, [jobId, attemptId]);
    if (status === 'running') return jobId;
    await pool.query(`UPDATE run_jobs SET status=$2::text::job_status, error_code=$3, result_summary=$4::jsonb, result_config_version_id=$5,
        finished_at=clock_timestamp()-coalesce($6::double precision,0)*interval '1 day' WHERE id=$1`,
    [jobId, status, errorCode, summary === null ? null : JSON.stringify(summary), version, finishedAgo]);
    await pool.query(`UPDATE run_job_attempts SET status=$2::text::attempt_status, finished_at=clock_timestamp() WHERE id=$1`, [attemptId, status === 'succeeded' ? 'succeeded' : 'failed']);
    return jobId;
  };
  const JB = {};
  JB.oldFail = await jobRow('train', { status: 'failed', errorCode: 'worker_unavailable', finishedAgo: 10 });
  JB.trainFail = await jobRow('train', { status: 'failed', errorCode: 'insufficient_complete_training_rows' });
  JB.trainOk = await jobRow('train', { status: 'succeeded', version: base.configId, summary: { configVersionId: base.configId } });
  JB.compareOk = await jobRow('compare', { status: 'succeeded', summary: { comparisonId: id(), itemCount: 3, failedCount: 1,
    replacementRecommendations: [{ modelKey: 'iforest', targetVersionId: base.configId, baselineConfigVersionId: id() }] } });
  JB.compareFail = await jobRow('compare', { status: 'failed', errorCode: 'comparison_all_items_failed' });
  JB.running = await jobRow('train', { status: 'running' });
  JB.retrying = await jobRow('compare', { status: 'queued', errorCode: 'trainer_unavailable' });
  JB.evaluate = await jobRow('evaluate', { status: 'succeeded', summary: {} });
  const jobItems = (body) => body.items.filter((item) => item.type === 'job');
  const adminJobs = await list('view=all&limit=50', admin);
  const shownJobs = byKey({ items: jobItems(adminJobs) });
  assert.deepEqual(Object.keys(shownJobs).sort(), [JB.trainFail, JB.trainOk, JB.compareOk, JB.compareFail].map((jobId) => `work:${jobId}`).sort(),
    'finished train/compare inside 7 days only (no running, retry wait, evaluate, 10-day-old job)');
  assert.deepEqual(shownJobs[`work:${JB.trainFail}`].job, { id: JB.trainFail, kind: 'train', status: 'failed', errorCode: 'insufficient_complete_training_rows',
    finishedAt: shownJobs[`work:${JB.trainFail}`].notifiedAt, configVersionId: null, failedCount: null, replacementRecommended: false });
  assert.deepEqual([shownJobs[`work:${JB.trainOk}`].job.configVersionId, shownJobs[`work:${JB.trainOk}`].reason, shownJobs[`work:${JB.trainOk}`].unread], [base.configId, 'finished', true]);
  assert.deepEqual([shownJobs[`work:${JB.compareOk}`].job.failedCount, shownJobs[`work:${JB.compareOk}`].job.replacementRecommended], [1, true]);
  assert.deepEqual([shownJobs[`work:${JB.compareFail}`].job.errorCode, shownJobs[`work:${JB.compareFail}`].job.replacementRecommended], ['comparison_all_items_failed', false]);
  assert.ok(jobItems(adminJobs).every((item) => BigInt(item.seq) <= BigInt(adminJobs.latestSeq)), 'job sort sequence is on the decision event axis');
  assert.deepEqual(adminJobs.items.map((item) => BigInt(item.seq)), [...adminJobs.items.map((item) => BigInt(item.seq))].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0)),
    'mixed list stays in sequence order');
  const lastFinished = (await pool.query(`SELECT to_char(max(finished_at) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS at FROM run_jobs
    WHERE kind IN ('train','compare') AND status IN ('succeeded','failed')`)).rows[0].at;
  assert.equal(adminJobs.latestJobAt, lastFinished, 'latestJobAt = latest finished train/compare job');
  const adminJobCount = await ok('GET', '/api/notifications/unread-count', admin);
  assert.equal(adminJobCount.unreadByType.job, 4);
  assert.deepEqual(adminJobCount.unreadByType, unreadTypes(adminJobs), 'per-type job count matches the list');
  assert.equal(adminJobCount.unreadCount, Object.values(adminJobCount.unreadByType).reduce((sum, value) => sum + value, 0), 'sum includes jobs');
  assert.equal(adminJobCount.latestJobAt, lastFinished);
  // 커서: 작업 항목이 섞여도 2개씩 끝까지 읽으면 같은 순서.
  const walkedJobs = [];
  let jobCursor = null;
  do {
    const page = await list(`view=all&limit=2${jobCursor ? `&cursor=${jobCursor}` : ''}`, admin);
    walkedJobs.push(...page.items.map((item) => item.key));
    jobCursor = page.nextCursor;
  } while (jobCursor);
  assert.deepEqual(walkedJobs, adminJobs.items.map((item) => item.key), 'keyset walk with job items');
  // 일반 사용자: 작업 항목·수 없음(includeJobs가 true여도).
  const userCount = await ok('GET', '/api/notifications/unread-count', user);
  assert.deepEqual([userCount.unreadByType.job, userCount.latestJobAt, jobItems(await list('view=all&limit=50', user)).length], [0, null, 0], 'job notifications are admin only');
  await ok('POST', '/api/notifications/read', user, { items: [{ key: `work:${JB.trainOk}`, seq: '1' }] });
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_job_notification_reads')).rows[0].n), 0, 'a user cannot mark a job item');
  // 설정 꺼짐: 목록·수에서 빠짐, latestJobAt null. 생략하면 저장값 유지.
  assert.equal((await ok('PUT', '/api/me/settings', admin, { notifications: jobSettings({ includeJobs: false }) })).notifications.includeJobs, false);
  assert.equal((await ok('PUT', '/api/me/settings', admin, { notifications: notif(7) })).notifications.includeJobs, false, 'omitted includeJobs keeps the stored value');
  const off = await list('view=all&limit=50', admin);
  assert.deepEqual([jobItems(off).length, off.latestJobAt, off.settings.includeJobs, (await ok('GET', '/api/notifications/unread-count', admin)).unreadByType.job], [0, null, false, 0]);
  await ok('PUT', '/api/me/settings', admin, { notifications: jobSettings() });
  for (const includeJobs of ['yes', null, 1]) {
    await code('PUT', '/api/me/settings', 400, 'invalid_notification_settings', { notifications: jobSettings({ includeJobs }) }, admin);
  }
  // 기간: 30일이면 10일 전 작업도 보인다.
  await ok('PUT', '/api/me/settings', admin, { notifications: jobSettings({ windowDays: 30 }) });
  assert.ok(jobItems(await list('view=all&limit=50', admin)).some((item) => item.key === `work:${JB.oldFail}`), 'window 30 days shows the 10-day-old job');
  assert.equal((await ok('GET', '/api/notifications/unread-count', admin)).unreadByType.job, 5);
  await ok('PUT', '/api/me/settings', admin, { notifications: jobSettings() });
  // 항목 읽음: 저장(종료 시각), 없는 키 무시.
  const ghostWork = `work:${id()}`;
  const readJob = await ok('POST', '/api/notifications/read', admin, { items: [{ key: `work:${JB.trainFail}`, seq: '9223372036854775807' }, { key: ghostWork, seq: '1' }] });
  assert.equal(readJob.unreadByType.job, 3);
  const jobMarks = (await pool.query('SELECT job_id::text AS j FROM ops_user_job_notification_reads WHERE actor_id=$1', [adminActor])).rows.map((row) => row.j);
  assert.deepEqual(jobMarks, [JB.trainFail], 'only the listed job is stored');
  assert.equal(jobItems(await list('view=unread&limit=50', admin)).some((item) => item.key === `work:${JB.trainFail}`), false);
  // 모두 읽음 형식: jobsThrough는 through와 함께만, 서버 형식(마이크로초 6자리 UTC)만.
  for (const bad of [{ jobsThrough: lastFinished }, { through: '1', jobsThrough: '2026-11-14T00:00:00Z' }, { through: '1', jobsThrough: '2026-02-30T00:00:00.000000Z' },
    { through: '1', jobsThrough: lastFinished, extra: 1 }, { through: '1', jobsThrough: null }]) {
    await code('POST', '/api/notifications/read', 400, 'invalid_read_request', bad, admin);
  }
  // through만(이전 화면): 작업 알림은 그대로 미확인.
  const latestNow = (await ok('GET', '/api/notifications/unread-count', admin)).latestSeq;
  assert.equal((await ok('POST', '/api/notifications/read', admin, { through: latestNow })).unreadByType.job, 3, 'read-all without jobsThrough leaves job items');
  // 미래 시각을 보내도 경계는 지금 가장 늦은 종료 시각으로 줄인다 → 뒤에 끝난 작업은 다시 미확인.
  const allJobsRead = await ok('POST', '/api/notifications/read', admin, { through: latestNow, jobsThrough: '2099-01-01T00:00:00.000000Z' });
  assert.deepEqual([allJobsRead.unreadByType.job, allJobsRead.unreadCount], [0, 0]);
  const cursorRow = (await pool.query(`SELECT to_char(jobs_read_through AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS at FROM ops_user_notification_cursors WHERE actor_id=$1`, [adminActor])).rows[0];
  assert.equal(cursorRow.at, lastFinished, 'jobs read-all boundary is capped at the latest finished job');
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_user_job_notification_reads WHERE actor_id=$1', [adminActor])).rows[0].n), 0,
    'job read marks at or below the boundary are pruned');
  JB.later = await jobRow('compare', { status: 'succeeded', summary: { comparisonId: id(), itemCount: 2, failedCount: 0, replacementRecommendations: [{ modelKey: 'iforest', targetVersionId: null }] } });
  const laterList = await list('view=unread&limit=50', admin);
  assert.deepEqual(jobItems(laterList).map((item) => [item.key, item.job.failedCount, item.job.replacementRecommended]), [[`work:${JB.later}`, 0, false]],
    'a job finished after read-all is unread again; null target is no recommendation');
  // 경계를 되돌리지 않는다(GREATEST).
  await ok('POST', '/api/notifications/read', admin, { through: '0', jobsThrough: '2000-01-01T00:00:00.000000Z' });
  assert.equal((await pool.query(`SELECT to_char(jobs_read_through AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS at FROM ops_user_notification_cursors WHERE actor_id=$1`, [adminActor])).rows[0].at,
    lastFinished, 'jobs boundary never moves back');
  const finalRead = await ok('POST', '/api/notifications/read', admin, { through: laterList.latestSeq, jobsThrough: laterList.latestJobAt });
  assert.equal(finalRead.unreadByType.job, 0);
  console.log('PASS job notifications: train/compare finished only, admin only, includeJobs, window by finish time, item and read-all by finish time');
  console.log('PASS notifications integration');
} finally {
  child?.kill();
  await pool.end();
  await api.end();
  // Windows에서 방금 끝낸 API 프로세스가 작업 폴더를 잠시 잡고 있을 수 있다 — 정리 실패가 시험 실패를 가리지 않게.
  await rm(temporary, { recursive: true, force: true }).catch(() => {});
}
