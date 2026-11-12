// 020: rule set content_hash_version 3 allowed, called from erd-v03.mjs inside the same disposable cluster.
// One database erd020: every file before 020, then 020 as one string. Catalog changes only by the one CHECK (same name), DEFAULT stays 2,
// r0/r1 rows unchanged, 3 accepted and 4 rejected after 020, re-run gives the same catalog. Small fixture rows only.
import assert from 'node:assert/strict';
import { catalog, delta } from './erd-v03-detect-v04.mjs';

const RULE = 'ops_rule_set_revisions';
const CHECK_NAME = 'ops_rule_set_revisions_content_hash_version_check';

export async function ruleHashV3_020({ connection, check, failures, migrations, migrationNames, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('020_'));
  assert(idx > 0, '020 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const no = async (label, codeWanted, run) => {
    try { await run(); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === codeWanted && e.constraint === CHECK_NAME, `${label} (${codeWanted} ${CHECK_NAME})`, `${e.code} ${e.constraint ?? ''} ${e.message}`); }
  };
  const db = 'erd020';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }

  const c = connection(db); await c.connect();
  const save = (version) => c.query(`INSERT INTO ${RULE}(revision,content_hash,snapshot,changes,summary,saved_by_actor_id${version === undefined ? '' : ',content_hash_version'})
    VALUES(2,$1,'{"hardRules":[],"softRules":[]}','[]','{}','admin902'${version === undefined ? '' : ','+version}) RETURNING content_hash_version`, ['c'.repeat(64)]);
  const rows = async () => (await c.query(`SELECT row_to_json(x)::text AS r FROM ${RULE} x ORDER BY revision`)).rows.map(r => r.r);
  try {
    for (let i = 0; i < idx; i++) await c.query(migrations[i]);
    await no(`${db}: before 020 (016 CHECK) content_hash_version 3 rejected`, '23514', () => save(3));
    const before = await catalog(c), rulesBefore = await rows();

    await c.query(migrations[idx]);
    check(`${db}: 001 through ${migrationNames[idx - 1]}, then ${migrationNames[idx]} applies as one string`);
    const after = await catalog(c);
    const con = delta(before.constraints, after.constraints);
    assert.deepEqual([con.removed, con.added], [
      [`${RULE}.${CHECK_NAME}:true:CHECK ((content_hash_version = ANY (ARRAY[1, 2])))`],
      [`${RULE}.${CHECK_NAME}:true:CHECK ((content_hash_version = ANY (ARRAY[1, 2, 3])))`]]);
    for (const key of ['enums', 'columns', 'triggers', 'indexes']) assert.deepEqual(after[key], before[key], `020 leaves ${key} unchanged`);
    assert.deepEqual(after.functions.map(f => [f.name, f.body, f.config, f.definer, f.owner]), before.functions.map(f => [f.name, f.body, f.config, f.definer, f.owner]), '020 leaves functions unchanged');
    check(`${db}: only ${CHECK_NAME} changes, same name, validated, (1,2) -> (1,2,3); enums, columns, triggers, indexes and functions unchanged`);
    verdict(after.columns.includes(`public.${RULE}.content_hash_version:int2:NO:2`), `${db}: content_hash_version stays NOT NULL DEFAULT 2`);
    const kept = await rows();
    const versions = (await c.query(`SELECT revision,content_hash_version FROM ${RULE} ORDER BY revision`)).rows;
    verdict(JSON.stringify(kept) === JSON.stringify(rulesBefore) && JSON.stringify(versions) === JSON.stringify([{ revision:'0', content_hash_version:1 }, { revision:'1', content_hash_version:2 }]),
      `${db}: r0 (version 1) and r1 (version 2) rows unchanged`, JSON.stringify(versions));

    await c.query('BEGIN');
    try { verdict((await save(3)).rows[0].content_hash_version === 3, `${db}: content_hash_version 3 accepted after 020`); } finally { await c.query('ROLLBACK'); }
    await c.query('BEGIN');
    try { verdict((await save()).rows[0].content_hash_version === 2, `${db}: a save that omits the column is still stored as 2`); } finally { await c.query('ROLLBACK'); }
    await no(`${db}: content_hash_version 4 rejected after 020`, '23514', () => save(4));
    await no(`${db}: content_hash_version 0 rejected after 020`, '23514', () => save(0));

    await c.query(migrations[idx]);
    assert.deepEqual(await catalog(c), after, 're-run 020 gives the same catalog');
    check(`${db}: re-running 020 succeeds and leaves the same catalog`);
    for (let i = idx + 1; i < migrations.length; i++) await c.query(migrations[i]);
    await parity(c);
  } finally { await c.end(); }
}
