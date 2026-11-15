// 032: correction rows (조사·조치 기록 수정) on the 014 investigation record table, called from erd-v03.mjs inside the same disposable cluster.
// One database erd032_upgrade: migrations before 032 (one string each) + roles script + a record written with the pre-032 API statement,
// then 032 without re-applying the roles script: existing record keeps both new columns NULL, the api LOGIN role writes/reads correction rows
// (table-level grant), the pre-032 INSERT still works (NULL), CHECK keeps the two columns together and forbids self references,
// the partial UNIQUE index lets one version be superseded only once, FKs reject unknown rows, UPDATE/DELETE stay denied,
// other runtime roles still have no access, a second run of 032 fails and rolls back, remaining migrations, DBML parity. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

export async function feedbackCorrection032({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('032_'));
  assert(idx > 0, '032 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd032_upgrade';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const login = async (role) => {
    const user = `qa032_${role}`, password = randomUUID();
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
    const source = await insert(c, 'ops_sources', { dataset_key:`qa032-${randomUUID()}` });
    const execution = await insert(c, 'data_executions', { source_id:source.id, layer:'P' });
    const observation = await insert(c, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1 });
    await insert(c, 'data_processes', { observation_id:observation.id });
    const config = await insert(c, 'ml_config_versions', { source_id:source.id });
    const decision = await insert(c, 'run_process_decisions', { execution_id:execution.id, process_observation_id:observation.id, config_version_id:config.id,
      status:'complete', rule_status:'succeeded', ml_status:'succeeded', latency_status:'unavailable', source_latency_ms:null, evidence:{ anomalous:true }, decided_at:new Date() });
    const api = await login('api');
    // Pre-032 API statement (services/analysisWorkflow.ts addFeedback after 029).
    const before = `INSERT INTO run_decision_feedback(id,decision_id,kind,reason,actor_id,outcome) VALUES($1,$2,$3,$4,$5,$6)`;
    const oldId = randomUUID();
    await api.query(before, [oldId, decision.id, 'action', '조치함', 'qa-admin', 'normal']);
    check(`${db}: ${migrationNames[idx - 1]} state with a record written by the pre-032 API statement`);

    await c.query(migrations[idx]);
    const kept = (await c.query('SELECT kind,reason,outcome,supersedes_id,origin_id FROM run_decision_feedback WHERE id=$1', [oldId])).rows[0];
    verdict(kept.kind === 'action' && kept.reason === '조치함' && kept.outcome === 'normal' && kept.supersedes_id === null && kept.origin_id === null,
      `${db}: 032 keeps the existing record with both new columns NULL`, JSON.stringify(kept));
    const columns = (await c.query(`SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns
      WHERE table_name='run_decision_feedback' AND column_name IN ('supersedes_id','origin_id') ORDER BY column_name`)).rows;
    verdict(columns.length === 2 && columns.every(r => r.data_type === 'uuid' && r.is_nullable === 'YES' && r.column_default === null),
      `${db}: supersedes_id/origin_id uuid NULL without DEFAULT`, JSON.stringify(columns));
    const fks = (await c.query(`SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
      WHERE conrelid='run_decision_feedback'::regclass AND contype='f' AND confrelid='run_decision_feedback'::regclass ORDER BY 1`)).rows.map(r => r.def);
    verdict(JSON.stringify(fks) === JSON.stringify(['FOREIGN KEY (origin_id) REFERENCES run_decision_feedback(id)', 'FOREIGN KEY (supersedes_id) REFERENCES run_decision_feedback(id)']),
      `${db}: two self-referencing FKs`, JSON.stringify(fks));
    const checks = (await c.query(`SELECT conname FROM pg_constraint
      WHERE conrelid='run_decision_feedback'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%supersedes_id%'`)).rows.map(r => r.conname);
    verdict(JSON.stringify(checks) === JSON.stringify(['run_decision_feedback_correction_check']), `${db}: one named CHECK on the correction columns`, JSON.stringify(checks));
    const unique = (await c.query(`SELECT indexdef FROM pg_indexes WHERE tablename='run_decision_feedback' AND indexname='run_decision_feedback_supersedes_uniq'`)).rows[0]?.indexdef ?? '';
    verdict(/CREATE UNIQUE INDEX .* \(supersedes_id\) WHERE \(supersedes_id IS NOT NULL\)/.test(unique), `${db}: partial UNIQUE index on supersedes_id`, unique);
    const extra = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid='run_decision_feedback'::regclass`)).rows[0].n;
    const indexes = (await c.query(`SELECT count(*)::int AS n FROM pg_index WHERE indrelid='run_decision_feedback'::regclass AND NOT indisprimary`)).rows[0].n;
    verdict(extra === 0 && indexes === 3, `${db}: no trigger, the two 014 indexes plus the 032 index`, `triggers=${extra} indexes=${indexes}`);

    // Same api LOGIN session, roles script NOT re-applied: the table-level 014 grant covers the new columns.
    const after = `INSERT INTO run_decision_feedback(id,decision_id,kind,reason,actor_id,outcome,supersedes_id,origin_id)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING supersedes_id,origin_id`;
    const first = randomUUID(), second = randomUUID();
    const r1 = (await api.query(after, [first, decision.id, 'suspected-false-positive', '정정 1', 'qa-admin', 'actual-anomaly', oldId, oldId])).rows[0];
    verdict(r1.supersedes_id === oldId && r1.origin_id === oldId, `${db}: api login (no roles re-apply) writes a correction row`);
    await api.query(after, [second, decision.id, 'action', '정정 2', 'qa-admin', null, first, oldId]);
    await rejected(api, `${db}: a version superseded twice`, '23505', after, [randomUUID(), decision.id, 'action', '동시 정정', 'qa-admin', null, first, oldId]);
    await api.query(before, [randomUUID(), decision.id, 'insufficient-evidence', '이전 API', 'qa-admin', null]);
    const current = (await api.query(`SELECT f.reason FROM run_decision_feedback f
      WHERE NOT EXISTS (SELECT 1 FROM run_decision_feedback n WHERE n.supersedes_id=f.id) ORDER BY f.reason`)).rows.map(r => r.reason);
    verdict(JSON.stringify(current) === JSON.stringify(['이전 API', '정정 2']),
      `${db}: api login reads the chain; current versions = rows nobody supersedes (pre-032 INSERT mixed in)`, JSON.stringify(current));
    await rejected(api, `${db}: supersedes_id without origin_id`, '23514', after, [randomUUID(), decision.id, 'action', 'x', 'qa-admin', null, second, null]);
    await rejected(api, `${db}: origin_id without supersedes_id`, '23514', after, [randomUUID(), decision.id, 'action', 'x', 'qa-admin', null, null, oldId]);
    const self = randomUUID();
    await rejected(api, `${db}: a row superseding itself`, '23514', after, [self, decision.id, 'action', 'x', 'qa-admin', null, self, oldId]);
    await rejected(api, `${db}: a correction of an unknown row`, '23503', after, [randomUUID(), decision.id, 'action', 'x', 'qa-admin', null, randomUUID(), oldId]);
    await rejected(api, `${db}: api login cannot UPDATE records`, '42501', `UPDATE run_decision_feedback SET reason='바꿈' WHERE id=$1`, [oldId]);
    await rejected(api, `${db}: api login cannot DELETE records`, '42501', 'DELETE FROM run_decision_feedback WHERE id=$1', [oldId]);
    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read records`, '42501', 'SELECT supersedes_id FROM run_decision_feedback LIMIT 1');
    }

    // A second run fails on the existing column and leaves the table as it was.
    const before032 = (await c.query('SELECT count(*)::int AS n FROM run_decision_feedback')).rows[0].n;
    await rejected(c, `${db}: running 032 again fails`, '42701', migrations[idx]);
    await c.query('ROLLBACK').catch(() => {});
    const state = (await c.query(`SELECT (SELECT count(*)::int FROM run_decision_feedback) AS n,
      (SELECT count(*)::int FROM pg_index WHERE indrelid='run_decision_feedback'::regclass AND NOT indisprimary) AS indexes`)).rows[0];
    verdict(state.n === before032 && state.indexes === 3, `${db}: failed re-run leaves rows and indexes unchanged`, JSON.stringify(state));

    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally {
    for (const client of Object.values(logins)) await client.end().catch(() => {});
    await c.end();
  }
}
