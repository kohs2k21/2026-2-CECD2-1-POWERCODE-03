import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {AuthSessionProvider,useAuthSession} from '../src/services/auth/AuthSessionProvider.tsx';

// Node's renderer does not consume styles; production compilation is checked separately.
const hooks=registerHooks({load(url,context,next){
  if(url.endsWith('.css'))return {format:'module',source:'',shortCircuit:true};
  return next(url,context);
}});
const {PipelineOperations}=await import('../src/features/pipeline/PipelineOperations.tsx');
globalThis.React=React;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const local=new Map([['token','actor-a-session']]),storage=new Map(),listeners=new Map();
const realSetInterval=globalThis.setInterval,realClearInterval=globalThis.clearInterval;
let intervalId=0;
globalThis.setInterval=()=>++intervalId;
globalThis.clearInterval=()=>{};
globalThis.window={location:{origin:'http://local.test'},
  localStorage:{getItem:k=>local.get(k)??null,setItem:(k,v)=>local.set(k,v),removeItem:k=>local.delete(k)},
  sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  addEventListener:(name,fn)=>{if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},
  removeEventListener:(name,fn)=>listeners.get(name)?.delete(fn),
  setTimeout,clearTimeout,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval,
};
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const posts=[];
let failNextPost=false,pendingPost=null,pendingFeatures=null;
const feature=(id,supported=true)=>({id,featureNames:['duration_ms'],contentHash:'fixture-hash',supported,reason:supported?null:'unsupported_feature_definition'});
const user=id=>({id,email:`${id}@example.test`,userType:'admin',createdAt:'2026-10-01T00:00:00Z'});
globalThis.fetch=async(raw,options={})=>{
  const url=new URL(raw,'http://local.test'),path=url.pathname;
  if(path==='/api/auth/me')return json({user:user(local.get('token')==='actor-a-session'?'actor-a':'actor-b')});
  if(path==='/api/jobs'&&options.method==='POST'){
    const input=JSON.parse(options.body),key=options.headers.get('Idempotency-Key');
    posts.push({input,key,actor:options.headers.get('Authorization')});
    if(failNextPost){failNextPost=false;throw new TypeError('network response lost');}
    if(pendingPost){const d=pendingPost;pendingPost=null;return d.promise;}
    return json({id:`job-${posts.length}`,kind:input.kind,status:'succeeded',inputSpec:input,
      resultSummary:input.kind==='train'?{configVersionId:'candidate-a',snapshotId:'snapshot-a'}:
      {evaluationId:'evaluation-a',configVersionId:input.configVersionId,snapshotId:input.snapshotId,applicability:'unmeasured'}});
  }
  if(path==='/api/jobs/capabilities')return json({contractVersion:'trainer-service-v1',models:[
    {key:'unsupported',displayName:'Unsupported model',supported:false},{key:'iforest',displayName:'Isolation Forest',supported:true}],evaluationFamilies:[]});
  if(path==='/api/jobs/feature-sets'){
    if(url.searchParams.get('sourceId')==='source-b'&&pendingFeatures){const d=pendingFeatures;pendingFeatures=null;return d.promise;}
    return json({items:url.searchParams.get('sourceId')==='source-a'?[feature('disabled-set',false),feature('features-a')]:[],hasMore:false});
  }
  if(path==='/api/pipeline/sources')return json({items:[{id:'source-a',datasetKey:'a',displayName:'Source A'},{id:'source-b',datasetKey:'b',displayName:'Source B'}]});
  if(path==='/api/configurations/active')return json({configVersionId:null,revision:0});
  if(path==='/api/pipeline/metrics')return json({queues:[],stages:[]});
  if(path==='/api/pipeline/events')return new Response(new ReadableStream({start(c){c.close();}}),{headers:{'content-type':'text/event-stream'}});
  if(path==='/api/inputs')return json({enabled:false,control:null,storedCounts:{},replays:[],configurations:[],csvResume:null});
  if(['/api/jobs','/api/pipeline/drift','/api/configurations/applications','/api/pipeline/decisions','/api/pipeline/windows','/api/pipeline/window-monitors','/api/pipeline/collector/metrics','/api/pipeline/collector','/api/pipeline/observations'].includes(path))return json({items:[]});
  throw new Error(`Unmocked endpoint ${path}`);
};
let session,renderer;
function Scope(){session=useAuthSession();return session.user?React.createElement(PipelineOperations,{key:`${session.user.id}:${session.user.userType}`}):null;}
const cache=new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false},mutations:{retry:false}}});
const text=node=>node==null?'':typeof node==='string'?node:Array.isArray(node)?node.map(text).join(' '):text(node.children);
const visible=()=>text(renderer.toJSON());
const tick=()=>act(async()=>{await new Promise(r=>setTimeout(r,10));});
const wait=async(check,label)=>{for(let i=0;i<50&&!check();i++)await tick();assert(check(),label);};
const button=label=>renderer.root.findAllByType('button').find(b=>text(b.children)===label);
const select=label=>renderer.root.findAllByType('label').find(l=>text(l.children).startsWith(label)).findByType('select');
const choose=(label,value)=>act(async()=>select(label).props.onChange({target:{value}}));
const click=label=>act(async()=>{const b=button(label);assert(b,`${label} exists`);assert(!b.props.disabled,`${label} enabled`);b.props.onClick();await Promise.resolve();});
try{
  await act(async()=>{renderer=create(React.createElement(QueryClientProvider,{client:cache},React.createElement(AuthSessionProvider,null,React.createElement(Scope))));});
  await wait(()=>visible().includes('입력 순서:'),'registered feature selection loaded');
  assert.equal(select('고정 피처셋').props.value,'features-a');
  assert.equal(select('모델').props.value,'iforest');
  assert.equal(select('고정 피처셋').findAllByType('option').find(o=>o.props.value==='disabled-set').props.disabled,true);
  assert.equal(button('선택 기간 학습 실행').props.disabled,true,'parallel mode requires rules and explicit combination');
  await choose('탐지 모드','ml_only');
  failNextPost=true;await click('선택 기간 학습 실행');
  assert(visible().includes('network response lost'));
  assert(!visible().includes('후보 구성:'));
  await click('선택 기간 학습 실행');
  assert.deepEqual(posts[0],posts[1],'ambiguous network retry keeps exact payload and key');
  assert.equal(posts[1].input.sourceId,'source-a');
  assert.equal(posts[1].input.featureSetId,'features-a');
  assert.equal(posts[1].input.modelKey,'iforest');
  assert.deepEqual(posts[1].input.ruleVersionIds,[]);
  assert.equal(posts[1].input.detectionPolicy.mode,'ml_only');
  await click('검증 분할 평가');
  assert.deepEqual(posts.at(-1).input,{kind:'evaluate',sourceId:'source-a',configVersionId:'candidate-a',snapshotId:'snapshot-a',purpose:'validation'});
  assert.equal(button('평가된 구성 적용 요청').props.disabled,true,'unmeasured evaluation cannot activate');
  const previousKey=posts[1].key;
  failNextPost=true;await click('새로 실행');const newKey=posts.at(-1).key;
  assert.notEqual(newKey,previousKey);
  await click('새로 실행');assert.equal(posts.at(-1).key,newKey,'new execution retry keeps key');
  // New completed job is now first; resetting an old job's successful action is tested at the key layer.
  assert(!visible().includes('network response lost'));
  const staleFeature=deferred();pendingFeatures=staleFeature;
  await choose('출처','source-b');
  assert.equal(button('선택 기간 학습 실행').props.disabled,true);
  await choose('출처','source-a');
  await wait(()=>select('고정 피처셋').props.value==='features-a','source A restored');
  await act(async()=>staleFeature.resolve(json({items:[feature('late-source-b')],hasMore:false})));
  assert.equal(select('고정 피처셋').props.value,'features-a','late source B catalog ignored');
  assert(!visible().includes('late-source-b'));
  const late=deferred();pendingPost=late;
  await click('선택 기간 학습 실행');
  await act(async()=>session.login({token:'actor-b-session',user:user('actor-b')}));
  await wait(()=>visible().includes('입력 순서:'),'actor B mounted fresh catalog');
  await act(async()=>late.resolve(json({id:'stale-actor-a-job',kind:'train',status:'succeeded',inputSpec:posts.at(-1).input,resultSummary:{configVersionId:'stale-actor-a-candidate',snapshotId:'stale-snapshot'}})));
  assert(!visible().includes('stale-actor-a-candidate'),'late previous actor result excluded');
  await choose('탐지 모드','ml_only');await click('선택 기간 학습 실행');
  assert.equal(posts.at(-1).actor,'Bearer actor-b-session');
  assert.notEqual(posts.at(-1).key,previousKey,'new actor receives isolated retry key');
  console.log('PASS: mounted catalog selection, disabled unsupported features/models, exact train/evaluation contract, ambiguous retry, explicit rerun, unmeasured activation block, stale source/user isolation');
}finally{
  if(renderer)await act(async()=>renderer.unmount());cache.clear();hooks.deregister();
  globalThis.setInterval=realSetInterval;globalThis.clearInterval=realClearInterval;
}
