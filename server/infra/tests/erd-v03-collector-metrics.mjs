// 030: Collector operations metrics (contracts/collector-metrics.md), called from erd-v03.mjs inside the same disposable cluster.
// One database erd030_upgrade: migrations before 030 + roles script as of 029 + rows written with the pre-030 Collector statements,
// then 030: existing rows kept (api_received_at value, ingest_phase NULL), api_received_at nullable, partial index on
// (source_id, source_log_occurred_at) with its predicate, ingest_phase CHECK NOT VALID that still checks new rows, the new
// resource table with per-metric status CHECKs, the pre-030 INSERT statements still work, roles re-apply gives collector
// SELECT/INSERT/DELETE (no UPDATE) and api SELECT only, collector can UPDATE ops_sources.clock_basis (operator CLI),
// a second run fails and leaves the catalog unchanged, remaining migrations, DBML parity. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const INDEX = 'data_observations_source_occurred_idx';
const PHASE_CHECK = 'data_input_origins_ingest_phase_check';
const TABLE = 'ops_resource_measurements';

export async function collectorMetrics030({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('030_'));
  assert(idx > 0, '030 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd030_upgrade';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const login = async (role) => {
    const user = `qa030_${role}`, password = randomUUID();
    await c.query(`CREATE ROLE ${user} LOGIN PASSWORD '${password}' IN ROLE powercode_${role}`);
    const client = new Client({ host:'127.0.0.1', port, user, password, database:db });
    await client.connect();
    logins[role] = client;
    return client;
  };
  try {
    for (const sql of migrations.slice(0, idx)) await c.query(sql);
    const rolesTo029 = rolesSql.replace(/^-- 030: [\s\S]*?(?=^-- #78-Q10)/m, '');
    assert.notEqual(rolesTo029, rolesSql, 'database-roles.sql has the 030 grant block before #78-Q10');
    await c.query(rolesTo029);
    const collector = await login('collector');
    const api = await login('api');
    // Pre-030 Collector statements (collector/storage.py ingest_observation and input_store.admit before 030).
    const source = await insert(c, 'ops_sources', { dataset_key:`qa030-${randomUUID()}`, clock_basis:{ status:'unknown' } });
    const run = randomUUID();
    await c.query(`INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status) VALUES($1,$2,'api',NULL,'{}','running')`, [run, source.id]);
    const oldObservation = async () => {
      const id = randomUUID();
      await collector.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
        source_revision_token,api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
        VALUES($1,$2,'P',NULL,NULL,$3,'h',NULL,clock_timestamp(),clock_timestamp(),'unknown','{}','{}','[]','[]')`, [id, source.id, `qa030:${id}`]);
      await collector.query(`INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace) VALUES($1,$2,'api',NULL,'api')`, [id, run]);
      return id;
    };
    const kept = await oldObservation();
    check(`${db}: ${migrationNames[idx - 1]} state with rows written by the pre-030 Collector statements`);

    await c.query(migrations[idx]);
    const old = (await c.query(`SELECT o.api_received_at IS NOT NULL AS received,o.clock_status,o.source_log_occurred_at,i.ingest_phase
      FROM data_observations o JOIN data_input_origins i ON i.observation_id=o.id WHERE o.id=$1`, [kept])).rows[0];
    verdict(old.received && old.clock_status === 'unknown' && old.source_log_occurred_at === null && old.ingest_phase === null,
      `${db}: 030 keeps existing rows (api_received_at value, clock_status unknown, ingest_phase NULL; no backfill)`, JSON.stringify(old));
    const nullable = (await c.query(`SELECT is_nullable FROM information_schema.columns WHERE table_name='data_observations' AND column_name='api_received_at'`)).rows[0];
    verdict(nullable.is_nullable === 'YES', `${db}: api_received_at is nullable`);
    const index = (await c.query(`SELECT pg_get_indexdef(i.indexrelid) AS def,pg_get_expr(i.indpred,i.indrelid) AS pred,i.indisvalid
      FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid WHERE x.relname=$1`, [INDEX])).rows[0];
    verdict(index && index.indisvalid && /\(source_id, source_log_occurred_at\)/.test(index.def) && index.pred === '(source_log_occurred_at IS NOT NULL)',
      `${db}: valid partial index ${INDEX} on (source_id, source_log_occurred_at) WHERE NOT NULL`, JSON.stringify(index));
    const phase = (await c.query(`SELECT convalidated,pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname=$1`, [PHASE_CHECK])).rows[0];
    verdict(phase && phase.convalidated === false && ['live','initial','replay'].every(v => phase.def.includes(`'${v}'`)),
      `${db}: ${PHASE_CHECK} is NOT VALID and lists live/initial/replay`, JSON.stringify(phase));

    // New Collector statements (roles script not yet re-applied: 002/009 table grants cover the new column).
    const newObservation = async (mode, ingestPhase, received) => {
      const id = randomUUID();
      await collector.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
        source_revision_token,source_log_time_raw,source_log_occurred_at,api_received_at,persisted_at,clock_status,parsed_times,
        allowed_values,quality_flags,missing_fields) VALUES($1,$2,'P',NULL,NULL,$3,'h',NULL,'2026-11-15 10:00:00',
        '2026-11-15T01:00:00Z',$4,clock_timestamp(),'assumed_tz','{"rule":"source-time-v1","field":"end","basis":"assumed_tz"}','{}','[]','[]')`,
        [id, source.id, `qa030:${id}`, received]);
      await collector.query(`INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace,ingest_phase) VALUES($1,$2,$3,NULL,'api',$4)`,
        [id, run, mode, ingestPhase]);
      return id;
    };
    for (const value of ['initial', 'live']) {
      await newObservation('api', value, new Date());
      check(`${db}: collector login (no roles re-apply) writes an api origin with ingest_phase ${value}`);
    }
    await collector.query('BEGIN');
    try { await newObservation('api', null, null); check(`${db}: NULL api_received_at and NULL ingest_phase accepted (pre-030 shape)`); }
    finally { await collector.query('ROLLBACK'); }
    await oldObservation();
    check(`${db}: the pre-030 Collector INSERT statements still work (mixed versions)`);
    for (const [mode, value] of [['api', 'replay'], ['api', 'Live'], ['api', ''], ['csv', 'live'], ['csv', 'initial']]) {
      const id = randomUUID();
      await collector.query('BEGIN');
      await collector.query(`INSERT INTO data_observations(id,source_id,layer,dedup_key,content_hash,persisted_at,clock_status,parsed_times,
        allowed_values,quality_flags,missing_fields) VALUES($1,$2,'P',$3,'h',clock_timestamp(),'absent','{}','{}','[]','[]')`, [id, source.id, `qa030:${id}`]);
      await rejected(collector, `${db}: ingest_phase '${value}' for mode ${mode}`, '23514',
        `INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace,ingest_phase) VALUES($1,$2,$3,NULL,'x',$4)`, [id, run, mode, value]);
      await collector.query('ROLLBACK');
    }

    // Resource table before the roles script is re-applied: no runtime role can touch it.
    await rejected(collector, `${db}: collector login cannot read ${TABLE} before roles re-apply`, '42501', `SELECT 1 FROM ${TABLE}`);
    await c.query(rolesSql);
    const privileges = {};
    for (const role of ['api', 'collector', 'detector', 'trainer', 'llm']) {
      privileges[role] = {};
      for (const table of [TABLE, 'ops_storage_measurements']) {
        privileges[role][table] = [];
        for (const p of ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'])
          if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, table, p])).rows[0].ok) privileges[role][table].push(p);
      }
    }
    const expected = { api:{ [TABLE]:['SELECT'], ops_storage_measurements:['SELECT'] }, collector:{ [TABLE]:['SELECT','INSERT','DELETE'], ops_storage_measurements:['SELECT','INSERT','DELETE'] },
      detector:{ [TABLE]:[], ops_storage_measurements:[] }, trainer:{ [TABLE]:[], ops_storage_measurements:[] }, llm:{ [TABLE]:[], ops_storage_measurements:[] } };
    verdict(JSON.stringify(privileges) === JSON.stringify(expected), `${db}: roles re-apply gives collector SELECT/INSERT/DELETE and api SELECT on the measurement tables only`, JSON.stringify(privileges));

    const resource = `INSERT INTO ${TABLE}(id,scope,cpu_status,cpu_percent,cpu_reason_code,sample_interval_ms,memory_status,memory_used_bytes,
      memory_total_bytes,memory_reason_code) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING measured_at`;
    const good = [randomUUID(), 'host_proc_unverified', 'measured', 12.5, null, 60000, 'measured', 10, 100, null];
    const saved = (await collector.query(resource, good)).rows[0];
    verdict(saved.measured_at instanceof Date, `${db}: collector inserts a measured resource row (measured_at from the DB clock)`);
    await collector.query(resource, [randomUUID(), 'host_proc_unverified', 'unavailable', null, 'cpu_first_sample', null, 'measured', 10, 100, null]);
    check(`${db}: CPU unavailable with reason while memory is measured`);
    const bad = [
      ['scope outside the list', { 1:'container' }], ['cpu measured without value', { 3:null }], ['cpu measured without interval', { 5:null }],
      ['cpu measured with reason', { 4:'x' }], ['cpu unavailable without reason', { 2:'unavailable', 3:null, 5:null }], ['cpu over 100', { 3:100.1 }],
      ['cpu negative', { 3:-0.1 }], ['memory used over total', { 7:101 }], ['memory unavailable without reason', { 6:'unavailable', 7:null, 8:null }],
      ['memory measured with reason', { 9:'x' }], ['memory total zero', { 8:0, 7:0 }],
    ];
    for (const [label, changes] of bad) {
      const values = good.map((v, i) => (i in changes ? changes[i] : i === 0 ? randomUUID() : v));
      await rejected(collector, `${db}: resource row rejected — ${label}`, '23514', resource, values);
    }
    await rejected(collector, `${db}: collector cannot UPDATE resource rows`, '42501', `UPDATE ${TABLE} SET scope='host'`);
    const removed = await collector.query(`DELETE FROM ${TABLE} WHERE measured_at<clock_timestamp()-interval '24 hours'`);
    verdict(removed.rowCount === 0, `${db}: collector retention DELETE runs (nothing old to delete)`);
    const storage = (await collector.query(`INSERT INTO ops_storage_measurements(id,storage_key,filesystem_key,target_kind,status,capacity_bytes,used_bytes,
      available_bytes,database_bytes,writer_access_verified,warning_spec_version,warning_state,reason_code,measured_at)
      VALUES($1,'database_data',NULL,'filesystem','unavailable',NULL,NULL,NULL,NULL,false,'disk-warning-v1','unknown','mount_not_visible',clock_timestamp()) RETURNING id`, [randomUUID()])).rows[0];
    verdict(Boolean(storage.id), `${db}: collector writes an unavailable disk row to the existing storage table`);
    const read = (await api.query(`SELECT (SELECT count(*)::int FROM ${TABLE}) AS r,(SELECT count(*)::int FROM ops_storage_measurements) AS s`)).rows[0];
    verdict(read.r === 2 && read.s === 1, `${db}: api login reads both measurement tables`, JSON.stringify(read));
    await rejected(api, `${db}: api login cannot insert resource rows`, '42501', resource, [randomUUID(), ...good.slice(1)]);
    for (const role of ['detector', 'trainer', 'llm']) {
      const other = await login(role);
      await rejected(other, `${db}: ${role} login cannot read ${TABLE}`, '42501', `SELECT 1 FROM ${TABLE} LIMIT 1`);
    }
    // Operator CLI `clock-basis verify` runs as the collector role: ops_sources has no trigger and the 002 grant covers UPDATE.
    const basis = (await collector.query(`UPDATE ops_sources SET clock_basis=jsonb_build_object('status','verified','timezone','Asia/Seoul',
      'method','persist_lag_check','verifiedAt','2026-11-15T00:00:00.000000Z','verifiedBy','qa') WHERE id=$1 RETURNING clock_basis`, [source.id])).rows[0];
    verdict(basis?.clock_basis?.status === 'verified', `${db}: collector login updates ops_sources.clock_basis (no trigger, no migration needed)`);

    const catalog = async () => (await c.query(`SELECT (SELECT count(*)::int FROM pg_indexes WHERE indexname=$1) AS i,
      (SELECT count(*)::int FROM pg_constraint WHERE conname=$2) AS k,(SELECT count(*)::int FROM ${TABLE}) AS n`, [INDEX, PHASE_CHECK])).rows[0];
    const before = await catalog();
    await rejected(c, `${db}: running 030 again fails`, '42P07', migrations[idx]);
    await c.query('ROLLBACK').catch(() => {});
    const after = await catalog();
    verdict(JSON.stringify(before) === JSON.stringify(after) && after.i === 1 && after.k === 1, `${db}: failed re-run leaves the index, CHECK and rows unchanged`, JSON.stringify(after));

    for (const sql of migrations.slice(idx + 1)) await c.query(sql);
    await parity(c);
    check(`${db}: remaining migrations apply and DBML parity holds`);
  } finally {
    for (const client of Object.values(logins)) await client.end().catch(() => {});
    await c.end();
  }
}
