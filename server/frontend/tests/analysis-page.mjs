import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 이상 분석 화면 통합(로컬 가짜 fetch만, 외부 요청 없음): 목록·사이드바 건수·검색 주소 반영·SSE 반영·오류 표시,
// 상세·판정 기록·AI 판정 근거 요약 카드·다듬기 자동 요청·404·작업 ID → 판정 ID 주소 교체, 옛 주소 넘김.
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
const { FeedbackEditBody, FeedbackEditModal, WorkflowRecordBody, WorkflowRecordDialog } = await import("../src/features/analysis/components/DecisionWorkflow.tsx");

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
const counts = { all: 13, anomaly: 3, anomaly_sign: 1, normal: 4, in_progress: 2, undetermined: 3, failed_replaced: 3,
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
  requested.push({ path: target.pathname, search: target.search, method: options.method ?? "GET", body: options.body, headers: options.headers, signal: options.signal });
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
// 판정 기록 시각 칸(labels.ts stampText와 같은 지역 시각 MM/DD HH:MM:SS)
const stampTime = (iso) => { const d = new Date(iso), two = (n) => String(n).padStart(2, "0");
  return `${two(d.getMonth() + 1)}/${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`; };
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

// 1) 목록: 사이드바(판정 결과별 7분류(정상 포함, 사용자 11/13) + 분류함) 건수, 행 → 상세 링크(목록 조건 유지), 미확인 점·위험도 칸, SSE는 목록의 커서 다음부터
{
  const blankCause = { methodId: null, ruleKey: null, code: null, observedStatus: null, fieldName: null, operator: null, threshold: null, unit: null,
    observedValue: null, modelName: null, score: null, scoreThreshold: null };
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1", { unread: true,
    causes: [{ ...blankCause, category: "hard", methodId: "hard:process-status", observedStatus: "F" }] }),
    item("j-2", { kind: "pending", stage: "pending", signal: "pending", decisionId: null, jobId: "j-2", status: null, ruleStatus: null, mlStatus: null, evidence: null, decidedAt: null, riskStatus: "not_applicable" }),
    item("d-3", { grade: "anomaly_sign", riskStatus: "rated", riskLevel: "critical", causes: [
      { ...blankCause, category: "soft", methodId: "soft:elapsed_ms-1a2b3c4d", fieldName: "처리 시간", operator: "gte", threshold: "5000", unit: "ms", observedValue: "8000" },
      { ...blankCause, category: "ml", methodId: "ml:iforest", modelName: "Isolation Forest", score: 0.84, scoreThreshold: "0.61" }] })], 13)) };
  const app = await mount("/analysis?category=all&sort=oldest");
  await wait(() => app.shown().includes("TX-d-1"), "list row rendered");
  const side = app.renderer.root.findByProps({ id: "analysis-categories" });
  assert.deepEqual(side.findAllByType("a").map((a) => text(a.children)), [
    "전체13", "이상3", "이상 징후1", "정상4", "판정 중·중간 판정2", "정상 여부 미확인3", "판정 실패·대체3", "보류1", "완료0", "사건 보기",
  ], "사건 보기(층 C, contracts/incidents-ui.md 5절) is the separated group below 분류함, without a count");
  assert.ok(side.findAllByType("a")[3].props.href.includes("category=normal"), "normal category link reaches the API value");
  assert.deepEqual(side.findAll((node) => node.type === "h2").map((h) => text(h.children)), ["판정", "분류함", "사건"]);
  assert.equal(side.findAllByType("a")[0].props["aria-current"], "page");
  // 사이드바 아이콘(사용자 11/05): 판정 결과별 그룹도 항목마다 서로 다른 아이콘
  const resultIcons = side.findAllByType("a").map((a) => iconNames(a));
  assert.deepEqual(resultIcons, [["inbox"], ["alert-octagon"], ["alert-circle"], ["shield-check"], ["clock"], ["question-mark"], ["circle-x"], ["folder-open"], ["circle-check"], ["timeline-event"]]);
  assert.equal(new Set(resultIcons.flat()).size, resultIcons.length, "each category has its own icon");
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
  // 이상 원인 줄(사용자 11/13): 거래 ID 다음 줄에 서버 causes 첫 원인 + 외 N건, 원인이 없으면 줄 없음. 인터페이스·채널은 오른쪽 아래 작은 글자.
  const causeOf = (id) => rowOf(id).findAll((node) => node.props["data-cause"] === "true").map((node) => text(node.children));
  assert.deepEqual([causeOf("d-1"), causeOf("j-2"), causeOf("d-3")],
    [["상태 F 실패 · 응답 코드 E500"], [], ["처리 시간 8,000 ms (기준 ≥ 5,000 ms) 외 1건"]]);
  const contextOf = (id) => rowOf(id).find((node) => node.props["data-context"] === "true");
  assert.equal(text(contextOf("d-1").children), "인터페이스 IF-ORD-1 · 채널 CH-01");
  assert.ok(contextOf("d-1").parent.props.className.includes("text-xs") && contextOf("d-1").parent.props.className.includes("sm:justify-items-end"),
    "interface/channel sit in the small right meta column");
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
  assert.deepEqual(riskSide.findAllByType("a").map((a) => text(a.children)), ["전체4", "위험1", "주의1", "참고0", "위험도 미정2", "보류1", "완료0", "사건 보기"]);
  // 위험도·분류함 그룹 아이콘(위험도 미정 포함)
  assert.deepEqual(riskSide.findAllByType("a").map((a) => iconNames(a)),
    [["inbox"], ["flame"], ["alert-triangle"], ["info-circle"], ["help-circle"], ["folder-open"], ["circle-check"], ["timeline-event"]]);
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
  assert.deepEqual(side.findAll((node) => node.type === "h2").map((h) => text(h.children)), ["위험도", "분류함", "사건"]);
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

// 2b) 알림 배지(상단)와 이상 분석 목록이 같은 판정 SSE를 나눠 쓴다: 열린 연결은 1개, 앞선 커서(배지 40)부터 받는다.
{
  requested.length = 0;
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-1")])),
    "GET /api/notifications/unread-count": () => json({ latestSeq: "40", unreadCount: 0, unreadByType: { decision: 0, incident: 0, protocol: 0, job: 0 } }),
  };
  const app = await mount("/analysis");
  await wait(() => app.shown().includes("실시간 연결됨"), "live status shown with the bell mounted");
  await wait(() => requested.some((r) => r.path === "/api/notifications/unread-count"), "bell read its cursor");
  await tick(50);
  const streams = () => requested.filter((r) => r.path === "/api/pipeline/events");
  assert.equal(streams().filter((r) => !r.signal?.aborted).length, 1, "one open decision stream for bell + analysis");
  assert.equal(streams().find((r) => !r.signal?.aborted).headers["Last-Event-ID"], "40", "shared stream starts at the earlier cursor");
  await app.cleanup();
  assert.ok(streams().every((r) => r.signal?.aborted), "unmount closes the shared stream");
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

// 3b) 확인 결과별 완료·근거 구분(사용자 11/15): 관리자만 완료 아래 확인 결과 하위 항목(건수), 근거 선택은 주소·API에 반영(1쪽으로)
{
  const outcomeCounts = { ...counts, workflow_resolved: 4, workflow_resolved_actual_anomaly: 2, workflow_resolved_normal: 1, workflow_resolved_undetermined: 0 };
  routes = { "GET /api/analysis/decisions": () => json({ ...listBody([item("d-1")]), counts: outcomeCounts }) };
  requested.length = 0;
  const admin = await mount("/analysis?category=workflow_resolved_normal&page=2", "admin");
  await wait(() => admin.shown().includes("완료 · 정상 판정"), "outcome list heading");
  await wait(() => admin.shown().includes("TX-d-1"), "outcome list rows");
  assert.ok(listCalls()[0].search.includes("category=workflow_resolved_normal"), "outcome category reaches the API");
  const side = admin.renderer.root.findByProps({ id: "analysis-categories" });
  const subList = side.find((node) => node.props["data-slot"] === "side-nav-sub");
  assert.deepEqual(subList.findAllByType("a").map((a) => [text(a.children), a.props["aria-current"]]),
    [["실제 이상2", undefined], ["정상1", "page"], ["판단 불가0", undefined]], "sub items under 완료 with counts, current = page");
  assert.ok(side.findAllByType("a").some((a) => text(a.children) === "완료4"), "완료 keeps its own count");
  assert.deepEqual(iconNames(headingOf(admin, "완료 · 정상 판정")), ["circle-check"]);
  assert.ok(subList.findAllByType("a")[0].props.href.includes("category=workflow_resolved_actual_anomaly") && !subList.findAllByType("a")[0].props.href.includes("page="));
  // 근거 선택: 전체 + 판정 상세 근거 구분 이름
  const basisTrigger = admin.renderer.root.find((node) => node.props["aria-labelledby"] === "analysis-basis-label" && node.props["data-slot"] === "select-trigger");
  let basisSelect = basisTrigger;
  while (basisSelect && !basisSelect.props.onValueChange) basisSelect = basisSelect.parent;
  assert.equal(basisSelect.props.value, "all");
  assert.equal(text(admin.renderer.root.find((node) => node.props.id === "analysis-basis-label").children), "근거");
  await act(async () => basisSelect.props.onValueChange("relation"));
  await wait(() => location(admin.router).includes("basis=relation"), "basis reaches the URL");
  assert.equal(location(admin.router), "/analysis?category=workflow_resolved_normal&basis=relation", "basis keeps the category and resets the page");
  await wait(() => listCalls().some((call) => call.search.includes("basis=relation") && call.search.includes("category=workflow_resolved_normal")), "basis reaches the API");
  await admin.cleanup();
  // user: 하위 항목 없음, 확인 결과별 주소는 완료로 replace(API에 확인 결과 분류를 보내지 않음)
  requested.length = 0;
  const user = await mount("/analysis?category=workflow_resolved_actual_anomaly&basis=ml", "user");
  await wait(() => user.shown().includes("완료 판정"), "user falls back to 완료");
  assert.equal(location(user.router), "/analysis?category=workflow_resolved&basis=ml");
  assert.ok(listCalls().length > 0 && listCalls().every((call) => !call.search.includes("workflow_resolved_")), "user never requests an outcome category");
  assert.equal(user.renderer.root.findByProps({ id: "analysis-categories" }).findAll((node) => node.props["data-slot"] === "side-nav-sub").length, 0);
  await user.cleanup();
}

// 4) 상세(최종): 요약·판정 기록·근거. v3 판정은 근거 요약 카드가 없고 다듬기도 요청하지 않는다(옛 설명 패널 없음)
{
  requested.length = 0;
  const detail = { ...item("d-1", { jobId: "j-1", stage: "replaced" }), configuration: { id: "config-1", createdAt: "2026-11-01T00:00:00Z", detectionMode: "parallel", ruleCount: 1,
    models: [{ modelKey: "iforest", adapterName: "sklearn-iforest" }] }, job: { id: "j-1", status: "succeeded", attemptCount: 2, maxAttempts: 3, errorCode: null, createdAt: null, finishedAt: null } };
  const workflowPuts = [];
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-1")])),
    "GET /api/analysis/decisions/d-1": () => json({ ...detail, workflow: { state: detail.workflowState, changedAt: null } }),
    "GET /api/analysis/decisions/d-1/interims": () => json({ id: "d-1", jobId: "j-1", items: [
      { id: "i1", kind: "first_anomaly", attemptNo: 1, seq: 1, createdAt: "2026-11-03T01:00:00Z", payload: { schemaVersion: 4, composite: { anomalous: true, grade: "anomaly", basisMethods: ["hard:process-status"] } } },
      { id: "i2", kind: "superseded", attemptNo: 1, seq: 2, supersededByAttemptNo: 2, createdAt: "2026-11-03T01:00:02Z", payload: {} },
    ] }),
    "GET /api/analysis/decisions/d-1/flow": () => json(flowBody("d-1")),
    // 그래프 시도 등급(트리 노드 바탕): P99 관측(p2)은 저장된 v4 등급 anomaly_sign
    "GET /api/analysis/decisions/d-1/graph": () => json({ id: "d-1", jobId: null, truncated: false, counts: { processes: 2, messages: 1, errors: 1 },
      graph: { layer: "instance", id: "TX-d-1", version: null, sourceId: "source-1", snapshotId: null, period: null, ruleSetRevision: null, generatedAt: "2026-11-03T01:05:00.000Z" },
      nodes: [{ id: "process:TX-d-1:P01", kind: "process", attrs: { focus: true, verification: null, attempts: [{ observationId: "p1", grade: null, focus: true }] } },
        { id: "process:TX-d-1:P99", kind: "process", attrs: { focus: false, verification: null, attempts: [{ observationId: "p2", grade: "anomaly_sign", focus: false }] } }],
      edges: [] }),
    "PUT /api/analysis/decisions/d-1/read": () => json({ id: "d-1", readAt: "2026-11-03T02:00:00.000000Z" }),
    "PUT /api/analysis/decisions/d-1/workflow": (_t, options) => {
      workflowPuts.push(JSON.parse(options.body));
      if (workflowPuts.length === 1) detail.workflowState = "open";
      // 두 번째 요청: 다른 사용자가 먼저 완료로 바꿈 → 409, 재조회하면 서버 상태가 resolved
      else detail.workflowState = "resolved";
      return workflowPuts.length === 1 ? json({ id: "d-1", workflow: { state: "open", changedAt: "2026-11-03T02:00:00Z" } })
        : json({ code: "workflow_state_conflict" }, 409);
    },
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
  assert.ok(!shown.includes("룰 변경 기록 보기") && !shown.includes("당시 머신러닝 모델") && !app.renderer.root.findAll((node) => node.props["data-admin-links"]).length, "admin links are admin only");
  // 섹션 이름·아이콘(사용자 11/04): Process Flow(IconBinaryTree), 위험도 미정이면 판정 요약 아이콘 없음. v3 판정은 근거 요약 카드 없음
  assert.ok(!shown.includes("AI 설명"), "old section name is gone");
  assert.equal(app.renderer.root.findAll((node) => node.type === "h2" && text(node.children) === "AI 판정 근거 요약").length, 0, "no summary card, no old explanation panel");
  assert.deepEqual(iconNames(headingOf(app, "Process Flow")), ["binary-tree"]);
  assert.deepEqual(iconNames(headingOf(app, "판정 요약")), [], "no risk icon when the risk level is unrated");
  assert.deepEqual(iconNames(headingOf(app, "판정 기록")), [], "no legacy counterpart, no icon");
  assert.ok(svgs(app.renderer.root).every(hiddenIcon), "every icon on the detail page is decorative (aria-hidden)");
  assert.ok(shown.includes("Rule 판정 근거") && shown.includes("이상 규칙(상태가 S가 아님)") && shown.includes("위험"));
  assert.ok(app.renderer.root.findAll((node) => node.type === "th" && node.props.title === "process-status").length === 1, "v3 rule key only as tooltip");
  // 위험도(v3 이상 = 위험도 미정, U6)·업무 분류
  assert.ok(shown.includes("위험도위험도 미정") && shown.includes("업무 분류감지됨"), "risk unrated and workflow label");
  // Process Flow(와이어프레임 dTree): 통계, Focus Process 기본 선택, 노드 선택 → 원본 정보, BODY 행 없음
  // 건수는 머리글이 아니라 보기 상자 오른쪽 아래(사용자 11/12), 범례는 상자 오른쪽 위
  const flowBox = app.renderer.root.findByProps({ role: "group", "aria-label": "Process Flow" });
  assert.equal(text(flowBox.findByProps({ "data-flow-counts": "" }).children), "(프로세스 230건 · 메시지 1건 · 에러 4건)", "counts use the true totals, not the capped node list");
  assert.equal(shown.split("프로세스 230건").length - 1, 1, "counts appear once (inside the box, no longer in the card header)");
  const legend = flowBox.findByProps({ "data-flow-legend": "" });
  // 오른쪽 위, 트리가 넘쳐 스크롤 막대가 생기면 그 폭(--flow-sb-x)만큼 안쪽(검토 11/12)
  assert.ok(legend.props.className.includes("top-2") && legend.props.className.includes("right-[calc(0.5rem+var(--flow-sb-x,0px))]"), "legend sits at the top right of the box");
  assert.ok(flowBox.findByProps({ "data-flow-counts": "" }).props.className.includes("bottom-[calc(0.5rem+var(--flow-sb-y,0px))]"), "counts clear the horizontal scrollbar");
  // 범례(사용자 11/12): 이상(붉은 칸)·이상 징후(노란 칸)·판정 대상(표적 아이콘). 일반 칸 없음, 트리에는 로그 없음 노드가 없어 점선 항목 없음
  assert.equal(text(legend.children), "이상이상 징후판정 대상", "tree legend: anomaly, anomaly sign, focus marker");
  assert.deepEqual(legend.findAllByType("li").map((li) => li.findAll((n) => n.type === "span" && n.props["aria-hidden"] === "true")[0]?.props.className ?? iconNames(li).join()),
    ["size-3 rounded-sm border border-destructive-text bg-destructive-soft", "size-3 rounded-sm border border-warning-text bg-warning-soft", "crosshair"],
    "legend chips: red, amber (node fills, dark outlines so they stand out on the muted box), focus marker icon");
  const viewButtons = app.renderer.root.findByProps({ role: "group", "aria-label": "Process Flow 보기" }).findAllByType("button");
  assert.deepEqual(viewButtons.map((b) => iconNames(b)), [["sitemap"], ["route"]], "view toggle buttons carry icons");
  // 노드에는 머리말 없이 ID만(방향은 작은 배지), 접근성 이름은 종류 + ID · 상태
  const nodeButton = (name) => app.renderer.root.findAll((node) => node.type === "button" && String(node.props["aria-label"] ?? "").startsWith(name))[0];
  assert.deepEqual(["Transaction TX-d-1", "Focus Process P01", "Process P99", "Message REQ MSG-1"].map((name) => nodeButton(name)?.props["aria-label"]),
    ["Transaction TX-d-1 · S", "Focus Process P01 · F", "Process P99 · S", "Message REQ MSG-1 · S"], "accessible names keep the node kind");
  assert.deepEqual(["Transaction TX-d-1", "Focus Process P01", "Process P99", "Message REQ MSG-1"].map((name) => text(nodeButton(name).children)),
    ["STX-d-1", "FP01", "SP99", "SREQMSG-1"], "visible node text is the status and the ID only (no Transaction:/Process:/Message: prefix)");
  assert.ok(!/Transaction:|Process:|Message:/.test(shown), "no header prefixes in the tree");
  assert.equal(nodeButton("Focus Process P01").props["aria-pressed"], true);
  // 노드 바탕 = 판정 결과(사용자 11/12): 판정 PROCESS = 판정 요약 결과(이상 신호 있음·v3 등급 없음 → 이상, 붉은 바탕),
  // 다른 PROCESS = 그래프 시도 등급(P99 anomaly_sign → 노란 바탕), Transaction·Message = 흰 바탕. 노드에 테두리·선택 링 없음
  await wait(() => nodeButton("Process P99").props.className.includes("bg-warning-soft"), "graph attempt grade colors the other process");
  const classTokens = (name) => nodeButton(name).props.className.split(/\s+/);
  assert.ok(classTokens("Focus Process P01").includes("bg-destructive-soft"), "focus process with an anomaly result: red fill");
  assert.equal(nodeButton("Focus Process P01").props["data-tone"], "anomaly");
  assert.ok(!classTokens("Process P99").includes("bg-destructive-soft") && nodeButton("Process P99").props["data-tone"] === "anomaly_sign", "anomaly sign: amber fill");
  assert.ok(!classTokens("Transaction TX-d-1").some((c) => /^bg-(destructive|warning|secondary)/.test(c)), "transaction node stays white");
  for (const name of ["Transaction TX-d-1", "Focus Process P01", "Process P99", "Message REQ MSG-1"]) {
    const tokens = classTokens(name);
    assert.ok(!tokens.some((c) => /^border(-|$)/.test(c) || /^ring-/.test(c)), `${name}: no border or selection ring`);
    assert.ok(tokens.includes("focus-visible:ring-3"), `${name}: keyboard focus ring kept`);
  }
  assert.ok(classTokens("Focus Process P01").includes("shadow-md") && !classTokens("Process P99").includes("shadow-md"), "default selection = stronger shadow, not a border");
  // 판정 대상 표시: 흰 바탕이어도 찾을 수 있게 표적 아이콘(범례와 같은 아이콘)
  const markers = (name) => nodeButton(name).findAll((n) => n.props?.["data-focus-marker"] === "");
  assert.ok(markers("Focus Process P01").length > 0 && markers("Process P99").length === 0, "focus marker only on the focus process");
  // 색 바탕 위 상태 배지는 흰 바탕(배지 모양이 노드 바탕에 묻히지 않게)
  const badge = (name) => nodeButton(name).findAll((n) => n.props?.["data-status-badge"] === "")[0].props.className;
  assert.ok(badge("Focus Process P01").includes("bg-card") && badge("Focus Process P01").includes("text-destructive-text"), "F badge on red: white chip");
  assert.ok(badge("Process P99").includes("bg-card") && badge("Transaction TX-d-1").includes("bg-success-soft"), "badges keep their fill on white nodes");
  assert.ok(app.renderer.root.findByProps({ role: "group", "aria-label": "Process Flow" }).props.className.includes("h-80"), "fixed view box height");
  // 위→아래 트리(포함 관계): Message 버튼은 그 Process 항목(li) 안의 하위 목록에 있다. 기본 보기는 트리.
  const itemOf = (button) => { let n = button.parent; while (n && n.type !== "li") n = n.parent; return n; };
  assert.ok(itemOf(nodeButton("Focus Process P01")).findAll((node) => node === nodeButton("Message REQ")).length === 1, "message nested under its process");
  assert.ok(itemOf(nodeButton("Transaction TX-d-1")).findAll((node) => node === nodeButton("Process P99")).length === 1, "process nested under the transaction");
  assert.equal(app.renderer.root.findByProps({ role: "group", "aria-label": "Process Flow 보기" }).findAllByType("button")[0].props["aria-pressed"], true, "tree view is the default");
  // 노드 아이콘(#100 이전 ProcessFlowTree): Transaction IconDatabase, Process IconCodeDots(F면 오류 색), Message IconMessage2, 원본 정보 IconListDetails
  assert.deepEqual(["Transaction TX-d-1", "Focus Process P01", "Process P99", "Message REQ"].map((name) => iconNames(nodeButton(name))),
    [["database"], ["code-dots", "crosshair"], ["code-dots"], ["message-2"]], "focus process also carries the focus marker");
  assert.ok(svgs(nodeButton("Focus Process P01"))[0].props.className.includes("text-destructive-text"), "failed process icon uses the error tone");
  // 선택 노드 원본 정보는 접지 않고 늘 보인다(details·summary 없음)
  assert.equal(app.renderer.root.findAll((node) => node.type === "details" || node.type === "summary").length, 0, "no dropdown toggle");
  assert.deepEqual(iconNames(app.renderer.root.find((node) => node.type === "h3" && text(node.children) === "선택 노드 원본 정보")), ["list-details"]);
  assert.ok(app.shown().includes("PROCESS_IDP01") && app.shown().includes("STATUSF · Code E500"), "focus process fields shown without opening anything");
  await act(async () => nodeButton("Message REQ").props.onClick());
  await wait(() => app.shown().includes("MESSAGE_IDMSG-1") && app.shown().includes("DATA_SIZE512 bytes"), "selected message fields");
  assert.ok(classTokens("Message REQ MSG-1").includes("shadow-md") && !classTokens("Focus Process P01").includes("shadow-md"), "selection moves as a shadow");
  assert.ok(!app.shown().includes("BODY"), "BODY is not read or shown");
  await act(async () => nodeButton("Focus Process P01").props.onClick());
  await wait(() => app.shown().includes("STATUSF · Code E500") && app.shown().includes("DURATION_TIME— 확인 불가"), "process fields");
  // 상세를 열면 읽음 표시 1회
  await wait(() => requested.filter((r) => r.method === "PUT" && r.path === "/api/analysis/decisions/d-1/read").length === 1, "read mark sent once");
  assert.deepEqual(JSON.parse(requested.find((r) => r.method === "PUT" && r.path === "/api/analysis/decisions/d-1/read").body),
    { occurredAt: detail.occurredAt }, "read up to the item time the screen saw");
  // 조사·조치 기록은 관리자에게만
  assert.ok(!app.shown().includes("조사·조치 기록"), "feedback box hidden for users");
  assert.equal(requested.some((r) => r.path.endsWith("/feedback")), false, "users do not read feedback");
  assert.equal(app.renderer.root.findAllByType(WorkflowRecordDialog).length, 0, "users change the state directly, no record dialog");
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
  const back = app.renderer.root.findAll((node) => node.type === "a" && text(node.children).includes("목록"))[0];
  assert.equal(back.props.href, "/analysis?category=anomaly", "back link keeps list conditions");
  assert.ok(!requested.some((r) => r.method === "POST" && r.path === "/api/jobs") && !app.shown().includes("설명 요청"), "no explanation request or button");
  await app.cleanup();
}

// 4b) 관리자 상세(evidence v4): 종합 근거·세부 방법별 결과 표, 위험도 결정 근거, 조사·조치 기록 저장
{
  requested.length = 0;
  const methods = [
    { methodId: "hard:process-status", category: "hard", methodVersion: "process-status-hr-v1", status: "succeeded", anomalous: false, riskLevel: null, elapsedMs: 9.1, evidence: [] },
    { methodId: "soft:retry_count-1a2b3c4d", category: "soft", status: "succeeded", anomalous: true, severity: "warning", riskLevel: "warning", elapsedMs: 9.3,
      condition: { field: "retry_count", operator: "gte", threshold: "3", unit: "회" }, observedValue: "4", evidence: [] },
    { methodId: "ml:iforest", category: "ml", modelVersionId: "7f3a9c21-0000-4000-8000-000000000001", status: "succeeded", anomalous: true, riskLevel: null, riskLevelReason: "level_undefined", elapsedMs: 21.7, score: 0.71,
      scoreDetail: { raw: 0.71, threshold: "0.61", direction: "higher_is_anomaly", normalized: { kind: "threshold_ratio", value: 1.164 } }, evidence: [] },
    { methodId: "ml:lof", category: "ml", modelVersionId: "0b12cd34-0000-4000-8000-000000000002", status: "not_ready", anomalous: null, cause: "timeout", elapsedMs: 3001, evidence: [] },
  ];
  const evidence = { schemaVersion: 4, anomalous: true, ruleSetRevision: 13, rules: [{ ruleKey: "process-status", result: "no_hit", observedStatus: "S" }],
    detectionPolicy: { version: 2, k: { hard: 1, soft: 1, ml: 2 }, deadlineMs: 3000 }, methodResults: methods,
    categoryResults: { hard: { n: 1, k: 1, result: "normal" }, soft: { n: 1, k: 1, result: "anomalous" }, ml: { n: 2, k: 2, result: "undetermined" } },
    composite: { policy: { k: { hard: 1, soft: 1, ml: 2 } }, status: "partial", anomalous: true, grade: "anomaly_sign", basisMethods: ["soft:retry_count-1a2b3c4d"],
      risk: { policy: "risk-max-v1", levelTable: "method-defined-v2", status: "rated", level: "warning", determinedBy: ["soft:retry_count-1a2b3c4d"], unratedMethods: ["ml:iforest"] } } };
  const detail = { ...item("d-4", { grade: "anomaly_sign", riskStatus: "rated", riskLevel: "warning", status: "partial", evidence, processStatus: "S", responseCode: "0000" }),
    configuration: null, job: null, explanation: null, workflow: { state: "resolved", changedAt: "2026-11-03T02:10:00Z" } };
  const feedback = [{ id: "f-1", kind: "action", reason: "재처리 확인", actorId: "admin-1", actorEmail: "admin@example.test", createdAt: "2026-11-03T02:09:00Z" }];
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
  await wait(() => app.shown().includes("세부 방법별 결과"), "v4 tables rendered");
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
  assert.ok(shown.includes("원래 상태로 복원") && !shown.includes("처리 보류") && !shown.includes("로그 분류하기"), "resolved state offers only restore");
  // 저장된 조사·조치 기록은 판정 기록 카드에 읽기 전용 줄로(사용자 11/13): 최종 판정 뒤 시각 순. 업무 분류 상태 줄은 없음(판정 요약에 있음). 아래 별도 카드·저장 버튼 없음.
  assert.ok(!shown.includes("조사·조치 기록") && !shown.includes("피드백 저장"), "no bottom record card");
  await wait(() => app.renderer.root.findAll((node) => node.type === "li" && node.props["data-entry"] === "record").length === 1, "records in the timeline");
  const timeline = app.renderer.root.findAll((node) => node.type === "li").filter((node) => node.parent?.type === "ol" && node.parent.props.className?.includes("border-l"));
  assert.deepEqual(timeline.map((node) => text(node.children)), [`${stampTime("2026-11-03T01:02:03Z")}최종`,
    `${stampTime("2026-11-03T02:09:00Z")}조치 기록 · admin@example.test수정재처리 확인`], "own record carries [수정] (4d)");
  assert.equal(requested.filter((r) => r.method === "GET" && r.path.endsWith("/feedback")).length, 1, "admins read records with the detail");
  assert.equal(app.renderer.root.findByType(WorkflowRecordDialog).props.open, false);
  // 종합 근거 설명 문단(와이어프레임 dCatsNote 문구 그대로)
  assert.equal(text(app.renderer.root.find((node) => node.props["data-category-note"] === "true").children),
    "세부 방법 → 대분류 → 종합. 대분류마다 결과를 정한 뒤 종합합니다. 이상 규칙 대분류가 이상이면 이상, 이상 징후 규칙·머신러닝 모델 대분류만 이상이면 이상 징후, 켠 대분류가 모두 정상 확인일 때만 이상 신호 없음, 그 밖은 정상 여부 미확인.");
  // 관리자 링크(와이어프레임 dAdminLinks): 룰 변경 기록(룰 설정 변경 기록 영역) + ML 방법에 기록된 모델 버전마다 그 버전 상세(ID 앞 8자리, 전체 ID 툴팁). 관련 평가는 평가 ID가 없어 없음
  const adminLinks = () => app.renderer.root.find((node) => node.type === "div" && node.props["data-admin-links"] === "true").findAll((node) => node.type === "a");
  assert.deepEqual(adminLinks().map((a) => [text(a.children), a.props.href]), [
    ["룰 변경 기록 보기", "/detection/rules#rules-history"],
    ["당시 머신러닝 모델 7f3a9c21 보기", "/detection/models/7f3a9c21-0000-4000-8000-000000000001"],
    ["당시 머신러닝 모델 0b12cd34 보기", "/detection/models/0b12cd34-0000-4000-8000-000000000002"],
  ]);
  assert.equal(adminLinks()[1].props.title, "7f3a9c21-0000-4000-8000-000000000001");
  assert.ok(!app.shown().includes("관련 평가 보기"), "no evaluation link without a linked evaluation id");
  // 당시 모델 → 모델 버전 상세: 탐지 설정 뒤로가기가 이 판정 상세로 돌아온다
  await act(async () => adminLinks()[1].props.onClick({ button: 0, metaKey: false, altKey: false, ctrlKey: false, shiftKey: false, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }));
  await wait(() => app.router.state.location.pathname === "/detection/models/7f3a9c21-0000-4000-8000-000000000001", "model version detail opened");
  assert.deepEqual(app.router.state.location.state, { returnTo: "/analysis/d/d-4" });
  const backBar = () => app.renderer.root.findAll((node) => node.type === "a" && node.props["aria-label"] === "뒤로 · 판정 상세");
  await wait(() => backBar().length === 1, "detection back bar returns to the decision");
  assert.equal(backBar()[0].props.href, "/analysis/d/d-4");
  await app.cleanup();
}

// 4c) 관리자 로그 분류하기(사용자 11/13): 보류·완료 버튼 하나 [로그 분류하기] → 가운데 대화상자(기록 유형·사유, 취소 버튼 없음) → 안에서 보류·완료 =
//     사유가 있으면 기록 저장 후 처리 상태 변경. 저장 실패면 상태를 바꾸지 않음, 상태 변경 실패는 대화상자 유지·오류 그대로, 다시 눌러도 기록은 한 번만 저장.
//     처리 중에는 닫히지 않음. 저장된 기록은 판정 기록에 읽기 전용으로. 원래 상태로 복원은 바로.
{
  requested.length = 0;
  const detail = { ...item("d-5"), configuration: null, job: null };
  let changedAt = null;
  const feedback = [{ id: "f-1", kind: "action", reason: "재처리 확인", actorId: "admin-1", actorEmail: "admin@example.test", createdAt: "2026-11-03T01:00:00Z" }];
  const puts = [];
  const posts = [];
  let failNext = true;
  let failSave = false;
  let holdSave = null;
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-5")])),
    "GET /api/analysis/decisions/d-5": () => json({ ...detail, workflow: { state: detail.workflowState, changedAt } }),
    "GET /api/analysis/decisions/d-5/interims": () => json({ id: "d-5", jobId: null, items: [] }),
    "GET /api/analysis/decisions/d-5/flow": () => json({ id: "d-5", jobId: null, transaction: null, processes: [], messages: [], truncated: false }),
    "PUT /api/analysis/decisions/d-5/read": () => json({ id: "d-5", readAt: "2026-11-03T02:00:00Z" }),
    "GET /api/analysis/decisions/d-5/feedback": () => json({ id: "d-5", items: feedback }),
    "POST /api/analysis/decisions/d-5/feedback": async (_t, options) => {
      const body = JSON.parse(options.body);
      posts.push(body);
      if (holdSave) await holdSave.promise;
      if (failSave) return json({ code: "analysis_storage_unavailable" }, 503);
      feedback.unshift({ id: `f-${feedback.length + 1}`, ...body, actorId: "admin-1", actorEmail: "admin@example.test", createdAt: "2026-11-03T02:00:00Z" });
      return json({ id: "d-5", item: feedback[0] }, 201);
    },
    "PUT /api/analysis/decisions/d-5/workflow": (_t, options) => {
      const body = JSON.parse(options.body);
      puts.push(body);
      if (failNext) {
        failNext = false;
        return json({ code: "workflow_state_conflict" }, 409);
      }
      detail.workflowState = body.state;
      changedAt = "2026-11-03T02:00:01Z";
      return json({ id: "d-5", workflow: { state: body.state, changedAt } });
    },
  };
  const app = await mount("/analysis/d/d-5", "admin");
  await wait(() => app.shown().includes("판정 요약") && app.shown().includes("업무 분류감지됨"), "admin detail rendered");
  const headerButton = (label) => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === label)[0];
  const headerLabels = () => app.renderer.root.findAll((node) => node.type === "button" && (node.props["data-workflow-to"] || node.props["data-workflow-classify"])).map((b) => text(b.children));
  const dialog = () => app.renderer.root.findByType(WorkflowRecordDialog);
  const records = () => app.renderer.root.findAll((node) => node.type === "li" && node.props["data-entry"] === "record").map((node) => text(node.children));
  // 감지됨 = [로그 분류하기] 하나(처리 보류·처리 완료 버튼 없음), 기존 기록은 판정 기록에 읽기 전용
  assert.deepEqual(headerLabels(), ["로그 분류하기"]);
  assert.deepEqual(iconNames(headerButton("로그 분류하기")), ["tags"]);
  await wait(() => records().length === 1, "saved record shown in the timeline");
  assert.ok(records()[0].endsWith("조치 기록 · admin@example.test수정재처리 확인"));
  assert.equal(dialog().props.open, false);
  // 로그 분류하기 → 대화상자(상태는 아직 그대로), X·Esc로 닫으면 요청 없음
  await act(async () => headerButton("로그 분류하기").props.onClick());
  assert.deepEqual([dialog().props.open, dialog().props.state], [true, "detected"]);
  await act(async () => dialog().props.onOpenChange(false));
  assert.equal(dialog().props.open, false, "Esc / outside click / X closes the dialog");
  assert.equal(puts.length + posts.length, 0, "closing sends nothing");
  await act(async () => headerButton("로그 분류하기").props.onClick());
  const renderBody = async () => {
    let body;
    await act(async () => { body = create(React.createElement(WorkflowRecordBody, dialog().props)); });
    return body;
  };
  let body = await renderBody();
  assert.equal(body.root.findByProps({ id: "analysis-feedback-reason" }).props.maxLength, 2000);
  assert.ok(body.root.findByProps({ id: "analysis-feedback-kind" }));
  // 확인 결과(029): 기록 유형 다음 선택, 기본 미입력(—), 값 3개는 기획 문구 그대로
  const outcomeSelect = body.root.findByProps({ id: "analysis-feedback-outcome" });
  assert.equal(outcomeSelect.props.value, "", "outcome starts unset");
  assert.deepEqual(outcomeSelect.findAll((node) => node.type === "option").map((o) => [o.props.value, text(o.children)]),
    [["", "—"], ["actual-anomaly", "실제 이상"], ["normal", "정상"], ["undetermined", "판단 불가"]]);
  const labelsShown = body.root.findAll((node) => node.type === "label").map((l) => text(l.children));
  assert.deepEqual(labelsShown, ["기록 유형", "확인 결과", "조사·조치 사유"], "field order");
  assert.ok(!text(body.toJSON()).includes("재처리 확인"), "records are not listed in the dialog (they live in the timeline)");
  assert.deepEqual(body.root.findAll((node) => node.type === "button").map((b) => text(b.children)), ["처리 보류", "처리 완료"], "no 취소 button");
  const variants = () => Object.fromEntries(body.root.findAll((node) => typeof node.type !== "string" && node.props["data-workflow-to"]).map((b) => [b.props["data-workflow-to"], b.props.variant]));
  assert.deepEqual(variants(), { open: "outline", resolved: "default" }, "처리 완료 is the primary action");
  await act(async () => body.unmount());
  // 기록 저장 실패(503): 상태를 바꾸지 않고 대화상자 안에 오류 그대로, 입력은 남는다
  failSave = true;
  await act(async () => dialog().props.onReason("저장 실패 사유"));
  await act(async () => dialog().props.onSubmit("open"));
  await wait(() => Boolean(dialog().props.saveError), "save failure kept in the dialog");
  assert.deepEqual([puts.length, dialog().props.open, dialog().props.reason], [0, true, "저장 실패 사유"], "no PUT after a failed save, memo kept");
  body = await renderBody();
  assert.ok(text(body.toJSON()).includes("피드백 저장 실패 · analysis_storage_unavailable"), "save error text");
  await act(async () => body.unmount());
  failSave = false;
  posts.length = 0;
  // 처리 중에는 닫히지 않는다(저장 요청을 붙잡아 둔 동안 X·Esc 무시), 버튼도 막힌다
  let release;
  holdSave = { promise: new Promise((resolve) => { release = resolve; }) };
  await act(async () => dialog().props.onKind("suspected-false-positive"));
  await act(async () => dialog().props.onReason("  배치 재처리  "));
  await act(async () => dialog().props.onSubmit("resolved"));
  await wait(() => dialog().props.pending === true, "pending while saving");
  await act(async () => dialog().props.onOpenChange(false));
  assert.equal(dialog().props.open, true, "close is blocked while pending");
  body = await renderBody();
  assert.ok(body.root.findAll((node) => node.type === "button").every((b) => b.props.disabled), "actions disabled while pending");
  await act(async () => body.unmount());
  holdSave = null;
  await act(async () => release());
  // 기록 저장(앞뒤 공백 제거) 다음 상태 변경. 409 → 대화상자 유지, 오류 그대로, 저장된 사유는 비움
  await wait(() => Boolean(dialog().props.changeError), "state change failure kept in the dialog");
  assert.deepEqual(posts, [{ kind: "suspected-false-positive", reason: "배치 재처리" }], "no outcome key when 확인 결과 is not chosen");
  assert.deepEqual(puts, [{ state: "resolved", expectedState: "detected" }]);
  const order = requested.filter((r) => r.method !== "GET" && r.path.startsWith("/api/analysis/decisions/d-5/") && !r.path.endsWith("/read")).map((r) => r.method);
  assert.deepEqual(order, ["POST", "POST", "PUT"], "failed save, then record saved before the state change");
  assert.deepEqual([dialog().props.open, dialog().props.reason], [true, ""], "dialog stays open, saved memo cleared");
  assert.ok(!app.shown().includes("업무 분류 실패"), "the error shows inside the dialog, not behind it");
  await wait(() => records().length === 2 && records()[1].includes("오탐 의심 · admin@example.test수정배치 재처리"), "saved record refreshed in the timeline");
  body = await renderBody();
  assert.ok(text(body.toJSON()).includes("업무 분류 실패 · workflow_state_conflict"), "error text pattern");
  await act(async () => body.unmount());
  // 다시 처리 완료: 기록은 다시 저장하지 않고 상태만 바꾼 뒤 닫힘 → 완료(판정 기록에 업무 분류 상태 줄은 생기지 않음)
  await act(async () => dialog().props.onSubmit("resolved"));
  await wait(() => dialog().props.open === false, "dialog closes after the state change");
  assert.equal(posts.length, 1, "retry does not save the memo twice");
  assert.deepEqual(puts[1], { state: "resolved", expectedState: "detected" });
  await wait(() => app.shown().includes("업무 분류완료") && Boolean(headerButton("원래 상태로 복원")), "resolved after refetch");
  assert.deepEqual(headerLabels(), ["원래 상태로 복원"], "resolved offers only restore");
  assert.ok(records().length === 2 && !records().some((r) => r.includes("업무 분류 ·")), "no workflow state line in the timeline");
  await wait(() => listCalls().length >= 2, "list (workflow box counts) refetched");
  // 원래 상태로 복원은 대화상자 없이 바로, 사유 없이 보류는 기록 없이 상태만 → 보류 = [원래 상태로 복원][로그 분류하기], 대화상자는 처리 완료만
  await act(async () => headerButton("원래 상태로 복원").props.onClick());
  assert.equal(dialog().props.open, false, "restore does not open the dialog");
  await wait(() => app.shown().includes("업무 분류감지됨"), "restored");
  assert.deepEqual(puts[2], { state: "detected", expectedState: "resolved" });
  await act(async () => headerButton("로그 분류하기").props.onClick());
  await act(async () => dialog().props.onSubmit("open"));
  await wait(() => dialog().props.open === false && app.shown().includes("업무 분류보류"), "memo is optional");
  assert.deepEqual([posts.length, puts[3]], [1, { state: "open", expectedState: "detected" }]);
  await wait(() => headerLabels().length === 2, "open state buttons");
  assert.deepEqual(headerLabels(), ["원래 상태로 복원", "로그 분류하기"]);
  await act(async () => headerButton("로그 분류하기").props.onClick());
  body = await renderBody();
  assert.deepEqual(body.root.findAll((node) => node.type === "button").map((b) => text(b.children)), ["처리 완료"], "open state: the dialog offers only 처리 완료");
  await act(async () => body.unmount());
  // 확인 결과(029): 고르고 사유가 비면 저장·상태 변경 없이 와이어프레임 안내 `조사·조치 사유를 입력하세요.`, 고른 값은 남는다.
  // 사유를 넣으면 안내가 사라지고, 처리 완료 = 기록(확인 결과 포함) 저장 뒤 상태 변경, 저장 뒤 확인 결과·사유를 비운다. 판정 기록 줄에 `확인 결과 <값>`.
  await act(async () => headerButton("로그 분류하기").props.onClick());
  await act(async () => dialog().props.onOutcome("actual-anomaly"));
  await act(async () => dialog().props.onReason("   "));
  const putsBefore = puts.length;
  await act(async () => dialog().props.onSubmit("resolved"));
  assert.deepEqual([dialog().props.reasonRequired, dialog().props.open, dialog().props.outcome, posts.length, puts.length],
    [true, true, "actual-anomaly", 1, putsBefore], "outcome without a reason sends nothing");
  body = await renderBody();
  assert.ok(text(body.toJSON()).includes("조사·조치 사유를 입력하세요."), "reason required text");
  assert.equal(body.root.findByProps({ id: "analysis-feedback-reason" }).props["aria-invalid"], true);
  await act(async () => body.unmount());
  await act(async () => dialog().props.onReason("HUB 연결 오류 확인"));
  assert.equal(dialog().props.reasonRequired, false, "typing a reason clears the notice");
  await act(async () => dialog().props.onSubmit("resolved"));
  await wait(() => dialog().props.open === false && app.shown().includes("업무 분류완료"), "saved with outcome, then resolved");
  assert.deepEqual(posts[1], { kind: "suspected-false-positive", reason: "HUB 연결 오류 확인", outcome: "actual-anomaly" });
  assert.deepEqual(puts.at(-1), { state: "resolved", expectedState: "open" });
  await wait(() => records().some((r) => r.includes("오탐 의심 · 확인 결과 실제 이상 · admin@example.test")), "outcome shown in the timeline");
  assert.deepEqual([dialog().props.outcome, dialog().props.reason], [null, ""], "outcome and memo cleared after the save");
  // 확인 결과를 고른 뒤 다시 미입력(—)으로 돌리면 안내도 사라진다
  await act(async () => headerButton("원래 상태로 복원").props.onClick());
  await wait(() => app.shown().includes("업무 분류감지됨"), "restored again");
  await act(async () => headerButton("로그 분류하기").props.onClick());
  await act(async () => dialog().props.onOutcome("normal"));
  await act(async () => dialog().props.onSubmit("open"));
  assert.equal(dialog().props.reasonRequired, true);
  await act(async () => dialog().props.onOutcome(null));
  assert.equal(dialog().props.reasonRequired, false, "clearing the outcome clears the notice");
  await act(async () => dialog().props.onOpenChange(false));
  await app.cleanup();
}

// 4d) 관리자 기록 수정(사용자 11/15, 032): 판정 기록의 내 기록 줄에만 [수정] → 같은 업무 분류 대화상자를 저장된 값으로 채워 연다 → [저장] = PUT(최초 기록 id 경로,
//     expectedId = 본 판본). 처리 상태는 바꾸지 않음. 수정된 기록은 같은 자리에 `· 수정됨`(툴팁 = 수정 시각). 실패는 `피드백 수정 실패 · <코드>`, 빈 사유는 저장하지 않음.
{
  const detail = { ...item("d-6"), configuration: null, job: null, explanation: null, workflow: { state: "resolved", changedAt: "2026-11-03T02:10:00Z" } };
  const feedback = [
    { id: "f-2", originId: "f-2", kind: "action", reason: "다른 관리자 확인", outcome: null, actorId: "admin-2", actorEmail: "other@example.test",
      createdAt: "2026-11-03T02:08:00Z", editedAt: null },
    { id: "f-1", originId: "f-1", kind: "action", reason: "재처리 확인", outcome: "normal", actorId: "admin-1", actorEmail: "admin@example.test",
      createdAt: "2026-11-03T02:05:00Z", editedAt: null },
  ];
  const puts = [];
  let failNext = null;
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([item("d-6")])),
    "GET /api/analysis/decisions/d-6": () => json(detail),
    "GET /api/analysis/decisions/d-6/interims": () => json({ id: "d-6", jobId: null, items: [] }),
    "GET /api/analysis/decisions/d-6/flow": () => json({ id: "d-6", jobId: null, transaction: null, processes: [], messages: [], truncated: false }),
    "PUT /api/analysis/decisions/d-6/read": () => json({ id: "d-6", readAt: "2026-11-03T02:00:00Z" }),
    "GET /api/analysis/decisions/d-6/feedback": () => json({ id: "d-6", items: feedback }),
    "PUT /api/analysis/decisions/d-6/feedback/f-1": (_t, options) => {
      const body = JSON.parse(options.body);
      puts.push(body);
      if (failNext) { const code = failNext; failNext = null; return json({ code }, 409); }
      const { expectedId: _seen, ...values } = body;
      feedback[1] = { id: `f-1-v${puts.length}`, originId: "f-1", outcome: null, ...values, actorId: "admin-1", actorEmail: "admin@example.test",
        createdAt: "2026-11-03T02:05:00Z", editedAt: "2026-11-03T03:00:00Z" };
      return json({ id: "d-6", item: feedback[1] });
    },
  };
  const app = await mount("/analysis/d/d-6", "admin");
  const records = () => app.renderer.root.findAll((node) => node.type === "li" && node.props["data-entry"] === "record");
  await wait(() => records().length === 2, "records in the timeline");
  const editButtons = () => app.renderer.root.findAll((node) => node.type === "button" && node.props["data-feedback-edit"]);
  assert.deepEqual(editButtons().map((button) => [button.props["data-feedback-edit"], text(button.children)]), [["f-1", "수정"]],
    "only the requester's own record has [수정]");
  const modal = () => app.renderer.root.findAllByType(FeedbackEditModal);
  assert.equal(modal().length, 0);
  await act(async () => editButtons()[0].props.onClick());
  await wait(() => modal().length === 1, "edit dialog opened");
  assert.deepEqual([modal()[0].props.kind, modal()[0].props.outcome, modal()[0].props.reason, modal()[0].props.canSave], ["action", "normal", "재처리 확인", false],
    "filled with the stored values, nothing to save yet");
  let body;
  await act(async () => { body = create(React.createElement(FeedbackEditBody, modal()[0].props)); });
  assert.deepEqual(body.root.findAll((node) => node.type === "button").map((button) => text(button.children)), ["저장"], "one [저장], no workflow buttons");
  assert.equal(body.root.findByProps({ id: "analysis-feedback-edit-reason" }).props.value, "재처리 확인");
  assert.equal(body.root.findByProps({ id: "analysis-feedback-edit-outcome" }).props.value, "normal");
  await act(async () => body.unmount());
  // 빈 사유: 저장하지 않고 빈 사유 안내.
  await act(async () => modal()[0].props.onReason("   "));
  await act(async () => modal()[0].props.onSave());
  assert.deepEqual([modal()[0].props.reasonRequired, puts.length], [true, 0], "empty reason is not saved");
  // 실패(409): 대화상자 유지, `피드백 수정 실패 · <코드>`.
  await act(async () => modal()[0].props.onReason("  재처리 후 정상 확인  "));
  assert.equal(modal()[0].props.reasonRequired, false);
  await act(async () => modal()[0].props.onOutcome(null));
  failNext = "feedback_edit_conflict";
  await act(async () => modal()[0].props.onSave());
  await wait(() => Boolean(modal()[0]?.props.error), "conflict kept in the dialog");
  await act(async () => { body = create(React.createElement(FeedbackEditBody, modal()[0].props)); });
  assert.ok(text(body.toJSON()).includes("피드백 수정 실패 · feedback_edit_conflict"));
  await act(async () => body.unmount());
  // 저장: 경로 = 최초 기록 id, expectedId = 연 판본, 확인 결과를 비우면 outcome 키 없음. 처리 상태는 바꾸지 않는다.
  await act(async () => modal()[0].props.onSave());
  await wait(() => modal().length === 0, "dialog closes after the save");
  assert.deepEqual(puts, [{ kind: "action", reason: "재처리 후 정상 확인", expectedId: "f-1" }, { kind: "action", reason: "재처리 후 정상 확인", expectedId: "f-1" }]);
  assert.equal(requested.filter((r) => r.method === "PUT" && r.path === "/api/analysis/decisions/d-6/workflow").length, 0, "edit does not change the workflow state");
  await wait(() => text(records()[0].children).includes("수정됨"), "edited record re-read");
  assert.deepEqual(records().map((node) => text(node.children)), [
    `${stampTime("2026-11-03T02:05:00Z")}조치 기록 · admin@example.test · 수정됨수정재처리 후 정상 확인`,
    `${stampTime("2026-11-03T02:08:00Z")}조치 기록 · other@example.test다른 관리자 확인`,
  ], "same position (original time), edited marker, [수정] still on own record");
  const editedLine = records()[0].find((node) => node.type === "span" && node.props.title);
  assert.equal(editedLine.props.title, stampTime("2026-11-03T03:00:00Z"), "edit time in the tooltip");
  // 다시 열면 새 판본 값과 새 expectedId.
  await act(async () => editButtons()[0].props.onClick());
  await wait(() => modal().length === 1, "reopened");
  assert.deepEqual([modal()[0].props.reason, modal()[0].props.outcome], ["재처리 후 정상 확인", null]);
  await act(async () => modal()[0].props.onKind("suspected-false-positive"));
  await act(async () => modal()[0].props.onSave());
  await wait(() => modal().length === 0, "second edit saved");
  assert.deepEqual(puts.at(-1), { kind: "suspected-false-positive", reason: "재처리 후 정상 확인", expectedId: "f-1-v2" });
  await app.cleanup();
}

// 4e) 판정 요약 판정 코드: AI 판정 근거 요약(#208)과 같은 결과 코드 이름표, 원래 코드는 툴팁. 이름표가 없는 코드는 원래 코드 그대로(툴팁 없음).
{
  const ddOf = (app, label) => {
    const dt = app.renderer.root.find((node) => node.type === "dt" && text(node.children) === label);
    return dt.parent.findByType("dd");
  };
  for (const [code, shown, title] of [["PROCESS_STATUS_F", "상태 F 실패", "PROCESS_STATUS_F"], ["NEW_CODE", "NEW_CODE", undefined]]) {
    const detail = { ...item("d-7", { decisionCode: code }), configuration: null, job: null };
    routes = {
      "GET /api/analysis/decisions": () => json(listBody([item("d-7")])),
      "GET /api/analysis/decisions/d-7": () => json(detail),
      "GET /api/analysis/decisions/d-7/interims": () => json({ id: "d-7", jobId: null, items: [] }),
      "PUT /api/analysis/decisions/d-7/read": () => json({ id: "d-7", readAt: "2026-11-03T02:00:00Z" }),
    };
    const app = await mount("/analysis/d/d-7");
    await wait(() => app.shown().includes("판정 코드"), "summary rendered");
    const dd = ddOf(app, "판정 코드");
    assert.equal(text(dd.children), shown, `decision code ${code} shown as its label`);
    assert.equal(dd.findAll((node) => node.type === "span" && node.props.title)[0]?.props.title, title, `raw code ${code} in the tooltip only when labelled`);
    await app.cleanup();
  }
}

// 4f) 기록 수정 대화상자: 같은 화면(재마운트 없음)에서 판정이 바뀌면 닫힌다 — 다른 판정에 갔다가 돌아와도 다시 열리지 않음(#219 검토 권고).
{
  const { DecisionDetailView } = await import("../src/features/analysis/components/DecisionDetailView.tsx");
  const own = { id: "f-1", originId: "f-1", kind: "action", reason: "재처리 확인", outcome: null, actorId: "admin-1", actorEmail: "admin@example.test",
    createdAt: "2026-11-03T02:05:00Z", editedAt: null };
  const details = Object.fromEntries(["d-8", "d-9"].map((id) => [id, { ...item(id), configuration: null, job: null, explanation: null,
    workflow: { state: "resolved", changedAt: "2026-11-03T02:10:00Z" } }]));
  routes = {
    "GET /api/analysis/decisions/d-8/feedback": () => json({ id: "d-8", items: [own] }),
    "GET /api/analysis/decisions/d-9/feedback": () => json({ id: "d-9", items: [{ ...own, id: "f-9", originId: "f-9" }] }),
    "PUT /api/analysis/decisions/d-8/read": () => json({ id: "d-8", readAt: "2026-11-03T02:00:00Z" }),
    "PUT /api/analysis/decisions/d-9/read": () => json({ id: "d-9", readAt: "2026-11-03T02:00:00Z" }),
  };
  const ready = (data) => ({ data, isPending: false, isError: false, error: null, refetch: () => {} });
  let show;
  const Harness = () => {
    const [id, setId] = React.useState("d-8");
    show = setId;
    const detail = details[id];
    return React.createElement(DecisionDetailView, { id: "d-8", query: ready(detail), interims: ready({ id, jobId: null, items: [] }),
      flow: ready({ id, jobId: null, transaction: null, processes: [], messages: [], truncated: false }), backHref: "/analysis", isWide: false, onToggleWide: () => {} });
  };
  currentUser = { id: "admin-1", email: "admin@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", "token-admin");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  const router = createMemoryRouter([{ path: "*", element: React.createElement(Harness) }], { initialEntries: ["/analysis/d/d-8"] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))));
  });
  const editButtons = () => renderer.root.findAll((node) => node.type === "button" && node.props["data-feedback-edit"]);
  const modal = () => renderer.root.findAllByType(FeedbackEditModal);
  await wait(() => editButtons().map((b) => b.props["data-feedback-edit"]).join() === "f-1", "own record on d-8");
  await act(async () => editButtons()[0].props.onClick());
  await wait(() => modal().length === 1, "edit dialog opened on d-8");
  await act(async () => show("d-9"));
  await wait(() => editButtons().map((b) => b.props["data-feedback-edit"]).join() === "f-9", "own record on d-9");
  assert.equal(modal().length, 0, "dialog closed after moving to another decision");
  await act(async () => show("d-8"));
  await wait(() => editButtons().map((b) => b.props["data-feedback-edit"]).join() === "f-1", "back on d-8");
  await tick(50);
  assert.equal(modal().length, 0, "dialog stays closed after coming back");
  await act(async () => renderer.unmount());
  router.dispose();
  client.clear();
}

// 5) 상세(최종 전): 진행 중 기록 / 404 / 작업 ID로 열었는데 최종 판정이 있으면 판정 ID 주소로 교체
{
  const pendingDetail = { ...item("j-2", { kind: "interim", stage: "updated", signal: "yes", grade: "anomaly_sign", decisionId: null, jobId: "j-2", status: null, ruleStatus: null, mlStatus: null,
    evidence: { schemaVersion: 4, composite: { anomalous: true, grade: "anomaly_sign", basisMethods: ["soft:elapsed_ms-3f9a2c1d"] } }, decidedAt: null }),
    configuration: null, job: { id: "j-2", status: "running", attemptCount: 1, maxAttempts: 3, errorCode: null, createdAt: null, finishedAt: null } };
  routes = {
    "GET /api/analysis/decisions": () => json(listBody([])),
    "GET /api/analysis/decisions/j-2": () => json(pendingDetail),
    "GET /api/analysis/decisions/j-2/interims": () => json({ id: "j-2", jobId: "j-2", items: [] }),
    "GET /api/analysis/decisions/j-9": () => json({ ...item("d-9", { jobId: "j-9" }), configuration: null, job: null }),
    "GET /api/analysis/decisions/d-9": () => json({ ...item("d-9", { jobId: "j-9" }), configuration: null, job: null }),
    "GET /api/analysis/decisions/missing": () => json({ code: "decision_not_found" }, 404),
  };
  const app = await mount("/analysis/d/j-2");
  await wait(() => app.shown().includes("남은 방법 결과 대기"), "interim detail shown");
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
  // 알림 페이지에서 연 작업 ID 상세: 판정 ID로 replace해도 돌아가기 state가 남아 "알림으로"(navigation.md 휴대폰 폭).
  const fromAlerts = await mount("/analysis");
  await act(async () => fromAlerts.router.navigate("/analysis/d/j-9", { state: { returnTo: "/notifications?view=all" } }));
  await wait(() => location(fromAlerts.router) === "/analysis/d/d-9", "job id replaced by the decision id");
  assert.deepEqual(fromAlerts.router.state.location.state, { returnTo: "/notifications?view=all" }, "return state survives the replace");
  await wait(() => fromAlerts.renderer.root.findAll((n) => n.type === "a" && text(n.children) === "알림으로")[0]?.props.href === "/notifications?view=all",
    "alerts back link after the replace");
  await fromAlerts.cleanup();
}

// 6) 옛 주소·운영 현황 링크·폐지한 API 미호출
{
  routes = { "GET /api/analysis/decisions": () => json(listBody([])) };
  const app = await mount("/analysis/process-decisions");
  assert.equal(location(app.router), "/analysis");
  await app.cleanup();
}
// 위험도 미정을 고르면 글자도 아이콘과 같은 톤이다(약한 표시는 선택 전에만)
{
  routes = { "GET /api/analysis/decisions": () => json(listBody([item("d-1")], 1)) };
  const app = await mount("/analysis?category=risk_unrated");
  await wait(() => app.shown().includes("TX-d-1"), "unrated rows");
  const link = app.renderer.root.findByProps({ id: "analysis-categories" }).findAllByType("a").find((node) => node.props["aria-current"] === "page");
  const label = link.findAll((node) => node.type === "span" && text(node.children) === "위험도 미정")[0];
  assert.ok(label.props.className.includes("text-body"), "still the smaller size");
  assert.ok(!label.props.className.includes("text-muted-foreground"), "selected label follows the icon");
  // 넓은 화면 전환: 버튼 이름·저장값이 바뀐다. 사이드바가 밀려 나가는 효과(exit)는 DOM이 없는 이 시험 환경에서 끝나지 않아 브라우저에서 확인한다.
  const toggle = (name) => app.renderer.root.findAll((node) => node.type === "button" && node.props["aria-label"] === name)[0];
  await act(async () => toggle("넓은 화면으로 보기").props.onClick());
  await wait(() => Boolean(toggle("콤팩트 화면으로 보기")), "wide mode toggle");
  assert.equal(storage.get("esb_layout_wide_analysis"), "true", "wide mode is remembered");
  await act(async () => toggle("콤팩트 화면으로 보기").props.onClick());
  await wait(() => Boolean(toggle("넓은 화면으로 보기")) && app.renderer.root.findAll((node) => node.props?.id === "analysis-categories").length > 0, "compact mode shows the side nav");
  assert.equal(storage.get("esb_layout_wide_analysis"), "false");
  await app.cleanup();
}

// 실행 순서(그래프) 보기 범례: 같은 FlowViewOverlay, 로그 없음(점선)은 missing 노드가 있을 때만(ProcessGraphView notLogged)
{
  const { FlowViewOverlay } = await import("../src/features/analysis/components/ProcessFlowCard.tsx");
  const legendOf = async (notLogged) => {
    let renderer;
    await act(async () => {
      renderer = create(React.createElement(FlowViewOverlay, { stats: "(프로세스 3건 · 메시지 0건 · 에러 0건)", notLogged }));
    });
    const legend = renderer.root.findByProps({ "data-flow-legend": "" });
    const result = [text(legend.children), legend.findAllByType("li").length,
      legend.findAll((n) => n.type === "span" && String(n.props.className ?? "").includes("border-dashed")).length];
    await act(async () => renderer.unmount());
    return result;
  };
  assert.deepEqual(await legendOf(true), ["이상이상 징후판정 대상로그 없음", 4, 1], "graph legend with a not-logged node: dashed item added");
  assert.deepEqual(await legendOf(false), ["이상이상 징후판정 대상", 3, 0], "graph legend without missing nodes: no dashed item");
}

// 그래프 보기 조각 불러오기·그리기 실패는 카드 안 ErrorState(실행 순서 불러오기 실패)로 가두고, 다시 시도하면 새로 불러온다
{
  const { GraphViewBoundary } = await import("../src/features/analysis/components/ProcessFlowCard.tsx");
  let failing = true;
  let retried = 0;
  const Chunk = () => {
    if (failing) throw new TypeError("Failed to fetch dynamically imported module");
    return React.createElement("p", null, "graph ready");
  };
  const quiet = console.error;
  console.error = () => {};
  let renderer;
  try {
    await act(async () => {
      renderer = create(React.createElement(GraphViewBoundary, { onRetry: () => (retried += 1) }, React.createElement(Chunk)));
    });
  } finally {
    console.error = quiet;
  }
  const alert = renderer.root.findByProps({ role: "alert" });
  assert.ok(text(alert.children).includes("실행 순서 불러오기 실패"), "chunk failure stays inside the card");
  failing = false;
  await act(async () => alert.findByType("button").props.onClick());
  assert.equal(retried, 1, "retry asks for a fresh chunk");
  assert.ok(text(renderer.root.children).includes("graph ready"), "graph renders after retry");
  await act(async () => renderer.unmount());
}

// 13) 프로토콜 알림 판정 상세(protocol-explanation-job.md 1절 알림 대상·11절): 관리자 + 최종 판정 + evidence.protocolReview(알림 목록과 같은 판별)일 때만
// "LLM 설명" 칸 [설명 보기] → POST explain_protocol {sourceId, target:{kind:"alert", decisionId}} → 진행 중 → 결과(경로 칩·확인할 점·버전 N). 실패는 `설명 실패 · <코드>`.
// 일반 사용자·프로토콜 근거 없음·형식이 다른 근거는 칸도 요청도 없다.
{
  const review = { version: 1, kind: "path_unmatched", interfaceId: "IF-ORD-1", protocolRuleId: "hard:proto-0000000a", ruleSetRevision: 15,
    routes: [{ channels: ["CH-01", "CH-09"], pathKey: "CH-01:CH-09", matched: false }], routesReason: null, endpoints: { start: "CH-01", end: "CH-09", matchesRoutes: true, ambiguous: false } };
  const protocolDetail = (id, protocolReview) => ({ ...item(id, { evidence: { ...item(id).evidence, ...(protocolReview === undefined ? {} : { protocolReview }) } }),
    configuration: null, job: null });
  const summary = { format: "protocol-explanation-v1", target: { kind: "alert", decisionId: "d-p1" }, interfaceId: "IF-ORD-1", ruleSetRevision: 15,
    explanation: "인터페이스 IF-ORD-1의 거래 경로 P1은 버전 15 허용 경로에 없습니다.", reviewPoints: [], paths: { P1: ["CH-01", "CH-09"] },
    facts: { format: "protocol-explain-facts-v1", kind: "alert", sha256: "d".repeat(64) }, check: { passed: true, failures: [], coverage: {} } };
  let jobState = { status: "queued", errorCode: null, resultSummary: null };
  const posted = [];
  const protocolRoutes = (id, detail) => ({
    "GET /api/analysis/decisions": () => json(listBody([item(id)])),
    [`GET /api/analysis/decisions/${id}`]: () => json(detail),
    [`GET /api/analysis/decisions/${id}/interims`]: () => json({ id, jobId: null, items: [] }),
    [`GET /api/analysis/decisions/${id}/feedback`]: () => json({ items: [] }),
    "GET /api/llm/status": () => json({ available: true, checkedAt: "2026-11-14T01:00:00.000Z", errorCode: null }),
    "GET /api/jobs": () => json({ items: [], pagination: { limit: 100, hasMore: false, nextCursor: null } }),
    "POST /api/jobs": (_target, init) => {
      posted.push({ body: JSON.parse(init.body), key: new Headers(init.headers).get("Idempotency-Key") });
      return json({ id: "pe-1", status: "queued", kind: "explain_protocol", inputSpec: JSON.parse(init.body) }, 201);
    },
    "GET /api/jobs/pe-1": () => json({ id: "pe-1", kind: "explain_protocol", status: jobState.status, inputSpec: {}, errorCode: jobState.errorCode,
      resultSummary: jobState.resultSummary, attempts: [] }),
  });
  const protocolCard = (app) => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-slot"] === "section-card" && node.props["aria-label"] === "LLM 설명")[0];
  const decisionCard = (app) => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-slot"] === "section-card" && text(node.children).startsWith("AI 판정 근거 요약"))[0];
  const askButton = (app) => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "설명 보기")[0];

  requested.length = 0;
  routes = protocolRoutes("d-p1", protocolDetail("d-p1", review));
  const app = await mount("/analysis/d/d-p1", "admin");
  await wait(() => Boolean(askButton(app)) && !askButton(app).props.disabled, "explain button on a protocol alert decision");
  assert.ok(!decisionCard(app), "no old decision explanation panel (protocol detail has no evidence summary here)");
  await act(async () => askButton(app).props.onClick());
  await wait(() => text(protocolCard(app).children).includes("설명을 만드는 중입니다…"), "running");
  assert.deepEqual(posted.map((p) => p.body), [{ kind: "explain_protocol", sourceId: "source-1", target: { kind: "alert", decisionId: "d-p1" } }], "alert target = decision id");
  assert.ok(posted[0].key, "idempotency key");
  jobState = { status: "succeeded", errorCode: null, resultSummary: summary };
  await act(async () => app.client.invalidateQueries({ queryKey: ["protocol-explanation-job"] }));
  await wait(() => text(protocolCard(app).children).includes("코드가 집계한 사실만으로 쓴 설명입니다 · 버전 15 기준"), "result with version note");
  const shownText = text(protocolCard(app).children);
  assert.ok(shownText.includes("인터페이스 IF-ORD-1의 거래 경로 P1CH-01CH-09은 버전 15 허용 경로에 없습니다."), shownText);
  assert.ok(!shownText.includes("확인할 점"), "no review points → hidden");
  assert.ok(!askButton(app), "no request button over a result");
  await app.cleanup();

  // 실패: `설명 실패 · <코드>`, 다시 요청할 수 있다.
  jobState = { status: "failed", errorCode: "protocol_explanation_facts_unavailable", resultSummary: null };
  routes = { ...protocolRoutes("d-p2", protocolDetail("d-p2", review)),
    "GET /api/jobs": () => json({ items: [{ id: "pe-1", kind: "explain_protocol", status: "failed", inputSpec: { kind: "explain_protocol", sourceId: "source-1", target: { kind: "alert", decisionId: "d-p2" } },
      attemptCount: 1, maxAttempts: 3, nextRunAt: null, errorCode: "protocol_explanation_facts_unavailable", resultConfigVersionId: null, createdAt: "2026-11-14T01:00:00.000000Z", finishedAt: null, result: null }],
      pagination: { limit: 100, hasMore: false, nextCursor: null } }) };
  const failed = await mount("/analysis/d/d-p2", "admin");
  await wait(() => Boolean(protocolCard(failed)) && text(protocolCard(failed).children).includes("설명 실패 · protocol_explanation_facts_unavailable"), "failure code");
  assert.equal(askButton(failed).props.disabled, false, "request again after a failure");
  await failed.cleanup();

  // 일반 사용자: 칸 없음, 설명 작업 목록도 읽지 않음.
  requested.length = 0;
  routes = protocolRoutes("d-p3", protocolDetail("d-p3", review));
  const user = await mount("/analysis/d/d-p3", "user");
  await wait(() => user.shown().includes("판정 요약"), "detail shown");
  await tick(30);
  assert.ok(!protocolCard(user) && !askButton(user), "not shown to a non-admin");
  assert.equal(requested.some((r) => r.path === "/api/jobs" && r.method === "GET"), false, "no explanation job lookup as a user");
  await user.cleanup();

  // 관리자라도 프로토콜 근거가 없거나 형식이 다르면(version 2·빈 interfaceId) 칸이 없다.
  for (const [id, protocolReview] of [["d-p4", undefined], ["d-p5", { ...review, version: 2 }], ["d-p6", { ...review, interfaceId: "" }]]) {
    routes = protocolRoutes(id, protocolDetail(id, protocolReview));
    const plain = await mount(`/analysis/d/${id}`, "admin");
    await wait(() => plain.shown().includes("판정 요약"), "detail shown");
    await tick(30);
    assert.ok(!protocolCard(plain), `no protocol explanation for ${id}`);
    await plain.cleanup();
  }
}

// 5g) AI 판정 근거 요약 카드(서버 evidenceSummary): 판정 상세 본문 첫 블록(Process Flow 위), 블록 순서(한 줄 요약 → 문단 → 표 → 확인할 것 → 고정 안내),
// 문자열 그대로(문단 = 문장을 공백 한 칸으로 이음), 행이 없는 표는 없음, ML 표가 빈 이유 한 줄, 묶음 구성원 들여쓰기·참가자 사이에만 구분선.
// 응답은 공용 시험 벡터의 기대값 그대로다. 옛 설명 패널(요청 버튼·결과)은 없다.
const fs = await import("node:fs");
const vectors = JSON.parse(fs.readFileSync(new URL("../../contracts/evidence-summary-vectors.json", import.meta.url), "utf8"));
const summaryOf = (id) => ({ templateVersion: vectors.templateVersion, notice: vectors.notice, ...vectors.cases.find((c) => c.id === id).expected });
const cardOf = (app) => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-slot"] === "section-card"
  && node.findAll((child) => child.props?.["data-evidence-summary-block"] === "line").length > 0);
const blocks = (card) => card.findAll((node) => typeof node.props?.["data-evidence-summary-block"] === "string").map((node) => node.props["data-evidence-summary-block"]);
const blockText = (card, name) => text(card.find((node) => node.props?.["data-evidence-summary-block"] === name).children);
const tables = (card) => card.findAll((node) => node.type === "table").map((node) => node.props["data-evidence-summary-table"]);
const summaryRoutes = (id, detail, polishedReads = []) => ({
  "GET /api/analysis/decisions": () => json(listBody([item(id)])),
  [`GET /api/analysis/decisions/${id}`]: () => json(detail),
  [`GET /api/analysis/decisions/${id}/interims`]: () => json({ id, jobId: null, items: [] }),
  [`GET /api/analysis/decisions/${id}/polished`]: () => json(polishedReads.length > 1 ? polishedReads.shift() : polishedReads[0] ?? { polishState: "none", polished: null }),
  "POST /api/jobs": () => json({ id: `explain-${id}`, status: "queued", kind: "explain", inputSpec: {} }, 201),
});
const summaryDetail = (id, summary, overrides = {}, polish = {}) => ({ ...item(id, { riskStatus: "rated", riskLevel: "warning", ...overrides }), configuration: null, job: null,
  evidenceSummary: summary, polishState: "none", polished: null, polishRequestable: false, ...polish });
const show = async (id, summary, overrides = {}, polish = {}, polishedReads = []) => {
  routes = summaryRoutes(id, summaryDetail(id, summary, overrides, polish), polishedReads);
  const app = await mount(`/analysis/d/${id}`);
  await wait(() => app.shown().includes("판정 요약"), "detail shown");
  return app;
};
const posts = (id) => requested.filter((r) => r.method === "POST" && r.path === "/api/jobs" && JSON.parse(r.body).decisionId === id);
const polishedReads = (id) => requested.filter((r) => r.path === `/api/analysis/decisions/${id}/polished`).length;
{
  requested.length = 0;
  // 혼합(소프트룰 + ML): 규칙 표·ML 표 모두, 확인할 것 2줄. 자동 요청 가능이 아니면(polishRequestable=false) 요청하지 않는다
  const mixed = summaryOf("example_c_soft_elapsed_and_ml");
  const app = await show("d-es1", mixed);
  const [card] = cardOf(app);
  assert.ok(card, "evidence summary card shown");
  const headings = app.renderer.root.findAll((node) => node.type === "h2").map((node) => text(node.children));
  assert.ok(headings.indexOf("AI 판정 근거 요약") < headings.indexOf("Process Flow") && headings.indexOf("Process Flow") < headings.indexOf("판정 요약")
    && headings.indexOf("판정 요약") < headings.indexOf("판정 근거"), `card is the first block: ${headings.join(" / ")}`);
  assert.equal(headings.filter((h) => h === "AI 판정 근거 요약").length, 1, "only the new card carries the title (old explanation panel removed)");
  assert.ok(!app.shown().includes("설명 요청") && !app.shown().includes("아직 설명을 요청하지 않은 판정입니다."), "no request button or old panel text");
  assert.deepEqual(blocks(card), ["line", "paragraph", "table", "checks", "notice"]);
  assert.equal(blockText(card, "line"), `${mixed.summaryLine.prefix}${mixed.summaryLine.text}`);
  assert.equal(blockText(card, "paragraph"), mixed.sentences.join(" "), "one paragraph, single spaces");
  const nameSpans = card.findAll((node) => node.type === "span" && String(node.props.className).includes("whitespace-nowrap")).map((node) => text(node.children));
  assert.ok(nameSpans.includes("처리량(TOTAL_COUNT)"), "a name with parentheses is kept on one line");
  assert.ok(nameSpans.includes("오류 건수(ERROR_COUNT)"), "a spaced count name is kept on one line with its first word");
  assert.deepEqual(tables(card), ["rule", "ml"]);
  const table = (kind) => card.find((node) => node.type === "table" && node.props["data-evidence-summary-table"] === kind);
  const cells = (kind) => table(kind).findAll((node) => node.type === "tr").map((tr) => tr.findAll((node) => node.type === "th" || node.type === "td").map((c) => text(c.children)));
  assert.deepEqual(cells("rule"), [["규칙 조건", "이번 값", "결과"], ["처리 시간 ≥ 3,000 ms", "4,200 ms", "이상 징후"]]);
  assert.deepEqual(cells("ml"), [["모델이 본 변수", "이번 값", "평소 값", "영향"], ["처리 시간", "4,200 ms", "800 ms", "62%"],
    ["처리량(TOTAL_COUNT)", "120", "100", "31%"], ["오류 건수(ERROR_COUNT)", "15", "0"], ["오류 비율", "0.125", "0"]], "group members on their own rows, impact once per player");
  const mlRows = table("ml").findAll((node) => node.type === "tr" && node.props["data-player-id"]);
  assert.deepEqual(mlRows.map((tr) => String(tr.props.className ?? "").includes("border-b-0")), [false, true, true, false], "separators only between players");
  assert.deepEqual(mlRows.map((tr) => String(tr.findAll((node) => node.type === "td")[0].props.className).includes("pl-6")), [false, false, true, true], "second group member onwards indented");
  const heads = card.findAll((node) => node.type === "th");
  assert.equal(heads.find((th) => text(th.children) === "평소 값").props.title, "학습 데이터 중앙값");
  assert.deepEqual(heads.filter((th) => text(th.children) === "이번 값").map((th) => th.props.title), [undefined, "단위 미확인 값 포함"], "unit note only where count values are shown");
  assert.deepEqual(card.findAll((node) => node.type === "li").map((li) => text(li.children)), mixed.checks.map((c) => c.text));
  assert.equal(blockText(card, "notice"), "AI 근거 요약은 참고용이며, 최종 판단은 담당자 확인이 필요합니다.");
  for (const raw of ["soft:", "ml:", "hard:", "elapsed_ms", "group:", "(단위 미확인)", "Shapley", "phi"]) assert.ok(!text(card.children).includes(raw), `no raw ${raw}`);
  await tick(30);
  assert.deepEqual([posts("d-es1").length, polishedReads("d-es1")], [0, 0], "not requestable: no request, no polling");
  await app.cleanup();

  // 하드룰: 이번 값 칸 = 이름 + 값(여러 개면 `, `), 건수 값은 덧말 없이 칸 툴팁
  const hardRule = await show("d-es6", summaryOf("example_a_hard_success_with_error"), { riskLevel: "critical" });
  const hardCell = cardOf(hardRule)[0].findAll((node) => node.type === "td")[1];
  assert.deepEqual([text(hardCell.children), hardCell.props.title], ["상태 S(성공), 오류 건수(ERROR_COUNT) 3", "단위 미확인"]);
  const statusSpans = cardOf(hardRule)[0].findAll((node) => node.type === "span" && String(node.props.className).includes("whitespace-nowrap")).map((node) => text(node.children));
  assert.ok(statusSpans.includes("S(성공)") && statusSpans.includes("오류 건수(ERROR_COUNT)"), `status and count names kept whole: ${statusSpans.join(" / ")}`);
  // v3 앞머리 `이상 규칙 - `는 글자 그대로이고 묶음(줄바꿈 없는 이름)에 들어가지 않는다
  assert.ok(blockText(cardOf(hardRule)[0], "paragraph").startsWith("이상 규칙 - 처리 상태가 S(성공)인데"), "v3 prefix shown as is");
  assert.ok(statusSpans.every((span) => !span.includes("-") && !span.includes("이상 규칙")), `prefix is not wrapped: ${statusSpans.join(" / ")}`);
  await hardRule.cleanup();

  // scale 1 구성원: 평소 값 칸은 서버 글자(평소와 다름) 그대로, 단위를 붙이지 않는다
  const scaled = await show("d-es7", summaryOf("ml_value_scale_unit_elapsed"), { riskStatus: "unrated", riskLevel: null, grade: "anomaly_sign" });
  const scaledCells = cardOf(scaled)[0].findAll((node) => node.type === "td").map((td) => text(td.children));
  assert.deepEqual(scaledCells, ["처리 시간", "4,200 ms", "평소와 다름", "70%"]);
  await scaled.cleanup();

  // ML 기여도 없음: 표 없이 상태 낱말 한 줄, 확인할 것 없음
  const noContribution = await show("d-es2", summaryOf("ml_no_contribution_skipped"), { riskStatus: "unrated", riskLevel: null, grade: "anomaly_sign" });
  const [plain] = cardOf(noContribution);
  assert.deepEqual(blocks(plain), ["line", "paragraph", "table", "notice"]);
  assert.deepEqual(tables(plain), []);
  assert.equal(blockText(plain, "table"), "기여도 계산 안 함");
  await noContribution.cleanup();

  // 정상 완료: 표·확인할 것 없음. 요약이 없는 판정(null·형식 어긋남)은 카드 없음
  const normal = await show("d-es3", summaryOf("normal_complete"), { signal: "no", riskStatus: "not_applicable" });
  assert.deepEqual(blocks(cardOf(normal)[0]), ["line", "paragraph", "notice"]);
  await normal.cleanup();
  for (const [id, summary] of [["d-es4", null], ["d-es5", { summaryLine: { prefix: "주의" }, notice: "x" }]]) {
    const none = await show(id, summary);
    assert.equal(cardOf(none).length, 0, `no card for ${id}`);
    await none.cleanup();
  }
}

// 5h) 블록 2 다듬기 자동 요청(버튼 없음): 이상 최종 판정 + polishRequestable + polishState none이면 한 번만 접수(새 Idempotency-Key, 판정 id만),
// pending 동안 …/polished를 5초마다 읽고 done이면 멈춘다. source=llm이면 문단만 바뀌고, source=code·실패·none이면 코드 문단 그대로(오류·상태 문구 없음).
{
  const mixed = summaryOf("example_c_soft_elapsed_and_ml");
  const code = mixed.sentences.join(" ");
  const polishedText = "이상 징후 규칙 - 처리 시간이 4,200 ms로 기준 3,000 ms를 넘어 이상 징후로 판단했습니다. 머신러닝 모델 - 평소 800 ms보다 긴 처리 시간 4,200 ms가 가장 크게 영향을 주어(62%) 이상 징후로 판단했고, 다음으로 평소와 다른 처리량(TOTAL_COUNT)·오류 건수(ERROR_COUNT)·오류 비율이 영향을 주었습니다(31%).";
  const paragraphOf = (app) => blockText(cardOf(app)[0], "paragraph");
  const requestable = { polishRequestable: true };

  // 요청 → pending 읽기 → done(llm) → 문단 교체·읽기 멈춤. 다른 블록은 그대로
  requested.length = 0;
  const reads = [{ polishState: "pending", polished: null }, { polishState: "done", polished: { text: polishedText, source: "llm" } }];
  const app = await show("d-ep1", mixed, {}, requestable, reads);
  await wait(() => posts("d-ep1").length === 1, "auto request sent once");
  const post = posts("d-ep1")[0];
  assert.deepEqual(JSON.parse(post.body), { kind: "explain", sourceId: "source-1", decisionId: "d-ep1" });
  assert.match(new Headers(post.headers).get("Idempotency-Key"), /^[0-9a-f-]{36}$/, "a new idempotency key per request");
  assert.equal(paragraphOf(app), code, "code paragraph while waiting");
  await wait(() => polishedReads("d-ep1") >= 1, "polished read after the request");
  // 다른 판정(B)의 상세 쿼리: 무효화돼 있던 상태가 A의 패치로 바뀌지 않아야 한다(신선도·무효화 표시 그대로)
  const detailQueryA = app.client.getQueryCache().findAll({ queryKey: ["analysis-decision"] }).find((q) => q.state.data?.id === "d-ep1");
  const keyB = [...detailQueryA.queryKey.slice(0, -1), "d-other"];
  app.client.setQueryData(keyB, summaryDetail("d-other", mixed));
  await act(async () => app.client.invalidateQueries({ queryKey: keyB, refetchType: "none" }));
  const stateB = () => { const q = app.client.getQueryCache().find({ queryKey: keyB, exact: true }); return [q.state.isInvalidated, q.state.dataUpdatedAt]; };
  const beforeB = stateB();
  assert.equal(beforeB[0], true, "B starts invalidated");
  const pollQuery = app.client.getQueryCache().findAll({ queryKey: ["analysis-decision-polished"] })[0];
  const interval = (query) => { const value = query.observers[0].options.refetchInterval; return typeof value === "function" ? value(query) : value; };
  assert.equal(interval(pollQuery), 5000, "5 s polling while pending");
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-decision-polished"] }));
  await wait(() => paragraphOf(app) === polishedText, "paragraph replaced by the polished one");
  assert.equal(interval(pollQuery), false, "polling stops when done");
  const cachedDetail = app.client.getQueryCache().findAll({ queryKey: ["analysis-decision"] }).map((q) => q.state.data).find((d) => d?.id === "d-ep1");
  assert.deepEqual([cachedDetail.polishState, cachedDetail.polished?.text, cachedDetail.polishRequestable], ["done", polishedText, false],
    "detail cache updated: re-entry shows the polished paragraph at once and sends no request");
  assert.deepEqual(stateB(), beforeB, "patching decision A leaves decision B's query (isInvalidated, dataUpdatedAt) untouched");
  assert.equal(blockText(cardOf(app)[0], "line"), `${mixed.summaryLine.prefix}${mixed.summaryLine.text}`, "other blocks unchanged");
  assert.deepEqual(cardOf(app)[0].findAll((node) => node.type === "li").map((li) => text(li.children)), mixed.checks.map((c) => c.text));
  await act(async () => app.client.invalidateQueries({ queryKey: ["analysis-decision"] }));
  await tick(30);
  assert.equal(posts("d-ep1").length, 1, "no second request on refetch");
  await app.cleanup();

  // 이미 done(llm)인 판정: 처음부터 다듬은 문단, 요청·읽기 없음. done(code)는 코드 문단
  requested.length = 0;
  const done = await show("d-ep2", mixed, {}, { polishState: "done", polished: { text: polishedText, source: "llm" }, polishRequestable: false });
  assert.equal(paragraphOf(done), polishedText);
  await tick(30);
  assert.deepEqual([posts("d-ep2").length, polishedReads("d-ep2")], [0, 0]);
  await done.cleanup();
  const codeDone = await show("d-ep3", mixed, {}, { polishState: "done", polished: { text: code, source: "code" } });
  assert.equal(paragraphOf(codeDone), code);
  await codeDone.cleanup();

  // 이미 pending: 요청하지 않고 읽기만. 실패로 끝남(none)이면 코드 문단 그대로, 읽기 멈춤
  requested.length = 0;
  const pending = await show("d-ep4", mixed, {}, { polishState: "pending" }, [{ polishState: "none", polished: null }]);
  await wait(() => polishedReads("d-ep4") >= 1, "pending read");
  await tick(30);
  assert.equal(posts("d-ep4").length, 0, "pending: no request");
  assert.equal(paragraphOf(pending), code);
  assert.ok(!/실패|대기|요청 중|generation_|다듬/.test(text(cardOf(pending)[0].children)), "no status or error text");
  await pending.cleanup();

  // 요청 실패(503)·읽기 실패는 조용히: 코드 문단 그대로, 다시 요청하지 않음
  requested.length = 0;
  routes = summaryRoutes("d-ep5", summaryDetail("d-ep5", mixed, {}, requestable));
  routes["POST /api/jobs"] = () => json({ code: "generation_service_unavailable" }, 503);
  const refused = await mount("/analysis/d/d-ep5");
  await wait(() => posts("d-ep5").length === 1, "request attempted");
  await tick(50);
  assert.deepEqual([posts("d-ep5").length, polishedReads("d-ep5")], [1, 0], "no retry, no polling after a refused request");
  assert.equal(paragraphOf(refused), code);
  assert.ok(!text(cardOf(refused)[0].children).includes("generation_service_unavailable"), "no error code shown");
  await refused.cleanup();

  // 요청하지 않는 판정: 정상·이상 아님, 시연 판정, 최종 전(interim), 서버가 요청 불가로 알림
  requested.length = 0;
  const cases = [
    ["d-ep6", summaryOf("normal_complete"), { signal: "no", riskStatus: "not_applicable" }],
    ["d-ep7", mixed, { evidence: { ...item("x").evidence, demo: { historicalReplay: true, runKey: "demo-1", scope: "demo" } } }],
    ["d-ep8", mixed, { kind: "interim", stage: "provisional", decisionId: null, jobId: "j-ep8" }],
  ];
  for (const [id, summary, overrides] of cases) {
    const view = await show(id, summary, overrides, requestable);
    await tick(30);
    assert.deepEqual([posts(id).length, polishedReads(id)], [0, 0], `${id}: no request`);
    await view.cleanup();
  }
}

assert.equal(requested.some((r) => r.path.startsWith("/api/anomaly/events/")), false, "removed anomaly detail/feedback API is never called");
assert.ok(requested.every((r) => r.path.startsWith("/api/")), "only same-origin API paths");
console.log("analysis page QA passed: default risk view, result/risk/workflow sidebar and view switch, admin-only outcome sub items under 완료 and basis select (URL + API), unread dot, risk cell, row links, search URL, SSE cursor with interim events "
  + "and single refetch, error/empty states, detail/timeline/evidence, process flow tree, read mark, workflow transitions and 409, admin 로그 분류하기 dialog (save then change, save failure keeps state, change failure keeps dialog, no double save, no close while pending), records in the timeline, list cause line, "
  + "v4 method tables, evidence summary card first (blocks, tables, group rows, names kept whole), automatic polish request once (pending polling, polished paragraph swap, silent failure, no request for normal/demo/interim/not requestable), protocol alert explanation (admin + protocolReview only, alert target, result/failure), 404, job→decision redirect, legacy address");
