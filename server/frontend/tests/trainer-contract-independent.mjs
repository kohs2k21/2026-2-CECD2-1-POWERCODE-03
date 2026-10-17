import assert from 'node:assert/strict';
import { clearJobRequestKeys, jobRequestKey, forgetJobRequestKey } from '../src/services/api/job-idempotency.ts';

// A browser may permit reads while denying writes (quota/private storage).
// Failed persistence must not turn a retry into another paid training job.
const input = {kind:'train',sourceId:'source-a',featureSetId:'features-a',modelKey:'iforest'};
const stored = new Map();
let failWrites = false;
globalThis.window = {sessionStorage: {
  getItem:key => stored.get(key) ?? null,
  setItem:(key,value) => {if(failWrites) throw new Error('storage unavailable');stored.set(key,value);},
  removeItem:key => {if(failWrites) throw new Error('storage unavailable');stored.delete(key);},
}};
clearJobRequestKeys();
failWrites=true;
const retryKey=jobRequestKey('actor-a',input);
assert.equal(jobRequestKey('actor-a',input),retryKey,'write-denied storage must preserve retry key in memory');
failWrites=false;
jobRequestKey('actor-a',input);
failWrites=true;
const fresh=jobRequestKey('actor-a',input,true);
assert.notEqual(fresh,retryKey);
assert.equal(jobRequestKey('actor-a',input),fresh,'stale storage must not replace a fresh execution key');
forgetJobRequestKey('actor-a',input);
const afterForget=jobRequestKey('actor-a',input);
assert.notEqual(afterForget,fresh,'forgetting a terminal execution must survive a failed storage write');
clearJobRequestKeys();
assert.notEqual(jobRequestKey('actor-a',input),afterForget,'logout reset must survive failed storage removal');
failWrites=false;
clearJobRequestKeys();
const persisted=jobRequestKey('actor-a',input);
// A fresh module instance models a reload while sessionStorage remains available.
const reloaded=await import('../src/services/api/job-idempotency.ts?independent-reload');
assert.equal(reloaded.jobRequestKey('actor-a',input),persisted,'reload hydrates the original key');
assert.notEqual(reloaded.jobRequestKey('actor-b',input),persisted,'hydration retains actor isolation');
clearJobRequestKeys();
console.log('PASS: write-denied storage retries, stale persisted key isolation, terminal/reset key invalidation, normal-storage reload hydration');
