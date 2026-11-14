// 015 (#110): operational revision tables and ops_operational_state, called from erd-v03.mjs inside the same disposable cluster.
// Databases: erd110_fresh (001-015 + role scripts), erd110_upgrade (001-014 + existing rows -> 015), erd110_rollback (015 fails before COMMIT).
// Everything here judges the state right after 015: r0 is current and the columns are the 015 ones. 016 adds r1 and content_hash_version (erd-v03-detect-v04.mjs).
// erd110_upgrade and erd110_rollback continue to the last migration for DBML parity; erd110_fresh saves revisions, so it stops at 015 and 016 would refuse it.
// Checks: contract column names, r0 rows and hash recalculation, append-only triggers, CHECK/FK limits, grants by real LOGIN roles,
// view results and single-statement consistency, search_path/temp-table independence, 001-014 catalog unchanged, rollback, DBML parity.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const RULE = 'ops_rule_set_revisions', ACTIVE = 'ops_active_model_set_revisions', POLICY = 'ops_detection_policy_revisions', VIEW = 'ops_operational_state';
const TABLES = [RULE, ACTIVE, POLICY];
const COLUMNS = {
  [RULE]: ['revision','content_hash','snapshot','changes','summary','note','restored_from_revision','saved_by_actor_id','saved_at'],
  [ACTIVE]: ['revision','snapshot','changes','note','restored_from_revision','changed_by_actor_id','changed_at'],
  [POLICY]: ['revision','policy','note','restored_from_revision','saved_by_actor_id','saved_at'],
};
const JSONB = new Set(['snapshot','changes','summary','policy']);
// Fixed value in 015. Recalculated below from the stored r0 snapshot with the rule-set.md serialization.
const R0_HASH = '80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89';
const R0_CANONICAL = '{"hardRules":[{"enabled":true,"id":"hard:negative-duration"},{"enabled":true,"id":"hard:process-status"}],"softRules":[]}';
// r0 is an immutable historical row: fixed here as written in 015. The contract examples now describe the current built-in list (4 rules, #116), not r0.
const R0_HARD_RULES = [
  { id:'hard:process-status', methodVersion:'process-status-hr-v1', displayName:'상태가 S가 아님', conditionText:'STATUS ≠ S',
    severity:{ byCode:{ PROCESS_STATUS_F:'critical', PROCESS_STATUS_P:'warning', PROCESS_STATUS_N:'warning' } }, enabled:true, editable:['enabled'] },
  { id:'hard:negative-duration', methodVersion:'negative-duration-v1', displayName:'시작 시각이 종료 시각보다 늦음',
    conditionText:'START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)', severity:'critical', enabled:true, editable:['enabled'] },
];
// rule-set.md 'implementation test value' for its example canonical object.
const CONTRACT_HASH = '5b15237724b48fcc4f18335a374ad7d5d8d7bcbaa7b5f851120f4d17b23dd8aa';
const EMPTY_SUMMARY = { hardEnabled:0, hardDisabled:0, softAdded:0, softUpdated:0, softRemoved:0, softEnabled:0, softDisabled:0 };
// pg_trigger.tgtype: BEFORE(2) + DELETE(8) + UPDATE(16) + TRUNCATE(32), ROW bit (1) clear.
const STATEMENT_TRIGGER = 2 + 8 + 16 + 32;
const ALL_PRIVILEGES = ['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER','MAINTAIN'];

// json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False) for these ASCII objects.
const sorted = v => Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object'
  ? Object.fromEntries(Object.keys(v).sort().map(k => [k, sorted(v[k])])) : v;
const byId = rows => [...rows].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
// rule-set.md 'revision format': hash input projected from a snapshot. threshold: r0 has no soft rules; only the contract example 8000 passes here.
const canonical = ({ hardRules, softRules }) => JSON.stringify(sorted({
  hardRules: byId(hardRules).map(({ id, enabled }) => ({ id, enabled })),
  softRules: byId(softRules).map(({ id, field, operator, threshold, severity, enabled }) =>
    ({ id, field, operator, threshold: String(threshold), severity, enabled })),
}));
const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex');

const put = (c, table, row) => {
  const names = Object.keys(row);
  return c.query(`INSERT INTO ${table}(${names.join(',')}) VALUES(${names.map((_, i) => '$' + (i + 1)).join(',')})`,
    names.map(n => JSONB.has(n) ? JSON.stringify(row[n]) : row[n]));
};
const ruleRow = (revision, over = {}) => ({ revision, content_hash:'a'.repeat(64), snapshot:{ hardRules:[], softRules:[] }, changes:[], summary:{},
  saved_by_actor_id:'admin110', ...over });
const activeRow = (revision, over = {}) => ({ revision, snapshot:{ models:[] }, changes:[], changed_by_actor_id:'admin110', ...over });
const policyRow = (revision, over = {}) => ({ revision, policy:{ version:2, policyKey:'category-k-of-n-or-v1', k:{ hard:1, soft:1, ml:1 }, deadlineMs:3000 },
  saved_by_actor_id:'admin110', ...over });
// Write forms that reach UPDATE or DELETE through another statement; row revision 1 exists when these run.
const indirectWrites = table => [
  ['INSERT ON CONFLICT DO UPDATE', `INSERT INTO ${table} SELECT * FROM ${table} WHERE revision=1 ON CONFLICT (revision) DO UPDATE SET note='x'`],
  ['MERGE UPDATE', `MERGE INTO ${table} t USING (SELECT 1::bigint AS revision) s ON t.revision=s.revision WHEN MATCHED THEN UPDATE SET note='x'`],
  ['MERGE DELETE', `MERGE INTO ${table} t USING (SELECT 1::bigint AS revision) s ON t.revision=s.revision WHEN MATCHED THEN DELETE`],
];
const state = async c => {
  const { rows } = await c.query(`SELECT * FROM ${VIEW}`);
  assert.equal(rows.length, 1, `${VIEW} returns exactly one row`);
  return rows[0];
};

export async function revisions015({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert, infra }) {
  const idx015 = migrationNames.findIndex(n => n.startsWith('015_'));
  assert(idx015 > 0, '015 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (label, code, run, message) => {
    try { await run(); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code && (!message || message.test(e.message)), `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const demoRoleSql = await readFile(join(infra, 'scripts/demo-role.sql'), 'utf8');
  const ruleSetContract = await readFile(join(infra, '..', 'contracts/rule-set.md'), 'utf8');

  const admin = connection('postgres'); await admin.connect();
  try { for (const name of ['erd110_fresh','erd110_upgrade','erd110_rollback']) await admin.query(`CREATE DATABASE ${name}`); }
  finally { await admin.end(); }

  // Hash rule itself: the test serializer reproduces the contract's own value before it judges r0.
  verdict(ruleSetContract.includes(CONTRACT_HASH) && sha256(canonical({
    hardRules:[{ id:'hard:process-status', enabled:true }, { id:'hard:negative-duration', enabled:false }],
    softRules:[{ id:'soft:elapsed_ms-3f9a2c1d', name:'ignored', field:'elapsed_ms', operator:'gt', threshold:8000, unit:'ms', severity:'warning', enabled:true }],
  })) === CONTRACT_HASH, 'hash serializer reproduces the rule-set.md reference value');

  const c = connection('erd110_fresh'); await c.connect();
  const logins = {};
  const openLogin = async role => {
    const l = logins[role];
    const a = new Client({ host:'127.0.0.1', port, user:l.user, password:l.password, database:'erd110_fresh' });
    await a.connect();
    return a;
  };
  try {
    for (const sql of migrations.slice(0, idx015 + 1)) await c.query(sql);
    check(`erd110_fresh: 001 through ${migrationNames[idx015]} apply`);
    // 019 tables do not exist yet at 015, so the 019 grant block is left out here (erd-v03-incidents.mjs checks it).
    const rolesTo015 = rolesSql.replace(/^-- 019: [\s\S]*?(?=^-- #78-Q10)/m, '');
    assert.notEqual(rolesTo015, rolesSql, 'database-roles.sql has the 019 grant block before #78-Q10');
    await c.query(rolesTo015);
    await c.query(demoRoleSql);
    for (const role of ['api','detector','collector','trainer','llm']) {
      logins[role] = { user:`qa110_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }

    // Contract column names, in contract order.
    for (const table of TABLES) {
      const names = (await c.query(`SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position`, [table])).rows.map(r => r.column_name);
      verdict(JSON.stringify(names) === JSON.stringify(COLUMNS[table]), `${table}: columns match the contract`, names.join(','));
    }

    // r0 rows.
    const r0 = (await c.query(`SELECT * FROM ${RULE}`)).rows;
    verdict(r0.length === 1 && r0[0].revision === '0' && r0[0].saved_by_actor_id === 'system' && r0[0].note === null && r0[0].restored_from_revision === null
      && r0[0].saved_at instanceof Date, `${RULE}: one r0 row by system`, JSON.stringify(r0));
    assert.deepEqual(r0[0].changes, []); assert.deepEqual(r0[0].summary, EMPTY_SUMMARY);
    check(`${RULE}: r0 changes empty and summary all zero`);
    assert.deepEqual(r0[0].snapshot, { hardRules:R0_HARD_RULES, softRules:[] }, 'r0 snapshot = the 015 hard rules, no soft rules');
    check('r0 snapshot equals the 015 hard rules (ids, versions, screen text, severity, editable), all enabled, empty softRules');
    verdict(canonical(r0[0].snapshot) === R0_CANONICAL, 'r0 canonical object from the stored snapshot', canonical(r0[0].snapshot));
    verdict(sha256(canonical(r0[0].snapshot)) === R0_HASH && r0[0].content_hash === R0_HASH, `r0 content_hash recalculated from the snapshot = ${R0_HASH}`, r0[0].content_hash);
    const a0 = (await c.query(`SELECT * FROM ${ACTIVE}`)).rows;
    verdict(a0.length === 1 && a0[0].revision === '0' && JSON.stringify(a0[0].snapshot) === '{"models":[]}' && JSON.stringify(a0[0].changes) === '[]'
      && a0[0].changed_by_actor_id === 'system' && a0[0].note === null && a0[0].restored_from_revision === null, `${ACTIVE}: one empty r0 row by system`, JSON.stringify(a0));
    verdict((await c.query(`SELECT count(*)::int AS n FROM ${POLICY}`)).rows[0].n === 0, `${POLICY}: no seed row (no row = revision 0)`);

    // Triggers: one statement-level trigger per table on the reused function.
    const triggers = (await c.query(`SELECT c.relname::text AS rel, t.tgtype::int AS type, p.proname::text AS fn FROM pg_trigger t
      JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid WHERE NOT t.tgisinternal AND c.relname = ANY($1) ORDER BY 1`, [TABLES])).rows;
    verdict(triggers.length === 3 && triggers.every(t => t.type === STATEMENT_TRIGGER && t.fn === 'erd_v03_immutable'),
      '3 BEFORE UPDATE OR DELETE OR TRUNCATE statement triggers on erd_v03_immutable', JSON.stringify(triggers));

    // Constraints from the contracts (superuser, so only CHECK/FK/PK decide).
    const limits = [
      ['revision below 0', RULE, ruleRow(-1), '23514'], ['revision below 0', ACTIVE, activeRow(-1), '23514'],
      ['revision 0 (r0 is no row)', POLICY, policyRow(0), '23514'],
      ['duplicate revision', RULE, ruleRow(0), '23505'], ['duplicate revision', ACTIVE, activeRow(0), '23505'],
      ['uppercase hash', RULE, ruleRow(1, { content_hash:'A'.repeat(64) }), '23514'],
      ['63-digit hash', RULE, ruleRow(1, { content_hash:'a'.repeat(63) }), '23514'],
      ['sha256: prefix', RULE, ruleRow(1, { content_hash:`sha256:${'a'.repeat(64)}` }), '23514'],
      ['hash with trailing newline', RULE, ruleRow(1, { content_hash:`${'a'.repeat(64)}\n` }), '23514'],
      ['missing hash', RULE, ruleRow(1, { content_hash:null }), '23502'],
      ['snapshot array', RULE, ruleRow(1, { snapshot:[] }), '23514'],
      ['snapshot without softRules', RULE, ruleRow(1, { snapshot:{ hardRules:[] } }), '23514'],
      ['snapshot without hardRules', RULE, ruleRow(1, { snapshot:{ softRules:[] } }), '23514'],
      ['snapshot hardRules not array', RULE, ruleRow(1, { snapshot:{ hardRules:{}, softRules:[] } }), '23514'],
      ['changes object', RULE, ruleRow(1, { changes:{} }), '23514'],
      ['summary array', RULE, ruleRow(1, { summary:[] }), '23514'],
      ['empty actor', RULE, ruleRow(1, { saved_by_actor_id:'' }), '23514'],
      ['restored from itself', RULE, ruleRow(1, { restored_from_revision:1 }), '23514'],
      ['restored from a later revision', RULE, ruleRow(1, { restored_from_revision:2 }), '23514'],
      ['restored from a missing revision', RULE, ruleRow(5, { restored_from_revision:4 }), '23503'],
      ['snapshot bare array', ACTIVE, activeRow(1, { snapshot:[] }), '23514'],
      ['snapshot without models', ACTIVE, activeRow(1, { snapshot:{} }), '23514'],
      ['changes object', ACTIVE, activeRow(1, { changes:{} }), '23514'],
      ['empty actor', ACTIVE, activeRow(1, { changed_by_actor_id:'' }), '23514'],
      ['restored from itself', ACTIVE, activeRow(1, { restored_from_revision:1 }), '23514'],
      ['restored from a missing revision', ACTIVE, activeRow(5, { restored_from_revision:4 }), '23503'],
      ['policy array', POLICY, policyRow(1, { policy:[] }), '23514'],
      ['empty actor', POLICY, policyRow(1, { saved_by_actor_id:'' }), '23514'],
      ['restored from itself', POLICY, policyRow(1, { restored_from_revision:1 }), '23514'],
      // r0 has no row, so a policy save cannot name it; going back to the built-in default is an ordinary save.
      ['restored from revision 0', POLICY, policyRow(1, { restored_from_revision:0 }), '23503'],
    ];
    for (const [label, table, row, code] of limits) await rejected(`${table}: ${label} rejected`, code, () => put(c, table, row));

    // Privilege matrix on tables and view for every runtime role.
    const expected = {
      powercode_api: { table:['SELECT','INSERT'], view:['SELECT'] }, powercode_detector: { table:['SELECT'], view:['SELECT'] }, powercode_trainer: { table:['SELECT'], view:['SELECT'] },
      powercode_collector: { table:[], view:[] }, powercode_llm: { table:[], view:[] }, powercode_demo: { table:[], view:[] },
    };
    // 027 roles script: LLM reads the rule set history only (explain_protocol facts), not the other two tables or the view.
    const override = { [`powercode_llm ${RULE}`]: ['SELECT'] };
    const wrong = [];
    for (const [role, want] of Object.entries(expected)) for (const [rel, list] of [...TABLES.map(t => [t, want.table]), [VIEW, want.view]]) for (const privilege of ALL_PRIVILEGES) {
      const has = (await c.query('SELECT has_table_privilege($1,$2,$3) AS p', [role, `public.${rel}`, privilege])).rows[0].p;
      if (has !== (override[`${role} ${rel}`] ?? list).includes(privilege)) wrong.push(`${role} ${rel} ${privilege}=${has}`);
    }
    verdict(wrong.length === 0, 'grants: api SELECT,INSERT + view SELECT; detector, trainer SELECT + view SELECT; llm SELECT on rule set history only; collector, demo none (MAINTAIN none for all)', wrong.join('; '));
    const acl = (await c.query(`SELECT c.relname::text AS rel FROM pg_class c, aclexplode(c.relacl) x WHERE c.relname = ANY($1) AND x.grantee = 0`, [[...TABLES, VIEW]])).rows;
    verdict(acl.length === 0, 'no PUBLIC grant on the 3 tables or the view', JSON.stringify(acl));

    // View metadata and first read: every revision at r0, no policy row.
    const meta = (await c.query(`SELECT relkind::text AS kind, reloptions::text[] AS options FROM pg_class WHERE oid='public.${VIEW}'::regclass`)).rows[0];
    verdict(meta.kind === 'v' && meta.options?.includes('security_invoker=true'), `${VIEW}: view with security_invoker=true`, JSON.stringify(meta));
    const types = (await c.query(`SELECT string_agg(column_name||':'||udt_name, ',' ORDER BY ordinal_position) AS t FROM information_schema.columns WHERE table_name=$1`, [VIEW])).rows[0].t;
    verdict(types === 'rule_set_revision:int8,rule_set_content_hash:text,active_model_set_revision:int8,active_models:jsonb,detection_policy_revision:int8,detection_policy:jsonb',
      `${VIEW}: column names and types`, types);
    const api = await openLogin('api'), detector = await openLogin('detector');
    try {
      const initial = { rule_set_revision:'0', rule_set_content_hash:R0_HASH, active_model_set_revision:'0', active_models:[], detection_policy_revision:'0', detection_policy:null };
      assert.deepEqual(await state(api), initial); assert.deepEqual(await state(detector), initial);
      check('view: API and Detector logins read r0, empty models, policy revision 0 with NULL policy (no policy row)');

      // API save path by a real LOGIN: common lock, three INSERTs, one COMMIT. Detector sees nothing until COMMIT.
      const models = [{ configVersionId:randomUUID(), modelKey:'iforest', displayName:'IF', activatedAtRevision:1 }];
      const hash1 = 'b'.repeat(64);
      await api.query('BEGIN');
      await api.query('SELECT pg_advisory_xact_lock(81739, 0)');
      await put(api, RULE, ruleRow(1, { content_hash:hash1, snapshot:r0[0].snapshot, note:'first save' }));
      await put(api, ACTIVE, activeRow(1, { snapshot:{ models }, changes:[{ type:'activated', modelKey:'iforest' }] }));
      await put(api, POLICY, policyRow(1));
      assert.deepEqual(await state(detector), initial);
      const other = connection('erd110_fresh'); await other.connect();
      try { verdict(!(await other.query('SELECT pg_try_advisory_xact_lock(81739, 0) AS got')).rows[0].got, 'API save holds the common lock (81739, 0) until COMMIT'); }
      finally { await other.end(); }
      await api.query('COMMIT');
      const first = await state(detector);
      assert.deepEqual(first, { rule_set_revision:'1', rule_set_content_hash:hash1, active_model_set_revision:'1', active_models:models,
        detection_policy_revision:'1', detection_policy:policyRow(1).policy });
      check('API login saves r1 in all 3 tables; view shows it only after COMMIT (rule hash, models, policy value)');

      // Each revision moves on its own; the view follows the largest row. Restores use the self reference, r0 included.
      await put(api, RULE, ruleRow(2, { content_hash:R0_HASH, snapshot:r0[0].snapshot, restored_from_revision:0 }));
      await put(api, ACTIVE, activeRow(2, { restored_from_revision:0 }));
      const changed = policyRow(2, { policy:{ ...policyRow(2).policy, deadlineMs:2000 }, restored_from_revision:1 });
      await put(api, POLICY, changed);
      const second = await state(api);
      verdict(second.rule_set_revision === '2' && second.rule_set_content_hash === R0_HASH && second.active_model_set_revision === '2' && second.active_models.length === 0
        && second.detection_policy_revision === '2' && second.detection_policy.deadlineMs === 2000, 'view follows the new revisions; restore rows reference r0 and r1', JSON.stringify(second));

      // Denied paths by real LOGIN roles.
      for (const table of TABLES) {
        for (const [verb, sql] of [['UPDATE', `UPDATE ${table} SET note='x'`], ['DELETE', `DELETE FROM ${table}`], ['TRUNCATE', `TRUNCATE ${table}`]])
          await rejected(`api ${verb} ${table} denied`, '42501', () => api.query(sql));
        for (const [verb, sql] of indirectWrites(table)) await rejected(`api ${verb} ${table} denied`, '42501', () => api.query(sql));
        await rejected(`detector INSERT ${table} denied`, '42501', () => put(detector, table, table === POLICY ? policyRow(99) : table === RULE ? ruleRow(99) : activeRow(99)));
      }
      await rejected('api cannot lock the current row with FOR UPDATE (common advisory lock instead)', '42501', () => api.query(`SELECT revision FROM ${RULE} ORDER BY revision DESC LIMIT 1 FOR UPDATE`));
      // Collector reads none of them. LLM reads rule set history only (explain_protocol, #180 grant in database-roles.sql) and writes nothing.
      for (const role of ['collector','llm']) {
        const denied = await openLogin(role);
        try {
          for (const rel of [...TABLES, VIEW]) {
            if (role === 'llm' && rel === RULE) {
              const rows = await denied.query(`SELECT revision FROM ${RULE}`).then(r => r.rows, e => e);
              verdict(Array.isArray(rows) && rows.length > 0, `llm SELECT ${RULE} allowed (explain_protocol, #180 grant)`, rows?.message ?? JSON.stringify(rows));
            } else await rejected(`${role} SELECT ${rel} denied`, '42501', () => denied.query(`SELECT * FROM ${rel}`));
          }
          if (role === 'llm') {
            await rejected(`llm INSERT ${RULE} denied`, '42501', () => put(denied, RULE, ruleRow(99)));
            for (const [verb, sql] of [['UPDATE', `UPDATE ${RULE} SET note='x'`], ['DELETE', `DELETE FROM ${RULE}`]])
              await rejected(`llm ${verb} ${RULE} denied`, '42501', () => denied.query(sql));
          }
        } finally { await denied.end(); }
      }
      // Trainer records the revisions in its results: it reads the view and writes nothing.
      const trainer = await openLogin('trainer');
      try {
        assert.deepEqual(await state(trainer), second);
        check('view: Trainer login reads the current revisions');
        for (const table of TABLES) await rejected(`trainer INSERT ${table} denied`, '42501', () => put(trainer, table, table === POLICY ? policyRow(99) : table === RULE ? ruleRow(99) : activeRow(99)));
      } finally { await trainer.end(); }

      // security_invoker: a view grant alone does not open the tables.
      await c.query('BEGIN');
      await c.query(`GRANT SELECT ON ${VIEW} TO powercode_collector`);
      await c.query('SET LOCAL ROLE powercode_collector');
      await rejected('view grant alone does not reach the tables (security_invoker)', '42501', () => c.query(`SELECT * FROM ${VIEW}`), new RegExp(`permission denied for table ops_`));
      await c.query('ROLLBACK');

      // Append-only holds for the owner, for every row and for statements matching no row.
      const fingerprint = async () => (await c.query(`SELECT ${TABLES.map(t => `(SELECT md5(string_agg(x::text, '|' ORDER BY revision)) FROM ${t} x)`).join(',')}`)).rows;
      const before = await fingerprint();
      for (const table of TABLES) {
        await rejected(`owner UPDATE ${table} rejected`, 'P0001', () => c.query(`UPDATE ${table} SET note='x' WHERE revision=1`), /is immutable/);
        await rejected(`owner DELETE ${table} rejected`, 'P0001', () => c.query(`DELETE FROM ${table} WHERE revision=1`), /is immutable/);
        await rejected(`owner TRUNCATE ${table} rejected`, 'P0001', () => c.query(`TRUNCATE ${table}`), /is immutable/);
        for (const [verb, sql] of indirectWrites(table)) await rejected(`owner ${verb} ${table} rejected`, 'P0001', () => c.query(sql), /is immutable/);
        await rejected(`owner UPDATE matching no row ${table} rejected`, 'P0001', () => c.query(`UPDATE ${table} SET note='x' WHERE false`), /is immutable/);
      }
      await rejected('owner TRUNCATE of all 3 tables in one statement rejected', 'P0001', () => c.query(`TRUNCATE ${TABLES.join(',')}`), /is immutable/);
      assert.deepEqual(await fingerprint(), before, 'rows unchanged after rejected mutations');
      check('rows unchanged after the rejected UPDATE, DELETE and TRUNCATE statements');

      // One statement = one snapshot: a writer commits all three together, a reader never sees them apart.
      const reads = [];
      let writing = true;
      const reader = (async () => { while (writing) { const s = await state(detector); reads.push([Number(s.rule_set_revision), Number(s.active_model_set_revision), Number(s.detection_policy_revision)]); } })();
      for (let revision = 3; revision < 43; revision++) {
        await api.query('BEGIN');
        await api.query('SELECT pg_advisory_xact_lock(81739, 0)');
        await put(api, RULE, ruleRow(revision)); await put(api, ACTIVE, activeRow(revision)); await put(api, POLICY, policyRow(revision));
        await api.query('COMMIT');
      }
      writing = false; await reader;
      const torn = reads.filter(([r, a, p]) => r !== a || a !== p);
      verdict(reads.length > 0 && torn.length === 0, `view: ${reads.length} reads during 40 three-table commits never mixed revisions`, JSON.stringify(torn.slice(0, 3)));
    } finally { await api.end(); await detector.end(); }

    // Hostile session settings: temp tables of the same names and a path without public change nothing.
    const real = await state(c);
    await c.query('BEGIN');
    for (const table of TABLES) await c.query(`CREATE TEMP TABLE ${table}(revision bigint, content_hash text, snapshot jsonb, policy jsonb, note text) ON COMMIT DROP`);
    await c.query(`INSERT INTO pg_temp.${RULE}(revision,content_hash) VALUES (999,'shadow')`);
    await c.query('SET LOCAL search_path = pg_temp');
    const shadowed = (await c.query(`SELECT * FROM public.${VIEW}`)).rows[0];
    assert.deepEqual(shadowed, real, 'view ignores temp tables and search_path');
    await c.query('SAVEPOINT shadow');
    let trigger = null;
    try { await c.query(`UPDATE public.${RULE} SET note='x'`); } catch (e) { trigger = e; }
    await c.query('ROLLBACK');
    verdict(trigger?.code === 'P0001' && /is immutable/.test(trigger.message), 'view and trigger unaffected by same-name temp tables and search_path=pg_temp', trigger?.message);
  } finally { await c.end(); }

  // Upgrade: 001-014 + rows -> 015. Rows and every 001-014 catalog object stay; no function is added.
  const catalog = async db => ({
    functions: (await db.query(`SELECT p.proname::text AS name, md5(p.prosrc) AS body, p.proconfig AS config, p.prosecdef AS definer FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1`)).rows,
    enums: (await db.query(`SELECT t.typname::text, e.enumlabel::text FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid ORDER BY 1, e.enumsortorder`)).rows,
    columns: (await db.query(`SELECT table_schema, table_name, column_name, udt_name, is_nullable, column_default FROM information_schema.columns
      WHERE table_schema IN ('public','powercode_queue') AND table_name <> ALL($1) ORDER BY 1,2,ordinal_position`, [[...TABLES, VIEW]])).rows,
    constraints: (await db.query(`SELECT r.relname::text AS rel, c.conname::text, pg_get_constraintdef(c.oid) AS def FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
      WHERE r.relname <> ALL($1) ORDER BY 1,2`, [TABLES])).rows,
    triggers: (await db.query(`SELECT r.relname::text AS rel, t.tgname::text, pg_get_triggerdef(t.oid) AS def FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid
      WHERE NOT t.tgisinternal AND r.relname <> ALL($1) ORDER BY 1,2`, [TABLES])).rows,
    indexes: (await db.query(`SELECT indexname, indexdef FROM pg_indexes WHERE schemaname IN ('public','powercode_queue') AND tablename <> ALL($1) ORDER BY 1`, [TABLES])).rows,
  });
  {
    const u = connection('erd110_upgrade'); await u.connect();
    try {
      for (let i = 0; i < idx015; i++) await u.query(migrations[i]);
      const source = await insert(u, 'ops_sources', { dataset_key:`qa110-${randomUUID()}` });
      const rowsOf = async () => (await u.query('SELECT row_to_json(x)::text AS r FROM ops_sources x WHERE id=$1 UNION ALL SELECT row_to_json(y)::text FROM ops_system_state y', [source.id])).rows;
      const rows = await rowsOf();
      const before = await catalog(u);
      await u.query(migrations[idx015]);
      check(`erd110_upgrade: 001-014 + rows, then ${migrationNames[idx015]} applies`);
      assert.deepEqual(await rowsOf(), rows, 'rows written before 015 are kept unchanged');
      assert.deepEqual(await catalog(u), before, '001-014 functions, enums, columns, constraints, triggers and indexes unchanged');
      check('erd110_upgrade: rows kept; 001-014 functions, enums, columns, constraints, triggers, indexes unchanged; 015 adds no function');
      verdict((await state(u)).rule_set_content_hash === R0_HASH, 'erd110_upgrade: r0 rows and view present after upgrade');
      for (let i = idx015 + 1; i < migrations.length; i++) await u.query(migrations[i]);
      await parity(u);
    } finally { await u.end(); }
  }

  // 015 fails just before COMMIT (psql -v ON_ERROR_STOP=1 -f semantics): nothing stays; then it applies cleanly.
  {
    const r = connection('erd110_rollback'); await r.connect();
    try {
      for (let i = 0; i < idx015; i++) await r.query(migrations[i]);
      const text = migrations[idx015].replaceAll('\r\n', '\n');
      const commit = text.lastIndexOf('\nCOMMIT;');
      const begin = text.search(/^BEGIN;$/m);
      assert(begin >= 0 && commit > begin, '015 is one BEGIN..COMMIT block');
      let error = null;
      try { await r.query(text.slice(0, commit + 1) + 'SELECT 1/0; -- injected failure after every statement\n' + text.slice(commit + 1)); } catch (e) { error = e; }
      await r.query('ROLLBACK');
      assert(error && error.code === '22012', `injected 015 failure must abort the migration (${error?.message})`);
      const left = (await r.query(`SELECT count(*)::int AS n FROM pg_class WHERE relname = ANY($1)`, [[...TABLES, VIEW]])).rows[0].n;
      verdict(left === 0, 'erd110_rollback: 015 failing before COMMIT leaves no table or view', `${left} left`);
      await r.query(migrations[idx015]);
      verdict((await state(r)).rule_set_revision === '0', 'erd110_rollback: 015 applies cleanly afterwards');
      for (let i = idx015 + 1; i < migrations.length; i++) await r.query(migrations[i]);
      await parity(r);
    } finally { await r.end(); }
  }
}
