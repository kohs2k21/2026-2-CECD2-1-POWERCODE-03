import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import express from 'express';
import {parseReportFilter,parseCandidateFilter,dailyReport,costReport,OPERATIONAL_SQL,COVERAGE_SQL,
  DETECTOR_COST_SQL,COHORT_SUMMARY_SQL,BACKLOG_SQL,BACKLOG_AT_CUTOFF_SQL} from '../dist/services/operationalReport.js';
import {hasStatusRulePolicy} from '../dist/services/statusRulePolicy.js';
import {JOB_COST_SQL,JOB_COST_SUMMARY_SQL} from '../dist/services/executionCostReport.js';

const temporary = await mkdtemp(path.join(os.tmpdir(),'powercode-report-test-'));
process.env.USER_DATA_PATH=path.join(temporary,'users.json');
process.env.JWT_SECRET='report-tests-only-local-secret';
for(const key of Object.keys(process.env)) if(key.startsWith('SEED_')) delete process.env[key];
const {createReportRouter}=await import('../dist/routes/reportRoutes.js');
after(()=>rm(temporary,{recursive:true,force:true}));
const sourceId='00000000-0000-4000-8000-000000000001';
const configVersionId='00000000-0000-4000-8000-000000000002';
const query={sourceId,configVersionId,from:'2026-10-01T00:00:00Z',until:'2026-10-03T00:00:00Z',timezone:'Asia/Seoul',scope:'live'};

test('filters reject rollover dates, injection, arrays, unknown keys, invalid timezone and unbounded reads',()=>{
  assert.equal(parseReportFilter(query).limit,31);
  for(const extra of [{from:'2026-02-30T00:00:00Z'},{from:'0000-01-01T00:00:00Z'},{from:'2026-10-01'},{until:query.from},{until:'2026-12-01T00:00:00Z'},
    {sourceId:"' OR true --"},{scope:'offline'},{timezone:'Asia/Seoul;DROP TABLE x'},{timezone:'Mars/Unknown'},
    {sourceId:[sourceId]},{configVersionId:[configVersionId]},{limit:'101'},{limit:'0'},{offset:'10001'},
    {offset:'1.1'},{limit:'01'},{extra:'ignored'}]) assert.throws(()=>parseReportFilter({...query,...extra}),/invalid|unknown/);
  assert.equal(parseReportFilter({...query,timezone:'America/New_York',limit:'100',offset:'10000'}).limit,100);
});
test('candidate cursor preserves microseconds and rejects additional fields or malformed cursor',()=>{
  const after={createdAt:'2026-10-02T00:00:00.123456Z',id:configVersionId};
  const cursor=Buffer.from(JSON.stringify(after)).toString('base64url');
  assert.deepEqual(parseCandidateFilter({sourceId,cursor}).after,after);
  for(const input of [{sourceId,cursor:'%'},{sourceId,offset:'0'},
    {sourceId,cursor:Buffer.from(JSON.stringify({...after,admin:true})).toString('base64url')},
    {sourceId,cursor:Buffer.from(JSON.stringify({...after,id:[configVersionId]})).toString('base64url')},
    {sourceId,cursor:Buffer.from(JSON.stringify({...after,createdAt:'2026-02-30T00:00:00Z'})).toString('base64url')}]) {
    assert.throws(()=>parseCandidateFilter(input),/invalid|unknown/);
  }
});
test('period validation preserves microsecond order and the exact 31-day bound',()=>{
  const period={...query,from:'2026-10-01T00:00:00.123456Z',until:'2026-10-01T00:00:00.123457Z'};
  assert.equal(parseReportFilter(period).until,period.until);
  assert.throws(()=>parseReportFilter({...period,from:period.until,until:period.from}),/invalid_report_period/);
  assert.equal(parseReportFilter({...query,until:'2026-11-01T00:00:00.000000Z'}).until,'2026-11-01T00:00:00.000000Z');
  assert.throws(()=>parseReportFilter({...query,until:'2026-11-01T00:00:00.000001Z'}),/invalid_report_period/);
  assert.equal(parseReportFilter({...query,from:'1969-12-31T23:59:59.999999Z',until:'1970-01-01T00:00:00Z'}).from,'1969-12-31T23:59:59.999999Z');
});
test('daily serializer uses decidable alarm denominator and distinct comparison coverage',()=>{
  const result=dailyReport({day:'2026-10-01',total:5,decidable:3,alarms:2,failed:1,pending:2,lastProcessedAt:'2026-10-01T12:00:00Z',
    comparisons:{hr:{comparable:2,both:1,ruleOnly:1,mlOnly:0,neither:0},sr:{comparable:0,both:0,ruleOnly:0,mlOnly:0,neither:0}}});
  assert.equal(result.alarmRatio.value,2/3);
  assert.equal(result.alarmRatio.numerator,2);assert.equal(result.alarmRatio.denominator,3);
  assert.equal(result.alarmRatio.coverage,3/5);assert.equal(result.unavailableRate.value,2/5);
  assert.equal(result.comparisons.hrMl.coverage,2/5);assert.equal(result.comparisons.hrMl.mlOnly.value,0);
  assert.equal(result.comparisons.srMl.both.value,null);assert.equal(result.comparisons.srMl.both.reason,'checker_evidence_unavailable');
  assert.equal(result.failedProcessCount.value,1);assert.equal(result.pendingArrivalCount.value,2);
});
test('pending zero is measured and SR pending experiment keeps the offline reason code',()=>{
  const base={day:'2026-10-01',total:3,decidable:3,alarms:0,failed:0,pending:0,lastProcessedAt:null};
  const zero=dailyReport({...base,comparisons:{sr:{comparable:0,both:0,ruleOnly:0,mlOnly:0,neither:0,srPending:3}}});
  assert.equal(zero.pendingArrivalCount.status,'measured');assert.equal(zero.pendingArrivalCount.value,0);
  assert.equal(zero.pendingArrivalCount.reason,null);
  assert.equal(zero.comparisons.srMl.both.reason,'sr_experiment_pending');
  // Only part of the SR evidence names the pending experiment: generic missing-evidence code stays.
  const mixed=dailyReport({...base,comparisons:{sr:{comparable:0,both:0,ruleOnly:0,mlOnly:0,neither:0,srPending:2}}});
  assert.equal(mixed.comparisons.srMl.both.reason,'checker_evidence_unavailable');
  assert.equal(mixed.comparisons.hrMl.both.reason,'checker_evidence_unavailable');
  const missing=dailyReport({...base,pending:undefined,comparisons:null});
  assert.equal(missing.pendingArrivalCount.value,null);assert.equal(missing.pendingArrivalCount.reason,'pending_count_unavailable');
});
test('empty cohort and missing historical telemetry are unavailable; measured zero survives',()=>{
  const empty=dailyReport({day:'2026-10-01',total:0,decidable:0,alarms:0,failed:0,pending:0,lastProcessedAt:null,comparisons:null});
  assert.equal(empty.processCount.value,null);assert.equal(empty.alarmRatio.reason,'no_targets');
  assert.equal(empty.unavailableRate.value,null);assert.equal(empty.comparisons.hrMl.neither.value,null);
  assert.equal(costReport([],3).inferenceMs.p50.reason,'telemetry_unavailable');
  assert.equal(costReport([],0).inferenceMs.p50.reason,'no_targets');
  const costs=costReport([{key:'inferenceMs',samples:2,sum:0,p50:0,p95:0}],3);
  assert.equal(costs.inferenceMs.p95.value,0);assert.equal(costs.inferenceMs.p95.coverage,2/3);
  assert.equal(costs.processRssBytes.sum,undefined);
});
test('pinned source policy permits exactly the fixed mapping and rejects malformed or foreign policy',()=>{
  const statusRulePolicy={version:'process-status-hr-v1',sourceId,mapping:{S:'no_hit',F:'critical',P:'warning',N:'warning'}};
  assert.equal(hasStatusRulePolicy({statusRulePolicy},sourceId),true);
  for(const policy of [undefined,{}, {...statusRulePolicy,version:'process-status-hr-v2'},
    {...statusRulePolicy,sourceId:configVersionId},{...statusRulePolicy,extra:true},
    {...statusRulePolicy,mapping:{...statusRulePolicy.mapping,X:'no_hit'}}]) {
    assert.equal(hasStatusRulePolicy({statusRulePolicy:policy},sourceId),false);
  }
});

async function harness(user,queryHandler) {
  const calls=[];let connects=0,releases=0;
  const app=express();
  if(user)app.use((req,_res,next)=>{req.user={id:'test',email:'test@example.test',userType:user};next();});
  app.use('/reports',createReportRouter(()=>({connect:async()=>{connects++;return {
    query:async(sql,args)=>{calls.push({sql,args});return queryHandler(sql,args);},release:()=>releases++};}})));
  app.use((_error,_req,res,_next)=>res.status(500).json({code:'test_database_error'}));
  const server=await new Promise(resolve=>{const value=app.listen(0,'127.0.0.1',()=>resolve(value));});
  return {calls,connects:()=>connects,releases:()=>releases,base:`http://127.0.0.1:${server.address().port}`,
    close:()=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();})};
}
const url='/reports/operational?'+new URLSearchParams(query);
test('report routes require authentication and admin before database access',async()=>{
  for(const [user,status]of [[null,401],['user',403]]) {
    const h=await harness(user,()=>{throw new Error('must not connect');});
    try {assert.equal((await fetch(h.base+url)).status,status);assert.equal(h.connects(),0);} finally {await h.close();}
  }
});
test('admin validation rejects bad request before connecting and reports missing source/configuration',async()=>{
  const h=await harness('admin',async sql=>({rowCount:0,rows:[]}));
  try {
    assert.equal((await fetch(h.base+url+'&limit=5000')).status,400);assert.equal(h.connects(),0);
    assert.equal((await fetch(h.base+url)).status,404);assert.equal(h.releases(),1);
    assert(h.calls.some(call=>call.sql==='ROLLBACK'));
  }finally{await h.close();}
});
test('operational HTTP response keeps missing values, query parameters, snapshots and cutoff explicit',async()=>{
  const h=await harness('admin',async(sql,args)=>{
    const rows=sql===OPERATIONAL_SQL ? [{day:'2026-10-01',total:2,decidable:1,alarms:1,failed:0,pending:1,
      comparisons:null,lastProcessedAt:'2026-10-01T01:00:00Z'}] :
      sql===COVERAGE_SQL ? [{unknownScopeObservations:1,missingIdentityObservations:0}] :
      sql===COHORT_SUMMARY_SQL ? [{decisionCount:2,totalDays:3}] :
      sql===BACKLOG_SQL ? [{targets:2,queued:0,running:0,failed:0,oldestWaitSeconds:null}] :
      sql===BACKLOG_AT_CUTOFF_SQL ? [{asOf:query.until,waiting:1,running:0,oldestWaitSeconds:'30.5'}] :
      sql===DETECTOR_COST_SQL ? [] : sql.includes('clock_timestamp() AS') ? [{generatedAt:'2026-10-03T00:00:01Z'}] : [{id:configVersionId}];
    return {rowCount:rows.length,rows};
  });
  try {
    const response=await fetch(h.base+url),body=await response.json();assert.equal(response.status,200);
    assert.equal(body.reportScope,'live');assert.equal(body.cutoff,query.until);
    assert.equal(body.items[0].alarmRatio.value,1);assert.equal(body.items[0].unavailableRate.value,.5);
    assert.equal(body.executionCost.metrics.inferenceMs.p95.reason,'telemetry_unavailable');
    assert.equal(body.externalNotificationCount.value,null);assert.equal(body.esbOccurrenceToDecisionLatency.value,null);
    assert.equal(body.queue.queued.value,0);assert.equal(body.pagination.total,3);
    // Current snapshot keys stay; the cutoff reconstruction is reported separately.
    assert.equal(body.queue.atCutoff.asOf,query.until);assert.equal(body.queue.atCutoff.waiting.value,1);
    assert.equal(body.queue.atCutoff.running.status,'measured');assert.equal(body.queue.atCutoff.running.value,0);
    assert.equal(body.queue.atCutoff.oldestWaitSeconds.value,30.5);
    assert.deepEqual(h.calls.find(call=>call.sql===BACKLOG_AT_CUTOFF_SQL).args,[sourceId,configVersionId,query.from,query.until,query.timezone,'live']);
    assert.deepEqual(h.calls.find(call=>call.sql===OPERATIONAL_SQL).args,[sourceId,configVersionId,query.from,query.until,query.timezone,'live',31,0]);
    assert.equal(h.calls[0].sql,'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');assert.equal(h.calls.at(-1).sql,'COMMIT');
    assert.equal(h.releases(),1);
  }finally{await h.close();}
});
test('database error rolls back and releases the report connection',async()=>{
  const h=await harness('admin',async sql=>{if(sql===OPERATIONAL_SQL)throw new Error('failed');return {rowCount:1,rows:[{}]};});
  try {assert.equal((await fetch(h.base+url)).status,500);assert(h.calls.some(call=>call.sql==='ROLLBACK'));assert.equal(h.releases(),1);}
  finally {await h.close();}
});
test('candidate HTTP keyset exposes saved provenance and a usable microsecond cursor',async()=>{
  const createdAt='2026-10-01T00:00:00.123456Z';
  const h=await harness('admin',async(sql,args)=>({rowCount:2,rows:[
    {configVersionId,createdAt,cursorCreatedAt:createdAt,configurationSpec:{trainingSelection:{policyVersion:'s-only-v1'}},latestEvaluation:null},
    {configVersionId:sourceId,createdAt,cursorCreatedAt:createdAt}]}));
  try {
    const body=await(await fetch(h.base+'/reports/candidates?'+new URLSearchParams({sourceId,limit:'1'}))).json();
    assert.equal(body.items.length,1);assert.equal(body.pagination.hasMore,true);assert.equal(body.manualApprovalRequired,true);
    const after=parseCandidateFilter({sourceId,cursor:body.pagination.nextCursor}).after;assert.equal(after.createdAt,createdAt);
    assert.deepEqual(body.items[0].configurationSpec.trainingSelection,{policyVersion:'s-only-v1'});
    assert.equal(body.items[0].latestEvaluation,null);assert.equal('cursorCreatedAt' in body.items[0],false);
  }finally{await h.close();}
});
test('offline cost HTTP uses finished jobs with explicit batch conditions and missing metric coverage',async()=>{
  const conditions={clock:'monotonic',batchSize:4,concurrency:1,warmCold:'mixed_unclassified',memoryScope:'process_current_rss_sample'};
  const h=await harness('admin',async sql=>({rowCount:1,rows:sql===JOB_COST_SQL ? [{kind:'train',key:'trainMs',samples:1,sum:7,p50:7,p95:7}] :
    sql===JOB_COST_SUMMARY_SQL ? [{kind:'train',total:2,failed:1,lastFinishedAt:'2026-10-01T01:00:00Z',conditions:[conditions]}] :
      sql.includes('clock_timestamp() AS') ? [{generatedAt:'2026-10-03T00:00:01Z'}] : [{}]}));
  try {
    const url='/reports/execution-cost?'+new URLSearchParams({...query,scope:'offline'});
    const response=await fetch(h.base+url),body=await response.json();assert.equal(response.status,200);
    assert.equal(body.reportScope,'offline');assert.equal(body.items[0].metrics.trainMs.p95.value,7);
    assert.equal(body.items[0].metrics.trainMs.p95.coverage,.5);assert.deepEqual(body.items[0].conditions,[conditions]);
    assert.equal(body.items[1].metrics.evaluationMs.p95.value,null);assert.equal(body.items[1].metrics.evaluationMs.p95.reason,'no_targets');
    assert.equal(body.items[0].metrics.inferenceMs.p95.reason,'telemetry_unavailable');
    assert.deepEqual(h.calls.find(call=>call.sql===JOB_COST_SUMMARY_SQL).args,[sourceId,configVersionId,query.from,query.until]);
    assert.equal((await fetch(h.base+url+'&offset=1')).status,400);
  }finally{await h.close();}
});
