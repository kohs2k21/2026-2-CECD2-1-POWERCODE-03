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
    tasks.push(streamDecisionEvents(res,{after:'0',pollMs:10,...options}).catch(error=>{errors.push(error);res.destroy();}));
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
