import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
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
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { SavedDecisionsPage } = await import("../src/features/analysis/SavedDecisionsPage.tsx");
const { mergeDecisionLists, savedDecisionsQueryName, SAVED_DECISION_LIMIT } = await import(
  "../src/features/analysis/data/savedDecisions.ts"
);

const decision = (id, code) => ({
  id, sourceId: "source", processObservationId: `obs-${id}`, configVersionId: "config",
  status: "complete", ruleStatus: "succeeded", mlStatus: "succeeded", decisionCode: code,
  anomalyScore: null, decidedAt: "2026-10-15T00:00:00Z",
});

// 1) 목록 응답 병합 규칙
const listed = [decision("a", "LIST-A"), decision("b", "LIST-B-OLD")];
const streamed = [decision("c", "SSE-C"), decision("b", "SSE-B-NEW")];
assert.deepEqual(mergeDecisionLists(listed, streamed).map((item) => item.decisionCode), ["SSE-C", "SSE-B-NEW", "LIST-A"]);
assert.deepEqual(mergeDecisionLists(listed, []).map((item) => item.id), ["a", "b"]);
assert.equal(
  mergeDecisionLists(Array.from({ length: 60 }, (_, i) => decision(`l${i}`, null)), [decision("s", null)]).length,
  SAVED_DECISION_LIMIT,
);

// 2) 최초 목록 응답 전에 SSE로 받은 판정이 늦은 목록 응답에 덮어쓰이지 않는다
let releaseList;
let listResponse = () => new Promise((resolve) => (releaseList = resolve));
let streamFrames = [];
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
globalThis.fetch = async (url, options = {}) => {
  const path = String(url);
  if (path.endsWith("/api/pipeline/decisions")) return listResponse();
  if (path.endsWith("/api/pipeline/events")) {
    const frames = streamFrames;
    streamFrames = [];
    return new Response(
      new ReadableStream({
        start(controller) {
          for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame));
          options.signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } },
    );
  }
  return json({ code: "unavailable" }, 503);
};
const frame = (id, value) => `id: ${id}\nevent: decision\ndata: ${JSON.stringify(value)}\n\n`;
const tick = () => act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
const text = (node) =>
  !node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join(" ") : text(node.children);
const wait = async (check, label) => {
  for (let i = 0; i < 60 && !check(); i++) await tick();
  assert.ok(check(), label);
};

let session;
const Probe = () => {
  session = useAuthSession();
  return null;
};
const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
const router = createMemoryRouter(
  [{ path: "/analysis/process-decisions", element: React.createElement(SavedDecisionsPage) }],
  { initialEntries: ["/analysis/process-decisions"] },
);
streamFrames = [frame(1, decision("s1", "SSE-FIRST"))];
let renderer;
await act(async () => {
  renderer = create(
    React.createElement(QueryClientProvider, { client: cache },
      React.createElement(AuthSessionProvider, null, React.createElement(Probe), React.createElement(RouterProvider, { router }))),
  );
});
await act(async () =>
  session.login({ token: "local-token", user: { id: "u1", email: "u@example.test", userType: "user", createdAt: "2026-10-01T00:00:00Z" } }),
);
await wait(() => typeof releaseList === "function", "list request pending");
await wait(
  () => (cache.getQueryData([savedDecisionsQueryName, "u1"]) ?? []).some((item) => item.id === "s1"),
  "the SSE decision reaches the cache before the list response (guards the test order)",
);
assert.equal(cache.getQueryState([savedDecisionsQueryName, "u1"]).fetchStatus, "fetching", "list response is still pending");
await act(async () => releaseList(json({ items: [decision("l1", "LIST-ONE")] })));
await wait(() => text(renderer.toJSON()).includes("LIST-ONE"), "list response shown");
const shown = text(renderer.toJSON());
assert.ok(shown.includes("SSE-FIRST"), "decision received over SSE before the list response is kept");
assert.ok(shown.indexOf("SSE-FIRST") < shown.indexOf("LIST-ONE"), "streamed (newer) decision stays first");

// 3) 재조회 실패: 이전 데이터가 빈 목록이어도 '없음'이 아니라 오류로 표시
const key = [savedDecisionsQueryName, "u1"];
await act(async () => cache.setQueryData(key, []));
listResponse = async () => json({ message: "decisions unavailable" }, 503);
await act(async () => cache.refetchQueries({ queryKey: key }));
await wait(() => cache.getQueryState(key)?.status === "error", "refetch failed");
await wait(() => text(renderer.toJSON()).includes("decisions unavailable"), "error state rendered");
const failed = text(renderer.toJSON());
assert.ok(failed.includes("decisions unavailable"), "failure is shown as an error state");
assert.equal(renderer.root.findAll((node) => node.props.role === "alert" && node.props["data-slot"] === "error-state").length, 1);
assert.ok(!failed.includes("저장된 판정이 없습니다"), "failure is not hidden as an empty result");

await act(async () => renderer.unmount());
router.dispose();
cache.clear();
console.log(
  "saved decisions QA passed: SSE decisions kept when a late list response arrives, newest first, 50 limit, refetch failure shown as error even with empty previous data",
);
