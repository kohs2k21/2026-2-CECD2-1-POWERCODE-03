// 031: judgment store measurement stage (contracts/detection-decision.md 19), called from erd-v03.mjs inside the same disposable cluster.
// erd031_upgrade: every file before 031 + feature (with config) and detector (config NULL) rows written with the pre-031 statements,
//   then 031 as one string: rows kept, only the two CHECKs change (same names, validated), judgment_store accepted with config NULL,
//   other stage values and feature without config still rejected, re-run gives the same catalog, remaining migrations, DBML parity.
// erd031_fresh: every file as fresh, same two CHECK definitions as the upgrade path. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { catalog, delta, nameOf } from './erd-v03-detect-v04.mjs';

const TABLE = 'ops_pipeline_measurements';
const STAGE_CHECK = 'ops_pipeline_measurements_stage_check';
const CONFIG_CHECK = 'ops_pipeline_measurements_config_check';

export async function judgmentStore031({ connection, check, failures, migrations, migrationNames, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('031_'));
  assert(idx > 0, '031 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const no = async (c, label, constraint, run) => {
    await c.query('SAVEPOINT judgment_store_case');
    try { await run(); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === '23514' && e.constraint === constraint, `${label} (23514 ${constraint})`, `${e.code} ${e.constraint ?? ''} ${e.message}`); }
    finally { await c.query('ROLLBACK TO SAVEPOINT judgment_store_case'); await c.query('RELEASE SAVEPOINT judgment_store_case'); }
  };
  // Same statement shape as detector/attempt_v04.py and feature_worker.py (no RETURNING, Detector has no SELECT on this table).
  const measure = (c, observation, stage, configId) => c.query(`INSERT INTO ${TABLE}(id,observation_id,stage,config_version_id,duration_ms) VALUES($1,$2,$3,$4,1.5)`,
    [randomUUID(), observation, stage, configId]);
  const checks = async c => (await c.query(`SELECT conname::text AS name,convalidated,pg_get_constraintdef(oid) AS def FROM pg_constraint
    WHERE conrelid='${TABLE}'::regclass AND conname IN ($1,$2) ORDER BY conname`, [CONFIG_CHECK, STAGE_CHECK])).rows;
  const admin = connection('postgres'); await admin.connect();
  try { for (const db of ['erd031_upgrade', 'erd031_fresh']) await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }

  let upgraded;
  const db = 'erd031_upgrade';
  const c = connection(db); await c.connect();
  try {
    for (const sql of migrations.slice(0, idx)) await c.query(sql);
    const source = await insert(c, 'ops_sources', { dataset_key:`qa031-${randomUUID()}` });
    const execution = await insert(c, 'data_executions', { source_id:source.id, layer:'P' });
    const observation = (await insert(c, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1 })).id;
    const config = (await insert(c, 'ml_config_versions', { source_id:source.id })).id;
    await measure(c, observation, 'feature', config);
    await measure(c, observation, 'detector', null);
    // With a config so only the stage CHECK can fail (CHECKs are evaluated in name order: config_check would fire first for NULL).
    // no() uses SAVEPOINT, which needs an open transaction block.
    await c.query('BEGIN');
    try { await no(c, `${db}: before 031 stage judgment_store rejected`, STAGE_CHECK, () => measure(c, observation, 'judgment_store', config)); }
    finally { await c.query('ROLLBACK'); }
    const rows = async () => (await c.query(`SELECT row_to_json(x)::text AS r FROM ${TABLE} x ORDER BY stage`)).rows.map(r => r.r);
    const before = await catalog(c), rowsBefore = await rows();

    await c.query(migrations[idx]);
    check(`${db}: 001 through ${migrationNames[idx - 1]} + feature/detector rows, then ${migrationNames[idx]} applies as one string`);
    const after = await catalog(c);
    const con = delta(before.constraints, after.constraints);
    assert.deepEqual([con.removed.map(nameOf).sort(), con.added.map(nameOf).sort()],
      [[`${TABLE}.${CONFIG_CHECK}`, `${TABLE}.${STAGE_CHECK}`], [`${TABLE}.${CONFIG_CHECK}`, `${TABLE}.${STAGE_CHECK}`]], JSON.stringify(con));
    for (const key of ['enums', 'columns', 'triggers', 'indexes']) assert.deepEqual(after[key], before[key], `031 leaves ${key} unchanged`);
    assert.deepEqual(after.functions.map(f => [f.name, f.body, f.config, f.definer, f.owner]), before.functions.map(f => [f.name, f.body, f.config, f.definer, f.owner]), '031 leaves functions unchanged');
    check(`${db}: only ${STAGE_CHECK} and ${CONFIG_CHECK} change (same names); enums, columns, triggers, indexes and functions unchanged`);
    upgraded = await checks(c);
    verdict(upgraded.length === 2 && upgraded.every(r => r.convalidated && r.def.includes("'judgment_store'"))
      && ["'feature'", "'detector'"].every(v => upgraded[1].def.includes(v)) && upgraded[0].def.includes("'detector'") && !upgraded[0].def.includes("'feature'"),
      `${db}: both CHECKs validated; stage lists feature/detector/judgment_store, config NULL only for detector/judgment_store`, JSON.stringify(upgraded));
    verdict(JSON.stringify(await rows()) === JSON.stringify(rowsBefore), `${db}: existing feature and detector rows unchanged`);

    await c.query('BEGIN');
    try {
      await measure(c, observation, 'judgment_store', null);
      await measure(c, observation, 'judgment_store', config);
      await measure(c, observation, 'detector', null);
      verdict(true, `${db}: judgment_store accepted with config NULL and with a config; detector NULL still accepted`);
    } finally { await c.query('ROLLBACK'); }
    await c.query('BEGIN');
    try {
      await no(c, `${db}: feature stage without config still rejected`, CONFIG_CHECK, () => measure(c, observation, 'feature', null));
      for (const stage of ['judgmentStore', 'judgment-store', 'judgment_store ', 'other', ''])
        await no(c, `${db}: stage '${stage}' rejected`, STAGE_CHECK, () => measure(c, observation, stage, config));
    } finally { await c.query('ROLLBACK'); }

    await c.query(migrations[idx]);
    assert.deepEqual(await catalog(c), after, 're-run 031 gives the same catalog');
    check(`${db}: re-running 031 succeeds and leaves the same catalog`);
    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally { await c.end(); }

  const f = connection('erd031_fresh'); await f.connect();
  try {
    for (const sql of migrations) await f.query(sql);
    const fresh = await checks(f);
    verdict(JSON.stringify(fresh) === JSON.stringify(upgraded), 'erd031_fresh: fresh 001 through last gives the same two CHECK definitions as the upgrade path', JSON.stringify(fresh));
    await parity(f);
  } finally { await f.end(); }
}
