// 013 (#82): search_path setting of the 001-010 trigger functions, called from erd-v03.mjs inside the same disposable cluster.
// Databases: erd82_upgrade (001-012 + existing rows -> 013), erd82_fresh (001-013), erd82_rollback (013 fails before COMMIT).
// Checks: function settings inventory, unchanged bodies/owners/SECURITY INVOKER, rows kept, rollback, re-apply, DBML parity.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const TARGETS = ['erd_v03_protect_observation','erd_v03_validate_artifact','erd_v03_validate_body','erd_v03_validate_manifest',
  'erd_v03_validate_result','fail_prepared_dataset_with_job','protect_prepared_dataset','protect_ready_snapshot_members'];
const PUBLIC_PIN = 'search_path=public, pg_temp';
const CATALOG_PIN = 'search_path=pg_catalog, pg_temp';
// Every function in every non-system schema after 001-013: [proconfig, prosecdef].
const INVENTORY = {
  ...Object.fromEntries(TARGETS.map(n => [n, [PUBLIC_PIN, false]])),
  // 011 (#78 A): created or redefined with the same setting.
  erd_v03_validate_job: [PUBLIC_PIN, false], protect_evaluation_set: [PUBLIC_PIN, false],
  fail_evaluation_set_with_job: [PUBLIC_PIN, false], validate_comparison_lineage: [PUBLIC_PIN, false],
  // 012 (#78 B): pg_catalog, pg_temp; two owner-context SECURITY DEFINER functions.
  validate_decision_interim: [CATALOG_PIN, false], supersede_decision_interims: [CATALOG_PIN, true],
  close_decision_interims_on_job_failure: [CATALOG_PIN, true], publish_decision_event: [CATALOG_PIN, false],
  publish_decision_interim_event: [CATALOG_PIN, false], reject_decision_after_no_final: [CATALOG_PIN, false],
  // No table or row-type reference: left unpinned (#82 scope).
  erd_v03_immutable: [null, false], erd_v03_guard_decision: [null, false], prevent_rule_version_mutation: [null, false],
  prevent_pipeline_immutable_mutation: [null, false], guard_demo_state: [null, false], guard_input_replay: [null, false],
  guard_input_run: [null, false],
};
const procQuery = `SELECT n.nspname AS schema, p.proname AS name, l.lanname AS language, p.proconfig AS config, p.prosecdef AS definer,
  pg_catalog.pg_get_userbyid(p.proowner) AS owner, md5(p.prosrc) AS body
  FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang
  WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
  ORDER BY 1,2`;
const procs = async c => (await c.query(procQuery)).rows;
const setting = r => (r.config ? r.config.join(';') : null);

export async function searchPath013({ connection, check, failures, migrations, migrationNames, parity, insert }) {
  const idx013 = migrationNames.findIndex(n => n.startsWith('013_'));
  assert(idx013 > 0, '013 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);

  // All functions are public plpgsql and match INVENTORY exactly (no function added, dropped or left out).
  async function inventory(c, database) {
    const rows = await procs(c);
    const names = rows.map(r => r.name).sort();
    verdict(JSON.stringify(names) === JSON.stringify(Object.keys(INVENTORY).sort()) && rows.every(r => r.schema === 'public' && r.language === 'plpgsql'),
      `${database}: function inventory is the 25 public plpgsql functions`, JSON.stringify(rows.map(r => `${r.schema}.${r.name}:${r.language}`)));
    const wrong = rows.filter(r => INVENTORY[r.name] && (setting(r) !== INVENTORY[r.name][0] || r.definer !== INVENTORY[r.name][1]));
    verdict(wrong.length === 0, `${database}: 8 targets ${PUBLIC_PIN} SECURITY INVOKER; 011 x4 and 012 x6 settings kept; 7 table-free functions unpinned`,
      JSON.stringify(wrong.map(r => `${r.name}=${setting(r)}${r.definer ? '(definer)' : ''}`)));
  }

  // Only proconfig of the 8 targets may differ; body (md5 of prosrc), owner and SECURITY DEFINER flag stay identical.
  function compare(before, after, database) {
    const key = r => `${r.schema}.${r.name}`;
    const map = new Map(after.map(r => [key(r), r]));
    const diffs = [];
    for (const b of before) {
      const a = map.get(key(b));
      if (!a) { diffs.push(`${key(b)} missing`); continue; }
      if (a.body !== b.body || a.owner !== b.owner || a.definer !== b.definer || a.language !== b.language) diffs.push(`${key(b)} body/owner/definer changed`);
      const expected = TARGETS.includes(b.name) ? PUBLIC_PIN : setting(b);
      if (setting(a) !== expected) diffs.push(`${key(b)} setting ${setting(b)} -> ${setting(a)}`);
      if (TARGETS.includes(b.name) && setting(b) !== null) diffs.push(`${key(b)} was already set before 013`);
    }
    if (after.length !== before.length) diffs.push(`function count ${before.length} -> ${after.length}`);
    verdict(diffs.length === 0, `${database}: 013 changes only the 8 targets' search_path (bodies, owners, SECURITY INVOKER, other settings unchanged)`, diffs.join('; '));
  }

  // Rows written before 013 in the tables the 8 target functions read or protect.
  const keptTables = ['ops_sources','data_observations','data_snapshots','data_snapshot_members','run_jobs','data_prepared_datasets','ml_artifacts'];
  const rowsOf = async c => {
    const rows = {};
    for (const t of keptTables) rows[t] = (await c.query(`SELECT row_to_json(x)::text AS r FROM public.${t} x ORDER BY 1`)).rows.map(r => r.r);
    return rows;
  };
  async function seed(c) {
    const src = await insert(c, 'ops_sources', { dataset_key:`qa82-${randomUUID()}` });
    const e = await insert(c, 'data_executions', { source_id:src.id, layer:'P' });
    const o = await insert(c, 'data_observations', { source_id:src.id, layer:'P', execution_id:e.id, revision_no:1 });
    const snap = await insert(c, 'data_snapshots', { source_id:src.id, status:'building' });
    await insert(c, 'data_snapshot_members', { snapshot_id:snap.id, source_id:src.id, observation_id:o.id, split:'train' });
    await c.query("UPDATE data_snapshots SET status='ready' WHERE id=$1", [snap.id]);
    const job = await insert(c, 'run_jobs', { contract_version:'v03', kind:'import_csv', queue:'training', status:'queued', producer_kind:'user',
      producer_key:'admin82', requested_by_actor_id:'admin82', requester_role:'admin', idempotency_key:randomUUID(), max_attempts:3, attempt_count:0 });
    const id = randomUUID();
    await insert(c, 'data_prepared_datasets', { id, source_id:src.id, manifest:{ sourceId:src.id }, manifest_hash:'a'.repeat(64),
      original_relative_path:`prepared/${id}/original.csv`, original_hash:'b'.repeat(64), original_byte_size:10, created_job_id:job.id, requested_by_actor_id:'admin82' });
    const features = await insert(c, 'ml_feature_sets');
    await insert(c, 'ml_artifacts', { kind:'fit', snapshot_id:snap.id, feature_set_id:features.id });
    return { job, id };
  }

  const admin = connection('postgres'); await admin.connect();
  try { for (const name of ['erd82_upgrade','erd82_fresh','erd82_rollback']) await admin.query(`CREATE DATABASE ${name}`); }
  finally { await admin.end(); }

  // Upgrade: 001-012 + rows -> 013. Rows kept; behaviour after 013 still syncs a failed import_csv job.
  {
    const c = connection('erd82_upgrade'); await c.connect();
    try {
      for (let i = 0; i < idx013; i++) await c.query(migrations[i]);
      const before = await procs(c);
      verdict(TARGETS.every(n => before.some(r => r.name === n && r.config === null)), 'erd82_upgrade: before 013 the 8 targets have no search_path setting',
        JSON.stringify(before.filter(r => TARGETS.includes(r.name)).map(r => `${r.name}=${setting(r)}`)));
      const seeded = await seed(c);
      const rows = await rowsOf(c);
      for (let i = idx013; i < migrations.length; i++) await c.query(migrations[i]);
      check(`erd82_upgrade: 001-012 + existing rows, then ${migrationNames.slice(idx013).join(', ')} apply`);
      assert.deepEqual(await rowsOf(c), rows, 'rows written before 013 are kept unchanged');
      check(`erd82_upgrade: rows written before 013 kept unchanged (${keptTables.join(', ')})`);
      compare(before, await procs(c), 'erd82_upgrade');
      await inventory(c, 'erd82_upgrade');
      await c.query('BEGIN');
      try {
        await c.query("UPDATE run_jobs SET status='failed',error_code='qa82_failed' WHERE id=$1", [seeded.job.id]);
        const ds = (await c.query('SELECT status,validation_result FROM data_prepared_datasets WHERE id=$1', [seeded.id])).rows[0];
        verdict(ds.status === 'failed' && ds.validation_result.errorCode === 'qa82_failed',
          'erd82_upgrade: after 013 a failed import_csv job still fails its queued prepared dataset', JSON.stringify(ds));
      } finally { await c.query('ROLLBACK'); }
      await parity(c);
    } finally { await c.end(); }
  }

  // Fresh 001-013, then 013 once more: same settings (ALTER … SET is repeatable).
  {
    const c = connection('erd82_fresh'); await c.connect();
    try {
      for (const sql of migrations) await c.query(sql);
      check(`erd82_fresh: 001 through ${migrationNames.at(-1)} apply`);
      await inventory(c, 'erd82_fresh');
      const once = await procs(c);
      await c.query(migrations[idx013]);
      assert.deepEqual(await procs(c), once, '013 re-run leaves every function unchanged');
      check('erd82_fresh: 013 re-run leaves every function unchanged');
      await parity(c);
    } finally { await c.end(); }
  }

  // 013 fails just before COMMIT (psql -v ON_ERROR_STOP=1 -f semantics): nothing changes; then it applies cleanly.
  {
    const c = connection('erd82_rollback'); await c.connect();
    try {
      for (let i = 0; i < idx013; i++) await c.query(migrations[i]);
      const before = await procs(c);
      const text = migrations[idx013].replaceAll('\r\n', '\n');
      const commit = text.lastIndexOf('\nCOMMIT;');
      const begin = text.search(/^BEGIN;$/m);
      assert(begin >= 0 && commit > begin, '013 is one BEGIN..COMMIT block');
      const faulty = text.slice(0, commit + 1) + 'SELECT 1/0; -- injected failure after every ALTER FUNCTION\n' + text.slice(commit + 1);
      let error = null;
      try { await c.query(faulty); } catch (e) { error = e; }
      await c.query('ROLLBACK');
      assert(error && error.code === '22012', `injected 013 failure must abort the migration (${error?.message})`);
      assert.deepEqual(await procs(c), before, '013 changes rolled back');
      check('erd82_rollback: 013 failing before COMMIT leaves every function setting unchanged');
      await c.query(migrations[idx013]);
      await inventory(c, 'erd82_rollback');
      await parity(c);
    } finally { await c.end(); }
  }
}
