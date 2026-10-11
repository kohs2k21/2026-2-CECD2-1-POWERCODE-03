import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { timingSafeEqual } from 'node:crypto';
import worker, { receive, consume, LIMITS } from '../dist/index.js';

const originalFetch = globalThis.fetch;
const originalWarn = console.warn;
// Cloudflare's native extension; only the Node test environment needs this adapter.
crypto.subtle.timingSafeEqual = (a, b) => timingSafeEqual(Buffer.from(a), Buffer.from(b));
afterEach(() => { globalThis.fetch = originalFetch; console.warn = originalWarn; });
const receiptId = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';
function setup() {
  const sent = [], dead = [];
  return { sent, dead, env: {
    WEBHOOK_ENABLED: 'true', NOTION_WEBHOOK_SECRET: 'test-only-key', GITHUB_DISPATCH_TOKEN: 'test-only-token',
    REQUESTS: { async send(body, options) { sent.push({ body, options }); } },
    DEAD_LETTER: { async send(body) { dead.push(body); } }
  } };
}
function request(body = '{}', options = {}) {
  return new Request('https://worker.example/notion', {
    method: 'POST', headers: { 'content-type': 'application/json', 'X-Webhook-Secret': 'test-only-key', ...options.headers },
    body, ...options
  });
}
function message(body = {}, attempts = 1) {
  return { id: 'queue-id', attempts, body: { receiptId, acceptedAt: new Date().toISOString(), ...body },
    acked: false, retries: [], ack() { this.acked = true; }, retry(options) { this.retries.push(options); } };
}
function mockFetch(...results) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    const result = results.shift();
    if (result instanceof Error) throw result;
    return result;
  };
  return calls;
}
const json = (value, status = 200, headers = {}) => Response.json(value, { status, headers });

test('authentication, disabled config, paths and methods fail closed', async () => {
  const { env, sent } = setup();
  assert.equal((await receive(request('{}', { headers: { 'X-Webhook-Secret': 'wrong' } }), env)).status, 401);
  assert.equal((await receive(new Request('https://worker.example/notion'), env)).status, 405);
  assert.equal((await receive(new Request('https://worker.example/other'), env)).status, 404);
  for (const change of [{ WEBHOOK_ENABLED: 'false' }, { NOTION_WEBHOOK_SECRET: '' }, { GITHUB_DISPATCH_TOKEN: '' }]) {
    assert.equal((await receive(request(), { ...env, ...change })).status, 503);
  }
  assert.equal(sent.length, 0);
});
test('JSON object and byte limits checked including streamed oversized body', async () => {
  const { env, sent } = setup();
  for (const body of ['bad json', 'null', '[]', '"scalar"']) assert.equal((await receive(request(body), env)).status, 400);
  assert.equal((await receive(request('{}', { headers: { 'X-Webhook-Secret': 'test-only-key', 'content-type': 'text/plain' } }), env)).status, 415);
  assert.equal((await receive(request(JSON.stringify({ x: 'x'.repeat(LIMITS.bodyBytes) })), env)).status, 413);
  const stream = new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(LIMITS.bodyBytes)); controller.enqueue(new Uint8Array(1)); controller.close();
  } });
  assert.equal((await receive(request(stream, { duplex: 'half' }), env)).status, 413);
  assert.equal(sent.length, 0);
});
test('202 follows successful durable send; raw payload not stored or forwarded', async () => {
  const { env, sent } = setup();
  const response = await receive(request('{"body":"private","repo":"attacker","ref":"evil"}'), env);
  assert.equal(response.status, 202);
  assert.deepEqual(Object.keys(sent[0].body).sort(), ['acceptedAt', 'receiptId']);
  assert.equal((await response.json()).status, 'queued');
  env.REQUESTS.send = async () => { throw Error('sensitive'); };
  assert.equal((await receive(request(), env)).status, 503);
});
test('fixed dispatch target and API version; run ID survives queued polling', async () => {
  const { env, sent } = setup();
  const calls = mockFetch(json({ workflow_run_id: 123 }));
  const msg = message({ repo: 'attacker', ref: 'evil', payload: 'private' });
  await consume(msg, env);
  assert.equal(msg.acked, true);
  assert.equal(calls[0].url, 'https://api.github.com/repos/kohs2k21/2026-2-CECD2-1-POWERCODE-03/actions/workflows/notion-sync.yml/dispatches');
  assert.deepEqual(JSON.parse(calls[0].options.body), { ref: 'main' });
  assert.equal(calls[0].options.headers['X-GitHub-Api-Version'], '2026-03-10');
  assert.equal(calls[0].options.redirect, 'manual');
  assert.equal(sent[0].body.runId, 123);
  assert.deepEqual(Object.keys(sent[0].body).sort(), ['acceptedAt', 'receiptId', 'runId']);
});
test('network, rate limit and 5xx retry with bounded delay; permanent errors dead-letter', async () => {
  for (const result of [Error('secret'), json({}, 429, { 'retry-after': '60' }), json({}, 503), json({}, 403, { 'x-ratelimit-remaining': '0' })]) {
    const { env, dead } = setup(); const msg = message(); mockFetch(result); await consume(msg, env);
    assert.equal(msg.acked, false); assert.equal(msg.retries.length, 1); assert.ok(msg.retries[0].delaySeconds <= 300); assert.equal(dead.length, 0);
  }
  for (const status of [401, 403, 404, 422]) {
    const { env, dead } = setup(); const msg = message(); mockFetch(json({}, status)); await consume(msg, env);
    assert.equal(msg.acked, true); assert.equal(dead.length, 1); assert.equal(msg.retries.length, 0);
  }
});
test('redirect response never follows another destination and dead-letters', async () => {
  const { env, dead } = setup();
  const calls = mockFetch(new Response(null, { status: 302, headers: { location: 'https://attacker.example/collect' } }));
  const msg = message(); await consume(msg, env);
  assert.equal(calls.length, 1); assert.equal(calls[0].options.redirect, 'manual');
  assert.equal(msg.acked, true); assert.equal(dead.length, 1); assert.equal(msg.retries.length, 0);
});
test('server Retry-After is respected within age budget and excessive wait dead-letters', async () => {
  const { env, dead } = setup();
  mockFetch(json({}, 429, { 'retry-after': '3600' }), json({}, 429, { 'retry-after': '86400' }), json({}, 403, { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(Math.ceil(Date.now() / 1000) + 500) }));
  const first = message(); await consume(first, env); assert.equal(first.retries[0].delaySeconds, 3600);
  const second = message(); await consume(second, env); assert.equal(second.acked, true); assert.equal(dead.length, 1);
  const third = message(); await consume(third, env); assert.ok(third.retries[0].delaySeconds >= 499);
});
test('GitHub request has a bounded abort signal and timeout failure retries', async () => {
  const { env } = setup(); let signal;
  globalThis.fetch = async (_url, options) => { signal = options.signal; throw new DOMException('test timeout', 'TimeoutError'); };
  const msg = message(); await consume(msg, env);
  assert.ok(signal instanceof AbortSignal); assert.equal(msg.retries.length, 1); assert.equal(msg.acked, false);
});
test('retry exhaustion, missing dispatch ID and unknown response do not loop', async () => {
  for (const result of [json({}, 503), json({}), json(null), json([]), new Response(null, { status: 204 }), new Response('invalid')]) {
    const { env, dead } = setup(); const msg = message({}, LIMITS.attempts); mockFetch(result); await consume(msg, env);
    assert.equal(msg.acked, true); assert.equal(dead.length, 1);
  }
});
test('successful run ACK; pending run delays poll with bounded persistent counter', async () => {
  const { env, sent } = setup();
  const calls = mockFetch(json({ id: 123, status: 'in_progress' }), json({ id: 123, status: 'completed', conclusion: 'success' }));
  const pending = message({ runId: 123, pollCount: 2 }); await consume(pending, env);
  assert.equal(calls[0].options.method, 'GET'); assert.equal(sent[0].body.pollCount, 3); assert.equal(sent[0].options.delaySeconds, 60);
  const completed = message(sent[0].body); await consume(completed, env); assert.equal(completed.acked, true); assert.equal(sent.length, 1);
});
test('failed/cancelled run recovers once; second failure dead-letters', async () => {
  for (const conclusion of ['failure', 'cancelled', 'timed_out', 'startup_failure']) {
    const { env, sent, dead } = setup();
    mockFetch(json({ id: 123, status: 'completed', conclusion }), json({ id: 124, status: 'completed', conclusion }));
    const first = message({ runId: 123, pollCount: 4 }); await consume(first, env);
    assert.equal(sent[0].body.recoveries, 1); assert.equal(sent[0].body.runId, undefined); assert.equal(sent[0].body.pollCount, 4);
    const second = message({ ...sent[0].body, runId: 124 }); await consume(second, env);
    assert.equal(dead.length, 1); assert.equal(second.acked, true); assert.equal(sent.length, 1);
  }
});
test('persisted poll, age and recovery budgets prevent enqueue reset loops', async () => {
  for (const body of [{ pollCount: LIMITS.polls }, { recoveries: 2 }, { acceptedAt: new Date(Date.now() - LIMITS.ageMs - 1000).toISOString() }]) {
    const { env, dead } = setup(); const calls = mockFetch(); const msg = message(body); await consume(msg, env);
    assert.equal(calls.length, 0); assert.equal(dead.length, 1); assert.equal(msg.acked, true);
  }
});
test('queue send failure never ACKs old message; batch retries without leaking exception', async () => {
  const { env } = setup(); env.REQUESTS.send = async () => { throw Error('private'); };
  mockFetch(json({ workflow_run_id: 123 })); const msg = message();
  await worker.queue({ messages: [msg] }, env);
  assert.equal(msg.acked, false); assert.equal(msg.retries.length, 1);
});
test('duplicate delivery may dispatch twice; each fixed reconcile safely tracked', async () => {
  const { env, sent } = setup(); const calls = mockFetch(json({ workflow_run_id: 123 }), json({ workflow_run_id: 124 }));
  await consume(message(), env); await consume(message(), env);
  assert.equal(calls.length, 2); assert.deepEqual(sent.map(x => x.body.runId), [123, 124]);
});
test('invalid envelopes and mismatched/unsupported run result stop safely', async () => {
  const { env, dead } = setup(); const invalid = message({ runId: -1 }); const calls = mockFetch();
  await consume(invalid, env); assert.equal(calls.length, 0); assert.equal(dead.length, 1);
  for (const data of [{ id: 456, status: 'completed', conclusion: 'success' }, { id: 123, status: 'unknown' }, { id: 123, status: 'completed', conclusion: 'skipped' }]) {
    const { env, dead } = setup(); mockFetch(json(data)); await consume(message({ runId: 123 }), env); assert.equal(dead.length, 1);
  }
});
test('retry diagnostics use fixed cause codes without raw errors, body, secrets or headers', async () => {
  const logs = []; console.warn = entry => logs.push(JSON.parse(entry));
  for (const [failure, expectedCode] of [
    [new TypeError('private-body test-only-token test-only-key Authorization'), 'github_network_error'],
    [new DOMException('private-body test-only-token', 'TimeoutError'), 'github_timeout'],
    [new Error('private-body test-only-key'), 'github_unavailable'],
    [json({ private: 'private-body' }, 503), 'github_transient']
  ]) {
    const { env } = setup(); const msg = message({ private: 'private-body' }, 2);
    mockFetch(failure); await consume(msg, env);
    assert.deepEqual(logs.at(-1), { event: 'retry', code: expectedCode, attempt: 2, receiptId, delaySeconds: 60 });
  }
  const { env } = setup(); const disabled = message();
  await consume(disabled, { ...env, WEBHOOK_ENABLED: 'false' });
  assert.equal(logs.at(-1).code, 'disabled');
  const output = JSON.stringify(logs);
  for (const secret of ['private-body', 'test-only-token', 'test-only-key', 'Authorization']) assert.ok(!output.includes(secret));
});
test('queue processing catch logs only fixed event and attempt without raw queue errors', async () => {
  const logs = []; console.warn = entry => logs.push(JSON.parse(entry));
  const { env } = setup(); env.REQUESTS.send = async () => { throw Error('private-body test-only-token test-only-key'); };
  mockFetch(json({ workflow_run_id: 123 })); const msg = message({ private: 'private-body' }, 3);
  await worker.queue({ messages: [msg] }, env);
  assert.deepEqual(logs, [{ event: 'queue_processing_error', attempt: 3 }]);
  assert.equal(msg.acked, false); assert.deepEqual(msg.retries, [{ delaySeconds: 60 }]);
});
