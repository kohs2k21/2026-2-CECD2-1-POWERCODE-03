import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 운영 설정·모델 교체 화면 통합(로컬 가짜 fetch만): 방법별 작동 상태 배지(정상·주의·점검 필요·첫 실행 대기), 판정 정책 비노출, 운영에서 제외·복원 요청 본문과
// 결과·실패 안내, 모델 교체 3단계(가짜 비교 API 응답을 근거로 replace 본문, 추천 stale 거절, v1 비교 결과로 3단계까지), 비교 API가 없으면 2단계에서 막힘.
register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  addEventListener() {},
  removeEventListener() {},
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

const { readFile } = await import("node:fs/promises");
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, Link, RouterProvider } = await import("react-router-dom");
const { Dialog } = await import("../src/components/ui/dialog.tsx");
const { Checkbox } = await import("../src/components/ui/checkbox.tsx");
const { RadioGroup } = await import("../src/components/ui/radio-group.tsx");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
const sample = await import("./evaluations-sample.ts");
const { evaluationResponse } = await import("./evaluations-fake-api.mjs");
const { methodBadge } = await import("../src/features/detection/operations/methodStatus.ts");

const catalog = JSON.parse(await readFile(new URL("../../contracts/method-catalog.json", import.meta.url), "utf8"));
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const [V_ACTIVE, V_EXP7, , V_PREVIOUS] = sample.compareVersions.map((version) => version.id);
const SOURCE = "11111111-1111-4111-8111-111111111111";
const EVAL7 = "e7e7e7e7-1111-4111-8111-111111111111";
const now = Date.now();
const iso = (minutesAgo) => new Date(now - minutesAgo * 60_000).toISOString();
const actor = { actorId: "1", displayName: "admin@example.test" };

// 단위 기준(사용자 9.7·9.8): 실행 0은 적용 뒤 대기 시간 안이면 첫 실행 대기, 지나면 점검 필요. 미판정 10% 점검 필요, 2% 주의(기한 초과 2%도 주의).
const stat = (overrides) => ({ methodId: "x", category: "hard", modelVersionId: null, enabledSince: iso(600), runs: 100, succeeded: 100, notReady: 0, failed: 0, anomalous: 0, timeouts: 0, causes: [], lastRunAt: iso(1), elapsedP95Ms: 2, ...overrides });
assert.equal(methodBadge(stat({ runs: 0, enabledSince: iso(10) }), 3_600_000, now).label, "적용됨 · 첫 실행 대기");
assert.deepEqual(methodBadge(stat({ runs: 0, enabledSince: iso(61) }), 3_600_000, now), { label: "점검 필요", tone: "critical", why: "실행 없음" });
assert.equal(methodBadge(stat({ runs: 0, enabledSince: null }), 3_600_000, now).label, "점검 필요", "unknown start is not shown as waiting");
assert.deepEqual(methodBadge(stat({ succeeded: 90, notReady: 6, failed: 4 }), 3_600_000, now), { label: "점검 필요", tone: "critical", why: "미판정 10%" });
assert.equal(methodBadge(stat({ succeeded: 98, notReady: 2 }), 3_600_000, now).label, "주의");
assert.equal(methodBadge(stat({ timeouts: 2 }), 3_600_000, now).label, "주의");
assert.equal(methodBadge(stat({ succeeded: 99, notReady: 1 }), 3_600_000, now).label, "정상");

const version = (id, overrides = {}) => ({
  id, sourceId: SOURCE, schemaVersion: 2, displayName: sample.compareVersions.find((item) => item.id === id)?.displayName ?? null, contentHash: "c".repeat(64), createdAt: "2026-11-01T00:00:00Z",
  modelKey: "iforest", active: false, activeSince: null, createdFrom: { kind: "train", trainJobId: null }, executable: true,
  methods: [{ methodId: "ml:iforest", enabled: true, status: "supported", executable: { process: true, window: true }, executableReason: null }],
  trainingSelection: { trainingPeriod: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z", timeBasis: "persisted_at" }, excludedIntervalCount: 0 },
  modelSummary: { featureCount: 6, parameters: { n_estimators: 128 }, thresholdValue: "0.58" },
  latestEvaluation: { evaluationId: EVAL7, applicability: "valid", completedAt: "2026-11-02T00:00:00Z" },
  applicationEligibility: { eligible: true, evaluationId: EVAL7, reason: null }, ...overrides,
});
const versions = [version(V_EXP7), version(V_ACTIVE, { active: true }), version(V_PREVIOUS, { applicationEligibility: { eligible: false, evaluationId: null, reason: "no_eligible_evaluation" } })];
const hard = (id, name, enabled = true) => ({ id, methodVersion: "v1", displayName: name, conditionText: `${name} 조건`, severity: "critical", enabled, editable: ["enabled"] });
const ruleSet = {
  revision: 13, contentHash: "a".repeat(64), contentHashVersion: 2, savedAt: iso(120), savedBy: actor, restoredFromRevision: null,
  counts: { hardEnabled: 2, hardTotal: 3, softEnabled: 2, softTotal: 2 },
  hardRules: [hard("hard:process-status", "상태가 S가 아님"), hard("hard:negative-duration", "시각 역전"), hard("hard:count-conservation", "건수 합계", false)],
  softRules: [
    { id: "soft:error_ratio-1", name: "오류 비율", field: "error_ratio", operator: "gt", threshold: 0.2, unit: "ratio", severity: "critical", enabled: true },
    { id: "soft:total_count-2", name: "총 건수", field: "total_count", operator: "gte", threshold: 100, unit: "count", severity: "info", enabled: true },
  ],
};
const model = { configVersionId: V_ACTIVE, modelKey: "iforest", displayName: "V4", activatedAtRevision: 7 };
let operational;
const freshState = () => ({
  ruleSet: { revision: 13, contentHash: "a".repeat(64), contentHashVersion: 2, savedAt: iso(120), savedBy: actor },
  activeModelSet: { revision: 7, models: [model], changedAt: iso(300), changedBy: actor, restoredFromRevision: null },
  detectionPolicy: { revision: 0, policy: { version: 2, policyKey: "category-k-of-n-or-v1", k: { hard: 1, soft: 1, ml: 1 }, deadlineMs: 3000 }, isDefault: true },
  readAt: iso(0),
});
const report = (period) => ({
  period, from: iso(60), until: iso(0), firstRunWaitMs: 3_600_000, ruleSetRevision: 13, activeModelSetRevision: 7,
  decisionCount: 200, legacyDecisionCount: 0, failedWithoutDecision: { count: 4, byCode: [{ code: "operational_state_unavailable", count: 2 }, { code: "job_failed", count: 1 }, { code: "rule_set_hash_mismatch", count: 1 }] },
  methods: [
    stat({ methodId: "hard:process-status", runs: 200, succeeded: 200, anomalous: 3 }),
    stat({ methodId: "hard:negative-duration", runs: 200, succeeded: 194, notReady: 6, causes: [{ cause: "input_invalid", count: 6 }] }),
    stat({ methodId: "soft:error_ratio-1", category: "soft", runs: 200, succeeded: 170, failed: 30, causes: [{ cause: "input_invalid", count: 30 }] }),
    stat({ methodId: "soft:total_count-2", category: "soft", runs: 0, succeeded: 0, enabledSince: iso(5), lastRunAt: null, elapsedP95Ms: null }),
    stat({ methodId: "ml:iforest", category: "ml", modelVersionId: V_ACTIVE, runs: 200, succeeded: 199, notReady: 1, timeouts: 1, causes: [{ cause: "timeout", count: 1 }], elapsedP95Ms: 1440 }),
  ],
});
const historyItem = (revision, models, changes, extra = {}) => ({ revision, changedAt: iso(1000 - revision), changedBy: actor, note: null, restoredFromRevision: null, changes, summary: {}, snapshot: { models }, ...extra });
const history = {
  items: [
    historyItem(7, [model], [{ type: "replaced", configVersionId: V_ACTIVE, modelKey: "iforest", displayName: "V4", previousConfigVersionId: V_PREVIOUS, previousDisplayName: "V3" }]),
    historyItem(6, [{ ...model, configVersionId: V_PREVIOUS, displayName: "V3", activatedAtRevision: 6 }], [{ type: "activated", configVersionId: V_PREVIOUS, modelKey: "iforest", displayName: "V3" }]),
    historyItem(0, [], []),
  ],
  pagination: { limit: 50, hasMore: false, nextCursor: null },
};

const requested = [];
let routes;
const fake = { up: true };
let currentUser;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, search: target.search, method: options.method ?? "GET", body: options.body ? JSON.parse(options.body) : undefined });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  const evaluation = evaluationResponse(sample, fake, options.method ?? "GET", target, options.body ? JSON.parse(options.body) : undefined);
  if (evaluation) return evaluation;
  const handler = routes[`${options.method ?? "GET"} ${target.pathname}`];
  return handler ? handler(target, options) : json({ code: "unavailable" }, 503);
};
const baseRoutes = (overrides = {}) => ({
  "GET /api/operational-state": () => json(operational),
  "GET /api/rule-set": () => json(ruleSet),
  "GET /api/jobs/capabilities": () => json({ methodCatalog: catalog, snapshot: { timeBasis: "persisted_at" } }),
  "GET /api/operations/method-status": (target) => json(report(target.searchParams.get("period"))),
  "GET /api/active-models/history": () => json(history),
  "GET /api/configurations": () => json({ items: versions, pagination: { limit: 50, hasMore: false, nextCursor: null } }),
  ...overrides,
});
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (path, { up = true, state } = {}) => {
  fake.up = up;
  currentUser = { id: "admin-1", email: "admin@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", "token-admin");
  useDraftStore.getState().reset();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [state ? { pathname: path.split("?")[0], search: path.includes("?") ? `?${path.split("?")[1]}` : "", state } : path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))), { createNodeMock: () => null });
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
  const row = (method) => text(renderer.root.findAll((node) => node.type === "tr" && node.props["data-method"] === method)[0]?.children);
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, button, click, link, go, row, cleanup };
};
const elements = (node) => (Array.isArray(node) ? node.flatMap(elements) : React.isValidElement(node) ? [node, ...elements(node.props.children)] : []);
const openDialog = (app) => app.renderer.root.findAllByType(Dialog).find((node) => node.props.open);
/** 확인창은 포털이라 시험 렌더러에 없다. 열린 Dialog 요소 트리에서 체크·버튼을 찾는다. */
const dialogPart = (app, match) => elements(openDialog(app).props.children).find(match);
const approveDialog = async (app) => act(async () => dialogPart(app, (node) => node.type === Checkbox).props.onCheckedChange(true));
const pressDialog = async (app, label) => {
  const target = dialogPart(app, (node) => node.props.onClick && node.props.children === label);
  assert.ok(target, label);
  assert.notEqual(target.props.disabled, true, `${label} is enabled`);
  await act(async () => target.props.onClick());
};
const posts = (path) => requested.filter((call) => call.method === "POST" && call.path === path);

const { detectionToc } = await import("../src/features/detection/detectionToc.ts");
/** 목차 영역 id → 화면 영역 제목(없으면 null). 영역 제목은 목차 이름과 같아야 한다. */
const tocSections = (app, menu) => detectionToc[menu].map(({ id }) => {
  const card = app.renderer.root.findAll((node) => node.type === "div" && node.props.id === id && node.props["data-slot"] === "section-card")[0];
  return [id, card ? text(card.findByType("h2").children) : null];
});
// 1) 운영 설정 overview: 켜진 규칙만, 상태 배지, 판정 없이 실패, 모델 행, 이력. 판정 정책은 보이지 않는다.
{
  operational = freshState();
  routes = baseRoutes();
  const app = await mount("/detection/operations", { up: false });
  await wait(() => app.row("ml:iforest").includes("1,440 ms"), "model row");
  assert.ok(app.shown().includes("룰 버전 13"));
  assert.deepEqual(tocSections(app, "operations"), [["operations-status", "운영 버전 상태"], ["operations-history", "모델 교체 이력"], ["operations-jobs", "작업"]], "TOC targets carry the area titles");
  assert.ok(app.row("hard:process-status").startsWith("상태가 S가 아님") && app.row("hard:process-status").includes("정상"));
  assert.ok(app.row("hard:negative-duration").includes("주의") && app.row("hard:negative-duration").includes("입력 형식 오류 6"));
  assert.ok(app.row("soft:error_ratio-1").includes("점검 필요") && app.row("soft:error_ratio-1").includes("미판정 15%"));
  assert.ok(app.row("soft:total_count-2").includes("적용됨 · 첫 실행 대기"));
  assert.equal(app.row("hard:count-conservation"), "", "rules that are off are not listed");
  assert.ok(app.row("ml:iforest").includes("4d2f7c10 · V4") && app.row("ml:iforest").includes("기한 초과 1"));
  assert.ok(app.shown().includes("판정 없이 실패 4건 · 최근 1시간 · 작업 실패 1 · 룰 세트 읽기 실패 1 · operational_state_unavailable 2 · 방법별 집계와 별도"));
  assert.ok(app.shown().includes("2a91e6d7 · V3 → 4d2f7c10 · V4"));
  assert.ok(!/정책|deadlineMs|category-k-of-n/.test(app.shown()), "the detection policy is not shown");
  assert.ok(app.shown().includes("모델 교체 추천 미연결 · service_unavailable"), "production has no comparison sample");
  await app.click("최근 24시간");
  await wait(() => requested.some((call) => call.path === "/api/operations/method-status" && call.search === "?period=24h"), "24h read");
  await app.cleanup();
}

// 2) 운영에서 제외: 확인 체크 뒤 retire 본문(expectedRevision = 조회한 활성 집합 버전), 성공 안내와 다시 읽기. 실패는 서버 코드 그대로.
{
  operational = freshState();
  routes = baseRoutes({
    "POST /api/configurations/4d2f7c10-6b1e-4e39-9a44-1c0e7b85d2a6/applications": () => {
      operational = { ...operational, activeModelSet: { ...operational.activeModelSet, revision: 8, models: [] } };
      return json({ revision: 8, previousRevision: 7, models: [], changes: [{ type: "retired", configVersionId: V_ACTIVE, modelKey: "iforest" }], changedAt: iso(0), appliesFrom: "next_decision" });
    },
  });
  const app = await mount("/detection/operations", { up: false });
  await wait(() => app.button("운영에서 제외"), "retire button");
  await app.click("운영에서 제외");
  await wait(() => openDialog(app), "retire dialog");
  assert.equal(dialogPart(app, (node) => node.props.onClick && node.props.children === "운영에서 제외").props.disabled, true, "approval required");
  await approveDialog(app);
  await pressDialog(app, "운영에서 제외");
  await wait(() => app.shown().includes("운영 모델에서 4d2f7c10 · V4 제외됨 · 이후 판정부터 사용 안 함 · 현재 룰만으로 판정"), "retired");
  assert.deepEqual(posts(`/api/configurations/${V_ACTIVE}/applications`).at(-1).body, { operation: "retire", expectedRevision: 7, confirmRetire: true });
  await wait(() => app.shown().includes("운영 중인 머신러닝 모델이 없습니다. 룰만으로 판정합니다."), "reloaded state");
  await app.cleanup();

  operational = freshState();
  routes = baseRoutes({ [`POST /api/configurations/${V_ACTIVE}/applications`]: () => json({ code: "active_set_revision_conflict", currentRevision: 8 }, 409) });
  const failing = await mount("/detection/operations", { up: false });
  await wait(() => failing.button("운영에서 제외"), "retire button");
  await failing.click("운영에서 제외");
  await approveDialog(failing);
  await pressDialog(failing, "운영에서 제외");
  await wait(() => failing.shown().includes("active_set_revision_conflict · 운영 모델 4d2f7c10 · V4 유지"), "failure kept");
  await failing.cleanup();
}

// 3) 복원: 지금과 다른 이전 집합만 후보(빈 집합 제외). 전용 경로로 targetRevision·confirmRetire.
{
  // 지금 운영: V4(iforest) + KMeans. 버전 6 집합(V3만)으로 복원하면 V4 자리에 V3, KMeans는 빠진다.
  operational = freshState();
  operational.activeModelSet.models = [model, { configVersionId: "e8a3f1b6-2c74-49d0-96e1-7f05c4b2d913", modelKey: "minibatch_kmeans", displayName: "KMeans · K=6", activatedAtRevision: 7 }];
  routes = baseRoutes({
    "POST /api/active-models/restore": () => json({ revision: 8, previousRevision: 7, models: [{ ...model, configVersionId: V_PREVIOUS, displayName: "V3", activatedAtRevision: 8 }],
      changes: [{ type: "restored", configVersionId: V_PREVIOUS, modelKey: "iforest", previousConfigVersionId: V_ACTIVE }], changedAt: iso(0), appliesFrom: "next_decision" }),
  });
  const app = await mount("/detection/operations", { up: false });
  await wait(() => app.shown().includes("버전 6"), "history");
  await app.click("복원");
  await wait(() => openDialog(app), "restore dialog");
  const choices = elements(openDialog(app).props.children).filter((node) => node.props?.value && node.props.id?.startsWith("restore-"));
  assert.deepEqual(choices.map((node) => node.props.value), ["6"], "only a different, non-empty earlier set");
  const choiceText = elements(openDialog(app).props.children).filter((node) => node.props?.htmlFor === "restore-6").map((node) => [node.props.children].flat(Infinity).join("")).join("");
  assert.ok(choiceText.includes("현재 4d2f7c10 · V4 자리") && choiceText.includes("e8a3f1b6 · KMeans · K=6 → 제외"), choiceText);
  await approveDialog(app);
  await pressDialog(app, "복원 요청");
  await wait(() => app.shown().includes("운영 모델 2a91e6d7 · V3로 전환됨 · 이후 판정부터 2a91e6d7 · V3 사용"), "restored");
  assert.deepEqual(posts("/api/active-models/restore").at(-1).body, { operation: "restore", expectedRevision: 7, targetRevision: 6, confirmRetire: true });
  await app.cleanup();
}

// 4) 모델 교체 3단계(가짜 비교 API): 1 선택 → 2 같은 평가 세트 비교 → 3 승인 → replace 본문, 운영 설정으로 돌아가 결과 안내.
{
  operational = freshState();
  let replaceBody;
  routes = baseRoutes({
    [`POST /api/configurations/${V_EXP7}/applications`]: (_target, options) => {
      replaceBody = JSON.parse(options.body);
      return json({ revision: 8, previousRevision: 7, models: [{ ...model, configVersionId: V_EXP7, displayName: "실험 7", activatedAtRevision: 8 }],
        changes: [{ type: "replaced", configVersionId: V_EXP7, modelKey: "iforest", previousConfigVersionId: V_ACTIVE }], changedAt: iso(0), appliesFrom: "next_decision" });
    },
  });
  const app = await mount("/detection/operations/replace");
  await wait(() => app.shown().includes("현재 운영: 4d2f7c10 · V4 · Isolation Forest"), "step 1");
  assert.equal(app.button("다음 · 비교 확인").props.disabled, true, "nothing selected");
  await act(async () => app.renderer.root.findByType(RadioGroup).props.onValueChange(V_EXP7));
  await app.go("다음 · 비교 확인");
  await wait(() => app.shown().includes("평가 세트 ESB 합성 E1 · b84e6d15"), "evidence");
  assert.ok(app.shown().includes("현재 4d2f7c10 · V4") && app.shown().includes("선택 7be19a34 · 실험 7"));
  await app.go("다음 · 승인·요청");
  await wait(() => app.shown().includes("바뀌지 않음 · 운영 룰 버전 13"), "step 3");
  assert.equal(app.button("모델 교체").props.disabled, true, "approval required");
  await act(async () => app.renderer.root.findAllByType(Checkbox).find((node) => node.props.id === "replace-approve").props.onCheckedChange(true));
  await app.click("모델 교체");
  await wait(() => app.router.state.location.pathname === "/detection/operations", "back to operations");
  assert.deepEqual(replaceBody, { operation: "replace", evaluationId: EVAL7, expectedRevision: 7, expectedActiveVersionId: V_ACTIVE });
  await wait(() => app.shown().includes("운영 모델 7be19a34 · 실험 7로 전환됨"), "result banner");
  await app.cleanup();
}

// 4b) 데이터가 이미 캐시에 있는 채로 모델 버전 상세에서 '이 버전으로 교체'(step=2)로 오면 2단계에 머문다(앞 렌더의 단계 상한으로 1단계로 되돌리지 않음).
{
  operational = freshState();
  routes = baseRoutes({ [`GET /api/configurations/${V_EXP7}`]: () => json({ ...version(V_EXP7), methods: [{ ...version(V_EXP7).methods[0], parameters: null }], models: [], configurationSpec: {} }) });
  const app = await mount("/detection/operations/replace");
  await wait(() => app.shown().includes("7be19a34 · 실험 7") && app.shown().includes("b84e6d15 · 66.67%"), "warm cache");
  await act(async () => { await app.router.navigate(`/detection/models/${V_EXP7}`); });
  await wait(() => app.link("이 버전으로 교체"), "replace entry on version detail");
  const entry = app.renderer.root.findAllByType(Link).find((node) => text(node.children) === "이 버전으로 교체").props;
  assert.deepEqual([entry.to, entry.state.targetVersionId], ["/detection/operations/replace?step=2", V_EXP7]);
  await act(async () => { await app.router.navigate(entry.to, { state: entry.state }); });
  for (let i = 0; i < 10; i++) await tick();
  assert.equal(app.router.state.location.search, "?step=2", "stays on step 2 with a warm cache");
  await wait(() => app.shown().includes("평가 세트 ESB 합성 E1 · b84e6d15"), "evidence at step 2");
  await app.cleanup();
}

// 5) 추천 수락의 stale 거절은 와이어프레임 문구로, 운영 모델은 그대로.
{
  operational = freshState();
  routes = baseRoutes({ [`POST /api/configurations/${V_EXP7}/applications`]: () => json({ code: "accepted_recommendation_stale" }, 400) });
  const accepted = { comparisonJobId: "bb220000-1111-4222-8333-444455556666", modelKey: "iforest", targetVersionId: V_EXP7 };
  const app = await mount("/detection/operations/replace?step=3", { state: { acceptedRecommendation: accepted } });
  await wait(() => app.shown().includes("추천 수락") && app.shown().includes("b84e6d15 추천 7be19a34 · 실험 7") && app.button("모델 교체"), "accepted at step 3");
  await act(async () => app.renderer.root.findAllByType(Checkbox).find((node) => node.props.id === "replace-approve").props.onCheckedChange(true));
  await app.click("모델 교체");
  await wait(() => app.shown().includes("교체 거절 · 400 accepted_recommendation_stale"), "stale");
  const sent = posts(`/api/configurations/${V_EXP7}/applications`).at(-1).body;
  assert.deepEqual(sent.acceptedRecommendation, accepted);
  assert.equal(sent.evaluationId, "7be19a34-0000-4000-8000-000000000000", "the recommendation target evaluation");
  assert.equal(app.router.state.location.pathname, "/detection/operations/replace");
  await app.cleanup();
}

// 6) 비교 평가 API가 없는 서버(503): 2단계에서 막힌다(성공 대체 없음). 바로 3단계 주소는 2단계로.
{
  operational = freshState();
  routes = baseRoutes();
  requested.length = 0;
  const app = await mount("/detection/operations/replace?step=3", { up: false, state: { targetVersionId: V_EXP7 } });
  await wait(() => app.shown().includes("비교 결과 미연결 · service_unavailable"), "not connected");
  await wait(() => app.router.state.location.search === "?step=2", "normalized to step 2");
  assert.equal(app.button("다음 · 승인·요청").props.disabled, true);
  assert.equal(posts(`/api/configurations/${V_EXP7}/applications`).length, 0);
  await app.cleanup();
}

// 7) v1 서버 비교 결과(교체 추천 없음 = not_implemented_v1): 운영 설정은 추천 미연결, 교체 2단계는 같은 세트 비교로 통과해 3단계까지.
{
  operational = freshState();
  let replaceBody;
  routes = baseRoutes({
    [`POST /api/configurations/${V_EXP7}/applications`]: (_target, options) => {
      replaceBody = JSON.parse(options.body);
      return json({ revision: 8, previousRevision: 7, models: [{ ...model, configVersionId: V_EXP7, displayName: "실험 7", activatedAtRevision: 8 }],
        changes: [{ type: "replaced", configVersionId: V_EXP7, modelKey: "iforest", previousConfigVersionId: V_ACTIVE }], changedAt: iso(0), appliesFrom: "next_decision" });
    },
  });
  const v1Row = { ...sample.comparisons[1], comparisonId: sample.C_V1, jobId: "ab120000-1111-4222-8333-444455556666", recommendations: null, recommendationsReason: "not_implemented_v1" };
  const saved = sample.comparisons.splice(0, sample.comparisons.length, v1Row);
  const ops = await mount("/detection/operations");
  await wait(() => ops.shown().includes("모델 교체 추천 미연결 · not_implemented_v1"), "v1 has no recommendations");
  assert.equal(ops.link("수락하고 교체"), undefined);
  await ops.cleanup();

  const app = await mount("/detection/operations/replace?step=2", { state: { targetVersionId: V_EXP7 } });
  await wait(() => app.shown().includes(`평가 세트 원본 검증 세트 10월 · ${sample.C_V1.slice(0, 8)}`), "v1 evidence");
  assert.ok(app.shown().includes("45%") && app.shown().includes("70%"), "recall from the comparison");
  assert.ok(app.shown().includes("평가 정책구성 정책 · ml_only"), "evaluation policy shown");
  assert.ok(app.shown().includes("판정 실패 행(완료 기준)0행0행"), "ml.unavailableCount is named as failed judgments");
  assert.ok(!app.shown().includes("평가 제외 행"), "old results have no excluded-row line");
  await app.go("다음 · 승인·요청");
  await wait(() => app.shown().includes("바뀌지 않음 · 운영 룰 버전 13"), "step 3");
  assert.ok(!app.shown().includes("평가 제외 행"), "old results: no excluded-row line in step 3");
  await act(async () => app.renderer.root.findAllByType(Checkbox).find((node) => node.props.id === "replace-approve").props.onCheckedChange(true));
  await app.click("모델 교체");
  await wait(() => app.router.state.location.pathname === "/detection/operations", "replaced");
  assert.deepEqual(replaceBody, { operation: "replace", evaluationId: EVAL7, expectedRevision: 7, expectedActiveVersionId: V_ACTIVE });
  await app.cleanup();

  // 11/14 결과: 2단계 표·카드와 3단계 요약(교체 버튼 위)에 평가 제외 행.
  sample.comparisons.splice(0, sample.comparisons.length, { ...v1Row, comparisonId: sample.C_V2, jobId: "cd340000-1111-4222-8333-444455556666" });
  const v2 = await mount("/detection/operations/replace?step=2", { state: { targetVersionId: V_EXP7 } });
  await wait(() => v2.shown().includes(`평가 세트 원본 검증 세트 10월 · ${sample.C_V2.slice(0, 8)}`), "v2 evidence");
  assert.ok(v2.shown().includes("평가 제외 행3행 · 입력 변수 계산 불가(예: 처리 건수 0) 3행0행처리 시간 p95"), "excluded rows per version in the metrics table");
  await v2.go("다음 · 승인·요청");
  await wait(() => v2.shown().includes("바뀌지 않음 · 운영 룰 버전 13"), "v2 step 3");
  assert.ok(v2.shown().includes("바뀌지 않음 · 운영 룰 버전 13평가 제외 행0행"), "target's excluded rows next to the approval");
  await v2.cleanup();

  // 같은 세트 비교가 없으면 '+ 새 비교 평가'가 교체할 버전을 비교 화면으로 넘긴다.
  sample.comparisons.splice(0, sample.comparisons.length);
  const none = await mount("/detection/operations/replace?step=2", { state: { targetVersionId: V_EXP7 } });
  await wait(() => none.link("+ 새 비교 평가"), "no evidence");
  const entry = none.renderer.root.findAllByType(Link).find((node) => text(node.children) === "+ 새 비교 평가").props;
  assert.deepEqual([entry.to, entry.state], ["/detection/evaluations?step=1", { compareVersionId: V_EXP7 }]);
  await act(async () => { await none.router.navigate(entry.to, { state: entry.state }); });
  await wait(() => useDraftStore.getState().drafts["/detection/evaluations"]?.value.versionIds?.includes(V_EXP7), "version handed to the comparison draft");
  assert.equal(useDraftStore.getState().drafts["/detection/evaluations"].value.setFormOpen, true, "set form opens for the default set");
  await none.cleanup();
  sample.comparisons.splice(0, sample.comparisons.length, ...saved);
}

console.log("operations page tests passed: status badges, overview, retire, restore, replace 3 steps, stale, production block");
