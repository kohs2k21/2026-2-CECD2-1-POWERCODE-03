import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 사건 보기(층 C, contracts/incidents-ui.md) 화면 시험(로컬 가짜 fetch만): 응답 해석·주소 쿼리, 시간축 범위, 사이드바 "사건 보기",
// 목록(시간축 막대·표 열·이어짐·해제·커서 쪽 넘김·기간·거래 2건 이상 기본·모두 보기), 상세(뒤 구간 id → 흐름 주소, 구간 표, 거래별 구성원·더 보기,
// 판정 상세 링크·돌아가기 "사건으로"), 오류 표시. Detector 계약 정합: 긴 기준값 "값 생략", 시각 출처(판정 시각 기준) 표시, 빈 목록 v3 안내.
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
globalThis.window.HTMLSelectElement = class {
  get value() {
    return "";
  }
  set value(_value) {}
};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { isAppPath, safeReturnPath } = await import("../src/app/routes.ts");
const api = await import("../src/features/analysis/incidents/incidentApi.ts");
const { timelineRange, barGeometry } = await import("../src/features/analysis/incidents/IncidentTimeline.tsx");
const labels = await import("../src/features/analysis/incidents/incidentLabels.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const counts = (transactions, decisions = transactions, extra = {}) => ({ transactions, releasedTransactions: 0, decisions, releasedDecisions: 0, missingTransactionId: 0,
  unknownGrade: 0, ...extra });
const SOURCE_TIME = { processTimeRaw: 35, decidedAt: 0 };
const DECIDED_TIME = { processTimeRaw: 0, decidedAt: 5 };
const MIXED_TIME = { processTimeRaw: 20, decidedAt: 15 };
const flow = (id, overrides = {}) => ({ id, sourceId: "s-1", criteria: { kind: "response_code", value: "E-CONN", longValue: false, version: "incident-gap-v1" },
  startAt: "2026-11-12T04:00:00.000000Z", endAt: "2026-11-12T06:10:00.000000Z", durationMs: 130 * 60_000, counts: counts(30, 35), occurredBasis: SOURCE_TIME,
  chain: { length: 3, mergedIncidents: 0 }, flags: [], ...overrides });
const listBody = (items, nextCursor = null) => ({ observedAt: "2026-11-12T08:00:00.000Z", truncated: false, total: items.length, nextCursor, items });
const detailBody = (id, requestedId = id, { occurredBasis = SOURCE_TIME, segmentBasis = [SOURCE_TIME, SOURCE_TIME], criteria = {} } = {}) => ({ ...flow(id), requestedId, truncated: false,
  occurredBasis,
  criteria: { kind: "response_code", value: "E-CONN", longValue: false, groupKey: "response_code:E-CONN", version: "incident-gap-v1", timeBasis: "process_time_raw",
    document: { gapSeconds: 300, maxSpanSeconds: 3600 }, ...criteria },
  segments: [
    { id, openedAt: "2026-11-12T04:00:00.000000Z", endAt: "2026-11-12T04:59:00.000000Z", durationMs: 59 * 60_000, incidentIds: [id], mergedIncidentIds: [], continuesFrom: [], counts: counts(14, 16),
      occurredBasis: segmentBasis[0] },
    { id: "seg-2", openedAt: "2026-11-12T05:03:00.000000Z", endAt: "2026-11-12T06:02:00.000000Z", durationMs: 59 * 60_000, incidentIds: ["seg-2", "m-1"], mergedIncidentIds: ["m-1"], continuesFrom: [id], counts: counts(14, 16),
      occurredBasis: segmentBasis[1] },
  ] });
const memberRow = (tx, decisionId, overrides = {}) => ({ key: `transaction:${tx}`, transactionId: tx, interfaceId: "IF-ORD-001",
  firstOccurredAt: "2026-11-12T04:00:00.000000Z", lastOccurredAt: "2026-11-12T04:00:00.000000Z", occurredBasis: "process_time_raw", released: false, decisionCount: 1, decisionsTruncated: false,
  decisions: [{ decisionId, incidentId: "f-1", segmentId: "f-1", occurredAt: "2026-11-12T04:00:00.000000Z", occurredBasis: "decided_at", processId: "PR001",
    grade: "anomaly", released: false, latestDecisionId: decisionId, latestGrade: "anomaly" }], ...overrides });

// 1) 응답 해석·주소 쿼리·시간축
{
  assert.throws(() => api.parseIncidentList({ items: "x" }), /사건 목록 응답/);
  const parsed = api.parseIncidentList(listBody([flow("f-1", { flags: ["continuation_branched", "bogus"], truncated: true })]));
  assert.deepEqual([parsed.items[0].flags, parsed.items[0].truncated], [["continuation_branched"], true], "unknown flags are dropped");
  assert.throws(() => api.parseIncidentList(listBody([flow("f-1", { counts: { ...counts(1), transactions: -1 } })])), /사건 응답/, "negative count is a parse error");
  assert.throws(() => api.parseIncidentList(listBody([flow("f-1", { counts: { transactions: 1 } })])), /사건 응답/, "missing count fields are a parse error");
  assert.throws(() => api.parseIncidentList(listBody([flow("f-1", { counts: null })])), /사건 응답/, "no counts is a parse error, not zeros");
  assert.throws(() => api.parseIncidentList(listBody([flow("f-1", { occurredBasis: undefined })])), /사건 응답/, "time basis counts are required");
  const long = api.parseIncidentList(listBody([flow("f-1", { criteria: { kind: "channel_id", value: null, longValue: true, version: "v1" } })])).items[0].criteria;
  assert.deepEqual([long.value, long.longValue, labels.criteriaText(long)], [null, true, "채널 값 생략(긴 값)"], "unresolved long value: label, never a hash");
  assert.equal(labels.criteriaText({ kind: "channel_id", value: "C".repeat(600), longValue: true }), `채널 ${"C".repeat(60)}…`, "long resolved value is shortened in titles");
  assert.deepEqual([labels.timeBasisKind(SOURCE_TIME), labels.timeBasisKind(DECIDED_TIME), labels.timeBasisKind(MIXED_TIME), labels.timeBasisKind({ processTimeRaw: 0, decidedAt: 0 })],
    ["source", "decided", "mixed", "source"]);
  assert.throws(() => api.parseIncidentDetail({ ...detailBody("f-1"), segments: [{ ...detailBody("f-1").segments[0], counts: { ...counts(1), unknownGrade: 1.5 } }] }),
    /사건 구간 응답/, "segment counts are checked too");
  const detail = api.parseIncidentDetail(detailBody("f-1", "seg-2"));
  assert.deepEqual([detail.requestedId, detail.criteria.gapSeconds, detail.segments.map((s) => s.continuesFrom)], ["seg-2", 300, [[], ["f-1"]]]);
  const members = api.parseIncidentMembers({ id: "f-1", total: 2, nextCursor: "abc", items: [memberRow("TX-1", "d-1"), { key: "decision:d-2", transactionId: null, decisions: [{ decisionId: "d-2", grade: "x", gradeUnknown: true }] }, { bad: 1 }] });
  assert.deepEqual(members.items.map((m) => [m.transactionId, m.decisions[0]?.grade, m.decisions[0]?.gradeUnknown]), [["TX-1", "anomaly", false], [null, null, true]]);
  assert.deepEqual(api.parseIncidentSearch("?period=24h&cursor=abc_-1"), { period: "24h", cursor: "abc_-1", all: false, invalid: false });
  assert.deepEqual(api.parseIncidentSearch("?all=1"), { period: "all", cursor: null, all: true, invalid: false }, "default period is all");
  assert.equal(api.parseIncidentSearch("?all=yes").invalid, true);
  assert.equal(api.parseIncidentSearch("?period=1y").invalid, true);
  assert.equal(api.parseIncidentSearch("?cursor=a%2Fb").invalid, true);
  assert.equal(api.incidentSearch({ period: "all", cursor: null, all: false }), "", "default period and filter are not written");
  assert.equal(api.incidentSearch({ period: "7d", cursor: null, all: false }), "?period=7d", "7 days is no longer the default");
  assert.equal(api.incidentSearch({ period: "24h", cursor: "c", all: true }), "?period=24h&all=1&cursor=c");
  assert.equal(api.periodFrom("all"), null);
  assert.equal(api.periodFrom("24h", Date.parse("2026-11-12T08:00:30Z")), "2026-11-11T08:00:00.000Z", "minute-floored so the query key is stable");
  const items = [flow("a"), flow("b", { startAt: "2026-11-12T07:00:00.000000Z", endAt: "2026-11-12T07:00:00.000000Z", durationMs: 0 })];
  const range = timelineRange(items);
  assert.ok(range.low < Date.parse(items[0].startAt) && range.high > Date.parse(items[1].endAt), "axis fits the shown incidents with padding");
  const point = barGeometry(items[1], range);
  assert.ok(point.width > 0 && point.left + point.width <= 100, "a zero-length incident still gets a visible bar inside the axis");
  assert.equal(labels.criteriaText({ kind: "channel_id", value: "CH:1", longValue: false }), "채널 CH:1");
  assert.equal(labels.criteriaKindLabel("process_hub_id"), "process_hub_id", "unknown key names are shown as-is");
  assert.deepEqual([labels.durationText(30_000), labels.durationText(131 * 60_000), labels.segmentsText(1), labels.segmentsText(3)], ["1분 미만", "2시간 11분", "—", "3개 구간"]);
  assert.ok(isAppPath("/analysis/incidents") && isAppPath("/analysis/incidents/f-1"));
  assert.equal(safeReturnPath("/analysis/incidents/f-1?period=24h"), "/analysis/incidents/f-1?period=24h");
}

let routes = {};
const requested = [];
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, search: target.search });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  if (target.pathname === "/api/pipeline/events") return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "text/event-stream" } });
  if (target.pathname === "/api/analysis/decisions") return json({ observedAt: "2026-11-12T08:00:00Z", counts: {}, total: 0, page: 1, pageSize: 10, latestDecidedAt: null, latestEventId: "0", items: [] });
  const handler = routes[target.pathname];
  if (handler) return handler(target);
  return json({ code: "analysis_storage_unavailable" }, 503);
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 100) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
let currentUser;
const mount = async (path) => {
  currentUser = { id: "user-1", email: "user@example.test", userType: "user", createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", "token-user");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))));
  });
  const shown = () => text(renderer.toJSON());
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, cleanup };
};
const at = (router) => router.state.location.pathname + router.state.location.search;
const byData = (app, key) => app.renderer.root.findAll((node) => node.props?.[key] !== undefined && typeof node.type === "string");

// 2) 목록: 사이드바 "사건 보기" 현재 항목, 시간축 막대·범례, 표 열, 이어짐·병합·해제, 기간 쿼리, 다음 쪽(커서)
{
  routes = { "/api/analysis/incidents": (target) => json(target.searchParams.get("cursor") === "next-1"
    ? listBody([flow("f-3", { criteria: { kind: "channel_id", value: "CH-BAT", longValue: false, version: "v1" }, chain: { length: 1, mergedIncidents: 0 } })])
    : listBody([flow("f-1"), flow("f-2", { criteria: { kind: "channel_id", value: "CH-MOB", longValue: false, version: "v1" }, chain: { length: 1, mergedIncidents: 1 },
        counts: counts(5, 5, { releasedTransactions: 1, releasedDecisions: 1 }), flags: ["merge_mismatch"], occurredBasis: DECIDED_TIME }),
        flow("f-4", { chain: { length: 1, mergedIncidents: 0 }, counts: counts(2), truncated: true }),
        flow("f-5", { chain: { length: 1, mergedIncidents: 0 }, counts: counts(2), criteria: { kind: "response_code", value: null, longValue: true, version: "v1" } })], "next-1")) };
  const app = await mount("/analysis/incidents?period=24h");
  await wait(() => byData(app, "data-incident-row").length === 4, "list rows rendered");
  const side = app.renderer.root.findByProps({ id: "analysis-categories" });
  const current = side.findAllByType("a").filter((a) => a.props["aria-current"] === "page").map((a) => text(a.children));
  assert.deepEqual(current, ["사건 보기"]);
  assert.ok(app.shown().includes(labels.INCIDENT_CRITERIA_NOTE), "criteria description label");
  const call = requested.filter((r) => r.path === "/api/analysis/incidents").at(-1);
  assert.ok(new URLSearchParams(call.search).get("from"), "period becomes from");
  assert.equal(new URLSearchParams(call.search).get("limit"), "20");
  assert.equal(new URLSearchParams(call.search).get("minTransactions"), "2", "default hides incidents with fewer than 2 transactions");
  const bars = byData(app, "data-incident-bar");
  assert.deepEqual(bars.slice(0, 2).map((b) => [b.props["data-incident-bar"], b.props.href]), [["response_code", "/analysis/incidents/f-1?period=24h"], ["channel_id", "/analysis/incidents/f-2?period=24h"]]);
  assert.equal(text(byData(app, "data-incident-legend")[0].children), "응답코드채널");
  assert.deepEqual(app.renderer.root.findAll((n) => n.type === "th").map((th) => text(th.children)), ["기간", "묶은 기준", "거래 수", "해제", "이어짐"]);
  const rows = byData(app, "data-incident-row").map((row) => row.findAllByType("td").map((td) => text(td.children)));
  assert.deepEqual(rows.map((r) => r.slice(1)), [
    ["응답코드E-CONN", "30판정 35건", "—", "3개 구간"],
    ["채널CH-MOB", "5판정 5건", "1", "병합 1기준 다른 병합 제외"],
    ["응답코드E-CONN", "2판정 2건", "—", "일부 구간 제외"],
    ["응답코드값 생략(긴 값)", "2판정 2건", "—", "—"],
  ], "chain cell: badges only, dash only when nothing; unresolved long value is a label, not a hash");
  const periods = byData(app, "data-incident-row").map((row) => text(row.findAllByType("td")[0].children));
  assert.ok(periods[1].endsWith("· 판정 시각 기준") && !periods[0].includes("판정 시각"), "decided_at flows mark their period, source-time flows do not");
  assert.equal(text(byData(app, "data-timeline-basis")[0].children), "시간축: 일부 판정 시각 기준", "timeline axis states its basis when some incidents use decision time");
  const flagBadge = byData(app, "data-incident-row")[1].findAll((n) => n.props?.title && text(n.children) === "기준 다른 병합 제외")[0];
  assert.ok(flagBadge?.props.title.includes("원본·기준 버전·기준값"), "flag badge explains the cause");
  assert.ok(app.shown().includes("사건 4건 · 거래 2건 이상"));
  const next = app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === "다음")[0];
  await act(async () => next.props.onClick());
  await wait(() => at(app.router) === "/analysis/incidents?period=24h&cursor=next-1", "cursor in address");
  await wait(() => byData(app, "data-incident-row").length === 1, "next page rendered");
  const toggle = app.renderer.root.findAll((n) => n.props?.["aria-label"] === "모두 보기" && typeof n.props.onCheckedChange === "function")[0];
  await act(async () => toggle.props.onCheckedChange(true));
  await wait(() => at(app.router) === "/analysis/incidents?period=24h&all=1", "show-all in address, cursor reset");
  await wait(() => requested.filter((r) => r.path === "/api/analysis/incidents").at(-1).search.includes("limit=20")
    && !new URLSearchParams(requested.filter((r) => r.path === "/api/analysis/incidents").at(-1).search).has("minTransactions"), "show-all drops the filter");
  assert.ok(!app.shown().includes("거래 2건 이상"));
  await app.cleanup();
}

// 3) 상세: 뒤 구간 id → 흐름 id 주소(replace), 머리·구간 표·구성원, 판정 링크(돌아가기 state), 목록 돌아가기
{
  routes = {
    "/api/analysis/incidents/seg-2": () => json(detailBody("f-1", "seg-2")),
    "/api/analysis/incidents/f-1": () => json(detailBody("f-1")),
    "/api/analysis/incidents/f-1/members": () => json({ id: "f-1", requestedId: "f-1", total: 2, nextCursor: null, items: [
      memberRow("TX-1", "d-1"),
      memberRow("TX-2", "d-2", { released: true, decisions: [{ ...memberRow("TX-2", "d-2").decisions[0], released: true, latestDecisionId: "d-9", latestGrade: "normal" }] }),
    ] }),
  };
  const app = await mount("/analysis/incidents/seg-2?period=30d");
  await wait(() => at(app.router) === "/analysis/incidents/f-1?period=30d", "segment id is replaced by the flow id");
  await wait(() => byData(app, "data-member-row").length === 2, "members rendered");
  const summary = app.renderer.root.find((n) => n.props?.["aria-label"] === "사건 요약" && n.props["data-slot"] === "section-card");
  assert.ok(text(summary.children).startsWith("응답코드 E-CONN"));
  const fields = summary.findAllByType("dt").map((dt) => text(dt.children));
  assert.deepEqual(fields, ["묶은 기준", "기준값", "기간", "거래 수", "판정 수", "해제", "구간", "묶음 간격 · 구간 최대 길이"]);
  assert.ok(text(summary.children).includes("5분 · 60분"));
  const segments = byData(app, "data-segment-row").map((row) => row.findAllByType("td").map((td) => text(td.children)));
  assert.deepEqual(segments.map((r) => [r[0], r[2], r[3], r[4]]), [["1", "14", "16", "—"], ["2", "14", "16", "1"]]);
  const members = byData(app, "data-member-row").map((row) => row.findAllByType("td").map((td) => text(td.children)));
  assert.deepEqual(members.map((r) => [r[0], r[1], r[3], r[4]]), [["TX-1", "IF-ORD-001", "PR001이상", "—"], ["TX-2", "IF-ORD-001", "PR001이상해제 · 최신 정상", "해제"]]);
  const link = byData(app, "data-member-row")[0].findAllByType("a")[0];
  assert.equal(link.props.href, "/analysis/d/d-1");
  const back = app.renderer.root.findAll((n) => n.type === "a" && text(n.children) === "사건 목록")[0];
  assert.equal(back.props.href, "/analysis/incidents?period=30d");
  const heads = app.renderer.root.findAll((n) => n.type === "table" && n.props["aria-label"] === "거래별 구성원 표")[0].findAllByType("th").map((th) => text(th.children));
  assert.deepEqual(heads, ["거래 ID", "인터페이스", "발생 시각", "판정(단계)", "상태"]);
  assert.ok(app.shown().includes("거래 2건 · 첫 발생 시각 순"));
  await app.cleanup();
}

// 3b) 구성원 "더 보기": 다음 커서 쪽을 덧붙이고(앞 쪽 유지), 마지막 쪽이면 버튼이 사라진다. 등급 모름 판정은 배지.
{
  const memberCalls = [];
  routes = {
    "/api/analysis/incidents/f-1": () => json(detailBody("f-1")),
    "/api/analysis/incidents/f-1/members": (target) => {
      const cursor = target.searchParams.get("cursor");
      memberCalls.push(cursor);
      if (cursor === null) return json({ id: "f-1", total: 3, nextCursor: "m-2", items: [memberRow("TX-1", "d-1"), memberRow("TX-2", "d-2")] });
      if (cursor === "m-2") return json({ id: "f-1", total: 3, nextCursor: null, items: [memberRow("TX-3", "d-3", {
        decisions: [{ ...memberRow("TX-3", "d-3").decisions[0], latestGrade: null, gradeUnknown: true }] })] });
      return json({ code: "invalid_cursor" }, 400);
    },
  };
  const app = await mount("/analysis/incidents/f-1");
  await wait(() => byData(app, "data-member-row").length === 2, "first member page");
  const more = () => app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === "더 보기");
  assert.equal(more().length, 1);
  await act(async () => more()[0].props.onClick());
  await wait(() => byData(app, "data-member-row").length === 3, "second page appended");
  assert.deepEqual(byData(app, "data-member-row").map((row) => row.props["data-member-row"]), ["TX-1", "TX-2", "TX-3"]);
  assert.equal(more().length, 0, "no more button after the last page");
  assert.equal(memberCalls.filter((c) => c === "m-2").length, 1, "next page requested once with the cursor");
  assert.ok(text(byData(app, "data-member-row")[2].children).includes("등급 모름"));
  await app.cleanup();
}

// 3c) 시각 출처: 흐름 전체가 판정 시각이면 칸 머리 "판정 시각"·설명 "첫 판정 시각 순"·기간 표시, 섞이면 칸 머리 "발생·판정 시각" + 판정 시각 줄·구간 표시.
//     긴 기준값을 찾았으면 기준값 칸은 원래 값(해시 아님).
{
  const members = (rows) => () => json({ id: "f-1", total: rows.length, nextCursor: null, items: rows });
  routes = {
    "/api/analysis/incidents/f-1": () => json(detailBody("f-1", "f-1", { occurredBasis: DECIDED_TIME, segmentBasis: [DECIDED_TIME, DECIDED_TIME],
      criteria: { kind: "channel_id", value: `CH-${"L".repeat(600)}`, longValue: true, groupKey: `channel_id:sha256:${"a".repeat(64)}` } })),
    "/api/analysis/incidents/f-1/members": members([memberRow("TX-1", "d-1", { occurredBasis: "decided_at" })]),
  };
  let app = await mount("/analysis/incidents/f-1");
  await wait(() => byData(app, "data-member-row").length === 1, "decided members rendered");
  const heads = () => app.renderer.root.findAll((n) => n.type === "table" && n.props["aria-label"] === "거래별 구성원 표")[0].findAllByType("th").map((th) => text(th.children));
  assert.equal(heads()[2], "판정 시각", "all decided_at: the column is decision time, not occurrence time");
  assert.ok(app.shown().includes("거래 1건 · 첫 판정 시각 순"));
  const summary = app.renderer.root.find((n) => n.props?.["aria-label"] === "사건 요약" && n.props["data-slot"] === "section-card");
  const values = summary.findAllByType("dd").map((dd) => text(dd.children));
  assert.ok(values[2].endsWith("판정 시각 기준"), "period carries the basis");
  assert.equal(values[1], `CH-${"L".repeat(600)}`, "resolved long value shown in full in the value field");
  assert.ok(!app.shown().includes("sha256"), "hash never shown");
  assert.equal(byData(app, "data-member-row")[0].findAll((n) => n.props?.["data-time-basis"] !== undefined && typeof n.type === "string").length, 0,
    "no per-row mark when the whole flow is decision time");
  await app.cleanup();

  routes = {
    "/api/analysis/incidents/f-1": () => json(detailBody("f-1", "f-1", { occurredBasis: MIXED_TIME, segmentBasis: [SOURCE_TIME, DECIDED_TIME] })),
    "/api/analysis/incidents/f-1/members": members([memberRow("TX-1", "d-1"), memberRow("TX-2", "d-2", { occurredBasis: "decided_at" }),
      memberRow("TX-3", "d-3", { occurredBasis: "mixed" })]),
  };
  app = await mount("/analysis/incidents/f-1");
  await wait(() => byData(app, "data-member-row").length === 3, "mixed members rendered");
  assert.equal(heads()[2], "발생·판정 시각");
  assert.ok(app.shown().includes("첫 시각 순"));
  const marks = byData(app, "data-member-row").map((row) => row.findAll((n) => n.props?.["data-time-basis"] !== undefined && typeof n.type === "string").map((n) => text(n.children)));
  assert.deepEqual(marks, [[], ["판정 시각 기준"], ["일부 판정 시각 기준"]], "mixed flow: decision-time rows are marked");
  const segmentMarks = byData(app, "data-segment-row").map((row) => text(row.findAllByType("td")[1].children));
  assert.ok(!segmentMarks[0].includes("판정 시각") && segmentMarks[1].endsWith("판정 시각 기준"));
  await app.cleanup();
}

// 3d) 빈 목록: v04 전환 전 판정(v3)은 등급이 없어 사건이 없다는 짧은 안내(Detector 8절).
{
  routes = { "/api/analysis/incidents": () => json(listBody([])) };
  const app = await mount("/analysis/incidents");
  await wait(() => app.shown().includes("사건이 없습니다"), "empty list");
  assert.ok(app.shown().includes(labels.incidentEmptyV3Note));
  await app.cleanup();
}

// 4) 판정 상세의 돌아가기는 사건 화면에서 왔으면 "사건으로"(그 주소), 아니면 "목록"(판정 목록)
{
  routes = {};
  const app = await mount("/analysis");
  await act(async () => app.router.navigate("/analysis/d/d-1", { state: { returnTo: "/analysis/incidents/f-1?period=30d" } }));
  await wait(() => app.renderer.root.findAll((n) => n.type === "a" && text(n.children) === "사건으로").length > 0, "decision back link");
  assert.equal(app.renderer.root.findAll((n) => n.type === "a" && text(n.children) === "사건으로")[0].props.href, "/analysis/incidents/f-1?period=30d");
  await act(async () => app.router.navigate("/analysis/d/d-2", { state: { returnTo: "https://example.test/x" } }));
  await wait(() => app.renderer.root.findAll((n) => n.type === "a" && text(n.children) === "목록")[0]?.props.href === "/analysis", "foreign returnTo is ignored");
  await app.cleanup();
}

// 5) 오류: 503은 미연결로, 성공·빈 목록으로 바꾸지 않음. 404 code 표시.
{
  routes = { "/api/analysis/incidents/x-404": () => json({ code: "incident_not_found" }, 404) };
  const list = await mount("/analysis/incidents");
  await wait(() => list.shown().includes("사건 목록 미연결"), "503 shown as not connected");
  assert.ok(!list.shown().includes("사건이 없습니다"));
  await list.cleanup();
  const missing = await mount("/analysis/incidents/x-404");
  await wait(() => missing.shown().includes("사건 · incident_not_found"), "404 code shown");
  await missing.cleanup();
}

console.log("incidents page QA passed: parsers and URL query, timeline range, sidebar entry, list (bars, legend, columns, released, chain, merge, flags, period, cursor), "
  + "strict counts, show-all toggle, detail (segment id redirect, summary, segments, members by transaction with released, more paging, unknown grade, "
  + "decision links, back links), errors, long value label, time basis marks (list, timeline, detail, members, segments), empty-state v3 note");
