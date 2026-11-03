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
// Radix RadioGroup 효과용 최소 DOM 대역(포털 대상 body는 없음 → Dialog 내용은 렌더하지 않음).
globalThis.HTMLFormElement = class {};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
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
    state.control = { ...state.control, mode: body.mode, revision: 5, status: "running", configVersionId: body.configVersionId ?? state.control.configVersionId,
      settings: body.mode === "csv" ? { replayId: body.replayId, rate: body.rate, repeat: body.repeat } : state.control.settings, position: 10 };
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
const { RadioGroup } = await import("../src/components/ui/radio-group.tsx");
const { Dialog } = await import("../src/components/ui/dialog.tsx");
// Radix Dialog 내용은 포털(document 없음)로 렌더되지 않으므로, Dialog 요소의 자식 트리를 직접 살핀다(navigation-draft와 같은 방식).
const walk = (node, found = []) => {
  if (Array.isArray(node)) node.forEach((child) => walk(child, found));
  else if (node && typeof node === "object" && node.props) {
    found.push(node);
    walk(node.props.children, found);
  }
  return found;
};
const textOf = (node) => (typeof node === "string" || typeof node === "number" ? String(node) : Array.isArray(node) ? node.map(textOf).join("") : node?.props ? textOf(node.props.children) : "");
const dialog = () => renderer.root.findByType(Dialog);
const dialogButton = (label) => walk(dialog().props.children).find((el) => el.props.onClick && textOf(el.props.children) === label);
const dialogField = (id) => walk(dialog().props.children).find((el) => el.props.id === id);
const choose = (mode) => act(async () => renderer.root.findByType(RadioGroup).props.onValueChange(mode));
const rendered = () => JSON.stringify(renderer.toJSON());
await wait(() => renderer.root.findAllByType(RadioGroup).length === 1, "input mode radios render after the account query");
assert.equal(reads, 1, "one read after login");

const observer = () => cache.getQueryCache().findAll({ queryKey: ["input-state"] })[0].observers[0];
const interval = (data) => observer().options.refetchInterval({ state: { data } });
assert.equal(interval(state), false, "paused input is not polled");
assert.equal(interval({ ...state, control: { ...state.control, status: "running" } }), RUNNING_POLL_MS, "running input polls every 5s");
assert.equal(inputRunning(undefined), false);
assert(rendered().includes("일시정지(입력 중단)"), "current mode label");
assert.equal(dialog().props.open, false);

// CSV 선택 → 확인 창(와이어프레임 문구)에서 구성 선택 후 전환. 요청 revision은 창을 연 시점 값.
await choose("csv");
assert.equal(dialog().props.open, true);
assert.equal(calls.length, 0, "selecting a radio only opens the confirmation");
assert(textOf(dialog().props.children).includes("CSV 재생으로 바꿀까요"));
await act(async () => dialogField("input-config").props.onChange({ target: { value: "candidate" } }));
await act(async () => dialogButton("CSV 재생으로 전환").props.onClick());
await wait(() => reads >= 2, "state is re-read after a mode change");
assert.deepEqual(calls[0], { mode: "csv", expectedRevision: 4, replayId: "replay", rate: 10, repeat: 1, restart: false, configVersionId: "candidate" });
await wait(() => rendered().includes("revision") && rendered().includes("5"), "latest server state is shown");
assert(rendered().includes("CSV 재생(시뮬레이션)"));
assert(rendered().includes("four-files"), "CSV target shown");
assert(rendered().includes("재생 설정 변경"), "CSV re-apply command for admin");

// 충돌: input_revision_conflict 코드와 안내를 함께 표시, 성공으로 바꾸지 않음.
failure = true;
await choose("api");
assert(textOf(dialog().props.children).includes("실서버 API 입력으로 바꿀까요"));
await act(async () => dialogButton("실서버 API로 전환").props.onClick());
await wait(() => rendered().includes("input_revision_conflict"), "conflict is shown without false success");
assert(rendered().includes("다른 화면에서"));
assert.equal(state.control.mode, "csv");
failure = false;
await choose("paused");
await act(async () => dialogButton("일시정지").props.onClick());
await wait(() => calls.length === 3, "pause request sent");
assert.equal(calls.at(-1).mode, "paused");
assert(!("configVersionId" in calls.at(-1)), "pause does not apply the draft configuration");
// 취소는 요청 없이 닫는다.
await choose("api");
await act(async () => dialogButton("취소").props.onClick());
assert.equal(dialog().props.open, false);
assert.equal(calls.length, 3);
// 세션(계정)이 바뀐 뒤 도착한 이전 계정의 실패 결과는 표시하지 않는다.
let releasePost;
heldPost = new Promise((resolve) => (releasePost = resolve));
await choose("csv");
await act(async () => dialogButton(textOf(walk(dialog().props.children).filter((el) => el.props.onClick).at(-1).props.children)).props.onClick());
await wait(() => calls.length === 4, "held request sent");
await act(async () => session.login({ token: "other-token", user: { id: "admin-2", email: "b@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" } }));
await act(async () => releasePost(new Response(JSON.stringify({ code: "input_revision_conflict" }), { status: 409 })));
await tick();
await tick();
assert(!rendered().includes("input_revision_conflict"), "late failure of the previous account is ignored");
heldPost = null;
await act(async () => renderer.unmount());
cache.clear();
console.log("PASS: late mutation result after account change ignored, account Query read, 5s polling only while running, radio + confirmation input switch, retained candidate config, revision conflict code without false success, pause does not apply draft config, cancel sends nothing");
