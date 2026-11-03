import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.React = React;
await import("react-hot-toast");
const storage = new Map();
let storageBroken = false;
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: {
    getItem: (key) => {
      if (storageBroken) throw new Error("blocked");
      return storage.get(key) ?? null;
    },
    setItem: (key, value) => {
      if (storageBroken) throw new Error("blocked");
      storage.set(key, value);
    },
    removeItem: (key) => storage.delete(key),
  },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
};
// Radix RadioGroup·Recharts 효과용 최소 대역(포털 body 없음).
globalThis.HTMLFormElement = class {};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const layout = await import("../src/features/operations/data/widgetLayout.ts");
const reads = await import("../src/features/operations/data/operationsReads.ts");
const { parseOperationsOverview, operationsReadUnavailable } = await import("../src/features/operations/data/operations.ts");
const { percentText, durationText } = await import("../src/features/operations/format.ts");
const { HttpError } = await import("../src/services/api/client.ts");

// 1) 위젯 격자 배치 규칙(x, y, w, h): 기본 배치·겹침 해소·압축·추가 위치·키보드 이동·크기 제한.
const { layoutReducer, defaultWidgetLayout, availableWidgets, sanitizeLayout, compactLayout, collides, readingOrder, parseStoredLayout, sameLayout, widgetSizes, GRID_COLUMNS } = layout;
const noOverlap = (items) => items.every((a) => items.every((b) => !collides(a, b)));
const inBounds = (items) => items.every((item) => item.x >= 0 && item.x + item.w <= GRID_COLUMNS && item.y >= 0);
const at = (items, id) => items.find((item) => item.i === id);
assert.deepEqual(readingOrder(defaultWidgetLayout), ["flow", "recent", "trend", "active", "queue", "collect"]);
assert.deepEqual(availableWidgets(defaultWidgetLayout), ["storage"], "storage is only in the add catalog by default");
assert(noOverlap(defaultWidgetLayout) && inBounds(defaultWidgetLayout));
// 겹친 배치: 우선 위젯은 자리 유지, 나머지는 아래로 밀린 뒤 위로 붙는다.
const overlapped = compactLayout([{ i: "flow", x: 0, y: 0, w: 2, h: 3 }, { i: "recent", x: 1, y: 1, w: 2, h: 3 }, { i: "trend", x: 4, y: 5, w: 2, h: 3 }], "recent");
assert(noOverlap(overlapped));
assert.deepEqual([at(overlapped, "recent").x, at(overlapped, "recent").y], [1, 0], "priority widget keeps its column and floats up");
assert.equal(at(overlapped, "flow").y, 3, "displaced widget is pushed below");
assert.equal(at(overlapped, "trend").y, 0, "gaps are compacted upward");
// 크기 제한: 최소·최대·열 범위.
assert.deepEqual(sanitizeLayout([{ i: "trend", x: 5, y: -2, w: 1, h: 1 }]), [{ i: "trend", x: 4, y: 0, w: 2, h: 3 }]);
assert.deepEqual(sanitizeLayout([{ i: "flow", x: 0, y: 0, w: 9, h: 99 }])[0], { i: "flow", x: 0, y: 0, w: 6, h: 6 });
assert.deepEqual(sanitizeLayout([{ i: "bogus", x: 0, y: 0, w: 2, h: 2 }, { i: "flow", x: "0", y: 0, w: 2, h: 3 }, { i: "queue", x: 0, y: 0, w: 2, h: 3 }, { i: "queue", x: 2, y: 0, w: 2, h: 3 }]),
  [{ i: "queue", x: 0, y: 0, w: 2, h: 3 }], "unknown, malformed and duplicate entries are dropped");
assert.equal(sanitizeLayout({ flow: true }), null);
// 제거·추가: 빈 칸 채우기, 추가는 위에서부터 첫 빈 칸 또는 놓은 칸, 중복 없음.
const removed = layoutReducer(defaultWidgetLayout, { type: "remove", id: "trend" });
assert.deepEqual(availableWidgets(removed), ["trend", "storage"]);
const storageAdded = layoutReducer(removed, { type: "add", id: "storage" });
assert.deepEqual([at(removed, "collect").x, at(removed, "collect").y], [4, 0], "removal compacts the column upward");
assert.deepEqual([at(storageAdded, "storage").x, at(storageAdded, "storage").y], [4, 3], "added widget takes the first free slot");
assert.deepEqual([at(storageAdded, "storage").w, at(storageAdded, "storage").h], [widgetSizes.storage.w, widgetSizes.storage.h]);
const dropped = layoutReducer(storageAdded, { type: "add", id: "trend", at: { x: 0, y: 0 } });
assert.deepEqual([at(dropped, "trend").x, at(dropped, "trend").y], [0, 0], "drop position from the catalog is kept");
assert(noOverlap(dropped) && inBounds(dropped), "existing widgets reflow around the dropped one");
assert.deepEqual(availableWidgets(dropped), [], "every widget placed → empty catalog");
assert.equal(layoutReducer(dropped, { type: "add", id: "trend" }), dropped, "no duplicates");
// 키보드 대안: 읽는 순서의 이웃과 자리 바꾸기, 양 끝은 변화 없음.
const moved = layoutReducer(defaultWidgetLayout, { type: "move", id: "recent", direction: -1 });
assert.deepEqual(readingOrder(moved).slice(0, 2), ["recent", "flow"]);
assert.equal(layoutReducer(defaultWidgetLayout, { type: "move", id: "flow", direction: -1 }), defaultWidgetLayout, "first cannot move forward");
assert.equal(layoutReducer(defaultWidgetLayout, { type: "move", id: "collect", direction: 1 }), defaultWidgetLayout, "last cannot move back");
// 격자 라이브러리 결과(change): 검증만, 같은 배치면 같은 배열(편집 초안 갱신 반복 방지).
assert.equal(layoutReducer(defaultWidgetLayout, { type: "change", layout: defaultWidgetLayout.map((item) => ({ ...item, moved: false, minW: 2 })) }), defaultWidgetLayout);
const resized = layoutReducer(defaultWidgetLayout, { type: "change", layout: defaultWidgetLayout.map((item) => (item.i === "collect" ? { ...item, h: 5 } : item)) });
assert.deepEqual([at(resized, "collect").w, at(resized, "collect").h], [2, 5]);
assert(sameLayout(layoutReducer(resized, { type: "reset" }), defaultWidgetLayout));
// 끌기 결과(change)는 라이브러리 배열 순서와 관계없이 읽는 순서로 정렬: 화면 순서 = DOM·Tab 순서(WCAG 2.4.3).
const draggedToFront = layoutReducer(defaultWidgetLayout, { type: "change", layout: [
  { i: "flow", x: 0, y: 3, w: 2, h: 3 }, { i: "recent", x: 2, y: 0, w: 2, h: 3 }, { i: "trend", x: 4, y: 0, w: 2, h: 3 },
  { i: "active", x: 4, y: 3, w: 2, h: 3 }, { i: "queue", x: 0, y: 0, w: 2, h: 3 }, { i: "collect", x: 2, y: 3, w: 2, h: 3 } ] });
assert.deepEqual(draggedToFront.map((item) => item.i), ["queue", "recent", "trend", "flow", "collect", "active"]);
assert.deepEqual(draggedToFront.map((item) => item.i), readingOrder(draggedToFront), "array order equals reading order");
assert.deepEqual(readingOrder(layoutReducer(draggedToFront, { type: "move", id: "flow", direction: -1 })).slice(2, 4), ["flow", "trend"], "keyboard move follows the visual order");
const reordered = layoutReducer(draggedToFront, { type: "change", layout: [...draggedToFront].reverse() });
assert.equal(reordered, draggedToFront, "same positions in another array order is not a change");
// 저장 형식: v2 위치 배열, v1(순서 배열)은 그 순서로 기본 크기 재배치, 그 외는 기본 배치(null).
assert.deepEqual(readingOrder(parseStoredLayout(["queue", "flow", "bogus", "queue"])), ["queue", "flow"], "v1 order-only format is migrated");
assert(noOverlap(parseStoredLayout(["queue", "flow", "trend", "recent"])));
assert.equal(parseStoredLayout(["bogus"]), null);
assert.equal(parseStoredLayout({ items: [] }), null, "unversioned object falls back to default");
assert.equal(parseStoredLayout("flow"), null);
assert(noOverlap(parseStoredLayout({ version: 2, items: [{ i: "flow", x: 0, y: 0, w: 2, h: 3 }, { i: "recent", x: 0, y: 0, w: 2, h: 3 }] })), "stored overlaps are resolved");

// 2) 계정별 브라우저 저장소(저장소 차단 시 예외 없이 실패 보고).
const store = layout.browserWidgetLayoutStore;
assert.equal(store.load("a"), null);
assert.equal(store.save("a", resized), true);
assert(sameLayout(store.load("a"), resized));
assert.equal(JSON.parse(storage.get(layout.widgetLayoutKey("a"))).version, 2);
assert.equal(store.load("b"), null, "layouts are per account");
storage.set(layout.widgetLayoutKey("c"), "not json");
assert.equal(store.load("c"), null, "broken storage value falls back to default");
storage.set(layout.widgetLayoutKey("d"), JSON.stringify(["collect", "flow"]));
assert.deepEqual(readingOrder(store.load("d")), ["collect", "flow"], "previous order-only saves are migrated");
storageBroken = true;
assert.equal(store.save("a", defaultWidgetLayout), false, "blocked storage reports failure instead of success");
assert.equal(store.load("a"), null);
storageBroken = false;
storage.clear();

// 3) 응답 파서: 명시 필드만 복사, 잘못된 값은 null, 필수 누락은 실패.
const overview = parseOperationsOverview({ observedAt: "2026-11-03T01:00:00.000Z", maxAgeMs: 60000, stages: {},
  activeConfiguration: { configVersionId: "cfg", sourceId: "src", ruleCount: 2, ruleSeverityCounts: { warning: 1, critical: -1, secret: 3 },
    models: [{ modelKey: "iforest", adapterName: "sklearn-iforest", path: "/models/x" }, { bad: true }], token: "x" } });
assert.deepEqual(overview.activeConfiguration, { configVersionId: "cfg", sourceId: "src", ruleCount: 2, ruleSeverityCounts: { warning: 1 },
  models: [{ modelKey: "iforest", adapterName: "sklearn-iforest" }] });
assert.equal(parseOperationsOverview({ observedAt: "2026-11-03T01:00:00Z", maxAgeMs: 1, activeConfiguration: null }).activeConfiguration, null);
assert.equal("activeConfiguration" in parseOperationsOverview({ observedAt: "2026-11-03T01:00:00Z", maxAgeMs: 1 }), false);

const collectionBody = {
  observedAt: "2026-11-03T01:00:00.000Z",
  source: { id: "src", datasetKey: "k", displayName: "d", ingestionMethod: "switchable-input", secret: "x" },
  input: { mode: "csv", revision: 7, status: "running", lastError: "boom", csv: { replayKey: "s1", recordCount: 3000, rate: 10, repeat: 2, position: 1248, total: 6000 }, api: null },
  layers: [{ layer: "P", storedCount: 41, lastMinuteCount: 5, lastPersistedAt: "2026-11-03T00:59:59.000Z", revisionArrivalCount: 1 },
    { layer: "T", storedCount: -1, lastMinuteCount: 1.5, lastPersistedAt: "bad", revisionArrivalCount: 0 }],
  hourly: { items: [{ hourStart: "2026-11-03T00:00:00.000Z", count: 3 }, { hourStart: "x", count: 1 }] },
  history: [{ id: "r1", createdAt: "2026-11-03T00:30:00.000Z", mode: "csv", replayKey: "s1", status: "running", settings: { secret: 1 } }, { id: "r2", mode: "ftp" }],
};
const collection = reads.parseCollection(collectionBody);
assert.deepEqual(collection.input, { mode: "csv", revision: 7, status: "running", csv: { replayKey: "s1", recordCount: 3000, rate: 10, repeat: 2, position: 1248, total: 6000 }, pollSeconds: null });
assert.deepEqual(collection.layers.map((row) => row.layer), ["T", "P", "M", "B"], "every layer row present");
assert.deepEqual(collection.layers[0], { layer: "T", storedCount: null, lastMinuteCount: null, lastPersistedAt: null, revisionArrivalCount: 0 });
assert.equal(collection.layers[2].storedCount, null, "missing layer is unknown, not zero");
assert.equal(collection.hourly.length, 1);
assert.deepEqual(collection.history, [{ id: "r1", createdAt: "2026-11-03T00:30:00.000Z", mode: "csv", replayKey: "s1", status: "running" }]);
assert.equal(reads.parseCollection({ ...collectionBody, input: { mode: "ftp" } }).input, null);
assert.throws(() => reads.parseCollection({ source: { id: "src" } }));

const latency = reads.parseLatency({ observedAt: "2026-11-03T01:00:00Z", window: "1h", stages: [
  { id: "collector", samples: 3, p50Ms: 60, p95Ms: 114, recentFailures: 1, queuePending: null },
  { id: "detectionQueue", samples: 0, p50Ms: null, p95Ms: null, queuePending: 2, oldestWaitMs: 3000 },
], endToEnd: { p50Ms: null, reason: "source_clock_unverified" } });
assert.deepEqual(latency.stages.map((stage) => stage.id), ["collector", "persist", "detectionQueue", "detector", "judgmentStore"]);
assert.equal(latency.stages[1].p50Ms, null, "unreported stage is unmeasured");
assert.equal(latency.stages[2].queuePending, 2);
assert.equal(latency.endToEndP50Ms, null);
assert.throws(() => reads.parseLatency({ observedAt: "2026-11-03T01:00:00Z" }));

const fields = reads.parseSourceFields({ layer: "T", sampleSize: 10, sampleLimit: 1000, fields: [{ name: "retry_count", presentCount: 0, nullCount: 10, missingCount: 0, sample: "TX-1" }, { name: "bad" }],
  qualityFlags: [{ flag: "invalid_numeric:retry_count", count: 1 }] });
assert.deepEqual(fields.fields, [{ name: "retry_count", presentCount: 0, nullCount: 10, missingCount: 0 }]);
assert.throws(() => reads.parseSourceFields({ layer: "X", sampleSize: 1 }));
assert.deepEqual(reads.parseStorage({ measuredAt: "2026-11-03T01:00:00Z", databaseBytes: 1024, disk: null }), { measuredAt: "2026-11-03T01:00:00Z", databaseBytes: 1024, disk: null });
const trend = reads.parseTrend({ configVersionId: "cfg", timezone: "Asia/Seoul", items: [{ day: "2026-11-03", decisionCount: 30, anomalySignalRatio: 0.069, unavailableRatio: 1.5 }, { day: "11/03", decisionCount: 1 }] });
assert.deepEqual(trend.items, [{ day: "2026-11-03", decisionCount: 30, anomalySignalRatio: 0.069, unavailableRatio: null }]);
assert.equal(reads.parseTrend({ configVersionId: null, items: [] }).configVersionId, null);
assert.equal(percentText(0.06896), "6.9%");
assert.equal(percentText(0.5), "50%");
assert.equal(percentText(0), "0%");
// 대기 시간 표시: 작은 값은 ms·초, 긴 대기는 분·초/시간·분.
assert.equal(durationText(420), "420 ms");
assert.equal(durationText(1800), "1.8초");
assert.equal(durationText(59_999), "59.9초");
assert.equal(durationText(60_000), "1분");
assert.equal(durationText(1_109_500), "18분 29초");
assert.equal(durationText(3_600_000), "1시간");
assert.equal(durationText(3 * 3_600_000 + 5 * 60_000 + 30_000), "3시간 5분");
// 오류 제목: 원천 없음(404 source_not_found)은 미연결(404 경로 없음·503)과 구분.
assert.equal(reads.readErrorTitle(new HttpError("x", 404, "source_not_found"), "수집 현황"), "수집 현황 · 원천 없음");
assert.equal(reads.readErrorTitle(new HttpError("x", 404, "not_found"), "수집 현황"), "수집 현황 미연결");
assert.equal(reads.readErrorTitle(new HttpError("x", 503, "operations_storage_unavailable"), "수집 현황"), "수집 현황 미연결");
assert.equal(parseOperationsOverview({ observedAt: "2026-11-03T01:00:00Z", maxAgeMs: 1, queueWaitP95Ms: 420 }).queueWaitP95Ms, 420);
assert.equal("queueWaitP95Ms" in parseOperationsOverview({ observedAt: "2026-11-03T01:00:00Z", maxAgeMs: 1, queueWaitP95Ms: -1 }), false);

// 4) 조회 함수: 경로·쿼리·AbortSignal, 404/503은 미연결(성공 대체 없음).
let token = "local-token";
window.localStorage.setItem("token", token);
const seen = [];
let mode = "ok";
let bodies = {};
globalThis.fetch = async (url, options) => {
  const parsed = new URL(String(url), "http://local.test");
  seen.push(parsed.pathname + parsed.search);
  if (mode === "503") return new Response(JSON.stringify({ code: "operations_storage_unavailable" }), { status: 503 });
  const body = bodies[parsed.pathname];
  if (!body) return new Response(JSON.stringify({ code: "not_found" }), { status: 404 });
  return new Response(JSON.stringify(typeof body === "function" ? body(parsed, options) : body), { headers: { "Content-Type": "application/json" } });
};
bodies = { "/api/operations/collection": collectionBody };
const controller = new AbortController();
assert.equal((await reads.readCollection("src", controller.signal)).source.id, "src");
assert.equal(seen.at(-1), "/api/operations/collection?sourceId=src");
await assert.rejects(reads.readLatency("src"), operationsReadUnavailable);
mode = "503";
await assert.rejects(reads.readStorage(), operationsReadUnavailable);
await assert.rejects(reads.readTrend(), (error) => operationsReadUnavailable(error) && seen.at(-1) === "/api/operations/decision-trend?days=3");
mode = "ok";

// 5) 화면: 일반 사용자/관리자 렌더(운영 설정 링크·입력 모드 변경은 관리자만), 위젯 편집·저장·취소.
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { MemoryRouter } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { OperationsPage } = await import("../src/features/operations/OperationsPage.tsx");
const { RadioGroup } = await import("../src/components/ui/radio-group.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");

const overviewBody = { observedAt: new Date().toISOString(), maxAgeMs: 60000,
  stages: { collector: { lastProcessedAt: "2026-11-03T00:59:00.000Z" }, observationQueue: { pendingCount: 2, oldestWaitMs: 3000 }, detector: { pendingCount: 0 }, judgmentStore: {} },
  activeConfiguration: { configVersionId: "cfg", sourceId: "src", ruleCount: 2, ruleSeverityCounts: {}, models: [{ modelKey: "iforest", adapterName: "sklearn-iforest" }] },
  activeConfigurationVersion: "cfg" };
bodies = {
  "/api/operations/overview": overviewBody,
  "/api/pipeline/sources": { items: [{ id: "src", datasetKey: "k", displayName: "d", createdAt: "2026-11-01T00:00:00Z" }] },
  "/api/pipeline/decisions": { items: [{ id: "decision-0001", sourceId: "src", processObservationId: "o", configVersionId: "cfg", status: "complete", ruleStatus: "succeeded", mlStatus: "succeeded", decisionCode: "R1", anomalyScore: "0.9", decidedAt: "2026-11-03T00:58:00.000Z", evidence: { anomalous: true } }] },
  "/api/operations/decision-trend": { configVersionId: "cfg", timezone: "Asia/Seoul", items: [{ day: "2026-11-03", decisionCount: 30, anomalySignalRatio: 0.069, unavailableRatio: 0.033 }] },
  "/api/operations/collection": collectionBody,
  "/api/operations/latency": { observedAt: "2026-11-03T01:00:00Z", window: "1h", stages: [{ id: "detectionQueue", samples: 0, p50Ms: null, p95Ms: null, queuePending: 2 }], endToEnd: { p50Ms: null } },
  "/api/operations/source-fields": { layer: "T", sampleSize: 0, sampleLimit: 1000, fields: [], qualityFlags: [] },
  "/api/operations/storage": { measuredAt: "2026-11-03T01:00:00Z", databaseBytes: 2048, disk: null },
  "/api/inputs": { enabled: true, sourceId: "src", storedCounts: {}, csvResume: null, replays: [], configurations: [],
    control: { mode: "paused", revision: 3, runId: null, configVersionId: null, status: null, settings: null, position: null, accepted: 0, duplicates: 0, unidentified: 0, lastError: null, workerHeartbeatAt: null } },
};
const tick = () => act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
const wait = async (check, label) => {
  // 부하가 큰 환경에서도 렌더를 기다리도록 상한만 늘린다(약 6초, 조건 충족 즉시 종료 — 단언은 그대로).
  for (let i = 0; i < 600 && !check(); i++) await tick();
  assert.ok(check(), label);
};
const textOf = (node) => (typeof node === "string" ? node : Array.isArray(node) ? node.map(textOf).join("") : node?.children ? textOf(node.children) : "");

const renderAs = async (role, entry) => {
  seen.length = 0;
  let session;
  const Probe = () => {
    session = useAuthSession();
    return null;
  };
  const cache = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(MemoryRouter, { initialEntries: [entry] },
        React.createElement(QueryClientProvider, { client: cache },
          React.createElement(AuthSessionProvider, null, React.createElement(Probe), React.createElement(OperationsPage)))),
    );
  });
  await act(async () => session.login({ token: `${role}-token`, user: { id: `${role}-1`, email: `${role}@example.test`, userType: role, createdAt: "2026-10-01T00:00:00Z" } }));
  const text = () => textOf(renderer.toJSON());
  return { renderer, text, cache, session };
};
const findButton = (renderer, label) =>
  renderer.root.findAll((node) => typeof node.type !== "string" && node.props?.onClick && (node.props["aria-label"] === label || flat(node.props.children) === label))[0];
const flat = (children) => (typeof children === "string" ? children : Array.isArray(children) ? children.map(flat).join("") : children?.props ? flat(children.props.children) : "");

// 일반 사용자: 개요 위젯 렌더, 운영 설정 링크 없음.
let view = await renderAs("user", "/operations");
await wait(() => view.text().includes("iforest(sklearn-iforest)") && view.text().includes("decision"), "user overview widgets render");
assert(view.text().includes("현재 운영: 룰 2개 · 머신러닝 모델 iforest(sklearn-iforest)"));
assert(!view.text().includes("운영 설정"), "user has no 운영 설정 link");
assert(view.text().includes("원천 발생 → 판정: 측정 불가"));
assert(view.text().includes("6.9%"));
assert(!seen.some((url) => url.startsWith("/api/operations/storage")), "storage widget is not placed → not fetched");

// 위젯 편집(좁은 화면·격자 측정 전 = 한 줄 배치, 키보드 버튼 경로): 제거 → 저장 / 다시 편집 → 위젯 추가 목록 → 추가 → 취소.
const { Dialog } = await import("../src/components/ui/dialog.tsx");
const dialogButton = (renderer, label) => {
  const walk = (node, found = []) => {
    if (Array.isArray(node)) node.forEach((child) => walk(child, found));
    else if (node && typeof node === "object" && node.props) { found.push(node); walk(node.props.children, found); }
    return found;
  };
  return walk(renderer.root.findByType(Dialog).props.children).find((el) => el.props?.["aria-label"] === label);
};
await wait(() => Boolean(findButton(view.renderer, "위젯 편집")), "lazy overview tab loaded");
await act(async () => findButton(view.renderer, "위젯 편집").props.onClick());
assert(useDraftStore.getState().drafts["/operations"]?.dirty, "editing registers a page draft (navigation guard)");
assert(findButton(view.renderer, "단계 흐름 뒤로"), "keyboard move buttons in edit mode");
await act(async () => findButton(view.renderer, "판정 추이 제거").props.onClick({ currentTarget: { closest: () => null } }));
await act(async () => findButton(view.renderer, "최근 판정 앞으로").props.onClick());
await act(async () => findButton(view.renderer, "배치 저장").props.onClick());
const savedValue = JSON.parse(storage.get(layout.widgetLayoutKey("user-1")));
assert.equal(savedValue.version, 2);
assert.deepEqual(readingOrder(savedValue.items), ["recent", "flow", "collect", "active", "queue"], "removal compacts, keyboard move swaps");
assert(noOverlap(savedValue.items) && inBounds(savedValue.items));
assert(view.text().includes("배치 저장됨 · 일반 사용자 계정 · 위젯 5개"));
assert.equal(useDraftStore.getState().drafts["/operations"], undefined, "saved edit clears the draft");
await act(async () => findButton(view.renderer, "위젯 편집").props.onClick());
assert.equal(view.renderer.root.findByType(Dialog).props.open, false);
await act(async () => findButton(view.renderer, "위젯 추가").props.onClick());
assert.equal(view.renderer.root.findByType(Dialog).props.open, true, "위젯 추가 opens the catalog");
assert(dialogButton(view.renderer, "판정 추이 추가") && dialogButton(view.renderer, "저장공간 추가"));
await act(async () => dialogButton(view.renderer, "저장공간 추가").props.onClick());
assert.equal(view.renderer.root.findByType(Dialog).props.open, false, "adding closes the catalog");
await wait(() => seen.some((url) => url.startsWith("/api/operations/storage")), "added storage widget fetches");
await act(async () => findButton(view.renderer, "편집 취소").props.onClick());
assert(view.text().includes("편집 취소 · 이전 배치 유지"));
assert(!view.text().includes("PostgreSQL"), "cancel restores the saved layout");
assert.equal(JSON.parse(storage.get(layout.widgetLayoutKey("user-1"))).items.length, 5);
await act(async () => view.renderer.unmount());

// 일반 사용자 수집 탭: 읽기 전용 카드, /api/inputs 요청·라디오 없음.
view = await renderAs("user", "/operations?tab=collect");
await wait(() => view.text().includes("원본별 저장 건수") && view.text().includes("CSV 재생(시뮬레이션)"), "user collect cards render");
assert.equal(view.renderer.root.findAllByType(RadioGroup).length, 0, "no input mode controls for users");
assert(!seen.some((url) => url.startsWith("/api/inputs")), "users never call the admin input API");
assert(view.text().includes("수집 안 함 · 판정 입력 아님"));
assert(view.text().includes("1,248 / 6,000행"));
assert(!view.text().includes("boom"), "input error text is not shown from the read model");
await act(async () => view.renderer.unmount());

// 관리자: 운영 설정 링크, 수집 탭 입력 모드 라디오(/api/inputs), 다른 계정 배치와 분리.
view = await renderAs("admin", "/operations");
await wait(() => view.text().includes("운영 설정"), "admin sees 운영 설정 link");
assert.equal(view.renderer.root.findAll((node) => node.props?.["data-widget"] === "trend").length, 1, "admin uses own (default) layout");
await act(async () => view.renderer.unmount());
view = await renderAs("admin", "/operations?tab=collect");
await wait(() => view.renderer.root.findAllByType(RadioGroup).length === 1, "admin gets input mode radios");
assert(seen.some((url) => url.startsWith("/api/inputs?sourceId=src")));
await act(async () => view.renderer.unmount());

console.log("operations overview QA passed: widget layout reducer, per-account storage with failure, strict parsers, unavailable reads, user read-only vs admin controls, edit save/cancel with draft");
