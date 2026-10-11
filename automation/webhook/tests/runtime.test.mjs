import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('native workerd fetch validates real options and never forwards credentials on redirect', async () => {
  const harness = `
    import { consume } from './index.js';
    export default { async fetch(request) {
      const scenario = new URL(request.url).pathname;
      const sent = [], dead = [], retries = [];
      const env = {WEBHOOK_ENABLED: 'true', NOTION_WEBHOOK_SECRET: 'native-test-only', GITHUB_DISPATCH_TOKEN: 'native-test-only',
        REQUESTS: {async send(body) { sent.push(body); }}, DEAD_LETTER: {async send(body) { dead.push(body); }}};
      const message = {id: 'native-test', attempts: 1, acked: false,
        body: {receiptId: 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa', acceptedAt: new Date().toISOString(), ...(scenario === '/poll' ? {runId: 123} : {})},
        ack() { this.acked = true; }, retry(options) { retries.push(options); }};
      await consume(message, env);
      return Response.json({sent, dead, retries, acked: message.acked});
    }};
  `;
  let fixtureScenario = 'dispatch';
  const calls = [];
  const runtime = new Miniflare(convertV4MiniflareOptions({
    compatibilityDate: '2026-10-08',
    // Native fetch executes in workerd. This service replaces all outbound networking, not fetch itself.
    outboundService: async request => {
      calls.push({ url: request.url, method: request.method, authorizationPresent: request.headers.has('Authorization') });
      if (fixtureScenario === 'redirect') return new Response(null, { status: 302, headers: { location: 'https://redirect-fixture.invalid/' } });
      return Response.json(fixtureScenario === 'poll'
        ? { id: 123, status: 'completed', conclusion: 'success' } : { workflow_run_id: 123 });
    },
    modules: [
      { type: 'ESModule', path: 'harness.js', contents: harness },
      { type: 'ESModule', path: 'index.js', contents: await readFile(new URL('../dist/index.js', import.meta.url), 'utf8') }
    ]
  }));
  try {
    const dispatch = await (await runtime.dispatchFetch('https://unit/dispatch')).json();
    assert.equal(dispatch.acked, true); assert.equal(dispatch.retries.length, 0); assert.equal(dispatch.sent[0].runId, 123);
    assert.deepEqual(calls, [{ url: 'https://api.github.com/repos/kohs2k21/2026-2-CECD2-1-POWERCODE-03/actions/workflows/notion-sync.yml/dispatches', method: 'POST', authorizationPresent: true }]);
    fixtureScenario = 'poll'; calls.length = 0;
    const poll = await (await runtime.dispatchFetch('https://unit/poll')).json();
    assert.equal(poll.acked, true); assert.equal(poll.retries.length, 0); assert.equal(poll.sent.length, 0);
    assert.equal(calls.length, 1); assert.equal(calls[0].method, 'GET');
    fixtureScenario = 'redirect'; calls.length = 0;
    const redirect = await (await runtime.dispatchFetch('https://unit/redirect')).json();
    assert.equal(redirect.acked, true); assert.equal(calls.length, 1); assert.equal(redirect.dead.length, 1);
    assert.equal(new URL(calls[0].url).origin, 'https://api.github.com');
    assert.equal(redirect.sent.length, 0); assert.equal(redirect.retries.length, 0);
  } finally { await runtime.dispose(); }
});
