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
