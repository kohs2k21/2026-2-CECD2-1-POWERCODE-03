import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 머신러닝 모델 화면 통합(로컬 가짜 fetch만, 외부 요청 없음): 버전 탭·커서·상세, 새 학습 3단계(기간 조회·그래프·제외 구간, 입력 변수, 모델·파라미터),
// 학습 요청 본문, 진행·실패 표시, 서버 거절 그대로 표시. 방법 카탈로그는 계약 파일 원본을 응답으로 쓴다.
register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
const windowListeners = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  sessionStorage: {
    getItem: (key) => storage.get(`s:${key}`) ?? null,
    setItem: (key, value) => storage.set(`s:${key}`, value),
    removeItem: (key) => storage.delete(`s:${key}`),
  },
  addEventListener: (type, handler) => windowListeners.set(type, handler),
  removeEventListener: (type) => windowListeners.delete(type),
  setTimeout,
  clearTimeout,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
globalThis.HTMLFormElement = class {};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
const period = await import("../src/features/detection/models/period.ts");
const draftModule = await import("../src/features/detection/models/trainDraft.ts");
const { Dialog } = await import("../src/components/ui/dialog.tsx");

const catalog = JSON.parse(await readFile(new URL("../../contracts/method-catalog.json", import.meta.url), "utf8"));
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const SOURCE = "11111111-1111-4111-8111-111111111111";
const SET_OK = "aaaaaaaa-1111-4111-8111-111111111111";
const SET_BAD = "bbbbbbbb-1111-4111-8111-111111111111";
const JOB = "cccccccc-1111-4111-8111-111111111111";
const V1 = "dddddddd-1111-4111-8111-111111111111";
const V2 = "eeeeeeee-1111-4111-8111-111111111111";
const V3 = "ffffffff-1111-4111-8111-111111111111";
const JOB2 = "c2c2c2c2-2222-4222-8222-222222222222";

const version = (id, overrides = {}) => ({
  id, sourceId: SOURCE, schemaVersion: 1, displayName: null, contentHash: "c".repeat(64), contentHashVersion: 1, createdAt: "2026-10-31T01:00:00Z", requestedMode: "ml_only",
  modelKey: "iforest", active: false, activeSince: null, createdFrom: { kind: "train", trainJobId: JOB, recommendationJobId: null }, executable: true,
  methods: [{ methodId: "ml:iforest", kind: "ml", enabled: true, status: "supported", executable: { process: true, window: true }, executableReason: null }],
  trainingSelection: { policyVersion: "train-success-only-v1", selectedCount: 47120, populationCount: 48210, trainingPeriod: { from: "2026-09-30T15:00:00Z", until: "2026-10-31T15:00:00Z", timeBasis: "persisted_at" }, excludedIntervalCount: 0 },
  modelSummary: { featureCount: 10, parameters: { n_estimators: 128, max_samples: 512, contamination: "auto", random_state: 42, thresholdPolicy: "train-internal-p99-v1" }, thresholdValue: "0.6585093417435648" },
  operationalEligibility: { eligible: true, reason: null }, applicationEligibility: { eligible: false, evaluationId: null, reason: "no_eligible_evaluation" }, latestEvaluation: null, latestComparison: null,
  ...overrides,
});
const active = version(V1, { active: true, activeSince: { revision: 7, at: "2026-11-01T02:00:00Z" }, latestEvaluation: { evaluationId: "e", applicability: "valid", completedAt: "2026-11-01T02:00:00Z" } });
const research = version(V2, {
  modelKey: "minibatch_kmeans", executable: false,
  methods: [{ methodId: "ml:minibatch_kmeans", kind: "ml", enabled: true, status: "research", executable: { process: false, window: true }, executableReason: "server_adapter_missing" }],
  trainingSelection: { trainingPeriod: null, excludedIntervalCount: 0 },
  modelSummary: { featureCount: null, parameters: null, thresholdValue: null },
});
const ruleOnly = version(V3, { modelKey: null, methods: [], executable: false, modelSummary: null });
const page = (items, nextCursor = null) => ({ items, pagination: { limit: 50, hasMore: Boolean(nextCursor), nextCursor } });
const detail = {
  ...active,
  methods: [{ ...active.methods[0], methodVersion: "process-iforest-v1", parameters: { n_estimators: 64, random_state: 7 }, artifacts: { modelArtifactId: "m-1", fitArtifactId: "f-1" } }],
  models: [{ modelKey: "iforest", featureSetId: SET_OK, modelArtifactId: "m-1", fitArtifactId: "f-1", adapterSpec: {}, thresholdSpec: { direction: "higher_is_anomaly", value: "0.58" },
    trainingSettings: { featureNames: ["error_count", "retry_count"], excludedIntervals: [{ from: "2026-10-03T00:00:00Z", until: "2026-10-03T03:00:00Z", note: "DB 장애" }] } }],
  configurationSpec: { schemaVersion: 1 },
};
const capabilities = (timeBasis = "persisted_at", excludedIntervals = false) => ({ models: [], methodCatalog: catalog, snapshot: { timeBasis, splitVersion: "execution-block-v1" }, training: { excludedIntervals } });
const featureSets = {
  scope: "global_registered_catalog", sourceId: SOURCE, hasMore: false,
  items: [{ id: SET_OK, contentHash: "h", featureNames: ["error_count", "retry_count"], supported: true, reason: null },
    { id: SET_BAD, contentHash: "h2", featureNames: ["prepared_value"], supported: false, reason: "prepared_dataset_path_required" }],
};
const job = (status, overrides = {}) => ({ id: JOB, kind: "train", status, inputSpec: {}, attemptCount: 0, maxAttempts: 3, nextRunAt: null, errorCode: null, resultSummary: null, ...overrides });

/** 요청 범위·단위대로 버킷을 만든다. 범위 안 하루마다 100건(하루 미만 버킷은 100건), 그중 S 90%. */
const volumeOf = (target) => {
  const params = Object.fromEntries(target.searchParams);
  const start = period.fromUtc(params.from), end = period.fromUtc(params.until);
  const buckets = [];
  const first = params.bucket === "day" ? Math.floor(start / 1440) * 1440 : params.bucket === "hour" ? Math.floor(start / 60) * 60 : params.bucket === "month" ? period.dateMinute(`${period.minuteDay(start).slice(0, 7)}-01`) : start;
  for (let minute = first; minute < end; minute = period.nextUnit(minute, params.bucket)) {
    const days = Math.max(1, Math.round((Math.min(period.nextUnit(minute, params.bucket), end) - Math.max(minute, start)) / 1440));
    buckets.push({ bucketStart: period.toUtc(minute).replace(".000Z", "Z"), processCount: days * 100, statusCounts: { S: days * 90, F: days * 10, P: 0, N: 0, other: 0 } });
  }
  return { ...params, totalCount: buckets.reduce((sum, item) => sum + item.processCount, 0), timeUnknownCount: 0, buckets };
};

const requested = [];
let routes = {};
let currentUser;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, search: target.search, method: options.method ?? "GET", body: options.body ? JSON.parse(options.body) : undefined, headers: options.headers });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  const handler = routes[`${options.method ?? "GET"} ${target.pathname}`];
  return handler ? handler(target, options) : json({ code: "unavailable" }, 503);
};
const baseRoutes = (overrides = {}) => ({
  "GET /api/configurations": (target) => json(page(target.searchParams.get("active") === "true" ? [active] : [active, research, ruleOnly])),
  "GET /api/jobs/capabilities": () => json(capabilities()),
  "GET /api/pipeline/sources": () => json({ items: [{ id: SOURCE, datasetKey: "ds", displayName: "출처", createdAt: "2026-10-01T00:00:00Z" }] }),
  "GET /api/pipeline/reports/volume": (target) => json(volumeOf(target)),
  "GET /api/jobs/feature-sets": () => json(featureSets),
  ...overrides,
});
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (path = "/detection/models", { keepDrafts = false, createNodeMock, userType = "admin", prepare } = {}) => {
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  if (!keepDrafts) useDraftStore.getState().reset();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  prepare?.(client);
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))), { createNodeMock: createNodeMock ?? (() => null) });
  });
  for (let i = 0; i < 20 && text(renderer.toJSON()).includes("로그인 상태를 확인하는 중"); i++) await tick();
  const shown = () => text(renderer.toJSON());
  const button = (label) => renderer.root.findAll((node) => node.type === "button" && text(node.children) === label)[0];
  const click = async (label) => {
    assert.notEqual(button(label)?.props.disabled, true, `${label} is enabled`);
    await act(async () => button(label).props.onClick());
  };
  const link = (label) => renderer.root.findAll((node) => node.type === "a" && text(node.children).includes(label))[0];
  const go = async (label) => act(async () => { await router.navigate(link(label).props.href); });
  const input = (id) => renderer.root.findAll((node) => node.type === "input" && node.props.id === id)[0];
  const set = async (id, value) => act(async () => input(id).props.onChange({ target: { value } }));
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, button, click, link, go, input, set, cleanup };
};
const search = (app) => app.router.state.location.search;
const calls = (method, path) => requested.filter((call) => call.method === method && call.path === path);
const queryPeriod = async (app, from = "2026-10-01", to = "2026-10-30") => {
  await wait(() => app.input("period-from"), "period inputs");
  await app.set("period-from", from);
  await app.set("period-to", to);
  await app.click("조회");
  await wait(() => app.renderer.root.findAll((node) => node.props["data-testid"] === "period-chart").length === 1, "chart");
};
const chart = (app) => app.renderer.root.findAll((node) => node.props["data-testid"] === "period-chart" && node.type === "div")[0];
const bars = (app) => app.renderer.root.findAll((node) => node.type === "rect" && node.props["data-bar"] !== undefined);
const excludedRows = (app) => app.renderer.root.findAll((node) => node.type === "li" && node.props["data-excluded"]).map((node) => node.props["data-excluded"]);
const bandsOf = (app) => app.renderer.root.findAll((node) => node.type === "rect" && node.props["data-band"]).length;
const DRAFT = "/detection/models";
const stored = () => useDraftStore.getState().drafts[DRAFT];
const accepted = (id = JOB) => json({ id, status: "queued", kind: "train", inputSpec: {} }, 201);
const elements = (node) => (Array.isArray(node) ? node.flatMap(elements) : React.isValidElement(node) ? [node, ...elements(node.props.children)] : []);
/** 확인창 본문은 포털이라 시험 렌더러에 없다. 열린 Dialog의 실제 버튼을 찾아 누른다. */
const openDialog = (app) => app.renderer.root.findAllByType(Dialog).find((node) => node.props.open);
const pressDialog = async (app, label) => {
  const target = elements(openDialog(app).props.children).find((node) => node.props.onClick && node.props.children === label);
  assert.ok(target, label);
  await act(async () => target.props.onClick());
};
const leaveViaDialog = async (app, label) => {
  await act(async () => { await app.router.navigate("/detection/evaluations"); });
  await wait(() => openDialog(app), "confirmation");
  await pressDialog(app, label);
  await wait(() => app.router.state.location.pathname === "/detection/evaluations", "left");
};
/** POST를 잡아 두고 키를 모은다. */
const heldPost = (extra = {}) => {
  const held = { keys: [], release: undefined, fail: undefined };
  routes = baseRoutes({
    "POST /api/jobs": (_target, options) => {
      held.keys.push(new Headers(options.headers).get("Idempotency-Key"));
      return new Promise((resolve) => {
        held.release = (id = JOB) => resolve(accepted(id));
        held.fail = () => resolve(json({ code: "job_storage_unavailable" }, 503));
      });
    },
    [`GET /api/jobs/${JOB}`]: () => json(job("running")),
    [`GET /api/jobs/${JOB2}`]: () => json(job("running", { id: JOB2 })),
    ...extra,
  });
  return held;
};
const startPost = async (app, held) => {
  held.release = undefined;
  await act(async () => { void app.button("학습 요청").props.onClick(); });
  await wait(() => held.release, "post in flight");
};
const settle = (fn) => act(async () => { fn(); await new Promise((resolve) => setTimeout(resolve, 30)); });
const treeInput = (app) => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0];
const setTrees = (app, value) => act(async () => treeInput(app).props.onChange({ target: { value } }));
const svgMock = (element) => (element.type === "svg" ? { getBoundingClientRect: () => ({ left: 0, width: 720 }) } : null);
/** 차트 x 좌표(뷰박스 = 화면 폭 720). */
const xOfDay = (date, start = "2026-10-01", days = 30) => 52 + ((period.dateMinute(date) - period.dateMinute(start) + 720) / (days * 1440)) * 660;
const pointer = (type, clientX) => act(async () => chart(globalThis.current)?.props[type]({ button: 0, clientX, pointerId: 1, preventDefault() {}, currentTarget: {} }));
const toStep3 = async (app) => {
  await queryPeriod(app);
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("error_count, retry_count"), "feature sets");
  await act(async () => app.renderer.root.findByProps({ "aria-label": "입력 변수 세트", "data-slot": "radio-group" }).props.onValueChange(SET_OK));
  await app.go("다음 · 모델·파라미터");
  await wait(() => app.shown().includes("준비 검사"), "step 3");
};

// 1) 순수 시각 계산: 한국 시각 경계, 단위 전환, 병합, 정밀 입력, 확대·이동
{
  assert.equal(period.toUtc(period.dateMinute("2026-10-01")), "2026-09-30T15:00:00.000Z");
  assert.equal(period.fromUtc("2026-09-30T15:00:00Z"), period.dateMinute("2026-10-01"));
  assert.equal(period.fullText("2026-09-30T15:30:00Z"), "2026-10-01 00:30");
  const DAY = period.DAY_MINUTES;
  assert.deepEqual([101 * DAY, 100 * DAY, 3 * DAY + 1, 3 * DAY, 241, 240, 30].map(period.unitFor), ["month", "day", "day", "hour", "hour", "minute", "minute"]);
  assert.deepEqual(period.mergeRanges([[10, 20], [20, 30], [40, 50], [45, 60], [1, 2]]), [[1, 2], [10, 30], [40, 60]]);
  assert.deepEqual(period.clipRanges([[0, 100], [200, 300]], [50, 250]), [[50, 100], [200, 250]]);
  assert.deepEqual(period.clipRanges([[0, 10]], [50, 250]), []);
  assert.deepEqual(period.queryRange("", "2026-10-01"), { error: "조회 시작·종료 날짜를 입력해 주세요." });
  assert.deepEqual(period.queryRange("2026-10-02", "2026-10-01"), { error: "시작 날짜가 종료 날짜보다 늦습니다." });
  assert.deepEqual(period.queryRange("2025-01-01", "2026-01-02"), { error: "조회 범위는 최대 366일입니다." });
  assert.ok("range" in period.queryRange("2025-01-01", "2026-01-01"), "366 days fit the API limit");
  const query = [period.dateMinute("2026-10-01"), period.dateMinute("2026-10-31")];
  assert.deepEqual(period.preciseRange({ fromDate: "2026-10-03", fromTime: "", toDate: "2026-10-03", toTime: "" }, query), { range: [period.dateMinute("2026-10-03"), period.dateMinute("2026-10-04")] });
  assert.deepEqual(period.preciseRange({ fromDate: "2026-10-03", fromTime: "02:00", toDate: "2026-10-03", toTime: "06:30" }, query), { range: [period.dateMinute("2026-10-03") + 120, period.dateMinute("2026-10-03") + 390] });
  assert.deepEqual(period.preciseRange({ fromDate: "2026-10-03", fromTime: "06:00", toDate: "2026-10-03", toTime: "02:00" }, query), { error: "끝이 시작보다 늦어야 합니다." });
  assert.match(period.preciseRange({ fromDate: "2026-09-30", fromTime: "", toDate: "2026-10-03", toTime: "" }, query).error, /조회 기간\(2026-10-01 ~ 2026-10-30\)/);
  assert.deepEqual(period.preciseOf([period.dateMinute("2026-10-03") + 120, period.dateMinute("2026-10-05")]), { fromDate: "2026-10-03", fromTime: "02:00", toDate: "2026-10-04", toTime: "" });
  const zoomed = period.zoomView(query, query, 0.7, query[0]);
  assert.deepEqual(zoomed, [query[0], query[0] + Math.round((query[1] - query[0]) * 0.7)]);
  assert.deepEqual(period.zoomView([0, 30], [0, 10000], 0.1, 15), [0, 30], "30 minutes is the smallest span");
  assert.deepEqual(period.zoomView(zoomed, query, 100, query[0]), query, "never beyond the queried range");
  assert.deepEqual(period.panView([100, 200], [0, 210], 1), [110, 210]);
  assert.deepEqual(period.panView([100, 200], [90, 300], -1), [90, 190]);
  assert.equal(period.rangeText(period.dateMinute("2026-10-03"), period.dateMinute("2026-10-04")), "10/3");
  assert.equal(period.durationText(1500), "1일 1시간");
  const report = { from: "2026-10-01T00:15:00Z", until: "2026-10-01T02:30:00Z", bucket: "hour", buckets: [{ bucketStart: "2026-10-01T00:00:00Z", processCount: 3 }, { bucketStart: "2026-10-01T01:00:00Z", processCount: 4 }, { bucketStart: "2026-10-01T02:00:00Z", processCount: 5 }] };
  const clipped = period.barsOf(report);
  assert.equal(clipped[0].s, period.fromUtc("2026-10-01T00:15:00Z"), "edge bars are clipped to the requested range");
  assert.equal(clipped[2].e, period.fromUtc("2026-10-01T02:30:00Z"));
  assert.equal(period.barAt(clipped, clipped[1].s + 1).n, 4);
  assert.equal(period.barAt(clipped, 0).n, 3, "before the first bar clamps to it");
  assert.equal(period.barAt([], 0), null);
}

// 2) 방법 카탈로그 값 대조(계약 파일): 모든 파라미터 표시, 기본값 통과, 범위·정수·고정 값, 요청 파라미터
{
  const { parameterRows, parameterIssues, parameterPayload, selectedMethod, modelKeyOf } = draftModule;
  for (const method of catalog.methods) {
    const rows = parameterRows(method);
    assert.deepEqual(rows.map((row) => row.key), Object.keys(method.parameterSchema.properties), method.methodId);
    assert.deepEqual(parameterIssues(method, undefined), [], `${method.methodId} defaults pass`);
    for (const row of rows) {
      assert.notEqual(row.label, row.key === "contamination" ? "" : row.key, `${row.key} has a screen name`);
      assert.ok(row.meaning, `${row.key} has a meaning`);
    }
    assert.ok(draftModule.METHOD_DESCRIPTIONS[method.methodId], `${method.methodId} has a description`);
  }
  const iforest = catalog.methods.find((method) => method.methodId === "ml:iforest");
  const byKey = Object.fromEntries(parameterRows(iforest).map((row) => [row.key, row]));
  assert.equal(byKey.n_estimators.rangeLabel, "16 ~ 1024");
  assert.equal(byKey.random_state.rangeLabel, "≥ 0");
  assert.equal(byKey.contamination.rangeLabel, "고정");
  assert.equal(byKey.contamination.defaultValue, "auto");
  assert.equal(byKey.thresholdPolicy.fixed, true);
  for (const value of ["15", "1025", "1.5", "", "abc", "1e3"]) assert.equal(parameterIssues(iforest, { n_estimators: value }).length, 1, `n_estimators=${value}`);
  assert.deepEqual(parameterIssues(iforest, { n_estimators: "16", max_samples: "4096" }), []);
  assert.deepEqual(parameterPayload(iforest, { n_estimators: "64" }), { n_estimators: 64, max_samples: 512, random_state: 42 }, "fixed values are not sent");
  const kmeans = catalog.methods.find((method) => method.methodId === "ml:minibatch_kmeans");
  assert.deepEqual(parameterIssues(kmeans, { quantile: "0.5" }), [], "number type allows decimals");
  assert.equal(parameterIssues(kmeans, { k: "1.5" }).length, 1);
  assert.equal(modelKeyOf(selectedMethod(catalog.methods, "eif")), "iforest", "a method that cannot run is never the selection");
  assert.equal(selectedMethod(catalog.methods.filter((method) => !method.executable.process), null), null);

  // 준비 검사: 입력 변수 상한·지원 여부·제외 구간
  const { freshDraft, readinessIssues, jobBlocksRequest, draftFingerprint } = draftModule;
  const ready = { ...freshDraft(), period: { ...freshDraft().period, query: [0, 1440] }, featureSetId: "s" };
  const set = (count, extra = {}) => ({ id: "s", contentHash: "h", featureNames: Array.from({ length: count }, (_, i) => `f${i}`), supported: true, reason: null, ...extra });
  const issues = (draft, featureSet, exclusionSupported = false) => readinessIssues({ draft, method: iforest, featureSet, exclusionSupported });
  assert.deepEqual(issues(ready, set(iforest.inputRequirements.maxFeatures)), [], "the catalog limit itself passes");
  assert.deepEqual(issues(ready, set(iforest.inputRequirements.maxFeatures + 1)), ["입력 변수 33개 · 최대 32개"]);
  assert.deepEqual(issues(ready, set(2, { supported: false, reason: "invalid_feature_set" })), ["입력 변수: invalid_feature_set"]);
  const excluded = { ...ready, period: { ...ready.period, excluded: [[0, 60]] } };
  assert.deepEqual(issues(excluded, set(2)), ["제외 구간: config_schema_not_supported_yet"]);
  assert.deepEqual(issues(excluded, set(2), true), [], "the block lifts when the server says so");

  // 작업 상태를 모를 때 막음
  const { HttpError } = await import("../src/services/api/client.ts");
  const blocks = (state) => jobBlocksRequest("j", { isError: false, error: null, ...state });
  assert.equal(jobBlocksRequest(null, { isError: false, error: null }), false);
  assert.equal(blocks({}), true, "not read yet");
  assert.equal(blocks({ data: { status: "queued" } }), true);
  assert.equal(blocks({ data: { status: "running" } }), true);
  assert.equal(blocks({ data: { status: "failed" } }), false);
  assert.equal(blocks({ data: { status: "succeeded" } }), false);
  assert.equal(blocks({ isError: true, error: new HttpError("x", 503, "job_storage_unavailable") }), true, "unknown state blocks");
  assert.equal(blocks({ isError: true, error: new TypeError("network") }), true);
  assert.equal(blocks({ isError: true, error: new HttpError("x", 404, "job_not_found") }), false, "a missing job does not block forever");
  assert.equal(blocks({ isError: true, error: new HttpError("x", 400, "invalid_job_id") }), false);
  assert.equal(blocks({ data: { status: "running" }, isError: true, error: new HttpError("x", 503) }), true, "a failed refresh keeps the last known state");
  const moved = { ...ready, period: { ...ready.period, view: [10, 20], pending: [1, 2] } };
  assert.equal(draftFingerprint(moved), draftFingerprint(ready), "zooming is not an edit");
  assert.notEqual(draftFingerprint({ ...ready, period: { ...ready.period, fromDate: "2026-10-02" } }), draftFingerprint(ready), "a typed date is an edit");
  assert.notEqual(draftFingerprint({ ...ready, modelKey: "x" }), draftFingerprint(ready));
}

// 3) 버전 목록: 기본 탭 현재 운영 버전, 룰 전용 행 제외, 머리글 '새 학습' 버튼 없음
{
  routes = baseRoutes();
  const app = await mount();
  await wait(() => app.shown().includes("Isolation Forest"), "active versions");
  const shown = app.shown();
  assert.ok(shown.includes("현재 운영 버전") && shown.includes("전체 버전"));
  assert.ok(shown.includes(V1.slice(0, 8)), "the active version is listed");
  assert.ok(!shown.includes(V2.slice(0, 8)), "versions out of operation stay on the other tab");
  assert.ok(shown.includes("운영 중") && shown.includes("지원 · supported") && shown.includes("평가 완료"));
  for (const title of ["버전", "모델", "상태", "운영", "입력 변수", "학습 파라미터", "판정선", "최근 평가", "생성"]) {
    assert.ok(app.renderer.root.findAll((node) => node.type === "th").some((node) => text(node.children) === title), `${title} column`);
  }
  assert.ok(shown.includes("10개"), "input variable count");
  assert.ok(shown.includes("n_estimators=128, max_samples=512, random_state=42"), "adjustable parameters only");
  assert.ok(!shown.includes("contamination=auto"), "fixed values stay in the detail");
  assert.ok(shown.includes("0.6585"), "threshold is shortened in the list");
  assert.ok(!shown.includes("0.65850"), "the full value stays in the detail");
  assert.equal(new URLSearchParams(calls("GET", "/api/configurations").slice(-1)[0].search).get("active"), "true", "the server filters the active tab");
  const current = app.renderer.root.findAll((node) => node.type === "a" && node.props["aria-current"] === "page").map((node) => text(node.children));
  assert.ok(current.includes("현재 운영 버전"), "URL tab marks the default");
  assert.equal(app.renderer.root.findAll((node) => node.type === "button" && text(node.children).includes("새 학습")).length, 0, "no header button");
  assert.ok(shown.includes("새 학습"), "the training area sits on the same screen");
  await app.go("전체 버전");
  await wait(() => app.shown().includes(V2.slice(0, 8)), "all versions");
  assert.equal(search(app), "?tab=all");
  assert.ok(app.shown().includes("연구 중 · research"));
  assert.ok(!app.shown().includes(V3.slice(0, 8)), "rule-only configurations are not model versions");
  assert.ok(app.shown().includes("평가 전"));
  assert.equal(new URLSearchParams(calls("GET", "/api/configurations").slice(-1)[0].search).get("active"), null, "all versions has no filter");
  const researchRow = app.renderer.root.findAll((node) => node.type === "tr" && text(node.children).includes(V2.slice(0, 8)))[0];
  assert.equal(text(researchRow.children).match(/없음/g).length, 3, "rows without stored values say so in all three columns");
  await app.cleanup();
}

// 4) 현재 운영 버전은 서버 필터 한 번으로 읽는다. 비면 빈 문구, 실패는 코드 그대로
{
  const before = calls("GET", "/api/configurations").length;
  routes = baseRoutes();
  let app = await mount();
  await wait(() => app.shown().includes(V1.slice(0, 8)), "active version");
  await tick(30);
  assert.equal(calls("GET", "/api/configurations").length - before, 1, "one request, no page scan");
  await app.cleanup();

  routes = baseRoutes({ "GET /api/configurations": () => json(page([])) });
  app = await mount();
  await wait(() => app.shown().includes("운영 중인 버전이 없습니다."), "empty active tab");
  await app.cleanup();

  routes = baseRoutes({ "GET /api/configurations": () => json({ code: "configuration_storage_unavailable" }, 503) });
  app = await mount();
  await wait(() => app.shown().includes("버전 조회 실패 · configuration_storage_unavailable"), "error shown");
  assert.ok(!app.shown().includes("운영 중인 버전이 없습니다."), "an error never looks like an empty list");
  routes = baseRoutes();
  await app.click("다시 조회");
  await wait(() => app.shown().includes(V1.slice(0, 8)), "retry");
  await app.cleanup();
}

// 4-1) 첫 쪽이 전부 룰 전용이어도 다음 쪽을 읽을 수 있다. 빈 문구는 끝을 확인한 뒤에만
{
  routes = baseRoutes({ "GET /api/configurations": (target) => json(target.searchParams.get("cursor") ? page([active]) : page([ruleOnly], "next-1")) });
  const app = await mount("/detection/models?tab=all");
  await wait(() => app.button("더 보기"), "more stays reachable");
  assert.ok(!app.shown().includes("학습된 버전이 없습니다."));
  await app.click("더 보기");
  await wait(() => app.shown().includes(V1.slice(0, 8)), "next page");
  await app.cleanup();

  routes = baseRoutes({ "GET /api/configurations": () => json(page([ruleOnly])) });
  const empty = await mount("/detection/models?tab=all");
  await wait(() => empty.shown().includes("학습된 버전이 없습니다."), "empty after the last page");
  await empty.cleanup();
}

// 4-2) 응답 전에 탭을 바꾸면 늦은 응답을 버린다
{
  let release;
  routes = baseRoutes({ "GET /api/configurations": (target) => {
    if (target.searchParams.get("cursor")) return new Promise((resolve) => { release = () => resolve(json(page([research]))); });
    return json(target.searchParams.get("active") === "true" ? page([active]) : page([version(V3)], "next-1"));
  } });
  const app = await mount("/detection/models?tab=all");
  await wait(() => app.button("더 보기"), "more");
  await act(async () => { void app.button("더 보기").props.onClick(); });
  await wait(() => release, "request in flight");
  await app.go("현재 운영 버전");
  await wait(() => app.shown().includes(V1.slice(0, 8)), "active tab");
  await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 20)); });
  assert.ok(!app.shown().includes(V2.slice(0, 8)), "the late page does not land on the other tab");
  await app.cleanup();
}

// 4-3) 늦은 응답은 다른 탭에서 이미 읽은 쪽을 덮지 않는다
{
  let release;
  const extra = version("abababab-1111-4111-8111-111111111111", { active: true });
  routes = baseRoutes({ "GET /api/configurations": (target) => {
    const cursor = target.searchParams.get("cursor");
    if (cursor === "next-all") return new Promise((resolve) => { release = () => resolve(json(page([research]))); });
    if (cursor === "next-active") return json(page([extra]));
    return json(target.searchParams.get("active") === "true" ? page([active], "next-active") : page([version(V3)], "next-all"));
  } });
  const app = await mount("/detection/models?tab=all");
  await wait(() => app.button("더 보기"), "more");
  await act(async () => { void app.button("더 보기").props.onClick(); });
  await wait(() => release, "request in flight");
  await app.go("현재 운영 버전");
  await wait(() => app.shown().includes(V1.slice(0, 8)) && app.button("더 보기")?.props.disabled !== true, "active tab");
  await app.click("더 보기");
  await wait(() => app.shown().includes("abababab"), "the active tab reads its next page");
  await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 20)); });
  assert.ok(app.shown().includes("abababab"), "the late page does not wipe it");
  assert.ok(!app.shown().includes(V2.slice(0, 8)));
  await app.cleanup();
}

// 5) 전체 버전 커서: 더 보기
{
  const cursors = [];
  routes = baseRoutes({ "GET /api/configurations": (target) => {
    cursors.push(target.searchParams.get("cursor"));
    return json(target.searchParams.get("cursor") ? page([research]) : page([active], "next-1"));
  } });
  const app = await mount("/detection/models?tab=all");
  await wait(() => app.button("더 보기"), "more button");
  await app.click("더 보기");
  await wait(() => app.shown().includes(V2.slice(0, 8)), "second page");
  assert.deepEqual(cursors, [null, "next-1"]);
  assert.equal(app.button("더 보기"), undefined, "the last page ends the list");
  await app.cleanup();
}

// 6) 버전 상세: 모델·파라미터·학습 기간·입력 변수·산출물, 작업 결과 hash
{
  routes = baseRoutes({
    [`GET /api/configurations/${V1}`]: () => json(detail),
    [`GET /api/jobs/${JOB}`]: () => json(job("succeeded", { attemptCount: 1, resultSummary: { modelManifest: { modelHash: "a".repeat(64), fitHash: "b".repeat(64) } } })),
  });
  const app = await mount(`/detection/models/${V1}`);
  await wait(() => app.shown().includes("트리 수"), "detail");
  await wait(() => app.shown().includes("a".repeat(64)), "artifact hash from the job");
  const shown = app.shown();
  for (const expected of ["Isolation Forest · ml:iforest", "process-iforest-v1", "운영 중", "2026-10-01 00:00 ~ 2026-11-01 00:00 · 저장 시각 기준", "47,120건", "train-success-only-v1", "입력 변수 2개", "error_count, retry_count", "64", "m-1", "f-1", "value 0.58", "2026-10-03 09:00 ~ 2026-10-03 12:00 · DB 장애"]) {
    assert.ok(shown.includes(expected), expected);
  }
  for (const excluded of ["이 버전으로 교체", "새 비교 평가", "리비전"]) assert.ok(!shown.includes(excluded), `${excluded} is out of this screen`);
  await app.cleanup();
}
{
  routes = baseRoutes({ [`GET /api/configurations/${V1}`]: () => json({ code: "config_not_found" }, 404) });
  const app = await mount(`/detection/models/${V1}`);
  await wait(() => app.shown().includes("버전 조회 실패 · config_not_found"), "missing version");
  await app.cleanup();
}
{
  const older = { ...detail, methods: [{ ...detail.methods[0], parameters: null }] };
  routes = baseRoutes({ [`GET /api/configurations/${V1}`]: () => json(older), [`GET /api/jobs/${JOB}`]: () => json({ code: "job_not_found" }, 404) });
  const app = await mount(`/detection/models/${V1}`);
  await wait(() => app.shown().includes("조회 실패 · job_not_found"), "hash failure is shown");
  assert.ok(app.shown().includes("이 버전에는 파라미터 기록이 없습니다."), "no parameters are invented for older rows");
  await app.cleanup();
}

// 7) 1단계: 입력 오류, 조회, 그래프 단위, 드래그·두 번 클릭·정밀 입력이 같은 목록, 병합·수정·삭제
{
  routes = baseRoutes({ "GET /api/jobs/capabilities": () => json(capabilities("persisted_at", true)) });
  const app = await mount("/detection/models", { createNodeMock: svgMock });
  globalThis.current = app;
  await wait(() => app.input("period-from"), "period");
  assert.equal(app.button("다음 · 입력 변수").props.disabled, true, "no next step before the query");
  await app.click("조회");
  await wait(() => app.shown().includes("조회 시작·종료 날짜를 입력해 주세요."), "date required");
  await queryPeriod(app);
  await wait(() => bars(app).length === 30, "30 daily bars");
  assert.ok(app.shown().includes("거래량 · 저장 시각 기준"), "the graph names the basis the Trainer can use");
  assert.ok(app.shown().includes("단위 일"));
  const volumeCalls = calls("GET", "/api/pipeline/reports/volume");
  assert.ok(volumeCalls.some((call) => call.search.includes("timeBasis=persisted_at") && call.search.includes("bucket=day") && call.search.includes("timezone=Asia%2FSeoul")));
  await wait(() => app.shown().includes("3,000건"), "total count");
  assert.ok(app.shown().includes("2,700건 · 상태 S · train 분할은 이 중 약 70%"), "training rows use status S");
  assert.ok(app.shown().includes("제외 구간 없음"));

  // 드래그: 10/3~10/5
  await pointer("onPointerDown", xOfDay("2026-10-03"));
  await pointer("onPointerMove", xOfDay("2026-10-05"));
  await pointer("onPointerUp", xOfDay("2026-10-05"));
  assert.deepEqual(excludedRows(app), [`${period.dateMinute("2026-10-03")}-${period.dateMinute("2026-10-06")}`]);
  assert.equal(bandsOf(app), 1, "the band shows on the graph");
  await wait(() => app.shown().includes("10/3 ~ 10/5 · 3일 · 300건"), "interval count from the server");
  await wait(() => app.shown().includes("2,430건"), "summary subtracts status S inside the interval");

  // 두 번 클릭: 10/10 → 10/11
  await pointer("onPointerDown", xOfDay("2026-10-10"));
  await pointer("onPointerUp", xOfDay("2026-10-10"));
  assert.ok(app.shown().includes("끝을 클릭하세요 · 시작 10/10"));
  await pointer("onPointerDown", xOfDay("2026-10-11"));
  await pointer("onPointerUp", xOfDay("2026-10-11"));
  assert.equal(excludedRows(app).length, 2);

  // 정밀 입력: 그래프 구간과 맞닿으면 합친다
  await app.set("excl-from", "2026-10-06");
  await app.set("excl-to", "2026-10-06");
  await app.set("excl-from-time", "");
  await app.click("추가");
  assert.deepEqual(excludedRows(app)[0], `${period.dateMinute("2026-10-03")}-${period.dateMinute("2026-10-07")}`, "touching intervals merge");
  await app.set("excl-from", "2026-10-20");
  await app.set("excl-to", "2026-10-19");
  await app.click("추가");
  assert.ok(app.shown().includes("끝이 시작보다 늦어야 합니다."));
  assert.equal(excludedRows(app).length, 2, "a bad input adds nothing");

  // 수정: 목록 → 입력칸 → 수정 적용
  await act(async () => app.renderer.root.findAll((node) => node.type === "button" && (node.props["aria-label"] ?? "").endsWith("입력칸에서 수정"))[1].props.onClick());
  assert.equal(app.input("excl-from").props.value, "2026-10-10");
  await app.set("excl-from-time", "09:30");
  await app.set("excl-to", "2026-10-10");
  await app.set("excl-to-time", "10:00");
  await app.click("수정 적용");
  assert.equal(excludedRows(app)[1], `${period.dateMinute("2026-10-10") + 570}-${period.dateMinute("2026-10-10") + 600}`);
  assert.equal(app.button("수정 취소"), undefined);
  await act(async () => app.renderer.root.findAll((node) => node.type === "button" && (node.props["aria-label"] ?? "").endsWith("제외 구간 삭제"))[1].props.onClick());
  assert.equal(excludedRows(app).length, 1);

  // 드래프트는 이탈 보호 대상이다
  assert.equal(useDraftStore.getState().drafts["/detection/models"]?.dirty, true);
  await app.cleanup();
}

// 8) 그래프 확대·축소·이동: 표시 범위가 바뀌면 그 단위로 다시 읽는다
{
  let wheel;
  routes = baseRoutes();
  const app = await mount("/detection/models", {
    createNodeMock: (element) => (element.type === "svg" ? svgMock(element) : element.props?.["data-testid"] === "period-chart" ? { addEventListener: (_type, handler) => { wheel = handler; }, removeEventListener() {} } : null),
  });
  await queryPeriod(app);
  await wait(() => bars(app).length === 30, "bars");
  const event = (overrides) => ({ preventDefault() {}, deltaX: 0, deltaY: 0, shiftKey: false, clientX: 382, ...overrides });
  for (let i = 0; i < 12; i++) await act(async () => wheel(event({ deltaY: -100 })));
  await wait(() => app.shown().includes("단위 시간"), "zooming in switches to hours");
  assert.ok(calls("GET", "/api/pipeline/reports/volume").some((call) => call.search.includes("bucket=hour")));
  for (let i = 0; i < 6; i++) await act(async () => wheel(event({ deltaY: -100 })));
  await wait(() => app.shown().includes("단위 분"), "then minutes");
  await act(async () => wheel(event({ deltaY: 100, shiftKey: true })));
  await tick(250);
  await app.click("전체 보기");
  await wait(() => app.shown().includes("단위 일"), "reset view");
  await app.cleanup();
}

// 9) 거래량 조회 실패는 코드 그대로
{
  routes = baseRoutes({ "GET /api/pipeline/reports/volume": () => json({ code: "statement_timeout" }, 503) });
  const app = await mount();
  await wait(() => app.input("period-from"), "period");
  await app.set("period-from", "2026-10-01");
  await app.set("period-to", "2026-10-02");
  await app.click("조회");
  await wait(() => app.shown().includes("거래량 조회 실패"), "volume failure");
  assert.equal(app.renderer.root.findAll((node) => node.type === "rect").length, 0, "no fake bars");
  await app.cleanup();
}

// 10) 단계 주소: 앞 단계가 안 끝났으면 가까운 유효 단계로, 뒤로 가기는 단계와 일치
{
  routes = baseRoutes();
  const app = await mount("/detection/models?step=3&tab=all");
  await wait(() => search(app) === "?step=1&tab=all", "step normalized");
  await queryPeriod(app);
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("error_count, retry_count"), "step 2");
  assert.ok(app.shown().includes("prepared_dataset_path_required"), "the server reason stays visible");
  assert.ok(app.shown().includes(catalog.catalogVersion));
  assert.equal(app.button("다음 · 모델·파라미터").props.disabled, true);
  const radio = (id) => app.renderer.root.findAll((node) => node.type === "button" && node.props["aria-label"] === `${id.slice(0, 8)} 선택`)[0];
  assert.equal(radio(SET_BAD).props.disabled, true, "an unsupported set cannot be selected");
  await app.go("이전");
  await wait(() => app.input("period-from"), "back to step 1 keeps the draft");
  assert.equal(app.input("period-from").props.value, "2026-10-01");
  await app.cleanup();
}

// 10-1) 단계 표시 이동: 갈 수 있는 단계만 링크, 주소값·초안 유지
{
  routes = baseRoutes({
    "POST /api/jobs": () => accepted(),
    [`GET /api/jobs/${JOB}`]: () => json(job("failed", { errorCode: "x" })),
  });
  const app = await mount("/detection/models?tab=all");
  await wait(() => app.input("period-from"), "period");
  const stepLink = (label) => app.renderer.root.findAll((node) => node.type === "a" && text(node.children) === label)[0];
  const stepLock = (label) => app.renderer.root.findAll((node) => node.type === "span" && node.props["aria-disabled"] === "true" && text(node.children) === label)[0];
  assert.equal(stepLink("1. 기간").props["aria-current"], "step");
  assert.ok(stepLock("2. 입력 변수") && stepLock("3. 모델·파라미터"), "later steps are disabled before the query");
  assert.equal(stepLink("2. 입력 변수"), undefined);
  await queryPeriod(app);
  assert.ok(stepLink("2. 입력 변수"), "step 2 opens after the query");
  assert.ok(stepLock("3. 모델·파라미터"), "step 3 waits for the input variables");
  await act(async () => { await app.router.navigate(stepLink("2. 입력 변수").props.href); });
  await wait(() => app.shown().includes("error_count, retry_count"), "step 2 by the stepper");
  assert.equal(stepLink("2. 입력 변수").props["aria-current"], "step");
  await act(async () => app.renderer.root.findByProps({ "aria-label": "입력 변수 세트", "data-slot": "radio-group" }).props.onValueChange(SET_OK));
  await act(async () => { await app.router.navigate(stepLink("3. 모델·파라미터").props.href); });
  await wait(() => app.shown().includes("준비 검사"), "step 3 by the stepper");
  await act(async () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0].props.onChange({ target: { value: "256" } }));
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${JOB}`), "job in the address");
  const back = stepLink("1. 기간").props.href;
  assert.ok(back.includes("step=1") && back.includes("tab=all") && back.includes(`job=${JOB}`), "other address values stay");
  await act(async () => { await app.router.navigate(back); });
  await wait(() => app.input("period-from"), "back on step 1");
  assert.equal(app.input("period-from").props.value, "2026-10-01", "the draft stays");
  await act(async () => { await app.router.navigate(stepLink("3. 모델·파라미터").props.href); });
  await wait(() => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0]?.props.value === "256", "edits stay");
  await app.cleanup();
}

// 10-2) 파라미터는 선택한 모델만: 표에도 요청에도 다른 모델의 입력값이 섞이지 않는다
{
  const both = { ...catalog, methods: catalog.methods.map((method) => (method.methodId === "ml:minibatch_kmeans" ? { ...method, executable: { ...method.executable, process: true } } : method)) };
  routes = baseRoutes({
    "GET /api/jobs/capabilities": () => json({ ...capabilities(), methodCatalog: both }),
    "POST /api/jobs": () => accepted(),
    [`GET /api/jobs/${JOB}`]: () => json(job("running")),
  });
  const before = calls("POST", "/api/jobs").length;
  const app = await mount();
  await toStep3(app);
  const paramInput = (label) => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === label)[0];
  const choose = (key) => act(async () => app.renderer.root.findByProps({ "aria-label": "모델", "data-slot": "radio-group" }).props.onValueChange(key));
  await act(async () => paramInput("트리 수").props.onChange({ target: { value: "256" } }));
  assert.equal(paramInput("군집 수 K"), undefined);
  await choose("minibatch_kmeans");
  assert.equal(paramInput("트리 수"), undefined, "the other model's table is gone");
  assert.equal(paramInput("군집 수 K").props.value, "6", "this model starts from its own default");
  await act(async () => paramInput("군집 수 K").props.onChange({ target: { value: "8" } }));
  await app.click("학습 요청");
  await wait(() => search(app).includes("job="), "accepted");
  const body = calls("POST", "/api/jobs").slice(before)[0].body;
  assert.equal(body.modelKey, "minibatch_kmeans");
  assert.deepEqual(Object.keys(body.parameters).sort(), ["batch_size", "k", "max_iter", "n_init", "quantile", "random_state"], "only the selected model's keys");
  assert.equal(body.parameters.k, 8);
  await choose("iforest");
  assert.equal(paramInput("트리 수").props.value, "256", "each model keeps its own values");
  assert.equal(paramInput("군집 수 K"), undefined);
  await app.cleanup();
}

// 11) 3단계와 학습 요청: 카탈로그 표시·검사, 요청 본문, 진행 → 성공
{
  let status = "queued";
  routes = baseRoutes({
    "POST /api/jobs": (_target, options) => json({ id: JOB, status: "queued", kind: "train", inputSpec: JSON.parse(options.body) }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job(status, status === "succeeded" ? { attemptCount: 1, resultSummary: { configVersionId: V1 } } : {})),
  });
  const app = await mount();
  await toStep3(app);
  const shown = app.shown();
  for (const name of ["Isolation Forest", "MiniBatch K-Means", "Extended Isolation Forest", "ml:iforest", "연구 중 · research", "트리 수", "16 ~ 1024", "train-internal-p99-v1 · 조정 안 함"]) {
    assert.ok(shown.includes(name), name);
  }
  for (const name of ["다른 모델 파라미터", "군집 수 K", "n_init"]) assert.ok(!shown.includes(name), `${name} belongs to another model`);
  const model = (name) => app.renderer.root.findAll((node) => node.type === "button" && node.props["aria-label"] === `${name} 선택`)[0];
  assert.equal(model("MiniBatch K-Means").props.disabled, true, "research models cannot be selected");
  assert.equal(model("Extended Isolation Forest").props.disabled, true);
  assert.notEqual(model("Isolation Forest").props.disabled, true);
  assert.ok(app.shown().includes("통과 ·"), "readiness passes");

  const param = (label) => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === label)[0];
  await act(async () => param("트리 수").props.onChange({ target: { value: "5" } }));
  assert.ok(app.shown().includes("확인 필요 · 트리 수: 허용 범위 밖 또는 빈 값"));
  assert.equal(app.button("학습 요청").props.disabled, true, "invalid parameters block the request");
  await act(async () => param("트리 수").props.onChange({ target: { value: "256" } }));
  assert.ok(app.shown().includes("조정됨"));
  await app.click("전체 기본값으로");
  assert.equal(param("트리 수").props.value, "128");
  await act(async () => param("트리 수").props.onChange({ target: { value: "256" } }));

  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${JOB}`), "job in the address");
  const [post] = calls("POST", "/api/jobs");
  assert.deepEqual(post.body, {
    kind: "train", sourceId: SOURCE, from: "2026-09-30T15:00:00.000Z", until: "2026-10-30T15:00:00.000Z", featureSetId: SET_OK, modelKey: "iforest",
    parameters: { n_estimators: 256, max_samples: 512, random_state: 42 }, detectionPolicy: { mode: "ml_only", deadlineMs: 3000, combination: "selected_path" },
  });
  assert.ok(new Headers(post.headers).get("Idempotency-Key"), "the request carries an idempotency key");
  const jobStatus = () => app.renderer.root.findAll((node) => typeof node.type === "string" && node.props["data-job-status"])[0]?.props["data-job-status"];
  await wait(() => jobStatus() === "queued", "queued is not success");
  assert.ok(!app.shown().includes("결과 보기"));
  assert.equal(app.button("학습 요청").props.disabled, true, "no second request while the job runs");
  const stored = useDraftStore.getState().drafts["/detection/models"];
  assert.ok(stored?.value, "the draft stays until the job succeeds");
  assert.equal(stored.leaveFree, true, "an unchanged accepted draft needs no leave confirmation inside the app");
  assert.equal(stored.dirty, true, "it still vanishes on reload, so the browser warning stays");
  assert.ok(windowListeners.has("beforeunload"), "reload and close keep their warning");

  status = "running";
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  await wait(() => jobStatus() === "running", "running from the 5s poll");
  const readsBefore = calls("GET", "/api/configurations").length;
  status = "succeeded";
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  await wait(() => app.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), "result link");
  await wait(() => search(app).includes("step=1"), "the finished draft is cleared and returns to step 1");
  assert.equal(useDraftStore.getState().drafts["/detection/models"], undefined, "success clears the draft");
  await tick(50);
  assert.equal(calls("GET", "/api/configurations").length, readsBefore + 1, "the version list reloads exactly once");
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  const polled = calls("GET", `/api/jobs/${JOB}`).length;
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  assert.equal(calls("GET", `/api/jobs/${JOB}`).length, polled, "polling stops when the job ends");
  await app.cleanup();
}

// 12) 학습 실패·재시도 대기: 코드 그대로, 다시 요청은 새 멱등키
{
  const keys = [];
  routes = baseRoutes({
    "POST /api/jobs": (_target, options) => { keys.push(new Headers(options.headers).get("Idempotency-Key")); return json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201); },
    [`GET /api/jobs/${JOB}`]: () => json(job("failed", { attemptCount: 3, errorCode: "method_parameters_invalid:ml:iforest" })),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => app.shown().includes("실패 · method_parameters_invalid:ml:iforest"), "failed job");
  assert.ok(app.shown().includes("시도 3/3"));
  assert.ok(!app.shown().includes("결과 보기"));
  assert.notEqual(app.button("학습 요청").props.disabled, true, "a failed job can be requested again with the same draft");
  await app.click("학습 요청");
  await wait(() => keys.length === 2, "second request");
  assert.notEqual(keys[0], keys[1], "a new execution uses a new key");
  await app.cleanup();
}
{
  routes = baseRoutes({ [`GET /api/jobs/${JOB}`]: () => json(job("queued", { attemptCount: 1, errorCode: "training_worker_lost" })) });
  const app = await mount(`/detection/models?job=${JOB}`);
  await wait(() => app.shown().includes("재시도 대기 · training_worker_lost"), "retry wait is not a failure");
  await app.cleanup();
}
{
  routes = baseRoutes({ [`GET /api/jobs/${JOB}`]: () => json({ code: "job_not_found" }, 404) });
  const app = await mount(`/detection/models?job=${JOB}`);
  await wait(() => app.shown().includes("학습 작업 조회 실패 · job_not_found"), "unreadable job");
  await app.cleanup();
}

// 12-1) 실패한 작업에서 나갔다 와도 초안·작업 상태가 남는다(대기 중 이탈도 같다)
{
  let status = "failed";
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job(status, status === "failed" ? { attemptCount: 3, errorCode: "insufficient_distinct_training_vectors" } : {})),
  });
  const app = await mount();
  await toStep3(app);
  await act(async () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0].props.onChange({ target: { value: "256" } }));
  await app.click("학습 요청");
  await wait(() => app.shown().includes("실패 · insufficient_distinct_training_vectors"), "failed");
  const jobLink = app.link(JOB.slice(0, 8));
  assert.ok(jobLink.props.href.startsWith("/detection/operations/jobs/"));
  await app.cleanup();

  const back = await mount("/detection/models?step=3", { keepDrafts: true });
  await wait(() => back.shown().includes("실패 · insufficient_distinct_training_vectors"), "job state comes back with the draft");
  await wait(() => back.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0]?.props.value === "256", "edited value is back");
  await wait(() => back.button("학습 요청")?.props.disabled !== true, "can request again");
  await back.cleanup();

  status = "queued";
  const waiting = await mount("/detection/models?step=3", { keepDrafts: true });
  await wait(() => waiting.shown().includes("대기"), "queued job after returning");
  await wait(() => waiting.button("학습 요청"), "step 3");
  assert.equal(waiting.button("학습 요청").props.disabled, true, "leaving and returning does not open a second request");
  await waiting.cleanup();
}

// 12-1a) 옛 작업 주소가 진행 중인 작업을 가리지 않는다: J1 실패 → 재요청 J2 실행 중 → 브라우저 뒤로 가기(`job=J1`) → (다음) → J2 성공
for (const stay of [3, 2]) {
  const created = [JOB, JOB2];
  let second = "running";
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: created.shift(), status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job("failed", { attemptCount: 3, errorCode: "first_failure" })),
    [`GET /api/jobs/${JOB2}`]: () => json(job(second, { id: JOB2, attemptCount: 1, ...(second === "succeeded" ? { resultSummary: { configVersionId: V1 } } : {}) })),
  });
  const before = calls("POST", "/api/jobs").length;
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => app.shown().includes("실패 · first_failure"), "J1 failed");
  await app.go("이전");
  await wait(() => app.shown().includes("error_count, retry_count"), "step 2");
  await app.go("다음 · 모델·파라미터");
  await wait(() => app.shown().includes("준비 검사"), "step 3");
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${JOB2}`), "J2 accepted");
  await act(async () => { await app.router.navigate(-1); });
  await wait(() => search(app).includes("step=2") && search(app).includes(`job=${JOB2}`), "the old address follows the draft's job");
  if (stay === 3) {
    await app.go("다음 · 모델·파라미터");
    await wait(() => app.shown().includes("준비 검사"), "step 3 again");
    await wait(() => app.button("학습 요청"), "request button");
    assert.equal(app.button("학습 요청").props.disabled, true, "no third request while J2 runs");
  }
  await wait(() => app.shown().includes(JOB2.slice(0, 8)), "the running job stays on screen");
  assert.ok(!app.shown().includes("실패 · first_failure"), "the old failure does not replace it");
  assert.equal(calls("POST", "/api/jobs").length - before, 2);
  second = "succeeded";
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  await wait(() => app.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), "J2 result");
  await wait(() => search(app).includes(`job=${JOB2}`) && search(app).includes("step=1"), "the address moves to J2 and the cleared draft returns to step 1");
  await tick(30);
  assert.ok(app.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), "the result stays");
  assert.ok(!app.shown().includes("실패 · first_failure"), "the old job does not come back");
  assert.equal(useDraftStore.getState().drafts["/detection/models"], undefined);
  await app.cleanup();
}

// 12-1b) 이탈 확인: 접수한 내용 그대로면 뜨지 않고 초안은 남는다. 접수 뒤 고치면 뜬다
{
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job("running")),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => app.link(JOB.slice(0, 8)), "job link");
  const target = app.link(JOB.slice(0, 8)).props.href;
  await act(async () => { await app.router.navigate(target); });
  await wait(() => app.router.state.location.pathname === target, "leaves without a confirmation");
  assert.ok(useDraftStore.getState().drafts["/detection/models"].value, "the draft is still kept");
  await app.cleanup();

  const edited = await mount("/detection/models?step=3", { keepDrafts: true });
  await wait(() => edited.link(JOB.slice(0, 8)), "job state after returning");
  await wait(() => edited.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0], "parameters");
  await act(async () => edited.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0].props.onChange({ target: { value: "300" } }));
  assert.notEqual(useDraftStore.getState().drafts["/detection/models"].leaveFree, true, "edits after the request are unsaved");
  await act(async () => { await edited.router.navigate(target); });
  await tick(30);
  assert.equal(edited.router.state.location.pathname, "/detection/models", "the confirmation holds the navigation");
  await edited.cleanup();
}

// 12-1b2) 내용이 같은 실패 작업 초안: 앱 안 이동은 확인 없이, 로그아웃은 기존 확인이 뜬다
{
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job("failed", { attemptCount: 3, errorCode: "x" })),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => app.shown().includes("실패 · x"), "failed");
  assert.equal(useDraftStore.getState().drafts["/detection/models"].leaveFree, true);
  assert.ok(windowListeners.has("beforeunload"));
  const dialogOpen = () => app.renderer.root.findAll((node) => node.props.onOpenChange && node.props.open === true).length > 0;
  assert.equal(dialogOpen(), false);
  await app.click("로그아웃");
  await wait(dialogOpen, "the logout confirmation appears");
  assert.equal(storage.get("token"), "token-admin", "not signed out yet");
  await app.cleanup();
}

// 12-1b3) 응답 전 이동: 다른 화면으로 끌려오지 않고 작업 ID만 초안에 남는다. 같은 화면 단계 이동은 그 주소에 작업을 더한다
{
  let release;
  routes = baseRoutes({
    "POST /api/jobs": () => new Promise((resolve) => { release = () => resolve(json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201)); }),
    [`GET /api/jobs/${JOB}`]: () => json(job("running")),
  });
  const app = await mount();
  await toStep3(app);
  await act(async () => { void app.button("학습 요청").props.onClick(); });
  await wait(() => release, "request in flight");
  await app.go("이전");
  await wait(() => search(app).includes("step=2"), "moved to step 2");
  await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 20)); });
  await wait(() => search(app).includes(`job=${JOB}`), "job lands on the current address");
  assert.ok(search(app).includes("step=2"), "the step the user moved to stays");
  await app.cleanup();

  const held = heldPost();
  const leaving = await mount();
  await toStep3(leaving);
  await startPost(leaving, held);
  await leaveViaDialog(leaving, "변경 사항 저장 후 이동");
  await settle(() => held.release());
  await tick(30);
  assert.equal(leaving.router.state.location.pathname, "/detection/evaluations", "the response does not pull the user back");
  assert.equal(stored().value.jobId, JOB, "the kept draft knows the job");
  await leaving.cleanup();

  const returning = await mount("/detection/models?step=3", { keepDrafts: true });
  await wait(() => returning.link(JOB.slice(0, 8)), "the job shows after returning");
  await wait(() => returning.button("학습 요청"), "step 3");
  assert.equal(returning.button("학습 요청").props.disabled, true, "and blocks a duplicate");
  await returning.cleanup();
}

// 12-1b4) 응답 전 `버리고 이동`(확인창 버튼): 초안은 사라지고 응답이 되살리지 않는다
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "버리고 이동");
  assert.equal(stored(), undefined, "discarded");
  await settle(() => held.release());
  await tick(30);
  assert.equal(stored(), undefined, "the response does not bring it back");
  assert.equal(app.router.state.location.pathname, "/detection/evaluations");
  await app.cleanup();
}

// 12-1b5) 응답 전에 나갔다 돌아온 뒤 응답: 지금 화면에 반영되고 다시 접수되지 않는다(응답을 받은 인스턴스와 화면이 달라도)
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate("/detection/models?step=3"); });
  await wait(() => app.button("학습 요청"), "returned");
  await settle(() => held.release());
  await wait(() => app.link(JOB.slice(0, 8)), "the accepted job shows on the returned screen");
  await wait(() => search(app).includes(`job=${JOB}`), "and in the address");
  assert.equal(app.button("학습 요청").props.disabled, true, "a second request stays blocked");
  assert.equal(held.keys.length, 1);
  await app.cleanup();
}

// 12-1b6) 복귀 화면에서 응답 전에 다시 눌러도 같은 키다(서버가 같은 작업을 돌려준다)
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate("/detection/models?step=3"); });
  await wait(() => app.button("학습 요청"), "returned");
  const first = held.release;
  await startPost(app, held);
  assert.deepEqual(new Set(held.keys).size, 1, "same content, same key");
  await settle(() => { first(); held.release(); });
  await wait(() => app.link(JOB.slice(0, 8)), "one job");
  await app.cleanup();
}

// 12-1b7) 응답 뒤 다음 편집이 작업 ID를 지우지 않는다
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate("/detection/models?step=3"); });
  await wait(() => treeInput(app), "returned");
  await settle(() => held.release());
  await wait(() => stored().value.jobId === JOB, "response landed");
  await setTrees(app, "300");
  assert.equal(stored().value.jobId, JOB, "the next edit keeps the job id");
  assert.notEqual(stored().leaveFree, true);
  await app.cleanup();
}

// 12-1b8) 버리고 돌아와 새 초안을 쓰는 중 응답: 버린 요청의 작업이 새 초안에 적히지 않는다
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "버리고 이동");
  await act(async () => { await app.router.navigate("/detection/models"); });
  await wait(() => app.input("period-from"), "fresh draft");
  await app.set("period-from", "2026-10-07");
  await settle(() => held.release());
  await tick(30);
  assert.equal(stored().value.jobId, null, "the discarded request does not stamp the new draft");
  assert.equal(search(app).includes("job="), false);
  await app.cleanup();
}

// 12-1b9) 재요청 중 확인창 없이 나갔다 돌아온 뒤 응답: 옛 실패가 아니라 새 작업이 보이고 다시 접수되지 않는다
{
  const held = heldPost({ [`GET /api/jobs/${JOB}`]: () => json(job("failed", { errorCode: "x" })) });
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await settle(() => held.release());
  await wait(() => app.shown().includes("실패 · x"), "failed");
  await startPost(app, held);
  await act(async () => { await app.router.navigate("/detection/evaluations"); });
  await wait(() => app.router.state.location.pathname === "/detection/evaluations", "free leave");
  assert.equal(openDialog(app), undefined, "unchanged content needs no confirmation");
  await act(async () => { await app.router.navigate(-1); });
  await wait(() => app.button("학습 요청"), "returned");
  await settle(() => held.release(JOB2));
  await wait(() => app.link(JOB2.slice(0, 8)), "the new job replaces the old failure");
  assert.ok(!app.shown().includes("실패 · x"));
  assert.equal(app.button("학습 요청").props.disabled, true, "blocked while it runs");
  assert.equal(held.keys.length, 2);
  assert.notEqual(held.keys[0], held.keys[1], "a confirmed failure needs a new key");
  await app.cleanup();
}

// 12-1b10) 요청 실패 재시도는 같은 키(실패 확정이 아니므로), 내용을 바꾸면 새 키
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await settle(() => held.fail());
  await wait(() => app.shown().includes("학습 요청 실패 · job_storage_unavailable"), "shown");
  await startPost(app, held);
  assert.equal(held.keys[0], held.keys[1], "the same key on retry");
  await settle(() => held.fail());
  await wait(() => app.button("학습 요청")?.props.disabled !== true, "open");
  await setTrees(app, "300");
  await startPost(app, held);
  assert.notEqual(held.keys[2], held.keys[0], "changed content, new key");
  await settle(() => held.fail());
  await app.cleanup();
}

// 12-1c) 나가 있는 동안 성공: 초안은 비우고 결과 표시는 남긴다(복귀 주소가 어떻든)
for (const address of ["/detection/models", "/detection/models?step=2", "/detection/models?step=3", "/detection/models?tab=all&step=3"]) {
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job("queued")),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes("job="), "accepted");
  await app.cleanup();

  routes = { ...routes, [`GET /api/jobs/${JOB}`]: () => json(job("succeeded", { attemptCount: 1, resultSummary: { configVersionId: V1 } })) };
  const back = await mount(address, { keepDrafts: true });
  await wait(() => back.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), "the result link is there");
  await wait(() => search(back).includes(`job=${JOB}`), "the job moves to the address");
  await tick(30);
  assert.ok(back.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), `and stays after the draft is cleared · ${address}`);
  assert.ok(search(back).includes(`job=${JOB}`), `job stays in the address · ${address}`);
  assert.equal(search(back).includes("tab=all"), address.includes("tab=all"));
  assert.equal(useDraftStore.getState().drafts["/detection/models"], undefined);
  await back.cleanup();
}

// 12-1c2) 돌아온 첫 화면에 이미 성공이 캐시돼 있어도(다른 화면에서 같은 작업을 읽음) 주소가 먼저 옮겨지고 표시가 남는다
{
  routes = baseRoutes({
    "POST /api/jobs": () => accepted(),
    [`GET /api/jobs/${JOB}`]: () => json(job("running")),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes("job="), "accepted");
  await app.cleanup();

  const done = job("succeeded", { attemptCount: 1, resultSummary: { configVersionId: V1 } });
  routes = { ...routes, [`GET /api/jobs/${JOB}`]: () => json(done) };
  const back = await mount("/detection/models?step=3", { keepDrafts: true, prepare: (client) => client.setQueryData(["train-job", "admin-1", JOB], done) });
  await wait(() => search(back).includes("step=1"), "the cleared draft returns to step 1");
  await tick(30);
  assert.ok(search(back).includes(`job=${JOB}`), "the job stays in the address");
  assert.ok(back.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), "the result link stays");
  await back.cleanup();
}

// 12-1d) 2단계에 있는 동안 폴링으로 성공해도 표시가 남는다
{
  let status = "running";
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job(status, status === "succeeded" ? { attemptCount: 1, resultSummary: { configVersionId: V1 } } : {})),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes("job="), "accepted");
  await app.go("이전");
  await wait(() => search(app).includes("step=2"), "step 2");
  status = "succeeded";
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  await wait(() => app.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`), "result");
  await wait(() => search(app).includes("step=1"), "step normalized");
  assert.ok(search(app).includes(`job=${JOB}`));
  await tick(30);
  assert.ok(app.shown().includes(`결과 보기 · 버전 ${V1.slice(0, 8)}`));
  await app.cleanup();
}

// 멱등 서버 모형: POST /api/jobs처럼 같은 키면 같은 작업을 돌려준다(끝난 작업도 200). 응답 유실은 접수 뒤 502다
const FAKE_JOBS = ["a1a1a1a1-1111-4111-8111-111111111111", "a2a2a2a2-2222-4222-8222-222222222222", "a3a3a3a3-3333-4333-8333-333333333333", "a4a4a4a4-4444-4444-8444-444444444444"];
const OLD_JOB = "a0a0a0a0-0000-4000-8000-000000000000";
const RESULT = `결과 보기 · 버전 ${V1.slice(0, 8)}`;
const poll = () => act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
const fakeServer = () => {
  const s = { byKey: new Map(), jobs: new Map(), bodies: new Map(), keys: [], ids: [...FAKE_JOBS], holdNext: false, release: null, commitThenFail: null };
  const commit = (key, body) => {
    let id = s.byKey.get(key);
    const fresh = !id;
    if (fresh) { id = s.ids.shift(); s.byKey.set(key, id); s.jobs.set(id, { status: "running" }); s.bodies.set(id, body); }
    return { id, fresh };
  };
  const answer = (key, body) => {
    const { id, fresh } = commit(key, body);
    return json({ id, status: s.jobs.get(id).status, kind: "train", inputSpec: {} }, fresh ? 201 : 200);
  };
  const read = (id) => () => {
    const entry = s.jobs.get(id);
    if (!entry) return json({ code: "job_not_found" }, 404);
    const inputSpec = { ...s.bodies.get(id), ruleVersionIds: [] };
    return json(job(entry.status, { id, attemptCount: 1, inputSpec,
      ...(entry.status === "failed" ? { errorCode: "boom" } : {}), ...(entry.status === "succeeded" ? { resultSummary: { configVersionId: V1 } } : {}) }));
  };
  routes = baseRoutes({
    "POST /api/jobs": (_target, options) => {
      const key = new Headers(options.headers).get("Idempotency-Key"), body = JSON.parse(options.body);
      s.keys.push(key);
      if (!s.holdNext) return answer(key, body);
      s.holdNext = false;
      return new Promise((resolve) => {
        s.release = () => resolve(answer(key, body));
        s.commitThenFail = () => { commit(key, body); resolve(json({ code: "bad_gateway" }, 502)); };
      });
    },
    ...Object.fromEntries([OLD_JOB, ...FAKE_JOBS].map((id) => [`GET /api/jobs/${id}`, read(id)])),
  });
  return s;
};
const savedKeys = () => JSON.parse(storage.get("s:pipeline-job-requests-v1") ?? "{}");
const clickNoWait = (app) => act(async () => { void app.button("학습 요청").props.onClick(); });
const refreshed = async (app, address) => {
  await app.cleanup();
  useDraftStore.getState().reset();
  return mount(address);
};

// R1c) 주소에만 있는 옛 실패는 키 판단에 쓰지 않는다: 응답 전에 나갔다 돌아와 다시 눌러도 학습은 하나
{
  const s = fakeServer();
  s.jobs.set(OLD_JOB, { status: "failed" });
  const app = await mount(`/detection/models?job=${OLD_JOB}`);
  await toStep3(app);
  await wait(() => app.shown().includes("실패 · boom"), "old failure on screen");
  await wait(() => app.button("학습 요청")?.props.disabled !== true, "open");
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.release, "held");
  const first = s.release;
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate(-1); });
  await wait(() => app.button("학습 요청"), "returned");
  await tick(30);
  if (app.button("학습 요청").props.disabled !== true) { await clickNoWait(app); await tick(40); }
  await settle(() => first());
  await tick(60);
  assert.equal(s.jobs.size - 1, 1, "one execution");
  assert.equal(new Set(s.keys).size, 1, "one key");
  await app.cleanup();
}

// R1d) 같은 상황에서 응답 유실 뒤 재시도: 같은 키
{
  const s = fakeServer();
  s.jobs.set(OLD_JOB, { status: "failed" });
  const app = await mount(`/detection/models?job=${OLD_JOB}`);
  await toStep3(app);
  await wait(() => app.shown().includes("실패 · boom"), "old failure");
  await wait(() => app.button("학습 요청")?.props.disabled !== true, "open");
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.commitThenFail, "held");
  await settle(() => s.commitThenFail());
  await wait(() => app.shown().includes("학습 요청 실패 · bad_gateway"), "error shown");
  await app.click("학습 요청");
  await tick(60);
  assert.equal(s.keys[0], s.keys[1], "same key on retry");
  assert.equal(s.jobs.size - 1, 1, "one execution");
  await app.cleanup();
}

// R1e) 초안의 작업 J1 실패 뒤 재요청 응답 유실: 그 재시도도 같은 키(실패 판단은 J1을 접수한 키에만 적용된다)
{
  const s = fakeServer();
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  s.jobs.get(FAKE_JOBS[0]).status = "failed";
  await poll();
  await wait(() => app.shown().includes("실패 · boom"), "J1 failed");
  await wait(() => app.button("학습 요청")?.props.disabled !== true, "open");
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.commitThenFail, "held");
  await settle(() => s.commitThenFail());
  await wait(() => app.shown().includes("학습 요청 실패 · bad_gateway"), "error");
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "J2");
  assert.notEqual(s.keys[0], s.keys[1], "a confirmed failure gets a new key");
  assert.equal(s.keys[1], s.keys[2], "the lost response retries that key");
  assert.equal(s.jobs.size, 2);
  await app.cleanup();
}

// R4a) 화면이 성공을 본 뒤 같은 내용: 키가 지워져 재전송 없이 새 작업
{
  const s = fakeServer();
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  await poll();
  await wait(() => app.shown().includes(RESULT), "result");
  await tick(60);
  assert.equal(stored(), undefined);
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "J2");
  assert.equal(s.keys.length, 2, "no resend: the key was already gone");
  assert.notEqual(s.keys[0], s.keys[1]);
  await app.cleanup();
}

// R4b·R4c) 새로고침으로 초안이 없을 때 끝난 작업이 같은 키로 돌아오면 새 키로 한 번만 다시 보내 새 작업을 접수한다
for (const refreshWhile of ["running", "succeeded"]) {
  const s = fakeServer();
  let app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  const address = `/detection/models${search(app)}`;
  if (refreshWhile === "succeeded") s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  app = await refreshed(app, address);
  if (refreshWhile === "running") {
    await wait(() => app.renderer.root.findAll((node) => typeof node.type === "string" && node.props["data-job-status"] === "running").length > 0, "running after reload");
    s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
    await poll();
  }
  await wait(() => app.shown().includes(RESULT), "result");
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "a new job");
  assert.equal(s.jobs.size, 2, "a new training");
  assert.equal(s.keys.length, 3, "one resend, no loop");
  assert.equal(s.keys[1], s.keys[0], "the first try still used the kept key");
  assert.notEqual(s.keys[2], s.keys[0], "the resend uses a new key");
  await tick(30);
  assert.ok(stored().value.period.query, "the draft is kept for the new job");
  assert.equal(stored().value.jobId, FAKE_JOBS[1]);
  await app.cleanup();
}

// R4d) 접수 뒤 고쳐 `버리고 이동`, 나중에 같은 내용: 끝난 작업이 돌아와도 새 학습
{
  const s = fakeServer();
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  await setTrees(app, "300");
  await leaveViaDialog(app, "버리고 이동");
  assert.equal(stored(), undefined);
  s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  await act(async () => { await app.router.navigate("/detection/models"); });
  await wait(() => app.input("period-from"), "fresh");
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "a new job");
  assert.equal(s.jobs.size, 2);
  await app.cleanup();
}

// 초안 없이 끝난 작업의 키는 남을 수 있다. 다음 같은 내용 요청의 재전송이 교체한다
{
  const s = fakeServer();
  (await import("../src/services/api/job-idempotency.ts")).clearJobRequestKeys();
  let app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  const address = `/detection/models${search(app)}`;
  s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  app = await refreshed(app, address);
  await wait(() => app.shown().includes(RESULT), "result");
  await tick(30);
  assert.deepEqual(Object.values(savedKeys()), [s.keys[0]], "the key stays: nothing ties it to the finished job");
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "new job");
  assert.equal(s.keys.length, 3, "one resend");
  assert.deepEqual(Object.values(savedKeys()), [s.keys[2]], "replaced by the new key");
  await app.cleanup();
}

// W) 옛 성공 작업과 내용이 같은 새 요청의 키는 지워지지 않는다(J2가 유실·실행 중인데 J3가 생기면 안 된다)
const j1Succeeded = async (s) => {
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  await poll();
  await wait(() => app.shown().includes(RESULT), "result");
  await tick(60);
  assert.equal(stored(), undefined, "draft cleared");
  return app;
};
const runningJobs = (s) => [...s.jobs.values()].filter((entry) => entry.status === "running").length;
// W1) 응답 유실 → 새로고침(주소는 성공한 J1) → 같은 내용
{
  const s = fakeServer();
  let app = await j1Succeeded(s);
  await toStep3(app);
  const address = `/detection/models${search(app)}`;
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.commitThenFail, "held");
  await settle(() => s.commitThenFail());
  await wait(() => app.shown().includes("학습 요청 실패"), "error");
  app = await refreshed(app, address);
  await wait(() => app.shown().includes(RESULT), "old result");
  await tick(60);
  await toStep3(app);
  await app.click("학습 요청");
  await tick(100);
  assert.equal(s.jobs.size, 2, "J1 and one retry");
  assert.equal(s.keys[1], s.keys[2], "the lost request is retried with its key");
  await app.cleanup();
}
// W2) 성공 주소에서 POST 대기 → 나갔다 돌아옴 → 다시 클릭
{
  const s = fakeServer();
  const app = await j1Succeeded(s);
  await toStep3(app);
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.release, "held");
  const first = s.release;
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate(-1); });
  await wait(() => app.button("학습 요청"), "returned");
  await tick(40);
  if (app.button("학습 요청").props.disabled !== true) { await clickNoWait(app); await tick(40); }
  await settle(() => first());
  await tick(60);
  assert.equal(s.jobs.size, 2, "J1 and one new training");
  await app.cleanup();
}
// W2b) 응답 유실 → 나갔다 돌아옴 → 재시도
{
  const s = fakeServer();
  const app = await j1Succeeded(s);
  await toStep3(app);
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.commitThenFail, "held");
  await settle(() => s.commitThenFail());
  await wait(() => app.shown().includes("학습 요청 실패"), "error");
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate(-1); });
  await wait(() => app.button("학습 요청"), "returned");
  await tick(40);
  await clickNoWait(app);
  await tick(100);
  assert.equal(s.jobs.size, 2, "J1 and one retry");
  await app.cleanup();
}
// W3) J2 실행 중 → 새로고침해 옛 J1 주소로 → 같은 내용: 같은 J2로 이어진다
{
  const s = fakeServer();
  let app = await j1Succeeded(s);
  const oldAddress = `/detection/models${search(app)}`;
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "J2 running");
  app = await refreshed(app, oldAddress);
  await wait(() => app.shown().includes(RESULT), "old result");
  await tick(60);
  await toStep3(app);
  await app.click("학습 요청");
  await tick(100);
  assert.equal(runningJobs(s), 1, "J2 only, no parallel duplicate");
  assert.equal(s.jobs.size, 2);
  assert.ok(search(app).includes(`job=${FAKE_JOBS[1]}`), "the screen follows J2");
  await app.cleanup();
}
// 옛 키의 늦은 응답은 재전송이 만든 새 키를 지우지 않는다
{
  const s = fakeServer();
  (await import("../src/services/api/job-idempotency.ts")).clearJobRequestKeys();
  let app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  const address = `/detection/models${search(app)}`;
  s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  app = await refreshed(app, address);
  await wait(() => app.shown().includes(RESULT), "result");
  await toStep3(app);
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.release, "first held");
  const late = s.release;
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate(-1); });
  await wait(() => app.button("학습 요청"), "returned");
  await tick(30);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[1]}`), "resent as a new job");
  const newest = s.keys.at(-1);
  await settle(() => late());
  await tick(40);
  assert.deepEqual(Object.values(savedKeys()), [newest], "the late reply leaves the new key alone");
  await app.cleanup();
}

// 재전송 전에 초안을 버렸으면 다시 보내지 않고 키만 지운다
{
  const s = fakeServer();
  let app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes(`job=${FAKE_JOBS[0]}`), "J1");
  const address = `/detection/models${search(app)}`;
  s.jobs.get(FAKE_JOBS[0]).status = "succeeded";
  app = await refreshed(app, address);
  await wait(() => app.shown().includes(RESULT), "result");
  await toStep3(app);
  s.holdNext = true;
  await clickNoWait(app);
  await wait(() => s.release, "held");
  await leaveViaDialog(app, "버리고 이동");
  await settle(() => s.release());
  await tick(30);
  assert.equal(s.keys.length, 2, "no resend for a discarded draft");
  assert.equal(s.jobs.size, 1);
  assert.equal(stored(), undefined);
  await app.cleanup();
}

// 버린 요청의 실패는 새 초안에 적히지 않는다
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "버리고 이동");
  await act(async () => { await app.router.navigate("/detection/models"); });
  await wait(() => app.input("period-from"), "fresh draft");
  await app.set("period-from", "2026-10-07");
  await settle(() => held.fail());
  await tick(30);
  assert.equal(stored().value.requestError, null, "the discarded request's failure stays out");
  assert.ok(!app.shown().includes("학습 요청 실패"));
  await app.cleanup();
}

// R6) 복귀 뒤 이전 요청이 실패해도 지금 화면에 코드가 보인다
{
  const held = heldPost();
  const app = await mount();
  await toStep3(app);
  await startPost(app, held);
  await leaveViaDialog(app, "변경 사항 저장 후 이동");
  await act(async () => { await app.router.navigate("/detection/models?step=3"); });
  await wait(() => app.button("학습 요청"), "returned");
  await settle(() => held.fail());
  await wait(() => app.shown().includes("학습 요청 실패 · job_storage_unavailable"), "the error reaches the returned screen");
  await startPost(app, held);
  assert.ok(!app.shown().includes("학습 요청 실패"), "a new request clears it");
  await settle(() => held.release());
  await app.cleanup();
}

// 12-2) 성공 뒤 접수 후 고친 초안은 남긴다
{
  let status = "running";
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job(status, status === "succeeded" ? { resultSummary: { configVersionId: V1 } } : {})),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => search(app).includes("job="), "accepted");
  await act(async () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0].props.onChange({ target: { value: "300" } }));
  status = "succeeded";
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5100)));
  await wait(() => app.shown().includes("결과 보기"), "success");
  await tick(30);
  assert.equal(app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "트리 수")[0]?.props.value, "300", "newer edits stay");
  await app.cleanup();
}

// 12-3) 접수 뒤 상태 조회가 실패하면 새 요청을 막고, 다시 조회가 성공하면 풀린다. 없는 작업은 막지 않는다
{
  let read = () => json({ code: "job_storage_unavailable" }, 503);
  routes = baseRoutes({
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => read(),
  });
  const app = await mount();
  await toStep3(app);
  await app.click("학습 요청");
  await wait(() => app.shown().includes("학습 작업 조회 실패 · job_storage_unavailable"), "status unknown");
  assert.equal(app.button("학습 요청").props.disabled, true, "an unknown job blocks a duplicate request");
  assert.equal(calls("POST", "/api/jobs").length > 0, true);
  const posts = calls("POST", "/api/jobs").length;
  read = () => json(job("failed", { errorCode: "x" }));
  await app.click("다시 조회");
  await wait(() => app.button("학습 요청").props.disabled !== true, "known state releases the button");
  assert.equal(calls("POST", "/api/jobs").length, posts);
  await app.cleanup();

  read = () => json({ code: "job_not_found" }, 404);
  const gone = await mount();
  await toStep3(gone);
  await gone.click("학습 요청");
  await wait(() => gone.shown().includes("학습 작업 조회 실패 · job_not_found"), "missing job");
  assert.notEqual(gone.button("학습 요청").props.disabled, true, "a missing job does not block forever");
  await gone.cleanup();
}

// 13) 제외 구간 미지원: 준비 검사에서 서버 코드 그대로 막고, 1단계 건수도 가능한 것처럼 보이지 않는다
{
  routes = baseRoutes();
  const app = await mount();
  await queryPeriod(app);
  await wait(() => app.shown().includes("건 · 상태 S"), "training rows");
  await app.set("excl-from", "2026-10-03");
  await app.set("excl-to", "2026-10-03");
  await app.click("추가");
  await wait(() => app.shown().includes("학습에 쓰는 건수확인 필요 · config_schema_not_supported_yet"), "no count for an impossible training");
  assert.ok(!app.shown().includes("건 · 상태 S"));
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("error_count, retry_count"), "feature sets");
  await act(async () => app.renderer.root.findByProps({ "aria-label": "입력 변수 세트", "data-slot": "radio-group" }).props.onValueChange(SET_OK));
  await app.go("다음 · 모델·파라미터");
  await wait(() => app.shown().includes("확인 필요 · 제외 구간: config_schema_not_supported_yet"), "readiness blocks");
  assert.ok(!app.shown().includes("통과 ·"));
  assert.equal(app.button("학습 요청").props.disabled, true);
  await app.cleanup();
}

// 13-0) 구간 건수를 모르면 0으로 보이지 않고 학습 건수도 내지 않는다(조회 실패·조회 중)
{
  const inRange = (target) => target.searchParams.get("bucket") === "month" && target.searchParams.get("from") !== period.toUtc(period.dateMinute("2026-10-01"));
  let hold = false, release;
  routes = baseRoutes({
    "GET /api/jobs/capabilities": () => json(capabilities("persisted_at", true)),
    "GET /api/pipeline/reports/volume": (target) => {
      if (!inRange(target)) return json(volumeOf(target));
      if (hold) return new Promise((resolve) => { release = () => resolve(json(volumeOf(target))); });
      return json({ code: "statement_timeout" }, 503);
    },
  });
  const app = await mount();
  await queryPeriod(app);
  await wait(() => app.shown().includes("2,700건 · 상태 S"), "training rows without intervals");
  await app.set("excl-from", "2026-10-03");
  await app.set("excl-to", "2026-10-03");
  await app.click("추가");
  await wait(() => app.shown().includes("제외 구간 건수 조회 실패 · statement_timeout"), "failure shown");
  assert.ok(app.shown().includes("10/3 · 1일 · —"), "the row has no count");
  assert.ok(app.shown().includes("1일 · — · 1구간"), "the summary has no count");
  assert.ok(!app.shown().includes("건 · 상태 S"), "no training count without the amount to subtract");
  assert.ok(app.shown().includes("학습에 쓰는 건수—"));

  hold = true;
  await app.set("excl-from", "2026-10-05");
  await app.set("excl-to", "2026-10-05");
  await app.click("추가");
  await wait(() => release, "second count in flight");
  assert.ok(app.shown().includes("2일 · — · 2구간"), "still unknown while loading");
  assert.ok(!app.shown().includes("건 · 상태 S"));
  hold = false;
  await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 20)); });
  await app.cleanup();
}

// 13-1) 지원으로 바뀌면 값만으로 풀린다. 서버가 그래도 거절하면 그대로 보여 주고 초안을 남긴다
{
  routes = baseRoutes({
    "GET /api/jobs/capabilities": () => json(capabilities("persisted_at", true)),
    "POST /api/jobs": () => json({ code: "config_schema_not_supported_yet" }, 422),
  });
  const app = await mount();
  await queryPeriod(app);
  await app.set("excl-from", "2026-10-03");
  await app.set("excl-to", "2026-10-03");
  await app.click("추가");
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("error_count, retry_count"), "feature sets");
  await act(async () => app.renderer.root.findByProps({ "aria-label": "입력 변수 세트", "data-slot": "radio-group" }).props.onValueChange(SET_OK));
  await app.go("다음 · 모델·파라미터");
  await wait(() => app.shown().includes("준비 검사"), "step 3");
  await app.click("학습 요청");
  await wait(() => app.shown().includes("학습 요청 실패 · config_schema_not_supported_yet"), "server refusal shown");
  const [post] = calls("POST", "/api/jobs").slice(-1);
  assert.deepEqual(post.body.excludedIntervals, [{ from: "2026-10-02T15:00:00.000Z", until: "2026-10-03T15:00:00.000Z" }]);
  assert.equal(search(app).includes("job="), false, "no job was registered");
  assert.equal(useDraftStore.getState().drafts["/detection/models"]?.dirty, true, "the draft stays");
  await app.cleanup();
}

// 14) 서버가 ESB 기준을 알리면 화면·요청이 따라간다(코드 분기 없이 capabilities 값)
{
  routes = baseRoutes({
    "GET /api/jobs/capabilities": () => json(capabilities("esb_occurred_at")),
    "POST /api/jobs": () => json({ id: JOB, status: "queued", kind: "train", inputSpec: {} }, 201),
    [`GET /api/jobs/${JOB}`]: () => json(job("queued")),
  });
  const app = await mount();
  await toStep3(app);
  assert.ok(calls("GET", "/api/pipeline/reports/volume").slice(-1)[0].search.includes("timeBasis=esb_occurred_at"));
  await app.click("학습 요청");
  await wait(() => search(app).includes("job="), "job");
  assert.equal(calls("POST", "/api/jobs").slice(-1)[0].body.timeBasis, "esb_occurred_at");
  await app.cleanup();
}
{
  routes = baseRoutes();
  const app = await mount();
  await queryPeriod(app);
  assert.ok(app.shown().includes("거래량 · 저장 시각 기준"));
  await app.cleanup();
}

// 15) 조회 실패·빈 값: 카탈로그·출처·입력 변수
{
  routes = baseRoutes({ "GET /api/jobs/capabilities": () => json({ models: [] }) });
  let app = await mount();
  await wait(() => app.shown().includes("방법 카탈로그 조회 실패 · method_catalog_unavailable"), "catalog missing");
  assert.equal(app.input("period-from"), undefined, "no training form without the catalog");
  await app.cleanup();

  routes = baseRoutes({ "GET /api/pipeline/sources": () => json({ items: [] }) });
  app = await mount();
  await wait(() => app.shown().includes("저장된 데이터 출처가 없습니다."), "no sources");
  await app.cleanup();

  routes = baseRoutes({ "GET /api/jobs/feature-sets": () => json({ ...featureSets, items: [] }) });
  app = await mount();
  await queryPeriod(app);
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("등록된 입력 변수가 없습니다."), "no feature sets");
  await app.cleanup();

  routes = baseRoutes({ "GET /api/jobs/feature-sets": () => json({ code: "job_storage_unavailable" }, 503) });
  app = await mount();
  await queryPeriod(app);
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("입력 변수 조회 실패 · job_storage_unavailable"), "feature set failure");
  await app.cleanup();
}

// 15-1) 입력 변수 목록이 잘렸을 때만 잘렸다고 표시한다(서버 상한 200개, hasMore 경계)
for (const [hasMore, expected] of [[true, true], [false, false]]) {
  routes = baseRoutes({ "GET /api/jobs/feature-sets": () => json({ ...featureSets, hasMore }) });
  const app = await mount();
  await queryPeriod(app);
  await app.go("다음 · 입력 변수");
  await wait(() => app.shown().includes("error_count, retry_count"), "feature sets");
  assert.equal(app.shown().includes("최근 등록 200개만 표시합니다."), expected);
  await app.cleanup();
}

// 16) 일반 사용자는 관리자 안내만 본다(서버 조회 없음)
{
  routes = baseRoutes();
  const before = requested.length;
  const app = await mount("/detection/models", { userType: "user" });
  await tick(30);
  assert.equal(requested.slice(before).filter((call) => call.path === "/api/configurations").length, 0);
  await app.cleanup();
}

console.log("models page QA passed: versions, detail, three training steps, job progress, refusals, catalog values");
