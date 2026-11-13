// 022: per-user settings and notification read state, called from erd-v03.mjs inside the same disposable cluster.
// One database erd022: every migration file (one string each), catalog shape (no FK, no trigger, PK/CHECK set), DBML parity, exact grant matrix,
// real LOGIN roles (api writes the API statement shapes: upsert, GREATEST cursor, prune DELETE; other runtime roles get 42501), CHECK boundaries. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const TABLES = ['ops_user_notification_settings', 'ops_user_widget_layouts', 'ops_user_notification_cursors', 'ops_user_notification_reads'];
const ROLES = ['api', 'collector', 'detector', 'trainer', 'llm'];
const PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'];
const API_GRANTS = { ops_user_notification_settings: 'SELECT,INSERT,UPDATE', ops_user_widget_layouts: 'SELECT,INSERT,UPDATE',
  ops_user_notification_cursors: 'SELECT,INSERT,UPDATE', ops_user_notification_reads: 'SELECT,INSERT,UPDATE,DELETE' };

export async function userSettings022({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('022_'));
  assert(idx > 0, '022 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd022';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const opened = [];
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:db }); await a.connect(); opened.push(a); return a; };
  try {
    for (const sql of migrations) await c.query(sql);
    check(`${db}: 001 through ${migrationNames.at(-1)} apply, each file as one string`);
    await parity(c);
    await c.query(rolesSql);
    for (const role of ROLES) {
      logins[role] = { user:`qa022_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }

    // Catalog: PKs only (no FK to any table, accounts live outside the database), no triggers, no sequences.
    const constraints = (await c.query(`SELECT conrelid::regclass::text AS t, contype, pg_get_constraintdef(oid) AS def
      FROM pg_constraint WHERE conrelid::regclass::text = ANY($1)`, [TABLES])).rows;
    const pk = Object.fromEntries(constraints.filter(k => k.contype === 'p').map(k => [k.t, k.def]));
    verdict(JSON.stringify(pk) === JSON.stringify({ ops_user_notification_settings:'PRIMARY KEY (actor_id)', ops_user_widget_layouts:'PRIMARY KEY (actor_id, screen)',
      ops_user_notification_cursors:'PRIMARY KEY (actor_id)', ops_user_notification_reads:'PRIMARY KEY (actor_id, item_key)' }),
    `${db}: primary keys settings(actor_id), layouts(actor_id, screen), cursors(actor_id), reads(actor_id, item_key)`, JSON.stringify(pk));
    verdict(!constraints.some(k => k.contype === 'f' || k.contype === 'u'), `${db}: no foreign keys or extra UNIQUE on the 4 tables`, JSON.stringify(constraints.filter(k => k.contype !== 'p' && k.contype !== 'c')));
    const triggers = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text = ANY($1)`, [TABLES])).rows[0].n;
    verdict(triggers === 0, `${db}: no triggers on the 4 tables (mutable API state)`, `triggers=${triggers}`);

    // Grants: exact matrix over the five runtime roles.
    const wrong = [];
    for (const role of ROLES) for (const table of TABLES) {
      const held = [];
      for (const p of PRIVILEGES) if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, table, p])).rows[0].ok) held.push(p);
      const want = role === 'api' ? API_GRANTS[table] : '';
      if (held.join(',') !== want) wrong.push(`${role}.${table}=${held.join(',')}`);
    }
    verdict(wrong.length === 0, `${db}: grants exactly api SELECT,INSERT,UPDATE (+DELETE on reads), nothing for collector, detector, trainer, llm`, JSON.stringify(wrong));

    // API statement shapes (backend services/userSettings.ts and notificationReadModel.ts) through a real LOGIN role.
    const api = await login('api');
    const actor = 'qa-user-1';
    const upsertSettings = `INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated) VALUES($1,$2,$3,$4)
      ON CONFLICT (actor_id) DO UPDATE SET decision_basis=EXCLUDED.decision_basis,min_risk_level=EXCLUDED.min_risk_level,
        include_unrated=EXCLUDED.include_unrated,updated_at=clock_timestamp() RETURNING decision_basis`;
    await api.query(upsertSettings, [actor, 'interim', 'info', true]);
    const second = await api.query(upsertSettings, [actor, 'final_complete', 'critical', false]);
    verdict(second.rows[0].decision_basis === 'final_complete', `${db}: api login upserts notification settings (ON CONFLICT DO UPDATE)`);
    const layout = { version:2, items:[{ i:'flow', x:0, y:0, w:2, h:3 }] };
    await api.query(`INSERT INTO ops_user_widget_layouts(actor_id,screen,layout) VALUES($1,'operations_overview',$2)
      ON CONFLICT (actor_id,screen) DO UPDATE SET layout=EXCLUDED.layout,updated_at=clock_timestamp()`, [actor, JSON.stringify(layout)]);
    check(`${db}: api login upserts the operations_overview widget layout`);
    const cursor = `INSERT INTO ops_user_notification_cursors(actor_id,read_through) VALUES($1,$2)
      ON CONFLICT (actor_id) DO UPDATE SET read_through=GREATEST(ops_user_notification_cursors.read_through,EXCLUDED.read_through),updated_at=clock_timestamp()
      RETURNING read_through::text AS n`;
    await api.query(cursor, [actor, 40]);
    const kept = await api.query(cursor, [actor, 12]);
    verdict(kept.rows[0].n === '40', `${db}: cursor upsert with GREATEST never moves read_through back`, kept.rows[0].n);
    const item = (kind) => `${kind}:${randomUUID()}`;
    const keys = [item('incident'), item('job'), item('decision')];
    for (const [index, key] of keys.entries()) {
      await api.query(`INSERT INTO ops_user_notification_reads(actor_id,item_key,read_seq) VALUES($1,$2,$3)
        ON CONFLICT (actor_id,item_key) DO UPDATE SET read_seq=GREATEST(ops_user_notification_reads.read_seq,EXCLUDED.read_seq),read_at=clock_timestamp()`,
      [actor, key, 30 + index * 10]);
    }
    const pruned = await api.query('DELETE FROM ops_user_notification_reads WHERE actor_id=$1 AND read_seq<=$2', [actor, 40]);
    verdict(pruned.rowCount === 2, `${db}: api login prunes read marks at or below the cursor (DELETE)`, `rowCount=${pruned.rowCount}`);
    await rejected(api, `${db}: api login cannot TRUNCATE read marks`, '42501', 'TRUNCATE ops_user_notification_reads');
    await rejected(api, `${db}: api login cannot DELETE settings`, '42501', 'DELETE FROM ops_user_notification_settings WHERE actor_id=$1', [actor]);

    // Other runtime roles: no read, no write.
    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read notification read marks`, '42501', 'SELECT 1 FROM ops_user_notification_reads LIMIT 1');
      await rejected(other, `${db}: ${role} login cannot write notification settings`, '42501',
        `INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated) VALUES('x','interim','info',true)`);
    }

    // CHECK boundaries (owner, so only the constraint decides).
    const settings = 'INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated) VALUES($1,$2,$3,$4)';
    await rejected(c, `${db}: decision_basis outside interim|final_any|final_complete`, '23514', settings, ['qa-2', 'final', 'info', true]);
    await rejected(c, `${db}: min_risk_level outside info|warning|critical`, '23514', settings, ['qa-2', 'interim', 'unrated', true]);
    await rejected(c, `${db}: empty actor_id`, '23514', settings, ['', 'interim', 'info', true]);
    await rejected(c, `${db}: actor_id longer than 128`, '23514', settings, ['a'.repeat(129), 'interim', 'info', true]);
    await rejected(c, `${db}: include_unrated NULL`, '23502', settings, ['qa-2', 'interim', 'info', null]);
    const layoutSql = 'INSERT INTO ops_user_widget_layouts(actor_id,screen,layout) VALUES($1,$2,$3)';
    await rejected(c, `${db}: layout screen outside operations_overview`, '23514', layoutSql, ['qa-2', 'analysis', '{}']);
    await rejected(c, `${db}: layout that is not a JSON object`, '23514', layoutSql, ['qa-2', 'operations_overview', '[]']);
    await rejected(c, `${db}: layout over 16384 bytes`, '23514', layoutSql, ['qa-2', 'operations_overview', JSON.stringify({ pad:'x'.repeat(16400) })]);
    await rejected(c, `${db}: negative read_through`, '23514', 'INSERT INTO ops_user_notification_cursors(actor_id,read_through) VALUES($1,$2)', ['qa-2', -1]);
    const readSql = 'INSERT INTO ops_user_notification_reads(actor_id,item_key,read_seq) VALUES($1,$2,$3)';
    await rejected(c, `${db}: item_key with an unknown kind`, '23514', readSql, ['qa-2', `flow:${randomUUID()}`, 1]);
    await rejected(c, `${db}: item_key with an upper-case uuid`, '23514', readSql, ['qa-2', `job:${randomUUID().toUpperCase()}`, 1]);
    await rejected(c, `${db}: negative read_seq`, '23514', readSql, ['qa-2', `job:${randomUUID()}`, -1]);
  } finally {
    for (const a of opened) await a.end().catch(() => {});
    await c.end();
  }
}
