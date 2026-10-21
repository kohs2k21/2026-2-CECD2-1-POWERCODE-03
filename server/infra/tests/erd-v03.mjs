// Isolated real-PostgreSQL integration checks. No .env or project runtime imports.
// Run: node server/infra/tests/erd-v03.mjs <temporary-tool-workspace>
// Tools: embedded-postgres@17.10.0-beta.17, pg, @dbml/core.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, readdir, mkdtemp } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:net';

const toolRoot = process.argv[2];
assert(toolRoot, 'Pass an isolated temporary workspace containing the test dependencies');
const requireTool = createRequire(resolve(toolRoot, 'package.json'));
const baselineMetadata = process.argv[3] ? JSON.parse(await readFile(process.argv[3], 'utf8')) : null;
const EmbeddedPostgres = requireTool('embedded-postgres').default;
const { Client } = requireTool('pg');
const { Parser } = requireTool('@dbml/core');
const infra = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const migrationNames = (await readdir(join(infra, 'migrations'))).filter(n => /^\d+.*\.sql$/.test(n)).sort();
assert(migrationNames.length >= 3, 'v03 migration must be present before running');
const migrations = await Promise.all(migrationNames.map(n => readFile(join(infra, 'migrations', n), 'utf8')));
const idx011 = migrationNames.findIndex(n => n.startsWith('011_'));
assert(idx011 > 0, '011 migration must be present');
const rolesSql = await readFile(join(infra, 'scripts/database-roles.sql'), 'utf8');
const demoRoleSql = await readFile(join(infra, 'scripts/demo-role.sql'), 'utf8');
const v03Admin = {contract_version:'v03',status:'queued',producer_kind:'user',producer_key:'admin78',requested_by_actor_id:'admin78',
  requester_role:'admin',max_attempts:3,attempt_count:0};
const queueFor = kind => kind==='recommend_methods' ? 'llm_recommendation' : 'training';
const methodSpec = () => ({evidence_version:'config-draft-catalog-v1',input_evidence_hash:'a'.repeat(64),model_version:'methods_v1',
  prompt_version:'method-selection-v0.1',output_schema_version:'method-recommendation-v1',reuse_key:randomUUID()});
// Same statement shape as pg_queue.reconcile_dead_jobs for the training queue.
const reconcileTrainingQueue = `UPDATE run_jobs j SET status='failed',error_code='queue_exhausted',finished_at=clock_timestamp()
  WHERE j.status IN ('queued','running') AND EXISTS(SELECT 1 FROM powercode_queue.training_jobs q
    WHERE q.status='dead' AND q.payload->>'jobId'=j.id::text)
    AND NOT EXISTS(SELECT 1 FROM run_job_attempts a WHERE a.id=j.current_attempt_id
      AND a.status='running' AND a.lease_until>clock_timestamp())`;
const model = Parser.parse(await readFile(join(infra, 'database/schema.dbml'), 'utf8'), 'dbmlv2').export();
console.log('PASS DBML parses without source normalization');
const port = await new Promise((ok, fail) => {
  const s = createServer(); s.on('error', fail); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => ok(p)); });
});
const databaseDir = await mkdtemp(join(tmpdir(), 'powercode-erd-v03-'));
const cluster = new EmbeddedPostgres({ databaseDir, port, user: 'erd_test', password: 'isolated-test-only', persistent: true,
  postgresFlags: ['-h', '127.0.0.1'], onLog: () => {}, onError: () => {} });
let checkCount = 0;
const failures = [];
const check = (label) => { checkCount++; console.log(`PASS ${label}`); };
const quote = s => '"' + s.replaceAll('"', '""') + '"';
const connection = database => new Client({ host: '127.0.0.1', port, user: 'erd_test', password: 'isolated-test-only', database });

async function insert(c, table, values = {}) {
  const columns = (await c.query(`SELECT column_name, udt_name, is_nullable, column_default, is_identity FROM information_schema.columns
    WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position`, [table])).rows;
  assert(columns.length, `Missing table ${table}`);
  const row = { ...values };
  for (const col of columns) {
    if (col.column_name in row || col.is_nullable === 'YES' || col.column_default !== null || col.is_identity==='YES') continue;
    const enums = (await c.query('SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname=$1 ORDER BY e.enumsortorder', [col.udt_name])).rows;
    row[col.column_name] = enums.length ? enums[0].enumlabel : col.udt_name === 'uuid' ? randomUUID() :
      col.udt_name === 'jsonb' ? {} : col.udt_name === 'bool' ? false :
      ['int2', 'int4', 'int8', 'numeric'].includes(col.udt_name) ? 1 :
      col.udt_name === 'timestamptz' ? new Date() : `test-${col.column_name}-${randomUUID()}`;
  }
  const names = Object.keys(row);
  return (await c.query(`INSERT INTO ${quote(table)} (${names.map(quote).join(',')}) VALUES (${names.map((_,i)=>'$'+(i+1)).join(',')}) RETURNING *`, names.map(n=>columns.find(col=>col.column_name===n)?.udt_name==='jsonb' && row[n]!==null ? JSON.stringify(row[n]) : row[n]))).rows[0];
}

async function rejected(c, label, action, codes = ['23514','23503','23505','23502','P0001','P0002']) {
  await c.query('SAVEPOINT negative_case');
  let accepted = false;
  try { await action(); accepted = true; }
  catch (e) { assert(codes.includes(e.code), `${label}: ${e.message} (${e.code})`); }
  finally { await c.query('ROLLBACK TO SAVEPOINT negative_case'); await c.query('RELEASE SAVEPOINT negative_case'); }
  if (accepted) { failures.push(label); console.log(`FAIL unexpected acceptance: ${label}`); }
  else check(label);
}

async function oldFixture(c) {
  const source = await insert(c, 'ops_sources', { dataset_key:`legacy-fixture-${randomUUID()}` });
  const execution = await insert(c, 'data_executions', { source_id:source.id, layer:'P' });
  const observation = await insert(c, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1 });
  const process = await insert(c, 'data_processes', { observation_id:observation.id });
  const config = await insert(c, 'ml_config_versions', { source_id:source.id });
  const snapshot = await insert(c, 'data_snapshots', { source_id:source.id, status:'building' });
  const job = await insert(c, 'run_jobs', { kind:'train', status:'queued', requested_by_actor_id:'legacy-admin', requester_role:'admin',
    input_config_version_id:config.id, input_snapshot_id:snapshot.id, attempt_count:0, max_attempts:3 });
  const decision = await insert(c, 'run_process_decisions', { execution_id:execution.id, process_observation_id:process.observation_id,
    config_version_id:config.id, status:'unavailable', rule_status:'not_ready', ml_status:'not_ready', latency_status:'unavailable', source_latency_ms:null });
  await c.query(`INSERT INTO powercode_queue.detection_logs (id,payload) VALUES ($1,'{"legacy":true}')`, [randomUUID()]);
  const saved = {};
  for (const table of ['ops_sources','data_executions','data_observations','data_processes','ml_config_versions','data_snapshots','run_jobs','run_process_decisions','powercode_queue.detection_logs']) {
    saved[table] = (await c.query(`SELECT row_to_json(t) AS row FROM ${table} t`)).rows.map(r=>r.row);
  }
  return saved;
}

async function assertRetained(c, before) {
  for (const [table,oldRows] of Object.entries(before)) {
    const now=(await c.query(`SELECT row_to_json(t) AS row FROM ${table} t`)).rows.map(r=>r.row);
    for (const old of oldRows) assert(now.some(n=>Object.entries(old).every(([k,v])=>JSON.stringify(n[k])===JSON.stringify(v))),`${table} legacy row preserved`);
  }
}

// Rows that 001-010 writers can create (legacy/v03 evaluate, import_csv, finished LLM recommendation).
async function preV011Fixture(c) {
  const source=await insert(c,'ops_sources');
  const config=await insert(c,'ml_config_versions',{source_id:source.id});
  const snapshot=await insert(c,'data_snapshots',{source_id:source.id,status:'ready'});
  const evaluation={config_version_id:config.id,snapshot_id:snapshot.id,purpose:'validation',applicability:'valid',
    population_count:10,scored_count:9,unavailable_count:1,metrics:{recall:0.5},reason_codes:[]};
  const legacy=await insert(c,'run_jobs',{kind:'evaluate',status:'succeeded',requested_by_actor_id:'legacy-admin',requester_role:'admin',
    input_config_version_id:config.id,input_snapshot_id:snapshot.id,attempt_count:1,max_attempts:3});
  await insert(c,'run_evaluations',{...evaluation,job_id:legacy.id});
  const v03={contract_version:'v03',queue:'training',producer_kind:'user',producer_key:'admin-pre011',requested_by_actor_id:'admin-pre011',
    requester_role:'admin',max_attempts:3,attempt_count:0,status:'queued'};
  const evaluate=await insert(c,'run_jobs',{...v03,kind:'evaluate',idempotency_key:randomUUID(),input_config_version_id:config.id});
  await insert(c,'run_evaluations',{...evaluation,job_id:evaluate.id});
  await insert(c,'run_jobs',{...v03,kind:'import_csv',idempotency_key:randomUUID()});
  const spec={evidence_version:'ev-pre011',input_evidence_hash:'h',model_version:'m',prompt_version:'p',output_schema_version:'s',reuse_key:randomUUID()};
  const llm=await insert(c,'run_jobs',{...v03,kind:'recommend_rule',queue:'llm_recommendation',idempotency_key:randomUUID(),llm_spec:spec});
  const attempt=await insert(c,'run_job_attempts',{job_id:llm.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+600000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[attempt.id,llm.id]);
  await insert(c,'run_llm_results',{job_id:llm.id,kind:'recommend_rule',...spec,output:{rules:[]},created_attempt_id:attempt.id,lease_token:attempt.lease_token});
  await c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[llm.id]);
  const saved={};
  for (const table of ['run_jobs','run_job_attempts','run_evaluations','run_llm_results','ml_config_versions','data_snapshots']) {
    saved[table]=(await c.query(`SELECT row_to_json(t) AS row FROM ${table} t`)).rows.map(r=>r.row);
  }
  return saved;
}

async function parity(c) {
  const rows = (await c.query(`SELECT table_schema,table_name,column_name,udt_name,is_nullable FROM information_schema.columns
    WHERE table_schema IN ('public','powercode_queue') ORDER BY table_schema,table_name,ordinal_position`)).rows;
  const aliases = { bigint:'int8',integer:'int4',smallint:'int2',boolean:'bool',timestamptz:'timestamptz' };
  const expected = model.schemas.flatMap(s=>s.tables.map(t=>({s:s.name,t})));
  assert.deepEqual([...new Set(rows.map(r=>r.table_schema+'.'+r.table_name))].sort(), expected.map(({s,t})=>s+'.'+t.name).sort(), 'DBML/PG table parity');
  for (const {s,t} of expected) {
    const actual = rows.filter(r=>r.table_schema===s && r.table_name===t.name);
    const primaryFields = new Set((t.indexes || []).filter(i=>i.pk).flatMap(i=>i.columns.filter(col=>col.type==='column').map(col=>col.value)));
    assert.deepEqual(actual.map(r=>r.column_name).sort(),t.fields.map(f=>f.name).sort(),`${t.name} column parity`);
    for (const f of t.fields) {
      const a = actual.find(r=>r.column_name===f.name);
      assert.equal(a.udt_name,aliases[f.type.type_name] || f.type.type_name,`${t.name}.${f.name} type parity`);
      const required=Boolean(f.not_null || f.pk || primaryFields.has(f.name));
      assert.equal(a.is_nullable,required?'NO':'YES',`${t.name}.${f.name} nullable parity`);
    }
  }
  const pgEnums = (await c.query(`SELECT t.typname,e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid ORDER BY t.typname,e.enumsortorder`)).rows;
  for (const s of model.schemas) for (const e of s.enums) assert.deepEqual(pgEnums.filter(p=>p.typname===e.name).map(p=>p.enumlabel),e.values.map(v=>v.name),`${e.name} enum parity`);
  const actualRefs = (await c.query(`SELECT ns.nspname AS schema_name,rel.relname AS table_name,nt.nspname AS target_schema,rt.relname AS target_table,
    ARRAY(SELECT a.attname::text FROM unnest(co.conkey) WITH ORDINALITY k(n,pos) JOIN pg_attribute a ON a.attrelid=co.conrelid AND a.attnum=k.n ORDER BY k.pos) AS fields,
    ARRAY(SELECT a.attname::text FROM unnest(co.confkey) WITH ORDINALITY k(n,pos) JOIN pg_attribute a ON a.attrelid=co.confrelid AND a.attnum=k.n ORDER BY k.pos) AS target_fields
    FROM pg_constraint co JOIN pg_class rel ON rel.oid=co.conrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace
    JOIN pg_class rt ON rt.oid=co.confrelid JOIN pg_namespace nt ON nt.oid=rt.relnamespace WHERE co.contype='f' AND ns.nspname IN ('public','powercode_queue')`)).rows;
  const key = (s,t,fields,ts,tt,tf) => `${s}.${t}(${fields.join(',')})->${ts}.${tt}(${tf.join(',')})`;
  const expectedRefs = model.schemas.flatMap(s=>s.refs.map(r=>{
    // DBML one-to-one refs are authored parent - child; v2 optionality uses 0..1.
    const child = r.endpoints.find(e=>e.relation==='*') || r.endpoints[1];
    const target = r.endpoints.find(e=>e!==child);
    return key(child.schemaName||s.name,child.tableName,child.fieldNames,target.schemaName||s.name,target.tableName,target.fieldNames);
  }));
  assert.deepEqual([...new Set(actualRefs.map(r=>key(r.schema_name,r.table_name,r.fields,r.target_schema,r.target_table,r.target_fields)))].sort(),[...new Set(expectedRefs)].sort(),'DBML/PG foreign-key parity');
  check(`DBML/final PG tables, columns, types, enums, FK parity (${expected.length} tables)`);
}

try {
  await cluster.initialise(); await cluster.start();
  const admin=connection('postgres'); await admin.connect();
  console.log((await admin.query('SELECT version() AS version')).rows[0].version);
  for (const name of ['erd_fresh','erd_upgrade','erd_rollback_011','erd_search_path_011']) await admin.query(`CREATE DATABASE ${name}`);
  await admin.end();
  for (const name of ['erd_fresh','erd_upgrade']) {
    const c=connection(name); await c.connect();
    try {
      let before, before011;
      for (let i=0;i<migrations.length;i++) {
        // Upgrade path: rows written by 001-010 era writers exist before 011 runs.
        if (name==='erd_upgrade' && i===idx011) before011=await preV011Fixture(c);
        await c.query(migrations[i]);
        if (i===1 && baselineMetadata) await baselineParity(c);
        if (name==='erd_upgrade' && i===1) before=await oldFixture(c);
      }
      check(`${name}: 001 through ${migrationNames.at(-1)} apply`);
      if (before) { await assertRetained(c,before); check('upgrade retains legacy rows and original field values'); }
      if (before011) {
        await assertRetained(c,before011);
        const lineage=(await c.query('SELECT count(*)::int AS n FROM run_evaluations WHERE comparison_id IS NOT NULL OR evaluation_set_id IS NOT NULL')).rows[0].n;
        assert.equal(lineage,0,'pre-011 evaluations keep NULL comparison lineage');
        check('upgrade: evaluate/import_csv/LLM rows written before 011 retained with NULL comparison lineage');
        await c.query('BEGIN');
        const old=(await c.query("SELECT e.job_id,e.snapshot_id FROM run_evaluations e JOIN run_jobs j ON j.id=e.job_id WHERE j.contract_version='v03' AND j.kind='evaluate'")).rows[0];
        const otherConfig=await insert(c,'ml_config_versions',{source_id:(await c.query('SELECT source_id FROM data_snapshots WHERE id=$1',[old.snapshot_id])).rows[0].source_id});
        await rejected(c,'pre-011 evaluate job still owns exactly one evaluation row',()=>insert(c,'run_evaluations',{job_id:old.job_id,config_version_id:otherConfig.id,
          snapshot_id:old.snapshot_id,purpose:'validation',population_count:1,scored_count:1,unavailable_count:0}));
        await c.query('ROLLBACK');
      }
      await parity(c);
      // Behavioral scenarios run in a rollback-only transaction.
      await c.query('BEGIN');
      await behavioral(c, name);
      await behavioral011(c, name);
      await roles011(c, name);
      await c.query('ROLLBACK');
      await concurrency(c, name);
    } finally { await c.end(); }
  }
  await rollback011('erd_rollback_011');
  await searchPath011('erd_search_path_011');
  assert.equal(failures.length,0,failures.join('; '));
  console.log(`PASS ${checkCount} integration checks; no project runtime or customer data used`);
} finally { await cluster.stop(); }

async function behavioral(c, database) {
  const asOf = new Date('2026-10-12T00:00:00Z');
  const past = new Date('2026-10-11T23:00:00Z');
  const source = await insert(c,'ops_sources');
  const otherSource = await insert(c,'ops_sources');
  async function observation(layer, sourceId=source.id, fields={}) {
    const e=await insert(c,'data_executions',{source_id:sourceId,layer});
    return insert(c,'data_observations',{source_id:sourceId,layer,execution_id:e.id,revision_no:1,persisted_at:past,...fields});
  }
  const po=await observation('P');
  const to=await observation('T');
  const mo=await observation('M');
  await insert(c,'data_transactions',{observation_id:to.id,transaction_id:'tx-main'});
  await insert(c,'data_processes',{observation_id:po.id,transaction_observation_id:to.id,transaction_id:'tx-main',process_id:'process-main'});
  await insert(c,'data_messages',{observation_id:mo.id,process_observation_id:po.id,transaction_id:'tx-main',process_id:'process-main',message_id:'message-main'});
  const config=await insert(c,'ml_config_versions',{source_id:source.id});
  const otherConfig=await insert(c,'ml_config_versions',{source_id:otherSource.id});
  const bodyObs=await observation('B');
  const bodyFields={observation_id:bodyObs.id,source_id:source.id,message_id:'message-main',collection_status:'not_collected',
    field_states:{message_in:'unknown',message_out:'unknown',message_body:'unknown'},representation:'unknown'};
  const body=await insert(c,'data_message_bodies',bodyFields);
  check(`${database}: BODY stores independently of MESSAGE/PROCESS links`);
  const rejectedBodyObs=await observation('B');
  await rejected(c,'BODY source namespace mismatch',()=>insert(c,'data_message_bodies',{...bodyFields,observation_id:rejectedBodyObs.id,source_id:otherSource.id}));
  await rejected(c,'BODY incorrect layer rejected',()=>insert(c,'data_message_bodies',{...bodyFields,observation_id:mo.id}));
  await rejected(c,'BODY missing/empty not silently interchanged',()=>insert(c,'data_message_bodies',{...bodyFields,observation_id:rejectedBodyObs.id,message_body:''}));
  await rejected(c,'BODY immutable update',()=>c.query('UPDATE data_message_bodies SET message_id=$1 WHERE observation_id=$2',['changed',body.observation_id]));
  const linkFields={source_id:source.id,body_observation_id:body.observation_id,message_observation_id:mo.id,
    assessment_revision:1,status:'verified',candidate_execution_count:1,input_as_of:asOf};
  await insert(c,'data_message_body_links',linkFields);
  await rejected(c,'verified BODY link cannot use NULL candidate count',()=>insert(c,'data_message_body_links',{...linkFields,assessment_revision:2,candidate_execution_count:null}));
  await rejected(c,'ambiguous BODY link cannot choose arbitrary MESSAGE',()=>insert(c,'data_message_body_links',{...linkFields,assessment_revision:2,status:'ambiguous',candidate_execution_count:2}));
  const mf={source_id:source.id,execution_id:po.execution_id,process_observation_id:po.id,transaction_observation_id:to.id,
    message_observation_refs:[{observation_id:mo.id,revision_no:1}],readiness:{ready:true},input_as_of:asOf,contract_version:'v03'};
  const manifest=await insert(c,'run_input_manifests',mf);
  await rejected(c,'manifest duplicate hash within source',()=>insert(c,'run_input_manifests',{...mf,content_hash:manifest.content_hash}));
  await rejected(c,'manifest immutable member revision',()=>c.query('UPDATE run_input_manifests SET message_observation_refs=$1 WHERE id=$2',['[]',manifest.id]));
  await rejected(c,'manifest wrong MESSAGE revision',()=>insert(c,'run_input_manifests',{...mf,message_observation_refs:[{observation_id:mo.id,revision_no:2}]}));
  await rejected(c,'manifest duplicate MESSAGE reference',()=>insert(c,'run_input_manifests',{...mf,message_observation_refs:[{observation_id:mo.id,revision_no:1},{observation_id:mo.id,revision_no:1}]}));
  await rejected(c,'manifest future observation',()=>insert(c,'run_input_manifests',{...mf,input_as_of:new Date('2020-01-01Z')}));
  const foreignMo=await observation('M',otherSource.id);
  await insert(c,'data_messages',{observation_id:foreignMo.id,transaction_id:'tx-main',process_id:'process-main',message_id:'message-main'});
  await rejected(c,'BODY verified link cannot cross source namespace',()=>insert(c,'data_message_body_links',{...linkFields,assessment_revision:2,message_observation_id:foreignMo.id}));
  await rejected(c,'manifest cross-source MESSAGE',()=>insert(c,'run_input_manifests',{...mf,message_observation_refs:[{observation_id:foreignMo.id,revision_no:1}]}));
  const wrongMo=await observation('M');
  await insert(c,'data_messages',{observation_id:wrongMo.id,transaction_id:'tx-main',process_id:'other-process'});
  await rejected(c,'manifest same-source different PROCESS relation',()=>insert(c,'run_input_manifests',{...mf,message_observation_refs:[{observation_id:wrongMo.id,revision_no:1}]}));
  const wrongTo=await observation('T');
  await insert(c,'data_transactions',{observation_id:wrongTo.id,transaction_id:'other-tx'});
  await rejected(c,'manifest same-source different TRANSACTION relation',()=>insert(c,'run_input_manifests',{...mf,transaction_observation_id:wrongTo.id}));
  await rejected(c,'frozen manifest MESSAGE observation cannot be changed',()=>c.query('UPDATE data_observations SET revision_no=2 WHERE id=$1',[mo.id]));
  await rejected(c,'frozen manifest MESSAGE metadata cannot be changed',()=>c.query("UPDATE data_messages SET process_id='changed' WHERE observation_id=$1",[mo.id]));
  await rejected(c,'frozen manifest MESSAGE observation cannot be deleted',()=>c.query('DELETE FROM data_observations WHERE id=$1',[mo.id]));
  await rejected(c,'frozen manifest MESSAGE metadata cannot be deleted',()=>c.query('DELETE FROM data_messages WHERE observation_id=$1',[mo.id]));
  const jobBase={idempotency_key:randomUUID(),contract_version:'v03',kind:'detect',queue:'detection',status:'queued',producer_kind:'system',producer_key:'collector-test',
    requested_by_actor_id:null,requester_role:null,input_manifest_id:manifest.id,input_config_version_id:config.id,max_attempts:3,attempt_count:0};
  const job=await insert(c,'run_jobs',jobBase);
  await rejected(c,'NULL queue cannot bypass v03 contract',()=>insert(c,'run_jobs',{...jobBase,idempotency_key:randomUUID(),queue:null}));
  await rejected(c,'system job cannot fabricate administrator actor',()=>insert(c,'run_jobs',{...jobBase,idempotency_key:randomUUID(),requested_by_actor_id:'admin1',requester_role:'admin'}));
  await rejected(c,'detection producer idempotency duplicate',()=>insert(c,'run_jobs',{...jobBase,idempotency_key:job.idempotency_key,request_hash:'different-hash'}));
  await rejected(c,'detection fixed manifest/config duplicate',()=>insert(c,'run_jobs',jobBase));
  await rejected(c,'detection configuration source mismatch',()=>insert(c,'run_jobs',{...jobBase,input_config_version_id:otherConfig.id}));
  await rejected(c,'v03 contract cannot be downgraded to legacy NULL',()=>c.query('UPDATE run_jobs SET contract_version=NULL,queue=NULL,producer_kind=NULL,producer_key=NULL,input_manifest_id=NULL,requested_by_actor_id=$1,requester_role=$2 WHERE id=$3',['fake-admin','admin',job.id]));
  await rejected(c,'job configuration cannot change on retry',()=>c.query('UPDATE run_jobs SET input_config_version_id=$1 WHERE id=$2',[otherConfig.id,job.id]));
  const attempt=await insert(c,'run_job_attempts',{job_id:job.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+60000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[attempt.id,job.id]);
  const decisionFields={execution_id:po.execution_id,process_observation_id:po.id,transaction_observation_id:to.id,config_version_id:config.id,
    input_manifest_id:manifest.id,input_manifest_hash:manifest.content_hash,input_as_of:asOf,job_id:job.id,result_attempt_id:attempt.id,result_lease_token:attempt.lease_token,
    status:'unavailable',rule_status:'not_ready',ml_status:'not_ready',latency_status:'unavailable',source_latency_ms:null};
  await rejected(c,'decision wrong lease token',()=>insert(c,'run_process_decisions',{...decisionFields,result_lease_token:randomUUID()}));
  await rejected(c,'decision wrong config',()=>insert(c,'run_process_decisions',{...decisionFields,config_version_id:otherConfig.id}));
  await rejected(c,'decision wrong manifest hash',()=>insert(c,'run_process_decisions',{...decisionFields,input_manifest_hash:'wrong'}));
  await rejected(c,'decision TRANSACTION differs from frozen manifest',()=>insert(c,'run_process_decisions',{...decisionFields,transaction_observation_id:wrongTo.id}));
  await rejected(c,'decision as-of differs from frozen manifest',()=>insert(c,'run_process_decisions',{...decisionFields,input_as_of:past}));
  await c.query("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1",[attempt.id]);
  await rejected(c,'expired worker cannot save decision',()=>insert(c,'run_process_decisions',decisionFields));
  await c.query("UPDATE run_job_attempts SET lease_until=clock_timestamp()+interval '1 minute' WHERE id=$1",[attempt.id]);
  const currentAttempt=await insert(c,'run_job_attempts',{job_id:job.id,attempt_no:2,status:'running',lease_until:new Date(Date.now()+60000)});
  await c.query('UPDATE run_jobs SET current_attempt_id=$1,attempt_count=2 WHERE id=$2',[currentAttempt.id,job.id]);
  await rejected(c,'reclaimed job rejects stale attempt',()=>insert(c,'run_process_decisions',decisionFields));
  await rejected(c,'job success requires durable matching decision',()=>c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[job.id]));
  const decision=await insert(c,'run_process_decisions',{...decisionFields,result_attempt_id:currentAttempt.id,result_lease_token:currentAttempt.lease_token});
  await c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[job.id]);
  check('current lease owner persists decision before job success');
  await rejected(c,'decision remains immutable after success',()=>c.query('UPDATE run_process_decisions SET evidence=$1 WHERE id=$2',['{"modified":true}',decision.id]));
  await c.query('UPDATE run_process_decisions SET published_at=clock_timestamp() WHERE id=$1',[decision.id]);
  check('decision publication timestamp remains updateable');
  const spec={evidence_version:'ev1',input_evidence_hash:'evidence1',model_version:'model1',prompt_version:'prompt1',output_schema_version:'schema1',reuse_key:randomUUID()};
  const llmBase={idempotency_key:randomUUID(),contract_version:'v03',kind:'recommend_feature',queue:'llm_recommendation',status:'queued',producer_kind:'user',producer_key:'admin1',
    requested_by_actor_id:'admin1',requester_role:'admin',llm_spec:spec,max_attempts:3,attempt_count:0,input_config_version_id:config.id};
  await rejected(c,'user cannot request LLM recommendation',()=>insert(c,'run_jobs',{...llmBase,requester_role:'user'}));
  await rejected(c,'NULL role cannot bypass administrator recommendation',()=>insert(c,'run_jobs',{...llmBase,requester_role:null}));
  await rejected(c,'LLM numeric version is rejected',()=>insert(c,'run_jobs',{...llmBase,llm_spec:{...spec,model_version:123}}));
  await rejected(c,'LLM object version is rejected',()=>insert(c,'run_jobs',{...llmBase,llm_spec:{...spec,prompt_version:{value:'v1'}}}));
  await insert(c,'run_jobs',llmBase);
  check('administrator may request feature recommendation');
  const explain={...llmBase,kind:'explain',queue:'llm_explanation',producer_key:'user1',requested_by_actor_id:'user1',requester_role:'user',input_decision_id:decision.id};
  await rejected(c,'explanation decision/config mismatch',()=>insert(c,'run_jobs',{...explain,input_config_version_id:otherConfig.id}));
  const llmJob=await insert(c,'run_jobs',explain);
  check('ordinary user may request explanation referencing fixed decision/config');
  const llmAttempt=await insert(c,'run_job_attempts',{job_id:llmJob.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+60000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[llmAttempt.id,llmJob.id]);
  const output={job_id:llmJob.id,kind:'explain',decision_id:decision.id,config_version_id:config.id,...spec,
    output:{explanation:'synthetic'},created_attempt_id:llmAttempt.id,lease_token:llmAttempt.lease_token};
  await rejected(c,'LLM result kind must match requested operation',()=>insert(c,'run_llm_results',{...output,kind:'recommend_feature',decision_id:null}));
  await rejected(c,'LLM result config must match referenced decision/request',()=>insert(c,'run_llm_results',{...output,config_version_id:otherConfig.id}));
  for (const field of ['evidence_version','input_evidence_hash','model_version','prompt_version','output_schema_version','reuse_key']) {
    await rejected(c,`LLM output ${field} must match request version`,()=>insert(c,'run_llm_results',{...output,[field]:'different'}));
  }
  await rejected(c,'LLM output cannot bypass lease token',()=>insert(c,'run_llm_results',{...output,lease_token:randomUUID()}));
  await rejected(c,'LLM success requires durable matching result',()=>c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[llmJob.id]));
  const llmResult=await insert(c,'run_llm_results',output);
  await c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[llmJob.id]);
  check('current LLM owner persists versioned result before success');
  await rejected(c,'LLM output immutable',()=>c.query("UPDATE run_llm_results SET prompt_version='changed' WHERE id=$1",[llmResult.id]));
  const reuseJob=await insert(c,'run_jobs',{...explain,idempotency_key:randomUUID()});
  const reuseAttempt=await insert(c,'run_job_attempts',{job_id:reuseJob.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+60000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[reuseAttempt.id,reuseJob.id]);
  await rejected(c,'LLM existing reuse key cannot persist a second result',()=>insert(c,'run_llm_results',{...output,job_id:reuseJob.id,created_attempt_id:reuseAttempt.id,lease_token:reuseAttempt.lease_token}));
  const snap=await insert(c,'data_snapshots',{source_id:source.id,status:'building'});
  const features=await insert(c,'ml_feature_sets');
  const fit=await insert(c,'ml_artifacts',{kind:'fit',snapshot_id:snap.id,feature_set_id:features.id});
  const lineage={snapshot_id:snap.id,feature_set_id:features.id,fit_artifact_id:fit.id};
  const modelInput=await insert(c,'ml_artifacts',{...lineage,kind:'model_input'});
  await insert(c,'ml_artifacts',{...lineage,kind:'model',model_input_artifact_id:modelInput.id});
  check('model/model_input matching snapshot/FeatureSet/fit accepted');
  await rejected(c,'model_input missing snapshot lineage',()=>insert(c,'ml_artifacts',{...lineage,kind:'model_input',snapshot_id:null}));
  await rejected(c,'model_input wrong fit kind',()=>insert(c,'ml_artifacts',{...lineage,kind:'model_input',fit_artifact_id:modelInput.id}));
  await rejected(c,'model reference must target model_input kind',()=>insert(c,'ml_artifacts',{...lineage,kind:'model',model_input_artifact_id:fit.id}));
  const otherSnap=await insert(c,'data_snapshots',{source_id:source.id,status:'building'});
  await insert(c,'ml_artifacts',{...lineage,kind:'model_input',snapshot_id:otherSnap.id});
  check('train fit reused for another snapshot from same source');
  const foreignSnap=await insert(c,'data_snapshots',{source_id:otherSource.id,status:'building'});
  await rejected(c,'fit cannot be reused across different data sources',()=>insert(c,'ml_artifacts',{...lineage,kind:'model_input',snapshot_id:foreignSnap.id}));
  await rejected(c,'model/model_input snapshot mismatch',()=>insert(c,'ml_artifacts',{...lineage,kind:'model',snapshot_id:otherSnap.id,model_input_artifact_id:modelInput.id}));
  await rejected(c,'model_input cannot refer to itself',()=>insert(c,'ml_artifacts',{...lineage,id:modelInput.id,kind:'model_input',model_input_artifact_id:modelInput.id}));
  await oldFixture(c);
  check(`${database}: old writer inserts remain valid after additive upgrade`);
}

async function concurrency(c, database) {
  const producer=`parallel-${randomUUID()}`;
  for (let n=0;n<2;n++) await insert(c,'run_jobs',{contract_version:'v03',kind:'preview',queue:'training',status:'queued',
    producer_kind:'user',producer_key:producer,requested_by_actor_id:producer,requester_role:'admin',idempotency_key:randomUUID(),max_attempts:3,attempt_count:0});
  const other=connection(database); await other.connect();
  try {
    for (const client of [c,other]) { await client.query('BEGIN'); await client.query("SET LOCAL statement_timeout='2s'"); }
    const sql="SELECT id FROM run_jobs WHERE producer_key=$1 AND status='queued' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1";
    const first=(await c.query(sql,[producer])).rows[0];
    const second=(await other.query(sql,[producer])).rows[0];
    assert(first && second && first.id!==second.id,'parallel claims must select distinct jobs');
    for (const [client,selected] of [[c,first],[other,second]]) {
      const attempt=await insert(client,'run_job_attempts',{job_id:selected.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+60000)});
      await client.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[attempt.id,selected.id]);
      await client.query('COMMIT');
    }
    assert.equal((await c.query(sql,[producer])).rows.length,0,'claimed jobs not selected as queued');
    check(`${database}: two real connections SKIP LOCKED select/commit distinct queued jobs`);
    await manifestRace(c, other, database);
  } finally { await c.query('ROLLBACK'); await other.query('ROLLBACK'); await other.end(); }
}

async function manifestRace(c, other, database) {
  const source=await insert(c,'ops_sources');
  async function obs(layer) {
    const execution=await insert(c,'data_executions',{source_id:source.id,layer});
    return insert(c,'data_observations',{source_id:source.id,layer,execution_id:execution.id,revision_no:1});
  }
  const p=await obs('P'), m=await obs('M');
  await insert(c,'data_processes',{observation_id:p.id,transaction_id:'race-tx',process_id:'race-p'});
  await insert(c,'data_messages',{observation_id:m.id,process_observation_id:p.id,transaction_id:'race-tx',process_id:'race-p'});
  await c.query('BEGIN'); await other.query('BEGIN');
  try {
    await insert(c,'run_input_manifests',{source_id:source.id,execution_id:p.execution_id,process_observation_id:p.id,
      message_observation_refs:[{observation_id:m.id,revision_no:1}],readiness:{ready:true},input_as_of:new Date(Date.now()+1000),contract_version:'v03'});
    await other.query("SET LOCAL lock_timeout='300ms'");
    await rejected(other,`${database}: in-flight manifest locks referenced typed MESSAGE`,()=>other.query("UPDATE data_messages SET process_id='race-change' WHERE observation_id=$1",[m.id]),['55P03']);
    await other.query('ROLLBACK'); await c.query('COMMIT');
    await other.query('BEGIN');
    await rejected(other,`${database}: after manifest commit MESSAGE mutation remains forbidden`,()=>other.query("UPDATE data_messages SET process_id='race-change' WHERE observation_id=$1",[m.id]));
  } finally { await other.query('ROLLBACK'); await c.query('ROLLBACK'); }
}

async function baselineParity(c) {
  const columns=(await c.query(`SELECT n.nspname AS schema_name,t.relname AS table_name,a.attnum::int AS ordinal,a.attname AS column_name,
    format_type(a.atttypid,a.atttypmod) AS formatted_type,a.attnotnull AS not_null,a.attidentity AS identity,a.attgenerated AS generated,
    pg_get_expr(d.adbin,d.adrelid) AS default_expression
    FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid JOIN pg_namespace n ON n.oid=t.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE n.nspname IN ('public','powercode_queue') AND t.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
    ORDER BY n.nspname,t.relname,a.attnum`)).rows;
  const constraints=(await c.query(`SELECT n.nspname AS schema_name,t.relname AS table_name,co.conname AS constraint_name,co.contype AS type,
    co.convalidated AS validated,pg_get_constraintdef(co.oid,true) AS definition FROM pg_constraint co
    JOIN pg_class t ON t.oid=co.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE n.nspname IN ('public','powercode_queue') ORDER BY n.nspname,t.relname,co.conname`)).rows;
  const indexes=(await c.query(`SELECT schemaname AS schema_name,tablename AS table_name,indexname AS index_name,indexdef AS definition
    FROM pg_indexes WHERE schemaname IN ('public','powercode_queue') ORDER BY schemaname,tablename,indexname`)).rows;
  const enums=(await c.query(`SELECT n.nspname AS schema_name,t.typname AS type_name,array_agg(e.enumlabel::text ORDER BY e.enumsortorder) AS labels
    FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_enum e ON e.enumtypid=t.oid
    WHERE n.nspname='public' GROUP BY n.nspname,t.typname ORDER BY n.nspname,t.typname`)).rows;
  for (const [key,value] of Object.entries({columns,constraints,indexes,enums})) {
    const normalized=rows=>rows.map(r=>JSON.stringify(Object.fromEntries(Object.entries(r).sort(([a],[b])=>a.localeCompare(b))))).sort();
    assert.deepEqual(normalized(value),normalized(baselineMetadata[key]),`001/002 live metadata ${key} parity`);
  }
  check(`001/002 captured server metadata parity (${columns.length} columns/${constraints.length} constraints/${indexes.length} indexes/${enums.length} enums)`);
}

// 011 (#78 A): evaluation sets, configuration comparisons and LLM method recommendation.
async function fixture011(c) {
  const source=await insert(c,'ops_sources');
  const otherSource=await insert(c,'ops_sources');
  const snapshot=await insert(c,'data_snapshots',{source_id:source.id,status:'ready'});
  const job=(kind,extra={})=>insert(c,'run_jobs',{...v03Admin,kind,queue:queueFor(kind),idempotency_key:randomUUID(),...extra});
  const setFields={source_id:source.id,snapshot_id:snapshot.id,purpose:'validation',display_name:'validation set P04 sev1',
    created_by_actor_id:'admin78',synthetic_request:{seed:42,repetitions:2},synthetic_profile_binding:{profileId:'esb-process-research-v1'}};
  const queuedSet=async()=>{ const j=await job('evaluation_set'); return [j,await insert(c,'data_evaluation_sets',{...setFields,job_id:j.id})]; };
  const readySet=async()=>{
    const [j,s]=await queuedSet(); const hash=randomUUID().replaceAll('-','').padEnd(64,'0');
    await c.query(`UPDATE data_evaluation_sets SET status='ready',generation=$2,content_hash=$3,artifact_relative_path=$4,ready_at=clock_timestamp() WHERE id=$1`,
      [s.id,JSON.stringify({rowCount:10,syntheticRowCount:2}),hash,`evaluation-sets/${s.id}/${hash}.json`]);
    return [j,s];
  };
  return { source, otherSource, snapshot, job, setFields, queuedSet, readySet };
}

async function behavioral011(c, database) {
  const { source, otherSource, snapshot, job, setFields, queuedSet } = await fixture011(c);
  const otherSnapshot=await insert(c,'data_snapshots',{source_id:otherSource.id,status:'ready'});
  const preparedSnapshot=await insert(c,'data_snapshots',{source_id:source.id,status:'ready',schema_version:'prepared-features-v1'});
  const buildingSnapshot=await insert(c,'data_snapshots',{source_id:source.id,status:'building'});
  const [configA,configB,configC]=[await insert(c,'ml_config_versions',{source_id:source.id}),
    await insert(c,'ml_config_versions',{source_id:source.id}),await insert(c,'ml_config_versions',{source_id:source.id})];
  const foreignConfig=await insert(c,'ml_config_versions',{source_id:otherSource.id});
  const legacy={contract_version:null,queue:null,producer_kind:null,producer_key:null,idempotency_key:null};

  // Job kind contract: queue, requester and legacy branch.
  for (const kind of ['evaluation_set','compare']) {
    await job(kind);
    check(`${database}: ${kind} accepted on training queue for administrator`);
    await rejected(c,`${kind} rejected on LLM recommendation queue`,()=>job(kind,{queue:'llm_recommendation'}));
    await rejected(c,`${kind} rejected for ordinary user`,()=>job(kind,{requester_role:'user'}));
    await rejected(c,`${kind} rejected for system producer`,()=>job(kind,{producer_kind:'system',producer_key:'system',requested_by_actor_id:null,requester_role:null}));
    await rejected(c,`${kind} rejects llm_spec`,()=>job(kind,{llm_spec:methodSpec()}));
    await rejected(c,`${kind} cannot use legacy NULL contract branch`,()=>job(kind,legacy));
  }
  const methods=await job('recommend_methods',{llm_spec:methodSpec()});
  check(`${database}: recommend_methods accepted on llm_recommendation queue for administrator`);
  await rejected(c,'recommend_methods rejected on training queue',()=>job('recommend_methods',{queue:'training',llm_spec:methodSpec()}));
  await rejected(c,'recommend_methods rejected on explanation queue',()=>job('recommend_methods',{queue:'llm_explanation',llm_spec:methodSpec()}));
  await rejected(c,'recommend_methods rejected for ordinary user',()=>job('recommend_methods',{requester_role:'user',llm_spec:methodSpec()}));
  await rejected(c,'recommend_methods requires llm_spec',()=>job('recommend_methods'));
  await rejected(c,'recommend_methods cannot use legacy NULL contract branch',()=>job('recommend_methods',legacy));
  for (const field of Object.keys(methodSpec())) {
    await rejected(c,`recommend_methods llm_spec.${field} required`,()=>job('recommend_methods',{llm_spec:{...methodSpec(),[field]:undefined}}));
  }
  await rejected(c,'recommend_methods llm_spec empty string rejected',()=>job('recommend_methods',{llm_spec:{...methodSpec(),model_version:''}}));
  await rejected(c,'recommend_methods llm_spec numeric version rejected',()=>job('recommend_methods',{llm_spec:{...methodSpec(),prompt_version:1}}));

  // recommend_methods result: decision-free, owner-checked, required before success.
  const attempt=await insert(c,'run_job_attempts',{job_id:methods.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+600000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[attempt.id,methods.id]);
  await rejected(c,'recommend_methods completion without stored result rejected',()=>c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[methods.id]));
  const result={job_id:methods.id,kind:'recommend_methods',...methods.llm_spec,created_attempt_id:attempt.id,lease_token:attempt.lease_token,
    output:{recommendationContractVersion:'method-recommendation-v1',recommendations:[],unregistered:[],reviews:[]}};
  await rejected(c,'recommend_methods result cannot reference a decision',()=>insert(c,'run_llm_results',{...result,decision_id:randomUUID()}));
  await rejected(c,'recommend_methods result must match requested prompt version',()=>insert(c,'run_llm_results',{...result,prompt_version:'other'}));
  await rejected(c,'recommend_methods result requires current lease token',()=>insert(c,'run_llm_results',{...result,lease_token:randomUUID()}));
  await insert(c,'run_llm_results',result);
  await c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[methods.id]);
  check(`${database}: recommend_methods result stored by current owner, then job succeeds`);

  // Evaluation set insert rules.
  const setJob=await job('evaluation_set');
  const fields={...setFields,job_id:setJob.id};
  await rejected(c,'evaluation set INSERT must be queued',()=>insert(c,'data_evaluation_sets',{...fields,status:'ready',generation:{},content_hash:'a'.repeat(64),ready_at:new Date()}));
  await rejected(c,'evaluation set INSERT cannot carry generation result',()=>insert(c,'data_evaluation_sets',{...fields,content_hash:'b'.repeat(64)}));
  await rejected(c,'evaluation set INSERT cannot carry error code',()=>insert(c,'data_evaluation_sets',{...fields,error_code:'x'}));
  await rejected(c,'evaluation set requires evaluation_set job',async()=>insert(c,'data_evaluation_sets',{...fields,job_id:(await job('compare')).id}));
  await rejected(c,'evaluation set rejects prepared snapshot',()=>insert(c,'data_evaluation_sets',{...fields,snapshot_id:preparedSnapshot.id}));
  await rejected(c,'evaluation set rejects non-ready snapshot',()=>insert(c,'data_evaluation_sets',{...fields,snapshot_id:buildingSnapshot.id}));
  await rejected(c,'evaluation set snapshot must share source',()=>insert(c,'data_evaluation_sets',{...fields,snapshot_id:otherSnapshot.id}));
  await rejected(c,'evaluation set purpose limited to validation/test',()=>insert(c,'data_evaluation_sets',{...fields,purpose:'train'}));
  const set=await insert(c,'data_evaluation_sets',fields);
  check(`${database}: queued evaluation set registered for evaluation_set job`);
  await rejected(c,'one evaluation set per job',()=>insert(c,'data_evaluation_sets',fields));
  await rejected(c,'queued evaluation set cannot be edited in place',()=>c.query("UPDATE data_evaluation_sets SET display_name='changed' WHERE id=$1",[set.id]));
  await rejected(c,'queued evaluation set cannot be deleted',()=>c.query('DELETE FROM data_evaluation_sets WHERE id=$1',[set.id]));
  await rejected(c,'ready requires generation/content hash/ready time',()=>c.query("UPDATE data_evaluation_sets SET status='ready' WHERE id=$1",[set.id]));
  const hash='c'.repeat(64);
  const ready=`UPDATE data_evaluation_sets SET status='ready',generation='{"rowCount":10}',content_hash=$2,ready_at=clock_timestamp()`;
  await rejected(c,'ready transition cannot change set input',()=>c.query(`${ready},display_name='changed' WHERE id=$1`,[set.id,hash]));
  await rejected(c,'ready artifact path must be evaluation-sets/<id>/<hash>.json',()=>c.query(`${ready},artifact_relative_path='evaluation-sets/other.json' WHERE id=$1`,[set.id,hash]));
  await rejected(c,'ready set cannot carry error code',()=>c.query(`${ready},error_code='x' WHERE id=$1`,[set.id,hash]));
  await c.query(`${ready},artifact_relative_path=$3 WHERE id=$1`,[set.id,hash,`evaluation-sets/${set.id}/${hash}.json`]);
  check(`${database}: queued evaluation set becomes ready once with generation result`);
  await rejected(c,'ready evaluation set cannot become failed',()=>c.query("UPDATE data_evaluation_sets SET status='failed',error_code='x',ready_at=NULL WHERE id=$1",[set.id]));
  await rejected(c,'ready evaluation set no-op UPDATE rejected',()=>c.query('UPDATE data_evaluation_sets SET display_name=display_name WHERE id=$1',[set.id]));
  await rejected(c,'ready evaluation set DELETE rejected',()=>c.query('DELETE FROM data_evaluation_sets WHERE id=$1',[set.id]));
  const [,failing]=await queuedSet();
  await rejected(c,'failed evaluation set requires error code',()=>c.query("UPDATE data_evaluation_sets SET status='failed' WHERE id=$1",[failing.id]));
  await c.query("UPDATE data_evaluation_sets SET status='failed',error_code='synthetic_generation_rejected' WHERE id=$1",[failing.id]);
  check(`${database}: queued evaluation set becomes failed with reason`);
  await rejected(c,'failed evaluation set cannot become ready',()=>c.query(`${ready},error_code=NULL WHERE id=$1`,[failing.id,hash]));
  await rejected(c,'failed evaluation set cannot return to queued',()=>c.query("UPDATE data_evaluation_sets SET status='queued',error_code=NULL WHERE id=$1",[failing.id]));
  await rejected(c,'failed evaluation set DELETE rejected',()=>c.query('DELETE FROM data_evaluation_sets WHERE id=$1',[failing.id]));

  // Job failure synchronisation (Trainer finish, max attempts, dead queue reconcile).
  const stateOf=async id=>(await c.query('SELECT status,error_code FROM data_evaluation_sets WHERE id=$1',[id])).rows[0];
  const [finishJob,finishSet]=await queuedSet();
  const finishAttempt=await insert(c,'run_job_attempts',{job_id:finishJob.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+600000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[finishAttempt.id,finishJob.id]);
  await c.query("UPDATE run_jobs SET status='queued',error_code='retryable_error' WHERE id=$1",[finishJob.id]);
  assert.deepEqual(await stateOf(finishSet.id),{status:'queued',error_code:null},'retry keeps evaluation set queued');
  await c.query("UPDATE run_jobs SET status='running' WHERE id=$1",[finishJob.id]);
  await c.query("UPDATE run_jobs SET status='failed',error_code='synthetic_profile_unsupported',finished_at=clock_timestamp() WHERE id=$1",[finishJob.id]);
  assert.deepEqual(await stateOf(finishSet.id),{status:'failed',error_code:'synthetic_profile_unsupported'},'finish failure synchronised');
  check(`${database}: worker finish failure fails queued evaluation set with job error; retry keeps it queued`);
  const [maxJob,maxSet]=await queuedSet();
  await c.query("UPDATE run_jobs SET status='failed',error_code='max_attempts',finished_at=clock_timestamp() WHERE id=$1",[maxJob.id]);
  assert.deepEqual(await stateOf(maxSet.id),{status:'failed',error_code:'max_attempts'},'max attempts synchronised');
  const [nullJob,nullSet]=await queuedSet();
  await c.query("UPDATE run_jobs SET status='failed',error_code=NULL WHERE id=$1",[nullJob.id]);
  assert.deepEqual(await stateOf(nullSet.id),{status:'failed',error_code:'evaluation_set_failed'},'missing job error gets default');
  const [deadJob,deadSet]=await queuedSet();
  await c.query(`INSERT INTO powercode_queue.training_jobs(id,payload,status) VALUES($1,$2,'dead')`,[randomUUID(),JSON.stringify({jobId:deadJob.id})]);
  await c.query(reconcileTrainingQueue);
  assert.deepEqual(await stateOf(deadSet.id),{status:'failed',error_code:'queue_exhausted'},'dead queue reconcile synchronised');
  await c.query("UPDATE run_jobs SET status='failed',error_code='late_failure' WHERE id=$1",[setJob.id]);
  assert.equal((await stateOf(set.id)).status,'ready','later job failure does not rewrite a ready set');
  check(`${database}: max-attempt, NULL-error and dead-queue job failures fail queued sets; ready set untouched`);

  // Comparison header.
  const cmpJob=await job('compare');
  const evalJob=await job('evaluate');
  const cmpFields={job_id:cmpJob.id,evaluation_set_id:set.id,source_id:source.id,baseline_config_version_id:configA.id,active_revision:3,
    status:'succeeded',summary:{comparisonContractVersion:'config-comparison-v1',replacementRecommendation:{ruleVersion:'replacement-recommendation-v1'}},completed_at:new Date()};
  await rejected(c,'comparison requires compare job',()=>insert(c,'run_config_comparisons',{...cmpFields,job_id:evalJob.id}));
  await rejected(c,'comparison requires ready evaluation set',()=>insert(c,'run_config_comparisons',{...cmpFields,evaluation_set_id:failing.id}));
  await rejected(c,'comparison source must match evaluation set',()=>insert(c,'run_config_comparisons',{...cmpFields,source_id:otherSource.id}));
  await rejected(c,'comparison baseline must belong to the same source',()=>insert(c,'run_config_comparisons',{...cmpFields,baseline_config_version_id:foreignConfig.id}));
  await rejected(c,'comparison evaluation set must exist',()=>insert(c,'run_config_comparisons',{...cmpFields,evaluation_set_id:randomUUID()}));
  await rejected(c,'comparison status limited to succeeded/failed',()=>insert(c,'run_config_comparisons',{...cmpFields,status:'running'}));
  await rejected(c,'comparison summary must be an object',()=>insert(c,'run_config_comparisons',{...cmpFields,summary:[]}));
  await rejected(c,'comparison active revision non-negative',()=>insert(c,'run_config_comparisons',{...cmpFields,active_revision:-1}));
  const cmp=await insert(c,'run_config_comparisons',cmpFields);
  check(`${database}: comparison header stored for compare job on ready set`);
  await rejected(c,'one comparison per compare job',()=>insert(c,'run_config_comparisons',cmpFields));

  // Comparison evaluations: one row per configuration of the same compare job.
  const evalFields={job_id:cmpJob.id,comparison_id:cmp.id,evaluation_set_id:set.id,snapshot_id:snapshot.id,purpose:'validation',
    evaluation_spec_version:'comparison-methods-v1',evaluation_spec_hash:'d'.repeat(64),applicability:'valid',
    population_count:10,scored_count:10,unavailable_count:0,metrics:{composite:{}},reason_codes:['synthetic_set_evaluation']};
  const evalA=await insert(c,'run_evaluations',{...evalFields,config_version_id:configA.id});
  const evalB=await insert(c,'run_evaluations',{...evalFields,config_version_id:configB.id});
  check(`${database}: one compare job stores several evaluation rows, one per configuration`);
  await rejected(c,'same compare job and configuration evaluation duplicate rejected',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:configA.id}));
  await rejected(c,'comparison evaluation requires evaluation set',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:configC.id,evaluation_set_id:null}));
  await rejected(c,'set evaluation snapshot must equal set snapshot',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:configC.id,snapshot_id:buildingSnapshot.id}));
  await rejected(c,'set evaluation purpose must equal set purpose',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:configC.id,purpose:'test'}));
  await rejected(c,'set evaluation configuration must share set source',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:foreignConfig.id}));
  await rejected(c,'comparison evaluation job must be the comparison job',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:configC.id,job_id:evalJob.id}));
  await rejected(c,'set evaluation requires ready set',()=>insert(c,'run_evaluations',{...evalFields,config_version_id:configC.id,comparison_id:null,evaluation_set_id:failing.id}));

  // Comparison items.
  const item=(extra)=>insert(c,'run_config_comparison_items',{comparison_id:cmp.id,status:'succeeded',...extra});
  await rejected(c,'baseline configuration cannot be a candidate item',()=>item({config_version_id:configA.id,role:'candidate',evaluation_id:evalA.id}));
  await rejected(c,'non-baseline configuration cannot be the active item',()=>item({config_version_id:configB.id,role:'active',evaluation_id:evalB.id}));
  await rejected(c,'item role limited to active/candidate',()=>item({config_version_id:configB.id,role:'baseline',evaluation_id:evalB.id}));
  await rejected(c,'item evaluation must be for the same configuration',()=>item({config_version_id:configB.id,role:'candidate',evaluation_id:evalA.id}));
  await rejected(c,'succeeded item requires evaluation',()=>item({config_version_id:configB.id,role:'candidate'}));
  await rejected(c,'succeeded item cannot carry error code',()=>item({config_version_id:configB.id,role:'candidate',evaluation_id:evalB.id,error_code:'x'}));
  await rejected(c,'failed item cannot carry evaluation',()=>item({config_version_id:configB.id,role:'candidate',status:'failed',error_code:'x',evaluation_id:evalB.id}));
  await rejected(c,'failed item requires error code',()=>item({config_version_id:configC.id,role:'candidate',status:'failed'}));
  await rejected(c,'item status limited to succeeded/failed',()=>item({config_version_id:configC.id,role:'candidate',status:'skipped',error_code:'x'}));
  await rejected(c,'item comparison must exist',()=>item({comparison_id:randomUUID(),config_version_id:configC.id,role:'candidate',status:'failed',error_code:'x'}));
  await item({config_version_id:configA.id,role:'active',evaluation_id:evalA.id});
  await item({config_version_id:configB.id,role:'candidate',evaluation_id:evalB.id});
  await item({config_version_id:configC.id,role:'candidate',status:'failed',error_code:'method_not_executable:sr:ewma'});
  check(`${database}: comparison items store active baseline, succeeded and failed candidates`);
  await rejected(c,'same comparison/configuration item duplicate rejected',()=>item({config_version_id:configC.id,role:'candidate',status:'failed',error_code:'y'}));
  const cmpJob2=await job('compare');
  const cmp2=await insert(c,'run_config_comparisons',{...cmpFields,job_id:cmpJob2.id,baseline_config_version_id:null});
  check(`${database}: comparison without active configuration stored with NULL baseline`);
  await rejected(c,'no active item when comparison has no baseline',()=>item({comparison_id:cmp2.id,config_version_id:configA.id,role:'active',evaluation_id:evalA.id}));
  await rejected(c,'item cannot reuse an evaluation of another comparison',()=>item({comparison_id:cmp2.id,config_version_id:configB.id,role:'candidate',evaluation_id:evalB.id}));

  // Immutability of 011 results and existing catalog rows.
  await rejected(c,'comparison summary immutable',()=>c.query(`UPDATE run_config_comparisons SET summary='{"rewritten":true}' WHERE id=$1`,[cmp.id]));
  await rejected(c,'comparison DELETE rejected',()=>c.query('DELETE FROM run_config_comparisons WHERE id=$1',[cmp2.id]));
  await rejected(c,'comparison item UPDATE rejected',()=>c.query("UPDATE run_config_comparison_items SET error_code='z' WHERE comparison_id=$1 AND config_version_id=$2",[cmp.id,configC.id]));
  await rejected(c,'comparison item DELETE rejected',()=>c.query('DELETE FROM run_config_comparison_items WHERE comparison_id=$1',[cmp.id]));
  await rejected(c,'comparison evaluation row UPDATE rejected',()=>c.query("UPDATE run_evaluations SET applicability='unmeasured' WHERE id=$1",[evalA.id]));
  await rejected(c,'comparison evaluation row DELETE rejected',()=>c.query('DELETE FROM run_evaluations WHERE id=$1',[evalB.id]));
  await rejected(c,'ml_config_versions UPDATE rejected',()=>c.query(`UPDATE ml_config_versions SET configuration_spec='{"schemaVersion":3}' WHERE id=$1`,[configA.id]));
  await rejected(c,'ml_config_versions DELETE rejected',()=>c.query('DELETE FROM ml_config_versions WHERE id=$1',[configC.id]));

  // Existing evaluate path keeps its single row and NULL comparison lineage.
  const plain={...evalFields,job_id:evalJob.id,comparison_id:null,evaluation_set_id:null};
  await insert(c,'run_evaluations',{...plain,config_version_id:configA.id});
  check(`${database}: evaluate job evaluation accepted without comparison lineage`);
  await rejected(c,'evaluate job still owns exactly one evaluation row',()=>insert(c,'run_evaluations',{...plain,config_version_id:configB.id}));
}

// database-roles.sql grants for the 011 tables, exercised with SET ROLE inside the rollback-only transaction.
async function roles011(c, database) {
  await c.query(rolesSql);
  const { source, job, setFields, queuedSet, readySet } = await fixture011(c);
  const config=await insert(c,'ml_config_versions',{source_id:source.id});
  const [,readyFixture]=await readySet();
  // An unexpected error aborts the transaction; surface it instead of a RESET ROLE failure.
  const asRole=async(role,action)=>{ await c.query(`SET ROLE ${role}`); const result=await action(); await c.query('RESET ROLE'); return result; };
  // API registers a queued set for its evaluation_set job.
  const apiJob=await job('evaluation_set');
  const apiSet=await asRole('powercode_api',()=>insert(c,'data_evaluation_sets',{...setFields,job_id:apiJob.id}));
  await asRole('powercode_api',()=>rejected(c,'API cannot complete an evaluation set',()=>c.query("UPDATE data_evaluation_sets SET status='failed',error_code='x' WHERE id=$1",[apiSet.id]),['42501']));
  // Trainer completes the set and writes the comparison bundle.
  const hash='e'.repeat(64);
  await asRole('powercode_trainer',()=>c.query(`UPDATE data_evaluation_sets SET status='ready',generation='{"rowCount":3}',content_hash=$2,ready_at=clock_timestamp() WHERE id=$1`,[apiSet.id,hash]));
  const cmpJob=await job('compare');
  await asRole('powercode_trainer',async()=>{
    const cmp=await insert(c,'run_config_comparisons',{job_id:cmpJob.id,evaluation_set_id:readyFixture.id,source_id:source.id,baseline_config_version_id:config.id,
      active_revision:0,status:'succeeded',summary:{},completed_at:new Date()});
    const evaluation=await insert(c,'run_evaluations',{job_id:cmpJob.id,comparison_id:cmp.id,evaluation_set_id:readyFixture.id,config_version_id:config.id,
      snapshot_id:readyFixture.snapshot_id,purpose:'validation',population_count:1,scored_count:1,unavailable_count:0});
    await insert(c,'run_config_comparison_items',{comparison_id:cmp.id,config_version_id:config.id,role:'active',status:'succeeded',evaluation_id:evaluation.id});
  });
  check(`${database}: roles - API registers set, Trainer completes set and comparison bundle`);
  // LLM worker reconciles dead training-queue jobs; the trigger needs its narrow column grant.
  const [deadJob,deadSet]=await queuedSet();
  await c.query(`INSERT INTO powercode_queue.training_jobs(id,payload,status) VALUES($1,$2,'dead')`,[randomUUID(),JSON.stringify({jobId:deadJob.id})]);
  await asRole('powercode_llm',()=>c.query(reconcileTrainingQueue));
  assert.equal((await c.query('SELECT status FROM data_evaluation_sets WHERE id=$1',[deadSet.id])).rows[0].status,'failed');
  check(`${database}: roles - LLM dead-queue reconcile fails the queued set through the trigger`);
  await asRole('powercode_llm',()=>rejected(c,'LLM role cannot edit set content',()=>c.query("UPDATE data_evaluation_sets SET display_name='x' WHERE id=$1",[deadSet.id]),['42501']));
  await asRole('powercode_detector',()=>rejected(c,'Detector role cannot read evaluation sets',()=>c.query('SELECT id FROM data_evaluation_sets'),['42501']));
  await asRole('powercode_detector',()=>rejected(c,'Detector role cannot read comparisons',()=>c.query('SELECT id FROM run_config_comparisons'),['42501']));
  await asRole('powercode_api',()=>rejected(c,'API cannot write comparisons',()=>c.query('DELETE FROM run_config_comparison_items'),['42501']));
  // The redefined completion check must not demand run_llm_results access for non-LLM jobs (demo role has none).
  await c.query(demoRoleSql);
  const trainJob=await job('train');
  const trainAttempt=await insert(c,'run_job_attempts',{job_id:trainJob.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+600000)});
  await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[trainAttempt.id,trainJob.id]);
  await asRole('powercode_demo',()=>c.query("UPDATE run_jobs SET status='succeeded' WHERE id=$1",[trainJob.id]));
  check(`${database}: roles - demo role without run_llm_results access still completes a v03 train job`);
}

// psql -v ON_ERROR_STOP=1 -f semantics: the enum statements commit first, then BEGIN..COMMIT fails midway.
async function rollback011(database) {
  const c=connection(database); await c.connect();
  try {
    for (let i=0;i<idx011;i++) await c.query(migrations[i]);
    const text=migrations[idx011].replaceAll('\r\n','\n');
    const begin=text.indexOf('\nBEGIN;\n');
    assert(begin>0,'011 has a BEGIN block after its enum statements');
    const anchor=text.indexOf('\n',text.indexOf('CREATE INDEX data_evaluation_sets_source_idx'));
    assert(anchor>begin,'fault injection point is inside the 011 transaction');
    const faulty=text.slice(begin,anchor+1)+'SELECT 1/0; -- injected failure after constraint, function and table changes\n'+text.slice(anchor+1);
    const structure=async()=>(await c.query(`SELECT
      (SELECT array_agg(table_schema||'.'||table_name ORDER BY 1) FROM information_schema.tables WHERE table_schema IN ('public','powercode_queue')) AS tables,
      (SELECT array_agg(column_name::text ORDER BY ordinal_position) FROM information_schema.columns WHERE table_name='run_evaluations') AS evaluation_columns,
      (SELECT array_agg(conrelid::regclass::text||':'||conname||':'||pg_get_constraintdef(oid) ORDER BY 1)
         FROM pg_constraint WHERE conrelid IN ('run_jobs'::regclass,'run_llm_results'::regclass,'run_evaluations'::regclass)) AS constraints,
      (SELECT array_agg(indexdef ORDER BY 1) FROM pg_indexes WHERE tablename='run_evaluations') AS evaluation_indexes,
      (SELECT array_agg(tgrelid::regclass::text||':'||tgname ORDER BY 1) FROM pg_trigger WHERE NOT tgisinternal) AS triggers,
      (SELECT array_agg(proname::text||':'||md5(prosrc) ORDER BY 1) FROM pg_proc WHERE pronamespace='public'::regnamespace) AS functions`)).rows[0];
    const before=await structure();
    await c.query(text.slice(0,begin));
    let error=null;
    try { await c.query(faulty); } catch (e) { error=e; }
    await c.query('ROLLBACK');
    assert(error && error.code==='22012',`injected 011 failure must abort the migration (${error?.message})`);
    assert.deepEqual(await structure(),before,'011 structural changes rolled back');
    const labels=(await c.query("SELECT array_agg(e.enumlabel::text ORDER BY e.enumsortorder) AS l FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='job_kind'")).rows[0].l;
    for (const label of ['evaluation_set','compare','recommend_methods']) assert(labels.includes(label),`committed enum ${label} remains`);
    check('011 mid-transaction failure rolls back constraints, function, tables and triggers; committed enum values remain');
    for (let i=idx011;i<migrations.length;i++) await c.query(migrations[i]);
    await parity(c);
    check('011 applies cleanly after the rolled-back attempt (enum ADD VALUE IF NOT EXISTS)');
  } finally { await c.end(); }
}

// Temp-table name shadowing against the 011 validators. Each attack uses a NEW connection whose first
// reference to the shadowed name is the attacker's temp table (a warmed plan cache would hide the hole).
async function searchPath011(database) {
  const c=connection(database); await c.connect();
  let f;
  try {
    for (const sql of migrations) await c.query(sql);
    await c.query(rolesSql);
    const { source, snapshot, job, setFields, queuedSet, readySet } = await fixture011(c);
    const configA=await insert(c,'ml_config_versions',{source_id:source.id});
    const configB=await insert(c,'ml_config_versions',{source_id:source.id});
    const [,readyFixture]=await readySet();
    const [queuedJob,queuedFixture]=await queuedSet();
    const compareJob=await job('compare');
    const cmp=await insert(c,'run_config_comparisons',{job_id:compareJob.id,evaluation_set_id:readyFixture.id,source_id:source.id,
      baseline_config_version_id:configA.id,active_revision:0,status:'succeeded',summary:{},completed_at:new Date()});
    const evalA=await insert(c,'run_evaluations',{job_id:compareJob.id,comparison_id:cmp.id,evaluation_set_id:readyFixture.id,
      config_version_id:configA.id,snapshot_id:snapshot.id,purpose:'validation',population_count:1,scored_count:1,unavailable_count:0});
    const spareCompare=await job('compare');
    const evaluateJob=await job('evaluate');
    const expired=await job('compare');
    const attempt=await insert(c,'run_job_attempts',{job_id:expired.id,attempt_no:1,status:'running',lease_until:new Date(Date.now()+600000)});
    await c.query("UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2",[attempt.id,expired.id]);
    await c.query("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 minute' WHERE id=$1",[attempt.id]);
    f={ source, snapshot, setFields, configA, configB, readyFixture, queuedJob, queuedFixture, cmp, evalA, spareCompare, evaluateJob, expired };
  } finally { await c.end(); }

  async function attack(label, role, shadow, action, verify) {
    const a=connection(database); await a.connect();
    try {
      await a.query('BEGIN');
      await a.query(`SET ROLE ${role}`);
      for (const [sql,params] of shadow) await a.query(sql,params);
      let error=null;
      try { await a.query('SAVEPOINT attack'); await action(a); } catch (e) { error=e; await a.query('ROLLBACK TO SAVEPOINT attack'); }
      const blocked=verify ? await verify(a,error) : Boolean(error && ['P0001','P0002'].includes(error.code));
      if (blocked) check(`${label} (${error ? error.code+' '+error.message : 'validator used public table'})`);
      else { failures.push(label); console.log(`FAIL search_path bypass: ${label}${error ? ' ('+error.code+' '+error.message+')' : ''}`); }
    } finally { await a.query('ROLLBACK').catch(()=>{}); await a.end(); }
  }
  const later="clock_timestamp()+interval '1 hour'";
  await attack('validate_job: trainer temp run_job_attempts cannot fake an unexpired lease for completion','powercode_trainer',
    [['CREATE TEMP TABLE run_job_attempts AS SELECT * FROM public.run_job_attempts WHERE job_id=$1',[f.expired.id]],
     [`UPDATE pg_temp.run_job_attempts SET lease_until=${later}`]],
    a=>a.query("UPDATE public.run_jobs SET status='succeeded' WHERE id=$1",[f.expired.id]));
  await attack('protect_evaluation_set: API temp run_jobs cannot relabel a compare job as evaluation_set','powercode_api',
    [["CREATE TEMP TABLE run_jobs AS SELECT id,'evaluation_set'::text AS kind FROM public.run_jobs WHERE id=$1",[f.spareCompare.id]]],
    a=>a.query(`INSERT INTO public.data_evaluation_sets(id,job_id,source_id,snapshot_id,purpose,display_name,created_by_actor_id)
      VALUES($1,$2,$3,$4,'validation','shadow','admin78')`,[randomUUID(),f.spareCompare.id,f.source.id,f.snapshot.id]));
  await attack('fail_evaluation_set_with_job: trainer temp data_evaluation_sets cannot swallow the failure sync','powercode_trainer',
    [['CREATE TEMP TABLE data_evaluation_sets(job_id uuid,status text,error_code text)']],
    a=>a.query("UPDATE public.run_jobs SET status='failed',error_code='shadowed' WHERE id=$1",[f.queuedJob.id]),
    async(a,error)=>!error && (await a.query('SELECT status FROM public.data_evaluation_sets WHERE id=$1',[f.queuedFixture.id])).rows[0].status==='failed');
  await attack('comparison lineage: trainer temp run_jobs cannot relabel an evaluate job as compare','powercode_trainer',
    [["CREATE TEMP TABLE run_jobs AS SELECT id,'compare'::text AS kind FROM public.run_jobs WHERE id=$1",[f.evaluateJob.id]]],
    a=>a.query(`INSERT INTO public.run_config_comparisons(id,job_id,evaluation_set_id,source_id,baseline_config_version_id,active_revision,status,summary,completed_at)
      VALUES($1,$2,$3,$4,$5,0,'succeeded','{}',clock_timestamp())`,[randomUUID(),f.evaluateJob.id,f.readyFixture.id,f.source.id,f.configA.id]));
  await attack('evaluation lineage: trainer temp data_evaluation_sets cannot change the set purpose','powercode_trainer',
    [['CREATE TEMP TABLE data_evaluation_sets AS SELECT * FROM public.data_evaluation_sets WHERE id=$1',[f.readyFixture.id]],
     ["UPDATE pg_temp.data_evaluation_sets SET purpose='test'"]],
    a=>a.query(`INSERT INTO public.run_evaluations(id,job_id,config_version_id,snapshot_id,purpose,evaluation_spec_version,evaluation_spec_hash,
      evaluation_spec,applicability,population_count,scored_count,unavailable_count,metrics,reason_codes,completed_at,comparison_id,evaluation_set_id)
      VALUES($1,$2,$3,$4,'test','v','h','{}','valid',1,1,0,'{}','[]',clock_timestamp(),$5,$6)`,
      [randomUUID(),f.cmp.job_id,f.configB.id,f.snapshot.id,f.cmp.id,f.readyFixture.id]));
  await attack('item lineage: trainer temp run_config_comparisons cannot move the baseline','powercode_trainer',
    [['CREATE TEMP TABLE run_config_comparisons AS SELECT * FROM public.run_config_comparisons WHERE id=$1',[f.cmp.id]],
     ['UPDATE pg_temp.run_config_comparisons SET baseline_config_version_id=$1',[f.configB.id]]],
    a=>a.query(`INSERT INTO public.run_config_comparison_items(comparison_id,config_version_id,role,status,evaluation_id)
      VALUES($1,$2,'candidate','succeeded',$3)`,[f.cmp.id,f.configA.id,f.evalA.id]));
  const c2=connection(database); await c2.connect();
  try {
    const pinned=(await c2.query(`SELECT array_agg(proname::text ORDER BY proname) AS names FROM pg_catalog.pg_proc
      WHERE proname IN ('erd_v03_validate_job','protect_evaluation_set','fail_evaluation_set_with_job','validate_comparison_lineage')
        AND proconfig @> ARRAY['search_path=public, pg_temp'] AND NOT prosecdef`)).rows[0].names;
    assert.deepEqual(pinned,['erd_v03_validate_job','fail_evaluation_set_with_job','protect_evaluation_set','validate_comparison_lineage'],
      '011 functions pin search_path and stay SECURITY INVOKER');
    check('011 functions pin search_path=public, pg_temp and stay SECURITY INVOKER');
  } finally { await c2.end(); }
}
