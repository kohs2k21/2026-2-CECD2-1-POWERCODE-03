// 026: per-user notification window (days) on the 022 settings table, called from erd-v03.mjs inside the same disposable cluster.
// One database erd026_upgrade: migrations before 026 (one string each) + roles script + a settings row written with the pre-026 API statement,
// then 026 without re-applying the roles script: existing row gets 7, the api LOGIN role reads/writes the new column (table-level grant),
// the pre-026 upsert keeps the stored value, CHECK boundaries (0, 31, NULL rejected; 1, 30 accepted), remaining migrations, DBML parity. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

export async function notificationWindow026({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('026_'));
  assert(idx > 0, '026 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd026_upgrade';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const login = { user: 'qa026_api', password: randomUUID() };
  let api;
  try {
    for (const sql of migrations.slice(0, idx)) await c.query(sql);
    await c.query(rolesSql);
    await c.query(`CREATE ROLE ${login.user} LOGIN PASSWORD '${login.password}' IN ROLE powercode_api`);
    api = new Client({ host:'127.0.0.1', port, user:login.user, password:login.password, database:db });
    await api.connect();
    // Pre-026 API statement (services/userSettings.ts before 026).
    const before = `INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated) VALUES($1,$2,$3,$4)
      ON CONFLICT (actor_id) DO UPDATE SET decision_basis=EXCLUDED.decision_basis,min_risk_level=EXCLUDED.min_risk_level,
        include_unrated=EXCLUDED.include_unrated,updated_at=clock_timestamp()`;
    await api.query(before, ['qa-old', 'final_any', 'warning', false]);
    check(`${db}: ${migrationNames[idx - 1]} state with a settings row written by the pre-026 API statement`);

    await c.query(migrations[idx]);
    const kept = (await c.query(`SELECT decision_basis,window_days FROM ops_user_notification_settings WHERE actor_id='qa-old'`)).rows[0];
    verdict(kept.decision_basis === 'final_any' && kept.window_days === 7, `${db}: 026 keeps the existing row and gives it window_days 7`, JSON.stringify(kept));
    const column = (await c.query(`SELECT data_type,is_nullable,column_default FROM information_schema.columns
      WHERE table_name='ops_user_notification_settings' AND column_name='window_days'`)).rows[0];
    verdict(column?.data_type === 'integer' && column.is_nullable === 'NO' && column.column_default === '7',
      `${db}: window_days integer NOT NULL DEFAULT 7`, JSON.stringify(column));
    const checks = (await c.query(`SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
      WHERE conrelid='ops_user_notification_settings'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%window_days%'`)).rows;
    verdict(checks.length === 1, `${db}: one CHECK on window_days`, JSON.stringify(checks));
    const extra = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid='ops_user_notification_settings'::regclass`)).rows[0].n
      + (await c.query(`SELECT count(*)::int AS n FROM pg_index WHERE indrelid='ops_user_notification_settings'::regclass AND NOT indisprimary`)).rows[0].n;
    verdict(extra === 0, `${db}: no trigger or extra index on the settings table`, `extra=${extra}`);

    // Same api LOGIN session, roles script NOT re-applied: the table-level 022 grant covers the new column.
    const upsert = `INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated,window_days)
      VALUES($1,$2,$3,$4,coalesce($5::integer,$6::integer))
      ON CONFLICT (actor_id) DO UPDATE SET decision_basis=EXCLUDED.decision_basis,min_risk_level=EXCLUDED.min_risk_level,
        include_unrated=EXCLUDED.include_unrated,window_days=coalesce($5::integer,ops_user_notification_settings.window_days),updated_at=clock_timestamp()`;
    const read = async (actor) => (await api.query('SELECT window_days FROM ops_user_notification_settings WHERE actor_id=$1', [actor])).rows[0]?.window_days;
    await api.query(upsert, ['qa-old', 'interim', 'info', true, 30, 7]);
    verdict(await read('qa-old') === 30, `${db}: api login (no roles re-apply) upserts and reads window_days`);
    await api.query(upsert, ['qa-old', 'interim', 'info', true, null, 7]);
    verdict(await read('qa-old') === 30, `${db}: upsert without windowDays keeps the stored value`);
    await api.query(upsert, ['qa-new', 'interim', 'info', true, null, 7]);
    verdict(await read('qa-new') === 7, `${db}: new row without windowDays gets the default 7`);
    await api.query(before, ['qa-old', 'final_complete', 'critical', true]);
    verdict(await read('qa-old') === 30, `${db}: pre-026 API upsert leaves window_days unchanged (mixed versions)`);
    await api.query(upsert, ['qa-one', 'interim', 'info', true, 1, 7]);
    verdict(await read('qa-one') === 1, `${db}: window_days 1 accepted`);
    await rejected(api, `${db}: api login cannot DELETE settings`, '42501', 'DELETE FROM ops_user_notification_settings WHERE actor_id=$1', ['qa-one']);

    // CHECK boundaries (owner, so only the constraint decides).
    const insert = 'INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated,window_days) VALUES($1,$2,$3,$4,$5)';
    await rejected(c, `${db}: window_days 0`, '23514', insert, ['qa-2', 'interim', 'info', true, 0]);
    await rejected(c, `${db}: window_days 31`, '23514', insert, ['qa-2', 'interim', 'info', true, 31]);
    await rejected(c, `${db}: window_days NULL`, '23502', insert, ['qa-2', 'interim', 'info', true, null]);
    await rejected(c, `${db}: UPDATE to window_days 31`, '23514', `UPDATE ops_user_notification_settings SET window_days=31 WHERE actor_id='qa-old'`);

    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally {
    await api?.end().catch(() => {});
    await c.end();
  }
}
