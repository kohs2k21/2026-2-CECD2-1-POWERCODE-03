import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// Toast CSS initializes in SSR mode; Query is imported after the browser timer shim.
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
  addEventListener(name, fn) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
  },
  removeEventListener(name, fn) {
    listeners.get(name)?.delete(fn);
  },
  setTimeout,
  clearTimeout,
};
// The product logo is an asset URL, not an executable dependency in the test renderer.
register("./notifications-assets-loader.mjs", import.meta.url);
const { QueryClient, QueryClientProvider, timeoutManager } =
  await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } =
  await import("../src/services/auth/AuthSessionProvider.tsx");
const { SessionGate } = await import("../src/app/SessionRoutes.tsx");
const { AppLayout } = await import("../src/app/AppLayout.tsx");
const { NotificationsPage } =
  await import("../src/features/notifications/NotificationsPage.tsx");
const { useNotificationJobs } =
  await import("../src/features/notifications/NotificationsProvider.tsx");
const { useSharedRealtimeAnomalies } =
  await import("../src/features/analysis/RealtimeAnomalyProvider.tsx");
const { Modal } = await import("../src/components/ui/Modal.tsx");
const { Button } = await import("../src/components/ui/button.tsx");
const { Dialog } = await import("../src/components/ui/dialog.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
const { settingsReturnPath, loginReturnPath } =
  await import("../src/app/routePaths.ts");
const { DetectionGatewayProvider, detectionPollingInterval } =
  await import("../src/features/detection/data/useDetectionQuery.tsx");
const { developmentDetectionData } =
  await import("../src/features/detection/data/fixtures.ts");
const { measuredProgress, resolveNotificationFilter, taskNotifications } =
  await import("../src/features/notifications/notificationPresentation.ts");

const intervals = new Map();
let intervalId = 0;
timeoutManager.setTimeoutProvider({
  setTimeout,
  clearTimeout,
  setInterval(callback, delay) {
    const id = ++intervalId;
    intervals.set(id, { callback, delay });
    return id;
  },
  clearInterval(id) {
    intervals.delete(id);
  },
});
const text = (node) =>
  !node
    ? ""
    : typeof node === "string"
      ? node
      : Array.isArray(node)
        ? node.map(text).join(" ")
        : text(node.children ?? node.props?.children);
const descendants = (node) =>
  Array.isArray(node)
    ? node.flatMap(descendants)
    : React.isValidElement(node)
      ? [node, ...descendants(node.props.children)]
      : [];
const settle = async () =>
  act(async () => new Promise((resolve) => setTimeout(resolve, 15)));
const until = async (predicate) => {
  for (let i = 0; i < 40 && !predicate(); i++) await settle();
  assert.ok(predicate(), "state did not settle");
};
const admin = {
  id: "admin",
  email: "admin@example.test",
  userType: "admin",
  createdAt: "2026-10-09T00:00:00Z",
};
const user = {
  ...admin,
  id: "user",
  email: "user@example.test",
  userType: "user",
};
let session, realtime, jobs;
const SessionProbe = () => {
  session = useAuthSession();
  return null;
};
const SharedProbe = () => {
  realtime = useSharedRealtimeAnomalies();
  jobs = useNotificationJobs();
  return React.createElement("p", null, "shared feed");
};
const fixture = structuredClone(developmentDetectionData);
fixture.trainingJobs = [
  {
    ...fixture.trainingJobs[0],
    id: "current-job",
    name: "현재 학습",
    state: "running",
    progress: 17,
  },
];
fixture.evaluations = [];
let readCount = 0,
  readFailure = false,
  pendingRead = null;
const gateway = {
  async read(signal) {
    readCount++;
    if (pendingRead) return pendingRead(signal);
    if (readFailure) throw new Error("작업 조회 실패");
    return structuredClone(fixture);
  },
  async request() {
    assert.fail("notifications must never submit an action");
  },
};
const streams = [];
let forbiddenFetch = 0;
globalThis.fetch = async (url, options = {}) => {
  if (url.includes("realtime-stream")) {
    const record = { signal: options.signal, controller: null };
    streams.push(record);
    return new Response(
      new ReadableStream({
        start(controller) {
          record.controller = controller;
          options.signal.addEventListener(
            "abort",
            () => controller.error(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        },
      }),
      { status: 200, headers: { "content-type": "text/event-stream" } },
    );
  }
  if (url.includes("/auth/me"))
    return new Response(JSON.stringify({ user }), {
      headers: { "content-type": "application/json" },
    });
  forbiddenFetch++;
  throw new Error("unexpected request");
};
const event = {
  schemaVersion: 1,
  eventId: "event /1",
  provenance: "backend-ingest",
  processName: "실제 수신",
  responseCode: "E123",
  anomalyScore: 0.5,
  processTimeMs: 3000,
  riskScore: 42,
  riskLevel: 2,
  severity: "Warning",
};
const emit = (record, value = event) =>
  record.controller.enqueue(
    new TextEncoder().encode(
      `event: anomaly\nid: ${value.eventId}\ndata: ${JSON.stringify(value)}\n\n`,
    ),
  );

assert.equal(measuredProgress(0), 0);
for (const value of [null, undefined, NaN, Infinity, -1, 101, "50"])
  assert.equal(measuredProgress(value), null);
assert.equal(resolveNotificationFilter("tasks", "user"), "operations");
assert.equal(resolveNotificationFilter("unknown", "admin"), "all");
assert.equal(detectionPollingInterval(undefined), false);
assert.equal(detectionPollingInterval(fixture), 5000);
assert.equal(detectionPollingInterval({ ...fixture, trainingJobs: [] }), false);
assert.equal(
  detectionPollingInterval({
    ...fixture,
    trainingJobs: [],
    evaluations: [{ state: "accepted" }],
  }),
  5000,
);
assert.equal(
  taskNotifications(fixture)[0].href,
  "/detection/create?tab=training&job=current-job",
);
assert.equal(
  settingsReturnPath("/notifications?filter=tasks&q=retain"),
  "/notifications?filter=tasks&q=retain",
);
assert.equal(
  loginReturnPath("/notifications?filter=operations", "user"),
  "/notifications?filter=operations",
);

const cache = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, gcTime: Infinity },
  },
});
const router = createMemoryRouter(
  [
    { path: "/login", element: React.createElement("p", null, "login") },
    {
      element: React.createElement(SessionGate),
      children: [
        {
          element: React.createElement(AppLayout),
          children: [
            {
              path: "/notifications",
              element: React.createElement(NotificationsPage),
            },
            { path: "/operations", element: React.createElement(SharedProbe) },
            { path: "/analysis", element: React.createElement(SharedProbe) },
            {
              path: "/detection/create",
              element: React.createElement(SharedProbe),
            },
            { path: "/settings", element: React.createElement(SharedProbe) },
          ],
        },
      ],
    },
  ],
  { initialEntries: ["/operations"] },
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
        React.createElement(SessionProbe),
        React.createElement(
          DetectionGatewayProvider,
          { gateway },
          React.createElement(RouterProvider, { router }),
        ),
      ),
    ),
  );
});
assert.equal(streams.length, 0);
assert.equal(
  readCount,
  0,
  "anonymous layout must neither connect nor read jobs",
);
await act(async () => session.login({ token: "temporary-admin", user: admin }));
await act(async () => router.navigate("/operations"));
await until(() => realtime?.status === "open" && jobs?.data);
assert.equal(streams.length, 1);
assert.equal(readCount, 1);
assert.equal(
  [...intervals.values()].filter((item) => item.delay === 5000).length,
  1,
);
await act(async () => {
  emit(streams[0]);
  emit(streams[0]);
});
await until(() => realtime.events.length === 1);
await act(async () => router.navigate("/analysis"));
assert.equal(
  streams.length,
  1,
  "analysis is a consumer, not another SSE connection",
);
assert.equal(realtime.events[0].eventId, event.eventId);
await act(async () =>
  router.navigate("/notifications?filter=all&retained=yes"),
);
await settle();
assert.equal(streams.length, 1);
assert.equal(readCount, 1, "popup and page share the polling observer");
assert.match(text(renderer.toJSON()), /실제 수신/);
assert.match(text(renderer.toJSON()), /17\s*%/);
assert.ok(
  renderer.root
    .findAllByType("a")
    .some(
      (node) =>
        node.props.href === "/analysis?category=Warning&event=event+%2F1",
    ),
);
assert.equal(
  renderer.root
    .findAllByType("a")
    .filter(
      (node) =>
        node.props["aria-current"] === "page" &&
        node.props.href === "/operations",
    ).length,
  0,
);
const modal = () => renderer.root.findByType(Modal);
await act(async () => modal().props.onOpenChange(true));
assert.equal(modal().props.isOpen, true);
await act(async () => modal().props.onOpenChange(false));
assert.equal(
  modal().props.isOpen,
  false,
  "Escape/close callback closes without submitting",
);
await act(async () => modal().props.onOpenChange(true));
await act(async () =>
  router.navigate("/notifications?filter=tasks&retained=yes"),
);
assert.equal(modal().props.isOpen, false, "route changes close the popup");
assert.equal(router.state.location.search, "?filter=tasks&retained=yes");
assert.doesNotMatch(text(renderer.toJSON()), /실제 수신/);
await act(async () => router.navigate(-1));
assert.equal(router.state.location.search, "?filter=all&retained=yes");

const polling = [...intervals.values()].find((item) => item.delay === 5000);
await act(async () => polling.callback());
await until(() => readCount === 2);
assert.equal(streams.length, 1);
fixture.trainingJobs[0].state = "succeeded";
await act(async () =>
  [...intervals.values()].find((item) => item.delay === 5000).callback(),
);
await until(() => ![...intervals.values()].some((item) => item.delay === 5000));
assert.equal(
  readCount,
  3,
  "polling stops for terminal states without inventing progress",
);
readFailure = true;
const jobsQuery = cache
  .getQueryCache()
  .find({ queryKey: ["detection-workbench", "admin"] });
await act(async () => jobsQuery.fetch().catch(() => {}));
await until(() => text(renderer.toJSON()).includes("마지막 조회 결과"));
assert.match(text(renderer.toJSON()), /작업 조회 실패/);
assert.match(
  text(renderer.toJSON()),
  /17\s*%/,
  "stale data stays marked as a previous observation",
);
readFailure = false;
await act(async () => jobsQuery.fetch());
await settle();
assert.doesNotMatch(text(renderer.toJSON()), /작업 조회 실패/);

// Navigation from a real editor still reaches the one shared guard.
await act(async () => router.navigate("/detection/create?tab=features"));
await act(async () =>
  useDraftStore
    .getState()
    .edit("/detection/create", { customFeatureIds: ["custom-1"] }),
);
await act(async () => router.navigate("/notifications?filter=all"));
assert.equal(router.state.location.pathname, "/detection/create");
const guard = renderer.root
  .findAllByType(Dialog)
  .find((node) => node.props.open);
assert.ok(guard);
const preserve = descendants(guard.props.children).find(
  (node) =>
    node.type === Button &&
    text(node.props.children) === "변경 사항 저장 후 이동",
);
await act(async () => preserve.props.onClick());
assert.equal(router.state.location.pathname, "/notifications");
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);

// Account change cancels an older task read and resets the feed immediately.
let finishOldRead, oldReadSignal;
pendingRead = (signal) => {
  oldReadSignal = signal;
  return new Promise((resolve) => {
    finishOldRead = resolve;
  });
};
await act(async () => {
  void jobsQuery.fetch().catch(() => {});
});
await act(async () => session.login({ token: "temporary-user", user }));
await until(() =>
  text(renderer.toJSON()).includes("수신한 운영 알림이 없습니다"),
);
assert.equal(oldReadSignal.aborted, true);
assert.equal(streams[0].signal.aborted, true);
assert.equal(
  [...intervals.values()].filter((item) => item.delay === 5000).length,
  0,
);
const readsBeforeUser = readCount;
await act(async () => finishOldRead(structuredClone(fixture)));
await settle();
assert.doesNotMatch(text(renderer.toJSON()), /현재 학습|실제 수신/);
await act(async () =>
  router.navigate("/notifications?filter=tasks&retained=yes"),
);
assert.equal(router.state.location.search, "?filter=operations&retained=yes");
assert.doesNotMatch(text(renderer.toJSON()), /진행 작업/);
assert.equal(
  readCount,
  readsBeforeUser,
  "ordinary users do not query administrator jobs",
);
assert.equal(cache.getQueryData(["detection-workbench", "admin"]), undefined);
assert.deepEqual(useDraftStore.getState().drafts, {});
await act(async () => session.logout());
assert.equal(router.state.location.pathname, "/login");
assert.ok(streams.every((item) => item.signal.aborted));

// A fresh read has real loading and empty states, rather than fixture fallback.
let finishFreshRead;
pendingRead = () =>
  new Promise((resolve) => {
    finishFreshRead = resolve;
  });
await act(async () =>
  session.login({ token: "temporary-admin-again", user: admin }),
);
await act(async () => router.navigate("/notifications?filter=all"));
assert.match(text(renderer.toJSON()), /학습·평가 작업을 불러오는 중/);
assert.doesNotMatch(text(renderer.toJSON()), /현재 학습|실제 수신/);
await act(async () =>
  finishFreshRead({
    ...structuredClone(fixture),
    trainingJobs: [],
    evaluations: [],
  }),
);
await until(() =>
  text(renderer.toJSON()).includes("조회된 학습·평가 작업이 없습니다"),
);
assert.equal(
  [...intervals.values()].filter((item) => item.delay === 5000).length,
  0,
);
pendingRead = null;
const freshStream = streams.at(-1);
await act(async () => emit(freshStream));
await until(() => text(renderer.toJSON()).includes("실제 수신"));
// Same account, different session: mask the old feed before effect cleanup.
await act(async () =>
  session.login({ token: "temporary-admin-new-session", user: admin }),
);
assert.doesNotMatch(text(renderer.toJSON()), /실제 수신/);
assert.equal(freshStream.signal.aborted, true);
await act(async () => session.logout());
assert.ok(streams.every((item) => item.signal.aborted));
assert.equal(forbiddenFetch, 0);
await act(async () => renderer.unmount());
router.dispose();
cache.clear();
assert.equal(intervals.size, 0);
console.log(
  "notifications QA passed: one shared SSE, dedupe and event links, truthful progress, single admin poll and terminal stop, failure/stale/retry, role masking and old read cancellation, URL return/history, popup lifecycle, preserved draft guard and cleanup",
);
