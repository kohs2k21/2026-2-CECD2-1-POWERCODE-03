// #81 LLM worker dead import_csv reconcile vs the 010 prepared-dataset failure trigger, called from erd-v03.mjs
// inside the same disposable cluster. The reconcile statements are read from server/worker/pg_queue.py (text only,
// no runtime import) and run by a real LOGIN role in powercode_llm on new connections, in one transaction like the runner.
// erd_llm_grant_81_pre applies database-roles.sql without the #81 grant (pre-fix control); erd_llm_grant_81 applies it as is.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

export const LLM_PREPARED_GRANT =
  'GRANT SELECT(created_job_id,status,validation_result),UPDATE(status,validation_result) ON data_prepared_datasets TO powercode_llm;';
const GRANTED = [['SELECT','created_job_id'],['SELECT','status'],['SELECT','validation_result'],['UPDATE','status'],['UPDATE','validation_result']];

export async function llmPreparedGrant81({ Client, port, connection, check, failures, migrations, rolesSql, insert, infra }) {
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const pass = (label, ok, detail) => ok ? check(label) : fail(label, detail);

  // Exact reconcile_dead_jobs SQL for the training queue.
  const pgQueue = await readFile(join(infra, '..', 'worker', 'pg_queue.py'), 'utf8');
  const start = pgQueue.indexOf('def reconcile_dead_jobs');
  assert(start >= 0, 'pg_queue.reconcile_dead_jobs present');
  const end = pgQueue.indexOf('\ndef ', start + 1);
  const reconcile = [...pgQueue.slice(start, end < 0 ? undefined : end).matchAll(/cur\.execute\(f"""([\s\S]*?)"""\)/g)]
    .map(m => m[1].replaceAll('{_table(queue)}', 'powercode_queue.training_jobs'));
  assert.equal(reconcile.length, 2, 'reconcile_dead_jobs has the attempt-expiry and job-failure statements');
  assert(reconcile.every(s => !/[{}]/.test(s)), 'reconcile SQL fully substituted');
  assert.equal(rolesSql.split(LLM_PREPARED_GRANT).length, 2, 'database-roles.sql carries the #81 grant exactly once');
  const rolesPre = rolesSql.replace(LLM_PREPARED_GRANT, '');

  const login = { user: 'qa81_llm', password: randomUUID() };
  const job = { contract_version:'v03', status:'queued', producer_kind:'user', producer_key:'admin81', requested_by_actor_id:'admin81',
    requester_role:'admin', max_attempts:3, attempt_count:0, queue:'training' };
  const hex = () => randomUUID().replaceAll('-', '').padEnd(64, '0');

  // Superuser fixtures (committed so the login connection sees them).
  const fixtures = c => {
    let source;
    const importJob = async () => insert(c, 'run_jobs', { ...job, kind:'import_csv', idempotency_key: randomUUID() });
    const dead = jobId => c.query(`INSERT INTO powercode_queue.training_jobs(id,payload,status) VALUES($1,$2,'dead')`, [randomUUID(), JSON.stringify({ jobId })]);
    const dataset = async (j, validation_result = { precheck: 'kept' }) => {
      source ??= await insert(c, 'ops_sources');
      const id = randomUUID();
      return insert(c, 'data_prepared_datasets', { id, source_id: source.id, manifest: { sourceId: source.id }, manifest_hash: hex(),
        original_relative_path: `prepared/${id}/original.csv`, original_hash: hex(), original_byte_size: 10, created_job_id: j.id,
        requested_by_actor_id: 'admin81', validation_result });
    };
    // Dead queued import_csv job with a queued set.
    const queuedDead = async () => { const j = await importJob(); const d = await dataset(j); await dead(j.id); return { j, d }; };
    // Dead running import_csv job whose crashed attempt lease already expired (both reconcile statements apply).
    const runningDead = async () => {
      const { j, d } = await queuedDead();
      const a = await insert(c, 'run_job_attempts', { job_id: j.id, attempt_no: 1, status: 'running', lease_until: new Date(Date.now() + 600000) });
      await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2", [a.id, j.id]);
      await c.query("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 minute' WHERE id=$1", [a.id]);
      return { j, d, a };
    };
    const readyDead = async () => {
      const { j, d } = await queuedDead();
      const snap = await insert(c, 'data_snapshots', { source_id: source.id, status: 'ready', schema_version: 'prepared-features-v1' });
      const h = hex();
      await c.query(`UPDATE data_prepared_datasets SET status='ready',normalized_relative_path=$2,normalized_hash=$3,row_count=1,
        row_metadata='[{}]',snapshot_id=$4,ready_at=clock_timestamp() WHERE id=$1`, [d.id, `prepared/${d.id}/${h}.jsonl`, h, snap.id]);
      return { j, d };
    };
    const failedDead = async () => {
      const { j, d } = await queuedDead();
      await c.query(`UPDATE data_prepared_datasets SET status='failed',validation_result='{"errorCode":"original"}' WHERE id=$1`, [d.id]);
      return { j, d };
    };
    const queuedAlive = async () => { const j = await importJob(); return { j, d: await dataset(j) }; };
    return { queuedDead, runningDead, readyDead, failedDead, queuedAlive, importJob, dataset };
  };
  const row = async (c, id) => (await c.query('SELECT to_jsonb(d.*) AS r FROM data_prepared_datasets d WHERE id=$1', [id])).rows[0].r;
  const jobRow = async (c, id) => (await c.query('SELECT status::text,error_code FROM run_jobs WHERE id=$1', [id])).rows[0];
  const attemptStatus = async (c, id) => (await c.query('SELECT status::text FROM run_job_attempts WHERE id=$1', [id])).rows[0].status;
  const privKey = p => `${p.level}:${p.privilege_type}:${p.col ?? '*'}`;
  const privileges = async c => (await c.query(`
    SELECT 'column' AS level, x.privilege_type, a.attname::text AS col FROM pg_attribute a, aclexplode(a.attacl) x
     WHERE a.attrelid='public.data_prepared_datasets'::regclass AND x.grantee='powercode_llm'::regrole
    UNION ALL SELECT 'table', x.privilege_type, NULL FROM pg_class r, aclexplode(r.relacl) x
     WHERE r.oid='public.data_prepared_datasets'::regclass AND x.grantee='powercode_llm'::regrole`)).rows.map(privKey).sort();

  // One runner-like transaction as the LLM login on a new connection. Returns the error (or null).
  const asLlm = async (database, statements) => {
    const a = new Client({ host:'127.0.0.1', port, user: login.user, password: login.password, database });
    await a.connect();
    try {
      assert.equal((await a.query('SELECT current_user::text AS u')).rows[0].u, login.user, 'runs as the LLM login itself');
      await a.query('BEGIN');
      try { for (const [sql, params] of statements) await a.query(sql, params); await a.query('COMMIT'); return null; }
      catch (e) { await a.query('ROLLBACK'); return e; }
    } finally { await a.end(); }
  };
  const reconcileAsLlm = database => asLlm(database, reconcile.map(sql => [sql]));
  const denied = e => Boolean(e && e.code === '42501' && /data_prepared_datasets/.test(e.message));
  const describe = e => e ? `${e.code} ${e.message}` : 'accepted';

  const setup = async (database, roles) => {
    const admin = connection('postgres'); await admin.connect();
    try { await admin.query(`CREATE DATABASE ${database}`); } finally { await admin.end(); }
    const c = connection(database); await c.connect();
    for (const sql of migrations) await c.query(sql);
    await c.query(roles);
    if (!(await c.query('SELECT 1 FROM pg_roles WHERE rolname=$1', [login.user])).rowCount)
      await c.query(`CREATE ROLE ${login.user} LOGIN PASSWORD '${login.password}' IN ROLE powercode_llm`);
    return c;
  };

  // Pre-fix control: dev database-roles.sql without the #81 grant.
  {
    const db = 'erd_llm_grant_81_pre';
    const c = await setup(db, rolesPre);
    try {
      const f = fixtures(c);
      pass(`${db}: powercode_llm has no data_prepared_datasets privilege before #81`, (await privileges(c)).length === 0, JSON.stringify(await privileges(c)));
      const r = await f.runningDead();
      const e = await reconcileAsLlm(db);
      const [js, ds, as] = [await jobRow(c, r.j.id), (await row(c, r.d.id)).status, await attemptStatus(c, r.a.id)];
      pass(`${db}: LLM login dead import_csv reconcile fails 42501 on data_prepared_datasets (job ${js.status}, set ${ds}, attempt ${as} rolled back) [${describe(e)}]`,
        denied(e) && js.status === 'running' && ds === 'queued' && as === 'running', describe(e));
      // Same reconcile without any import_csv job succeeds: the 42501 comes from the 010 trigger path only.
      await c.query("UPDATE powercode_queue.training_jobs SET status='done' WHERE payload->>'jobId'=$1", [r.j.id]);
      const train = await insert(c, 'run_jobs', { ...job, kind:'train', idempotency_key: randomUUID() });
      await c.query(`INSERT INTO powercode_queue.training_jobs(id,payload,status) VALUES($1,$2,'dead')`, [randomUUID(), JSON.stringify({ jobId: train.id })]);
      const e2 = await reconcileAsLlm(db);
      pass(`${db}: LLM login dead train-job reconcile succeeds without the grant (control path)`, !e2 && (await jobRow(c, train.id)).status === 'failed', describe(e2));
    } finally { await c.end(); }
  }

  // Fixed: database-roles.sql as committed.
  const db = 'erd_llm_grant_81';
  const c = await setup(db, rolesSql);
  try {
    const f = fixtures(c);
    const expected = GRANTED.map(([privilege_type, col]) => privKey({ level:'column', privilege_type, col })).sort();
    const priv = await privileges(c);
    pass(`${db}: powercode_llm holds only SELECT(created_job_id,status,validation_result), UPDATE(status,validation_result), no table-level privilege`,
      JSON.stringify(priv) === JSON.stringify(expected), JSON.stringify(priv));

    const queued = await f.queuedDead(), running = await f.runningDead(), ready = await f.readyDead(), failed = await f.failedDead(), alive = await f.queuedAlive();
    const readyBefore = await row(c, ready.d.id), failedBefore = await row(c, failed.d.id), aliveBefore = await row(c, alive.d.id);
    const e = await reconcileAsLlm(db);
    pass(`${db}: LLM login dead import_csv reconcile commits`, !e, describe(e));
    for (const [label, r] of [['queued', queued], ['running', running]]) {
      const [js, d] = [await jobRow(c, r.j.id), await row(c, r.d.id)];
      pass(`${db}: ${label} dead import_csv job failed/queue_exhausted, its queued set failed with validation_result {precheck kept, errorCode queue_exhausted}`,
        js.status === 'failed' && js.error_code === 'queue_exhausted' && d.status === 'failed'
          && JSON.stringify(d.validation_result) === JSON.stringify({ precheck: 'kept', errorCode: 'queue_exhausted' }), JSON.stringify([js, d.status, d.validation_result]));
    }
    pass(`${db}: crashed attempt expired by the same reconcile`, await attemptStatus(c, running.a.id) === 'expired');
    for (const [label, r, before] of [['ready', ready, readyBefore], ['failed', failed, failedBefore]]) {
      const [js, after] = [await jobRow(c, r.j.id), await row(c, r.d.id)];
      pass(`${db}: dead job of a ${label} set is failed, the ${label} set row is unchanged`, js.status === 'failed' && JSON.stringify(after) === JSON.stringify(before),
        JSON.stringify([js, after]));
    }
    pass(`${db}: queued set whose job has no dead queue item is untouched`, JSON.stringify(await row(c, alive.d.id)) === JSON.stringify(aliveBefore)
      && (await jobRow(c, alive.j.id)).status === 'queued');

    // Least privilege for direct statements by the LLM login.
    const spareId = randomUUID();
    const direct = [
      ['SELECT id', 'SELECT id FROM data_prepared_datasets', [], '42501'],
      ['SELECT manifest', 'SELECT manifest FROM data_prepared_datasets', [], '42501'],
      ['SELECT row_metadata', 'SELECT row_metadata FROM data_prepared_datasets', [], '42501'],
      ['SELECT *', 'SELECT * FROM data_prepared_datasets', [], '42501'],
      ['UPDATE manifest_hash', 'UPDATE data_prepared_datasets SET manifest_hash=$1 WHERE created_job_id=$2', [hex(), alive.j.id], '42501'],
      ['UPDATE manifest', `UPDATE data_prepared_datasets SET manifest='{}' WHERE created_job_id=$1`, [alive.j.id], '42501'],
      ['UPDATE snapshot_id', 'UPDATE data_prepared_datasets SET snapshot_id=NULL WHERE created_job_id=$1', [alive.j.id], '42501'],
      ['UPDATE normalized_relative_path', 'UPDATE data_prepared_datasets SET normalized_relative_path=NULL WHERE created_job_id=$1', [alive.j.id], '42501'],
      ['UPDATE row_count', 'UPDATE data_prepared_datasets SET row_count=1 WHERE created_job_id=$1', [alive.j.id], '42501'],
      ['UPDATE ready_at', 'UPDATE data_prepared_datasets SET ready_at=NULL WHERE created_job_id=$1', [alive.j.id], '42501'],
      ['INSERT', `INSERT INTO data_prepared_datasets(id,source_id,manifest,manifest_hash,original_relative_path,original_hash,original_byte_size,created_job_id,requested_by_actor_id)
        VALUES($1,$2,$3,$4,$5,$4,10,(SELECT id FROM run_jobs WHERE kind::text='import_csv' ORDER BY created_at DESC LIMIT 1),'admin81')`,
        [spareId, aliveBefore.source_id, JSON.stringify({ sourceId: aliveBefore.source_id }), hex(), `prepared/${spareId}/original.csv`], '42501'],
      ['DELETE', 'DELETE FROM data_prepared_datasets WHERE created_job_id=$1', [alive.j.id], '42501'],
      ['TRUNCATE', 'TRUNCATE data_prepared_datasets', [], '42501'],
      // Granted columns cannot reopen or rewrite finished sets: the 010 protect trigger still applies.
      ['ready set -> failed', `UPDATE data_prepared_datasets SET status='failed' WHERE created_job_id=$1`, [ready.j.id], 'P0001'],
      ['failed set -> queued', `UPDATE data_prepared_datasets SET status='queued' WHERE created_job_id=$1`, [failed.j.id], 'P0001'],
      ['failed set validation_result rewrite', `UPDATE data_prepared_datasets SET validation_result='{}' WHERE created_job_id=$1`, [failed.j.id], 'P0001'],
    ];
    for (const [label, sql, params, code] of direct) {
      const err = await asLlm(db, [[sql, params]]);
      pass(`${db}: LLM login ${label} rejected ${code} [${describe(err)}]`, code === '42501' ? denied(err) : Boolean(err && err.code === code), describe(err));
    }
    pass(`${db}: ready/failed/unrelated sets unchanged after direct LLM attempts`,
      JSON.stringify(await row(c, ready.d.id)) === JSON.stringify(readyBefore) && JSON.stringify(await row(c, failed.d.id)) === JSON.stringify(failedBefore)
        && JSON.stringify(await row(c, alive.d.id)) === JSON.stringify(aliveBefore));
    const ok = await asLlm(db, [['SELECT created_job_id,status,validation_result FROM data_prepared_datasets WHERE created_job_id=$1', [alive.j.id]]]);
    pass(`${db}: LLM login reads only the granted columns`, !ok, describe(ok));
    console.log('INFO #81 residual (same level as 011): the LLM login can still move a queued set to failed and set its validation_result directly');

    // Column minimality: removing any one granted column breaks the reconcile.
    const stuck = [];
    for (const [p, col] of GRANTED) {
      await c.query(`REVOKE ${p}(${col}) ON data_prepared_datasets FROM powercode_llm`);
      const r = await f.queuedDead(); stuck.push(r);
      const err = await reconcileAsLlm(db);
      const [js, ds] = [await jobRow(c, r.j.id), (await row(c, r.d.id)).status];
      pass(`${db}: without ${p}(${col}) the dead import_csv reconcile fails 42501 and rolls back (job ${js.status}, set ${ds})`,
        denied(err) && js.status === 'queued' && ds === 'queued', describe(err));
      await c.query(`GRANT ${p}(${col}) ON data_prepared_datasets TO powercode_llm`);
    }
    pass(`${db}: grants restored to the committed set after the minimality loop`, JSON.stringify(await privileges(c)) === JSON.stringify(expected));
    const e3 = await reconcileAsLlm(db);
    const healed = [];
    for (const r of stuck) healed.push([(await jobRow(c, r.j.id)).status, (await row(c, r.d.id)).status]);
    pass(`${db}: with the full grant the ${stuck.length} left-over dead import_csv jobs and sets are reconciled to failed`,
      !e3 && healed.every(([j, d]) => j === 'failed' && d === 'failed'), `${describe(e3)} ${JSON.stringify(healed)}`);
  } finally { await c.end(); }
}
