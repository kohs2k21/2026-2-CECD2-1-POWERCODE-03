// 025: response code dictionary, called from erd-v03.mjs inside the same disposable cluster.
// One database erd025: every migration file (one string each), catalog shape (PK, one FK change log -> dictionary, no trigger), DBML parity,
// exact grant matrix, real LOGIN roles (api: insert/update dictionary rows, insert-only change log; DELETE/TRUNCATE 42501;
// other runtime roles 42501), CHECK boundaries. Synthetic codes and messages only (no customer dictionary values).
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';

const TABLES = ['ops_response_codes', 'ops_response_code_changes'];
const ROLES = ['api', 'collector', 'detector', 'trainer', 'llm'];
const PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'];
const API_GRANTS = { ops_response_codes: 'SELECT,INSERT,UPDATE', ops_response_code_changes: 'SELECT,INSERT' };

export async function responseCodes025({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('025_'));
  assert(idx > 0, '025 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd025';
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
      logins[role] = { user:`qa025_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }

    const constraints = (await c.query(`SELECT conrelid::regclass::text AS t, contype, pg_get_constraintdef(oid) AS def
      FROM pg_constraint WHERE conrelid::regclass::text = ANY($1)`, [TABLES])).rows;
    const pk = Object.fromEntries(constraints.filter(k => k.contype === 'p').map(k => [k.t, k.def]));
    verdict(JSON.stringify(pk) === JSON.stringify({ ops_response_codes:'PRIMARY KEY (code)', ops_response_code_changes:'PRIMARY KEY (id)' }),
      `${db}: primary keys dictionary(code), changes(id)`, JSON.stringify(pk));
    const fks = constraints.filter(k => k.contype === 'f').map(k => `${k.t}:${k.def}`);
    verdict(fks.length === 1 && fks[0] === 'ops_response_code_changes:FOREIGN KEY (code) REFERENCES ops_response_codes(code)',
      `${db}: only FK is change log code -> dictionary code`, JSON.stringify(fks));
    verdict(!constraints.some(k => k.contype === 'u'), `${db}: no extra UNIQUE on the 2 tables`);
    const triggers = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text = ANY($1)`, [TABLES])).rows[0].n;
    verdict(triggers === 0, `${db}: no triggers on the 2 tables`, `triggers=${triggers}`);
    const index = (await c.query(`SELECT indexdef FROM pg_indexes WHERE indexname='ops_response_code_changes_code_idx'`)).rows[0]?.indexdef ?? '';
    verdict(index.includes('(code, id DESC)'), `${db}: change log index (code, id DESC)`, index);

    const wrong = [];
    for (const role of ROLES) for (const table of TABLES) {
      const held = [];
      for (const p of PRIVILEGES) if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, table, p])).rows[0].ok) held.push(p);
      const want = role === 'api' ? API_GRANTS[table] : '';
      if (held.join(',') !== want) wrong.push(`${role}.${table}=${held.join(',')}`);
    }
    verdict(wrong.length === 0, `${db}: grants exactly api SELECT,INSERT,UPDATE on dictionary and SELECT,INSERT on change log, nothing for other roles`, JSON.stringify(wrong));

    const api = await login('api');
    const insertCode = `INSERT INTO ops_response_codes(code,enum_name,code_type,http_status,message_key,message,origin,updated_by_actor_id)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8)`;
    await api.query(insertCode, ['T001', 'SYN_FIRST', 'SYN_TYPE', '500 SYN', 'syn.first', '합성 메시지 1', 'import', 'system:import']);
    await api.query(insertCode, ['9100', null, null, null, null, '합성 메시지 2', 'edited', 'qa-admin']);
    const fresh = (await api.query(`SELECT revision FROM ops_response_codes WHERE code='T001'`)).rows[0];
    verdict(fresh?.revision === 1, `${db}: api login inserts dictionary rows, revision starts at 1 and optional columns may be NULL`);
    const updated = await api.query(`UPDATE ops_response_codes SET message=$2,origin='edited',revision=revision+1,updated_at=clock_timestamp(),updated_by_actor_id=$3
      WHERE code=$1 AND revision=$4 RETURNING revision`, ['T001', '합성 메시지 1 수정', 'qa-admin', 1]);
    const stale = await api.query(`UPDATE ops_response_codes SET message='x' WHERE code=$1 AND revision=$2`, ['T001', 1]);
    verdict(updated.rows[0]?.revision === 2 && stale.rowCount === 0, `${db}: api login edits in place with a revision guard (stale revision updates nothing)`);
    const log = `INSERT INTO ops_response_code_changes(code,kind,message_before,message_after,actor_id) VALUES($1,$2,$3,$4,$5) RETURNING id::text AS id`;
    const first = await api.query(log, ['T001', 'import', null, '합성 메시지 1', 'system:import']);
    const second = await api.query(log, ['T001', 'edit', '합성 메시지 1', '합성 메시지 1 수정', 'qa-admin']);
    verdict(BigInt(second.rows[0].id) > BigInt(first.rows[0].id), `${db}: api login appends change log rows without a sequence grant (identity)`);
    await rejected(api, `${db}: api login cannot DELETE dictionary rows`, '42501', `DELETE FROM ops_response_codes WHERE code='9100'`);
    await rejected(api, `${db}: api login cannot TRUNCATE the dictionary`, '42501', 'TRUNCATE ops_response_codes CASCADE');
    await rejected(api, `${db}: api login cannot UPDATE change log rows`, '42501', `UPDATE ops_response_code_changes SET message_after='x'`);
    await rejected(api, `${db}: api login cannot DELETE change log rows`, '42501', 'DELETE FROM ops_response_code_changes');
    await rejected(api, `${db}: change log id is GENERATED ALWAYS`, '428C9',
      `INSERT INTO ops_response_code_changes(id,code,kind,message_after,actor_id) VALUES(1,'T001','edit','m','a')`);
    await rejected(api, `${db}: change log row for a code not in the dictionary`, '23503', log, ['T999', 'edit', null, 'm', 'a']);
    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read the dictionary`, '42501', 'SELECT 1 FROM ops_response_codes LIMIT 1');
      await rejected(other, `${db}: ${role} login cannot write the change log`, '42501',
        `INSERT INTO ops_response_code_changes(code,kind,message_after,actor_id) VALUES('T001','edit','m','a')`);
    }

    const row = (code, extra = {}) => {
      const v = { enum_name: null, code_type: null, http_status: null, message_key: null, message: 'm', origin: 'import', actor: 'a', ...extra };
      return [code, v.enum_name, v.code_type, v.http_status, v.message_key, v.message, v.origin, v.actor];
    };
    await rejected(c, `${db}: empty code`, '23514', insertCode, row(''));
    await rejected(c, `${db}: code with a space`, '23514', insertCode, row('T 1'));
    await rejected(c, `${db}: code with non-ASCII`, '23514', insertCode, row('코드'));
    await rejected(c, `${db}: code longer than 64`, '23514', insertCode, row('C'.repeat(65)));
    await c.query(insertCode, row('~!' + 'Z'.repeat(62)));
    check(`${db}: 64-character printable ASCII code is stored`);
    await rejected(c, `${db}: duplicate code`, '23505', insertCode, row('T001'));
    await rejected(c, `${db}: empty message`, '23514', insertCode, row('T002', { message: '' }));
    await rejected(c, `${db}: message NULL`, '23502', insertCode, row('T002', { message: null }));
    await rejected(c, `${db}: message over 500 characters`, '23514', insertCode, row('T002', { message: '가'.repeat(501) }));
    await c.query(insertCode, row('T003', { message: '가'.repeat(500) }));
    check(`${db}: 500-character message is stored`);
    await rejected(c, `${db}: unknown origin`, '23514', insertCode, row('T002', { origin: 'manual' }));
    await rejected(c, `${db}: empty enum_name`, '23514', insertCode, row('T002', { enum_name: '' }));
    await rejected(c, `${db}: code_type over 64`, '23514', insertCode, row('T002', { code_type: 't'.repeat(65) }));
    await rejected(c, `${db}: http_status over 64`, '23514', insertCode, row('T002', { http_status: 'h'.repeat(65) }));
    await rejected(c, `${db}: message_key over 256`, '23514', insertCode, row('T002', { message_key: 'k'.repeat(257) }));
    await rejected(c, `${db}: actor over 128`, '23514', insertCode, row('T002', { actor: 'a'.repeat(129) }));
    await rejected(c, `${db}: revision 0`, '23514', `UPDATE ops_response_codes SET revision=0 WHERE code='T001'`);
    await rejected(c, `${db}: change log kind unknown`, '23514', log, ['T001', 'delete', null, 'm', 'a']);
    await rejected(c, `${db}: change log empty message_after`, '23514', log, ['T001', 'edit', null, '', 'a']);
    await rejected(c, `${db}: change log message_before over 500`, '23514', log, ['T001', 'edit', 'x'.repeat(501), 'm', 'a']);
  } finally {
    for (const a of opened) await a.end().catch(() => {});
    await c.end();
  }
}
