import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 이상 분석 화면 통합(로컬 가짜 fetch만, 외부 요청 없음): 목록·사이드바 건수·검색 주소 반영·SSE 반영·오류 표시,
// 상세·판정 기록·설명 요청/실패·최종 전 요청 막기·404·작업 ID → 판정 ID 주소 교체, 옛 주소 넘김.
register("./svg-loader.mjs", import.meta.url);
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
  // prefers-reduced-motion(Motion useReducedMotion이 처음 쓸 때 한 번 읽고 change로 갱신). 기본은 동작 허용, 5e에서 줄이기로 바꾼다.
  matchMedia: () => reducedMotionQuery,
};
const reducedMotionQuery = {
  matches: false,
  listeners: [],
  addEventListener(_type, listener) {
    this.listeners.push(listener);
  },
  removeEventListener() {},
};
const setReducedMotion = (matches) => {
  reducedMotionQuery.matches = matches;
  for (const listener of reducedMotionQuery.listeners) listener();
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
const { LIVE_REFRESH_MS } = await import("../src/features/analysis/data/useAnalysis.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const item = (id, overrides = {}) => ({
  id, kind: "final", stage: "final", signal: "yes", decisionId: id, jobId: null, jobStatus: null, sourceId: "source-1",
  processObservationId: `obs-${id}`, configVersionId: "config-1", status: "complete", ruleStatus: "succeeded", mlStatus: "succeeded",
  decisionCode: "PROCESS_STATUS_F", anomalyScore: "0.42",
  evidence: { schemaVersion: 3, anomalous: true, rules: [{ ruleKey: "process-status", version: "process-status-hr-v1", result: "hit", observedStatus: "F", severity: "critical" }],
    models: [{ modelKey: "iforest", anomalous: false }], detectionPolicy: { mode: "parallel", deadlineMs: 3000, combination: "any_anomaly" } },
  occurredAt: "2026-11-03T01:02:03.000000Z", decidedAt: "2026-11-03T01:02:03.000000Z", transactionId: `TX-${id}`, processId: "P01",
  interfaceId: "IF-ORD-1", channelId: "CH-01", processStatus: "F", responseCode: "E500", grade: null, riskStatus: "unrated", riskLevel: null,
  workflowState: "detected", unread: false, ...overrides,
});
const counts = { all: 13, anomaly: 3, anomaly_sign: 1, in_progress: 2, undetermined: 3, failed_replaced: 3,
  risk_all: 4, risk_critical: 1, risk_warning: 1, risk_info: 0, risk_unrated: 2, workflow_open: 1, workflow_resolved: 0 };
const listBody = (items, total = items.length) => ({ observedAt: "2026-11-03T01:05:00Z", counts, total, page: 1, pageSize: 10,
  latestDecidedAt: "2026-11-03T01:02:03.000000Z", latestEventId: "42", items });

let routes = {};
const flowBody = (id) => ({ id, jobId: null, truncated: true, counts: { processes: 230, messages: 1, errors: 4 },
  transaction: { transactionId: `TX-${id}`, interfaceId: "IF-ORD-1", channelId: null, status: "S", responseCode: null, processTimeMs: "41", processCount: null },
  processes: [{ observationId: "p1", processId: "P01", dependsOn: null, status: "F", responseCode: "E500", totalCount: null, focus: true },
    { observationId: "p2", processId: "P99", dependsOn: "P01", status: "S", responseCode: "0000", totalCount: "5", focus: false }],
  messages: [{ observationId: "m1", messageId: "MSG-1", processId: "P01", direction: "REQ", status: "S", dataSize: "512" }] });
const requested = [];
let streamFrames = [];
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, search: target.search, method: options.method ?? "GET", body: options.body, headers: options.headers });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  if (target.pathname === "/api/pipeline/events") {
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
  const handler = routes[`${options.method ?? "GET"} ${target.pathname}`];
  if (handler) return handler(target, options);
  return json({ code: "unavailable" }, 503);
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
// 장식 아이콘(#100 이전 화면 아이콘 복원, 사용자 11/04): Tabler svg 이름(`tabler-icon-<name>`)과 aria-hidden(자신 또는 조상).
const svgs = (node) => node.findAll((child) => child.type === "svg");
const iconNames = (node) => svgs(node).map((svg) => /tabler-icon-([a-z0-9-]+)/.exec(svg.props.className)?.[1] ?? "?");
const hiddenIcon = (svg) => {
  for (let current = svg; current; current = current.parent) if (current.props?.["aria-hidden"] === "true" || current.props?.["aria-hidden"] === true) return true;
  return false;
};
const headingOf = (app, label) => app.renderer.root.find((node) => node.type === "h2" && text(node.children) === label);
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 80) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
let currentUser;
const mount = async (path, userType = "user") => {
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  // gcTime Infinity: 조회·변경(설명 요청 mutation) 캐시가 5분 GC 타이머를 남겨 프로세스가 끝나지 않는 것을 막는다.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))),
    );
  });
  for (let i = 0; i < 20 && text(renderer.toJSON()).includes("로그인 상태를 확인하는 중"); i++) await tick();
  const shown = () => text(renderer.toJSON());
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, client, shown, cleanup };
};
const location = (router) => router.state.location.pathname + router.state.location.search;
const listCalls = () => requested.filter((r) => r.path === "/api/analysis/decisions");

// 1) 목록: 사이드바(판정 결과별 6분류 + 분류함) 건수, 행 → 상세 링크(목록 조건 유지), 미확인 점·위험도 칸, SSE는 목록의 커서 다음부터
{
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1", { unread: true }),
    item("j-2", { kind: "pending", stage: "pending", signal: "pending", decisionId: null, jobId: "j-2", status: null, ruleStatus: null, mlStatus: null, evidence: null, decidedAt: null, riskStatus: "not_applicable" }),
    item("d-3", { grade: "anomaly_sign", riskStatus: "rated", riskLevel: "critical" })], 13)) };
  const app = await mount("/analysis?category=all&sort=oldest");
  await wait(() => app.shown().includes("TX-d-1"), "list row rendered");
  const side = app.renderer.root.findByProps({ id: "analysis-categories" });
  assert.deepEqual(side.findAllByType("a").map((a) => text(a.children)), [
    "전체13", "이상3", "이상 징후1", "판정 중·중간 판정2", "정상 여부 미확인3", "판정 실패·대체3", "보류1", "완료0",
  ]);
  assert.deepEqual(side.findAll((node) => node.type === "h2").map((h) => text(h.children)), ["판정", "분류함"]);
  assert.equal(side.findAllByType("a")[0].props["aria-current"], "page");
  // 사이드바 아이콘(총괄 11/04): 판정 결과별 그룹은 아이콘·빈 자리 없음, 분류함(보류·완료)만
  assert.deepEqual(side.findAllByType("a").map((a) => iconNames(a)), [[], [], [], [], [], [], ["folder-open"], ["circle-check"]]);
  assert.equal(side.findAllByType("a").slice(0, 6).every((a) => a.findAll((node) => node.type === "span" && node.props["aria-hidden"] === "true").length === 0), true,
    "result-view group has no blank icon slots");
  assert.deepEqual(iconNames(headingOf(app, "전체 판정")), ["inbox"], "list heading category icon");
  const views = side.findByProps({ role: "group", "aria-label": "분류 보기" }).findAllByType("button");
  assert.deepEqual(views.map((b) => [text(b.children), b.props["aria-pressed"]]), [["위험도별", false], ["판정 결과별", true]]);
  // 행: 미확인 점(요청자 기준), 위험도 칸(단계 배지 / 위험도 미정 / 위험도 —)
  const rowOf = (id) => app.renderer.root.find((node) => node.type === "a" && node.props["data-decision"] === id);
  assert.equal(rowOf("d-1").findAll((node) => node.props["data-unread"] === "true" && node.props.title === "미확인").length, 1);
  assert.equal(rowOf("d-3").findAll((node) => node.props["data-unread"]).length, 0);
  assert.ok(text(rowOf("d-1").children).includes("위험도 미정"));
  assert.ok(text(rowOf("j-2").children).includes("위험도 —"));
  assert.ok(text(rowOf("d-3").children).includes("이상 징후") && text(rowOf("d-3").children).includes("위험"));
  // 행 아이콘: 저장된 위험도 단계가 있는 이상만(위험 = IconFlame, 위험도 배지와 같은 색)
  assert.deepEqual([iconNames(rowOf("d-1")), iconNames(rowOf("j-2")), iconNames(rowOf("d-3"))], [[], [], ["flame"]]);
  assert.ok(svgs(rowOf("d-3"))[0].props.className.includes("text-destructive-text"));
  assert.ok([...svgs(side), ...svgs(rowOf("d-3")), ...svgs(headingOf(app, "전체 판정"))].every(hiddenIcon), "list icons are decorative");
  const rows = app.renderer.root.findAll((node) => node.type === "a" && node.props["data-decision"]);
  assert.deepEqual(rows.map((row) => row.props.href), ["/analysis/d/d-1?category=all&sort=oldest", "/analysis/d/j-2?category=all&sort=oldest", "/analysis/d/d-3?category=all&sort=oldest"]);
  const shown = app.shown();
  const badges = app.renderer.root.findAll((node) => node.props["data-slot"] === "badge").map((node) => text(node.children));
  assert.ok(badges.includes("이상") && !badges.includes("이상 신호 있음"), "anomaly badge uses the wireframe term");
  assert.ok(shown.includes("판정 중") && shown.includes("검사 판정 중"), "pending result and completeness labels");
  assert.ok(shown.includes("검색결과 13건") && shown.includes("전체 13건 · 이번 세션 실시간 수신 0건"));
  assert.ok(shown.includes("Rule 완료 · ML 완료 · 모델 iforest"));
  assert.ok(shown.includes("최근 감지:") && !shown.includes("최근 판정:"), "wireframe summary label");
  const pageButtons = app.renderer.root.findByProps({ "aria-label": "페이지" }).findAll((node) => node.type === "button" && /^\d+$/.test(text(node.children)));
  assert.ok(pageButtons.length > 0 && pageButtons.every((node) => node.props["aria-label"] === undefined), "page number text is the accessible name");
  assert.equal(pageButtons.find((node) => text(node.children) === "1").props["aria-current"], "page");
  assert.equal(listCalls()[0].search.includes("sort=oldest") && listCalls()[0].search.includes("pageSize=10"), true, "URL conditions reach the API");
  await wait(() => requested.some((r) => r.path === "/api/pipeline/events"), "SSE connected after the first list response");
  const sse = requested.find((r) => r.path === "/api/pipeline/events");
  assert.equal(sse.headers["Last-Event-ID"], "42", "stream starts after the listed cursor (no replay)");
  await wait(() => app.shown().includes("실시간 연결됨"), "live status shown");

  // 검색: 입력 후 잠시 뒤 주소(replace)·API에 반영, 1쪽으로
  const input = app.renderer.root.findByProps({ "aria-label": "판정 검색" });
  await act(async () => input.props.onChange({ target: { value: " TX-d " } }));
  await wait(() => location(app.router).includes("q=TX-d"), "search reaches the URL", 60);
  assert.equal(app.router.state.historyAction, "REPLACE");
  await wait(() => listCalls().some((call) => call.search.includes("q=TX-d")), "search reaches the API");
  // 위험도별 보기: 위험도 전체 분류로(1쪽, 다른 조건 유지), 사이드바가 위험도 5분류 + 분류함
  const riskButton = () => app.renderer.root.findByProps({ id: "analysis-categories" }).findAll((node) => node.type === "button" && text(node.children) === "위험도별")[0];
  await act(async () => riskButton().props.onClick());
  await wait(() => !location(app.router).includes("category="), "risk view selects the risk 전체 category (the default address)");
  assert.ok(location(app.router).includes("sort=oldest") && location(app.router).includes("q=TX-d"));
  assert.ok(listCalls().at(-1).search.includes("category=risk_all"), "risk 전체 reaches the API");
  await wait(() => text(app.renderer.root.findByProps({ id: "analysis-categories" }).children).includes("위험도 미정2"), "risk view sidebar");
  const riskSide = app.renderer.root.findByProps({ id: "analysis-categories" });
  assert.deepEqual(riskSide.findAllByType("a").map((a) => text(a.children)), ["전체4", "위험1", "주의1", "참고0", "위험도 미정2", "보류1", "완료0"]);
  // 위험도·분류함 그룹은 이전 화면 아이콘(위험도 미정은 대응 없음 → 빈 자리)
  assert.deepEqual(riskSide.findAllByType("a").map((a) => iconNames(a)),
    [["inbox"], ["flame"], ["alert-triangle"], ["info-circle"], [], ["folder-open"], ["circle-check"]]);
  const unratedLabel = riskSide.findAllByType("a")[4].findAll((node) => node.type === "span" && text(node.children) === "위험도 미정")[0];
  assert.ok(unratedLabel.props.className.includes("text-body") && unratedLabel.props.className.includes("text-muted-foreground"), "위험도 미정 is muted");
  assert.equal(riskButton().props["aria-pressed"], true);
  assert.ok(app.shown().includes("전체 판정"));
  await wait(() => iconNames(headingOf(app, "전체 판정")).length > 0, "risk_all heading icon");
  assert.deepEqual(iconNames(headingOf(app, "전체 판정")), ["inbox"], "risk_all list heading keeps the legacy IconInbox");
  // 분류함은 직전 보기 유지
  await act(async () => app.router.navigate("/analysis?category=workflow_open"));
  await wait(() => app.shown().includes("보류 판정"), "workflow box title");
  assert.equal(riskButton().props["aria-pressed"], true, "workflow box keeps the previous view");
  await wait(() => listCalls().some((call) => call.search.includes("category=workflow_open")), "workflow category reaches the API");
  await app.cleanup();
}

// 1b) 분류 없는 /analysis = 위험도별 전체(와이어프레임 기본), 판정 결과별로 바꾸면 category=all이 주소에 남는다
{
  requested.length = 0;
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1")])) };
  const app = await mount("/analysis");
  await wait(() => app.shown().includes("TX-d-1"), "default list rendered");
  const side = app.renderer.root.findByProps({ id: "analysis-categories" });
  assert.deepEqual(side.findAll((node) => node.type === "h2").map((h) => text(h.children)), ["위험도", "분류함"]);
  assert.equal(side.findAllByType("a")[0].props["aria-current"], "page", "risk 전체 is current");
  assert.ok(listCalls()[0].search.includes("category=risk_all"));
  assert.equal(location(app.router), "/analysis", "default address has no category");
  const resultButton = side.findAll((node) => node.type === "button" && text(node.children) === "판정 결과별")[0];
  assert.equal(resultButton.props["aria-pressed"], false);
  await act(async () => resultButton.props.onClick());
  await wait(() => location(app.router) === "/analysis?category=all", "result view 전체 is explicit");
  await wait(() => listCalls().some((call) => !call.search.includes("category=")), "result 전체 sends no category (API default all)");
  await app.cleanup();
}

// 2) 새 판정 SSE → 목록·건수 다시 조회(이벤트 때만, 묶어서 한 번), 수신 건수 표시
{
  requested.length = 0;
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1")])) };
  const frame = (id) => `id: ${id}\nevent: decision\ndata: ${JSON.stringify({ id: `new-${id}`, status: "complete", eventKind: "final" })}\n\n`;
  const interimFrame = (id) => `id: ${id}\nevent: interim\ndata: ${JSON.stringify({ eventKind: "first_anomaly", interimId: `i-${id}`, jobId: "j-9", attemptNo: 1 })}\n\n`;
  streamFrames = [frame(43), interimFrame(44), frame(45)];
  const app = await mount("/analysis");
  await wait(() => app.shown().includes("이번 세션 실시간 수신 3건"), "final and interim events counted");
  assert.ok(requested.find((r) => r.path === "/api/pipeline/events").search.includes("include=interims"), "stream opts in to interim events");
  const before = listCalls().length;
  await tick(LIVE_REFRESH_MS + 300);
  await wait(() => listCalls().length === before + 1, "one list refetch per burst of events");
  await tick(300);
  assert.equal(listCalls().length, before + 1, "no polling after the refetch");
  await app.cleanup();
}

// 3) 목록 조회 실패: 빈 목록이 아니라 오류(미연결)로 표시, 잘못된 쿼리는 기본값으로 replace
{
  routes = { "GET /api/analysis/decisions": () => json({ code: "analysis_storage_unavailable" }, 503) };
  const app = await mount("/analysis?category=risk&page=x&signal=sign");
  await wait(() => app.shown().includes("판정 목록 미연결"), "error state");
  assert.ok(!app.shown().includes("저장된 판정이 없습니다"));
  assert.equal(location(app.router), "/analysis", "invalid category/page normalized");
  await app.cleanup();
  routes = { "GET /api/analysis/decisions": () => json(listBody([], 0)) };
  const empty = await mount("/analysis?q=none&signal=anomaly&completeness=partial");
  await wait(() => empty.shown().includes("검색 또는 분류 조건에 맞는 판정이 없습니다."), "filtered empty state");
  assert.ok(!/이상 신호 이상|검사 부분/.test(empty.shown()), "no filter summary in the summary line (state lives in the filter dialog)");
  await empty.cleanup();
  // 이전 주소 signal=yes(이상 신호 있음)는 그대로 받아 API에 보낸다
  requested.length = 0;
  const legacySignal = await mount("/analysis?signal=yes");
  await wait(() => listCalls().length > 0, "list requested");
  assert.equal(location(legacySignal.router), "/analysis?signal=yes", "signal=yes is not normalized away");
  assert.ok(listCalls()[0].search.includes("signal=yes"));
  await legacySignal.cleanup();
  // 분류함 주소로 바로 들어오면 위험도별 보기(와이어프레임 기본)
  const workflowEntry = await mount("/analysis?category=workflow_resolved");
  await wait(() => workflowEntry.shown().includes("완료 판정"), "workflow box entry");
  const viewButtons = workflowEntry.renderer.root.findByProps({ role: "group", "aria-label": "분류 보기" }).findAllByType("button");
  assert.deepEqual(viewButtons.map((b) => [text(b.children), b.props["aria-pressed"]]), [["위험도별", true], ["판정 결과별", false]]);
  await workflowEntry.cleanup();
}

// 4) 상세(최종): 요약·판정 기록·근거, 설명 요청 → 작업 조회, 실패는 그대로 + 다시 요청
{
  requested.length = 0;
  const detail = { ...item("d-1", { jobId: "j-1", stage: "replaced" }), configuration: { id: "config-1", createdAt: "2026-11-01T00:00:00Z", detectionMode: "parallel", ruleCount: 1,
    models: [{ modelKey: "iforest", adapterName: "sklearn-iforest" }] }, job: { id: "j-1", status: "succeeded", attemptCount: 2, maxAttempts: 3, errorCode: null, createdAt: null, finishedAt: null }, explanation: null };
  let jobStatus = "queued";
  const workflowPuts = [];
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-1")])),
    "GET /api/analysis/decisions/d-1": () => json({ ...detail, workflow: { state: detail.workflowState, changedAt: null } }),
    "GET /api/analysis/decisions/d-1/interims": () => json({ id: "d-1", jobId: "j-1", items: [
      { id: "i1", kind: "first_anomaly", attemptNo: 1, seq: 1, createdAt: "2026-11-03T01:00:00Z", payload: { schemaVersion: 4, composite: { anomalous: true, grade: "anomaly", basisMethods: ["hard:process-status"] } } },
      { id: "i2", kind: "superseded", attemptNo: 1, seq: 2, supersededByAttemptNo: 2, createdAt: "2026-11-03T01:00:02Z", payload: {} },
    ] }),
    "GET /api/analysis/decisions/d-1/flow": () => json(flowBody("d-1")),
    "PUT /api/analysis/decisions/d-1/read": () => json({ id: "d-1", readAt: "2026-11-03T02:00:00.000000Z" }),
    "PUT /api/analysis/decisions/d-1/workflow": (_t, options) => {
      workflowPuts.push(JSON.parse(options.body));
      if (workflowPuts.length === 1) detail.workflowState = "open";
      // 두 번째 요청: 다른 사용자가 먼저 완료로 바꿈 → 409, 재조회하면 서버 상태가 resolved
      else detail.workflowState = "resolved";
      return workflowPuts.length === 1 ? json({ id: "d-1", workflow: { state: "open", changedAt: "2026-11-03T02:00:00Z" } })
        : json({ code: "workflow_state_conflict" }, 409);
    },
    "POST /api/jobs": () => json({ id: "explain-1", status: "queued", kind: "explain", inputSpec: {} }, 201),
    "GET /api/jobs/explain-1": () => json({ id: "explain-1", kind: "explain", status: jobStatus, inputSpec: {}, errorCode: jobStatus === "failed" ? "llm_unavailable" : null }),
  };
  const app = await mount("/analysis/d/d-1?category=anomaly");
  await wait(() => app.shown().includes("판정 요약") && app.shown().includes("시도 1 결과 · 대체됨"), "detail and timeline rendered");
  const shown = app.shown();
  assert.ok(shown.includes("시도 1 · 중간 판정 · 근거 이상 규칙(상태가 S가 아님)") && !shown.includes("근거 hard:"), "display name, raw id only as tooltip");
  assert.ok(shown.includes("시도 2 · 최종 · 모든 방법 종료"));
  assert.ok(shown.includes("수행 상태") && shown.includes("상태 F"), "wireframe/origin labels: 수행 상태, 측정값 상태 F");
  const ruleHeads = app.renderer.root.findAll((node) => node.type === "th" && node.props["data-slot"] === "table-head" && !node.props.scope).map((node) => text(node.children));
  assert.ok(["방법", "결과", "측정값", "위험도 단계"].every((head) => ruleHeads.includes(head)) && !ruleHeads.includes("규칙"), `wireframe dMethods columns: ${ruleHeads}`);
  assert.ok(shown.includes("최종 · 이전 중간 판정 대체됨"));
  assert.ok(shown.includes("당시 운영 머신러닝 모델") && shown.includes("iforest(sklearn-iforest)"));
  assert.ok(!/3000|기한|탐지 모드|당시 룰 수|탐지 작업/.test(shown), "deadline number, policy and non-wireframe rows are not shown");
  assert.ok(!shown.includes("당시 룰 버전"), "rule revision row only when v4 evidence stores it");
  // 섹션 이름·아이콘(사용자 11/04): AI 설명 → AI 판정 근거 요약(IconBrain), Process Flow(IconBinaryTree), 위험도 미정이면 판정 요약 아이콘 없음
  assert.ok(!shown.includes("AI 설명"), "old section name is gone");
  assert.deepEqual(iconNames(headingOf(app, "AI 판정 근거 요약")), ["brain"]);
  assert.deepEqual(iconNames(headingOf(app, "Process Flow")), ["binary-tree"]);
  assert.deepEqual(iconNames(headingOf(app, "판정 요약")), [], "no risk icon when the risk level is unrated");
  assert.deepEqual(iconNames(headingOf(app, "판정 기록")), [], "no legacy counterpart, no icon");
  assert.ok(svgs(app.renderer.root).every(hiddenIcon), "every icon on the detail page is decorative (aria-hidden)");
  assert.ok(shown.includes("Rule 판정 근거") && shown.includes("이상 규칙(상태가 S가 아님)") && shown.includes("위험"));
  assert.ok(app.renderer.root.findAll((node) => node.type === "th" && node.props.title === "process-status").length === 1, "v3 rule key only as tooltip");
  // 위험도(v3 이상 = 위험도 미정, U6)·업무 분류
  assert.ok(shown.includes("위험도위험도 미정") && shown.includes("업무 분류감지됨"), "risk unrated and workflow label");
  // Process Flow(와이어프레임 dTree): 통계, Focus Process 기본 선택, 노드 선택 → 원본 정보, BODY 행 없음
  assert.ok(shown.includes("(프로세스 230건 · 메시지 1건 · 에러 4건)"), "header uses the true totals, not the capped node list");
  assert.ok(shown.includes("Transaction: TX-d-1") && shown.includes("Focus Process: P01") && shown.includes("Process: P99") && shown.includes("Message: REQ · MSG-1"));
  const nodeButton = (label) => app.renderer.root.findAll((node) => node.type === "button" && text(node.children).includes(label))[0];
  assert.equal(nodeButton("Focus Process: P01").props["aria-pressed"], true);
  // 노드 아이콘(#100 이전 ProcessFlowTree): Transaction IconDatabase, Process IconCodeDots(F면 오류 색), Message IconMessage2, 원본 정보 IconListDetails
  assert.deepEqual(["Transaction: TX-d-1", "Focus Process: P01", "Process: P99", "Message: REQ"].map((label) => iconNames(nodeButton(label))),
    [["database"], ["code-dots"], ["code-dots"], ["message-2"]]);
  assert.ok(svgs(nodeButton("Focus Process: P01"))[0].props.className.includes("text-destructive-text"), "failed process icon uses the error tone");
  assert.deepEqual(iconNames(app.renderer.root.find((node) => node.type === "summary")), ["list-details"]);
  await act(async () => nodeButton("Message: REQ").props.onClick());
  await wait(() => app.shown().includes("MESSAGE_IDMSG-1") && app.shown().includes("DATA_SIZE512 bytes"), "selected message fields");
  assert.ok(!app.shown().includes("BODY"), "BODY is not read or shown");
  await act(async () => nodeButton("Focus Process: P01").props.onClick());
  await wait(() => app.shown().includes("STATUSF · Code E500") && app.shown().includes("DURATION_TIME— 확인 불가"), "process fields");
  // 상세를 열면 읽음 표시 1회
  await wait(() => requested.filter((r) => r.method === "PUT" && r.path === "/api/analysis/decisions/d-1/read").length === 1, "read mark sent once");
  assert.deepEqual(JSON.parse(requested.find((r) => r.method === "PUT" && r.path === "/api/analysis/decisions/d-1/read").body),
    { occurredAt: detail.occurredAt }, "read up to the item time the screen saw");
  // 조사·조치 기록은 관리자에게만
  assert.ok(!app.shown().includes("조사·조치 기록"), "feedback box hidden for users");
  assert.equal(requested.some((r) => r.path.endsWith("/feedback")), false, "users do not read feedback");
  // 처리 상태: [처리 보류][처리 완료] → 보류 → [원래 상태로 복원][처리 완료], 409는 오류 코드 그대로
  const wfButton = (label) => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === label)[0];
  assert.ok(wfButton("처리 보류") && wfButton("처리 완료") && !wfButton("원래 상태로 복원"));
  assert.deepEqual([iconNames(wfButton("처리 보류")), iconNames(wfButton("처리 완료"))], [["folder-open"], ["circle-check"]], "legacy workflow icons");
  await act(async () => wfButton("처리 보류").props.onClick());
  await wait(() => Boolean(wfButton("원래 상태로 복원")), "open state buttons after refetch");
  assert.deepEqual(workflowPuts[0], { state: "open", expectedState: "detected" });
  assert.ok(app.shown().includes("업무 분류보류"));
  await act(async () => wfButton("처리 완료").props.onClick());
  await wait(() => app.shown().includes("업무 분류 실패 · workflow_state_conflict"), "conflict shown as is");
  assert.deepEqual(workflowPuts[1], { state: "resolved", expectedState: "open" });
  // 409 뒤 재조회로 상태가 바뀌어도(버튼이 완료 상태로 바뀜) 실패 표시는 남는다
  await wait(() => app.shown().includes("업무 분류완료") && !wfButton("처리 완료"), "refetched server state after the conflict");
  await tick(50);
  assert.ok(app.shown().includes("업무 분류 실패 · workflow_state_conflict"), "conflict error survives the refetch");
  assert.ok(wfButton("원래 상태로 복원"));
  assert.deepEqual(iconNames(wfButton("원래 상태로 복원")), ["refresh"]);
  assert.ok(shown.includes("아직 설명을 요청하지 않은 판정입니다."));
  const back = app.renderer.root.findAll((node) => node.type === "a" && text(node.children).includes("목록"))[0];
  assert.equal(back.props.href, "/analysis?category=anomaly", "back link keeps list conditions");
  const ask = app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 요청")[0];
  assert.deepEqual(iconNames(ask), ["brain"], "request button keeps the legacy IconBrain");
  await act(async () => ask.props.onClick());
  await wait(() => requested.some((r) => r.path === "/api/jobs/explain-1"), "job read after request");
  const post = requested.find((r) => r.method === "POST" && r.path === "/api/jobs");
  assert.deepEqual(JSON.parse(post.body), { kind: "explain", sourceId: "source-1", decisionId: "d-1" });
  await wait(() => app.shown().includes("요청 중"), "running state");
  assert.equal(app.renderer.root.findAll((node) => node.type === "button" && /설명 (다시 )?요청/.test(text(node.children))).length, 0, "no duplicate request while queued");
  jobStatus = "failed";
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-explain-job"] }));
  await wait(() => app.shown().includes("설명 작업 실패 · llm_unavailable"), "failure shown as is");
  assert.ok(app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 다시 요청").length === 1);
  await app.cleanup();
}

// 4b) 관리자 상세(evidence v4): 종합 근거·세부 방법별 결과 표, 위험도 결정 근거, 조사·조치 기록 저장
{
  requested.length = 0;
  const methods = [
    { methodId: "hard:process-status", category: "hard", methodVersion: "process-status-hr-v1", status: "succeeded", anomalous: false, riskLevel: null, elapsedMs: 9.1, evidence: [] },
    { methodId: "soft:retry_count-1a2b3c4d", category: "soft", status: "succeeded", anomalous: true, severity: "warning", riskLevel: "warning", elapsedMs: 9.3,
      condition: { field: "retry_count", operator: "gte", threshold: "3", unit: "회" }, observedValue: "4", evidence: [] },
    { methodId: "ml:iforest", category: "ml", status: "succeeded", anomalous: true, riskLevel: null, riskLevelReason: "level_undefined", elapsedMs: 21.7, score: 0.71,
      scoreDetail: { raw: 0.71, threshold: "0.61", direction: "higher_is_anomaly", normalized: { kind: "threshold_ratio", value: 1.164 } }, evidence: [] },
    { methodId: "ml:lof", category: "ml", status: "not_ready", anomalous: null, cause: "timeout", elapsedMs: 3001, evidence: [] },
  ];
  const evidence = { schemaVersion: 4, anomalous: true, ruleSetRevision: 13, rules: [{ ruleKey: "process-status", result: "no_hit", observedStatus: "S" }],
    detectionPolicy: { version: 2, k: { hard: 1, soft: 1, ml: 2 }, deadlineMs: 3000 }, methodResults: methods,
    categoryResults: { hard: { n: 1, k: 1, result: "normal" }, soft: { n: 1, k: 1, result: "anomalous" }, ml: { n: 2, k: 2, result: "undetermined" } },
    composite: { policy: { k: { hard: 1, soft: 1, ml: 2 } }, status: "partial", anomalous: true, grade: "anomaly_sign", basisMethods: ["soft:retry_count-1a2b3c4d"],
      risk: { policy: "risk-max-v1", levelTable: "method-defined-v2", status: "rated", level: "warning", determinedBy: ["soft:retry_count-1a2b3c4d"], unratedMethods: ["ml:iforest"] } } };
  const detail = { ...item("d-4", { grade: "anomaly_sign", riskStatus: "rated", riskLevel: "warning", status: "partial", evidence, processStatus: "S", responseCode: "0000" }),
    configuration: null, job: null, explanation: null, workflow: { state: "resolved", changedAt: null } };
  const feedback = [{ id: "f-1", kind: "action", reason: "재처리 확인", actorId: "admin-1", actorEmail: "admin@example.test", createdAt: "2026-11-03T01:00:00Z" }];
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-4")])),
    "GET /api/analysis/decisions/d-4": () => json(detail),
    "GET /api/analysis/decisions/d-4/interims": () => json({ id: "d-4", jobId: null, items: [] }),
    "GET /api/analysis/decisions/d-4/flow": () => json({ id: "d-4", jobId: null, transaction: null, processes: [], messages: [], truncated: false }),
    "PUT /api/analysis/decisions/d-4/read": () => json({ id: "d-4", readAt: "2026-11-03T02:00:00Z" }),
    "GET /api/analysis/decisions/d-4/feedback": () => json({ id: "d-4", items: feedback }),
    "POST /api/analysis/decisions/d-4/feedback": (_t, options) => {
      const body = JSON.parse(options.body);
      feedback.unshift({ id: `f-${feedback.length + 1}`, ...body, actorId: "admin-1", actorEmail: "admin@example.test", createdAt: "2026-11-03T02:00:00Z" });
      return json({ id: "d-4", item: feedback[0] }, 201);
    },
  };
  const app = await mount("/analysis/d/d-4", "admin");
  await wait(() => app.shown().includes("세부 방법별 결과") && app.shown().includes("재처리 확인"), "v4 tables and feedback rendered");
  const shown = app.shown();
  assert.ok(shown.includes("판정 결과이상 징후 · 근거 이상 징후 규칙"), "result with basis category");
  assert.ok(shown.includes("위험도주의 · 결정 근거 이상 징후 규칙 · 단계 없는 근거 머신러닝 모델(iforest)"));
  assert.ok(shown.includes("위험도 기준 표 method-defined-v2") && shown.includes("당시 룰 버전13") && shown.includes("업무 분류완료"));
  // 판정 요약 제목 아이콘 = 저장된 위험도 단계(주의 = IconAlertTriangle, 배지와 같은 색)
  assert.deepEqual(iconNames(headingOf(app, "판정 요약")), ["alert-triangle"]);
  assert.ok(svgs(headingOf(app, "판정 요약"))[0].props.className.includes("text-warning-text"));
  const heads = app.renderer.root.findAll((node) => node.type === "th" && !node.props.scope).map((node) => text(node.children));
  for (const head of ["대분류", "방법", "실행 상태", "결과", "측정값", "당시 기준", "위험도 단계", "처리 시간", "사유", "켠 방법", "대분류 결과"])
    assert.ok(heads.includes(head), `column ${head}`);
  const row = (id) => text(app.renderer.root.find((node) => node.type === "tr" && node.props["data-method"] === id).children);
  assert.equal(row("hard:process-status"), "이상 규칙이상 규칙(상태가 S가 아님)완료정상 확인상태 SSTATUS ≠ S—9.1 ms—");
  assert.equal(row("soft:retry_count-1a2b3c4d"), "이상 징후 규칙이상 징후 규칙완료이상retry_count 4 회retry_count ≥ 3 회주의9.3 ms—");
  assert.equal(row("ml:iforest"), "머신러닝 모델머신러닝 모델(iforest)완료이상원점수 0.71 · 판정선 대비 1.16배판정선 0.61단계 없음21.7 ms—");
  assert.equal(row("ml:lof"), "머신러닝 모델머신러닝 모델(lof)미완료미판정———3,001 ms기한 초과");
  assert.ok(shown.includes("이상 징후 규칙1개1000이상") && shown.includes("머신러닝 모델2개1010미판정"), "category table from stored results");
  assert.ok(!/3000|k=|ml: 2/.test(shown), "policy values (deadline, k) are not shown");
  assert.ok(shown.includes("원래 상태로 복원") && !shown.includes("처리 보류"), "resolved state offers only restore");
  // 조사·조치 기록: 빈 사유는 와이어프레임 안내, 저장하면 목록 갱신
  const save = () => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "피드백 저장")[0];
  await act(async () => save().props.onClick());
  await wait(() => app.shown().includes("조사·조치 사유를 입력하세요."), "empty reason message");
  assert.equal(requested.some((r) => r.method === "POST"), false);
  const kindSelect = app.renderer.root.findByProps({ id: "analysis-feedback-kind" });
  await act(async () => kindSelect.props.onChange({ target: { value: "suspected-false-positive" } }));
  const reasonBox = app.renderer.root.findByProps({ id: "analysis-feedback-reason" });
  assert.equal(reasonBox.props.maxLength, 2000);
  await act(async () => reasonBox.props.onChange({ target: { value: "  배치 재처리  " } }));
  await act(async () => save().props.onClick());
  await wait(() => app.shown().includes("오탐 의심 · admin@example.test") && app.shown().includes("배치 재처리"), "saved record listed");
  assert.deepEqual(JSON.parse(requested.find((r) => r.method === "POST").body), { kind: "suspected-false-positive", reason: "배치 재처리" });
  assert.ok(app.shown().includes("선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다."));
  await app.cleanup();
}

// 5) 상세(최종 전): 설명 요청 막힘, 진행 중 기록 / 404 / 작업 ID로 열었는데 최종 판정이 있으면 판정 ID 주소로 교체
{
  const pendingDetail = { ...item("j-2", { kind: "interim", stage: "updated", signal: "yes", grade: "anomaly_sign", decisionId: null, jobId: "j-2", status: null, ruleStatus: null, mlStatus: null,
    evidence: { schemaVersion: 4, composite: { anomalous: true, grade: "anomaly_sign", basisMethods: ["soft:elapsed_ms-3f9a2c1d"] } }, decidedAt: null }),
    configuration: null, job: { id: "j-2", status: "running", attemptCount: 1, maxAttempts: 3, errorCode: null, createdAt: null, finishedAt: null }, explanation: null };
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([])),
    "GET /api/analysis/decisions/j-2": () => json(pendingDetail),
    "GET /api/analysis/decisions/j-2/interims": () => json({ id: "j-2", jobId: "j-2", items: [] }),
    "GET /api/analysis/decisions/j-9": () => json({ ...item("d-9", { jobId: "j-9" }), configuration: null, job: null, explanation: null }),
    "GET /api/analysis/decisions/d-9": () => json({ ...item("d-9", { jobId: "j-9" }), configuration: null, job: null, explanation: null }),
    "GET /api/analysis/decisions/missing": () => json({ code: "decision_not_found" }, 404),
  };
  const app = await mount("/analysis/d/j-2");
  await wait(() => app.shown().includes("최종 판정 후 요청할 수 있습니다."), "explain blocked before a final decision");
  const ask = app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 요청")[0];
  assert.equal(ask.props.disabled, true);
  assert.ok(app.shown().includes("이상 징후"), "stored v4 grade is shown when present");
  assert.ok(app.shown().includes("남은 방법 결과 대기") && !app.shown().includes("최종 판정 없음"));
  assert.ok(!app.shown().includes("판정 코드") && !app.shown().includes("수행 상태"), "final-only rows are omitted before a final decision");
  await app.cleanup();
  const missing = await mount("/analysis/d/missing");
  await wait(() => missing.shown().includes("판정 상세 · decision_not_found"), "404 title shows the error code");
  await missing.cleanup();
  const resolved = await mount("/analysis/d/j-9?page=2");
  await wait(() => location(resolved.router) === "/analysis/d/d-9?page=2", "job id replaced by the decision id");
  assert.equal(resolved.router.state.historyAction, "REPLACE");
  await resolved.cleanup();
}

// 5b) 설명 작업 blocked: 저장된 상태 그대로, 요청 중 표시·주기 조회 없음
{
  requested.length = 0;
  const detail = { ...item("d-7"), configuration: null, job: null, explanation: { jobId: "explain-7", status: "blocked", errorCode: null, createdAt: null } };
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-7")])),
    "GET /api/analysis/decisions/d-7": () => json(detail),
    "GET /api/analysis/decisions/d-7/interims": () => json({ id: "d-7", jobId: null, items: [] }),
    "GET /api/jobs/explain-7": () => json({ id: "explain-7", kind: "explain", status: "blocked", inputSpec: {}, errorCode: null }),
  };
  const app = await mount("/analysis/d/d-7");
  await wait(() => app.shown().includes("설명 작업 blocked"), "blocked status shown as stored");
  assert.ok(!app.shown().includes("요청 중"));
  // 주기 조회 설정을 직접 확인(5초를 기다리지 않음): blocked면 refetchInterval = false
  const explainQuery = app.client.getQueryCache().findAll({ queryKey: ["analysis-explain-job"] })[0];
  const interval = explainQuery.observers[0].options.refetchInterval;
  assert.equal(typeof interval === "function" ? interval(explainQuery) : interval, false, "no polling for blocked");
  assert.equal(requested.filter((r) => r.path === "/api/jobs/explain-7").length, 1);
  await app.cleanup();
}

// 5c) 기존 설명 작업 조회 실패: 오류 그대로, 새 설명 요청 버튼은 막힘(중복 요청 방지)
{
  requested.length = 0;
  const detail = { ...item("d-8"), configuration: null, job: null, explanation: { jobId: "explain-8", status: "queued", errorCode: null, createdAt: null } };
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-8")])),
    "GET /api/analysis/decisions/d-8": () => json(detail),
    "GET /api/analysis/decisions/d-8/interims": () => json({ id: "d-8", jobId: null, items: [] }),
    "GET /api/jobs/explain-8": () => json({ code: "analysis_storage_unavailable" }, 503),
  };
  const app = await mount("/analysis/d/d-8");
  await wait(() => app.shown().includes("설명 작업 미연결"), "lookup error shown");
  const ask = app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 요청")[0];
  assert.equal(ask.props.disabled, true, "no new request while the existing job lookup failed");
  assert.equal(requested.some((r) => r.method === "POST" && r.path === "/api/jobs"), false);
  await app.cleanup();
}

// 5d~5f) 설명 결과 표시: 이미 있던 결과는 바로 전체, 보는 중 도착한 결과는 타자 효과(화면 읽기용 전체 사본 + aria-hidden 사본),
// 동작 줄이기 설정이면 효과 없이 전체. 결과 형식은 worker/explanation.py validate_output(facts·nextChecks).
const explainSummary = {
  decisionId: "d-9",
  facts: [{ id: "fact_0", name: "anomalous", value: true }, { id: "fact_1", name: "decisionCode", value: "PROCESS_STATUS_F" }],
  nextChecks: [{ code: "review_rule_threshold", text: "적중한 Rule의 승인 기준과 입력값을 확인하세요." }],
  causeStatus: "unconfirmed", contributionStatus: "not_computed",
};
const explainFull = "anomalous: truedecisionCode: PROCESS_STATUS_F확인할 것(원인 후보)적중한 Rule의 승인 기준과 입력값을 확인하세요.";
const explainRoutes = (id, detail, jobState) => ({
  "GET /api/analysis/decisions": () => json(listBody([item(id)])),
  [`GET /api/analysis/decisions/${id}`]: () => json(detail),
  [`GET /api/analysis/decisions/${id}/interims`]: () => json({ id, jobId: null, items: [] }),
  "POST /api/jobs": () => json({ id: `explain-${id}`, status: "queued", kind: "explain", inputSpec: {} }, 201),
  [`GET /api/jobs/explain-${id}`]: () => json({ id: `explain-${id}`, kind: "explain", status: jobState.status, inputSpec: {}, errorCode: null,
    resultSummary: jobState.status === "succeeded" ? explainSummary : null }),
});
const typingNodes = (app) => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-typing"] === "true");
const srOnlyNodes = (app) => app.renderer.root.findAll((node) => node.type === "div" && node.props.className === "sr-only");
const explainCard = (app) => app.renderer.root.find((node) => node.type === "div" && node.props["data-slot"] === "section-card" && text(node.children).startsWith("AI 판정 근거 요약"));

// 5d) 처음 열었을 때 이미 완료된 결과: 타자 효과 없이 바로 전체
{
  requested.length = 0;
  const detail = { ...item("d-9"), configuration: null, job: null, explanation: { jobId: "explain-d-9", status: "succeeded", errorCode: null, createdAt: null } };
  routes = explainRoutes("d-9", detail, { status: "succeeded" });
  const app = await mount("/analysis/d/d-9");
  await wait(() => app.shown().includes("설명 작업 완료"), "existing result loaded");
  assert.equal(typingNodes(app).length, 0, "existing result is not typed");
  const entrance = app.renderer.root.findAll((node) => node.type === "div" && node.props["data-entrance"]);
  assert.equal(entrance.length, 1);
  assert.equal(entrance[0].props["data-entrance"], "none", "existing result has no entrance motion (initial=false)");
  assert.notEqual(entrance[0].props.style?.opacity, 0, "existing result is not faded in from 0");
  assert.ok(text(explainCard(app).children).includes(explainFull), "existing result shown in full at once");
  assert.equal(app.renderer.root.findAll((node) => node.props["data-slot"] === "explain-progress").length, 0);
  await app.cleanup();
}

// 5e) 보는 중 도착한 결과: 요청 → 대기(진행 막대) → 완료 시 타자 효과, 화면 읽기용 전체 사본, 끝나면 전체 한 벌
{
  requested.length = 0;
  const detail = { ...item("d-10"), configuration: null, job: null, explanation: null };
  const jobState = { status: "queued" };
  routes = explainRoutes("d-10", detail, jobState);
  const app = await mount("/analysis/d/d-10");
  await wait(() => app.shown().includes("아직 설명을 요청하지 않은 판정입니다."), "not requested yet");
  const ask = app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 요청")[0];
  await act(async () => ask.props.onClick());
  await wait(() => app.shown().includes("요청 중"), "running state");
  const bar = app.renderer.root.findAll((node) => node.props["data-slot"] === "explain-progress");
  assert.equal(bar.length, 1, "progress bar while queued");
  assert.equal(bar[0].props["aria-hidden"], "true", "progress bar is decorative; status text carries the state");
  jobState.status = "succeeded";
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-explain-job"] }));
  await wait(() => typingNodes(app).length === 1, "fresh result starts typing");
  const typed = typingNodes(app)[0];
  assert.equal(typed.props["aria-hidden"], "true", "typed copy hidden from screen readers");
  assert.ok(text(typed.children).length < explainFull.length, "typed copy starts partial");
  assert.equal(text(srOnlyNodes(app)[0].children), explainFull, "screen readers get the full text at once");
  assert.equal(app.renderer.root.findAll((node) => node.props["data-slot"] === "explain-progress").length, 0, "progress bar gone when done");
  await wait(() => typingNodes(app).length === 0, "typing finishes", 400);
  assert.equal(srOnlyNodes(app).length, 0, "single copy after typing");
  assert.ok(text(explainCard(app).children).includes(explainFull), "full text after typing");
  await app.cleanup();
}

// 5e2) 요청하지 않고 연 화면에서 진행 중(queued)이던 작업이 완료: 이 화면이 진행 중을 본 작업이므로 타자 효과(request.data 경로 아님).
// 타자 효과 중 재조회가 와도 다시 시작하지 않는다.
{
  requested.length = 0;
  const detail = { ...item("d-12"), configuration: null, job: null, explanation: { jobId: "explain-d-12", status: "queued", errorCode: null, createdAt: null } };
  const jobState = { status: "queued" };
  routes = explainRoutes("d-12", detail, jobState);
  const app = await mount("/analysis/d/d-12");
  await wait(() => app.shown().includes("요청 중"), "queued job shown on open");
  assert.equal(requested.some((r) => r.method === "POST" && r.path === "/api/jobs"), false, "no request from this screen");
  jobState.status = "succeeded";
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-explain-job"] }));
  await wait(() => typingNodes(app).length === 1, "watched job types when it completes");
  assert.equal(typingNodes(app)[0].props["aria-hidden"], "true");
  assert.equal(text(srOnlyNodes(app)[0].children), explainFull, "sr-only full copy while typing");
  assert.equal(app.renderer.root.findAll((node) => node.type === "div" && node.props["data-entrance"] === "fresh").length, 1, "fresh entrance");
  const before = text(typingNodes(app)[0].children).length;
  const reads = requested.filter((r) => r.path === "/api/jobs/explain-d-12").length;
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-explain-job"] }));
  await wait(() => requested.filter((r) => r.path === "/api/jobs/explain-d-12").length > reads, "refetch during typing");
  assert.equal(typingNodes(app).length, 1, "still typing after the refetch");
  assert.ok(text(typingNodes(app)[0].children).length >= before, "refetch does not restart typing");
  await wait(() => typingNodes(app).length === 0, "typing finishes", 400);
  assert.ok(text(explainCard(app).children).includes(explainFull));
  await app.cleanup();
}

// 5f) 동작 줄이기 설정: 보는 중 도착한 결과도 타자 효과 없이 바로 전체
{
  setReducedMotion(true);
  requested.length = 0;
  const detail = { ...item("d-11"), configuration: null, job: null, explanation: { jobId: "explain-d-11", status: "queued", errorCode: null, createdAt: null } };
  const jobState = { status: "queued" };
  routes = explainRoutes("d-11", detail, jobState);
  const app = await mount("/analysis/d/d-11");
  await wait(() => app.shown().includes("요청 중"), "running state on revisit");
  jobState.status = "succeeded";
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-explain-job"] }));
  await wait(() => app.shown().includes("설명 작업 완료"), "result arrived");
  assert.equal(typingNodes(app).length, 0, "no typing with reduced motion");
  assert.ok(text(explainCard(app).children).includes(explainFull), "full text at once with reduced motion");
  await app.cleanup();
  setReducedMotion(false);
}

// 6) 옛 주소·운영 현황 링크·폐지한 API 미호출
{
  routes = { "GET /api/analysis/decisions": () => json(listBody([])) };
  const app = await mount("/analysis/process-decisions");
  assert.equal(location(app.router), "/analysis");
  await app.cleanup();
}
assert.equal(requested.some((r) => r.path.startsWith("/api/anomaly/events/")), false, "removed anomaly detail/feedback API is never called");
assert.ok(requested.every((r) => r.path.startsWith("/api/")), "only same-origin API paths");
console.log("analysis page QA passed: default risk view, result/risk/workflow sidebar and view switch, unread dot, risk cell, row links, search URL, SSE cursor with interim events "
  + "and single refetch, error/empty states, detail/timeline/evidence, process flow tree, read mark, workflow transitions and 409, admin-only feedback, "
  + "v4 method tables, explain request/failure, blocked explain without polling, lookup failure blocks re-request, explain result typing only for a result arriving while watching (requested or watched queued job, no restart on refetch, sr-only full copy, existing result without motion, reduced motion = full text), final-only explain, 404, job→decision redirect, legacy address");
