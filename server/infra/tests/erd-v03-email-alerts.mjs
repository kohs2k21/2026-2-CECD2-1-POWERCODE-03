// 033: email alert tables (contracts/email-alerts.md), called from erd-v03.mjs inside the same disposable cluster.
// One database erd033_upgrade: migrations before 033 (one string each) + roles script as of 032, then 033: catalog (PK/FK/UNIQUE/partial indexes,
// no trigger, no FK to existing tables), the api login has no access before the roles script is re-applied, exact grant matrix after,
// real LOGIN api statement shapes (settings upsert, enabled CHECK, cursor arming, alert/mapping insert, delivery once key, claim with SKIP LOCKED,
// cascade/set null on alert prune), other runtime roles 42501, CHECK boundaries (port allowlist, security, password format, URL, recipients,
// payload size, state pairs), re-running 033 fails as a whole, remaining migrations and DBML parity. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const TABLES = ['ops_email_alert_settings', 'ops_email_alert_cursor', 'ops_email_alerts', 'ops_email_alert_incidents', 'ops_email_deliveries'];
const ROLES = ['api', 'collector', 'detector', 'trainer', 'llm'];
const PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'];
// Shape of the API ciphertext (services/emailAlerts/secretBox.ts): v1.<key id>.<iv 12 bytes>.<tag 16 bytes>.<ciphertext>, base64url.
const SEALED = `v1.0123abcd.${'A'.repeat(16)}.${'B'.repeat(22)}.${'C'.repeat(40)}`;

export async function emailAlerts033({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, rolesBefore033, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('033_'));
  assert(idx > 0, '033 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd033_upgrade';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const opened = [];
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:db }); await a.connect(); opened.push(a); return a; };
  try {
    for (const sql of migrations.slice(0, idx)) await c.query(sql);
    await c.query(rolesBefore033);
    for (const role of ROLES) {
      logins[role] = { user:`qa033_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }
    const api = await login('api');
    check(`${db}: ${migrationNames[idx - 1]} state with the roles script as of 032`);

    const incidentIndexes = async () => (await c.query(`SELECT count(*)::int AS n FROM pg_index WHERE indrelid IN ('run_incidents'::regclass,'run_decision_events'::regclass)`)).rows[0].n;
    const before = await incidentIndexes();
    await c.query(migrations[idx]);

    // Catalog.
    const constraints = (await c.query(`SELECT conrelid::regclass::text AS t,contype,conname,pg_get_constraintdef(oid) AS def FROM pg_constraint
      WHERE conrelid = ANY($1::regclass[]) AND contype IN ('p','f','u') ORDER BY 1,2,3`, [TABLES])).rows;
    const pk = Object.fromEntries(constraints.filter(k => k.contype === 'p').map(k => [k.t, k.def]));
    verdict(JSON.stringify(pk) === JSON.stringify({ ops_email_alert_cursor:'PRIMARY KEY (id)', ops_email_alert_incidents:'PRIMARY KEY (incident_id)',
      ops_email_alert_settings:'PRIMARY KEY (id)', ops_email_alerts:'PRIMARY KEY (id)', ops_email_deliveries:'PRIMARY KEY (id)' }), `${db}: one PK per new table`, JSON.stringify(pk));
    const fks = constraints.filter(k => k.contype === 'f').map(k => `${k.t}: ${k.def}`).sort();
    verdict(JSON.stringify(fks) === JSON.stringify([
      'ops_email_alert_incidents: FOREIGN KEY (alert_id) REFERENCES ops_email_alerts(id) ON DELETE CASCADE',
      'ops_email_deliveries: FOREIGN KEY (alert_id) REFERENCES ops_email_alerts(id) ON DELETE SET NULL']),
    `${db}: FKs only among the new tables (cascade mapping, set-null deliveries)`, JSON.stringify(fks));
    const uniques = constraints.filter(k => k.contype === 'u').map(k => `${k.conname}: ${k.def}`).sort();
    verdict(JSON.stringify(uniques) === JSON.stringify(['ops_email_alerts_key_unique: UNIQUE (alert_key)',
      'ops_email_deliveries_once_key: UNIQUE (alert_id, risk_rank, recipient)']), `${db}: alert key and delivery once key UNIQUE`, JSON.stringify(uniques));
    const indexes = (await c.query(`SELECT indexname,indexdef FROM pg_indexes WHERE tablename = ANY($1) ORDER BY indexname`, [TABLES])).rows;
    const def = name => indexes.find(i => i.indexname === name)?.indexdef ?? '';
    verdict(/\(next_attempt_at\) WHERE \(status = ANY \(ARRAY\['queued'::text, 'retrying'::text\]\)\)/.test(def('ops_email_deliveries_due_idx'))
      && /\(lease_until\) WHERE \(status = 'sending'::text\)/.test(def('ops_email_deliveries_lease_idx'))
      && /\(created_at DESC, id DESC\)/.test(def('ops_email_deliveries_recent_idx'))
      && /\(alert_id\)/.test(def('ops_email_alert_incidents_alert_idx')) && indexes.length === 11,
    `${db}: due/lease partial indexes, recent index, mapping alert index (11 indexes incl. PK/UNIQUE)`, JSON.stringify(indexes.map(i => i.indexname)));
    const triggers = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = ANY($1::regclass[])`, [TABLES])).rows[0].n;
    verdict(triggers === 0, `${db}: no trigger on the new tables`, `triggers=${triggers}`);
    verdict(await incidentIndexes() === before, `${db}: 033 adds no index to run_incidents or run_decision_events`);

    // Deployment order: 033 → roles → API. Before the roles script the api login has no access.
    await rejected(api, `${db}: api login without roles re-apply cannot read ops_email_alert_settings`, '42501', 'SELECT 1 FROM ops_email_alert_settings');
    await c.query(rolesSql);
    const wrong = [];
    for (const table of TABLES) for (const role of ROLES) {
      const held = [];
      for (const p of PRIVILEGES) if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, table, p])).rows[0].ok) held.push(p);
      const want = role === 'api' ? 'SELECT,INSERT,UPDATE,DELETE' : '';
      if (held.join(',') !== want) wrong.push(`${table}:${role}=${held.join(',')}`);
    }
    verdict(wrong.length === 0, `${db}: grants exactly api SELECT,INSERT,UPDATE,DELETE on the 5 tables, nothing for collector, detector, trainer, llm`, JSON.stringify(wrong));

    // API statement shapes through a real LOGIN role (services/emailAlerts/settingsStore.ts, scan.ts, outbox.ts).
    const upsert = `INSERT INTO ops_email_alert_settings(id,enabled,notify_incidents,notify_decisions,min_risk_level,recipients,smtp_host,smtp_port,smtp_security,
        smtp_username,smtp_password_enc,from_address,public_base_url,updated_by_actor_id)
      VALUES(1,$1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)
      ON CONFLICT (id) DO UPDATE SET enabled=EXCLUDED.enabled,notify_incidents=EXCLUDED.notify_incidents,notify_decisions=EXCLUDED.notify_decisions,
        min_risk_level=EXCLUDED.min_risk_level,recipients=EXCLUDED.recipients,smtp_host=EXCLUDED.smtp_host,smtp_port=EXCLUDED.smtp_port,
        smtp_security=EXCLUDED.smtp_security,smtp_username=EXCLUDED.smtp_username,smtp_password_enc=EXCLUDED.smtp_password_enc,
        from_address=EXCLUDED.from_address,public_base_url=EXCLUDED.public_base_url,updated_by_actor_id=EXCLUDED.updated_by_actor_id,updated_at=clock_timestamp()`;
    const full = [true, true, false, 'critical', JSON.stringify(['ops@example.test']), 'smtp.example.test', 587, 'starttls', 'alerts', SEALED,
      'alerts@example.test', 'http://dashboard.example.test:8080', 'qa-admin'];
    await rejected(api, `${db}: enabled without SMTP host is rejected`, '23514', upsert, [true, true, false, 'critical', '["ops@example.test"]', null, null, null, null, null, null, null, 'qa-admin']);
    await rejected(api, `${db}: enabled without recipients is rejected`, '23514', upsert, full.map((v, i) => i === 4 ? '[]' : v));
    await rejected(api, `${db}: enabled without any alert kind is rejected`, '23514', upsert, full.map((v, i) => i === 1 ? false : v));
    await api.query(upsert, [false, true, false, 'critical', '[]', null, null, null, null, null, null, null, 'qa-admin']);
    await api.query(upsert, full);
    const stored = (await api.query('SELECT enabled,smtp_port,smtp_security,smtp_password_enc FROM ops_email_alert_settings')).rows[0];
    verdict(stored.enabled === true && stored.smtp_port === 587 && stored.smtp_password_enc === SEALED, `${db}: api login stores complete settings with the sealed password`);
    await rejected(api, `${db}: a second settings row is rejected`, '23514', `INSERT INTO ops_email_alert_settings(id,enabled,notify_incidents,notify_decisions,min_risk_level,recipients,updated_by_actor_id)
      VALUES(2,false,true,false,'critical','[]','qa')`);
    await api.query(`UPDATE ops_email_alert_settings SET transport_checked_at=clock_timestamp(),transport_result='auth_failed',transport_error_code='smtp_auth_failed'
      WHERE smtp_changed_at=(SELECT smtp_changed_at FROM ops_email_alert_settings)`);
    check(`${db}: api login records a connection check result`);

    const arm = `INSERT INTO ops_email_alert_cursor(id,processed_through_seq,armed_at) VALUES(1,(SELECT coalesce(max(id),0) FROM run_decision_events),clock_timestamp())
      ON CONFLICT (id) DO UPDATE SET processed_through_seq=EXCLUDED.processed_through_seq,armed_at=EXCLUDED.armed_at`;
    await api.query(arm); await api.query(arm);
    verdict((await api.query('SELECT count(*)::int AS n FROM ops_email_alert_cursor')).rows[0].n === 1, `${db}: api login arms the cursor (one row)`);

    const alertId = randomUUID(), incidentA = randomUUID(), incidentB = randomUUID();
    const alertKey = `incident:${incidentA}`;
    await api.query(`INSERT INTO ops_email_alerts(id,alert_key,kind,first_seq,last_seq,notified_rank) VALUES($1,$2,'incident',10,10,3)`, [alertId, alertKey]);
    const again = await api.query(`INSERT INTO ops_email_alerts(id,alert_key,kind,first_seq,last_seq,notified_rank) VALUES($1,$2,'incident',11,11,3) ON CONFLICT (alert_key) DO NOTHING`, [randomUUID(), alertKey]);
    verdict(again.rowCount === 0, `${db}: same alert key twice inserts nothing (ON CONFLICT)`);
    await api.query(`INSERT INTO ops_email_alert_incidents(incident_id,alert_id) SELECT unnest($1::uuid[]),$2 ON CONFLICT DO NOTHING`, [[incidentA, incidentB], alertId]);
    const delivery = `INSERT INTO ops_email_deliveries(id,alert_id,kind,reason,risk_rank,trigger_seq,recipient,payload,status,next_attempt_at)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,clock_timestamp()) ON CONFLICT (alert_id,risk_rank,recipient) DO NOTHING`;
    const first = await api.query(delivery, [randomUUID(), alertId, 'incident', 'opened', 3, 10, 'ops@example.test', '{"incidentId":"x"}', 'queued']);
    const dup = await api.query(delivery, [randomUUID(), alertId, 'incident', 'opened', 3, 12, 'ops@example.test', '{}', 'queued']);
    verdict(first.rowCount === 1 && dup.rowCount === 0, `${db}: delivery once key: same alert, rank and recipient queue once`);
    await api.query(delivery, [randomUUID(), alertId, 'incident', 'opened', 3, 10, 'second@example.test', '{}', 'suppressed']);
    const claimed = await api.query(`WITH due AS (SELECT id FROM ops_email_deliveries WHERE status IN ('queued','retrying') AND next_attempt_at<=clock_timestamp()
        ORDER BY next_attempt_at,id LIMIT 20 FOR UPDATE SKIP LOCKED)
      UPDATE ops_email_deliveries d SET status='sending',attempts=d.attempts+1,lease_until=clock_timestamp()+interval '2 minutes' FROM due WHERE d.id=due.id RETURNING d.id`);
    verdict(claimed.rowCount === 1, `${db}: api login claims the due delivery with FOR UPDATE SKIP LOCKED (suppressed row not claimed)`);
    await api.query(`UPDATE ops_email_deliveries SET status='sent',sent_at=clock_timestamp(),finished_at=clock_timestamp(),lease_until=NULL,smtp_code=250 WHERE id=$1`, [claimed.rows[0].id]);
    const tests = `INSERT INTO ops_email_deliveries(id,kind,reason,recipient,payload,status,lease_until,requested_by_actor_id,attempts)
      VALUES($1,'test','test',$2,'{}','sending',clock_timestamp()+interval '1 minute',$3,1)`;
    await api.query(tests, [randomUUID(), 'ops@example.test', 'qa-admin']);
    await api.query(tests, [randomUUID(), 'ops@example.test', 'qa-admin']);
    check(`${db}: test deliveries (alert_id NULL) are not limited by the once key`);
    const pruned = await api.query(`DELETE FROM ops_email_alerts WHERE id=$1`, [alertId]);
    const left = (await api.query(`SELECT (SELECT count(*)::int FROM ops_email_alert_incidents) AS mapped,
      (SELECT count(*)::int FROM ops_email_deliveries WHERE alert_id IS NULL AND kind='incident') AS orphaned`)).rows[0];
    verdict(pruned.rowCount === 1 && left.mapped === 0 && left.orphaned === 2, `${db}: pruning an alert target deletes its mapping and keeps deliveries with alert_id NULL`, JSON.stringify(left));
    const oldRows = await api.query(`DELETE FROM ops_email_deliveries WHERE created_at<clock_timestamp()-interval '90 days'`);
    verdict(oldRows.rowCount === 0, `${db}: api login prunes old deliveries (DELETE grant)`);
    await rejected(api, `${db}: api login cannot TRUNCATE ops_email_deliveries`, '42501', 'TRUNCATE ops_email_deliveries');

    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      for (const table of ['ops_email_alert_settings', 'ops_email_deliveries']) {
        await rejected(other, `${db}: ${role} login cannot read ${table}`, '42501', `SELECT 1 FROM ${table} LIMIT 1`);
      }
    }

    // CHECK / NOT NULL boundaries (owner, so only the constraint decides).
    const set = async (label, column, value, code = '23514') => rejected(c, `${db}: settings ${label}`, code, `UPDATE ops_email_alert_settings SET ${column}=$1`, [value]);
    await set('port outside the SMTP allowlist', 'smtp_port', 2526);
    await set('port 5432 (not SMTP)', 'smtp_port', 5432);
    await set('plaintext security mode', 'smtp_security', 'none');
    await set('password not in the sealed v1 format', 'smtp_password_enc', 'hunter2');
    await set('sealed password with a bad tag length', 'smtp_password_enc', `v1.0123abcd.${'A'.repeat(16)}.${'B'.repeat(21)}.CC`);
    await set('public URL with a query', 'public_base_url', 'http://dash.example.test/?a=1');
    await set('public URL that is not http(s)', 'public_base_url', 'ftp://dash.example.test');
    await set('recipients not an array', 'recipients', '{"a":1}');
    await set('more than 10 recipients', 'recipients', JSON.stringify(Array.from({ length: 11 }, (_, i) => `r${i}@example.test`)));
    await set('unknown risk level', 'min_risk_level', 'high');
    await set('empty host', 'smtp_host', '');
    await set('error code with capitals', 'transport_error_code', 'SMTP_AUTH');
    await rejected(c, `${db}: settings password without username`, '23514', `UPDATE ops_email_alert_settings SET smtp_username=NULL`);
    await rejected(c, `${db}: settings check time without result`, '23514', `UPDATE ops_email_alert_settings SET transport_result=NULL`);
    await rejected(c, `${db}: settings error code on an ok result`, '23514', `UPDATE ops_email_alert_settings SET transport_result='ok'`);
    const row = (extra) => ({ id:randomUUID(), alert_id:null, kind:'incident', reason:'opened', risk_rank:3, trigger_seq:1, recipient:'ops@example.test',
      payload:'{}', status:'queued', next_attempt_at:new Date(), lease_until:null, sent_at:null, requested_by_actor_id:null, attempts:0, ...extra });
    const insertRow = `INSERT INTO ops_email_deliveries(id,alert_id,kind,reason,risk_rank,trigger_seq,recipient,payload,status,next_attempt_at,lease_until,sent_at,requested_by_actor_id,attempts)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14)`;
    const bad = async (label, extra, code = '23514') => { const r = row(extra); await rejected(c, `${db}: delivery ${label}`, code, insertRow, Object.values(r)); };
    await bad('test kind with a non-test reason', { kind:'test' });
    await bad('test without requester', { kind:'test', reason:'test', risk_rank:null, trigger_seq:null });
    await bad('alert delivery without rank', { risk_rank:null });
    await bad('sending without lease', { status:'sending' });
    await bad('queued without next attempt', { next_attempt_at:null });
    await bad('sent without sent_at', { status:'sent' });
    await bad('unknown status', { status:'delivered' });
    await bad('six attempts', { attempts:6 });
    await bad('payload over 8 KB', { payload:JSON.stringify({ x:'y'.repeat(8200) }) });
    await bad('payload not an object', { payload:'[]' });
    await bad('NULL recipient', { recipient:null }, '23502');
    await rejected(c, `${db}: alert kind and key prefix disagree`, '23514', `INSERT INTO ops_email_alerts(id,alert_key,kind,first_seq,last_seq,notified_rank) VALUES($1,$2,'decision',1,1,3)`, [randomUUID(), `incident:${randomUUID()}`]);
    await rejected(c, `${db}: alert rank 4`, '23514', `INSERT INTO ops_email_alerts(id,alert_key,kind,first_seq,last_seq,notified_rank) VALUES($1,$2,'decision',1,1,4)`, [randomUUID(), `job:${randomUUID()}`]);
    await rejected(c, `${db}: alert key not a lowercase uuid`, '23514', `INSERT INTO ops_email_alerts(id,alert_key,kind,first_seq,last_seq,notified_rank) VALUES($1,'job:ABC','decision',1,1,3)`, [randomUUID()]);
    await rejected(c, `${db}: mapping to a missing alert`, '23503', `INSERT INTO ops_email_alert_incidents(incident_id,alert_id) VALUES($1,$2)`, [randomUUID(), randomUUID()]);
    await rejected(c, `${db}: a second cursor row`, '23514', `INSERT INTO ops_email_alert_cursor(id,processed_through_seq,armed_at) VALUES(2,0,clock_timestamp())`);

    // Re-running 033 fails on the existing table and changes nothing (single transaction).
    const count = async () => (await c.query('SELECT count(*)::int AS n FROM ops_email_deliveries')).rows[0].n;
    const kept = await count();
    await rejected(c, `${db}: re-running 033 fails as a whole`, '42P07', migrations[idx]);
    await c.query('ROLLBACK').catch(() => {});
    verdict(await count() === kept, `${db}: failed re-run leaves the tables and rows as they were`);

    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally {
    for (const a of opened) await a.end().catch(() => {});
    await c.end();
  }
}
