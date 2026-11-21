// 메일 알림(contracts/email-alerts.md) 통합 시험: 실제 PostgreSQL(새 QA DB qa_email_alerts*, 001~033 적용) + API 역할(powercode_api) + 가짜 전송.
// 실제 메일·외부 연결 없음(전송은 시험이 주입한 가짜). 라우터는 시험 프로세스 안 Express 앱에 붙여 가짜 전송을 쓴다(인증·관리자 검사는 실제 미들웨어).
// 확인: 관리자 전용·기본값, SMTP 저장(비밀번호 쓰기 전용·암호화·키 파일·키 없음 503·키 바뀜), 저장 뒤 연결 확인, 켤 때 지난 판정 제외, 사건 묶음 경계 대기,
// 새 사건 대기열·중복 없음·발송, 구성원 증가 무발송, 위험도 상승 1번, 흐름 머리 변경 시 다시 보내지 않음, 묶이지 않은 판정, 시간당 제한·생략 수,
// 재시도·실패·연결 실패 묶음 멈춤·인증 실패 상태, 멈춘 발송 확인 불가, 시험 발송(목록 밖 404·30초 1번·실패 그대로), 끄기·받는 사람 제거, 발송 목록 커서, 잠금 건너뜀.
// 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { fixtureWriters } from './analysis-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_email_alerts[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_email_alerts database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-email-alerts-'));
// 모듈을 읽기 전에: 작업 폴더를 임시 폴더로(dotenv가 저장소의 .env를 읽지 않게), 인증·계정 저장은 임시 값.
process.chdir(temporary);
Object.assign(process.env, {
  NODE_ENV: 'test', JWT_SECRET: 'qa-email-alerts-local-only-secret', USER_DATA_PATH: path.join(temporary, 'users.json'),
  SEED_ADMIN_EMAIL: 'email-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-email-admin-local-only',
  SEED_USER_EMAIL: 'email-user@example.test', SEED_USER_PASSWORD: 'qa-email-user-local-only',
});
delete process.env.SETTINGS_SECRET_KEY;
const { default: express } = await import('express');
const { default: authRoutes } = await import('../dist/routes/authRoutes.js');
const { createEmailAlertRouter } = await import('../dist/routes/emailAlertRoutes.js');
const { runScan } = await import('../dist/services/emailAlerts/scan.js');
const { sendDue } = await import('../dist/services/emailAlerts/outbox.js');
const { readEmailSettings, updateEmailSettings } = await import('../dist/services/emailAlerts/settingsStore.js');
const { createKeySource } = await import('../dist/services/emailAlerts/secretBox.js');
const { parseSettingsUpdate } = await import('../dist/services/emailAlerts/config.js');
const { readNotifications } = await import('../dist/services/notificationReadModel.js');

const pool = new pg.Pool({ connectionString: url });
const apiUrl = new URL(url);
apiUrl.searchParams.set('options', '-c role=powercode_api');
const api = new pg.Pool({ connectionString: apiUrl.toString() });
const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
const v4 = ({ level = 'critical', grade = 'anomaly', anomalous = true } = {}) => ({
  schemaVersion: 4, anomalous,
  composite: { status: 'complete', anomalous, grade, basisMethods: [],
    risk: anomalous ? { policy: 'risk-max-v1', status: level ? 'rated' : 'unrated', level, determinedBy: [], unratedMethods: [] }
      : { policy: 'risk-max-v1', status: 'not_applicable', level: null, determinedBy: [], unratedMethods: [] } },
});

// 가짜 전송: 보낸 메일·받은 설정을 기록하고, plan에 넣은 오류를 차례로 던진다.
const fake = { sent: [], configs: [], verified: 0, plan: [], verifyPlan: [] };
const factory = async (config) => {
  fake.configs.push(config);
  return {
    async verify() { fake.verified += 1; const step = fake.verifyPlan.shift(); if (step) throw step; },
    async send(message) { const step = fake.plan.shift(); if (step) throw step; fake.sent.push(message); return { smtpCode: 250 }; },
    close() {},
  };
};
const keyFile = path.join(temporary, 'data', 'settings-secret.key');
const keys = createKeySource({ envKey: undefined, filePath: keyFile });
const deps = { factory, keys };
let server;

try {
  await pool.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM run_decision_events')).rows[0].n), 0, 'use a new qa_email_alerts database');
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api');

  // 앱: 메일 알림 라우터(가짜 전송) + 로그인.
  const app = express();
  app.use('/api/email-alerts', createEmailAlertRouter({ database: () => api, deps }));
  app.use(express.json());
  app.use('/api/auth', authRoutes);
  server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const login = async (email, password) => (await (await fetch(`${base}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }) })).json()).token;
  const admin = await login(process.env.SEED_ADMIN_EMAIL, process.env.SEED_ADMIN_PASSWORD);
  const user = await login(process.env.SEED_USER_EMAIL, process.env.SEED_USER_PASSWORD);
  assert.ok(admin && user, 'seeded accounts log in');
  const call = async (method, route, token, body) => {
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    if (body !== undefined) headers['content-type'] = 'application/json';
    const response = await fetch(`${base}${route}`, { method, headers, body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body) });
    const text = await response.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status, body: json, text };
  };
  const ok = async (method, route, body) => {
    const r = await call(method, route, admin, body);
    assert.equal(r.status, 200, `${method} ${route} → ${r.status} ${r.text}`);
    return r.body;
  };
  const code = async (method, route, status, expected, body, token = admin) => {
    const r = await call(method, route, token, body);
    assert.deepEqual([r.status, r.body?.code], [status, expected], `${method} ${route} ${r.text}`);
    return r;
  };

  // 1) 관리자 전용·기본값.
  for (const [method, route] of [['GET', '/api/email-alerts/settings'], ['PUT', '/api/email-alerts/settings'], ['POST', '/api/email-alerts/test'], ['GET', '/api/email-alerts/deliveries']]) {
    await code(method, route, 401, 'authentication_required', undefined, null);
    await code(method, route, 403, 'admin_required', method === 'GET' ? undefined : {}, user);
  }
  const defaults = await ok('GET', '/api/email-alerts/settings');
  assert.deepEqual([defaults.enabled, defaults.rules, defaults.recipients, defaults.smtp.passwordSet, defaults.transport.status, defaults.isDefault, defaults.publicBaseUrl],
    [false, { kinds: ['incident'], minRiskLevel: 'critical' }, [], false, 'not_configured', true, null]);
  assert.deepEqual(defaults.limits, { recipients: 10, ports: [25, 465, 587, 2525], security: ['tls', 'starttls'] });
  await code('GET', '/api/email-alerts/settings?x=1', 400, 'unknown_email_alert_filter');
  console.log('PASS admin only, defaults');

  // 2) 저장·검증·비밀번호.
  const PASSWORD = 'qa-smtp-Pass word!';
  const settings = (patch = {}, smtp = {}) => ({ enabled: false, rules: { kinds: ['incident'], minRiskLevel: 'critical' },
    recipients: ['ops@example.test', 'oncall@example.test'],
    smtp: { host: 'smtp.example.test', port: 587, security: 'starttls', username: 'alerts', fromAddress: 'alerts@example.test', ...smtp },
    publicBaseUrl: 'http://dash.example.test:8080/', ...patch });
  await code('PUT', '/api/email-alerts/settings', 409, 'email_transport_not_configured', settings({ enabled: true }, { host: null }));
  // 계정은 있는데 저장된 비밀번호가 없으면 켤 수 없다(smtp_password_missing).
  await code('PUT', '/api/email-alerts/settings', 409, 'email_transport_not_configured', settings({ enabled: true }));
  await code('PUT', '/api/email-alerts/settings', 400, 'invalid_smtp_port', settings({}, { port: 5432 }));
  await code('PUT', '/api/email-alerts/settings', 400, 'smtp_host_blocked', settings({}, { host: '127.0.0.1' }));
  const badRecipient = await code('PUT', '/api/email-alerts/settings', 400, 'invalid_email_recipient', settings({ recipients: ['ops@example.test', 'x\r\n@y'] }));
  assert.equal(badRecipient.body.index, 1);
  await code('PUT', '/api/email-alerts/settings', 400, 'invalid_json', '{"enabled":');
  assert.equal(fs.existsSync(keyFile), false, 'no key file before a password is saved');
  // 키를 만들 수 없는 곳(파일 아래 폴더) → 503, 저장 안 됨.
  fs.writeFileSync(path.join(temporary, 'not-a-dir'), '');
  const noKeys = createKeySource({ envKey: undefined, filePath: path.join(temporary, 'not-a-dir', 'k.key') });
  await assert.rejects(updateEmailSettings(api, 'qa-admin', parseSettingsUpdate(settings({}, { password: PASSWORD })), noKeys),
    (e) => e.code === 'email_secret_key_unavailable' && e.status === 503 && !String(e.message).includes(PASSWORD));
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM ops_email_alert_settings')).rows[0].n, 0, 'failed save stores nothing');

  const saved = await call('PUT', '/api/email-alerts/settings', admin, settings({}, { password: PASSWORD }));
  assert.equal(saved.status, 200, saved.text);
  assert.equal(saved.text.includes(PASSWORD), false, 'password never echoed');
  assert.deepEqual([saved.body.smtp.passwordSet, saved.body.verify, saved.body.transport.status, saved.body.publicBaseUrl, fake.verified],
    [true, { result: 'ok', code: null }, 'configured', 'http://dash.example.test:8080', 1]);
  assert.ok(saved.body.transport.checkedAt, 'verify result recorded');
  assert.deepEqual([fake.configs.at(-1).password, fake.configs.at(-1).host, fake.configs.at(-1).security], [PASSWORD, 'smtp.example.test', 'starttls']);
  const stored = (await pool.query('SELECT smtp_password_enc AS enc FROM ops_email_alert_settings')).rows[0].enc;
  assert.ok(stored.startsWith('v1.') && !stored.includes(PASSWORD), 'stored sealed, not plaintext');
  assert.ok(fs.existsSync(keyFile), 'key file created on the first password save');
  if (process.platform !== 'win32') assert.equal(fs.statSync(keyFile).mode & 0o777, 0o600);
  // 비밀번호 생략 = 유지, SMTP 그대로면 연결 확인 생략.
  const kept = await ok('PUT', '/api/email-alerts/settings', settings({ rules: { kinds: ['incident'], minRiskLevel: 'critical' } }));
  assert.deepEqual([kept.smtp.passwordSet, kept.verify.result, fake.verified], [true, 'skipped', 1]);
  assert.equal((await pool.query('SELECT smtp_password_enc AS enc FROM ops_email_alert_settings')).rows[0].enc, stored);
  // 연결 확인 실패는 저장을 되돌리지 않는다.
  fake.verifyPlan.push({ code: 'EAUTH', responseCode: 535, message: 'Invalid login secret' });
  const failedVerify = await ok('PUT', '/api/email-alerts/settings', settings({}, { password: PASSWORD + '2' }));
  assert.deepEqual([failedVerify.verify, failedVerify.transport.status, failedVerify.transport.code, failedVerify.smtp.passwordSet],
    [{ result: 'auth_failed', code: 'smtp_auth_failed' }, 'auth_failed', 'smtp_auth_failed', true]);
  const restored = await ok('PUT', '/api/email-alerts/settings', settings({}, { password: PASSWORD }));
  assert.equal(restored.transport.status, 'configured');
  // 키가 바뀌면(키 분실) 비밀번호를 풀 수 없음 → invalid, 다시 넣어야 함.
  const otherKey = createKeySource({ envKey: Buffer.alloc(32, 42).toString('base64'), filePath: keyFile });
  assert.deepEqual((await readEmailSettings(api, otherKey)).transport, { status: 'invalid', code: 'smtp_password_unreadable', checkedAt: null });
  console.log('PASS settings save, write-only sealed password, key file, verify on save');

  // 3) 시험 데이터 쓰기 도구.
  const client = await pool.connect();
  const out = { decisions: {}, jobs: {} };
  /** 원본 1개(+ 구성 버전)와 그 원본의 판정 쓰기 도구. */
  const newSource = async () => {
    const source = id(), config = id();
    await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
      VALUES($1,$2,$2,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["T","P","M","B"]}',clock_timestamp())`, [source, `email-${source}`]);
    await client.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,'{}','{}',$3,'email-fixture',clock_timestamp())`, [config, source, sha(config)]);
    return { sourceId: source, writers: fixtureWriters(client, source, config, out) };
  };
  const { sourceId, writers: w } = await newSource();
  let serial = 0;
  const decision = async (level = 'critical', minutes = 5) => { serial += 1; return w.legacyDecision(`d${serial}`, minutes, 'complete', v4({ level })); };
  const seqOf = async (decisionId) => (await pool.query('SELECT id::text AS n FROM run_decision_events WHERE decision_id=$1', [decisionId])).rows[0].n;
  /** 사건 묶음 작업 1회차(019 순서: 사건 → 구성원 → 병합 → 실행 행, 한 트랜잭션). cursorTo = 이 회차가 읽은 마지막 이벤트. */
  const incidentRun = async ({ incidents = [], members = [], merges = [], cursorTo, criteriaVersion = 'incident-gap-v1' }) => {
    const run = id();
    await client.query('BEGIN');
    for (const i of incidents) {
      await client.query(`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,time_basis,opened_at,continues_from,added_by_run)
        VALUES($1,$2,'{"gapSeconds":300}',$7,$3,'decided_at',clock_timestamp()-($4::double precision*interval '1 minute'),$5,$6)`,
      [i.id, sourceId, i.groupKey ?? 'response_code:E500', i.minutesAgo ?? 5, i.continuesFrom ?? null, run, criteriaVersion]);
    }
    for (const [incidentId, decisionId] of members) {
      await client.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,clock_timestamp(),'decided_at',$3)`,
        [decisionId, incidentId, run]);
    }
    for (const [from, to] of merges) {
      await client.query(`INSERT INTO run_incident_events(incident_id,kind,target_incident_id,added_by_run) VALUES($1,'merged_into',$2,$3)`, [from, to, run]);
    }
    await client.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,$7,0,$3,$4,$5,$6,clock_timestamp())`, [run, sourceId, cursorTo, members.length, incidents.length, merges.length, criteriaVersion]);
    await client.query('COMMIT');
  };
  const latest = async () => (await pool.query('SELECT coalesce(max(id),0)::text AS n FROM run_decision_events')).rows[0].n;
  const deliveries = async () => (await pool.query(`SELECT d.id,d.kind,d.reason,d.risk_rank,d.recipient,d.status,d.attempts,d.error_code,d.payload,a.alert_key
    FROM ops_email_deliveries d LEFT JOIN ops_email_alerts a ON a.id=d.alert_id ORDER BY d.created_at,d.recipient`)).rows;
  const byRecipient = (list) => [...list].sort((x, y) => x.recipient.localeCompare(y.recipient));
  const scan = (options) => runScan(api, options);
  const send = () => sendDue(api, deps);
  const I = {}, D = {};

  try {
    // 4) 켜기 전 사건은 보내지 않는다.
    D.old = await decision('critical', 30);
    I.old = id();
    await incidentRun({ incidents: [{ id: I.old, minutesAgo: 30 }], members: [[I.old, D.old]], cursorTo: await seqOf(D.old) });
    assert.equal((await scan()).status, 'disabled');
    const enabled = await ok('PUT', '/api/email-alerts/settings', settings({ enabled: true }));
    assert.deepEqual([enabled.enabled, enabled.processing.processedThroughSeq], [true, await latest()], 'armed at the newest event');
    assert.equal((await scan()).queued, 0);
    assert.equal((await deliveries()).length, 0, 'incidents before enabling are not mailed');

    // 5) 사건 묶음 경계 전이면 기다리고, 경계를 지나면 새 사건 1건 = 받는 사람마다 1행.
    D.a1 = await decision('critical');
    const waiting = await scan();
    assert.deepEqual([waiting.waiting, waiting.queued], [true, 0], 'membership not decided yet → wait');
    assert.ok(BigInt(waiting.processedThrough) < BigInt(await seqOf(D.a1)));
    I.a = id();
    await incidentRun({ incidents: [{ id: I.a }], members: [[I.a, D.a1]], cursorTo: await seqOf(D.a1) });
    assert.equal((await scan()).queued, 1);
    let rows = await deliveries();
    assert.deepEqual(byRecipient(rows).map((r) => [r.kind, r.reason, r.risk_rank, r.recipient, r.status, r.alert_key]),
      [['incident', 'opened', 3, 'oncall@example.test', 'queued', `incident:${I.a}`], ['incident', 'opened', 3, 'ops@example.test', 'queued', `incident:${I.a}`]]);
    assert.equal((await scan()).queued, 0, 'a second scan queues nothing');
    assert.equal((await deliveries()).length, 2);
    const summary = await send();
    assert.deepEqual([summary.sent, summary.retrying, summary.failed], [2, 0, 0]);
    const mail = fake.sent.find((m) => m.to === 'ops@example.test');
    assert.equal(mail.subject, '[ESB 알림] 위험 · 사건 · 응답코드 E500');
    assert.ok(mail.text.includes(`사건 보기: http://dash.example.test:8080/analysis/incidents/${I.a}`), mail.text);
    assert.ok(mail.text.includes('묶인 판정 1건 · 거래 1건'), mail.text);
    assert.equal(/TX-/.test(mail.text + mail.html), false, 'no transaction ids in mail');
    assert.ok((await deliveries()).every((r) => r.status === 'sent'));
    const notif = await readNotifications(api, 'email-consistency', { decisionBasis: 'final_any', minRiskLevel: 'critical', includeUnrated: false, windowDays: 7, includeJobs: false },
      { view: 'all', limit: 50, cursor: null });
    assert.ok(notif.items.some((item) => item.key === `incident:${I.a}`), 'the mailed flow is the same incident item the notification panel shows');
    console.log('PASS arming, incident boundary wait, queue once per recipient, send');

    // 6) 구성원 증가만으로는 보내지 않는다.
    D.a2 = await decision('critical');
    await incidentRun({ members: [[I.a, D.a2]], cursorTo: await seqOf(D.a2) });
    assert.equal((await scan()).queued, 0);
    assert.equal((await deliveries()).length, 2, 'growth sends nothing');

    // 7) 하한 주의: 주의로 새 사건 → 위험 구성원이 들어오면 위험도 상승 1번.
    await ok('PUT', '/api/email-alerts/settings', settings({ enabled: true, rules: { kinds: ['incident'], minRiskLevel: 'warning' } }));
    D.b1 = await decision('warning');
    I.b = id();
    await incidentRun({ incidents: [{ id: I.b, groupKey: 'channel_id:CH-9' }], members: [[I.b, D.b1]], cursorTo: await seqOf(D.b1) });
    D.b2 = await decision('critical');
    D.b3 = await decision('critical');
    await incidentRun({ members: [[I.b, D.b2], [I.b, D.b3]], cursorTo: await seqOf(D.b3) });
    assert.equal((await scan()).queued, 2);
    rows = (await deliveries()).filter((r) => r.alert_key === `incident:${I.b}`);
    assert.deepEqual(rows.map((r) => [r.reason, r.risk_rank]).sort(), [['opened', 2], ['opened', 2], ['risk_raised', 3], ['risk_raised', 3]]);
    await send();
    assert.ok(fake.sent.some((m) => m.subject === '[ESB 알림] 위험 · 사건 · 채널 CH-9 · 위험도 상승'));
    console.log('PASS growth silent, risk raised once');

    // 8) 흐름 머리 변경: I.c(보냄)가 더 이른 I.d에 병합되면 흐름 머리는 I.d지만 같은 흐름이라 다시 보내지 않는다.
    D.c1 = await decision('critical');
    I.c = id();
    await incidentRun({ incidents: [{ id: I.c, groupKey: 'response_code:E777', minutesAgo: 10 }], members: [[I.c, D.c1]], cursorTo: await seqOf(D.c1) });
    assert.equal((await scan()).queued, 1);
    D.d1 = await decision('info');
    I.d = id();
    await incidentRun({ incidents: [{ id: I.d, groupKey: 'response_code:E777', minutesAgo: 60 }], members: [[I.d, D.d1]], merges: [[I.c, I.d]], cursorTo: await seqOf(D.d1) });
    D.d2 = await decision('critical');
    await incidentRun({ members: [[I.d, D.d2]], cursorTo: await seqOf(D.d2) });
    assert.equal((await scan()).queued, 0, 'same flow under a new head is not a new incident');
    const keysNow = (await pool.query('SELECT alert_key FROM ops_email_alerts')).rows.map((r) => r.alert_key);
    assert.equal(keysNow.includes(`incident:${I.d}`), false);
    const mapped = (await pool.query('SELECT count(*)::int AS n FROM ops_email_alert_incidents WHERE incident_id=$1', [I.d])).rows[0].n;
    assert.equal(mapped, 1, 'the new head is mapped to the existing alert');
    await send();
    console.log('PASS flow head change does not re-send');

    // 9) 묶이지 않은 판정(설정에서 켤 때만).
    D.e = await decision('critical');
    await incidentRun({ cursorTo: await seqOf(D.e) });
    assert.equal((await scan()).queued, 0, 'unbundled decisions are off by default');
    await ok('PUT', '/api/email-alerts/settings', settings({ enabled: true, rules: { kinds: ['incident', 'decision'], minRiskLevel: 'warning' } }));
    D.f = await decision('critical');
    await incidentRun({ cursorTo: await seqOf(D.f) });
    assert.equal((await scan()).queued, 1);
    assert.ok((await deliveries()).some((r) => r.alert_key === `decision:${D.f}` && r.kind === 'decision'));
    await send();
    const decisionMail = fake.sent.at(-1);
    assert.ok(decisionMail.text.includes(`판정 보기: http://dash.example.test:8080/analysis/d/${D.f}`), decisionMail.text);
    console.log('PASS unbundled decisions only when selected');

    // 10) 시간당 제한: 상한을 넘은 새 사건은 생략, 한 시간 뒤 다음 메일에 생략 수.
    const usedNow = (await readEmailSettings(api, keys)).rateLimit.newAlertsLastHour;
    const limited = [];
    for (let i = 0; i < 2; i += 1) {
      const d = await decision('critical');
      const incident = id();
      limited.push(incident);
      await incidentRun({ incidents: [{ id: incident, groupKey: `response_code:R${i}` }], members: [[incident, d]], cursorTo: await seqOf(d) });
    }
    const capped = await scan({ perHour: usedNow + 1 });
    assert.deepEqual([capped.queued, capped.suppressed], [1, 1]);
    rows = await deliveries();
    assert.equal(rows.filter((r) => r.alert_key === `incident:${limited[1]}`).every((r) => r.status === 'suppressed'), true);
    await send();
    await pool.query(`UPDATE ops_email_deliveries SET created_at=created_at-interval '2 hours'`);
    const dNext = await decision('critical');
    const iNext = id();
    await incidentRun({ incidents: [{ id: iNext, groupKey: 'response_code:NEXT' }], members: [[iNext, dNext]], cursorTo: await seqOf(dNext) });
    assert.equal((await scan({ perHour: 1 })).queued, 1);
    const nextRow = (await deliveries()).find((r) => r.alert_key === `incident:${iNext}`);
    assert.deepEqual(nextRow.payload.suppressed, { incident: 1, decision: 0 });
    await send();
    assert.ok(fake.sent.at(-1).text.includes('발송 제한으로 생략된 사건 1건이 있습니다.'));
    console.log('PASS hourly cap, suppressed rows, omitted count in the next mail');

    // 11) 재시도·실패·연결 실패 묶음·인증 실패 상태·멈춘 발송.
    await pool.query(`UPDATE ops_email_deliveries SET created_at=created_at-interval '2 hours'`);
    const dRetry = await decision('critical');
    const iRetry = id();
    await incidentRun({ incidents: [{ id: iRetry, groupKey: 'response_code:RETRY' }], members: [[iRetry, dRetry]], cursorTo: await seqOf(dRetry) });
    assert.equal((await scan()).queued, 1);
    fake.plan.push({ code: 'ECONNECTION', originalCode: 'ECONNREFUSED' });
    const before = fake.sent.length;
    const broken = await send();
    assert.deepEqual([broken.retrying, broken.sent, fake.sent.length], [2, 0, before], 'a connection failure stops the batch and retries both');
    rows = (await deliveries()).filter((r) => r.alert_key === `incident:${iRetry}`);
    assert.ok(rows.every((r) => r.status === 'retrying' && r.attempts === 1 && r.error_code === 'smtp_connection_failed'));
    const next = (await pool.query(`SELECT extract(epoch FROM min(next_attempt_at-clock_timestamp()))::float8 AS s FROM ops_email_deliveries WHERE status='retrying'`)).rows[0].s;
    assert.ok(next > 30 && next <= 60, `retry about one minute later (${next} s)`);
    assert.equal((await send()).sent, 0, 'not due yet');
    await pool.query(`UPDATE ops_email_deliveries SET next_attempt_at=clock_timestamp() WHERE status='retrying'`);
    fake.plan.push({ code: 'EAUTH', responseCode: 535 });
    const auth = await send();
    assert.deepEqual([auth.failed, auth.sent], [2, 0], 'auth failure is permanent and stops the batch');
    assert.deepEqual([(await readEmailSettings(api, keys)).transport.status, (await readEmailSettings(api, keys)).transport.code], ['auth_failed', 'smtp_auth_failed']);
    const stale = rows[0].id;
    await pool.query(`UPDATE ops_email_deliveries SET status='sending',lease_until=clock_timestamp()-interval '1 minute',finished_at=NULL WHERE id=$1`, [stale]);
    const sentBefore = fake.sent.length;
    const recovered = await send();
    assert.equal(recovered.recovered, 1);
    const staleRow = (await pool.query('SELECT status,error_code FROM ops_email_deliveries WHERE id=$1', [stale])).rows[0];
    assert.deepEqual([staleRow.status, staleRow.error_code, fake.sent.length], ['unknown', 'send_interrupted', sentBefore], 'interrupted sends are marked unknown and not re-sent');
    // 보내는 도중 응답 없이 끊김(소켓 시간 초과) → 결과 모름, 다시 보내지 않음. 같은 묶음의 다음 행은 계속 보낸다. 연결 시간 초과는 다시 시도.
    await pool.query(`UPDATE ops_email_deliveries SET created_at=created_at-interval '2 hours'`);
    const dUnknown = await decision('critical');
    const iUnknown = id();
    await incidentRun({ incidents: [{ id: iUnknown, groupKey: 'response_code:UNKNOWN' }], members: [[iUnknown, dUnknown]], cursorTo: await seqOf(dUnknown) });
    assert.equal((await scan()).queued, 1);
    fake.plan.push({ code: 'ETIMEDOUT', timeoutType: 'SOCKET_TIMEOUT' });
    const sentBeforeUnknown = fake.sent.length;
    const unknownRun = await send();
    assert.deepEqual([unknownRun.unknown, unknownRun.sent, fake.sent.length], [1, 1, sentBeforeUnknown + 1]);
    rows = (await deliveries()).filter((r) => r.alert_key === `incident:${iUnknown}`);
    const unknownRow = rows.find((r) => r.status === 'unknown');
    assert.deepEqual([unknownRow.error_code, unknownRow.attempts], ['smtp_result_unknown', 1]);
    assert.equal((await pool.query('SELECT next_attempt_at FROM ops_email_deliveries WHERE id=$1', [unknownRow.id])).rows[0].next_attempt_at, null);
    await pool.query(`UPDATE ops_email_deliveries SET next_attempt_at=clock_timestamp() WHERE id=$1`, [unknownRow.id]).catch(() => {});
    const again2 = await send();
    assert.deepEqual([again2.sent, again2.unknown, fake.sent.length], [0, 0, sentBeforeUnknown + 1], 'an unknown result is never picked up again');
    const dConnect = await decision('critical');
    const iConnect = id();
    await incidentRun({ incidents: [{ id: iConnect, groupKey: 'response_code:CONNECT' }], members: [[iConnect, dConnect]], cursorTo: await seqOf(dConnect) });
    assert.equal((await scan()).queued, 1);
    fake.plan.push({ code: 'ETIMEDOUT', timeoutType: 'CONNECT_TIMEOUT' });
    const connectRun = await send();
    assert.deepEqual([connectRun.retrying, connectRun.unknown], [2, 0], 'a connect timeout is retried (batch stops, both retry)');
    console.log('PASS retry, permanent failure, batch stop, auth status, interrupted send → unknown, result unknown never retried');

    // 12) 시험 발송.
    await code('POST', '/api/email-alerts/test', 404, 'email_recipient_not_listed', { to: 'stranger@example.test' });
    await code('POST', '/api/email-alerts/test', 400, 'invalid_test_request', { to: 'ops@example.test', cc: 'x@example.test' });
    const test1 = await ok('POST', '/api/email-alerts/test', { to: 'OPS@example.test' });
    assert.deepEqual([test1.status, test1.errorCode], ['sent', null]);
    assert.deepEqual([fake.sent.at(-1).subject, fake.sent.at(-1).to], ['[ESB 알림] 시험 메일', 'ops@example.test']);
    assert.equal((await readEmailSettings(api, keys)).transport.status, 'configured', 'a successful test send clears the auth failure');
    await code('POST', '/api/email-alerts/test', 429, 'email_test_rate_limited', { to: 'ops@example.test' });
    await pool.query(`UPDATE ops_email_deliveries SET created_at=created_at-interval '1 minute' WHERE kind='test'`);
    fake.plan.push({ code: 'EAUTH', responseCode: 535, message: 'secret' });
    const test2 = await ok('POST', '/api/email-alerts/test', { to: 'ops@example.test' });
    assert.deepEqual([test2.status, test2.errorCode, test2.smtpCode], ['failed', 'smtp_auth_failed', 535], 'failure is reported as failure');
    console.log('PASS test send: listed only, 30 s limit, failure reported');

    // 13) 발송 목록.
    const page1 = await ok('GET', '/api/email-alerts/deliveries?limit=2');
    assert.equal(page1.items.length, 2);
    assert.deepEqual([page1.items[0].kind, page1.items[0].status], ['test', 'failed']);
    const page2 = await ok('GET', `/api/email-alerts/deliveries?limit=2&cursor=${page1.nextCursor}`);
    assert.equal(page2.items.some((item) => page1.items.some((p) => p.id === item.id)), false);
    await code('GET', '/api/email-alerts/deliveries?cursor=zzz', 400, 'invalid_cursor');
    await code('GET', '/api/email-alerts/deliveries?limit=51', 400, 'invalid_limit');
    assert.equal(JSON.stringify(page1).includes('payload'), false);

    // 14) 받는 사람 제거·끄기는 대기 발송을 실패로.
    await pool.query(`UPDATE ops_email_deliveries SET created_at=created_at-interval '2 hours'`);
    const dLate = await decision('critical');
    const iLate = id();
    await incidentRun({ incidents: [{ id: iLate, groupKey: 'response_code:LATE' }], members: [[iLate, dLate]], cursorTo: await seqOf(dLate) });
    assert.equal((await scan()).queued, 1);
    await ok('PUT', '/api/email-alerts/settings', settings({ enabled: true, recipients: ['ops@example.test'], rules: { kinds: ['incident', 'decision'], minRiskLevel: 'warning' } }));
    rows = byRecipient((await deliveries()).filter((r) => r.alert_key === `incident:${iLate}`));
    assert.deepEqual(rows.map((r) => [r.recipient, r.status, r.error_code]),
      [['oncall@example.test', 'failed', 'email_recipient_removed'], ['ops@example.test', 'queued', null]]);
    await ok('PUT', '/api/email-alerts/settings', settings({ recipients: ['ops@example.test'] }));
    rows = byRecipient((await deliveries()).filter((r) => r.alert_key === `incident:${iLate}`));
    assert.deepEqual(rows.map((r) => [r.status, r.error_code]), [['failed', 'email_recipient_removed'], ['failed', 'email_alerts_disabled']]);
    assert.equal((await scan()).status, 'disabled');
    console.log('PASS recipient removal and disabling fail pending deliveries');

    // 15) 다른 회차가 잠금을 잡고 있으면 건너뜀.
    await ok('PUT', '/api/email-alerts/settings', settings({ enabled: true, recipients: ['ops@example.test'] }));
    const holder = await pool.connect();
    try {
      await holder.query('BEGIN');
      await holder.query('SELECT pg_advisory_xact_lock(81743,0)');
      assert.equal((await scan()).status, 'skipped');
      await holder.query('ROLLBACK');
    } finally { holder.release(); }
    assert.equal((await scan()).status, 'completed');
    console.log('PASS scan lock');

    // 16) 사건 묶음 경계 = 원본별, 기준 버전마다 최신 cursor_to의 최댓값. 이전 버전(v0)의 cursor_to가 더 커도 그 값까지 판단한다.
    //     v0 회차(큰 cursor_to)를 먼저, v1 회차(작은 cursor_to)를 나중에 넣는다 → "마지막에 넣은 회차"로 경계를 정하는 구현은 기다리게 되어 실패한다.
    const latestV1 = (await pool.query(`SELECT max(cursor_to)::text AS n FROM run_incident_runs WHERE source_id=$1 AND criteria_version='incident-gap-v1'`, [sourceId])).rows[0].n;
    const dV0 = await decision('critical');
    const iV0 = id();
    await incidentRun({ incidents: [{ id: iV0, groupKey: 'response_code:V0' }], members: [[iV0, dV0]], cursorTo: await seqOf(dV0), criteriaVersion: 'incident-gap-v0' });
    await incidentRun({ cursorTo: latestV1 });
    assert.ok(BigInt(latestV1) < BigInt(await seqOf(dV0)), 'the newer version boundary is behind the decision');
    const lastRun = (await pool.query(`SELECT criteria_version,cursor_to::text AS c FROM run_incident_runs WHERE source_id=$1 ORDER BY finished_at DESC LIMIT 1`, [sourceId])).rows[0];
    assert.deepEqual([lastRun.criteria_version, lastRun.c], ['incident-gap-v1', latestV1], 'the most recently inserted run is the lower-cursor v1 run');
    const crossVersion = await scan();
    assert.deepEqual([crossVersion.waiting, crossVersion.queued], [false, 1], 'the older version run with the larger cursor_to sets the boundary');
    assert.ok((await deliveries()).some((r) => r.alert_key === `incident:${iV0}`));
    // 사건 묶음 실행 행이 하나도 없는 원본의 판정은 소속을 알 수 없으므로 기다린다(다른 원본의 경계를 쓰지 않음).
    // 첫 원본에 그 판정 순번 이상의 cursor_to 회차를 넣어 둔다 → 원본을 가리지 않고 경계를 정하는 구현은 이 판정을 처리해 실패한다.
    const other = await newSource();
    const dOther = await other.writers.legacyDecision('other-source', 1, 'complete', v4({ level: 'critical' }));
    await incidentRun({ cursorTo: await seqOf(dOther) });
    const noRuns = await scan();
    assert.deepEqual([noRuns.waiting, noRuns.queued], [true, 0], 'a source without incident runs waits');
    assert.ok(BigInt(noRuns.processedThrough) < BigInt(await seqOf(dOther)));
    console.log('PASS boundary across criteria versions, source without runs waits');
  } finally {
    client.release();
  }
  console.log('PASS email alerts integration');
} finally {
  if (server) await new Promise((resolve) => server.close(resolve));
  await api.end();
  await pool.end();
  process.chdir(os.tmpdir());
  await rm(temporary, { recursive: true, force: true });
}
