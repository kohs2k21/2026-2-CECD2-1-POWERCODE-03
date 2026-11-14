// 029: outcome column (확인 결과) on the 014 investigation record table, called from erd-v03.mjs inside the same disposable cluster.
// One database erd029_upgrade: migrations before 029 (one string each) + roles script + a record written with the pre-029 API statement,
// then 029 without re-applying the roles script: existing record keeps NULL, the api LOGIN role writes/reads the new column (table-level grant),
// the pre-029 INSERT still works (NULL), CHECK accepts the three values and NULL and rejects anything else, UPDATE/DELETE stay denied,
// other runtime roles still have no access, a second run of 029 fails and rolls back, remaining migrations, DBML parity. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const OUTCOMES = ['actual-anomaly', 'normal', 'undetermined'];

export async function feedbackOutcome029({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('029_'));
  assert(idx > 0, '029 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd029_upgrade';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const login = async (role) => {
    const user = `qa029_${role}`, password = randomUUID();
    await c.query(`CREATE ROLE ${user} LOGIN PASSWORD '${password}' IN ROLE powercode_${role}`);
    const client = new Client({ host:'127.0.0.1', port, user, password, database:db });
    await client.connect();
    logins[role] = client;
    return client;
  };
  try {
    for (const sql of migrations.slice(0, idx)) await c.query(sql);
    await c.query(rolesSql);
    // Fixture (owner): one final decision as the record target.
    const source = await insert(c, 'ops_sources', { dataset_key:`qa029-${randomUUID()}` });
    const execution = await insert(c, 'data_executions', { source_id:source.id, layer:'P' });
    const observation = await insert(c, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1 });
    await insert(c, 'data_processes', { observation_id:observation.id });
    const config = await insert(c, 'ml_config_versions', { source_id:source.id });
    const decision = await insert(c, 'run_process_decisions', { execution_id:execution.id, process_observation_id:observation.id, config_version_id:config.id,
      status:'complete', rule_status:'succeeded', ml_status:'succeeded', latency_status:'unavailable', source_latency_ms:null, evidence:{ anomalous:true }, decided_at:new Date() });
    const api = await login('api');
    // Pre-029 API statement (services/analysisWorkflow.ts addFeedback before 029).
    const before = `INSERT INTO run_decision_feedback(id,decision_id,kind,reason,actor_id) VALUES($1,$2,$3,$4,$5)`;
    const oldId = randomUUID();
    await api.query(before, [oldId, decision.id, 'action', '조치함', 'qa-admin']);
    check(`${db}: ${migrationNames[idx - 1]} state with a record written by the pre-029 API statement`);

    await c.query(migrations[idx]);
    const kept = (await c.query('SELECT kind,reason,outcome FROM run_decision_feedback WHERE id=$1', [oldId])).rows[0];
    verdict(kept.kind === 'action' && kept.reason === '조치함' && kept.outcome === null, `${db}: 029 keeps the existing record with outcome NULL`, JSON.stringify(kept));
    const column = (await c.query(`SELECT data_type,is_nullable,column_default FROM information_schema.columns
      WHERE table_name='run_decision_feedback' AND column_name='outcome'`)).rows[0];
    verdict(column?.data_type === 'text' && column.is_nullable === 'YES' && column.column_default === null,
      `${db}: outcome text NULL without DEFAULT`, JSON.stringify(column));
    const checks = (await c.query(`SELECT conname,pg_get_constraintdef(oid) AS def FROM pg_constraint
      WHERE conrelid='run_decision_feedback'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%outcome%'`)).rows;
    verdict(checks.length === 1 && checks[0].conname === 'run_decision_feedback_outcome_check' && OUTCOMES.every(v => checks[0].def.includes(`'${v}'`)),
      `${db}: one named CHECK on outcome listing the three values`, JSON.stringify(checks));
    const extra = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid='run_decision_feedback'::regclass`)).rows[0].n;
    const indexes = (await c.query(`SELECT count(*)::int AS n FROM pg_index WHERE indrelid='run_decision_feedback'::regclass AND NOT indisprimary`)).rows[0].n;
    verdict(extra === 0 && indexes === 2, `${db}: no trigger, the two 014 indexes only`, `triggers=${extra} indexes=${indexes}`);

    // Same api LOGIN session, roles script NOT re-applied: the table-level 014 grant covers the new column.
    const after = `INSERT INTO run_decision_feedback(id,decision_id,kind,reason,actor_id,outcome) VALUES($1,$2,$3,$4,$5,$6) RETURNING outcome`;
    for (const value of [...OUTCOMES, null]) {
      const row = (await api.query(after, [randomUUID(), decision.id, 'suspected-false-positive', '확인', 'qa-admin', value])).rows[0];
      verdict(row.outcome === value, `${db}: api login (no roles re-apply) writes outcome ${value ?? 'NULL'}`);
    }
    await api.query(before, [randomUUID(), decision.id, 'insufficient-evidence', '이전 API', 'qa-admin']);
    const counts = (await api.query(`SELECT outcome,count(*)::int AS n FROM run_decision_feedback GROUP BY outcome ORDER BY outcome NULLS LAST`)).rows;
    verdict(JSON.stringify(counts) === JSON.stringify([{ outcome:'actual-anomaly', n:1 }, { outcome:'normal', n:1 }, { outcome:'undetermined', n:1 }, { outcome:null, n:3 }]),
      `${db}: api login reads outcome; the pre-029 INSERT still works and stores NULL (mixed versions)`, JSON.stringify(counts));
    for (const value of ['label', 'Normal', '', 'actual_anomaly']) {
      await rejected(api, `${db}: outcome '${value}'`, '23514', after, [randomUUID(), decision.id, 'action', 'x', 'qa-admin', value]);
    }
    await rejected(api, `${db}: api login cannot UPDATE outcome`, '42501', `UPDATE run_decision_feedback SET outcome='normal' WHERE id=$1`, [oldId]);
    await rejected(api, `${db}: api login cannot DELETE records`, '42501', 'DELETE FROM run_decision_feedback WHERE id=$1', [oldId]);
    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read records`, '42501', 'SELECT outcome FROM run_decision_feedback LIMIT 1');
    }
    // Owner: CHECK decides UPDATE too.
    await rejected(c, `${db}: owner UPDATE to an unknown outcome`, '23514', `UPDATE run_decision_feedback SET outcome='false-positive' WHERE id=$1`, [oldId]);

    // A second run fails on the existing column and leaves the table as it was.
    const before029 = (await c.query('SELECT count(*)::int AS n FROM run_decision_feedback')).rows[0].n;
    await rejected(c, `${db}: running 029 again fails`, '42701', migrations[idx]);
    await c.query('ROLLBACK').catch(() => {});
    const state = (await c.query(`SELECT (SELECT count(*)::int FROM run_decision_feedback) AS n,
      (SELECT count(*)::int FROM pg_constraint WHERE conrelid='run_decision_feedback'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%outcome%') AS checks`)).rows[0];
    verdict(state.n === before029 && state.checks === 1, `${db}: failed re-run leaves rows and the CHECK unchanged`, JSON.stringify(state));

    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally {
    for (const client of Object.values(logins)) await client.end().catch(() => {});
    await c.end();
  }
}
