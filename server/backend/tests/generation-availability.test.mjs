// LLM 생성 서비스 가용성 확인(llm-recommendation-job.md '생성 서비스 가용성' 절): 가짜 /health 서버로 판정·제한 시간·캐시·확인 공유를 고정한다.
import assert from 'node:assert/strict';
import net from 'node:net';
import { after, test } from 'node:test';
import { startFakeLlmHealth } from './fake-llm-health.mjs';

const {
  AVAILABILITY_CACHE_MS, DEFAULT_LLM_HEALTH_URL, GENERATION_SERVICE_UNAVAILABLE, HEALTH_TIMEOUT_MS, LLM_JOB_KINDS,
  createGenerationAvailability, probeHealth,
} = await import('../dist/services/generationAvailability.js');

const health = await startFakeLlmHealth();
after(() => health.close());

test('contract constants: default URL, 1.5 s timeout, 10 s cache, LLM job kinds, error code', () => {
  assert.equal(DEFAULT_LLM_HEALTH_URL, 'http://llm:8010/health');
  assert.equal(HEALTH_TIMEOUT_MS, 1500);
  assert.equal(AVAILABILITY_CACHE_MS, 10000);
  assert.deepEqual([...LLM_JOB_KINDS].sort(), ['explain', 'explain_protocol', 'recommend_thresholds']);
  assert.equal(GENERATION_SERVICE_UNAVAILABLE, 'generation_service_unavailable');
});

test('available only for HTTP 200 with status "ready"', async () => {
  for (const [mode, expected] of [['ready', true], ['down', false], ['loading', false], ['garbage', false]]) {
    health.state.mode = mode;
    assert.equal(await probeHealth(health.url, 1500), expected, mode);
  }
  health.state.mode = 'ready';
  assert.equal(await probeHealth(health.url.replace('/health', '/other'), 1500), false, '404 is unavailable');
});

test('no response within the timeout is unavailable and returns at the timeout', async () => {
  health.state.mode = 'hang';
  const started = Date.now();
  assert.equal(await probeHealth(health.url, 200), false);
  const elapsed = Date.now() - started;
  assert.ok(elapsed >= 150 && elapsed < 1000, `returned after ${elapsed} ms`);
  health.state.mode = 'ready';
});

test('refused connection, unknown host and malformed URL are unavailable', async () => {
  const probe = net.createServer();
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const closedPort = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  assert.equal(await probeHealth(`http://127.0.0.1:${closedPort}/health`, 1500), false, 'connection refused');
  assert.equal(await probeHealth('http://llm-health.invalid:8010/health', 1500), false, 'DNS failure');
  assert.equal(await probeHealth('not a url', 1500), false, 'malformed URL');
  assert.equal(await probeHealth('ftp://127.0.0.1/health', 1500), false, 'unsupported scheme');
});

test('both results are cached for 10 s from the check, then checked again', async () => {
  let clock = 1_800_000_000_000;
  const checker = createGenerationAvailability({ url: health.url, now: () => clock });
  health.state.mode = 'ready';
  const hits = health.state.hits;
  const first = await checker.check();
  assert.deepEqual(first, { available: true, checkedAt: new Date(clock).toISOString() });
  health.state.mode = 'down';
  clock += AVAILABILITY_CACHE_MS - 1;
  assert.deepEqual(await checker.check(), first, 'cached success inside 10 s');
  assert.equal(health.state.hits, hits + 1);
  clock += 1;
  const second = await checker.check();
  assert.deepEqual(second, { available: false, checkedAt: new Date(clock).toISOString() }, 'checked again at 10 s');
  assert.equal(health.state.hits, hits + 2);
  health.state.mode = 'ready';
  clock += AVAILABILITY_CACHE_MS - 1;
  assert.deepEqual(await checker.check(), second, 'cached failure inside 10 s');
  assert.equal(health.state.hits, hits + 2);
  clock += 1;
  assert.equal((await checker.check()).available, true);
  assert.equal(health.state.hits, hits + 3);
});

test('concurrent requests share one in-flight check', async () => {
  health.state.mode = 'ready';
  health.state.delayMs = 100;
  const checker = createGenerationAvailability({ url: health.url });
  const hits = health.state.hits;
  const results = await Promise.all(Array.from({ length: 5 }, () => checker.check()));
  assert.equal(health.state.hits, hits + 1, 'one /health request for five callers');
  assert.ok(results.every((result) => result === results[0] && result.available === true));
  health.state.delayMs = 0;
});

test('a probe that throws is treated as unavailable and still cached', async () => {
  let calls = 0;
  const checker = createGenerationAvailability({ url: 'http://unused/health', probe: async () => { calls += 1; throw new Error('boom'); } });
  assert.equal((await checker.check()).available, false);
  assert.equal((await checker.check()).available, false);
  assert.equal(calls, 1);
});
