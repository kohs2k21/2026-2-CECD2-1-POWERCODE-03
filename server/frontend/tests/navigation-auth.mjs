import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import {
  AuthSessionProvider,
  useAuthSession,
} from "../src/services/auth/AuthSessionProvider.tsx";
import { SessionGate, AdminGate } from "../src/app/SessionRoutes.tsx";
import { httpClient, HttpError } from "../src/services/api/client.ts";
import { useDraftStore } from "../src/stores/draftStore.ts";
import { notify } from "../src/lib/notify.ts";
import { useToasterStore } from "react-hot-toast";
import { useRealtimeAnomalies } from "../src/features/analysis/hooks/useRealtimeAnomalies.ts";
import {
  loginReturnPath,
  safeReturnPath,
  settingsReturnPath,
} from "../src/app/routePaths.ts";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map();
const listeners = new Map();
globalThis.window = {
  location: { origin: "http://localhost" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  addEventListener(name, listener) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(listener);
  },
  removeEventListener(name, listener) {
    listeners.get(name)?.delete(listener);
  },
  setTimeout,
  clearTimeout,
};
const storageEvent = (key) => {
  listeners
    .get("storage")
    ?.forEach((listener) =>
      listener({ key, storageArea: window.localStorage }),
    );
};
const StreamEditor = () => {
  useRealtimeAnomalies();
  return React.createElement("p", null, "protected editor");
};
const admin = {
  id: "admin",
  email: "admin@example.test",
  userType: "admin",
  createdAt: "2026-10-08T00:00:00Z",
};
const user = {
  ...admin,
  id: "user",
  email: "user@example.test",
  userType: "user",
};
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
let session;
let notifications;
const Probe = () => {
  session = useAuthSession();
  notifications = useToasterStore().toasts;
  return null;
};
const text = (node) =>
  !node
    ? ""
    : typeof node === "string"
      ? node
      : Array.isArray(node)
        ? node.map(text).join(" ")
        : text(node.children);
const tick = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 12));
  });
};
const wait = async (check) => {
  for (let i = 0; i < 40 && !check(); i++) await tick();
  assert.ok(check(), "state did not settle");
};
const mount = async (withStream = false) => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  const router = createMemoryRouter(
    [
      { path: "/login", element: React.createElement("p", null, "login") },
      {
        element: React.createElement(SessionGate),
        children: [
          {
            path: "/operations",
            element: React.createElement("p", null, "operations"),
          },
          {
            element: React.createElement(AdminGate),
            children: [
              {
                path: "/detection/create",
                element: withStream
                  ? React.createElement(StreamEditor)
                  : React.createElement("p", null, "protected editor"),
              },
            ],
          },
        ],
      },
    ],
    { initialEntries: ["/detection/create?tab=training"] },
  );
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(
        QueryClientProvider,
        { client: cache },
        React.createElement(
          AuthSessionProvider,
          null,
          React.createElement(Probe),
          React.createElement(RouterProvider, { router }),
        ),
      ),
    );
  });
  return {
    renderer,
    router,
    cache,
    close: async () => {
      await act(async () => renderer.unmount());
      cache.clear();
      router.dispose();
    },
  };
};

for (const path of [
  "https://evil.test/analysis",
  "//evil.test/analysis",
  "/\\evil.test/analysis",
  "/unknown",
  "/\\[",
  "javascript:alert(1)",
])
  assert.equal(safeReturnPath(path), "/operations");
assert.equal(
  safeReturnPath("/settings?section=profile"),
  "/settings?section=profile",
);
assert.equal(settingsReturnPath("/settings?section=profile"), "/operations");
assert.equal(
  settingsReturnPath("/analysis?category=Critical&q=abc"),
  "/analysis?category=Critical&q=abc",
);
assert.equal(
  loginReturnPath("/detection/create?tab=rules", "user"),
  "/operations",
);
assert.equal(
  loginReturnPath("/detection/create?tab=rules", "admin"),
  "/detection/create?tab=rules",
);

storage.set("token", "test-token");
let resolveMe;
globalThis.fetch = () =>
  new Promise((resolve) => {
    resolveMe = resolve;
  });
let mounted = await mount();
assert.equal(session.checking, true);
assert.ok(
  !text(mounted.renderer.toJSON()).includes("protected editor"),
  "pending /me must hide protected content",
);
await act(async () => resolveMe(json({ user: admin })));
await wait(() => session.user?.id === "admin");
assert.ok(text(mounted.renderer.toJSON()).includes("protected editor"));
useDraftStore.getState().edit("/detection/create", { value: "unsaved" });
mounted.cache.setQueryData(["private-records"], ["admin-only"]);
let finishOldRequest;
globalThis.fetch = () =>
  new Promise((resolve) => {
    finishOldRequest = resolve;
  });
const oldRequest = httpClient.get("/api/old-session");
const oldRequestRejected = assert.rejects(
  oldRequest,
  (error) => error.status === 401,
);
globalThis.fetch = async () =>
  json({ message: "denied", code: "admin_required" }, 403);
await assert.rejects(
  httpClient.get("/api/admin/test"),
  (error) =>
    error instanceof HttpError &&
    error.status === 403 &&
    error.code === "admin_required",
);
assert.equal(storage.get("token"), "test-token");
assert.equal(session.user.id, "admin", "403 must keep current session");
await act(async () => notify.error("이전 계정 작업 오류", "account-operation"));
assert.equal(notifications.length, 1);
await act(async () => session.login({ token: "user-token", user }));
assert.equal(
  notifications.length,
  0,
  "account change must remove old notifications",
);
await act(async () =>
  finishOldRequest(json({ message: "expired", code: "token_expired" }, 401)),
);
await oldRequestRejected;
assert.equal(
  storage.get("token"),
  "user-token",
  "late 401 from previous account must not clear the new session",
);
assert.equal(session.user.id, "user");
assert.equal(mounted.cache.getQueryData(["private-records"]), undefined);
assert.deepEqual(useDraftStore.getState().drafts, {});
assert.ok(
  !text(mounted.renderer.toJSON()).includes("protected editor"),
  "role change must close admin content",
);
await act(async () =>
  notify.success("현재 계정 화면 배치 반영", "account-operation"),
);
await act(async () => session.logout());
assert.equal(
  notifications.length,
  0,
  "logout must remove notifications immediately",
);
await wait(() => mounted.router.state.location.pathname === "/login");
assert.equal(
  mounted.router.state.location.state.returnTo,
  undefined,
  "explicit logout clears return destination",
);
await mounted.close();

storage.set("token", "expired-token");
globalThis.fetch = async () =>
  json({ message: "expired", code: "token_expired" }, 401);
mounted = await mount();
await wait(() => mounted.router.state.location.pathname === "/login");
assert.equal(storage.has("token"), false);
assert.equal(
  mounted.router.state.location.state.returnTo,
  "/detection/create?tab=training",
  "expiry preserves internal return destination",
);
await mounted.close();

for (const failure of [
  () => Promise.reject(new Error("offline")),
  () => Promise.resolve(json({ message: "temporarily unavailable" }, 503)),
  () =>
    Promise.resolve(json({ message: "denied", code: "admin_required" }, 403)),
]) {
  storage.set("token", "retry-token");
  globalThis.fetch = failure;
  mounted = await mount();
  await wait(() => Boolean(session.error));
  assert.equal(storage.get("token"), "retry-token");
  assert.ok(!text(mounted.renderer.toJSON()).includes("protected editor"));
  globalThis.fetch = async () => json({ user: admin });
  await act(async () => session.retry());
  await wait(() => session.user?.id === "admin");
  assert.ok(text(mounted.renderer.toJSON()).includes("protected editor"));
  await mounted.close();
}
// Cross-tab changes must gate A's protected content before /me for B succeeds.
storage.set("token", "storage-admin");
let streamSignal;
globalThis.fetch = async (url, options) => {
  if (url.includes("realtime-stream")) {
    streamSignal = options.signal;
    return new Response(
      new ReadableStream({
        start(controller) {
          options.signal.addEventListener(
            "abort",
            () => controller.error(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    );
  }
  return json({ user: admin });
};
mounted = await mount(true);
await wait(() => session.user?.id === "admin" && Boolean(streamSignal));
useDraftStore.getState().edit("/detection/create", { value: "admin draft" });
mounted.cache.setQueryData(["private-records"], ["admin-only"]);
const pendingMe = [];
globalThis.fetch = (_url, options) =>
  new Promise((resolve) => pendingMe.push({ resolve, signal: options.signal }));
await act(async () => session.retry());
assert.equal(pendingMe.length, 1);
await act(async () => notify.loading("이전 계정 작업 중", "account-operation"));
await act(async () => {
  storage.set("token", "storage-user");
  storageEvent("token");
});
await wait(() => pendingMe.length === 2);
assert.equal(session.user, null);
assert.equal(session.checking, true);
assert.ok(!text(mounted.renderer.toJSON()).includes("protected editor"));
assert.equal(mounted.cache.getQueryData(["private-records"]), undefined);
assert.deepEqual(useDraftStore.getState().drafts, {});
assert.equal(
  notifications.length,
  0,
  "cross-tab account change must remove loading notifications",
);
assert.equal(
  pendingMe[0].signal.aborted,
  true,
  "previous /me request must be cancelled",
);
assert.equal(
  streamSignal.aborted,
  true,
  "previous protected stream must stop on storage change",
);
// Simulate a transport that ignores cancellation and completes the old response anyway.
await act(async () => pendingMe[0].resolve(json({ user: admin })));
await tick();
assert.equal(session.user, null);
assert.equal(session.checking, true);
await act(async () => pendingMe[1].resolve(json({ user })));
await wait(() => session.user?.id === "user");
assert.ok(!text(mounted.renderer.toJSON()).includes("protected editor"));
await act(async () => {
  storage.delete("token");
  storageEvent("token");
});
await wait(() => mounted.router.state.location.pathname === "/login");
assert.equal(session.hasToken, false);
await act(async () =>
  session.login({ token: "storage-admin-again", user: admin }),
);
useDraftStore
  .getState()
  .edit("/detection/create", { value: "new admin draft" });
mounted.cache.setQueryData(["private-records"], ["another private row"]);
await act(async () => {
  storage.clear();
  storageEvent(null);
});
assert.equal(session.user, null);
assert.equal(session.hasToken, false);
assert.equal(mounted.cache.getQueryData(["private-records"]), undefined);
assert.deepEqual(useDraftStore.getState().drafts, {});
await mounted.close();
assert.equal(
  listeners.get("storage")?.size,
  0,
  "storage listener must be removed on unmount",
);

console.log(
  "navigation/auth QA passed: safe return, role guard, 401 vs 403, retry, account isolation, cross-tab change/delete/clear, cancelled old /me and SSE, late response exclusion",
);
