// 028: job notification read marks and settings/cursor columns, called from erd-v03.mjs inside the same disposable cluster.
// One database erd028_upgrade: migrations before 028 (one string each) + roles script as of 027 + rows written with the pre-028 API statements,
// then 028: existing rows get include_jobs true / jobs_read_through NULL, new table catalog (PK only, no FK, no trigger), api has no access before the
// roles script is re-applied, exact grant matrix after, real LOGIN api statement shapes (settings upsert, GREATEST cursor, job read upsert, prune DELETE,
// run_jobs columns the job notifications read), other runtime roles 42501, CHECK boundaries, re-running 028 fails as a whole, DBML parity. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const TABLE = 'ops_user_job_notification_reads';
const ROLES = ['api', 'collector', 'detector', 'trainer', 'llm'];
const PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'];

export async function jobNotifications028({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity }) {
  const idx = migrationNames.findIndex(n => n.startsWith('028_'));
  assert(idx > 0, '028 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd028_upgrade';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const opened = [];
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:db }); await a.connect(); opened.push(a); return a; };
  try {
    for (const sql of migrations.slice(0, idx)) await c.query(sql);
    // Roles script as of 027: the 028 block grants on a table that does not exist yet.
    const rolesTo027 = rolesSql.replace(/^-- 028: [\s\S]*?(?=^-- 024 )/m, '');
    assert.notEqual(rolesTo027, rolesSql, 'database-roles.sql has the 028 grant block before the 024 block');
    await c.query(rolesTo027);
    for (const role of ROLES) {
      logins[role] = { user:`qa028_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }
    const api = await login('api');
    // Pre-028 API statements (services/userSettings.ts and notificationReadModel.ts after 026).
    const settingsBefore = `INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated,window_days)
      VALUES($1,$2,$3,$4,coalesce($5::integer,$6::integer))
      ON CONFLICT (actor_id) DO UPDATE SET decision_basis=EXCLUDED.decision_basis,min_risk_level=EXCLUDED.min_risk_level,
        include_unrated=EXCLUDED.include_unrated,window_days=coalesce($5::integer,ops_user_notification_settings.window_days),updated_at=clock_timestamp()`;
    const cursorBefore = `INSERT INTO ops_user_notification_cursors(actor_id,read_through) VALUES($1,least($2::bigint,$3::bigint))
      ON CONFLICT (actor_id) DO UPDATE SET read_through=GREATEST(ops_user_notification_cursors.read_through,EXCLUDED.read_through),updated_at=clock_timestamp()`;
    await api.query(settingsBefore, ['qa-old', 'final_any', 'warning', false, 14, 7]);
    await api.query(cursorBefore, ['qa-old', 40, 50]);
    check(`${db}: ${migrationNames[idx - 1]} state with settings and cursor rows written by the pre-028 API statements`);

    const runJobsIndexes = async () => (await c.query(`SELECT count(*)::int AS n FROM pg_index WHERE indrelid='run_jobs'::regclass`)).rows[0].n;
    const indexesBefore = await runJobsIndexes();
    await c.query(migrations[idx]);
    const kept = (await c.query(`SELECT s.decision_basis,s.window_days,s.include_jobs,k.read_through::text AS rt,k.jobs_read_through
      FROM ops_user_notification_settings s JOIN ops_user_notification_cursors k USING (actor_id) WHERE actor_id='qa-old'`)).rows[0];
    verdict(kept.decision_basis === 'final_any' && kept.window_days === 14 && kept.include_jobs === true && kept.rt === '40' && kept.jobs_read_through === null,
      `${db}: 028 keeps existing rows (include_jobs true, jobs_read_through NULL)`, JSON.stringify(kept));
    const columns = (await c.query(`SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns
      WHERE (table_name,column_name) IN (('ops_user_notification_settings','include_jobs'),('ops_user_notification_cursors','jobs_read_through')) ORDER BY table_name`)).rows;
    verdict(JSON.stringify(columns) === JSON.stringify([
      { table_name:'ops_user_notification_cursors', column_name:'jobs_read_through', data_type:'timestamp with time zone', is_nullable:'YES', column_default:null },
      { table_name:'ops_user_notification_settings', column_name:'include_jobs', data_type:'boolean', is_nullable:'NO', column_default:'true' }]),
    `${db}: include_jobs boolean NOT NULL DEFAULT true, jobs_read_through timestamptz NULL`, JSON.stringify(columns));
    const constraints = (await c.query(`SELECT contype,pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid=$1::regclass ORDER BY contype`, [TABLE])).rows;
    verdict(constraints.filter(k => k.contype === 'p').map(k => k.def).join() === 'PRIMARY KEY (actor_id, job_id)'
      && !constraints.some(k => k.contype === 'f' || k.contype === 'u'), `${db}: ${TABLE} PK (actor_id, job_id), no FK, no extra UNIQUE`, JSON.stringify(constraints));
    const extra = (await c.query(`SELECT count(*)::int AS n FROM pg_trigger WHERE NOT tgisinternal AND tgrelid=$1::regclass`, [TABLE])).rows[0].n
      + (await c.query(`SELECT count(*)::int AS n FROM pg_index WHERE indrelid=$1::regclass AND NOT indisprimary`, [TABLE])).rows[0].n;
    verdict(extra === 0, `${db}: no trigger or extra index on ${TABLE}`, `extra=${extra}`);
    const indexesAfter = await runJobsIndexes();
    verdict(indexesAfter === indexesBefore, `${db}: 028 adds no run_jobs index`, `${indexesBefore} -> ${indexesAfter}`);

    // Before the roles script is re-applied the api login has no access to the new table (deployment order: 028 → roles → API).
    await rejected(api, `${db}: api login without roles re-apply cannot read ${TABLE}`, '42501', `SELECT 1 FROM ${TABLE} LIMIT 1`);
    // Same api session: the table-level 022 grants cover the new columns.
    await api.query(settingsBefore, ['qa-old', 'interim', 'info', true, null, 7]);
    verdict((await api.query(`SELECT include_jobs FROM ops_user_notification_settings WHERE actor_id='qa-old'`)).rows[0].include_jobs === true,
      `${db}: api login (no roles re-apply) reads include_jobs; pre-028 upsert leaves it`);

    await c.query(rolesSql);
    const wrong = [];
    for (const role of ROLES) {
      const held = [];
      for (const p of PRIVILEGES) if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, TABLE, p])).rows[0].ok) held.push(p);
      const want = role === 'api' ? 'SELECT,INSERT,UPDATE,DELETE' : '';
      if (held.join(',') !== want) wrong.push(`${role}=${held.join(',')}`);
    }
    verdict(wrong.length === 0, `${db}: grants exactly api SELECT,INSERT,UPDATE,DELETE on ${TABLE}, nothing for collector, detector, trainer, llm`, JSON.stringify(wrong));

    // API statement shapes after 028 (services/userSettings.ts updateSettings, notificationReadModel.ts markNotificationsRead) through a real LOGIN role.
    const settings = `INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated,window_days,include_jobs)
      VALUES($1,$2,$3,$4,coalesce($5::integer,$6::integer),coalesce($7::boolean,$8::boolean))
      ON CONFLICT (actor_id) DO UPDATE SET decision_basis=EXCLUDED.decision_basis,min_risk_level=EXCLUDED.min_risk_level,
        include_unrated=EXCLUDED.include_unrated,window_days=coalesce($5::integer,ops_user_notification_settings.window_days),
        include_jobs=coalesce($7::boolean,ops_user_notification_settings.include_jobs),updated_at=clock_timestamp()`;
    const includeJobs = async (actor) => (await api.query('SELECT include_jobs FROM ops_user_notification_settings WHERE actor_id=$1', [actor])).rows[0]?.include_jobs;
    await api.query(settings, ['qa-old', 'interim', 'info', true, null, 7, false, true]);
    verdict(await includeJobs('qa-old') === false, `${db}: api login stores include_jobs false`);
    await api.query(settings, ['qa-old', 'interim', 'info', true, null, 7, null, true]);
    verdict(await includeJobs('qa-old') === false, `${db}: upsert without includeJobs keeps the stored value`);
    await api.query(settings, ['qa-new', 'interim', 'info', true, null, 7, null, true]);
    verdict(await includeJobs('qa-new') === true, `${db}: new row without includeJobs gets true`);
    await api.query(settingsBefore, ['qa-old', 'final_complete', 'critical', true, null, 7]);
    verdict(await includeJobs('qa-old') === false, `${db}: pre-028 API upsert leaves include_jobs unchanged (mixed versions)`);

    // Jobs the notifications read: v03 training queue train/compare. The api reads these run_jobs columns (job list grant).
    const jobRead = await api.query(`SELECT j.id,j.kind::text,j.status::text,j.error_code,j.finished_at,j.result_config_version_id,j.result_summary
      FROM run_jobs j WHERE j.contract_version='v03' AND j.queue='training' AND j.status IN ('succeeded','failed') AND j.kind IN ('train','compare')
        AND j.finished_at IS NOT NULL AND j.finished_at>=now()-make_interval(days=>7)`);
    verdict(Array.isArray(jobRead.rows), `${db}: api login reads the run_jobs columns used by job notifications`);

    const cursor = `INSERT INTO ops_user_notification_cursors(actor_id,read_through,jobs_read_through) VALUES($1,$2,$3::timestamptz)
      ON CONFLICT (actor_id) DO UPDATE SET read_through=GREATEST(ops_user_notification_cursors.read_through,EXCLUDED.read_through),
        jobs_read_through=GREATEST(ops_user_notification_cursors.jobs_read_through,EXCLUDED.jobs_read_through),updated_at=clock_timestamp()`;
    const boundary = async () => (await api.query(`SELECT to_char(jobs_read_through AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS at
      FROM ops_user_notification_cursors WHERE actor_id='qa-old'`)).rows[0].at;
    await api.query(cursor, ['qa-old', 40, '2026-11-14T01:00:00.123456Z']);
    verdict(await boundary() === '2026-11-14T01:00:00.123456Z', `${db}: cursor upsert sets jobs_read_through from NULL (microseconds kept)`);
    await api.query(cursor, ['qa-old', 40, '2026-11-13T00:00:00.000000Z']);
    await api.query(cursor, ['qa-old', 40, null]);
    verdict(await boundary() === '2026-11-14T01:00:00.123456Z', `${db}: GREATEST never moves jobs_read_through back, NULL keeps it`);

    const mark = `INSERT INTO ${TABLE}(actor_id,job_id,read_through) VALUES($1,$2::uuid,$3::timestamptz)
      ON CONFLICT (actor_id,job_id) DO UPDATE SET read_through=GREATEST(${TABLE}.read_through,EXCLUDED.read_through),read_at=clock_timestamp()`;
    const [jobA, jobB, jobC] = [randomUUID(), randomUUID(), randomUUID()];
    await api.query(mark, ['qa-old', jobA, '2026-11-14T02:00:00.000000Z']);
    await api.query(mark, ['qa-old', jobA, '2026-11-14T01:30:00.000000Z']);
    const markAt = (await api.query(`SELECT to_char(read_through AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS at FROM ${TABLE} WHERE job_id=$1`, [jobA])).rows[0].at;
    verdict(markAt === '2026-11-14T02:00:00.000000Z', `${db}: job read upsert with GREATEST keeps the later finish time`, markAt);
    await api.query(mark, ['qa-old', jobB, '2026-11-14T00:30:00.000000Z']); // at or below the boundary
    await api.query(mark, ['qa-old', jobC, new Date(Date.now() - 40 * 86_400_000).toISOString()]); // older than 30 days
    const pruned = await api.query(`DELETE FROM ${TABLE} WHERE actor_id=$1 AND (read_through<now()-make_interval(days=>$2::integer)
      OR read_through<=(SELECT jobs_read_through FROM ops_user_notification_cursors WHERE actor_id=$1))`, ['qa-old', 30]);
    const left = (await api.query(`SELECT job_id::text AS j FROM ${TABLE}`)).rows.map(r => r.j);
    verdict(pruned.rowCount === 2 && left.join() === jobA, `${db}: api login prunes job read marks at or below the boundary or older than 30 days`, `rowCount=${pruned.rowCount} left=${left}`);
    await rejected(api, `${db}: api login cannot TRUNCATE ${TABLE}`, '42501', `TRUNCATE ${TABLE}`);

    for (const role of ['detector', 'collector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read ${TABLE}`, '42501', `SELECT 1 FROM ${TABLE} LIMIT 1`);
      await rejected(other, `${db}: ${role} login cannot write ${TABLE}`, '42501', `INSERT INTO ${TABLE}(actor_id,job_id,read_through) VALUES('x',$1,clock_timestamp())`, [randomUUID()]);
    }

    // CHECK / NOT NULL boundaries (owner, so only the constraint decides).
    const insert = `INSERT INTO ${TABLE}(actor_id,job_id,read_through) VALUES($1,$2,$3)`;
    await rejected(c, `${db}: empty actor_id`, '23514', insert, ['', randomUUID(), '2026-11-14T00:00:00Z']);
    await rejected(c, `${db}: actor_id longer than 128`, '23514', insert, ['a'.repeat(129), randomUUID(), '2026-11-14T00:00:00Z']);
    await rejected(c, `${db}: job_id NULL`, '23502', insert, ['qa-2', null, '2026-11-14T00:00:00Z']);
    await rejected(c, `${db}: read_through NULL`, '23502', insert, ['qa-2', randomUUID(), null]);
    await rejected(c, `${db}: include_jobs NULL`, '23502', `UPDATE ops_user_notification_settings SET include_jobs=NULL WHERE actor_id='qa-old'`);

    // Re-running 028 fails on the existing column and changes nothing (single transaction).
    const before = (await c.query(`SELECT count(*)::int AS n FROM ${TABLE}`)).rows[0].n;
    await rejected(c, `${db}: re-running 028 fails as a whole`, '42701', migrations[idx]);
    await c.query('ROLLBACK').catch(() => {});
    verdict((await c.query(`SELECT count(*)::int AS n FROM ${TABLE}`)).rows[0].n === before, `${db}: failed re-run leaves the table and rows as they were`);

    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally {
    for (const a of opened) await a.end().catch(() => {});
    await c.end();
  }
}
