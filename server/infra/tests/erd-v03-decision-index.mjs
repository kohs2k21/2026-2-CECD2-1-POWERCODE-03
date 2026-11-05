// 018 (#129): index for the latest decision of an observation, called from erd-v03.mjs inside the same disposable cluster.
// Databases: erd129_fresh (001-018 in one string each, index definition, DBML, runtime list), erd129_upgrade (001-017 + existing rows -> 018 as psql -f sends it, rows and catalog kept,
// real report SQL uses the index on both config paths and returns the same rows as a scan without indexes, re-run refused), erd129_lock (018 as one string fails on a held write lock, nothing stays, then applies).
// The report SQL is read from the backend source, so a query change that stops using the index fails here.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { catalog, delta, statements } from './erd-v03-detect-v04.mjs';

const TABLE = 'run_process_decisions';
const INDEX = 'run_process_decisions_observation_latest_idx';
const DEFINITION = `CREATE INDEX ${INDEX} ON public.${TABLE} USING btree (process_observation_id, decided_at DESC, id DESC)`;
const ROWS = 2000;
const HOUR = 3600000;

// Template constants of operationalReport.ts, ${NAME} resolved in file order. Same text the API sends.
const reportQueries = async infra => {
  const source = await readFile(join(infra, '../backend/src/services/operationalReport.ts'), 'utf8');
  const out = {};
  for (const [, name, text] of source.matchAll(/export const ([A-Z_]+) = `([\s\S]*?)`;/g)) {
    out[name] = text.replace(/\$\{([A-Z_]+)\}/g, (_, key) => { assert(key in out, `${key} is defined before ${name}`); return out[key]; });
  }
  for (const name of ['SCOPED_OBSERVATIONS','OPERATIONAL_SQL','COHORT_SUMMARY_SQL','DETECTOR_COST_SQL']) assert(out[name], `${name} found in operationalReport.ts`);
  return out;
};

export async function decisionIndex018({ connection, check, failures, migrations, migrationNames, parity, insert, infra, model }) {
  const idx = migrationNames.findIndex(n => n.startsWith('018_'));
  assert(idx > 0, '018 migration must be present');
  const name = migrationNames[idx];
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const open = async database => { const c = connection(database); await c.connect(); return c; };
  const upTo = async (c, end) => { for (let i = 0; i < end; i++) await c.query(migrations[i]); };
  const S = await reportQueries(infra);
  const indexRow = async db => (await db.query(`SELECT pg_get_indexdef(i.indexrelid) AS def,i.indisvalid,i.indisready,i.indisunique,i.indpred IS NULL AS whole_table
    FROM pg_index i WHERE i.indexrelid=to_regclass($1)`, [INDEX])).rows[0];

  const admin = connection('postgres'); await admin.connect();
  try { for (const database of ['erd129_fresh','erd129_upgrade','erd129_lock']) await admin.query(`CREATE DATABASE ${database}`); }
  finally { await admin.end(); }

  // Rows by plain SQL with triggers and FK checks off, CHECK constraints on. Mix per observation: none, one config, config + NULL config (v04), two configs + NULL config.
  // The last observation is `edge`: fixed times and ids that decide ties, the config filter and the cutoff.
  const population = async db => {
    const until = new Date(Date.now() + HOUR), from = new Date(Date.now() - 30 * 24 * HOUR), edgeTime = new Date(until.getTime() - 3 * HOUR);
    const source = randomUUID(), run = randomUUID(), configA = randomUUID(), configB = randomUUID();
    await db.query('SET session_replication_role=replica');
    await db.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
      VALUES($1,$2,$2,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["P"]}',clock_timestamp())`, [source, `qa129-${source}`]);
    await db.query(`INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status) VALUES($1,$2,'api',NULL,'{}','running')`, [run, source]);
    for (const config of [configA, configB]) await db.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,'{}','{}',$3,'qa129',clock_timestamp())`, [config, source, `hash-${config}`]);
    await db.query(`CREATE TEMP TABLE qa129_obs AS SELECT g,gen_random_uuid() AS obs,gen_random_uuid() AS execution,$2::timestamptz+g*interval '10 minutes' AS at FROM generate_series(1,$1::int) g`, [ROWS, from]);
    await db.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at) SELECT execution,$1,'P','qa129-'||g,'{}','v1',at FROM qa129_obs`, [source]);
    await db.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
      SELECT obs,$1,'P',execution,1,'qa129-'||g,md5(g::text),at,at,'ok','{}','{}','[]','[]' FROM qa129_obs`, [source]);
    await db.query(`INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace) SELECT obs,$1,'api',g,'qa129' FROM qa129_obs`, [run]);
    const decisions = (config, where, minutes) => db.query(`INSERT INTO ${TABLE}(id,execution_id,process_observation_id,config_version_id,input_manifest_id,input_manifest_hash,job_id,result_attempt_id,result_lease_token,
        status,rule_status,ml_status,evidence,decided_at,latency_status)
      SELECT gen_random_uuid(),execution,obs,$1::uuid,${config ? 'NULL::uuid' : 'gen_random_uuid()'},md5(g::text||$2::text),${config ? 'NULL::uuid,NULL::uuid,NULL::uuid' : 'gen_random_uuid(),gen_random_uuid(),gen_random_uuid()'},
        'complete','succeeded','succeeded',jsonb_build_object('schemaVersion',4,'anomalous',g%10=0),at+make_interval(mins=>$2::int),'unavailable' FROM qa129_obs WHERE ${where}`, [config, minutes]);
    await decisions(configA, 'g%4 IN (1,2,3)', 1);
    await decisions(configB, 'g%4=3', 2);
    await decisions(null, 'g%4 IN (0,2,3)', 3);
    const { rows:[edge] } = await db.query('SELECT obs,execution FROM qa129_obs WHERE g=$1', [ROWS]);
    const one = (id, config, minutes, hash) => db.query(`INSERT INTO ${TABLE}(id,execution_id,process_observation_id,config_version_id,input_manifest_id,input_manifest_hash,job_id,result_attempt_id,result_lease_token,
        status,rule_status,ml_status,evidence,decided_at,latency_status)
      VALUES($1,$2,$3,$4::uuid,$5::uuid,$6,$7::uuid,$8::uuid,$9::uuid,'complete','succeeded','succeeded','{"schemaVersion":4,"anomalous":false}',$10::timestamptz+make_interval(mins=>$11::int),'unavailable')`,
    [id, edge.execution, edge.obs, config, config ? null : randomUUID(), hash, config ? null : randomUUID(), config ? null : randomUUID(), config ? null : randomUUID(), edgeTime, minutes]);
    const ids = { tieLow:'00000000-0000-4000-8000-000000000001', tieHigh:'ffffffff-ffff-4fff-8fff-ffffffffffff', otherConfig:randomUUID(), noConfig:randomUUID(), afterCutoff:randomUUID() };
    await db.query(`DELETE FROM ${TABLE} WHERE process_observation_id=$1`, [edge.obs]);
    await one(ids.tieLow, configA, 0, 'tie-low');
    await one(ids.tieHigh, configA, 0, 'tie-high');
    await one(ids.otherConfig, configB, 60, 'other-config');
    await one(ids.noConfig, null, 120, 'no-config');
    await one(ids.afterCutoff, configA, 240, 'after-cutoff');
    await db.query('DROP TABLE qa129_obs');
    await db.query('SET session_replication_role=origin');
    await db.query('VACUUM ANALYZE');
    const parameters = config => [source, config, from.toISOString(), until.toISOString(), 'Asia/Seoul', 'live', 31, 0];
    return { source, configA, configB, edge, ids, parameters };
  };
  const paths = w => ({ 'config': w.configA, 'NULL config (v04)': null });
  const argumentsOf = {
    OPERATIONAL_SQL: p => p,
    COHORT_SUMMARY_SQL: p => p.slice(0, 6),
    DETECTOR_COST_SQL: p => [...p.slice(0, 6), ['totalMs']],
  };
  const explain = async (db, query, args) => (await db.query(`EXPLAIN ${S[query]}`, args)).rows.map(r => r['QUERY PLAN']);
  const latest = async (db, parameters) => (await db.query(`${S.SCOPED_OBSERVATIONS} SELECT id,decision_id FROM latest ORDER BY id`, parameters.slice(0, 6))).rows;
  const withoutIndexes = async (db, run) => {
    await db.query('BEGIN');
    try {
      for (const setting of ['enable_indexscan','enable_indexonlyscan','enable_bitmapscan']) await db.query(`SET LOCAL ${setting}=off`);
      return await run();
    } finally { await db.query('ROLLBACK'); }
  };

  // Fresh: all files in file order, one string each, the shape of prepare-runtime-db.py.
  {
    const c = await open('erd129_fresh');
    try {
      for (const sql of migrations) await c.query(sql);
      check(`erd129_fresh: 001 through ${migrationNames.at(-1)} apply, each file as one string`);
      const row = await indexRow(c);
      assert.equal(row?.def, DEFINITION, '018 index definition');
      verdict(row.indisvalid && row.indisready && !row.indisunique && row.whole_table, 'erd129_fresh: index is valid, ready, not unique and covers every row', JSON.stringify(row));
      await parity(c);
      const table = model.schemas.flatMap(s => s.tables).find(t => t.name === TABLE);
      const entry = table.indexes.find(i => i.name === INDEX);
      assert.deepEqual(entry?.columns.map(col => col.value), ['process_observation_id','decided_at','id'], 'DBML lists the index columns in order');
      verdict(/decided_at DESC, id DESC/.test(entry.note?.value ?? entry.note ?? ''), 'erd129_fresh: DBML index note states the DESC columns', JSON.stringify(entry.note));
      const runtimeList = await readFile(join(infra, 'tests/prepare-runtime-db.py'), 'utf8');
      const missing = migrationNames.filter(n => !runtimeList.includes(`'${n}'`));
      verdict(missing.length === 0, 'erd129_fresh: prepare-runtime-db.py lists every migration file', missing.join(', '));
    } finally { await c.end(); }
  }

  // Existing rows: 001-017, rows, then 018 statement by statement as psql -f sends it.
  {
    const u = await open('erd129_upgrade');
    try {
      await upTo(u, idx);
      const world = await population(u);
      const source = await insert(u, 'ops_sources', { dataset_key:`qa129-trigger-${randomUUID()}` });
      const execution = await insert(u, 'data_executions', { source_id:source.id, layer:'P' });
      const observation = await insert(u, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1 });
      await insert(u, 'data_processes', { observation_id:observation.id });
      const config = await insert(u, 'ml_config_versions', { source_id:source.id });
      const written = await insert(u, TABLE, { execution_id:execution.id, process_observation_id:observation.id, config_version_id:config.id, status:'complete', rule_status:'succeeded', ml_status:'succeeded',
        latency_status:'unavailable', source_latency_ms:null, evidence:{ anomalous:false }, decided_at:new Date() });
      check('erd129_upgrade: a decision written through the triggers exists before 018');
      const rowsOf = async () => Object.fromEntries(await Promise.all([TABLE,'run_decision_events'].map(async t => [t, (await u.query(`SELECT row_to_json(x)::text AS r FROM ${t} x ORDER BY 1`)).rows.map(r => r.r)])));
      const rows = await rowsOf(), before = await catalog(u);
      assert(rows[TABLE].length > ROWS, 'the population is there');
      for (const [path, config] of Object.entries(paths(world))) {
        const plan = await explain(u, 'COHORT_SUMMARY_SQL', argumentsOf.COHORT_SUMMARY_SQL(world.parameters(config)));
        verdict(!plan.join('\n').includes(INDEX), `erd129_upgrade: before 018 the ${path} report has no such index to use`);
      }
      const expected = await latest(u, world.parameters(null));

      const parts = statements(migrations[idx].replaceAll('\r\n', '\n'));
      assert.equal(parts.length, 1, '018 is one statement');
      verdict(parts.every(p => /^[\x00-\x7f]*$/.test(p)), '018 sends ASCII statements only (no client_encoding dependence)');
      for (const part of parts) await u.query(part);
      check(`erd129_upgrade: 001-017 + existing rows, then ${name} applies statement by statement`);
      assert.deepEqual(await rowsOf(), rows, 'rows written before 018 are kept unchanged');
      check(`erd129_upgrade: rows kept (${TABLE}, run_decision_events)`);
      const after = await catalog(u);
      assert.deepEqual(delta(before.indexes, after.indexes), { removed:[], added:[`${INDEX}:${DEFINITION}`] }, '018 adds one index');
      for (const key of ['enums','columns','constraints','triggers']) assert.deepEqual(after[key], before[key], `018 leaves ${key} unchanged`);
      assert.deepEqual(after.functions.map(f => [f.name, f.body]), before.functions.map(f => [f.name, f.body]), '018 leaves functions unchanged');
      check('erd129_upgrade: one index added; enums, columns, constraints, triggers and functions unchanged');
      await parity(u);

      await u.query('ANALYZE');
      for (const [query, args] of Object.entries(argumentsOf)) for (const [path, config] of Object.entries(paths(world))) {
        const plan = await explain(u, query, args(world.parameters(config)));
        // Small tables may take the bitmap form on the config path, so the bitmap index line counts as the access. Any other access fails: a sequential scan or the 4-column unique index.
        const access = plan.filter(line => line.includes(`Seq Scan on ${TABLE}`) || line.includes(`Bitmap Index Scan on ${TABLE}`) || (line.includes(' using ') && line.includes(` on ${TABLE} `)));
        verdict(access.length > 0 && access.every(line => line.includes(INDEX)), `erd129_upgrade: ${query} on the ${path} path reads the decisions through the index only`, access.join(' | '));
      }

      const pick = async config => (await latest(u, world.parameters(config))).find(r => r.id === world.edge.obs)?.decision_id;
      verdict(await pick(world.configA) === world.ids.tieHigh, 'erd129_upgrade: config path, same decided_at resolves to the larger id, a later decision past the cutoff is ignored');
      verdict(await pick(world.configB) === world.ids.otherConfig, 'erd129_upgrade: config path skips newer decisions of another config');
      verdict(await pick(null) === world.ids.noConfig, 'erd129_upgrade: NULL config path takes the newest decision of any config');
      assert.deepEqual(await latest(u, world.parameters(null)), expected, 'NULL config path: same rows before and after 018');
      for (const [path, config] of Object.entries(paths(world))) {
        const args = world.parameters(config);
        const indexed = await u.query(S.OPERATIONAL_SQL, args);
        const scanned = await withoutIndexes(u, () => u.query(S.OPERATIONAL_SQL, args));
        const plain = rows => JSON.stringify(rows.map(({ lastProcessedAt, ...rest }) => ({ ...rest, lastProcessedAt:lastProcessedAt?.toISOString() ?? null })));
        verdict(indexed.rows.length > 0 && plain(indexed.rows) === plain(scanned.rows), `erd129_upgrade: OPERATIONAL_SQL on the ${path} path returns the same rows as a scan without indexes`);
      }

      // The index sees a decision written after 018.
      const newer = await insert(u, TABLE, { execution_id:execution.id, process_observation_id:observation.id, config_version_id:config.id, input_manifest_hash:'after-018', status:'complete', rule_status:'succeeded',
        ml_status:'succeeded', latency_status:'unavailable', source_latency_ms:null, evidence:{ anomalous:false }, decided_at:new Date(written.decided_at.getTime() + 1000) });
      const top = (await u.query(`SELECT id FROM ${TABLE} WHERE process_observation_id=$1 ORDER BY decided_at DESC,id DESC LIMIT 1`, [observation.id])).rows[0];
      verdict(top.id === newer.id, 'erd129_upgrade: a decision written through the triggers after 018 is the latest of its observation');

      let again = null;
      try { await u.query(migrations[idx]); } catch (e) { again = e; }
      verdict(again?.code === '42P07', 'erd129_upgrade: re-running 018 stops with 42P07 (one-time migration, no IF NOT EXISTS over a different definition)', again?.message);
      assert.equal((await indexRow(u)).def, DEFINITION, 'the index survives the refused re-run');
    } finally { await u.end(); }
  }

  // Failure: one statement, so there is no half state. A writer holding the table stops the build, nothing stays, and the same file then applies.
  {
    const c = await open('erd129_lock'), writer = await open('erd129_lock');
    try {
      await upTo(c, idx);
      const before = await catalog(c);
      await writer.query('BEGIN');
      await writer.query(`LOCK TABLE ${TABLE} IN ROW EXCLUSIVE MODE`);
      await c.query(`SET lock_timeout='300ms'`);
      let error = null;
      try { await c.query(migrations[idx]); } catch (e) { error = e; }
      verdict(error?.code === '55P03', 'erd129_lock: 018 waits for an open writer transaction and stops on lock_timeout (55P03)', error?.message);
      assert.deepEqual(await catalog(c), before, 'failed 018 leaves the catalog unchanged');
      assert.equal(await indexRow(c), undefined, 'failed 018 leaves no index, valid or not');
      check('erd129_lock: failed 018 leaves no index and no other catalog change');
      await c.query('RESET lock_timeout');
      await writer.query('ROLLBACK');
      await c.query(migrations[idx]);
      const row = await indexRow(c);
      verdict(row?.def === DEFINITION && row.indisvalid, 'erd129_lock: 018 applies after the writer ended', JSON.stringify(row));
      for (let i = idx + 1; i < migrations.length; i++) await c.query(migrations[i]);
      await parity(c);
    } finally { await writer.end(); await c.end(); }
  }
}
