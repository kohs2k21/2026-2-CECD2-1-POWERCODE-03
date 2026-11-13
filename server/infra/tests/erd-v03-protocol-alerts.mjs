// 023: protocol alert actions, called from erd-v03.mjs inside the same disposable cluster.
// One database erd023: every migration file (one string each), catalog shape (PK only, no FK, no trigger), DBML parity, exact grant matrix,
// real LOGIN roles (api: insert-only keeps with ON CONFLICT DO NOTHING, read-mark upsert/prune; other runtime roles get 42501), CHECK boundaries.
// Small synthetic fixture rows only.
import { createHash, randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';

const TABLES = ['ops_protocol_alert_keeps', 'ops_user_protocol_alert_reads'];
const ROLES = ['api', 'collector', 'detector', 'trainer', 'llm'];
const PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'];
const API_GRANTS = { ops_protocol_alert_keeps: 'SELECT,INSERT', ops_user_protocol_alert_reads: 'SELECT,INSERT,UPDATE,DELETE' };
/** API와 같은 키: 'protocol:' || sha256(jsonb_build_array(interface_id, path_key)::text) 16진(notificationReadModel.ts PROTOCOL_KEY_SQL). */
const KEY_SQL = `'protocol:'||encode(sha256(convert_to(jsonb_build_array($1::text,$2::text)::text,'UTF8')),'hex')`;

export async function protocolAlerts023({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('023_'));
  assert(idx > 0, '023 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd023';
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
      logins[role] = { user:`qa023_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }

    const constraints = (await c.query(`SELECT conrelid::regclass::text AS t, contype, pg_get_constraintdef(oid) AS def
      FROM pg_constraint WHERE conrelid::regclass::text = ANY($1)`, [TABLES])).rows;
    const pk = Object.fromEntries(constraints.filter(k => k.contype === 'p').map(k => [k.t, k.def]));
    verdict(JSON.stringify(pk) === JSON.stringify({ ops_protocol_alert_keeps:'PRIMARY KEY (alert_key)', ops_user_protocol_alert_reads:'PRIMARY KEY (actor_id, item_key)' }),
      `${db}: primary keys keeps(alert_key), reads(actor_id, item_key)`, JSON.stringify(pk));
    verdict(!constraints.some(k => k.contype === 'f' || k.contype === 'u'), `${db}: no foreign keys or extra UNIQUE on the 2 tables`);
    const triggers = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text = ANY($1)`, [TABLES])).rows[0].n;
    verdict(triggers === 0, `${db}: no triggers on the 2 tables`, `triggers=${triggers}`);
    const unchanged = (await c.query(`SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid='ops_user_notification_reads'::regclass AND contype='c'
      AND pg_get_constraintdef(oid) LIKE '%item_key%'`)).rows.map(r => r.def).join();
    verdict(unchanged.includes('incident|job|decision') && !unchanged.includes('protocol'), `${db}: the 022 read-mark item_key CHECK is unchanged`, unchanged);

    const wrong = [];
    for (const role of ROLES) for (const table of TABLES) {
      const held = [];
      for (const p of PRIVILEGES) if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, table, p])).rows[0].ok) held.push(p);
      const want = role === 'api' ? API_GRANTS[table] : '';
      if (held.join(',') !== want) wrong.push(`${role}.${table}=${held.join(',')}`);
    }
    verdict(wrong.length === 0, `${db}: grants exactly api SELECT,INSERT on keeps and SELECT,INSERT,UPDATE,DELETE on reads, nothing for other roles`, JSON.stringify(wrong));

    const api = await login('api');
    const key = (await api.query(`SELECT ${KEY_SQL} AS k`, ['IF_SYN', 'CH_A:CH%3AB'])).rows[0].k;
    const expected = 'protocol:' + createHash('sha256').update(JSON.stringify(['IF_SYN', 'CH_A:CH%3AB']).replace('","', '", "'), 'utf8').digest('hex');
    verdict(key === expected, `${db}: alert key = sha256 of jsonb array text (interface, pathKey)`, `${key} vs ${expected}`);
    const keep = `INSERT INTO ops_protocol_alert_keeps(alert_key,interface_id,path_key,kept_through_seq,decision_id,kept_by_actor_id)
      VALUES(${KEY_SQL},$1,$2,$3,$4,$5) ON CONFLICT (alert_key) DO NOTHING RETURNING kept_through_seq::text AS seq`;
    const first = await api.query(keep, ['IF_SYN', 'CH_A:CH%3AB', 40, randomUUID(), 'qa-admin']);
    const again = await api.query(keep, ['IF_SYN', 'CH_A:CH%3AB', 90, randomUUID(), 'qa-admin-2']);
    verdict(first.rows[0]?.seq === '40' && again.rowCount === 0, `${db}: api login records [keep anomaly] once (ON CONFLICT DO NOTHING keeps the first)`);
    await api.query(keep, ['IF_SYN', '', 41, null, 'qa-admin']);
    check(`${db}: api login records an interface-level keep (path_key '')`);
    await rejected(api, `${db}: api login cannot UPDATE keeps`, '42501', 'UPDATE ops_protocol_alert_keeps SET kept_through_seq=0');
    await rejected(api, `${db}: api login cannot DELETE keeps`, '42501', 'DELETE FROM ops_protocol_alert_keeps');
    const read = `INSERT INTO ops_user_protocol_alert_reads(actor_id,item_key,read_seq) VALUES($1,$2,$3)
      ON CONFLICT (actor_id,item_key) DO UPDATE SET read_seq=GREATEST(ops_user_protocol_alert_reads.read_seq,EXCLUDED.read_seq),read_at=clock_timestamp() RETURNING read_seq::text AS n`;
    await api.query(read, ['qa-user', key, 50]);
    verdict((await api.query(read, ['qa-user', key, 10])).rows[0].n === '50', `${db}: api login read-mark upsert with GREATEST`);
    const pruned = await api.query(`DELETE FROM ops_user_protocol_alert_reads WHERE actor_id=$1 AND read_seq<=$2::bigint-$3::bigint`, ['qa-user', 5100, 5000]);
    verdict(pruned.rowCount === 1, `${db}: api login prunes protocol read marks outside the window`, `rowCount=${pruned.rowCount}`);
    await rejected(api, `${db}: api login cannot TRUNCATE protocol read marks`, '42501', 'TRUNCATE ops_user_protocol_alert_reads');
    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read keeps`, '42501', 'SELECT 1 FROM ops_protocol_alert_keeps LIMIT 1');
      await rejected(other, `${db}: ${role} login cannot write protocol read marks`, '42501',
        `INSERT INTO ops_user_protocol_alert_reads(actor_id,item_key,read_seq) VALUES('x','protocol:${'0'.repeat(64)}',1)`);
    }

    const keepSql = 'INSERT INTO ops_protocol_alert_keeps(alert_key,interface_id,path_key,kept_through_seq,kept_by_actor_id) VALUES($1,$2,$3,$4,$5)';
    const okKey = () => 'protocol:' + createHash('sha256').update(randomUUID()).digest('hex');
    await rejected(c, `${db}: alert_key not protocol:<64 hex>`, '23514', keepSql, [`protocol:${'A'.repeat(64)}`, 'IF', 'P', 1, 'a']);
    await rejected(c, `${db}: alert_key with a uuid`, '23514', keepSql, [`job:${randomUUID()}`, 'IF', 'P', 1, 'a']);
    await rejected(c, `${db}: empty interface_id`, '23514', keepSql, [okKey(), '', 'P', 1, 'a']);
    await rejected(c, `${db}: interface_id over 512 characters`, '23514', keepSql, [okKey(), 'x'.repeat(513), 'P', 1, 'a']);
    await rejected(c, `${db}: negative kept_through_seq`, '23514', keepSql, [okKey(), 'IF', 'P', -1, 'a']);
    await rejected(c, `${db}: empty kept_by_actor_id`, '23514', keepSql, [okKey(), 'IF', 'P', 1, '']);
    await rejected(c, `${db}: path_key NULL`, '23502', keepSql, [okKey(), 'IF', null, 1, 'a']);
    await c.query(keepSql, [okKey(), 'x'.repeat(512), 'p'.repeat(200000), 1, 'a']);
    check(`${db}: 512-character interface and a long pathKey are stored (PK is the fixed-length key)`);
    const readSql = 'INSERT INTO ops_user_protocol_alert_reads(actor_id,item_key,read_seq) VALUES($1,$2,$3)';
    await rejected(c, `${db}: read item_key with another kind`, '23514', readSql, ['qa-2', `incident:${randomUUID()}`, 1]);
    await rejected(c, `${db}: negative read_seq`, '23514', readSql, ['qa-2', okKey(), -1]);
    await rejected(c, `${db}: actor_id longer than 128`, '23514', readSql, ['a'.repeat(129), okKey(), 1]);
  } finally {
    for (const a of opened) await a.end().catch(() => {});
    await c.end();
  }
}
