import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.React = React;
// react-hot-toast(goober) must load before the window stub, as in the other session tests.
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
};
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { InputControls } = await import("../src/features/operations/components/InputControls.tsx");
const { inputRunning } = await import("../src/features/operations/data/inputs.ts");
const { RUNNING_POLL_MS } = await import("../src/lib/query.ts");

const state = {
  enabled: true,
  sourceId: "source",
  storedCounts: { P: 1000, M: 10, B: 2 },
  csvResume: null,
  control: {
    mode: "paused", revision: 4, runId: null, configVersionId: null, status: null, settings: null,
    accepted: 0, duplicates: 0, unidentified: 0, lastError: null, workerHeartbeatAt: null,
  },
  replays: [{ id: "replay", replayKey: "four-files", recordCount: 1003, layerCounts: { P: 1000 } }],
  configurations: [{ id: "candidate", mode: "parallel", createdAt: "2026-10-01T00:00:00Z" }],
};
const calls = [];
let reads = 0;
let failure = false;
let heldPost = null;
globalThis.fetch = async (url, options) => {
  assert.ok(["Bearer local-token", "Bearer other-token"].includes(options.headers.get("Authorization")));
  if (options.method === "POST") {
    const body = JSON.parse(options.body);
    calls.push(body);
    if (heldPost) return heldPost;
    if (failure) return new Response(JSON.stringify({ code: "input_revision_conflict" }), { status: 409 });
    state.control = { ...state.control, mode: body.mode, revision: 5, status: "running", configVersionId: body.configVersionId ?? state.control.configVersionId };
    return new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json" } });
  }
  assert.ok(String(url).startsWith("/api/inputs?sourceId=source"));
  reads += 1;
  return new Response(JSON.stringify(state), { headers: { "Content-Type": "application/json" } });
};
const tick = () => act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
const wait = async (check, label) => {
  for (let i = 0; i < 50 && !check(); i++) await tick();
  assert.ok(check(), label);
};

let session;
const Probe = () => {
  session = useAuthSession();
  return null;
};
const cache = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity }, mutations: { gcTime: Infinity } } });
let renderer;
await act(async () => {
  renderer = create(
    React.createElement(QueryClientProvider, { client: cache },
      React.createElement(AuthSessionProvider, null, React.createElement(Probe), React.createElement(InputControls, { sourceId: "source" }))),
  );
});
await act(async () => session.login({ token: "local-token", user: { id: "admin", email: "a@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" } }));
const selects = () => renderer.root.findAllByType("select");
const buttonByText = (text) => renderer.root.findAllByType("button").find((b) => b.children.join("") === text);
await wait(() => selects().length >= 2, "input state renders after the account query");
assert.equal(reads, 1, "one read after login");

const observer = () => cache.getQueryCache().findAll({ queryKey: ["input-state"] })[0].observers[0];
const interval = (data) => observer().options.refetchInterval({ state: { data } });
assert.equal(interval(state), false, "paused input is not polled");
assert.equal(interval({ ...state, control: { ...state.control, status: "running" } }), RUNNING_POLL_MS, "running input polls every 5s");
assert.equal(inputRunning(undefined), false);

await act(async () => {
  selects()[0].props.onChange({ target: { value: "csv" } });
  selects()[1].props.onChange({ target: { value: "candidate" } });
});
assert.equal(buttonByText("선택한 입력 적용").props.disabled, false);
await act(async () => buttonByText("선택한 입력 적용").props.onClick());
await wait(() => reads >= 2, "state is re-read after a mode change");
assert.deepEqual(calls[0], { mode: "csv", expectedRevision: 4, replayId: "replay", rate: 10, repeat: 1, restart: false, configVersionId: "candidate" });
await wait(() => JSON.stringify(renderer.toJSON()).includes("running"), "latest server state is shown");
const text = JSON.stringify(renderer.toJSON());
assert(text.includes("CSV"));
assert(text.includes("검증"));
assert(text.includes("BODY"));

failure = true;
await act(async () => selects()[0].props.onChange({ target: { value: "api" } }));
await act(async () => buttonByText("선택한 입력 적용").props.onClick());
await wait(() => JSON.stringify(renderer.toJSON()).includes("다른 화면에서"), "conflict is shown without false success");
assert.equal(state.control.mode, "csv");
failure = false;
await act(async () => buttonByText("입력 일시정지").props.onClick());
await wait(() => calls.length === 3, "pause request sent");
assert(!("configVersionId" in calls.at(-1)), "pause does not apply the draft configuration");
// 세션(계정)이 바뀐 뒤 도착한 이전 계정의 실패 결과는 표시하지 않는다.
let releasePost;
heldPost = new Promise((resolve) => (releasePost = resolve));
await act(async () => selects()[0].props.onChange({ target: { value: "csv" } }));
await act(async () => buttonByText("선택한 입력 적용").props.onClick());
await wait(() => calls.length === 4, "held request sent");
await act(async () => session.login({ token: "other-token", user: { id: "admin-2", email: "b@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" } }));
await act(async () => releasePost(new Response(JSON.stringify({ code: "input_revision_conflict" }), { status: 409 })));
await tick();
await tick();
assert(!JSON.stringify(renderer.toJSON()).includes("다른 화면에서"), "late failure of the previous account is ignored");
heldPost = null;
await act(async () => renderer.unmount());
cache.clear();
console.log("PASS: late mutation result after account change ignored, account Query read, 5s polling only while running, manual input switch, retained candidate config, revision conflict without false success, pause does not apply draft config");
