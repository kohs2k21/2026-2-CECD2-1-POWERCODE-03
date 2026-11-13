import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 알림 패널·배지·알림 설정 화면 시험(contracts/notifications.md, 로컬 가짜 fetch만): 응답 해석·문구, 배지(서버 미확인 수·99+·확인 불가),
// 패널(읽지 않음/전체·항목 읽음·모두 읽음·더 보기·이동 링크·빈 목록·오류), 설정 › 알림(불러오기·바로 저장·실패 시 서버 값 유지).
register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
globalThis.HTMLFormElement = class {};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { MemoryRouter } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const api = await import("../src/features/notifications/notificationsApi.ts");
const labels = await import("../src/features/notifications/notificationLabels.ts");
const { NotificationPanels, notificationHref } = await import("../src/features/notifications/NotificationPanels.tsx");
const { NotificationsBell } = await import("../src/features/notifications/NotificationsBell.tsx");
const { NotificationSettings } = await import("../src/features/settings/sections/NotificationSettings.tsx");

const U1 = "11111111-1111-4111-8111-111111111111", U2 = "22222222-2222-4222-8222-222222222222", U3 = "33333333-3333-4333-8333-333333333333";
const decisionItem = (over = {}) => ({ key: `job:${U1}`, type: "decision", seq: "120", reason: "risk_raised", notifiedAt: "2026-11-13T01:02:03.000000Z", unread: true,
  decision: { detailId: U2, decisionId: U2, jobId: U1, stage: "final", completeness: "complete", grade: "anomaly", riskStatus: "rated", riskLevel: "critical",
    hasUnratedEvidence: false, processId: "P01", transactionId: "TX-1" }, ...over });
const incidentItem = (over = {}) => ({ key: `incident:${U3}`, type: "incident", seq: "118", reason: "grew", notifiedAt: "2026-11-13T01:00:00.000000Z", unread: true,
  incident: { id: U3, openedAt: "2026-11-13T00:00:00.000000Z", criteria: { kind: "response_code", value: "E500", longValue: false }, memberDecisions: 12,
    riskStatus: "unrated", riskLevel: null, hasUnratedEvidence: true }, ...over });
const listBody = (items, over = {}) => ({ observedAt: "2026-11-13T01:05:00.000Z", latestSeq: "130", readThrough: "0", window: { events: 5000, fromSeq: "1", truncated: false },
  settings: { decisionBasis: "interim", minRiskLevel: "info", includeUnrated: true }, unreadCount: items.filter((item) => item.unread).length, total: items.length, nextCursor: null, items, ...over });

// 1) 응답 해석·문구
{
  assert.throws(() => api.parseNotificationList({ items: [] }), /알림 목록 응답/);
  const parsed = api.parseNotificationList(listBody([decisionItem(), incidentItem(), { key: "flow:x", type: "flow" }, decisionItem({ reason: "grew" }),
    incidentItem({ incident: { ...incidentItem().incident, memberDecisions: 0 } })]));
  assert.deepEqual(parsed.items.map((item) => item.key), [`job:${U1}`, `incident:${U3}`], "unknown type, wrong reason for a decision and empty incidents are dropped");
  assert.throws(() => api.parseNotificationList(listBody([], { settings: { decisionBasis: "final" } })), /알림 목록/);
  assert.throws(() => api.parseUnreadCount({ latestSeq: "1", unreadCount: -1 }), /알림 수/);
  const [decision, incident] = parsed.items;
  assert.deepEqual(labels.notificationText(decision), { headline: "이상 · 최종 · 위험도 상승", subject: "거래 TX-1 · 프로세스 P01" });
  assert.deepEqual(labels.notificationText(incident), { headline: "사건 · 응답코드 E500 · 판정 추가", subject: "묶인 판정 12건" });
  const withdrawn = api.parseNotificationItem(decisionItem({ reason: "withdrawn", decision: { ...decisionItem().decision, stage: "no_final", decisionId: null, detailId: U1 } }));
  assert.equal(labels.notificationText(withdrawn).headline, "철회 · 판정 실패(최종 없음)");
  const cleared = (completeness, finalSignal) => api.parseNotificationItem(decisionItem({ reason: "withdrawn",
    decision: { ...decisionItem().decision, stage: "cleared", completeness, finalSignal, grade: "normal" } }));
  assert.deepEqual([cleared("complete", "no"), cleared("partial", "unknown"), cleared("unavailable", "unknown")].map((item) => labels.notificationText(item).headline),
    ["철회 · 최종 이상 신호 없음", "철회 · 최종 정상 여부 미확인", "철회 · 최종 판정불가"], "withdrawn by a non-anomalous final uses the analysis result words");
  assert.equal(labels.showsRisk(cleared("complete", "no")), false);
  assert.equal(labels.showsRisk(withdrawn), false);
  const sign = api.parseNotificationItem(decisionItem({ reason: "opened", decision: { ...decisionItem().decision, stage: "interim", grade: "anomaly_sign", transactionId: null, processId: null } }));
  assert.deepEqual(labels.notificationText(sign), { headline: "이상 징후 · 중간 판정", subject: "PROCESS 판정" });
  assert.deepEqual([labels.riskText(decision.decision), labels.riskText(incident.incident)], ["위험", "위험도 미정"]);
  assert.deepEqual([notificationHref(decision), notificationHref(withdrawn), notificationHref(incident)], [`/analysis/d/${U2}`, `/analysis/d/${U1}`, `/analysis/incidents/${U3}`]);
  assert.deepEqual([labels.badgeText(7), labels.badgeText(99), labels.badgeText(100)], ["7", "99", "99+"]);
  assert.equal(labels.savedSettingsMessage("admin"), "관리자 계정 알림 설정 반영 · 알림 목록·배지에 바로 적용");
  const settings = api.parseMySettings({ notifications: { decisionBasis: "final_any", minRiskLevel: "warning", includeUnrated: false, isDefault: false, updatedAt: "x" },
    widgetLayout: { layout: null, updatedAt: null } });
  assert.deepEqual([settings.notifications.decisionBasis, settings.widgetLayout.layout], ["final_any", null]);
}

// 2) 화면: 가짜 fetch(계정 토큰별), SSE는 열린 채 이벤트 없음.
const requests = [];
let routes = {};
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const body = options.body ? JSON.parse(options.body) : undefined;
  requests.push({ method: options.method ?? "GET", path: target.pathname, search: target.search, body });
  if (target.pathname === "/api/pipeline/events") return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "text/event-stream" } });
  const handler = routes[`${options.method ?? "GET"} ${target.pathname}`];
  if (!handler) return new Response(JSON.stringify({ code: "notification_storage_unavailable" }), { status: 503 });
  const [status, value] = await handler(target, body);
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 300) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (element, role = "user") => {
  let session;
  const Probe = () => {
    session = useAuthSession();
    return null;
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(MemoryRouter, null, React.createElement(QueryClientProvider, { client },
      React.createElement(AuthSessionProvider, null, React.createElement(Probe), element))));
  });
  await act(async () => session.login({ token: `${role}-token`, user: { id: `${role}-1`, email: `${role}@example.test`, userType: role, createdAt: "2026-10-01T00:00:00Z" } }));
  return { renderer, client, shown: () => text(renderer.toJSON()), cleanup: async () => { await act(async () => renderer.unmount()); client.clear(); } };
};
const buttons = (app, label) => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === label);
const click = async (button) => act(async () => button.props.onClick?.({ preventDefault() {} }));

// 2-1) 배지: 서버 미확인 수, 99 초과 99+, 조회 실패는 확인 불가.
for (const [count, status, label, badge] of [[3, 200, "알림 · 읽지 않음 3건", "3"], [120, 200, "알림 · 읽지 않음 120건", "99+"], [0, 200, "알림", null],
  [null, 503, "알림 · 읽지 않은 수 확인 불가", "?"]]) {
  routes = { "GET /api/notifications/unread-count": () => [status, status === 200 ? { latestSeq: "130", unreadCount: count } : { code: "notification_storage_unavailable" }] };
  const app = await mount(React.createElement(NotificationsBell));
  await wait(() => app.renderer.root.findAll((n) => n.type === "button" && n.props["aria-label"] === label).length === 1, `bell label ${label}`);
  const marks = app.renderer.root.findAll((n) => n.props?.["data-notification-badge"] !== undefined && typeof n.type === "string");
  assert.deepEqual(marks.map((n) => text(n.children)), badge === null ? [] : [badge]);
  await app.cleanup();
}
assert.ok(requests.some((r) => r.path === "/api/pipeline/events" && r.search === "?include=interims"), "bell listens to the decision stream for refreshes");

// 2-2) 패널: 읽지 않음 기본, 항목 읽음 처리, 모두 읽음, 전체 보기, 더 보기, 이동 링크.
{
  let unreadCount = 2;
  routes = {
    "GET /api/notifications/unread-count": () => [200, { latestSeq: "130", unreadCount }],
    "GET /api/notifications": (target) => {
      const view = target.searchParams.get("view"), cursor = target.searchParams.get("cursor");
      if (cursor === "next-1") return [200, listBody([incidentItem({ key: `incident:${U1}`, unread: false, incident: { ...incidentItem().incident, id: U1 } })])];
      if (view === "all") return [200, listBody([decisionItem(), incidentItem(), decisionItem({ key: `decision:${U3}`, unread: false })], { nextCursor: "next-1" })];
      return [200, listBody(unreadCount ? [decisionItem(), incidentItem()] : [])];
    },
    "POST /api/notifications/read": (_target, body) => {
      unreadCount = "through" in body ? 0 : unreadCount - body.items.length;
      return [200, { readThrough: "through" in body ? body.through : "0", latestSeq: "130", unreadCount }];
    },
  };
  const app = await mount(React.createElement(NotificationPanels));
  await wait(() => app.renderer.root.findAll((n) => n.props?.["data-notification"] && typeof n.type === "string").length === 2, "unread items rendered");
  assert.equal(new URLSearchParams(requests.filter((r) => r.path === "/api/notifications").at(-1).search).get("view"), "unread", "unread is the default view");
  assert.ok(app.shown().includes("이상 · 최종 · 위험도 상승") && app.shown().includes("사건 · 응답코드 E500 · 판정 추가") && app.shown().includes("위험도 미정"));
  const links = app.renderer.root.findAll((n) => n.type === "a").map((a) => a.props.href);
  assert.deepEqual(links, [`/analysis/d/${U2}`, `/analysis/incidents/${U3}`]);
  // 경합: 읽음 요청 전에 시작한 느린 배지 조회(읽기 전 값)는 읽음 응답 뒤에 도착해도 배지를 덮지 않는다.
  let slow = true;
  const fast = routes["GET /api/notifications/unread-count"];
  routes["GET /api/notifications/unread-count"] = async (...args) => {
    const stale = fast(...args);
    if (slow) await new Promise((resolve) => setTimeout(resolve, 150));
    return stale;
  };
  await act(async () => void app.client.invalidateQueries({ queryKey: ["notifications-unread"] }));
  await click(buttons(app, "읽음 처리")[0]);
  await wait(() => requests.some((r) => r.method === "POST"), "item read sent");
  await tick(250);
  assert.equal(app.client.getQueryData(["notifications-unread", "user-1"]).unreadCount, 1, "stale count started before the read does not overwrite it");
  slow = false;
  assert.deepEqual(requests.filter((r) => r.method === "POST").at(-1).body, { items: [{ key: `job:${U1}`, seq: "120" }] });
  await click(buttons(app, "모두 읽음")[0]);
  await wait(() => requests.filter((r) => r.method === "POST").length === 2, "read-all sent");
  assert.deepEqual(requests.filter((r) => r.method === "POST").at(-1).body, { through: "130" }, "read-all uses the server latest sequence");
  await wait(() => app.shown().includes("읽지 않은 알림이 없습니다."), "unread view empties after read-all");
  assert.equal(buttons(app, "모두 읽음")[0].props.disabled, true, "nothing left to read");
  await click(buttons(app, "전체")[0]);
  await wait(() => app.renderer.root.findAll((n) => n.props?.["data-notification"] && typeof n.type === "string").length === 3, "all view rendered");
  await click(buttons(app, "더 보기")[0]);
  await wait(() => app.renderer.root.findAll((n) => n.props?.["data-notification"] && typeof n.type === "string").length === 4, "next page appended");
  assert.ok(requests.some((r) => r.search.includes("cursor=next-1")));
  await app.cleanup();
}

// 2-3) 패널 오류: 목록 조회 실패는 빈 목록이 아니라 오류.
{
  routes = { "GET /api/notifications/unread-count": () => [200, { latestSeq: "1", unreadCount: 1 }] };
  const app = await mount(React.createElement(NotificationPanels));
  await wait(() => app.shown().includes("알림을 불러오지 못했습니다"), "list error shown");
  assert.ok(!app.shown().includes("읽지 않은 알림이 없습니다."));
  await app.cleanup();
}

// 2-4) 설정 › 알림: 서버 값 표시, 바꾸면 바로 저장(알림 설정 3값 전부), 실패하면 서버 값 유지.
{
  let stored = { decisionBasis: "interim", minRiskLevel: "info", includeUnrated: true };
  let failPut = false;
  const settingsBody = () => ({ notifications: { ...stored, isDefault: false, updatedAt: "2026-11-13T00:00:00.000000Z" }, widgetLayout: { layout: null, updatedAt: null } });
  routes = {
    "GET /api/me/settings": () => [200, settingsBody()],
    "PUT /api/me/settings": (_target, body) => {
      if (failPut) return [503, { code: "notification_storage_unavailable" }];
      stored = body.notifications;
      return [200, settingsBody()];
    },
  };
  const user = { id: "admin-1", email: "admin@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" };
  const app = await mount(React.createElement(NotificationSettings, { currentUser: user }), "admin");
  await wait(() => app.shown().includes("알릴 판정 기준"), "settings loaded");
  const radio = () => app.renderer.root.findAll((n) => typeof n.props?.onValueChange === "function" && n.props["aria-label"] === "알릴 판정 기준")[0];
  assert.equal(radio().props.value, "interim");
  assert.ok(app.shown().includes("중간 판정부터") && app.shown().includes("최종(부분 포함)") && app.shown().includes("최종 완료만"));
  await act(async () => radio().props.onValueChange("final_complete"));
  await wait(() => app.shown().includes("관리자 계정 알림 설정 반영 · 알림 목록·배지에 바로 적용"), "saved status");
  assert.deepEqual(requests.filter((r) => r.method === "PUT").at(-1).body, { notifications: { decisionBasis: "final_complete", minRiskLevel: "info", includeUnrated: true } });
  const select = app.renderer.root.findAll((n) => n.type === "select" && n.props.id === "notif-min-risk")[0];
  assert.deepEqual(select.findAllByType("option").map((o) => text(o.children)), ["위험", "주의 이상", "참고 이상"]);
  failPut = true;
  await act(async () => select.props.onChange({ target: { value: "critical" } }));
  await wait(() => app.shown().includes("알림 설정을 저장하지 못했습니다"), "save failure shown");
  assert.equal(app.renderer.root.findAll((n) => n.type === "select" && n.props.id === "notif-min-risk")[0].props.value, "info", "failed save keeps the stored value");
  const box = app.renderer.root.findAll((n) => typeof n.props?.onCheckedChange === "function" && n.props.id === "notif-unrated")[0];
  failPut = false;
  await act(async () => box.props.onCheckedChange(false));
  await wait(() => requests.filter((r) => r.method === "PUT").at(-1).body.notifications.includeUnrated === false, "unrated toggle saved");
  await wait(() => !app.shown().includes("알림 설정을 저장하지 못했습니다"), "error cleared after a successful save");
  await app.cleanup();
}

console.log("notifications page QA passed: parsers and wording, bell badge (count, 99+, unknown), panel views/read/read-all/more/links/errors, notification settings save and failure");
