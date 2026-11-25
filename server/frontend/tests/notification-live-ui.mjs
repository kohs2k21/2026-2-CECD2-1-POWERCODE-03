import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 브라우저 수명주기·가짜 HTTP/SSE로 실제 컴포넌트 연결을 시험한다. 모든 값은 시험용이다.
register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const browserEvents = new EventTarget(), pageEvents = new EventTarget(), storage = new Map();
const originalTimer = globalThis.setTimeout;
globalThis.setTimeout = (call, delay, ...args) => originalTimer(call, [250, 3000, 10000].includes(delay) ? 5 : delay, ...args);
let online = true;
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { get onLine() { return online; } } });
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  addEventListener: browserEvents.addEventListener.bind(browserEvents), removeEventListener: browserEvents.removeEventListener.bind(browserEvents),
  setTimeout: globalThis.setTimeout, clearTimeout,
  matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
};
globalThis.document = { visibilityState: "visible", addEventListener: pageEvents.addEventListener.bind(pageEvents), removeEventListener: pageEvents.removeEventListener.bind(pageEvents) };
globalThis.HTMLFormElement = class {};
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { MemoryRouter } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { NotificationLiveProvider } = await import("../src/features/notifications/NotificationLiveProvider.tsx");
const { NotificationLiveContext } = await import("../src/features/notifications/NotificationLiveContext.ts");
const { NotificationLiveFeedback } = await import("../src/features/notifications/NotificationLiveFeedback.tsx");
const { NotificationsBell } = await import("../src/features/notifications/NotificationsBell.tsx");
const { NotificationsPage } = await import("../src/features/notifications/NotificationsPage.tsx");
const { subscribeDecisionStream } = await import("../src/services/api/decision-stream-hub.ts");
const { useSaveMySettings } = await import("../src/features/notifications/useNotifications.ts");
const U1 = "11111111-1111-4111-8111-111111111111", U2 = "22222222-2222-4222-8222-222222222222";
const settings = { decisionBasis: "interim", minRiskLevel: "info", includeUnrated: true, windowDays: 7, includeJobs: true };
const item = (seq, reason = "opened") => ({ key: `decision:${U1}`, type: "decision", seq: String(seq), reason, notifiedAt: "2026-11-25T00:00:00.000000Z", unread: true,
  decision: { detailId: U1, decisionId: U1, jobId: null, stage: "final", completeness: "complete", grade: "anomaly", riskStatus: "rated", riskLevel: "critical", hasUnratedEvidence: false, processId: "P01", transactionId: "TX-1" } });
const snapshot = (seq) => ({ observedAt: "2026-11-25T00:00:00Z", latestSeq: String(seq), latestJobAt: null, readThrough: "0", settings,
  window: { events: 5000, days: 7, fromSeq: "1", truncated: false, truncatedBy: null }, unreadCount: 1, total: 1, nextCursor: null, items: [item(seq)] });
let data = snapshot(10), failHead = false, failCount = false, delayedHead = null;
const requests = [], streams = [];
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const method = options.method ?? "GET";
  requests.push({ method, path: target.pathname, head: target.searchParams.get("limit") === "50" });
  if (target.pathname === "/api/pipeline/events") {
    let controller;
    const body = new ReadableStream({ start(value) { controller = value; } });
    const stream = { controller, signal: options.signal };
    streams.push(stream);
    options.signal.addEventListener("abort", () => { try { controller.close(); } catch {} }, { once: true });
    return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
  }
  if (target.pathname === "/api/notifications/unread-count") return failCount ? json({ code: "notification_storage_unavailable" }, 503)
    : json({ latestSeq: data.latestSeq, latestJobAt: null, unreadCount: 1, unreadByType: { decision: 1, incident: 0, protocol: 0, job: 0 } });
  if (target.pathname === "/api/notifications") {
    if (target.searchParams.get("limit") === "50") {
      if (delayedHead) return delayedHead;
      if (failHead) return json({ code: "notification_storage_unavailable" }, 503);
    }
    return json(data);
  }
  if (target.pathname === "/api/me/settings" && method === "PUT") {
    const next = JSON.parse(options.body).notifications;
    data = { ...data, settings: next };
    return json({ notifications: { ...next, isDefault: false, updatedAt: "2026-11-25T00:00:00Z" }, widgetLayout: { layout: null, updatedAt: null } });
  }
  return json({ code: "unhandled_test_request" }, 503);
};
let session, live, save;
const SessionProbe = () => { session = useAuthSession(); return null; };
const LiveProbe = () => { live = React.useContext(NotificationLiveContext); save = useSaveMySettings(); return null; };
const tick = () => act(async () => new Promise((resolve) => originalTimer(resolve, 10)));
const wait = async (check, label) => {
  for (let i = 0; i < 200 && !check(); i++) await tick();
  assert.ok(check(), label);
};
const textOf = (node) => !node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(textOf).join("") : textOf(node.children);
const countHeads = () => requests.filter((row) => row.head).length;
const emit = (target, name) => act(async () => target.dispatchEvent(new Event(name)));
const login = async (id) => act(async () => session.login({ token: `test-${id}`, user: { id, email: "test@example.test", userType: "admin", createdAt: "2026-11-25T00:00:00Z" } }));
const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false, refetchOnReconnect: false }, mutations: { gcTime: Infinity } } });
let renderer, stopAnalysis;
try {
  await act(async () => {
    renderer = create(React.createElement(MemoryRouter, { initialEntries: ["/notifications"] }, React.createElement(QueryClientProvider, { client },
      React.createElement(AuthSessionProvider, null, React.createElement(SessionProbe),
        React.createElement(NotificationLiveProvider, null, React.createElement(LiveProbe), React.createElement(NotificationsBell),
          React.createElement(NotificationLiveFeedback), React.createElement(NotificationsPage))))));
  });
  await login(U1);
  await wait(() => live.state.lastCheckedAt !== null, "최초 성공 확인 시각");
  assert.equal(live.state.banner, null, "최초 이력은 배너 없음");
  assert.equal(streams.length, 1, "종 버튼·페이지는 SSE 한 개 공유");
  stopAnalysis = subscribeDecisionStream({ after: "10", onEvent() {} });
  assert.equal(streams.length, 1, "분석 화면 구독도 같은 SSE 공유");
  assert.ok(textOf(renderer.toJSON()).includes("마지막 확인"));

  data = { ...snapshot(11), items: [item(11, "risk_raised")] };
  await act(async () => streams[0].controller.enqueue(new TextEncoder().encode(`id: 11\nevent: decision\ndata: {"id":"${U1}","status":"complete"}\n\n`)));
  await wait(() => live.state.banner?.riskRaised === true, "이벤트 뒤 서버 목록 기반 배너");
  assert.ok(textOf(renderer.toJSON()).includes("위험도가 상승한 알림"));
  const close = renderer.root.findAll((node) => node.type === "button" && node.props["aria-label"] === "새 알림 배너 닫기")[0];
  await act(async () => close.props.onClick());
  assert.equal(live.state.banner, null);
  assert.ok(requests.every((row) => row.method === "GET"), "표시·닫기는 읽음 API 호출 없음");

  document.visibilityState = "hidden"; await emit(pageEvents, "visibilitychange");
  assert.equal(live.state.connection, "paused");
  const hiddenHeads = countHeads();
  data = snapshot(12); await emit(browserEvents, "focus"); await tick();
  assert.equal(countHeads(), hiddenHeads, "숨긴 화면에서는 다시 조회하지 않음");
  document.visibilityState = "visible"; await emit(pageEvents, "visibilitychange");
  await wait(() => live.state.banner !== null, "복귀 시 놓친 새 알림 확인");
  assert.equal(streams.length, 1, "분석 구독이 남아 있으면 알림 복귀도 연결 추가 없음");
  stopAnalysis(); stopAnalysis = null;
  online = false; await emit(browserEvents, "offline");
  assert.equal(live.state.connection, "offline"); assert.ok(streams[0].signal.aborted);
  assert.ok(textOf(renderer.toJSON()).includes("네트워크 연결 끊김"));
  online = true; await emit(browserEvents, "online");
  await wait(() => live.state.connection === "connected" && !live.state.refreshing, "온라인 복귀");
  assert.equal(streams.length, 2);

  const successfulAt = live.state.lastCheckedAt;
  failHead = true; await act(async () => live.refresh());
  await wait(() => live.state.refreshFailed, "조회 실패 표시");
  assert.equal(live.state.lastCheckedAt, successfulAt); assert.ok(textOf(renderer.toJSON()).includes("최신 상태가 아닐 수 있습니다"));
  failHead = false; await act(async () => live.refresh()); await wait(() => !live.state.refreshFailed && !live.state.refreshing, "수동 확인 복구");
  assert.equal(live.state.banner?.riskRaised, false);
  await act(async () => save.mutate({ notifications: { ...settings, windowDays: 30 } }));
  await wait(() => save.isSuccess && !live.state.refreshing, "설정 저장 완료");
  assert.equal(live.state.banner, null, "설정 변경 뒤 배너 초기화");
  assert.ok(requests.filter((row) => row.method !== "GET").every((row) => row.path === "/api/me/settings"), "읽음은 별도 동작");

  let release;
  delayedHead = new Promise((resolve) => { release = resolve; });
  await act(async () => live.refresh()); await tick();
  assert.ok(live.state.refreshing);
  const stale = snapshot(100);
  data = snapshot(20); failCount = true;
  await login(U2);
  await wait(() => live.state.refreshFailed, "새 계정 최초 배지 조회 실패 표시");
  assert.equal(live.state.banner, null); assert.equal(live.state.lastCheckedAt, null);
  delayedHead = null; await act(async () => release(json(stale)));
  assert.equal(live.state.lastCheckedAt, null, "이전 계정 응답은 새 계정에 표시되지 않음");
  failCount = false; await emit(browserEvents, "focus");
  await wait(() => live.state.lastCheckedAt !== null && !live.state.refreshFailed, "최초 실패도 포커스 복귀로 재시작");
  assert.equal(live.state.banner, null, "새 계정 최초 이력도 무음");
  await act(async () => session.logout());
  assert.equal(live.state.banner, null); assert.equal(live.state.lastCheckedAt, null);
} finally {
  stopAnalysis?.();
  if (renderer) await act(async () => renderer.unmount());
  client.clear();
  globalThis.setTimeout = originalTimer;
}
assert.ok(streams.every((stream) => stream.signal.aborted), "종료 뒤 모든 SSE 정리");
console.log("notification live UI QA passed: shared SSE, filtered banner, no implicit reads, visibility, offline/recovery, stale status, settings and account isolation");
