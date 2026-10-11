import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
const listeners = new Map();
globalThis.window = {
  location: { origin: "http://localhost" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  addEventListener: (name, fn) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
  },
  removeEventListener: (name, fn) => listeners.get(name)?.delete(fn),
  setTimeout,
  clearTimeout,
};
const { QueryClient, QueryClientProvider } =
  await import("@tanstack/react-query");
const { AuthSessionProvider, useAuthSession } =
  await import("../src/services/auth/AuthSessionProvider.tsx");
const { AnomalyFeedback } =
  await import("../src/features/analysis/components/AnomalyFeedback.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
let session;
const Probe = () => {
  session = useAuthSession();
  return null;
};
const cache = new QueryClient({
  defaultOptions: { queries: { gcTime: Infinity, retry: false } },
});
let renderer;
const root = (capability, eventId = "e1") =>
  React.createElement(
    QueryClientProvider,
    { client: cache },
    React.createElement(
      AuthSessionProvider,
      null,
      React.createElement(Probe),
      React.createElement(AnomalyFeedback, { capability, eventId }),
    ),
  );
await act(async () => {
  renderer = create(root(false));
});
const admin = {
  id: "local-admin",
  email: "admin@example.test",
  userType: "admin",
  createdAt: "2026-10-09T00:00:00Z",
};
await act(async () =>
  session.login({ token: "local-test-token", user: admin }),
);
let writes = 0;
globalThis.fetch = async () => {
  writes++;
  return new Response(null, { status: 503 });
};
const textarea = () => renderer.root.findByType("textarea");
const button = () => renderer.root.findByType("button");
await act(async () =>
  textarea().props.onChange({ target: { value: "조치 검토" } }),
);
assert.equal(useDraftStore.getState().drafts["/analysis"].dirty, true);
assert.equal(button().props.disabled, true);
await act(async () => button().props.onClick());
assert.equal(
  writes,
  0,
  "false capability must reject even direct click invocation",
);
await act(async () => renderer.update(root(true)));
await act(async () => button().props.onClick());
assert.equal(writes, 1);
assert.equal(
  textarea().props.value,
  "조치 검토",
  "failed server write keeps draft",
);
await act(async () => renderer.update(root(true, "e2")));
await act(async () =>
  textarea().props.onChange({ target: { value: "근거 부족 조사" } }),
);
await act(async () => renderer.update(root(true)));
assert.equal(
  textarea().props.value,
  "조치 검토",
  "event switching restores its draft",
);
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      eventId: "e1",
      feedbackId: "feedback-1",
      savedAt: "2026-10-09T10:00:00Z",
    }),
  );
await act(async () => button().props.onClick());
assert.equal(
  useDraftStore.getState().drafts["/analysis"].value.feedbackByEvent.e1,
  undefined,
);
assert.equal(
  useDraftStore.getState().drafts["/analysis"].value.feedbackByEvent.e2.reason,
  "근거 부족 조사",
  "other event draft survives server save",
);
await act(async () => {
  const store = useDraftStore.getState();
  store.edit("/analysis", {
    ...store.drafts["/analysis"].value,
    unrelatedDraft: { note: "보존할 분석 초안" },
  });
  renderer.update(root(true, "e2"));
});
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      eventId: "e2",
      feedbackId: "feedback-2",
      savedAt: "2026-10-09T10:00:00Z",
    }),
  );
await act(async () => button().props.onClick());
assert.deepEqual(
  useDraftStore.getState().drafts["/analysis"].value,
  { unrelatedDraft: { note: "보존할 분석 초안" } },
  "saving the last event removes only its feedback field and preserves sibling drafts",
);
assert.equal(
  useDraftStore.getState().drafts["/analysis"].dirty,
  true,
  "unrelated analysis draft remains protected",
);
await act(async () => {
  renderer.update(root(true));
  useDraftStore.getState().edit("/analysis", {
    feedbackByEvent: { e1: { kind: "action", reason: "피드백만 남은 초안" } },
  });
});
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      eventId: "e1",
      feedbackId: "feedback-3",
      savedAt: "2026-10-09T10:00:00Z",
    }),
  );
await act(async () => button().props.onClick());
assert.equal(
  useDraftStore.getState().drafts["/analysis"],
  undefined,
  "discard the route draft only when no feedback or sibling fields remain",
);
await act(async () =>
  textarea().props.onChange({ target: { value: "미완료 요청" } }),
);
let signal;
let resolveRequest;
globalThis.fetch = (_url, options) => {
  signal = options.signal;
  return new Promise((resolve) => {
    resolveRequest = resolve;
  });
};
await act(async () => button().props.onClick());
await act(async () =>
  session.login({
    token: "local-user-test",
    user: { ...admin, id: "local-user", userType: "user" },
  }),
);
assert.equal(signal.aborted, true);
assert.equal(
  renderer.root.findAllByType("textarea").length,
  0,
  "ordinary user has no feedback editor",
);
assert.deepEqual(
  useDraftStore.getState().drafts,
  {},
  "session change clears drafts",
);
await act(async () =>
  resolveRequest(
    new Response(
      JSON.stringify({
        eventId: "e1",
        feedbackId: "old",
        savedAt: "2026-10-09T10:00:00Z",
      }),
    ),
  ),
);
assert.deepEqual(
  useDraftStore.getState().drafts,
  {},
  "old request cannot recreate drafts",
);
await act(async () => renderer.unmount());
cache.clear();
console.log(
  "anomaly feedback QA passed: admin UI, disabled capability, retained failures, event drafts, server receipt, abort and account isolation",
);
