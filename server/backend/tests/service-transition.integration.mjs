import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import net from 'node:net';
import pg from 'pg';
import {startFakeLlmHealth} from './fake-llm-health.mjs';

const url=process.env.PIPELINE_TEST_DATABASE_URL;
const parsed=new URL(url);
assert(['localhost','127.0.0.1'].includes(parsed.hostname) && parsed.pathname==='/qa_research_pipeline');
const directory=await mkdtemp(path.join(os.tmpdir(),'pwc-transition-api-'));
const pool=new pg.Pool({connectionString:url});
const backend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
let child;
// 설명 요청은 LLM 생성 서비스 가용성을 먼저 본다(llm-recommendation-job.md '생성 서비스 가용성' 절). 켜진 상태의 가짜 /health.
const llmHealth=await startFakeLlmHealth('ready');
const controller=new AbortController();
try {
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));
 child=spawn(process.execPath,[path.join(backend,'dist/index.js')],{cwd:directory,stdio:'ignore',env:{PATH:process.env.PATH,
  DATABASE_URL:url,JWT_SECRET:'local-transition-secret',USER_DATA_PATH:path.join(directory,'users.json'),PORT:String(port),
  SEED_ADMIN_EMAIL:'admin@example.test',SEED_ADMIN_PASSWORD:'test-password',SEED_USER_EMAIL:'user@example.test',SEED_USER_PASSWORD:'test-password',LLM_HEALTH_URL:llmHealth.url}});
 const base=`http://127.0.0.1:${port}`;
 for(let i=0;i<50;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
 const login=async email=>(await (await fetch(base+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password:'test-password'})})).json()).token;
 const token=await login('user@example.test');const admin=await login('admin@example.test');assert(token && admin);
 // 설명(다듬기)은 이상 신호가 있는 v4 판정만 접수한다. 가장 최근 그런 판정(없으면 설명 단계를 건너뜀).
 const {rows:[decision]}=await pool.query(`SELECT d.id,o.source_id FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id
  WHERE d.status<>'unavailable' AND d.evidence->'schemaVersion'='4'::jsonb AND d.evidence->'composite'->'anomalous'='true'::jsonb ORDER BY d.decided_at DESC LIMIT 1`);
 const call=async(token,input)=>{const res=await fetch(base+'/api/jobs',{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(input)});return {status:res.status,body:await res.json()};};
 let explainSkipped=false;
 if (decision) {
  const input={kind:'explain',sourceId:decision.source_id,decisionId:decision.id};
  const first=await call(token,input);assert.equal(first.status,201,JSON.stringify(first.body));
  const repeated=await call(token,input);assert.equal(repeated.status,200);assert.equal(first.body.id,repeated.body.id);
  // 재사용 키에 요청자가 없다: 다른 계정도 같은 작업을 받는다(판정마다 공유)
  const otherActor=await call(admin,input);assert.equal(otherActor.status,200);assert.equal(otherActor.body.id,first.body.id);
  const {rows:[lineage]}=await pool.query('SELECT queue,llm_spec,input_spec,max_attempts FROM run_jobs WHERE id=$1',[first.body.id]);
  assert.equal(lineage.queue,'llm_explanation');assert.equal(lineage.llm_spec.evidence_version,'summary-sentences-v1');assert.equal(lineage.max_attempts,1);
  assert(Array.isArray(lineage.input_spec.sentences) && lineage.input_spec.sentences.length>0);assert(!('savedEvidenceJson' in lineage.input_spec));
 } else {
  // 건너뜀을 드러낸다: 경고 + 마지막 PASS 줄에 표시(설명 접수는 analysis.integration이 fixture 판정으로 늘 시험한다)
  console.warn('WARN explain intake not exercised here: no anomalous v4 decision in this database (covered by analysis.integration)');
  explainSkipped = true;
 }
 const {rows:[anyDecision]}=await pool.query(`SELECT o.source_id FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id ORDER BY d.decided_at DESC LIMIT 1`);
 const metrics=await fetch(base+`/api/pipeline/collector/metrics?sourceId=${anyDecision.source_id}`,{headers:{Authorization:`Bearer ${token}`}});assert.equal(metrics.status,403);
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
 console.log((explainSkipped ? 'PASS (explain intake SKIPPED): ' : 'PASS: user explanation requests, cached reuse shared across actors, ') + ' strict mode policy, saved-decision SSE, cursor bounds');
} finally {controller.abort();if(child && child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}await llmHealth.close();await pool.end();await rm(directory,{recursive:true,force:true});}
