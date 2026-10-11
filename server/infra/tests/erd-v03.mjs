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
  for (const name of ['erd_fresh','erd_upgrade']) await admin.query(`CREATE DATABASE ${name}`);
  await admin.end();
  for (const name of ['erd_fresh','erd_upgrade']) {
    const c=connection(name); await c.connect();
    try {
      let before;
      for (let i=0;i<migrations.length;i++) {
        await c.query(migrations[i]);
        if (i===1 && baselineMetadata) await baselineParity(c);
        if (name==='erd_upgrade' && i===1) before=await oldFixture(c);
      }
      check(`${name}: 001 through ${migrationNames.at(-1)} apply`);
      if (before) for (const [table,oldRows] of Object.entries(before)) {
        const now=(await c.query(`SELECT row_to_json(t) AS row FROM ${table} t`)).rows.map(r=>r.row);
        for (const old of oldRows) assert(now.some(n=>Object.entries(old).every(([k,v])=>JSON.stringify(n[k])===JSON.stringify(v))),`${table} legacy row preserved`);
      }
      if (before) check('upgrade retains legacy rows and original field values');
      await parity(c);
      // Behavioral scenarios run in a rollback-only transaction.
      await c.query('BEGIN');
      await behavioral(c, name);
      await c.query('ROLLBACK');
      await concurrency(c, name);
    } finally { await c.end(); }
  }
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
