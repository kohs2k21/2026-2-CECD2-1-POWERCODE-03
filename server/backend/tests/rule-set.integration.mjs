// 룰 세트 API(rule-set.md)·수식 추천(llm-recommendation-job.md) 통합 시험: 인증·권한, 저장·검증·리비전, 충돌 409, 무변경 409,
// 되돌리기 분기, detection_methods_empty(v1·v2 투영), 공통 잠금, 멱등키, 미리보기, 이력·커서, append-only, 표본 규칙·분위 일치, 기간 mode, 503.
// 로컬 전용 새 QA 데이터베이스(qa_rule_set*)만 허용한다. 표 ops_rule_set_revisions는 마이그레이션 015가 만든다(r0 행 포함).
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { activateConfig, addProcess, clearActiveConfig, seedRuleSetSource } from './rule-set-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_rule_set[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_rule_set database is permitted');
}
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-rule-set-'));
const pool = new pg.Pool({ connectionString: url });
const R0_HASH = '80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89';
const HARD = ['hard:process-status', 'hard:negative-duration', 'hard:success-with-error', 'hard:count-conservation'];
// r0 스냅샷에는 앞 2개만 있어 뒤 2개는 꺼짐으로 표시된다. 아래 흐름은 켜지 않은 채로 둔다.
const LATER = HARD.slice(2);
const STATUS_DEFAULT = { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_Z: 'warning', PROCESS_STATUS_H: 'info', PROCESS_STATUS_N: 'info' } };
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

try {
  const environment = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, NODE_ENV: 'test', DATABASE_URL: url,
    JWT_SECRET: 'qa-rule-set-local-only-secret',
    SEED_USER_EMAIL: 'rule-set-user@example.test', SEED_USER_PASSWORD: 'qa-rule-set-local-only-password',
    SEED_ADMIN_EMAIL: 'rule-set-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-rule-set-admin-local-only',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(await freePort()),
    DOTENV_CONFIG_PATH: path.join(temporary, 'no-local-env-file') };
  let base = await startServer(environment);
  const call = async (route, { method = 'GET', token, body, headers = {}, raw } = {}) => {
    const response = await fetch(`${base}${route}`, {
      method, body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined || raw ? { 'Content-Type': 'application/json' } : {}), ...headers },
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
  const read = async (token = admin) => (await call('/api/rule-set', { token })).body;
  const hardOn = (off = [], severity = STATUS_DEFAULT) => HARD.map((id) => ({ id, enabled: !off.includes(id) && !LATER.includes(id), ...(id === HARD[0] ? { severity } : {}) }));
  const asRequest = ({ id, name, field, operator, threshold, severity, enabled }) => ({ id, name, field, operator, threshold, severity, enabled });
  const newRule = (overrides = {}) => ({ id: null, clientRef: 'new-1', name: '처리 시간 5초 초과', field: 'elapsed_ms', operator: 'gt', threshold: 5000, severity: 'warning', enabled: true, ...overrides });
  const put = (body, options = {}) => call('/api/rule-set', { method: 'PUT', token: admin, body, ...options });
  const save = async (changes) => {
    const state = await read();
    const result = await put({ expectedRevision: state.revision, hardRules: state.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })), softRules: state.softRules.map(asRequest), ...changes });
    assert.equal(result.status, 200, JSON.stringify(result.body));
    return result.body;
  };

  // 1) 인증·권한: 미인증 401, 일반 사용자 쓰기·미리보기·수식 추천 403, 읽기 200.
  for (const [method, route] of [['GET', '/api/rule-set'], ['PUT', '/api/rule-set'], ['POST', '/api/rule-set/preview'], ['GET', '/api/rule-set/history'],
    ['GET', '/api/rule-set/history/0'], ['POST', '/api/rule-set/threshold-stats']]) {
    assert.equal((await call(route, { method, body: method === 'GET' ? undefined : {} })).status, 401, `${method} ${route}`);
  }
  for (const [method, route] of [['PUT', '/api/rule-set'], ['POST', '/api/rule-set/preview'], ['POST', '/api/rule-set/threshold-stats']]) {
    const denied = await call(route, { method, token: user, body: {} });
    assert.deepEqual([denied.status, denied.body.code], [403, 'admin_required'], `${method} ${route}`);
  }

  // 2) r0: 시스템 기본값, 이상 규칙 전부 켬, 이상 징후 규칙 없음. 일반 사용자도 읽기 가능.
  const r0 = await read(user);
  assert.equal(r0.contractVersion, 'rule-set-v1');
  assert.deepEqual([r0.revision, r0.contentHash, r0.contentHashVersion, r0.catalogVersion, r0.restoredFromRevision], [0, R0_HASH, 1, 'method-catalog-v2', null], 'hash and version stay as the row stores them');
  assert.deepEqual(r0.savedBy, { actorId: 'system', displayName: 'system' });
  assert.deepEqual(r0.counts, { hardEnabled: 2, hardTotal: 4, softEnabled: 0, softTotal: 0 }, 'rules not in the r0 snapshot count as off');
  assert.deepEqual(r0.hardRules.map((rule) => [rule.id, rule.enabled, rule.editable]), [[HARD[0], true, ['enabled', 'severity']], [HARD[1], true, ['enabled']], [HARD[2], false, ['enabled']], [HARD[3], false, ['enabled']]]);
  assert.deepEqual(r0.hardRules.slice(2).map((rule) => [rule.methodVersion, rule.severity]), [['success-with-error-v1', 'critical'], ['count-conservation-v1', 'critical']], 'off with the built-in values');
  assert.deepEqual([r0.hardRules[0].methodVersion, r0.hardRules[0].severity], ['process-status-hr-v2', STATUS_DEFAULT], 'the stored v1 values of r0 are not shown');
  assert.deepEqual(r0.softRules, []);
  const capabilities = (await call('/api/jobs/capabilities', { token: admin })).body;
  assert.deepEqual(capabilities.methodCatalog.softRuleFields.map((field) => field.fieldId),
    ['total_count', 'success_count', 'error_count', 'retry_count', 'elapsed_ms', 'error_ratio']);

  // 3) 형식 오류·충돌·무변경.
  assert.deepEqual((await put({ expectedRevision: 0 })).body, { code: 'invalid_rule_set_request' });
  assert.deepEqual((await call('/api/rule-set', { method: 'PUT', token: admin, raw: '{' })).body, { code: 'invalid_json' });
  // NUL은 PostgreSQL이 저장하지 못하는 문자다. 503이 아니라 요청 오류로 끝난다.
  for (const body of [{ expectedRevision: 0, hardRules: hardOn(), softRules: [], note: 'a\u0000b' }, { expectedRevision: 0, hardRules: hardOn(), softRules: [newRule({ clientRef: 'a\u0000' })] }]) {
    const failed = await put(body);
    assert.deepEqual([failed.status, failed.body.code], [400, 'invalid_rule_set_request']);
  }
  const stale = await put({ expectedRevision: 7, hardRules: [], softRules: [] });
  assert.equal(stale.status, 409, 'expectedRevision is checked before hard rule validation');
  assert.equal(stale.body.code, 'rule_set_revision_conflict');
  assert.equal(stale.body.currentRevision, 0);
  assert.equal(stale.body.savedBy.actorId, 'system');
  assert.ok(stale.body.savedAt);
  const unchanged = await put({ expectedRevision: 0, hardRules: hardOn(), softRules: [] });
  assert.deepEqual([unchanged.status, unchanged.body.code], [409, 'rule_set_unchanged']);
  assert.equal((await read()).revision, 0, 'failed saves change nothing');

  // 4) 추가: 서버가 id·단위를 채우고 새 리비전 1개를 만든다.
  const first = await put({ expectedRevision: 0, hardRules: hardOn(), softRules: [newRule()], note: '최초 추가' });
  assert.equal(first.status, 200, JSON.stringify(first.body));
  assert.deepEqual([first.body.revision, first.body.previousRevision, first.body.appliesFrom], [1, 0, 'next_decision']);
  const added = first.body.changes[0];
  assert.match(added.id, /^soft:elapsed_ms-[0-9a-f]{8}$/);
  assert.deepEqual([added.type, added.clientRef], ['soft_added', 'new-1']);
  const r1 = await read();
  assert.equal(r1.contentHash, first.body.contentHash);
  assert.equal(r1.contentHashVersion, 2, 'new saves are v2');
  assert.equal((await pool.query('SELECT content_hash_version AS v FROM ops_rule_set_revisions WHERE revision=1')).rows[0].v, 2);
  assert.equal(r1.savedBy.displayName, environment.SEED_ADMIN_EMAIL, 'admin sees the account email');
  assert.notEqual((await read(user)).savedBy.displayName, environment.SEED_ADMIN_EMAIL, 'email is not exposed to a regular user');
  assert.deepEqual([r1.softRules[0].unit, r1.softRules[0].createdAtRevision, r1.softRules[0].updatedAtRevision], ['ms', 1, 1]);
  assert.equal(typeof r1.softRules[0].threshold, 'number');
  assert.deepEqual(r1.counts, { hardEnabled: 2, hardTotal: 4, softEnabled: 1, softTotal: 1 });
  const stored = (await pool.query('SELECT snapshot,saved_by_actor_id,note FROM ops_rule_set_revisions WHERE revision=1')).rows[0];
  assert.equal(stored.note, '최초 추가');
  assert.deepEqual(stored.snapshot.softRules, r1.softRules, 'snapshot keeps the GET format with number thresholds');
  assert.deepEqual(stored.snapshot.hardRules, r1.hardRules);

  // 5) 대상 필드 변경 불가, 끄기 확인 플래그, 이름만 변경(hash 동일).
  const ruleId = added.id;
  const immutable = await put({ expectedRevision: 1, hardRules: hardOn(), softRules: [{ ...asRequest(r1.softRules[0]), field: 'retry_count', threshold: 5 }] });
  assert.deepEqual([immutable.status, immutable.body.code], [422, `soft_rule_field_immutable:${ruleId}`]);
  const needsConfirm = await put({ expectedRevision: 1, hardRules: hardOn([HARD[1]]), softRules: r1.softRules.map(asRequest) });
  assert.deepEqual([needsConfirm.status, needsConfirm.body.code], [422, 'hard_rule_disable_confirmation_required']);
  const renamed = await save({ softRules: [{ ...asRequest(r1.softRules[0]), name: '처리 시간 초과' }] });
  assert.equal(renamed.contentHash, first.body.contentHash, 'name is not a hash input');
  assert.equal(renamed.revision, 2);
  const turnedOff = await save({ hardRules: hardOn([HARD[1]]), confirmHardRuleDisable: true });
  assert.deepEqual(turnedOff.changes, [{ type: 'hard_disabled', id: HARD[1], displayName: '시작 시각이 종료 시각보다 늦음' }]);
  assert.equal((await save({ softRules: [{ ...asRequest((await read()).softRules[0]), threshold: 6000 }] })).revision, 4, 'already off rule needs no flag');

  // 6) 미리보기: 저장하지 않고 요약·검증만, expectedRevision 불일치만 409.
  const state4 = await read();
  const previewBody = { expectedRevision: 4, hardRules: hardOn(), softRules: [...state4.softRules.map(asRequest), newRule({ name: '다른 규칙', field: 'error_ratio', threshold: 0.01 })] };
  const preview = await call('/api/rule-set/preview', { method: 'POST', token: admin, body: previewBody });
  assert.deepEqual([preview.status, preview.body.valid], [200, true]);
  assert.deepEqual(preview.body.changes.map((change) => [change.type, change.id]), [['hard_enabled', HARD[1]], ['soft_added', null]]);
  assert.deepEqual(preview.body.hardDisabledAfterSave, LATER);
  assert.equal((await read()).revision, 4);
  // JSON 숫자 1e400은 파싱하면 비유한값이다(JSON.stringify로는 만들 수 없어 원문으로 보낸다).
  const invalidPreview = await call('/api/rule-set/preview', { method: 'POST', token: admin, raw: JSON.stringify({ ...previewBody, softRules: [newRule({ threshold: 12345 })] }).replace('12345', '1e400') });
  assert.deepEqual([invalidPreview.status, invalidPreview.body], [200, { valid: false, error: { code: 'soft_rule_threshold_invalid:new-1' } }]);
  const stalePreview = await call('/api/rule-set/preview', { method: 'POST', token: admin, body: { ...previewBody, expectedRevision: 3 } });
  assert.deepEqual([stalePreview.status, stalePreview.body.code], [409, 'rule_set_revision_conflict']);

  // 7) 이력·커서·스냅샷.
  const history = (await call('/api/rule-set/history?limit=2', { token: user })).body;
  assert.deepEqual(history.items.map((item) => item.revision), [4, 3]);
  assert.equal(history.items[0].contentHashVersion, 2);
  assert.equal(history.pagination.hasMore, true);
  assert.deepEqual((await call(`/api/rule-set/history?limit=10&cursor=${history.pagination.nextCursor}`, { token: admin })).body.items.map((item) => item.revision), [2, 1, 0]);
  const item1 = (await call('/api/rule-set/history/1', { token: admin })).body;
  assert.deepEqual([item1.revision, item1.note, item1.restoredFromRevision, item1.summary.softAdded, item1.snapshot.softRules[0].id], [1, '최초 추가', null, 1, ruleId]);
  const r0Item = (await call('/api/rule-set/history/0', { token: user })).body;
  assert.equal(r0Item.snapshot.softRules.length, 0);
  assert.deepEqual([r0Item.contentHashVersion, r0Item.snapshot.hardRules[0].methodVersion], [1, 'process-status-hr-v1'], 'history shows the snapshot as stored');
  for (const [route, status, code] of [['/api/rule-set/history?limit=0', 400, 'invalid_limit'], ['/api/rule-set/history?limit=101', 400, 'invalid_limit'],
    ['/api/rule-set/history?cursor=@@', 400, 'invalid_cursor'], ['/api/rule-set/history?other=1', 400, 'unknown_rule_set_filter'],
    ['/api/rule-set/history/99', 404, 'rule_set_revision_not_found'], ['/api/rule-set/history/x', 400, 'invalid_revision']]) {
    const failed = await call(route, { token: admin });
    assert.deepEqual([failed.status, failed.body.code], [status, code], route);
  }

  // 8) 되돌리기: 스냅샷 재적용 = 새 리비전, 삭제된 규칙은 같은 id로 재생성, 그 밖의 값이 다르면 422.
  const deleted = await save({ softRules: [] });
  assert.deepEqual(deleted.changes.map((change) => change.type), ['soft_removed']);
  const source = (await call('/api/rule-set/history/4', { token: admin })).body;
  const restoreBody = { expectedRevision: 5, restoreFromRevision: 4, hardRules: source.snapshot.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })), softRules: source.snapshot.softRules.map(asRequest) };
  const unknownRevision = await put({ ...restoreBody, restoreFromRevision: 99 });
  assert.deepEqual([unknownRevision.status, unknownRevision.body.code], [422, 'restore_revision_unknown']);
  const mismatch = await put({ ...restoreBody, softRules: [{ ...restoreBody.softRules[0], threshold: 1 }] });
  assert.deepEqual([mismatch.status, mismatch.body.code], [422, 'restore_payload_mismatch']);
  const restored = await put(restoreBody);
  assert.equal(restored.status, 200, JSON.stringify(restored.body));
  assert.deepEqual(restored.body.changes, [{ type: 'soft_added', id: ruleId, name: '처리 시간 초과', restored: true }]);
  assert.equal(restored.body.contentHash, source.contentHash, 'restored revision has the source hash');
  const r6 = await read();
  assert.deepEqual([r6.revision, r6.restoredFromRevision, r6.softRules[0].createdAtRevision, r6.softRules[0].updatedAtRevision], [6, 4, 1, 6]);
  // 스냅샷에 없는 내장 규칙: 꺼짐·내장 기본 위험도로 채우고, 현재 켜져 있으면 새로 꺼지는 것이다(옛 스냅샷을 직접 넣어 재현).
  const legacy = { hardRules: [{ id: HARD[0], enabled: true }], softRules: [] };
  await pool.query(`INSERT INTO ops_rule_set_revisions(revision,content_hash,snapshot,changes,summary,saved_by_actor_id,saved_at)
    VALUES(7,$1,$2::jsonb,'[]','{}','fixture',clock_timestamp())`, ['a'.repeat(64), JSON.stringify(legacy)]);
  await save({ softRules: [newRule({ name: '중간 규칙' })] });
  await save({ hardRules: hardOn() });
  const restoreLegacy = { expectedRevision: 9, restoreFromRevision: 7, hardRules: legacy.hardRules, softRules: [] };
  const needsFlag = await put(restoreLegacy);
  assert.deepEqual([needsFlag.status, needsFlag.body.code], [422, 'hard_rule_disable_confirmation_required']);
  const newHard = await put({ ...restoreLegacy, confirmHardRuleDisable: true });
  assert.equal(newHard.status, 200, JSON.stringify(newHard.body));
  assert.deepEqual(newHard.body.changes.map((change) => change.type), ['hard_disabled', 'soft_removed'], 'a filled rule that was on is newly off');
  assert.deepEqual((await read()).hardRules.map((rule) => rule.enabled), [true, false, false, false]);
  // 스냅샷의 규칙이 내장 목록에서 사라졌으면 hard_rule_unknown.
  const gone = { hardRules: [...hardOn(), { id: 'hard:removed', enabled: true }], softRules: [] };
  await pool.query(`INSERT INTO ops_rule_set_revisions(revision,content_hash,snapshot,changes,summary,saved_by_actor_id,saved_at)
    VALUES(11,$1,$2::jsonb,'[]','{}','fixture',clock_timestamp())`, ['b'.repeat(64), JSON.stringify(gone)]);
  const goneRestore = await put({ expectedRevision: 11, restoreFromRevision: 11, hardRules: gone.hardRules, softRules: [] });
  assert.deepEqual([goneRestore.status, goneRestore.body.code], [422, 'hard_rule_unknown:hard:removed']);

  // 8b) 코드별 위험도: 상태 규칙만 편집, 저장·이력·되돌리기·hash·무변경.
  const editedStatus = { byCode: { ...STATUS_DEFAULT.byCode, PROCESS_STATUS_N: 'critical' } };
  const severitySave = await put({ expectedRevision: 11, hardRules: hardOn([], editedStatus), softRules: [] });
  assert.equal(severitySave.status, 200, JSON.stringify(severitySave.body));
  assert.deepEqual(severitySave.body.changes, [{ type: 'hard_updated', id: HARD[0], displayName: '상태가 S가 아님', diff: { severity: [STATUS_DEFAULT, editedStatus] } }]);
  assert.equal(severitySave.body.summary.hardUpdated, 1);
  const r12 = await read();
  assert.deepEqual([r12.revision, r12.contentHashVersion, r12.hardRules[0].severity], [12, 2, editedStatus]);
  assert.deepEqual((await put({ expectedRevision: 12, hardRules: hardOn([], editedStatus), softRules: [] })).body.code, 'rule_set_unchanged');
  for (const [body, code] of [
    [{ hardRules: HARD.map((id) => ({ id, enabled: !LATER.includes(id) })) }, 'hard_rule_severity_invalid:hard:process-status'],
    [{ hardRules: hardOn([], { byCode: { PROCESS_STATUS_F: 'critical' } }) }, 'hard_rule_severity_invalid:hard:process-status'],
    [{ hardRules: hardOn().map((rule) => (rule.id === HARD[1] ? { ...rule, severity: 'info' } : rule)) }, 'hard_rule_field_not_editable:hard:negative-duration'],
  ]) {
    const failed = await put({ expectedRevision: 12, softRules: [], ...body });
    assert.deepEqual([failed.status, failed.body.code], [422, code]);
  }
  const defaults = await save({ hardRules: hardOn() });
  const v2Source = (await call('/api/rule-set/history/12', { token: admin })).body;
  const backToEdited = await put({ expectedRevision: defaults.revision, restoreFromRevision: 12, hardRules: v2Source.snapshot.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })), softRules: [] });
  assert.equal(backToEdited.status, 200, JSON.stringify(backToEdited.body));
  assert.equal(backToEdited.body.contentHash, severitySave.body.contentHash, 'same version, no filled rule: same hash');
  assert.deepEqual((await read()).hardRules[0].severity, editedStatus);
  // r0(v1 행) 되돌리기: 위험도는 현재 내장 기본값으로 채우고 hash는 v2라 r0와 다르다.
  const r0Source = (await call('/api/rule-set/history/0', { token: admin })).body;
  // 새 규칙 2개를 켠 뒤 되돌리면 r0에 없는 규칙이 꺼짐으로 채워져 새로 꺼진다.
  const allFour = await save({ hardRules: HARD.map((id) => ({ id, enabled: true, ...(id === HARD[0] ? { severity: editedStatus } : {}) })) });
  assert.deepEqual(allFour.changes.map((change) => [change.type, change.id]), [['hard_enabled', HARD[2]], ['hard_enabled', HARD[3]]]);
  const backToR0 = await put({ expectedRevision: allFour.revision, confirmHardRuleDisable: true, restoreFromRevision: 0, hardRules: r0Source.snapshot.hardRules.map(({ id, enabled }) => ({ id, enabled })), softRules: [] });
  assert.equal(backToR0.status, 200, JSON.stringify(backToR0.body));
  assert.notEqual(backToR0.body.contentHash, R0_HASH);
  assert.deepEqual(backToR0.body.changes.map((change) => change.type), ['hard_updated', 'hard_disabled', 'hard_disabled']);
  assert.deepEqual((await read()).hardRules[0].severity, STATUS_DEFAULT);

  // 9) detection_methods_empty: 활성 모델은 v1·v2 투영(ops_system_state → ml_config_models).
  const sourceId = await seedRuleSetSource(pool);
  const allOff = { expectedRevision: backToR0.body.revision, hardRules: hardOn(HARD), confirmHardRuleDisable: true, softRules: [] };
  const empty = await put(allOff);
  assert.deepEqual([empty.status, empty.body.code], [422, 'detection_methods_empty']);
  await activateConfig(pool, sourceId, { models: 0 });
  assert.equal((await put(allOff)).body.code, 'detection_methods_empty', 'an active configuration without models does not count');
  await activateConfig(pool, sourceId, { models: 1, trainFrom: null });
  const withModel = await put(allOff);
  assert.equal(withModel.status, 200, JSON.stringify(withModel.body));
  assert.equal((await read()).counts.hardEnabled, 0);
  await save({ hardRules: hardOn() });
  await clearActiveConfig(pool);

  // 10) 동시 저장: 공통 잠금을 기다리고, 같은 리비전 기준이면 한쪽만 성공.
  const current = await read();
  const blocker = await pool.connect();
  await blocker.query('BEGIN');
  await blocker.query('SELECT pg_advisory_xact_lock(81739,0)');
  const body = (name) => ({ expectedRevision: current.revision, hardRules: hardOn(), softRules: [newRule({ name })] });
  const racing = [put(body('경쟁 1')), put(body('경쟁 2'))];
  let waiting = 0;
  for (let attempt = 0; attempt < 100 && waiting < 2; attempt += 1) {
    waiting = (await pool.query(`SELECT count(*)::int AS n FROM pg_locks WHERE locktype='advisory' AND classid=81739 AND objid=0 AND NOT granted`)).rows[0].n;
    if (waiting < 2) await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(waiting, 2, 'both saves wait on the shared lock');
  await blocker.query('COMMIT');
  blocker.release();
  const results = await Promise.all(racing);
  assert.deepEqual(results.map((result) => result.status).sort(), [200, 409]);
  assert.equal(results.find((result) => result.status === 409).body.code, 'rule_set_revision_conflict');
  assert.equal((await read()).revision, current.revision + 1);

  // 11) 멱등키: 같은 키·같은 본문은 첫 응답 재전송, 다른 본문은 409.
  const latest = await read();
  const keyed = { expectedRevision: latest.revision, hardRules: hardOn(), softRules: latest.softRules.map((rule) => ({ ...asRequest(rule), threshold: 7000 })) };
  const once = await put(keyed, { headers: { 'Idempotency-Key': 'save-1' } });
  const twice = await put(keyed, { headers: { 'Idempotency-Key': 'save-1' } });
  assert.deepEqual([once.status, twice.status], [200, 200]);
  assert.deepEqual(twice.body, once.body);
  assert.equal((await read()).revision, latest.revision + 1, 'replay does not add a revision');
  const otherBody = await put({ ...keyed, note: '다름' }, { headers: { 'Idempotency-Key': 'save-1' } });
  assert.deepEqual([otherBody.status, otherBody.body.code], [409, 'idempotency_payload_conflict']);
  assert.equal((await put(keyed, { headers: { 'Idempotency-Key': 'bad key' } })).body.code, 'invalid_idempotency_key');

  // 12) append-only.
  await assert.rejects(pool.query(`UPDATE ops_rule_set_revisions SET note='x' WHERE revision=0`));
  await assert.rejects(pool.query('DELETE FROM ops_rule_set_revisions WHERE revision=0'));

  // 13) 수식 추천 표본 규칙: status=S, 명시 anomaly 제외(최신 라벨 기준), 실행별 최신 revision, 값 없는 행 제외. 분위는 numpy linear와 같은 정의.
  const stats = (payload) => call('/api/rule-set/threshold-stats', { method: 'POST', token: admin, body: { sourceId, ...payload } });
  const day = (offset) => new Date(Date.now() - offset * 86400000).toISOString();
  const windowRange = { mode: 'custom', from: day(10), until: day(0) };
  const at = (ms) => new Date(Date.parse('2026-01-01T00:00:00Z') + ms).toISOString().replace('Z', '');
  const durations = Array.from({ length: 41 }, (_, index) => 100 + index * index);
  for (const [index, duration] of durations.entries()) {
    await addProcess(pool, sourceId, { occurredAt: day(5), start: at(0), end: at(duration), counts: { total: 1000 + index, error: index === 40 ? 1 : 0, retry: index % 4 },
      label: index === 0 ? ['anomaly', 'normal'] : null });
  }
  await addProcess(pool, sourceId, { occurredAt: day(5), status: 'F', start: at(0), end: at(900000) });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: at(0), end: at(800000), label: 'anomaly' });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: at(0), end: at(700000), label: ['normal', 'anomaly'] });
  const revised = await addProcess(pool, sourceId, { occurredAt: day(5), start: at(0), end: at(600000), revision: 1 });
  await addProcess(pool, sourceId, { occurredAt: day(5), status: 'F', start: at(0), end: at(600000), execution: revised.execution, revision: 2 });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: at(0), end: null });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: null, end: at(10) });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: 'not a time', end: at(10) });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: '2026-02-30T00:00:00', end: '2026-03-01T00:00:00' });
  await addProcess(pool, sourceId, { occurredAt: day(5), start: '2026-01-01T00:00:00+09:00', end: '2026-01-01T00:00:01' });
  await addProcess(pool, sourceId, { occurredAt: day(20), start: at(0), end: at(500000) });
  await addProcess(pool, sourceId, { occurredAt: null, start: at(0), end: at(400000) });
  const percentile = (values, q) => {
    const sorted = [...values].sort((left, right) => left - right), position = (sorted.length - 1) * q, low = Math.floor(position);
    return sorted[low] + (sorted[Math.min(low + 1, sorted.length - 1)] - sorted[low]) * (position - low);
  };
  const elapsed = await stats({ field: 'elapsed_ms', period: windowRange });
  assert.equal(elapsed.status, 200, JSON.stringify(elapsed.body));
  assert.deepEqual([elapsed.body.status, elapsed.body.operator, elapsed.body.unit, elapsed.body.source, elapsed.body.rationale], ['proposed', 'gt', 'ms', 'data_p95', null]);
  assert.deepEqual([elapsed.body.evidence.n, elapsed.body.evidence.min, elapsed.body.evidence.max], [41, 100, 1700], 'sample rules select only the 41 valid rows');
  assert.equal(elapsed.body.evidence.p95, percentile(durations, 0.95));
  assert.equal(elapsed.body.threshold, String(percentile(durations, 0.95)));
  assert.equal(elapsed.body.evidence.quantileMethod, 'percentile_cont');
  assert.equal(elapsed.body.evidence.scope, 'period');
  assert.equal(elapsed.body.evidence.exclusionRule, 'status=S, explicit anomaly labels excluded, excludedIntervals=0');
  assert.deepEqual(elapsed.body.evidence.period, { ...windowRange, timeBasis: 'esb_occurred_at', resolvedFrom: null });
  for (const [statistic, q] of [['p99', 0.99], ['median', 0.5]]) {
    assert.equal(Number((await stats({ field: 'elapsed_ms', statistic, period: windowRange })).body.threshold), Number(percentile(durations, q).toPrecision(15)), statistic);
  }
  assert.equal((await stats({ field: 'elapsed_ms', statistic: 'max', period: windowRange })).body.threshold, '1700');
  // 정수 필드 임계값은 정수, 오류 비율은 고정소수·유효숫자 표시. 재시도 수 분위가 소수여도 저장 가능한 값이어야 한다.
  const retry = await stats({ field: 'retry_count', period: windowRange });
  assert.match(retry.body.threshold, /^\d+$/);
  const ratio = await stats({ field: 'error_ratio', statistic: 'max', period: windowRange });
  assert.equal(Number(ratio.body.threshold), Number((1 / 1040).toPrecision(15)));
  assert.doesNotMatch(ratio.body.threshold, /e/i);
  assert.equal(ratio.body.displayThreshold, '0.0009615');
  const applied = await put({ expectedRevision: (await read()).revision, hardRules: hardOn(), softRules: [newRule({ field: 'error_ratio', threshold: Number(ratio.body.threshold) })] });
  assert.equal(applied.status, 200, 'a proposed threshold is always saveable');

  // 14) 제외 구간·표본 부족·분산 없음·기간 오류·persisted_at 기준.
  const excluded = await stats({ field: 'elapsed_ms', period: windowRange, excludedIntervals: [{ from: day(6), until: day(4) }] });
  assert.deepEqual([excluded.body.status, excluded.body.evidence.n, excluded.body.threshold, excluded.body.operator], ['no_applicable', 0, null, null]);
  assert.match(excluded.body.evidence.exclusionRule, /excludedIntervals=1$/);
  assert.equal((await stats({ field: 'elapsed_ms', period: windowRange, excludedIntervals: [{ from: day(30), until: day(4) }] })).body.code, 'excluded_interval_out_of_range');
  assert.equal((await stats({ field: 'elapsed_ms', period: windowRange, excludedIntervals: [{ from: day(6), until: day(4) }, { from: day(5), until: day(3) }] })).body.code, 'excluded_interval_overlap');
  // 제외 구간은 선택된 시간 기준에 적용한다: persisted_at 기준이면 저장 시각으로 빠지고, 근거의 건수와 실제가 맞는다.
  const persistedRange = { mode: 'custom', from: day(1), until: day(-1), timeBasis: 'persisted_at' };
  const excludedPersisted = await stats({ field: 'elapsed_ms', period: persistedRange, excludedIntervals: [{ from: day(0.9), until: day(-0.9) }] });
  assert.deepEqual([excludedPersisted.body.evidence.n, excludedPersisted.body.evidence.exclusionRule], [0, 'status=S, explicit anomaly labels excluded, excludedIntervals=1']);
  const noVariance = await stats({ field: 'success_count', period: windowRange });
  assert.deepEqual([noVariance.body.status, noVariance.body.evidence.n], ['no_applicable', 0]);
  const other = await seedRuleSetSource(pool, 'constant');
  for (let index = 0; index < 20; index += 1) await addProcess(pool, other, { occurredAt: day(2), counts: { total: 5 } });
  const constant = await call('/api/rule-set/threshold-stats', { method: 'POST', token: admin, body: { sourceId: other, field: 'total_count', period: windowRange } });
  assert.deepEqual([constant.body.status, constant.body.evidence.n, constant.body.evidence.min, constant.body.evidence.max], ['no_applicable', 20, 5, 5]);
  // 기간 안에서 최신을 고른다(학습 스냅샷과 같음): 기간 밖의 새 revision은 기간 안 행을 빼지 않고, 기간 안의 새 revision은 이전 행을 대체한다.
  const revisions = await seedRuleSetSource(pool, 'revisions');
  const statsOf = (source, payload) => call('/api/rule-set/threshold-stats', { method: 'POST', token: admin, body: { sourceId: source, ...payload } });
  for (let index = 0; index < 22; index += 1) await addProcess(pool, revisions, { occurredAt: day(5), counts: { total: 10 + index } });
  const outside = await addProcess(pool, revisions, { occurredAt: day(5), counts: { total: 500 } });
  await addProcess(pool, revisions, { occurredAt: day(-1), status: 'F', counts: { total: 500 }, execution: outside.execution, revision: 2 });
  const inside = await addProcess(pool, revisions, { occurredAt: day(5), counts: { total: 600 } });
  await addProcess(pool, revisions, { occurredAt: day(4), status: 'F', counts: { total: 600 }, execution: inside.execution, revision: 2 });
  // 실행 식별이 없는 관측은 학습 스냅샷과 같이 표본에서 빠진다.
  await addProcess(pool, revisions, { occurredAt: day(5), counts: { total: 9000 }, execution: null, revision: null });
  const latestInPeriod = await statsOf(revisions, { field: 'total_count', statistic: 'max', period: windowRange });
  assert.deepEqual([latestInPeriod.body.evidence.n, latestInPeriod.body.evidence.max], [23, 500], 'newer revision outside the period keeps the row, inside the period replaces it');
  // 분위 고정값: numpy.percentile(values, 95)(linear)로 미리 계산한 956.4999999999995와 대조한다.
  const fixed = await seedRuleSetSource(pool, 'numpy-fixed');
  for (const duration of [12, 15, 15, 18, 21, 30, 31, 44, 47, 52, 60, 61, 75, 90, 110, 120, 155, 180, 240, 320, 990, 4000]) {
    await addProcess(pool, fixed, { occurredAt: day(3), start: at(0), end: at(duration) });
  }
  const numpyCase = await statsOf(fixed, { field: 'elapsed_ms', period: windowRange });
  assert.equal(numpyCase.body.evidence.n, 22);
  assert.ok(Math.abs(numpyCase.body.evidence.p95 - 956.4999999999995) < 1e-9, `p95 ${numpyCase.body.evidence.p95}`);
  assert.deepEqual([numpyCase.body.threshold, numpyCase.body.displayThreshold], ['956.5', '956'], 'threshold keeps 15 digits, display rounds the raw value');
  for (const [payload, status, code] of [
    [{ field: 'elapsed_ms', statistic: 'toString', period: windowRange }, 400, 'invalid_threshold_stats_request'],
    [{ field: 'elapsed_ms', statistic: 'constructor', period: windowRange }, 400, 'invalid_threshold_stats_request'],
    [{ field: 'elapsed_ms', period: { ...windowRange, timeBasis: '__proto__' } }, 400, 'invalid_threshold_stats_request'],
    [{ field: 'elapsed_ms', period: { mode: 'custom', from: '2026-02-30T00:00:00Z', until: '2026-03-10T00:00:00Z' } }, 400, 'invalid_threshold_stats_request'],
    [{ field: 'elapsed_ms', period: { mode: 'custom', from: day(400), until: day(0) } }, 400, 'period_too_long'],
    [{ field: 'nope', period: windowRange }, 422, 'soft_rule_field_unknown:nope'],
    [{ field: 'elapsed_ms', period: { mode: 'custom' } }, 400, 'invalid_threshold_stats_request'],
    [{ field: 'elapsed_ms', sourceId: '11111111-1111-4111-8111-111111111111', period: windowRange }, 404, 'source_not_found'],
  ]) {
    const failed = await stats(payload);
    assert.deepEqual([failed.status, failed.body.code], [status, code], JSON.stringify(payload));
  }
  const recent = await stats({ field: 'elapsed_ms', period: { mode: 'recent_1m' } });
  assert.deepEqual([recent.body.evidence.n, recent.body.evidence.period.mode], [42, 'recent_1m'], 'recent month keeps the 20 day old row');
  const untilLocal = new Date(Date.parse(recent.body.evidence.period.until) + 9 * 3600000), fromLocal = new Date(Date.parse(recent.body.evidence.period.from) + 9 * 3600000);
  assert.equal(fromLocal.getUTCHours() * 60 + fromLocal.getUTCMinutes(), untilLocal.getUTCHours() * 60 + untilLocal.getUTCMinutes(), 'calendar month keeps the local time');
  assert.ok(Date.parse(recent.body.evidence.period.until) - Date.parse(recent.body.evidence.period.from) >= 28 * 86400000);
  assert.ok((await stats({ field: 'elapsed_ms', period: { mode: 'recent_3m' } })).body.evidence.n >= 42);
  const persisted = await stats({ field: 'elapsed_ms', period: { mode: 'custom', from: day(1), until: day(-1), timeBasis: 'persisted_at' } });
  assert.deepEqual([persisted.body.evidence.n, persisted.body.evidence.period.timeBasis], [43, 'persisted_at'], 'rows without an ESB time count only on persisted_at');

  // 15) mode (a): 활성 모델 없음 / 학습 기간 투영 불가 / 투영 성공 / 366일 초과 표본화.
  const active = { field: 'elapsed_ms', period: { mode: 'active_model_training_start' } };
  assert.deepEqual([(await stats(active)).status, (await stats(active)).body.code], [422, 'active_model_missing']);
  await activateConfig(pool, sourceId, { models: 0, trainFrom: day(30) });
  assert.equal((await stats(active)).body.code, 'active_model_missing', 'configuration without models is not an active model');
  await activateConfig(pool, sourceId, { trainFrom: null });
  assert.deepEqual((await stats(active)).body, { code: 'active_model_period_unknown', hint: 'custom' });
  const configId = await activateConfig(pool, sourceId, { trainFrom: day(15) });
  const fromModel = await stats(active);
  assert.equal(fromModel.status, 200, JSON.stringify(fromModel.body));
  assert.deepEqual([fromModel.body.evidence.period.mode, fromModel.body.evidence.period.resolvedFrom, fromModel.body.evidence.period.timeBasis],
    ['active_model_training_start', configId, 'persisted_at'], 'v1/v2 projection carries persisted_at');
  assert.equal(fromModel.body.evidence.quantileMethod, 'percentile_cont');
  assert.equal(fromModel.body.evidence.sampled, undefined);
  await activateConfig(pool, sourceId, { trainFrom: day(500) });
  const longPeriod = await stats(active);
  assert.equal(longPeriod.status, 200, 'mode (a) is exempt from the 366 day limit');
  assert.deepEqual([longPeriod.body.evidence.quantileMethod, longPeriod.body.evidence.sampled], ['percentile_cont_daily_sampled', true]);
  assert.ok(longPeriod.body.evidence.n >= 41);

  // 16) 저장소 실패는 503(빈 룰·성공 대체 없음).
  await stopChild();
  base = await startServer({ ...environment, DATABASE_URL: 'postgresql://postgres:x@127.0.0.1:1/qa_rule_set_unreachable', PORT: String(await freePort()) });
  for (const [method, route, payload] of [['GET', '/api/rule-set'], ['GET', '/api/rule-set/history'], ['PUT', '/api/rule-set', { expectedRevision: 0, hardRules: [], softRules: [] }]]) {
    const failed = await call(route, { method, token: admin, body: payload });
    assert.deepEqual([failed.status, failed.body.code], [503, 'rule_set_storage_unavailable'], `${method} ${route}`);
  }
  console.log('rule-set integration passed: auth, revisions, validation, conflict, unchanged, restore branches, detection_methods_empty, shared lock, idempotency, preview, history, append-only, threshold stats, 503');
} finally {
  await stopChild();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
