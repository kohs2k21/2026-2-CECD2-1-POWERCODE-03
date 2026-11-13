// 입력 변수 세트 등록 API(models-ui.md 2단계, trainer-service.md 입력 변수 세트 등록 규칙) 통합 시험.
// 로컬 새 QA DB(qa_feature_sets*)에 001~022가 적용돼 있어야 한다. API 프로세스는 powercode_api 역할로 접속한다. 실행 전 `npm run build`.
// 확인: 인증·관리자·거절 코드(벡터 거절 요청 포함), 벡터 4사례의 세트·버전·멤버 열과 hash, 같은 조합 재사용(요청 순서 무관),
// 변수 버전 재사용(이름·식 hash·종류, 가장 낮은 version)과 종류 무관 max+1, advisory lock 대기·동시 요청 1건 생성, 목록 지원 여부, 행 불변.
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
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_feature_sets[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_feature_sets database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, '..');
const vectors = JSON.parse(await readFile(path.join(here, '../../contracts/feature-set-hash-vectors.json'), 'utf8'));
const temporary = await mkdtemp(path.join(os.tmpdir(), 'powercode-feature-sets-'));
const pool = new pg.Pool({ connectionString: url });
const apiUrl = new URL(url);
apiUrl.searchParams.set('options', '-c role=powercode_api');
const api = new pg.Pool({ connectionString: apiUrl.toString() });
let child;

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
const sha = (text) => crypto.createHash('sha256').update(text).digest('hex');
const maxVersions = async (names) => {
  const { rows } = await pool.query('SELECT definition_key, max(version) AS v FROM ml_feature_versions WHERE definition_key = ANY($1) GROUP BY 1', [names]);
  const found = Object.fromEntries(rows.map((row) => [row.definition_key, row.v]));
  return Object.fromEntries(names.map((name) => [name, found[name] ?? 0]));
};
const membersOf = async (setId) => (await pool.query(
  `SELECT m.output_name, m.output_position, m.model_input, m.transform_settings, v.id, v.definition_key, v.version, v.display_name,
          v.definition_kind, v.expression, v.output_type, v.output_unit, v.value_policy, v.availability_spec, v.requires_fit,
          v.content_hash, v.created_by_actor_id, v.created_at
     FROM ml_feature_set_members m JOIN ml_feature_versions v ON v.id = m.feature_version_id
    WHERE m.feature_set_id = $1 ORDER BY m.output_position`, [setId])).rows;

try {
  await pool.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ml_feature_sets')).rows[0].n), 0, 'use a new qa_feature_sets database');
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api', 'API connection string runs as the runtime role');

  const port = await freePort();
  const environment = { PATH: process.env.PATH, NODE_ENV: 'test', DATABASE_URL: apiUrl.toString(), JWT_SECRET: 'qa-feature-sets-local-only-secret',
    SEED_USER_EMAIL: 'feature-user@example.test', SEED_USER_PASSWORD: 'qa-feature-user-local-only-password',
    SEED_ADMIN_EMAIL: 'feature-admin@example.test', SEED_ADMIN_PASSWORD: 'qa-feature-admin-local-only-password',
    USER_DATA_PATH: path.join(temporary, 'users.json'), PORT: String(port) };
  child = spawn(process.execPath, [path.join(backend, 'dist/index.js')], { cwd: temporary, env: environment, stdio: 'ignore' });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let attempt = 0; attempt < 50 && !ready; attempt += 1) {
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* 서버 시작을 기다린다. */ }
    if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(ready, true);
  const call = async (route, method = 'GET', token, body, raw) => {
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined || raw !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(`${base}${route}`, { method, headers, body: raw ?? (body === undefined ? undefined : JSON.stringify(body)) });
    return { status: response.status, body: await response.json() };
  };
  const login = async (email, password) => (await call('/api/auth/login', 'POST', undefined, { email, password })).body.token;
  const token = await login(environment.SEED_ADMIN_EMAIL, environment.SEED_ADMIN_PASSWORD);
  const userToken = await login(environment.SEED_USER_EMAIL, environment.SEED_USER_PASSWORD);
  const actorId = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).id;
  const register = (fields, as = token) => call('/api/jobs/feature-sets', 'POST', as, { fields });

  // 1) 필드 목록·인증·관리자·거절 코드. 거절은 행을 남기지 않는다.
  const list = await call('/api/jobs/feature-sets/fields', 'GET', userToken);
  assert.equal(list.status, 200);
  assert.deepEqual(list.body.fields.map((field) => field.key), ['total_count', 'success_count', 'error_count', 'retry_count', 'elapsed_ms', 'error_ratio']);
  assert.equal(list.body.version, 'ml-feature-fields-v1');
  assert.equal((await call('/api/jobs/feature-sets/fields')).status, 401);
  assert.equal((await call('/api/jobs/feature-sets', 'POST', undefined, { fields: ['error_count'] })).status, 401);
  assert.deepEqual(await register(['error_count'], userToken), { status: 403, body: { code: 'admin_required' } });
  const statusOf = { invalid_feature_count: 400, duplicate_feature_field: 400, unknown_feature_field: 422 };
  for (const { fields, error } of vectors.invalidRequests) assert.deepEqual(await register(fields), { status: statusOf[error], body: { code: error } }, JSON.stringify(fields));
  assert.deepEqual(await call('/api/jobs/feature-sets', 'POST', token, {}), { status: 400, body: { code: 'invalid_feature_count' } });
  assert.deepEqual(await register(Array(33).fill('error_count')), { status: 400, body: { code: 'invalid_feature_count' } });
  assert.deepEqual(await register(['x', 'x']), { status: 422, body: { code: 'unknown_feature_field' } });
  assert.deepEqual(await call('/api/jobs/feature-sets', 'POST', token, { fields: ['error_count'], name: 'x' }), { status: 400, body: { code: 'invalid_job_request' } });
  assert.deepEqual(await call('/api/jobs/feature-sets', 'POST', token, ['error_count']), { status: 400, body: { code: 'invalid_job_request' } });
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ml_feature_versions')).rows[0].n), 0, 'rejections write nothing');

  // 2) 버전 번호 시드(관리 연결): retry_count는 같은 식의 model_generated v1(재사용 안 함 → v2), success_count는 다른 식의 operator_defined v1(→ v2),
  //    total_count는 같은 식 operator_defined v3·v5(가장 낮은 v3 재사용).
  const allSix = vectors.cases.find((item) => item.id === 'all_six');
  const memberOf = (name) => allSix.members.find((member) => member.name === name);
  const exact = (name) => ({ expression: JSON.parse(memberOf(name).canonicalExpressionJson) });
  const insertSeed = async (name, version, kind, expression, hash) => {
    const member = memberOf(name), id = crypto.randomUUID();
    await pool.query(`INSERT INTO ml_feature_versions(id,definition_key,version,display_name,definition_kind,expression,output_type,output_unit,
        value_policy,availability_spec,requires_fit,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,$3,$2,$4,$5::jsonb,$6,$7,$8::jsonb,$9::jsonb,false,$10,'feature-sets-fixture',clock_timestamp())`,
    [id, name, version, kind, JSON.stringify(expression), member.output_type, member.output_unit, JSON.stringify(member.value_policy),
      JSON.stringify(member.availability_spec), hash]);
    return id;
  };
  await insertSeed('retry_count', 1, 'model_generated', exact('retry_count').expression, memberOf('retry_count').expressionHash);
  await insertSeed('success_count', 1, 'operator_defined', { op: 'field', field: 'P.total_count' }, memberOf('total_count').expressionHash);
  const totalV3 = await insertSeed('total_count', 3, 'operator_defined', exact('total_count').expression, memberOf('total_count').expressionHash);
  await insertSeed('total_count', 5, 'operator_defined', exact('total_count').expression, memberOf('total_count').expressionHash);
  const expectedReuse = { total_count: totalV3 };

  // 3) 벡터 4사례: 응답·세트 열·멤버 열이 벡터와 같고, 같은 이름·식은 세트가 달라도 같은 변수 버전이다.
  const versionIds = {};
  const ids = {};
  for (const vector of vectors.cases) {
    const names = vector.definitions.map((item) => item.name);
    const before = await maxVersions(names);
    const reusable = Object.fromEntries((await pool.query(`SELECT DISTINCT ON (definition_key) definition_key, id FROM ml_feature_versions
        WHERE definition_kind='operator_defined' AND (definition_key, content_hash) IN (SELECT * FROM unnest($1::text[], $2::text[]))
        ORDER BY definition_key, version`, [names, vector.members.map((member) => member.expressionHash)])).rows.map((row) => [row.definition_key, row.id]));
    const created = await register(vector.fields);
    assert.equal(created.status, 201, vector.id);
    assert.deepEqual({ ...created.body, id: undefined }, { id: undefined, contentHash: vector.contentHash, featureNames: vector.featureSet.output_schema, reused: false }, vector.id);
    ids[vector.id] = created.body.id;
    const again = await register([...vector.fields].reverse());
    assert.deepEqual({ status: again.status, body: again.body }, { status: 200, body: { ...created.body, reused: true } }, `${vector.id} reuse`);
    const set = (await pool.query('SELECT schema_version, transform_version, output_schema, content_hash, created_by_actor_id FROM ml_feature_sets WHERE id=$1', [created.body.id])).rows[0];
    assert.deepEqual(set, { ...vector.featureSet, content_hash: vector.contentHash, created_by_actor_id: actorId }, vector.id);
    assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ml_feature_sets WHERE content_hash=$1', [vector.contentHash])).rows[0].n), 1);
    const rows = await membersOf(created.body.id);
    assert.equal(rows.length, vector.members.length);
    const fresh = [];
    rows.forEach((row, index) => {
      const member = vector.members[index];
      const { canonicalExpressionJson, ...expected } = member;
      assert.deepEqual({ name: row.output_name, expressionHash: row.content_hash, definition_key: row.definition_key, display_name: row.display_name,
        definition_kind: row.definition_kind, output_type: row.output_type, output_unit: row.output_unit, value_policy: row.value_policy,
        availability_spec: row.availability_spec, requires_fit: row.requires_fit, model_input: row.model_input, output_position: row.output_position,
        output_name: row.output_name, transform_settings: row.transform_settings }, expected, `${vector.id} ${member.name}`);
      assert.deepEqual(row.expression, vector.definitions[index].expression);
      assert.equal(sha(canonicalExpressionJson), row.content_hash);
      assert.equal(versionIds[row.output_name] ??= row.id, row.id, `${vector.id} ${row.output_name} same version across sets`);
      if (reusable[row.output_name]) assert.equal(row.id, reusable[row.output_name], `${vector.id} ${row.output_name} reuses`);
      else {
        assert.equal(row.version, before[row.output_name] + 1, `${vector.id} ${row.output_name} max+1`);
        assert.equal(row.created_by_actor_id, actorId);
        fresh.push(row.created_at.getTime());
      }
    });
    if (fresh.length > 1) assert.equal(new Set(fresh).size, fresh.length, 'clock_timestamp per insert');
  }
  assert.equal(new Set(Object.values(ids)).size, vectors.cases.length);
  assert.equal(versionIds.total_count, expectedReuse.total_count, 'lowest matching version reused');
  const versionOf = async (name) => (await pool.query('SELECT version FROM ml_feature_versions WHERE id=$1', [versionIds[name]])).rows[0].version;
  assert.deepEqual({ retry: await versionOf('retry_count'), success: await versionOf('success_count'), error: await versionOf('error_count'), total: await versionOf('total_count') },
    { retry: 2, success: 2, error: 1, total: 3 }, 'kind-independent max+1, hash mismatch is a new version');

  // 4) advisory lock: 다른 거래가 같은 lock을 잡고 있으면 등록은 기다린다.
  const holder = await pool.connect();
  let waited;
  try {
    await holder.query('BEGIN');
    await holder.query('SELECT pg_advisory_xact_lock(81732, 1)');
    let settled = false;
    const pending = register(['success_count', 'error_ratio']).then((value) => { settled = true; return value; });
    await new Promise((resolve) => setTimeout(resolve, 400));
    waited = !settled;
    await holder.query('COMMIT');
    assert.equal((await pending).status, 201);
  } finally { holder.release(); }
  assert.equal(waited, true, 'registration waits for pg_advisory_xact_lock(81732,1)');

  // 5) 동시 같은 조합 8건: 생성 1건, 나머지는 같은 id 재사용. 다른 조합이 섞여도 버전 번호가 겹치지 않는다.
  const race = await Promise.all([...Array(8)].map((_, index) => register(index % 2 ? ['retry_count', 'success_count'] : ['success_count', 'retry_count']))
    .concat([register(['retry_count', 'elapsed_ms']), register(['error_count', 'elapsed_ms', 'success_count'])]));
  const same = race.slice(0, 8);
  assert.deepEqual(same.map((item) => item.status).sort(), [200, 200, 200, 200, 200, 200, 200, 201]);
  assert.equal(new Set(same.map((item) => item.body.id)).size, 1);
  assert.deepEqual(race.slice(8).map((item) => item.status), [201, 201]);
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ml_feature_sets WHERE content_hash=$1', [same[0].body.contentHash])).rows[0].n), 1);
  assert.equal(Number((await pool.query('SELECT count(*) AS n FROM ml_feature_set_members WHERE feature_set_id=$1', [same[0].body.id])).rows[0].n), 2);

  // 6) 목록: 등록한 세트가 최신순 맨 앞에 있고 train 접수 검사를 통과한다(supported=true).
  const sourceId = crypto.randomUUID();
  await pool.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,'Feature set test','synthetic-test','v1','{}','{}',clock_timestamp())`, [sourceId, `feature-sets-${sourceId}`]);
  const sets = await call(`/api/jobs/feature-sets?sourceId=${sourceId}`, 'GET', token);
  assert.equal(sets.status, 200);
  const listed = new Map(sets.body.items.map((item) => [item.id, item]));
  for (const vector of vectors.cases) {
    assert.deepEqual(listed.get(ids[vector.id]), { id: ids[vector.id], contentHash: vector.contentHash, featureNames: vector.featureSet.output_schema, supported: true, reason: null });
  }
  assert.ok(sets.body.items.every((item) => item.supported), 'every registered set is trainable');

  // 7) API 역할: 세트·버전·멤버 수정·삭제 불가(INSERT만 부여, 004 불변 trigger).
  const denied = async (sql) => { try { await api.query(sql); return 'allowed'; } catch (error) { return error.code; } };
  assert.deepEqual({
    updateSet: await denied(`UPDATE ml_feature_sets SET schema_version='x'`),
    deleteMember: await denied('DELETE FROM ml_feature_set_members'),
    updateVersion: await denied(`UPDATE ml_feature_versions SET display_name='x'`),
  }, { updateSet: '42501', deleteMember: '42501', updateVersion: '42501' });
  console.log(`feature set registration API passed: ${vectors.cases.length} vectors, ${vectors.invalidRequests.length} rejections, reuse, version numbering, lock wait, concurrent registration`);
} finally {
  // 서버 작업 폴더를 지우기 전에 종료를 기다린다(Windows는 실행 중 폴더를 지우지 못한다).
  if (child && child.exitCode === null) await new Promise((resolve) => { child.once('exit', resolve); child.kill(); });
  await api.end();
  await pool.end();
  await rm(temporary, { recursive: true, force: true });
}
