// 응답 코드 사전(contracts/response-codes-ui.md) 통합 시험. 로컬 새 QA DB(qa_response_codes*)에 001~023·025가 적용돼 있어야 한다.
// API 프로세스·가져오기 도구는 powercode_api 역할로 접속한다(연결 options role). 확인: 가져오기(잘못된 행이면 반영 없음, 건수만 출력, 다시 실행 무변경,
// dry-run, 고친 행 보존·--force), 인증·권한, 목록(코드 포인트 순·검색·유형·커서·사전에 없는 byCode 코드), 상세(코드별 위험도·위험도 바뀐 룰 세트 버전·메시지 기록),
// 메시지 편집(409 충돌·무변경, 사전에 없는 코드 새 행), 이상 분석 항목의 응답 코드 메시지. 값은 모두 합성. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { seedAnalysisFixture } from './analysis-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_response_codes[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_response_codes database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-response-codes-'));
const pool = new pg.Pool({ connectionString: url });
const apiUrl = new URL(url);
apiUrl.searchParams.set('options', '-c role=powercode_api');
const api = new pg.Pool({ connectionString: apiUrl.toString() });
let child;

const MESSAGES = { T001: '합성 메시지 일', T002: '합성 메시지 이', E500: '합성 서버 오류', 10: '숫자처럼 생긴 합성 코드' };
const csvFile = async (name, lines) => {
  const file = path.join(temporary, name);
  await writeFile(file, `﻿${lines.join('\r\n')}\r\n`, 'utf8');
  return file;
};
const HEADER = '코드명 (Enum),유형 (Type),에러코드,HTTP 상태,메시지 키,메시지 (한국어)';
const goodLines = (overrides = {}) => [
  HEADER, '합성 묶음 (SYNTHETIC),,,,,',
  `SYN_ONE,SYN_A,T001,500 SYN,syn.one,${overrides.T001 ?? MESSAGES.T001}`,
  `SYN_TWO,SYN_A,T002,400 SYN,syn.two,${MESSAGES.T002}`,
  '다른 묶음,,,,,',
  `SYN_SERVER,SYN_B,E500,500 SYN,syn.server,"${MESSAGES.E500}"`,
  `SYN_DIGITS,SYN_B,10,200 SYN,syn.digits,${MESSAGES[10]}`,
];
const runImport = (file, ...flags) => {
  const result = spawnSync(process.execPath, [path.join(backend, 'dist/scripts/importResponseCodes.js'), file, ...flags], {
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, DATABASE_URL: apiUrl.toString() }, encoding: 'utf8',
  });
  const output = `${result.stdout}${result.stderr}`;
  for (const message of [...Object.values(MESSAGES), '합성 겹침', 'SYN_ONE', 'syn.one', 'SYN_M', 'syn.m']) assert.ok(!output.includes(message), 'the tool prints counts only, never values');
  return { status: result.status, output };
};
const line = (output, label) => output.split(/\r?\n/).find((item) => item.startsWith(label)) ?? '';

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

try {
  await pool.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ops_response_codes')).rows[0].n), 0, 'use a new qa_response_codes database');
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api', 'API connection string runs as the runtime role');
  const dictionary = async () => (await pool.query(`SELECT code,message,origin,revision FROM ops_response_codes ORDER BY code COLLATE "C"`)).rows;

  // 1) 가져오기 도구: 잘못된 행이 있으면 아무것도 쓰지 않는다(종료 2). 출력은 건수·행 번호·사유 코드만.
  {
    const bad = await runImport(await csvFile('bad.csv', [...goodLines(), 'SYN_BAD,SYN_A,T 9,500,syn.bad,공백 코드', 'SYN_EMPTY,SYN_A,T008,500,syn.empty,']));
    assert.equal(bad.status, 2, bad.output);
    assert.equal(line(bad.output, 'invalid rows'), 'invalid rows: 2 (row 8 code_invalid, row 9 message_missing)');
    assert.deepEqual(await dictionary(), []);
    const usage = await runImport(path.join(temporary, 'missing.csv'));
    assert.deepEqual([usage.status, line(usage.output, 'file error')], [1, 'file error: ENOENT']);
    assert.equal((await runImport(path.join(temporary, 'codes.txt'))).status, 1);
  }
  const good = await csvFile('good.csv', goodLines());
  {
    const first = await runImport(good);
    assert.equal(first.status, 0, first.output);
    assert.deepEqual(['rows read', 'group title rows skipped', 'invalid rows', 'merged codes', 'codes to import', 'imported'].map((label) => line(first.output, label)), [
      'rows read: 6', 'group title rows skipped: 2', 'invalid rows: 0', 'merged codes: 0', 'codes to import: 4',
      'imported: inserted 4, updated 0, unchanged 0, kept edited 0, overwritten edited 0']);
    assert.deepEqual((await dictionary()).map((row) => [row.code, row.message, row.origin, row.revision]),
      [['10', MESSAGES[10], 'import', 1], ['E500', MESSAGES.E500, 'import', 1], ['T001', MESSAGES.T001, 'import', 1], ['T002', MESSAGES.T002, 'import', 1]]);
    assert.equal(line((await runImport(good)).output, 'imported'), 'imported: inserted 0, updated 0, unchanged 4, kept edited 0, overwritten edited 0');
    const changed = await csvFile('changed.csv', goodLines({ T001: '합성 메시지 일 개정' }));
    assert.equal(line((await runImport(changed, '--dry-run')).output, 'dry run'), 'dry run (rolled back): inserted 0, updated 1, unchanged 3, kept edited 0, overwritten edited 0');
    assert.equal((await dictionary()).find((row) => row.code === 'T001').message, MESSAGES.T001, 'dry run writes nothing');
    assert.equal(line((await runImport(changed)).output, 'imported'), 'imported: inserted 0, updated 1, unchanged 3, kept edited 0, overwritten edited 0');
    assert.deepEqual((await dictionary()).find((row) => row.code === 'T001'), { code: 'T001', message: '합성 메시지 일 개정', origin: 'import', revision: 2 });
    assert.equal(line((await runImport(good)).output, 'imported'), 'imported: inserted 0, updated 1, unchanged 3, kept edited 0, overwritten edited 0');
  }

  // 2) API 서버(powercode_api 연결)
  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: apiUrl.toString(),
    JWT_SECRET: 'qa-response-codes-local-only-secret',
    SEED_USER_EMAIL: 'codes-user@example.test', SEED_USER_PASSWORD: 'qa-codes-local-only-password',
    SEED_ADMIN_EMAIL: 'codes-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-codes-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: 'ignore' });
  const base = `http://127.0.0.1:${environment.PORT}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 서버 시작 대기 */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const call = async (route, { method = 'GET', token, body, raw } = {}) => {
    const response = await fetch(`${base}${route}`, {
      method, body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined || raw ? { 'Content-Type': 'application/json' } : {}) },
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
  const list = async (query = '', token = admin) => {
    const result = await call(`/api/response-codes${query}`, { token });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    return result.body;
  };
  const detail = async (code, token = admin) => {
    const result = await call(`/api/response-codes/${encodeURIComponent(code)}`, { token });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    return result.body;
  };
  /** 룰 세트 저장으로 hard:transaction-failed 위험도만 바꾼다(화면과 같은 전체 상태 치환). */
  const saveFailedSeverity = async (severity, extra = {}) => {
    const state = (await call('/api/rule-set', { token: admin })).body;
    const hardRules = state.hardRules.map(({ id, enabled, severity: current, editable }) =>
      ({ id, enabled, ...(editable.includes('severity') ? { severity: id === 'hard:transaction-failed' ? severity : current } : {}) }));
    const result = await call('/api/rule-set', { method: 'PUT', token: admin, body: {
      expectedRevision: state.revision, confirmHardRuleDisable: false, restoreFromRevision: null, note: null, hardRules,
      softRules: state.softRules.map(({ id, name, field, operator, threshold, severity: level, enabled, scope }) => ({ id, name, field, operator, threshold, severity: level, enabled, scope })),
      relationRules: [], protocolRules: [], ...extra } });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    return result.body.revision;
  };

  // 3) 인증·권한
  for (const route of ['/api/response-codes', '/api/response-codes/T001']) assert.equal((await call(route)).status, 401);
  const denied = await call('/api/response-codes/T001', { method: 'PUT', token: user, body: { message: 'x', expectedRevision: 1 } });
  assert.deepEqual([denied.status, denied.body.code], [403, 'admin_required']);
  assert.equal((await list('', user)).items.length, 4, 'signed-in users can read');

  // 4) 룰 세트 byCode: T001 위험, 사전에 없는 ZZ9 참고. 목록 = 코드 포인트 순, 사전에 없는 코드 포함.
  const start = (await call('/api/rule-set', { token: admin })).body.revision;
  const r1 = await saveFailedSeverity({ byCode: { T001: 'critical', ZZ9: 'info' }, default: 'warning' });
  {
    const all = await list();
    assert.deepEqual(all.ruleSet, { revision: r1, ruleId: 'hard:transaction-failed', enabled: false, defaultSeverity: 'warning', registeredCodes: 2 });
    assert.deepEqual(all.counts, { dictionary: 4, edited: 0, missingFromDictionary: 1 });
    assert.deepEqual(all.types, [{ codeType: 'SYN_A', count: 2 }, { codeType: 'SYN_B', count: 2 }]);
    assert.deepEqual(all.items.map((item) => [item.code, item.inDictionary, item.severity.level, item.severity.source]),
      [['10', true, 'warning', 'default'], ['E500', true, 'warning', 'default'], ['T001', true, 'critical', 'code'], ['T002', true, 'warning', 'default'], ['ZZ9', false, 'info', 'code']]);
    assert.deepEqual(all.items[2], { code: 'T001', inDictionary: true, enumName: 'SYN_ONE', codeType: 'SYN_A', httpStatus: '500 SYN', messageKey: 'syn.one',
      message: MESSAGES.T001, origin: 'import', severity: { level: 'critical', source: 'code' } });
    assert.deepEqual(all.items[4], { code: 'ZZ9', inDictionary: false, enumName: null, codeType: null, httpStatus: null, messageKey: null, message: null, origin: null,
      severity: { level: 'info', source: 'code' } });
    assert.deepEqual(all.pagination, { limit: 50, hasMore: false, nextCursor: null });
    assert.deepEqual((await list('?q=%EC%84%9C%EB%B2%84')).items.map((item) => item.code), ['E500'], 'message search');
    assert.deepEqual((await list('?q=t00')).items.map((item) => item.code), ['T001', 'T002'], 'code search ignores case');
    assert.deepEqual((await list('?q=zz')).items.map((item) => item.code), ['ZZ9'], 'codes outside the dictionary are searchable by code');
    assert.deepEqual((await list('?q=syn_digits')).items.map((item) => item.code), ['10'], 'enum name search');
    assert.deepEqual((await list('?type=SYN_B')).items.map((item) => item.code), ['10', 'E500']);
    const pages = [];
    let cursor = null;
    do {
      const page = await list(`?limit=2${cursor ? `&cursor=${cursor}` : ''}`);
      pages.push(page.items.map((item) => item.code));
      cursor = page.pagination.nextCursor;
    } while (cursor);
    assert.deepEqual(pages, [['10', 'E500'], ['T001', 'T002'], ['ZZ9']]);
    for (const [query, code] of [['?limit=0', 'invalid_limit'], ['?cursor=x%20y', 'invalid_cursor'], ['?sort=code', 'unknown_response_code_filter']]) {
      const result = await call(`/api/response-codes${query}`, { token: admin });
      assert.deepEqual([result.status, result.body.code], [400, code], query);
    }
  }

  // 5) 상세: 위험도 바뀐 룰 세트 버전. 기본 단계 변경은 기본값을 따르는 코드에만 바뀜이다. 되돌리기도 바뀜으로 남는다.
  const r2 = await saveFailedSeverity({ byCode: { T001: 'critical', ZZ9: 'info' }, default: 'info' });
  const r3 = await saveFailedSeverity({ byCode: { ZZ9: 'info' }, default: 'info' });
  {
    const t001 = await detail('T001');
    assert.deepEqual([t001.inDictionary, t001.revision, t001.origin, t001.severity, t001.ruleSet.revision], [true, 3, 'import', { level: 'info', source: 'default' }, r3]);
    assert.deepEqual(t001.severityHistory.map((item) => [item.revision, item.before, item.after, item.restoredFromRevision]), [
      [r3, { level: 'critical', source: 'code' }, { level: 'info', source: 'default' }, null],
      [r1, { level: 'warning', source: 'default' }, { level: 'critical', source: 'code' }, null],
    ], 'r2 changed only the default, which T001 did not follow then');
    assert.equal(t001.severityHistory[0].savedBy.displayName, environment.SEED_ADMIN_EMAIL);
    assert.deepEqual(t001.messageHistory.map((item) => [item.kind, item.messageBefore, item.messageAfter, item.changedBy.actorId]), [
      ['import', '합성 메시지 일 개정', MESSAGES.T001, 'system:import'], ['import', MESSAGES.T001, '합성 메시지 일 개정', 'system:import'], ['import', null, MESSAGES.T001, 'system:import']]);
    const e500 = await detail('E500');
    assert.deepEqual(e500.severityHistory.map((item) => [item.revision, item.before.level, item.after.level]), [[r2, 'warning', 'info']]);
    const zz9 = await detail('ZZ9');
    assert.deepEqual([zz9.inDictionary, zz9.message, zz9.revision, zz9.updatedBy, zz9.messageHistory, zz9.severityHistory.map((item) => item.revision)],
      [false, null, null, null, [], [r1]]);
    const missing = await call('/api/response-codes/NOPE', { token: admin });
    assert.deepEqual([missing.status, missing.body.code], [404, 'response_code_not_found']);
    assert.equal((await call('/api/response-codes/a%20b', { token: admin })).body.code, 'invalid_response_code');
    // 되돌리기(r1 상태 = 같은 hard 상태를 다시 보냄)도 위험도가 바뀐 버전으로 보인다.
    const r1Snapshot = (await call(`/api/rule-set/history/${r1}`, { token: admin })).body.snapshot;
    const restored = await saveFailedSeverity(r1Snapshot.hardRules.find((rule) => rule.id === 'hard:transaction-failed').severity, { restoreFromRevision: r1,
      hardRules: r1Snapshot.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, ...(severity !== undefined ? { severity } : {}) })) });
    const again = await detail('T001');
    assert.deepEqual([again.severityHistory[0].revision, again.severityHistory[0].restoredFromRevision, again.severity],
      [restored, r1, { level: 'critical', source: 'code' }]);
    assert.ok(start < r1);
    const plain = await detail('T001', user);
    assert.equal(plain.severityHistory[0].savedBy.displayName, plain.severityHistory[0].savedBy.actorId, 'account email is shown to admins only');
  }

  // 6) 메시지 편집: 낙관적 잠금·무변경·검사, 사전에 없는 byCode 코드는 새 행.
  {
    const edit = (code, body, token = admin) => call(`/api/response-codes/${encodeURIComponent(code)}`, { method: 'PUT', token, body });
    const saved = await edit('T001', { message: '  화면에서 고친 합성 메시지 ', expectedRevision: 3 });
    assert.equal(saved.status, 200, JSON.stringify(saved.body));
    assert.deepEqual([saved.body.message, saved.body.origin, saved.body.revision, saved.body.updatedBy.displayName], ['화면에서 고친 합성 메시지', 'edited', 4, environment.SEED_ADMIN_EMAIL]);
    assert.deepEqual(saved.body.messageHistory[0], { kind: 'edit', messageBefore: MESSAGES.T001, messageAfter: '화면에서 고친 합성 메시지',
      changedAt: saved.body.messageHistory[0].changedAt, changedBy: saved.body.updatedBy });
    const stale = await edit('T001', { message: '늦은 편집', expectedRevision: 3 });
    assert.deepEqual([stale.status, stale.body], [409, { code: 'response_code_revision_conflict', currentRevision: 4 }]);
    const same = await edit('T001', { message: '화면에서 고친 합성 메시지', expectedRevision: 4 });
    assert.deepEqual([same.status, same.body.code], [409, 'response_code_unchanged']);
    for (const [body, code] of [[{ message: '', expectedRevision: 4 }, 'response_code_message_invalid'], [{ message: 'x' }, 'invalid_response_code_edit']]) {
      const result = await edit('T001', body);
      assert.deepEqual([result.status, result.body.code], [400, code]);
    }
    const textBody = await call('/api/response-codes/T001', { method: 'PUT', token: admin, raw: '{"message":' });
    assert.deepEqual([textBody.status, textBody.body.code], [400, 'invalid_json']);
    const notRegistered = await edit('T404', { message: '없는 코드', expectedRevision: null });
    assert.deepEqual([notRegistered.status, notRegistered.body.code], [404, 'response_code_not_found']);
    const wrongCreate = await edit('ZZ9', { message: '사전에 없던 합성 코드', expectedRevision: 1 });
    assert.deepEqual([wrongCreate.status, wrongCreate.body], [409, { code: 'response_code_revision_conflict', currentRevision: null }]);
    const created = await edit('ZZ9', { message: '사전에 없던 합성 코드', expectedRevision: null });
    assert.equal(created.status, 200, JSON.stringify(created.body));
    assert.deepEqual([created.body.inDictionary, created.body.origin, created.body.revision, created.body.enumName, created.body.messageHistory.length], [true, 'edited', 1, null, 1]);
    const twice = await edit('ZZ9', { message: '다시 만들기', expectedRevision: null });
    assert.deepEqual([twice.status, twice.body.currentRevision], [409, 1]);
    assert.deepEqual((await list()).counts, { dictionary: 5, edited: 2, missingFromDictionary: 0 });
  }

  // 7) 다시 가져오기: 화면에서 고친 행은 그대로, --force면 파일 값으로 덮는다(기록 남김).
  {
    assert.equal(line((await runImport(good)).output, 'imported'), 'imported: inserted 0, updated 0, unchanged 3, kept edited 1, overwritten edited 0');
    assert.equal((await detail('T001')).message, '화면에서 고친 합성 메시지');
    assert.equal(line((await runImport(good, '--force')).output, 'imported'), 'imported: inserted 0, updated 0, unchanged 3, kept edited 0, overwritten edited 1');
    const forced = await detail('T001');
    assert.deepEqual([forced.message, forced.origin, forced.revision, forced.messageHistory[0].kind, forced.messageHistory[0].messageBefore],
      [MESSAGES.T001, 'import', 5, 'import', '화면에서 고친 합성 메시지']);
    assert.equal((await detail('ZZ9')).origin, 'edited', 'codes absent from the file are left alone');
  }

  // 8) 이상 분석 항목: 응답 코드가 사전에 있으면 메시지를 함께 낸다(목록·상세, 같은 조회 안의 조인). 시험 고정 데이터의 이상 판정 응답 코드는 E500.
  {
    const fixture = await seedAnalysisFixture(pool);
    const anomaly = fixture.decisions.anomaly.id;
    const listed = async (decision) => {
      const page = await call(`/api/analysis/decisions?q=${encodeURIComponent(decision.tx)}`, { token: user });
      assert.equal(page.status, 200, JSON.stringify(page.body));
      return page.body.items.find((item) => item.id === decision.id);
    };
    const anomalyItem = await listed(fixture.decisions.anomaly);
    assert.deepEqual([anomalyItem.responseCode, anomalyItem.responseCodeMessage], ['E500', MESSAGES.E500]);
    const normalItem = await listed(fixture.decisions.normal);
    assert.ok(normalItem.responseCode && !(normalItem.responseCode in MESSAGES), 'the fixture code of this item is not in the synthetic dictionary');
    assert.equal(normalItem.responseCodeMessage, null, 'codes outside the dictionary have no message');
    const one = (await call(`/api/analysis/decisions/${anomaly}`, { token: user })).body;
    assert.deepEqual([one.id, one.responseCode, one.responseCodeMessage], [anomaly, 'E500', MESSAGES.E500]);
  }

  // 9) 겹치는 코드 합치기(사용자 결정 11/13): 같은 코드의 행은 메시지·코드명·메시지 키를 `A 또는 B`로 합쳐 한 행으로 넣는다.
  //    유형·HTTP 상태가 다르거나 합친 메시지가 500자를 넘으면 잘못된 행(아무것도 쓰지 않음). 같은 파일을 다시 넣으면 unchanged.
  {
    const mergeLines = (extra = []) => [HEADER, 'SYN_M1,SYN_M,M001,409 SYN,syn.m1,합성 겹침 가', 'SYN_M2,SYN_M,M001,409 SYN,syn.m2,합성 겹침 나',
      'SYN_M3,SYN_M,M002,409 SYN,syn.m3,합성 겹침 다', 'SYN_M3,SYN_M,M002,409 SYN,syn.m3,합성 겹침 다', ...extra];
    const mismatch = await runImport(await csvFile('merge-bad.csv', mergeLines(['SYN_M1,SYN_OTHER,M001,409 SYN,syn.m1,합성 겹침 라',
      `SYN_M1,SYN_M,M001,409 SYN,syn.m1,${'겹'.repeat(490)}`])));
    assert.equal(mismatch.status, 2, mismatch.output);
    assert.equal(line(mismatch.output, 'invalid rows'), 'invalid rows: 2 (row 6 duplicate_code_type_mismatch, row 7 merged_message_too_long)');
    assert.equal((await pool.query(`SELECT count(*)::int AS n FROM ops_response_codes WHERE code LIKE 'M00%'`)).rows[0].n, 0, 'nothing written');
    const mergeFile = await csvFile('merge.csv', mergeLines());
    const merged = await runImport(mergeFile);
    assert.equal(merged.status, 0, merged.output);
    assert.deepEqual(['merged codes', 'codes to import', 'imported'].map((label) => line(merged.output, label)),
      ['merged codes: 2 (rows 3, 5)', 'codes to import: 2', 'imported: inserted 2, updated 0, unchanged 0, kept edited 0, overwritten edited 0']);
    const rows = (await pool.query(`SELECT code,enum_name,code_type,http_status,message_key,message,origin FROM ops_response_codes WHERE code LIKE 'M00%' ORDER BY code`)).rows;
    assert.deepEqual(rows.map((row) => Object.values(row)), [
      ['M001', 'SYN_M1 또는 SYN_M2', 'SYN_M', '409 SYN', 'syn.m1 또는 syn.m2', '합성 겹침 가 또는 합성 겹침 나', 'import'],
      ['M002', 'SYN_M3', 'SYN_M', '409 SYN', 'syn.m3', '합성 겹침 다', 'import'],
    ]);
    assert.deepEqual([(await detail('M001')).message, (await detail('M001')).messageHistory.length], ['합성 겹침 가 또는 합성 겹침 나', 1]);
    assert.equal(line((await runImport(mergeFile)).output, 'imported'), 'imported: inserted 0, updated 0, unchanged 2, kept edited 0, overwritten edited 0');
  }

  console.log('PASS response code dictionary import (repeated codes merged), API, rule set severity history and analysis message under powercode_api');
} finally {
  if (child && child.exitCode === null) {
    const exited = new Promise((resolve) => child.once('exit', resolve));
    child.kill();
    await exited;
  }
  await api.end();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
