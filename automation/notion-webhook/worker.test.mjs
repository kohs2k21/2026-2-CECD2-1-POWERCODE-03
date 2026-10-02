import assert from "node:assert/strict";
import test from "node:test";
import { createHandler } from "./worker.mjs";

const env = { NOTION_RELAY_SECRET: "s".repeat(32), GITHUB_DISPATCH_TOKEN: "synthetic-token" };
const request = (options = {}) => new Request("https://relay.example/notion-sync", {
  method: "POST", headers: { "X-Notion-Relay-Secret": env.NOTION_RELAY_SECRET }, ...options,
});

test("authenticated button dispatches only the fixed main workflow, ignoring payload", async () => {
  let received;
  const handler = createHandler({ fetcher: async (url, init) => {
    received = { url, init };
    return new Response(null, { status: 204 });
  } });
  const response = await handler(request({ body: JSON.stringify({
    repository: "attacker/repo", ref: "unreviewed-branch", workflow: "other.yml", title: "private",
  }) }), env);
  assert.equal(response.status, 202);
  assert.equal(received.url, "https://api.github.com/repos/kohs2k21/2026-2-CECD2-1-POWERCODE-03/actions/workflows/notion-sync.yml/dispatches");
  assert.deepEqual(JSON.parse(received.init.body), { ref: "main" });
  assert.equal(received.init.redirect, "error");
  assert.equal(received.init.headers.Authorization, "Bearer synthetic-token");
  assert.deepEqual(await response.json(), { code: "WORKFLOW_DISPATCHED" });
});

test("current GitHub 200 dispatch response is accepted without forwarding its body", async () => {
  const handler = createHandler({ fetcher: async () => Response.json({
    workflow_run_id: 123, html_url: "https://github.com/private-result", private_value: "do-not-forward",
  }, { status: 200 }) });
  const response = await handler(request(), env);
  assert.equal(response.status, 202);
  assert.deepEqual(await response.json(), { code: "WORKFLOW_DISPATCHED" });
});

test("missing or incorrect shared secret cannot dispatch", async () => {
  let calls = 0;
  const handler = createHandler({ fetcher: async () => { calls++; } });
  for (const secret of [undefined, "wrong-secret", "s".repeat(257)]) {
    const headers = secret ? { "X-Notion-Relay-Secret": secret } : {};
    const response = await handler(request({ headers }), env);
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { code: "RELAY_AUTH_REQUIRED" });
  }
  assert.equal(calls, 0);
});

test("unsupported path and method cannot dispatch", async () => {
  let calls = 0;
  const handler = createHandler({ fetcher: async () => { calls++; } });
  assert.equal((await handler(new Request("https://relay.example/other", { method: "POST" }), env)).status, 404);
  const response = await handler(new Request("https://relay.example/notion-sync"), env);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("Allow"), "POST");
  assert.equal(calls, 0);
});

test("missing deployment secrets fail without dispatch or disclosing configuration", async () => {
  const handler = createHandler({ fetcher: async () => { assert.fail("must not dispatch"); } });
  for (const incomplete of [{}, { ...env, GITHUB_DISPATCH_TOKEN: "" }, { ...env, NOTION_RELAY_SECRET: "short" }]) {
    const response = await handler(request(), incomplete);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { code: "RELAY_NOT_CONFIGURED" });
  }
});

test("upstream rejection never becomes successful completion or exposes its body", async () => {
  for (const [status, expectedStatus, code] of [
    [401, 502, "GITHUB_ACCESS_REJECTED"], [403, 502, "GITHUB_ACCESS_REJECTED"],
    [429, 503, "GITHUB_RATE_LIMITED"], [404, 502, "GITHUB_DISPATCH_REJECTED"],
    [500, 502, "GITHUB_DISPATCH_REJECTED"],
  ]) {
    const handler = createHandler({ fetcher: async () => new Response("private upstream body", { status }) });
    const response = await handler(request(), env);
    assert.equal(response.status, expectedStatus);
    assert.deepEqual(await response.json(), { code });
  }
});

test("each authenticated request dispatches without debounce or silent coalescing", async () => {
  let calls = 0;
  const handler = createHandler({ fetcher: async () => { calls++; return new Response(null, { status: 204 }); } });
  assert.equal((await handler(request(), env)).status, 202);
  assert.equal((await handler(request(), env)).status, 202);
  assert.equal(calls, 2);
});

test("uncertain upstream error is sanitized and a later button can retry", async () => {
  let calls = 0;
  const handler = createHandler({ fetcher: async () => {
    if (++calls === 1) throw new Error("synthetic-token private-error");
    return new Response(null, { status: 204 });
  } });
  const failure = await handler(request(), env);
  assert.equal(failure.status, 502);
  assert.deepEqual(await failure.json(), { code: "GITHUB_DISPATCH_UNCERTAIN" });
  assert.equal((await handler(request(), env)).status, 202);
});

test("upstream timeout aborts the request and returns a bounded failure", async () => {
  const handler = createHandler({ timeoutMs: 10, fetcher: async (_url, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("private timeout")), { once: true });
  }) });
  const response = await handler(request(), env);
  assert.equal(response.status, 504);
  assert.deepEqual(await response.json(), { code: "GITHUB_DISPATCH_TIMEOUT" });
});
