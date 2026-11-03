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
  interfaceId: "IF-ORD-1", channelId: "CH-01", processStatus: "F", responseCode: "E500", ...overrides,
});
const counts = { all: 13, anomaly: 4, in_progress: 2, undetermined: 3, failed_replaced: 3 };
const listBody = (items, total = items.length) => ({ observedAt: "2026-11-03T01:05:00Z", counts, total, page: 1, pageSize: 10,
  latestDecidedAt: "2026-11-03T01:02:03.000000Z", latestEventId: "42", items });

let routes = {};
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
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 80) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
let currentUser;
const mount = async (path) => {
  currentUser = { id: "user-1", email: "user@example.test", userType: "user", createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", "token-user");
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

// 1) 목록: 사이드바 5분류 건수, 행 → 상세 링크(목록 조건 유지), SSE는 목록의 커서 다음부터
{
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1"), item("j-2", { kind: "pending", stage: "pending", signal: "pending", decisionId: null, jobId: "j-2", status: null, ruleStatus: null, mlStatus: null, evidence: null, decidedAt: null })], 13)) };
  const app = await mount("/analysis?sort=oldest");
  await wait(() => app.shown().includes("TX-d-1"), "list row rendered");
  const side = app.renderer.root.findByProps({ id: "analysis-categories" });
  assert.deepEqual(side.findAllByType("a").map((a) => text(a.children)), [
    "전체13", "이상4", "판정 중·중간 판정2", "정상 여부 미확인3", "판정 실패·대체3",
  ]);
  assert.equal(side.findAllByType("a")[0].props["aria-current"], "page");
  const rows = app.renderer.root.findAll((node) => node.type === "a" && node.props["data-decision"]);
  assert.deepEqual(rows.map((row) => row.props.href), ["/analysis/d/d-1?sort=oldest", "/analysis/d/j-2?sort=oldest"]);
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
  await app.cleanup();
}

// 2) 새 판정 SSE → 목록·건수 다시 조회(이벤트 때만, 묶어서 한 번), 수신 건수 표시
{
  requested.length = 0;
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1")])) };
  const frame = (id) => `id: ${id}\nevent: decision\ndata: ${JSON.stringify({ id: `new-${id}`, status: "complete" })}\n\n`;
  streamFrames = [frame(43), frame(44)];
  const app = await mount("/analysis");
  await wait(() => app.shown().includes("이번 세션 실시간 수신 2건"), "received events counted");
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
  const app = await mount("/analysis?category=risk&page=x");
  await wait(() => app.shown().includes("판정 목록 미연결"), "error state");
  assert.ok(!app.shown().includes("저장된 판정이 없습니다"));
  assert.equal(location(app.router), "/analysis", "invalid category/page normalized");
  await app.cleanup();
  routes = { "GET /api/analysis/decisions": () => json(listBody([], 0)) };
  const empty = await mount("/analysis?q=none&signal=yes&completeness=partial");
  await wait(() => empty.shown().includes("검색 또는 분류 조건에 맞는 판정이 없습니다."), "filtered empty state");
  assert.ok(!/이상 신호 이상|검사 부분/.test(empty.shown()), "no filter summary in the summary line (state lives in the filter dialog)");
  await empty.cleanup();
}

// 4) 상세(최종): 요약·판정 기록·근거, 설명 요청 → 작업 조회, 실패는 그대로 + 다시 요청
{
  requested.length = 0;
  const detail = { ...item("d-1", { jobId: "j-1", stage: "replaced" }), configuration: { id: "config-1", createdAt: "2026-11-01T00:00:00Z", detectionMode: "parallel", ruleCount: 1,
    models: [{ modelKey: "iforest", adapterName: "sklearn-iforest" }] }, job: { id: "j-1", status: "succeeded", attemptCount: 2, maxAttempts: 3, errorCode: null, createdAt: null, finishedAt: null }, explanation: null };
  let jobStatus = "queued";
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-1")])),
    "GET /api/analysis/decisions/d-1": () => json(detail),
    "GET /api/analysis/decisions/d-1/interims": () => json({ id: "d-1", jobId: "j-1", items: [
      { id: "i1", kind: "first_anomaly", attemptNo: 1, seq: 1, createdAt: "2026-11-03T01:00:00Z", payload: { schemaVersion: 4, composite: { anomalous: true, grade: "anomaly", basisMethods: ["hard:process-status"] } } },
      { id: "i2", kind: "superseded", attemptNo: 1, seq: 2, supersededByAttemptNo: 2, createdAt: "2026-11-03T01:00:02Z", payload: {} },
    ] }),
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
  assert.ok(!shown.includes("당시 룰 리비전"), "rule revision row only when v4 evidence stores it");
  assert.ok(shown.includes("Rule 판정 근거") && shown.includes("process-status") && shown.includes("위험"));
  assert.ok(shown.includes("아직 설명을 요청하지 않은 판정입니다."));
  const back = app.renderer.root.findAll((node) => node.type === "a" && text(node.children).includes("목록"))[0];
  assert.equal(back.props.href, "/analysis?category=anomaly", "back link keeps list conditions");
  const ask = app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 요청")[0];
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

// 5) 상세(최종 전): 설명 요청 막힘, 진행 중 기록 / 404 / 작업 ID로 열었는데 최종 판정이 있으면 판정 ID 주소로 교체
{
  const pendingDetail = { ...item("j-2", { kind: "interim", stage: "updated", signal: "yes", decisionId: null, jobId: "j-2", status: null, ruleStatus: null, mlStatus: null,
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

// 6) 옛 주소·운영 현황 링크·폐지한 API 미호출
{
  routes = { "GET /api/analysis/decisions": () => json(listBody([])) };
  const app = await mount("/analysis/process-decisions");
  assert.equal(location(app.router), "/analysis");
  await app.cleanup();
}
assert.equal(requested.some((r) => r.path.startsWith("/api/anomaly/events/")), false, "removed anomaly detail/feedback API is never called");
assert.ok(requested.every((r) => r.path.startsWith("/api/")), "only same-origin API paths");
console.log("analysis page QA passed: sidebar counts, row links, search URL, SSE cursor and single refetch, error/empty states, detail/timeline/evidence, explain request/failure, blocked explain without polling, lookup failure blocks re-request, final-only explain, 404, job→decision redirect, legacy address");
