import assert from 'node:assert/strict';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {InputControls} from '../src/features/pipeline/InputControls.tsx';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.React=React;
globalThis.window={location:{origin:'http://local.test'},localStorage:{getItem:()=> 'token'}};
const state={enabled:true,sourceId:'source',storedCounts:{P:1000,M:10,B:2},csvResume:null,
 control:{mode:'paused',revision:4,runId:null,configVersionId:null,status:null,settings:null,accepted:0,duplicates:0,unidentified:0,lastError:null,workerHeartbeatAt:null},
 replays:[{id:'replay',replayKey:'four-files',recordCount:1003,layerCounts:{P:1000}}],configurations:[{id:'candidate',mode:'parallel',createdAt:'2026-10-01T00:00:00Z'}]};
const calls=[];let failure=false;
globalThis.fetch=async(url,options)=>{
 assert.equal(options.headers.get('Authorization'),'Bearer token');
 if(options.method==='POST'){
  const body=JSON.parse(options.body);calls.push(body);
  if(failure)return new Response(JSON.stringify({code:'input_revision_conflict'}),{status:409});
  state.control={...state.control,mode:body.mode,revision:5,status:'running',configVersionId:body.configVersionId??state.control.configVersionId};
 }
 return new Response(JSON.stringify(state),{headers:{'Content-Type':'application/json'}});
};
let renderer;await act(async()=>{renderer=create(React.createElement(InputControls,{sourceId:'source'}));});
const selects=()=>renderer.root.findAllByType('select');
const apply=()=>renderer.root.findAllByType('button').find(b=>b.children.join('')==='선택한 입력 적용');
await act(async()=>{selects()[0].props.onChange({target:{value:'csv'}});selects()[1].props.onChange({target:{value:'candidate'}});});
assert.equal(apply().props.disabled,false);
await act(async()=>{await apply().props.onClick();});
assert.deepEqual(calls[0],{mode:'csv',expectedRevision:4,replayId:'replay',rate:10,repeat:1,restart:false,configVersionId:'candidate'});
const text=JSON.stringify(renderer.toJSON());assert(text.includes('CSV'));assert(text.includes('검증'));assert(text.includes('BODY'));
failure=true;
await act(async()=>{selects()[0].props.onChange({target:{value:'api'}});});
await act(async()=>{await apply().props.onClick();});
assert.equal(state.control.mode,'csv');assert(JSON.stringify(renderer.toJSON()).includes('다른 화면에서'));
const pause=renderer.root.findAllByType('button').find(b=>b.children.join('')==='입력 일시정지');
failure=false;await act(async()=>{await pause.props.onClick();});
assert(!('configVersionId' in calls.at(-1)));
await act(async()=>renderer.unmount());
console.log('PASS: manual input switch, retained candidate config, revision conflict without false success, pause does not apply draft config');
