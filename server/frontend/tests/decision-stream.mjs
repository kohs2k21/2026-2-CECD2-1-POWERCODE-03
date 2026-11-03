import assert from 'node:assert/strict';
const store=new Map([['token','local-token']]);
globalThis.window={location:{origin:'http://local.test'},localStorage:{getItem:key=>store.get(key)??null,removeItem:key=>store.delete(key)}};
const {consumeDecisionStream}=await import('../src/services/api/decision-stream.api.ts');
const frame='id: 12\r\nevent: decision\r\ndata: {"id":"saved","status":"partial","sourceId":"source"}\r\n\r\n';
const chunks=[frame.slice(0,7),frame.slice(7,25),frame.slice(25)];
let calls=0;
globalThis.fetch=async(url,options)=>{
 calls+=1;assert.equal(url,'/api/pipeline/events');assert.equal(options.headers.Authorization,'Bearer local-token');
 assert.equal(options.headers['Last-Event-ID'],'11');assert.equal(options.redirect,'error');
 return new Response(new ReadableStream({start(controller){for(const chunk of chunks)controller.enqueue(new TextEncoder().encode(chunk));controller.close();}}),{headers:{'Content-Type':'text/event-stream'}});
};
const events=[];
await consumeDecisionStream(new AbortController().signal,'11',(value,id)=>events.push([value,id]));
assert.equal(calls,1);assert.equal(events.length,1);assert.equal(events[0][0].status,'partial');assert.equal(events[0][1],'12');
console.log('PASS: Bearer decision stream, fragmented CRLF frames, stored partial result, resume cursor');
// include=interims: onInterim을 주면 `event: interim` 중간 판정 기록 이벤트도 받는다(같은 커서), 주지 않으면 기존 요청 그대로.
{
  const frames='id: 13\nevent: interim\ndata: {"eventKind":"first_anomaly","interimId":"i-1","jobId":"j-1","attemptNo":1}\n\n'
    +'id: 14\nevent: decision\ndata: {"id":"d-1","status":"complete","eventKind":"final"}\n\n';
  let url;
  globalThis.fetch=async(target)=>{url=target;return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(frames));controller.close();}}),{headers:{'Content-Type':'text/event-stream'}});};
  const finals=[],interims=[];
  await consumeDecisionStream(new AbortController().signal,'12',(value,id)=>finals.push(id),undefined,(value,id)=>interims.push([value.eventKind,value.jobId,id]));
  assert.equal(url,'/api/pipeline/events?include=interims');
  assert.deepEqual(interims,[['first_anomaly','j-1','13']]);assert.deepEqual(finals,['14']);
  await assert.rejects(consumeDecisionStream(new AbortController().signal,'12',()=>{}),/판정 이벤트 형식/,'without onInterim an interim frame is not accepted (legacy consumers never request them)');
  globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('id: 15\nevent: interim\ndata: {"eventKind":"made_up","interimId":"i","jobId":"j"}\n\n'));controller.close();}}),{headers:{'Content-Type':'text/event-stream'}});
  await assert.rejects(consumeDecisionStream(new AbortController().signal,'12',()=>{},undefined,()=>{}),/판정 이벤트 검증/);
  console.log('PASS: interim decision events opt-in, shared cursor, kind validation');
}
