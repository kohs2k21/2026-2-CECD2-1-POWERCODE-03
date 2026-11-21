import assert from 'node:assert/strict';
import {EventEmitter, once} from 'node:events';
import http from 'node:http';
import test from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {streamDecisionEvents} from '../dist/services/decisionStream.js';

function deferred() {
  let resolve;
  const promise=new Promise(done=>{resolve=done;});
  return {promise,resolve};
}

async function harness(options) {
  const tasks=[],errors=[],responses=[];
  let backpressure=0,afterEnd=0;
  const server=http.createServer({highWaterMark:1024},(req,res)=>{
    if(req.url==='/health'){res.end('ready');return;}
    responses.push(res);
    const original=res.write.bind(res);
    res.write=(...args)=>{
      if(res.writableEnded)afterEnd++;
      const ok=original(...args);
      if(!ok)backpressure++;
      return ok;
    };
    tasks.push(streamDecisionEvents(res,{after:'0',pollMs:10,authorizeMs:0,...options}).catch(error=>{errors.push(error);res.destroy();}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {
    base:`http://127.0.0.1:${server.address().port}`,responses,tasks,errors,
    stats:()=>({backpressure,afterEnd}),
    close:async()=>{
      const closing=new Promise(resolve=>server.close(resolve));
      server.closeAllConnections();await closing;await Promise.all(tasks);
    },
  };
}

test('large decision backlog drains without ending SSE or crashing the HTTP server',{timeout:5000},async()=>{
  const resumed=deferred();
  const h=await harness({authorize:async()=>true,readEvents:async after=>{
    if(after!=='0'){assert.equal(after,'100');resumed.resolve();return [];}
    return Array.from({length:100},(_,i)=>({eventId:String(i+1),payload:'x'.repeat(4096)}));
  }});
  try{
    const response=await fetch(h.base+'/events',{signal:AbortSignal.timeout(4000)});
    assert.equal(response.status,200);
    assert(response.headers.get('content-type').startsWith('text/event-stream'));
    const reader=response.body.getReader(),ids=[];let pending='';
    while(ids.length<100){
      const value=await reader.read();assert(!value.done,'SSE ended before the batch was delivered');
      pending+=new TextDecoder().decode(value.value);
      let boundary;
      while((boundary=pending.indexOf('\n\n'))>=0){
        const frame=pending.slice(0,boundary);pending=pending.slice(boundary+2);
        const match=/^id: (\d+)\nevent: decision\ndata: /.exec(frame);
        if(match)ids.push(Number(match[1]));
      }
    }
    assert.deepEqual(ids,Array.from({length:100},(_,i)=>i+1));
    await resumed.promise;
    await reader.cancel();
    assert(h.stats().backpressure>0,'the test must exercise write(false)');
    assert.equal(h.stats().afterEnd,0);
    assert.equal(await (await fetch(h.base+'/health')).text(),'ready');
    assert.deepEqual(h.errors,[]);
  }finally{await h.close();}
});

test('disconnect during an outstanding query prevents all late writes',{timeout:5000},async()=>{
  const started=deferred(),read=deferred();let queries=0;
  const h=await harness({authorize:async()=>true,readEvents:async()=>{queries++;started.resolve();return read.promise;}});
  const controller=new AbortController();
  try{
    await fetch(h.base+'/events',{signal:controller.signal});await started.promise;
    const closed=once(h.responses[0],'close');controller.abort();await closed;
    read.resolve([{eventId:'1'}]);await Promise.all(h.tasks);await delay(30);
    assert.equal(queries,1);assert.equal(h.stats().afterEnd,0);
    assert.equal(await (await fetch(h.base+'/health')).text(),'ready');assert.deepEqual(h.errors,[]);
  }finally{read.resolve([]);controller.abort();await h.close();}
});

test('authorization revocation closes the stream without a trailing heartbeat',{timeout:5000},async()=>{
  let checks=0,queries=0;
  const h=await harness({authorize:async()=>++checks===1,readEvents:async()=>{queries++;return [];}});
  try{
    const response=await fetch(h.base+'/events',{signal:AbortSignal.timeout(4000)});
    assert.equal(await response.text(),': keepalive\n\n');
    await delay(30);assert.equal(checks,2);assert.equal(queries,1);assert.equal(h.stats().afterEnd,0);
    assert.equal(await (await fetch(h.base+'/health')).text(),'ready');
  }finally{await h.close();}
});

class BlockedResponse extends EventEmitter {
  destroyed=false;writableEnded=false;writes=0;ends=0;
  setHeader(){} flushHeaders(){}
  write(){assert(!this.writableEnded);this.writes++;return false;}
  end(){this.ends++;this.writableEnded=true;this.emit('finish');}
  destroy(){this.destroyed=true;this.emit('close');}
}

test('a stalled consumer times out, cancels drain listeners, and stops polling',async()=>{
  const response=new BlockedResponse();let queries=0;
  await streamDecisionEvents(response,{after:'0',pollMs:5,drainTimeoutMs:20,authorize:async()=>true,
    readEvents:async()=>{queries++;return [{eventId:'1'},{eventId:'2'}];}});
  await delay(30);
  assert.equal(response.writes,1);assert.equal(response.ends,1);assert.equal(queries,1);
  assert.equal(response.listenerCount('drain'),0);
});

test('asynchronous response errors cancel pending drain without an unhandled error',async()=>{
  const response=new BlockedResponse();
  const running=streamDecisionEvents(response,{after:'0',pollMs:5,drainTimeoutMs:100,authorize:async()=>true,
    readEvents:async()=>[{eventId:'1'}]});
  setImmediate(()=>response.emit('error',new Error('local response failure')));
  await running;
  assert.equal(response.destroyed,true);assert.equal(response.listenerCount('drain'),0);
});

test('event names come from the caller (interim records) and default to decision',{timeout:5000},async()=>{
  let served=false;
  const h=await harness({authorize:async()=>true,eventName:event=>event.eventKind==='final'?'decision':'interim',readEvents:async()=>{
    if(served)return [];served=true;
    return [{eventId:'1',eventKind:'final'},{eventId:'2',eventKind:'first_anomaly'},{eventId:'3'}];
  }});
  try{
    const response=await fetch(h.base+'/events',{signal:AbortSignal.timeout(4000)});
    const reader=response.body.getReader();let text='';
    while(!text.includes(': keepalive')){const value=await reader.read();assert(!value.done);text+=new TextDecoder().decode(value.value);}
    await reader.cancel();
    assert.deepEqual([...text.matchAll(/^id: (\d+)\nevent: (\w+)$/gm)].map(m=>[m[1],m[2]]),[['1','decision'],['2','interim'],['3','interim']]);
  }finally{await h.close();}
  const plain=await harness({authorize:async()=>true,readEvents:async after=>after==='0'?[{eventId:'1',eventKind:'first_anomaly'}]:[]});
  try{
    const response=await fetch(plain.base+'/events',{signal:AbortSignal.timeout(4000)});
    const reader=response.body.getReader();let text='';
    while(!text.includes(': keepalive')){const value=await reader.read();assert(!value.done);text+=new TextDecoder().decode(value.value);}
    await reader.cancel();
    assert.match(text,/^id: 1\nevent: decision\n/m,'without eventName the existing frame name is kept');
  }finally{await plain.close();}
});

class RecordingResponse extends EventEmitter {
  destroyed=false;writableEnded=false;frames=[];
  setHeader(){} flushHeaders(){}
  write(frame){assert(!this.writableEnded);this.frames.push(frame);return true;}
  end(){this.writableEnded=true;this.emit('finish');}
  destroy(){this.destroyed=true;this.emit('close');}
}
const ids=frames=>frames.flatMap(frame=>/^id: (\d+)\n/.exec(frame)?.[1]??[]);
const page=(after,count)=>Array.from({length:count},(_,i)=>({eventId:String(Number(after)+i+1)}));

test('a full page is re-read with the advanced cursor in the same tick, then one keepalive',async()=>{
  const response=new RecordingResponse(),reads=[];
  const sizes={'0':100,'100':100,'200':30};
  await streamDecisionEvents(response,{after:'0',pollMs:60_000,authorize:async()=>true,
    readEvents:async after=>{reads.push(after);return page(after,sizes[after]??0);}});
  try{
    assert.deepEqual(reads,['0','100','200'],'stops re-reading once a page is not full');
    assert.deepEqual(ids(response.frames),page('0',230).map(e=>e.eventId));
    assert.equal(response.frames.at(-1),': keepalive\n\n');
    assert.equal(response.frames.filter(f=>f.startsWith(':')).length,1);
  }finally{response.destroy();}
});

test('re-reads per tick are capped and the rest continues on the next tick in cursor order',{timeout:5000},async()=>{
  const response=new RecordingResponse(),reads=[];
  await streamDecisionEvents(response,{after:'0',pollMs:20,pageSize:2,maxPagesPerTick:3,authorize:async()=>true,
    readEvents:async after=>{reads.push(after);return Number(after)<10?page(after,2):[];}});
  try{
    assert.deepEqual(reads,['0','2','4'],'first tick stops after maxPagesPerTick full pages');
    assert.deepEqual(ids(response.frames),['1','2','3','4','5','6']);
    while(reads.length<6)await delay(5);
    assert.deepEqual(reads.slice(0,6),['0','2','4','6','8','10']);
    await delay(5);
    assert.deepEqual(ids(response.frames),Array.from({length:10},(_,i)=>String(i+1)),'no gap or duplicate across ticks');
  }finally{response.destroy();}
});

test('authorization is re-checked on its own interval, not on every read, and revocation ends the stream',{timeout:5000},async()=>{
  const response=new RecordingResponse();let clock=0,checks=0,reads=0,allow=true;
  const running=streamDecisionEvents(response,{after:'0',pollMs:5,authorizeMs:900,now:()=>clock,
    authorize:async()=>{checks++;return allow;},readEvents:async()=>{reads++;return [];}});
  await running;
  try{
    await delay(40);
    assert.equal(checks,1,'reads within the interval reuse the last check');
    assert(reads>3,`reads keep their own short period (reads=${reads})`);
    clock=899;await delay(20);assert.equal(checks,1);
    clock=900;await delay(20);assert.equal(checks,2,'checked again once the interval passed');
    allow=false;clock=1800;
    while(!response.writableEnded)await delay(5);
    const readsAtEnd=reads;await delay(20);
    assert.equal(checks,3);assert.equal(reads,readsAtEnd,'no reads after revocation');
  }finally{response.destroy();}
});

test('authorization is re-checked between pages of one tick when the interval passes mid-tick',async()=>{
  const response=new RecordingResponse();let clock=0;const calls=[];
  await streamDecisionEvents(response,{after:'0',pollMs:60_000,pageSize:1,maxPagesPerTick:3,authorizeMs:900,now:()=>clock,
    authorize:async()=>{calls.push('auth');return calls.filter(c=>c==='auth').length===1;},
    readEvents:async after=>{calls.push('read:'+after);clock+=500;return page(after,1);}});
  assert.deepEqual(calls,['auth','read:0','read:1','auth']);
  assert.deepEqual(ids(response.frames),['1','2'],'no event is written after the failed check');
  assert.equal(response.writableEnded,true);
});
