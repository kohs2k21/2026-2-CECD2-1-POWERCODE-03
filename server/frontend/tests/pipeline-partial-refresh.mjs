import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {AuthSessionProvider,useAuthSession} from '../src/services/auth/AuthSessionProvider.tsx';
import {HttpError} from '../src/services/api/client.ts';
import {emptyResultVisible,initialRefreshStatus,mergeRefreshStatus,refreshAlertText,refreshKeys,rows,trackRefresh} from '../src/features/pipeline/pipelineRefresh.ts';

const unhandled=[];
process.on('unhandledRejection',reason=>unhandled.push(reason));
// 1) 순수 반영 로직: 조회마다 끝나는 즉시 반영하고, 실패·멈춤·applier 예외가 다른 조회 반영을 막지 않는다.
const describe=reason=>reason instanceof HttpError&&reason.code?reason.code:reason instanceof Error?reason.message:'unknown';
const payload={sources:[{id:'s'}],jobs:[{id:'j'}],active:{configVersionId:'c',revision:1},metrics:{queues:[],stages:[]},
  drift:[{featureName:'f'}],applied:[{id:'a'}],decisions:[{id:'d'}],windows:[{id:'w'}],monitors:[{id:'m'}]};
const loaders=(fail=[])=>Object.fromEntries(refreshKeys.map(key=>[key,async()=>{
  if(fail.includes(key))throw new HttpError(`GET ${key} failed: 502`,502,'HTTP_502');return payload[key];}]));
const noop=keys=>Object.fromEntries(keys.map(key=>[key,()=>{}]));
const settle=(source,appliers=noop(Object.keys(source)),records=[])=>trackRefresh(source,appliers,describe,outcome=>records.push(outcome));
const state={windows:['previous-window']},applied=[],records=[];
let outcome=await settle(loaders(['windows']),Object.fromEntries(refreshKeys.map(key=>[key,value=>{applied.push(key);state[key]=value;}])),records);
assert.deepEqual(Object.keys(outcome.values).sort(),refreshKeys.filter(key=>key!=='windows').sort(),'8 successful queries are kept');
assert.deepEqual(outcome.failures,{windows:'HTTP_502'});
assert.deepEqual(applied.sort(),refreshKeys.filter(key=>key!=='windows').sort(),'only successful appliers run');
assert.deepEqual(state.windows,['previous-window'],'failed query keeps previous success value');
assert.deepEqual(state.active,payload.active);
assert.equal(records.length,9,'each query is recorded separately');
assert(records.every(item=>Object.keys(item.values).length+Object.keys(item.failures).length===1));
// 응답이 오지 않는 조회가 있어도 나머지는 즉시 반영·기록된다.
let release;const stalled=new Promise(resolve=>{release=resolve;});
const early=[],earlyApplied=[];let finished=false;
const pending=settle({...loaders(),windows:()=>stalled},Object.fromEntries(refreshKeys.map(key=>[key,()=>earlyApplied.push(key)])),early).then(result=>{finished=true;return result;});
for(let i=0;i<5;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));
assert.deepEqual(earlyApplied.sort(),refreshKeys.filter(key=>key!=='windows').sort(),'8 queries applied while windows is pending');
assert.equal(early.length,8,'8 queries recorded while windows is pending');
assert.equal(finished,false,'overall promise still waits for every query');
release([]);
assert.deepEqual(Object.keys((await pending).values).sort(),[...refreshKeys].sort());
// applier 예외(응답 형태 이상)는 그 식별자만 실패로 기록하고 reject하지 않는다.
const brokenRecords=[];let brokenReason;
const broken=await trackRefresh({...loaders(),metrics:async()=>({stages:[]})},{...noop(refreshKeys),metrics:value=>{rows(value.queues);}},
  reason=>{brokenReason=reason;return describe(reason);},item=>brokenRecords.push(item));
assert.deepEqual(Object.keys(broken.failures),['metrics']);
assert(brokenReason instanceof TypeError,'applier shape error is a TypeError');
assert.equal(broken.failures.metrics,describe(brokenReason),'failure keeps errorText format');
assert.equal(Object.keys(broken.values).length,8);
assert.equal(brokenRecords.length,9);
assert.throws(()=>rows(undefined),TypeError);
assert.throws(()=>rows({}),TypeError);
const nullActive=await settle({active:async()=>null});
assert.ok(Object.hasOwn(nullActive.values,'active'),'null success is still a success');
const thrown=await settle({sources:()=>{throw new Error('sync failure');}});
assert.deepEqual(thrown.failures,{sources:'sync failure'},'synchronous throw is captured per query');
let status=mergeRefreshStatus(initialRefreshStatus,outcome);
assert.equal(status.loaded.windows,undefined);
assert.equal(status.failures.windows,'HTTP_502');
assert.equal(refreshAlertText(status,null),'windows: HTTP_502');
assert.equal(refreshAlertText(status,'explain_conflict'),'windows: HTTP_502 · explain_conflict','action error is kept beside query failure');
// 활성 구성·출처: 미조회·실패 상태에서는 '없음' 표시 조건이 거짓이다.
for(const key of ['active','sources']){
  assert.equal(emptyResultVisible(initialRefreshStatus,key),false,`${key}: never loaded`);
  const failed=mergeRefreshStatus(initialRefreshStatus,await settle(loaders([key])));
  assert.equal(emptyResultVisible(failed,key),false,`${key}: first query failed`);
  const loaded=mergeRefreshStatus(failed,await settle(loaders()));
  assert.equal(emptyResultVisible(loaded,key),true,`${key}: loaded`);
  assert.equal(loaded.failures[key],undefined,`${key}: recovered failure is cleared`);
  const failedAgain=mergeRefreshStatus(loaded,await settle(loaders([key])));
  assert.equal(emptyResultVisible(failedAgain,key),false,`${key}: failed after success`);
}
status=mergeRefreshStatus(status,await settle({active:async()=>{throw new Error('active down');}}));
assert.equal(refreshAlertText(status,null),'active: active down · windows: HTTP_502','failures follow fixed key order');
status=mergeRefreshStatus(status,await settle({windows:async()=>[]}));
assert.equal(status.failures.active,'active down','partial update leaves other keys untouched');
assert.equal(refreshAlertText(status,null),'active: active down');
status=mergeRefreshStatus(status,await settle(loaders()));
assert.equal(refreshAlertText(status,null),null,'all recovered');

// 2) 화면 반영: 실제 PipelineOperations를 렌더해 조회별 반영과 '없음' 표시 조건을 확인한다.
const hooks=registerHooks({load(url,context,next){
  if(url.endsWith('.css'))return {format:'module',source:'',shortCircuit:true};
  return next(url,context);
}});
const {PipelineOperations}=await import('../src/features/pipeline/PipelineOperations.tsx');
globalThis.React=React;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const local=new Map([['token','local-test-session']]),storage=new Map(),listeners=new Map(),intervals=new Map();
const realSetInterval=globalThis.setInterval,realClearInterval=globalThis.clearInterval;
let intervalId=0;
globalThis.setInterval=(fn,ms)=>{intervals.set(++intervalId,{fn,ms});return intervalId;};
globalThis.clearInterval=id=>{intervals.delete(id);};
globalThis.window={location:{origin:'http://local.test'},
  localStorage:{getItem:k=>local.get(k)??null,setItem:(k,v)=>local.set(k,v),removeItem:k=>local.delete(k)},
  sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  addEventListener:(name,fn)=>{if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},
  removeEventListener:(name,fn)=>listeners.get(name)?.delete(fn),
  setTimeout,clearTimeout,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval,
};
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
const badGateway=()=>new Response('<html>bad gateway</html>',{status:502,headers:{'content-type':'text/html'}});
const admin={id:'admin',email:'admin@example.test',userType:'admin',createdAt:'2026-10-01T00:00:00Z'};
const endpoints={sources:'/api/pipeline/sources',jobs:'/api/jobs',active:'/api/configurations/active',metrics:'/api/pipeline/metrics',
  drift:'/api/pipeline/drift',applied:'/api/configurations/applications',decisions:'/api/pipeline/decisions',
  windows:'/api/pipeline/windows',monitors:'/api/pipeline/window-monitors'};
const filled={
  sources:{items:[{id:'source-a',datasetKey:'a',displayName:'Source A'}]},
  jobs:{items:[{id:'train-job-1',kind:'train',status:'succeeded',inputSpec:{sourceId:'source-a'},resultSummary:{configVersionId:'candidate-a'}}]},
  active:{configVersionId:'active-config-1',revision:3,detectorState:{}},
  metrics:{queues:[{queue:'detect',status:'pending',count:7}],stages:[]},
  drift:{items:[{featureName:'duration_ms',baselineMissingRate:0.1,recentMissingRate:0.2,reviewRequired:false,reasonCode:null,measuredAt:'2026-10-20T00:00:00Z'}]},
  applied:{items:[{id:'application-1',targetConfigVersionId:'previous-config-1',evaluationId:'evaluation-1',activatedAt:'2026-10-19T00:00:00Z',status:'succeeded'}]},
  decisions:{items:[{id:'decision-1',sourceId:'source-a',processObservationId:'observation-1',configVersionId:'active-config-1',status:'complete',
    ruleStatus:'complete',mlStatus:'complete',decisionCode:'DECISION_FIXTURE',anomalyScore:null,decidedAt:'2026-10-20T00:00:00Z'}]},
  windows:{items:[]},
  monitors:{items:[{id:'monitor-1',algorithm:'monitor-fixture',enabled:true,lastError:null}]},
};
const empty={...Object.fromEntries(Object.keys(endpoints).map(key=>[key,{items:[]}])),
  active:{configVersionId:null,revision:0,detectorState:{}},metrics:{queues:[],stages:[]}};
let bodies=filled,failing=new Set(),failExplain=false,overrides={};const stalls=new Map();
globalThis.fetch=async(raw,options={})=>{
  const url=new URL(raw,'http://local.test'),path=url.pathname;
  if(path==='/api/auth/me')return json({user:admin});
  if(path==='/api/jobs'&&options.method==='POST'){
    if(failExplain)return json({message:'conflict',code:'explain_conflict'},409);
    return json({id:'explain-job',kind:'explain',status:'succeeded',inputSpec:JSON.parse(options.body)});
  }
  const key=Object.keys(endpoints).find(name=>endpoints[name]===path);
  if(key)return stalls.has(key)?stalls.get(key).promise:failing.has(key)?badGateway():json(overrides[key]??bodies[key]);
  if(path==='/api/jobs/capabilities')return json({contractVersion:'trainer-service-v1',models:[],evaluationFamilies:[]});
  if(path==='/api/jobs/feature-sets')return json({items:[],hasMore:false});
  if(path==='/api/pipeline/events')return new Response(new ReadableStream({start(c){c.close();}}),{headers:{'content-type':'text/event-stream'}});
  if(path==='/api/inputs')return json({enabled:false,control:null,storedCounts:{},replays:[],configurations:[],csvResume:null});
  if(['/api/pipeline/collector/metrics','/api/pipeline/collector','/api/pipeline/observations'].includes(path))return json({items:[]});
  throw new Error(`Unmocked endpoint ${path}`);
};
const failure=key=>`${key}: GET ${endpoints[key]} failed: 502`;
let renderer;
function Scope(){const session=useAuthSession();return session.user?React.createElement(PipelineOperations):null;}
const cache=new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false},mutations:{retry:false}}});
const text=node=>node==null?'':typeof node==='string'?node:Array.isArray(node)?node.map(text).join(' '):text(node.children);
const visible=()=>text(renderer.toJSON());
const flat=()=>visible().replace(/\s+/g,'');
const alert=()=>{const nodes=renderer.root.findAll(node=>node.type==='p'&&node.props.role==='alert');return nodes.length?text(nodes[0].children):'';};
const tick=()=>act(async()=>{await new Promise(r=>setTimeout(r,10));});
const wait=async(check,label)=>{for(let i=0;i<50&&!check();i++)await tick();assert(check(),label);};
const button=label=>renderer.root.findAllByType('button').find(b=>text(b.children)===label);
const click=async label=>{const b=button(label);assert(b,`${label} exists`);await act(async()=>{b.props.onClick();await Promise.resolve();});};
const runIntervals=async ms=>{for(const {fn,ms:delay} of [...intervals.values()])if(delay===ms)await act(async()=>{fn();});await tick();};
const mount=async()=>{await act(async()=>{renderer=create(React.createElement(QueryClientProvider,{client:cache},React.createElement(AuthSessionProvider,null,React.createElement(Scope))));});};
const unmount=async()=>{if(renderer)await act(async()=>renderer.unmount());renderer=null;intervals.clear();};
const reflected={sources:'SourceA(a)',jobs:'작업ID:train-job-1',active:'현재활성구성:active-config-1',metrics:'detect·pending:7건',
  drift:'duration_ms:기준0.1→최근0.2',applied:'previous-config-1',decisions:'DECISION_FIXTURE',monitors:'monitor-fixture'};
const emptyTexts=['평가 적합성','저장된 데이터 출처가 없습니다','현재 활성 구성','등록된 작업이 없습니다','측정 결과가 없습니다','저장된 판정이 없습니다','등록된 시간창 감시가 없습니다'];
try{
  // windows 502 한 건이 나머지 8건 반영을 막지 않는다.
  failing=new Set(['windows']);
  await mount();
  await wait(()=>alert().includes(failure('windows')),'windows failure is identified');
  const screen=flat();
  assert(screen.includes('SourceA(a)'),'sources reflected');
  assert(screen.includes('작업ID:train-job-1'),'jobs reflected');
  assert(screen.includes('현재활성구성:active-config-1'),'active reflected');
  assert(screen.includes('detect·pending:7건'),'metrics reflected');
  assert(screen.includes('duration_ms:기준0.1→최근0.2'),'drift reflected');
  assert(screen.includes('previous-config-1'),'applied reflected');
  assert(screen.includes('DECISION_FIXTURE'),'decisions reflected');
  assert(screen.includes('monitor-fixture'),'monitors reflected');
  assert.equal(alert(),failure('windows'),'only the failed query is listed');
  // 이미 성공한 값은 이후 refresh 실패로 지워지지 않는다.
  failing=new Set(['active','sources']);
  await click('새로고침');
  await wait(()=>alert().includes(failure('active')),'active failure shown');
  assert.equal(alert(),`${failure('sources')} · ${failure('active')}`,'recovered windows failure is cleared');
  assert(flat().includes('현재활성구성:active-config-1'),'previous active kept');
  assert(flat().includes('SourceA(a)'),'previous sources kept');
  // 사용자 동작 오류는 조회 갱신으로 지워지거나 덮어쓰이지 않는다.
  failExplain=true;
  await click('저장된 근거 설명 요청');
  await wait(()=>alert().endsWith('explain_conflict'),'action error shown');
  failing=new Set();
  await click('새로고침');
  await wait(()=>!alert().includes('active:'),'query failures cleared after recovery');
  assert.equal(alert(),'explain_conflict','action error kept after refresh');
  // 주기 갱신(windows/monitors)도 식별자별로 기록하고 복구되면 지운다.
  failing=new Set(['monitors']);
  await runIntervals(3000);
  await wait(()=>alert().includes(failure('monitors')),'periodic monitor failure shown');
  assert.equal(alert(),`${failure('monitors')} · explain_conflict`);
  assert(visible().includes('monitor-fixture'),'periodic failure keeps previous monitors');
  failing=new Set();
  await runIntervals(3000);
  await wait(()=>alert()==='explain_conflict','periodic recovery clears failure');
  await unmount();

  // 첫 조회 실패 시 활성 구성·출처 등 '없음' 표시가 나오지 않는다.
  failExplain=false;bodies=empty;
  failing=new Set(['sources','active','jobs','drift','decisions','monitors']);
  await mount();
  await wait(()=>alert().includes(failure('monitors')),'first-load failures shown');
  for(const phrase of emptyTexts)assert(!visible().includes(phrase),`hidden while unknown: ${phrase}`);
  assert.equal(alert(),['sources','jobs','active','drift','decisions','monitors'].map(failure).join(' · '));
  // 복구 후 실제 빈 결과는 기존 '없음' 문구로 표시된다.
  failing=new Set();
  await click('새로고침');
  await wait(()=>alert()==='','all failures cleared');
  for(const phrase of emptyTexts)assert(visible().includes(phrase),`shown after empty success: ${phrase}`);
  assert(flat().includes('현재활성구성:없음'),'null active after success shows existing text');
  assert(flat().includes('평가적합성:미평가'),'no evaluate job after jobs success shows existing text');
  // 구성 없음({configVersionId:null}) 성공 뒤 active 조회가 실패하면 '없음'을 표시하지 않는다(출처·drift 빈 결과도 같음).
  failing=new Set(['active','sources','drift']);
  await click('새로고침');
  await wait(()=>alert().includes(failure('active')),'active failure after empty success shown');
  assert.equal(alert(),['sources','active','drift'].map(failure).join(' · '));
  for(const phrase of ['현재 활성 구성','저장된 데이터 출처가 없습니다','측정 결과가 없습니다'])assert(!visible().includes(phrase),`hidden while failing after empty success: ${phrase}`);
  failing=new Set();
  await click('새로고침');
  await wait(()=>alert()==='','recovered after empty success');
  assert(flat().includes('현재활성구성:없음'));
  // 구성 있음 성공 뒤 active 조회가 실패하면 직전 configVersionId를 계속 표시한다.
  bodies=filled;
  await click('새로고침');
  await wait(()=>flat().includes(reflected.active),'configured active loaded');
  failing=new Set(['active']);
  await click('새로고침');
  await wait(()=>alert()===failure('active'),'configured active failure shown');
  assert(flat().includes(reflected.active),'configured active kept while failing');
  failing=new Set();
  await unmount();

  // windows 응답이 오지 않아도(pending) 나머지 8건은 도착 즉시 화면에 반영된다.
  bodies=filled;failing=new Set();
  let releaseWindows;stalls.set('windows',{promise:new Promise(resolve=>{releaseWindows=resolve;})});
  await mount();
  await wait(()=>Object.values(reflected).every(item=>flat().includes(item)),'8 queries reflected while windows is pending');
  assert.equal(alert(),'','pending query is not reported as failure');
  stalls.delete('windows');
  await act(async()=>releaseWindows(json(filled.windows)));
  await tick();
  assert.equal(alert(),'');
  await unmount();

  // 깨진 metrics 응답(queues 없음)은 metrics만 실패로 표시하고 나머지는 반영한다. 주기 경로도 같다.
  overrides={metrics:{stages:[]}};
  await mount();
  await wait(()=>alert().startsWith('metrics: '),'broken metrics recorded as failure');
  assert.match(alert(),/^metrics: [^·]+$/,'only metrics fails');
  for(const [key,item] of Object.entries(reflected))if(key!=='metrics')assert(flat().includes(item),`${key} reflected beside broken metrics`);
  overrides={monitors:{}};
  await runIntervals(3000);
  await wait(()=>alert().includes('monitors: '),'broken periodic monitors recorded');
  assert(flat().includes('monitor-fixture'),'broken periodic response keeps previous monitors');
  overrides={};
  await click('새로고침');
  await wait(()=>alert()==='','broken responses recover');
  assert(flat().includes(reflected.metrics));
  await tick();
  assert.deepEqual(unhandled,[],'no unhandled rejection');
  console.log('PASS: per-query immediate refresh keeps 8 results on windows 502 or pending windows, keeps prior values on failure, broken responses fail only their key, hides empty text while unknown/failed, clears recovered failures, keeps action errors, periodic windows/monitors tracked, no unhandled rejection');
}finally{
  await unmount();cache.clear();hooks.deregister();
  globalThis.setInterval=realSetInterval;globalThis.clearInterval=realClearInterval;
}
