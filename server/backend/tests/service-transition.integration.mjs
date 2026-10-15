import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import net from 'node:net';
import pg from 'pg';

const url=process.env.PIPELINE_TEST_DATABASE_URL;
const parsed=new URL(url);
assert(['localhost','127.0.0.1'].includes(parsed.hostname) && parsed.pathname==='/qa_research_pipeline');
const directory=await mkdtemp(path.join(os.tmpdir(),'pwc-transition-api-'));
const pool=new pg.Pool({connectionString:url});
const backend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
let child;
const controller=new AbortController();
try {
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));
 child=spawn(process.execPath,[path.join(backend,'dist/index.js')],{cwd:directory,stdio:'ignore',env:{PATH:process.env.PATH,
  DATABASE_URL:url,JWT_SECRET:'local-transition-secret',USER_DATA_PATH:path.join(directory,'users.json'),PORT:String(port),
  SEED_ADMIN_EMAIL:'admin@example.test',SEED_ADMIN_PASSWORD:'test-password',SEED_USER_EMAIL:'user@example.test',SEED_USER_PASSWORD:'test-password'}});
 const base=`http://127.0.0.1:${port}`;
 for(let i=0;i<50;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
 const login=async email=>(await (await fetch(base+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password:'test-password'})})).json()).token;
 const token=await login('user@example.test');const admin=await login('admin@example.test');assert(token && admin);
 const {rows:[decision]}=await pool.query(`SELECT d.id,o.source_id FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id ORDER BY d.decided_at DESC LIMIT 1`);
 const call=async(token,input)=>{const res=await fetch(base+'/api/jobs',{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(input)});return {status:res.status,body:await res.json()};};
 const input={kind:'explain',sourceId:decision.source_id,decisionId:decision.id};
 const first=await call(token,input);assert.equal(first.status,201);
 const repeated=await call(token,input);assert.equal(repeated.status,200);assert.equal(first.body.id,repeated.body.id);
 const otherActor=await call(admin,input);assert.equal(otherActor.status,201);assert.notEqual(otherActor.body.id,first.body.id);
 const {rows:[lineage]}=await pool.query('SELECT queue,llm_spec,input_spec FROM run_jobs WHERE id=$1',[first.body.id]);
 assert.equal(lineage.queue,'llm_explanation');assert(lineage.input_spec.savedEvidenceJson);
 const metrics=await fetch(base+`/api/pipeline/collector/metrics?sourceId=${decision.source_id}`,{headers:{Authorization:`Bearer ${token}`}});assert.equal(metrics.status,403);
 const {rows:[state]}=await pool.query('SELECT configuration_spec,source_id FROM ml_config_versions ORDER BY created_at LIMIT 1');
 const bad=await call(admin,{kind:'train',sourceId:state.source_id,featureSetId:state.configuration_spec.featureSetId,from:'2026-01-01T00:00:00Z',until:'2026-01-02T00:00:00Z'});
 assert.equal(bad.status,400);assert.equal(bad.body.code,'detection_policy_required');
 const {rows:[snapshot]}=await pool.query('SELECT selection_spec FROM data_snapshots WHERE id=$1',[state.configuration_spec.trainingSnapshotId]);
 const trainInput={kind:'train',sourceId:state.source_id,featureSetId:state.configuration_spec.featureSetId,
   ruleVersionIds:state.configuration_spec.ruleVersionIds,snapshotId:state.configuration_spec.trainingSnapshotId,
   from:snapshot.selection_spec.from.replace('+00:00','Z'),until:snapshot.selection_spec.until.replace('+00:00','Z'),
   detectionPolicy:{mode:'rule_only',deadlineMs:1000,combination:'selected_path'}};
 const candidate=await call(admin,trainInput);assert.equal(candidate.status,201);
 const {rows:[contract]}=await pool.query('SELECT contract_version,queue FROM run_jobs WHERE id=$1',[candidate.body.id]);
 assert.equal(contract.contract_version,'v03');assert.equal(contract.queue,'training');
 const stream=await fetch(base+'/api/pipeline/events',{headers:{Authorization:`Bearer ${token}`,'Last-Event-ID':'0'},signal:controller.signal});assert.equal(stream.status,200);
 const reader=stream.body.getReader();let buffer='';const timeout=setTimeout(()=>controller.abort(),5000);
 while(!buffer.includes('\n\n')) {const value=await reader.read();assert(!value.done);buffer+=new TextDecoder().decode(value.value);}
 clearTimeout(timeout);assert(buffer.includes('event: decision'));assert(buffer.includes('"ruleStatus"'));assert(buffer.includes('"evidence"'));controller.abort();
 const invalid=await fetch(base+'/api/pipeline/events',{headers:{Authorization:`Bearer ${token}`,'Last-Event-ID':'9999999999999999999'}});assert.equal(invalid.status,400);
 console.log('PASS: user explanation requests, cached reuse, actor isolation, strict mode policy, saved-decision SSE, cursor bounds');
} finally {controller.abort();if(child && child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}await pool.end();await rm(directory,{recursive:true,force:true});}
