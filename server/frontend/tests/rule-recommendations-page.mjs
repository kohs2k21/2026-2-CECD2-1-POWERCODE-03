import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 규칙 추천 화면 한 곳(contracts/rule-candidates-ui.md 8절, 사용자 11/15) 시험(로컬 가짜 fetch만, 합성 값):
// 머리(설명·후보 작업 요청)·탭(건수·주소 tab)·룰 설정 카드 "추천 n건 보기" 건수·뒤로가기,
// 이상 징후 탭: 처리 시간 후보 행(새 규칙·승인됨·행 승인 → 확인창 → 저장) + 기존 이상 징후 규칙 행(수식 추천) + AI 규칙 추천(룰 설정에서 옮김, 적용은 룰 설정 입력칸만),
// 실행 흐름 규칙 탭: 경로(관측 경로 초안 요약 → 프로토콜 상세) + 단계 조건(관계 후보 표·행 승인·승인됨·상세 조회 실패).
register("./svg-loader.mjs", import.meta.url);
register("data:text/javascript," + encodeURIComponent('export const load = (url, context, next) => url.endsWith(".css") ? { format: "module", source: "", shortCircuit: true } : next(url, context);'));
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
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame = (handle) => clearTimeout(handle);
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
const { RuleSaveDialog } = await import("../src/features/detection/rules/RuleDialogs.tsx");
const ruleDraft = await import("../src/features/detection/rules/ruleDraft.ts");
const labels = await import("../src/features/detection/rules/candidates/candidateLabels.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const RULES = "/detection/rules";
const HUB = "/detection/rules/candidates";
const JOB = "8cc86810-673b-4886-87b1-e026856fe636";
const hardRules = [
  { id: "hard:process-status", methodVersion: "process-status-hr-v2", displayName: "상태가 S가 아님", conditionText: "STATUS ≠ S", severity: { byCode: { PROCESS_STATUS_F: "critical" } }, enabled: true, editable: ["enabled", "severity"] },
];
const softRule = { id: "soft:elapsed_ms-3f9a2c1d", name: "처리 시간 5초 초과", field: "elapsed_ms", operator: "gt", threshold: 5000, unit: "ms", severity: "warning", enabled: true, createdAtRevision: 7, updatedAtRevision: 12 };
/** 단계 P2·HTTP 범위의 기존 규칙(처리 시간 후보 t1과 같은 조건 → 승인됨). */
const scopedRule = { ...softRule, id: "soft:elapsed_ms-0b0b0b0b", name: "P2 처리 시간", threshold: 900, scope: { processId: "P2", adapterType: "HTTP", interfaceId: "IF-A" } };
const state = (revision = 12, overrides = {}) => ({
  contractVersion: "rule-set-v1", revision, contentHash: "a".repeat(64), contentHashVersion: 3, savedAt: "2026-11-02T08:12:33Z",
  savedBy: { actorId: "1", displayName: "admin@example.test" }, restoredFromRevision: null, catalogVersion: "method-catalog-v2",
  counts: { hardEnabled: 1, hardTotal: 1, softEnabled: 1, softTotal: 1 }, hardRules, softRules: [softRule], relationRules: [], protocolRules: [], ...overrides,
});
const field = (fieldId, displayName, unit, valueType) => ({ fieldId, displayName, unit, valueType, operators: ["gte", "gt", "lte", "lt"], status: "supported" });
const capabilities = { methodCatalog: { softRuleFields: [field("retry_count", "재시도 수", "count", "integer"), field("elapsed_ms", "처리 시간", "ms", "number")] } };

const job = { id: JOB, status: "succeeded", errorCode: null, createdAt: "2026-11-12T03:00:00.000000Z", finishedAt: "2026-11-12T03:01:00.000000Z", snapshotId: "snap-1" };
const approval = { available: true, reason: null, contentHashVersion: 3 };
const counts = (applicable, satisfied) => ({ applicable, satisfied, violated: applicable - satisfied, undetermined: 0 });
const relation = (index, kind, from, to, extra = {}) => ({ candidateId: `${JOB}.r${index}`, kind, status: "candidate", approvable: true, listedForPairing: false,
  pairApprovable: kind === "message_handoff" ? true : null, interfaceId: "IF-A", scope: kind === "required_step" ? { interfaceId: "IF-A" } : { adapterType: "HTTP" },
  from: { processId: from }, to: to ? { processId: to } : null, counts: counts(1000, 999), satisfactionRate: 0.999, evaluatedAt: "successor", nonStandardProcessId: false, ...extra });
const threshold = (index, processId, extra = {}) => ({ candidateId: `${JOB}.t${index}`, field: "elapsed_ms", status: "candidate", reason: null, scope: { processId, adapterType: "HTTP" },
  interfaceIds: ["IF-A"], operator: "gt", threshold: 812.5, displayThreshold: "812.5", unit: "ms", source: "data_p95",
  evidence: { n: 120, rows: 140, referenceRows: 130, endTimeMissingReferenceRows: 10, unavailableReferenceRows: 0, p95: 812.5, min: 3, max: 990, quantileMethod: "linear" },
  limitation: null, nonStandardProcessId: false, ...extra });
const head = { job, snapshotId: "snap-1", sourceId: "s-1", period: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z", timeBasis: "persisted_at" },
  generatedAt: "2026-11-12T03:01:00.000Z", experimental: true, parameters: { minApplicable: 30, candidatePercent: 99, reviewPercent: 95, maxCounterexamples: 5 } };
const listBody = () => ({
  latestJob: job, approval,
  result: { ...head,
    interfaces: [{ interfaceId: "IF-A", transactions: 1000, relationCandidates: 3, approvable: 2, thresholdCandidates: 3 }],
    selectedInterfaceId: "IF-A", graph: { graph: {}, nodes: [], edges: [] },
    relationCandidates: [relation(0, "precedes", "P1", "P2"), relation(1, "required_step", "P1", null), relation(2, "precedes", "P1", "P3", { status: "not_candidate", approvable: false })],
    thresholdCandidates: [threshold(0, "P1"), threshold(1, "P2", { threshold: 904, displayThreshold: "904" }), threshold(2, "P3", { status: "insufficient_evidence", threshold: null, displayThreshold: null, operator: null })],
    thresholdsWithoutInterface: 0, thresholdLimitations: [],
    totals: { relationCandidates: 3, approvable: 2, thresholdCandidates: 3, thresholdApprovable: 2 } },
});
const thresholdDetail = { ...head, isLatest: true, approval, type: "threshold", threshold: threshold(0, "P1"),
  ruleDraft: { field: "elapsed_ms", operator: "gt", threshold: 812.5, unit: "ms", enabled: true, scope: { processId: "P1", adapterType: "HTTP", interfaceId: "IF-A" } } };
const relationDraft = { kind: "precedes", scope: { interfaceId: "IF-A", adapterType: "HTTP" }, from: { processId: "P1" }, to: { processId: "P2" }, match: null, enabled: true, source: { jobId: JOB } };
const relationDetail = { ...head, isLatest: true, approval, type: "relation",
  relation: { ...relation(0, "precedes", "P1", "P2"), violationReasons: {}, undeterminedReasons: {}, violationsByTransactionStatus: {} },
  counterexamples: [], pairedPrecedes: null, ruleDraft: relationDraft };
const draftJob = { ...job };
const summary = (interfaceId, extra = {}) => ({ interfaceId, status: "draft", transactions: 157, pathTransactions: 157, branchingTransactions: 6, distinctPaths: 3,
  excludedReasons: {}, endpoints: { compared: 157, mismatch: 6, notCompared: 0 }, omittedPaths: { paths: 0, transactions: 0 }, listedPaths: 3, ...extra });
const drafts = { state: "available", latestJob: draftJob, job: draftJob, snapshotId: "snap-1", sourceId: "s-1", period: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z" },
  split: "train", parameters: { minTransactions: 30, rarePercent: 1, maxPathSteps: 64, maxRoutes: 64, maxProcessRows: 200, maxOutputPaths: 64, reasonOrder: ["channel_absent"] },
  excludedReasons: {}, transactionIdMissingRows: 0, endpointMismatch: 6,
  interfaces: [summary("IF_SYN_ORDER"), summary("IF_SYN_PAY", { distinctPaths: 2, pathTransactions: 47 }), summary("IF_SYN_THIN", { status: "insufficient_evidence", distinctPaths: 1, pathTransactions: 4 })] };
const protocolEntry = { id: "hard:proto-0000000a", interfaceId: "IF_SYN_PAY", paths: [{ channels: ["CH_GW", "CH_PAY"], source: null }], severity: "critical", enabled: true, source: null, createdAtRevision: 3, updatedAtRevision: 3 };

const requested = [];
let routes = {};
let currentUser;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const method = options.method ?? "GET";
  requested.push({ path: target.pathname, search: target.search, method, body: options.body ? JSON.parse(options.body) : undefined });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  if (target.pathname === "/api/pipeline/events") return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "text/event-stream" } });
  const handler = routes[`${method} ${target.pathname}`];
  return handler ? handler(target, options) : json({ code: "unavailable" }, 503);
};
const baseRoutes = (overrides = {}) => ({
  "GET /api/rule-set": () => json(state()),
  "GET /api/rule-set/history": () => json({ items: [], pagination: { limit: 50, hasMore: false, nextCursor: null } }),
  "GET /api/jobs/capabilities": () => json(capabilities),
  "GET /api/pipeline/sources": () => json({ items: [{ id: "11111111-1111-4111-8111-111111111111", datasetKey: "ds", displayName: "출처", createdAt: "2026-10-01T00:00:00Z" }] }),
  "GET /api/rule-set/candidates": () => json(listBody()),
  "GET /api/rule-set/protocol-drafts": () => json(drafts),
  ...overrides,
});
const calls = (method, path) => requested.filter((call) => call.method === method && call.path === path);
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (entry = HUB, { keepDrafts = false } = {}) => {
  currentUser = { id: "admin-1", email: "admin@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", "token-admin");
  if (!keepDrafts) useDraftStore.getState().reset();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [entry] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))));
  });
  const shown = () => text(renderer.toJSON());
  const button = (label, index = 0) => renderer.root.findAll((node) => node.type === "button" && text(node.children) === label)[index];
  const click = async (label, index) => {
    assert.ok(button(label, index), `${label} exists`);
    assert.notEqual(button(label, index).props.disabled, true, `${label} is enabled`);
    await act(async () => button(label, index).props.onClick());
  };
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, client, shown, button, click, cleanup };
};
const byData = (app, key) => app.renderer.root.findAll((node) => node.props?.[key] !== undefined && typeof node.type === "string");
const rowOf = (app, key, id) => byData(app, key).find((node) => node.props[key] === id);
const rowButton = (row, label) => row.findAll((node) => node.type === "button" && text(node.children) === label)[0];
const pendingOf = (app) => app.renderer.root.findByType(RuleSaveDialog).props.pending;
const at = (router) => router.state.location.pathname + router.state.location.search + router.state.location.hash;
const stored = () => useDraftStore.getState().drafts[RULES];
const storedSoft = (id) => stored()?.value.soft.find((rule) => rule.id === id);
const tabLinks = (app) => app.renderer.root.findAll((node) => node.type === "nav" && node.props["aria-label"] === "규칙 추천")[0].findAllByType("a");

// 1) 머리·탭·건수, 이상 징후 탭 처리 시간 후보 행(새 규칙·승인됨), 기존 규칙 행, 뒤로가기
{
  routes = baseRoutes({ "GET /api/rule-set": () => json(state(12, { softRules: [softRule, scopedRule], counts: { hardEnabled: 1, hardTotal: 1, softEnabled: 2, softTotal: 2 } })) });
  const app = await mount({ pathname: HUB, state: { returnTo: "/detection/rules#rules-soft" } });
  await wait(() => byData(app, "data-threshold-row").length === 3 && byData(app, "data-rule").length === 2, "both kinds of rows");
  const shown = app.shown();
  assert.ok(shown.includes(labels.RECOMMEND_DESCRIPTION), "approved description sentence");
  assert.equal(labels.RECOMMEND_DESCRIPTION, "후보 → 근거(숫자는 코드, 문장은 LLM) → 승인하면 룰 세트 새 버전으로 저장");
  assert.ok(app.button(labels.JOB_REQUEST), "candidate job request lives here");
  // 탭: 밑줄 링크(주소 tab), 건수 = 처리 시간 후보(후보 상태) / 관계 승인 가능 + 경로 초안(근거 부족 제외).
  await wait(() => !tabLinks(app).some((node) => text(node.children).includes("—")), "tab counts loaded");
  assert.deepEqual(tabLinks(app).map((node) => [text(node.children), node.props.href, node.props["aria-current"] ?? null]), [
    ["이상 징후 · 2", `${HUB}?tab=anomaly`, "page"], ["실행 흐름 규칙 · 4", `${HUB}?tab=flow`, null],
  ]);
  // 한 표에 두 묶음: 처리 시간 후보 → 이상 징후 규칙.
  const table = app.renderer.root.findAll((node) => node.type === "table" && node.props["aria-label"] === "이상 징후")[0];
  assert.deepEqual(table.findAll((node) => node.type === "tr" && node.props["data-group-row"] !== undefined).map((node) => text(node.children)), ["처리 시간(이상 징후) 후보", "이상 징후 규칙"]);
  const t0 = rowOf(app, "data-threshold-row", `${JOB}.t0`);
  const t1 = rowOf(app, "data-threshold-row", `${JOB}.t1`);
  const t2 = rowOf(app, "data-threshold-row", `${JOB}.t2`);
  assert.ok(text(t0.children).includes("후보새 규칙"), "not in the rule set: new rule");
  assert.equal(rowButton(t0, "승인").props.disabled, false);
  assert.ok(text(t1.children).includes("후보승인됨") && !text(t1.children).includes("새 규칙"), "same field + scope exists: approved");
  assert.equal(rowButton(t1, "승인").props.disabled, true);
  assert.ok(!text(t2.children).includes("새 규칙") && rowButton(t2, "승인").props.disabled, "insufficient evidence: not a recommendation");
  // 근거 칸은 Trainer 결과 값만(LLM 문장 없음).
  assert.ok(text(t0.children).includes("> 812.5 ms기준 표본 p95(코드 계산)") && text(t0.children).includes("근거 건수 120 · END_TIME 없음 10"));
  // 기존 이상 징후 규칙 행: 현재 조건·임계값, 켜짐, [수식 추천].
  const r0 = rowOf(app, "data-rule", softRule.id);
  assert.deepEqual(r0.findAllByType("td").map((td) => text(td.children)), ["처리 시간 5초 초과처리 시간 · elapsed_ms", "> 5,000 ms", "—", "켜짐", "수식 추천"]);
  // 처리 시간 후보 상세로 가는 링크는 지금 주소를 돌아올 곳으로 넘긴다.
  const detailLink = t0.findAll((node) => typeof node.type !== "string" && node.props.to === `${HUB}/${JOB}.t0`)[0];
  assert.deepEqual(detailLink.props.state, { returnTo: HUB });
  // 들어온 카드로 돌아가는 뒤로가기(#193): 넘겨받은 returnTo.
  const back = app.renderer.root.findAll((node) => node.type === "a" && String(node.props["aria-label"] ?? "").startsWith("뒤로"))[0];
  assert.deepEqual([back.props.href, back.props["aria-label"]], ["/detection/rules#rules-soft", "뒤로 · 룰 설정"]);
  // 탭 이동은 주소 tab(쿼리 보존). 실행 흐름 규칙 탭 → 경로·단계 조건 구역.
  await act(async () => app.router.navigate(`${HUB}?tab=flow`));
  await wait(() => byData(app, "data-path-row").length === 3 && byData(app, "data-candidate-row").length === 3, "flow tab sections");
  assert.ok(app.shown().includes("경로(정상 프로토콜)") && app.shown().includes("단계 조건(관계 규칙)"));
  assert.ok(app.renderer.root.findAll((node) => node.props?.id === "recommend-paths").length > 0 && app.renderer.root.findAll((node) => node.props?.id === "recommend-relations").length > 0, "section anchors");
  await app.cleanup();
  // 넘겨받은 주소가 없으면(새로고침·직접 입력) 룰 설정의 규칙 추천 카드로.
  const direct = await mount(`${HUB}?tab=bogus`);
  await wait(() => at(direct.router) === `${HUB}?tab=anomaly`, "invalid tab normalized");
  const directBack = direct.renderer.root.findAll((node) => node.type === "a" && String(node.props["aria-label"] ?? "").startsWith("뒤로"))[0];
  assert.equal(directBack.props.href, "/detection/rules#rules-candidates");
  await direct.cleanup();
}

// 2) 처리 시간 후보 행 [승인]: 상세를 읽어 상세와 같은 입력창 → 서버 미리보기 확인창 → 저장(새 버전) → 다시 읽으면 승인됨
{
  let current = state(12);
  routes = baseRoutes({
    "GET /api/rule-set": () => json(current),
    [`GET /api/rule-set/candidates/${JOB}.t0`]: () => json(thresholdDetail),
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_added", id: null, clientRef: "candidate", name: "처리 시간 P1 > 812.5 ms" }], warnings: [] }),
    "PUT /api/rule-set": () => {
      current = state(13, { softRules: [softRule, { ...softRule, id: "soft:elapsed_ms-1c1c1c1c", name: "처리 시간 P1 > 812.5 ms", threshold: 812.5, scope: thresholdDetail.ruleDraft.scope }] });
      return json({ revision: 13, previousRevision: 12, contentHash: "c".repeat(64), appliedAt: "2026-11-15T08:20:05Z", appliesFrom: "next_decision", changes: [], summary: {} });
    },
  });
  const app = await mount();
  await wait(() => rowOf(app, "data-threshold-row", `${JOB}.t0`) && rowButton(rowOf(app, "data-threshold-row", `${JOB}.t0`), "승인").props.disabled === false, "approve enabled");
  await act(async () => rowButton(rowOf(app, "data-threshold-row", `${JOB}.t0`), "승인").props.onClick());
  const ask = () => app.renderer.root.findAll((node) => typeof node.props?.onNext === "function" && node.props.kind === "threshold")[0];
  await wait(() => Boolean(ask()), "approve input dialog");
  assert.equal(calls("GET", `/api/rule-set/candidates/${JOB}.t0`).length, 1, "detail read for the rule draft");
  assert.equal(ask().props.defaultName, "처리 시간 P1 > 812.5 ms", "same default name as the detail page");
  await act(async () => ask().props.onNext({ name: "처리 시간 P1 > 812.5 ms", severity: "warning" }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "server preview confirmation");
  const sent = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.equal(sent.expectedRevision, 12);
  assert.deepEqual(sent.softRules.at(-1), { id: null, clientRef: "candidate", name: "처리 시간 P1 > 812.5 ms", field: "elapsed_ms", operator: "gt", threshold: 812.5, severity: "warning", enabled: true,
    scope: thresholdDetail.ruleDraft.scope }, "current state + one new rule");
  assert.deepEqual(sent.softRules.slice(0, -1), [ruleDraft.softRequest(softRule)], "other rules unchanged");
  assert.equal(pendingOf(app).actionText, "승인하고 적용");
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13"), "saved as a new version");
  await wait(() => text(rowOf(app, "data-threshold-row", `${JOB}.t0`).children).includes("승인됨"), "approved after reload");
  assert.equal(rowButton(rowOf(app, "data-threshold-row", `${JOB}.t0`), "승인").props.disabled, true);
  await app.cleanup();
}

// 3) 이상 징후 규칙이 0개여도·룰 세트를 못 읽어도 처리 시간 후보는 보인다
{
  routes = baseRoutes({ "GET /api/rule-set": () => json(state(12, { softRules: [], counts: { hardEnabled: 1, hardTotal: 1, softEnabled: 0, softTotal: 0 } })) });
  const empty = await mount();
  await wait(() => byData(empty, "data-threshold-row").length === 3 && empty.shown().includes("이상 징후 규칙: 표시할 항목이 없습니다."), "candidates with no soft rule");
  assert.equal(empty.button("AI 규칙 추천").props.disabled, true, "nothing to re-recommend");
  await empty.cleanup();
  routes = baseRoutes({ "GET /api/rule-set": () => json({ code: "rule_set_storage_unavailable" }, 503) });
  const failed = await mount();
  await wait(() => byData(failed, "data-threshold-row").length === 3 && failed.shown().includes("현재 운영 룰 조회 실패 · rule_set_storage_unavailable"), "candidates while the rule set fails");
  assert.equal(failed.button("AI 규칙 추천"), undefined, "no AI run without the saved rule set");
  assert.ok(byData(failed, "data-threshold-row").every((row) => rowButton(row, "승인").props.disabled), "approve needs the current rule set");
  await failed.cleanup();
}

// 4) 수식 추천(규칙 행에서 옮김): 기간 선택 → 계산 → 적용은 룰 설정 입력칸(세션 초안)만 — 서버 저장 없음. 룰 설정에 들어가면 그 값으로 편집이 이어진다.
{
  let stats = () => json({ field: "elapsed_ms", statistic: "p95", operator: "gt", threshold: "7210.5", displayThreshold: "7211", unit: "ms", source: "data_p95", status: "proposed", rationale: null,
    evidence: { n: 51230, min: 12, max: 184233, p95: 7210.5, quantileMethod: "percentile_cont", scope: "period", period: { mode: "recent_1m", from: "2026-10-04T00:00:00Z", until: "2026-11-04T00:00:00Z", timeBasis: "esb_occurred_at", resolvedFrom: null } } });
  routes = baseRoutes({ "POST /api/rule-set/threshold-stats": () => stats() });
  const app = await mount();
  await wait(() => byData(app, "data-rule").length === 1, "rule rows");
  const savesBefore = calls("PUT", "/api/rule-set").length;
  await app.click("수식 추천");
  await wait(() => app.shown().includes("수식 추천 기준 기간") && app.button("계산")?.props.disabled === false, "formula row");
  const periods = app.renderer.root.findByProps({ id: `formula-period-${softRule.id}`, "data-slot": "native-select" });
  assert.deepEqual(periods.findAllByType("option").map((option) => text(option.children)), ["운영 머신러닝 모델 학습 기간 시작 ~ 현재", "최근 1개월", "최근 3개월", "직접 지정"]);
  await act(async () => periods.props.onChange({ target: { value: "recent_1m" } }));
  await app.click("계산");
  await wait(() => app.shown().includes("수식 추천 · 데이터 p95 7211 ms"), "result");
  assert.ok(app.shown().includes("표본 51,230건") && app.shown().includes("(최근 1개월) · LLM 미사용"));
  assert.deepEqual(app.renderer.root.findAll((node) => node.type === "span" && node.props["data-formula-basis"] !== undefined).map((node) => text(node.children)), ["기준 · 기간 전체 p95"]);
  const sent = calls("POST", "/api/rule-set/threshold-stats").at(-1).body;
  assert.deepEqual([sent.field, sent.statistic, sent.period], ["elapsed_ms", "p95", { mode: "recent_1m", timeBasis: "persisted_at" }]);
  assert.equal(Object.hasOwn(sent, "scope"), false, "a global rule sends no scope (request as before)");
  // (10차) 범위 있는 규칙은 판정 키만 scope로 보낸다(표시용 interfaceId 제외).
  const { thresholdStatsScope } = await import("../src/features/detection/rules/ruleSetApi.ts");
  assert.deepEqual(thresholdStatsScope(scopedRule.scope), { processId: "P2", adapterType: "HTTP" });
  assert.deepEqual([thresholdStatsScope(null), thresholdStatsScope(undefined), thresholdStatsScope({ processId: "P1", processHubId: "H" })], [undefined, undefined, { processId: "P1", processHubId: "H" }]);
  assert.ok(app.renderer.root.find((node) => node.type === "strong" && node.props.title === "원값 7210.5"), "raw value tooltip");
  await app.click("적용");
  assert.equal(storedSoft(softRule.id).threshold, "7210.5", "apply fills the rules draft input");
  assert.equal(stored().dirty, true);
  const cell = rowOf(app, "data-rule", softRule.id).findAll((node) => node.type === "td" && node.props["data-rule-threshold"] !== undefined)[0];
  assert.ok(text(cell.children) === "> 7,210.5 ms" && cell.props.className.includes("bg-warning-soft"), "the row shows the draft value as changed");
  assert.equal(calls("PUT", "/api/rule-set").length, savesBefore, "apply does not save");
  await app.click("수식 추천");
  await wait(() => app.button("계산")?.props.disabled === false, "formula row reopened");
  stats = () => json({ field: "elapsed_ms", statistic: "p95", operator: null, threshold: null, displayThreshold: null, unit: "ms", source: "data_p95", status: "no_applicable", rationale: null,
    evidence: { n: 3, min: 1, max: 2, p95: 2, period: { mode: "active_model_training_start", from: "2026-10-04T00:00:00Z", until: "2026-11-04T00:00:00Z" } } });
  await app.click("계산");
  await wait(() => app.shown().includes("해당 없음"), "no_applicable");
  assert.deepEqual(calls("POST", "/api/rule-set/threshold-stats").at(-1).body.period, { mode: "active_model_training_start" }, "server picks the basis for the model period");
  assert.ok(app.shown().includes("최소 기준 미달") && app.shown().includes("표본 3건"));
  assert.equal(app.button("적용"), undefined, "nothing to apply");
  await app.click("다시 시도");
  stats = () => json({ code: "active_model_missing" }, 422);
  await wait(() => app.button("계산")?.props.disabled === false, "back to the period choice");
  await app.click("계산");
  await wait(() => app.shown().includes("수식 추천 실패 · active_model_missing · 입력칸은 그대로입니다."), "error code shown");
  assert.equal(storedSoft(softRule.id).threshold, "7210.5", "failure keeps the draft");
  // 룰 설정으로 가면 채운 값으로 편집이 이어지고 저장 줄에 변경이 보인다(저장은 거기서).
  await act(async () => app.router.navigate(RULES));
  await wait(() => app.shown().includes("변경 1건"), "rules page continues the draft");
  assert.equal(app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${softRule.name} 임계값`)[0].props.value, "7210.5");
  await app.cleanup();
}

// 4b) 수식 추천 직접 지정: 두 시각을 넣어야 계산, 화면 입력이 오프셋 있는 시각 + 저장 시각 기준으로 요청에 들어간다. 최근 3개월도 같은 기준.
{
  const stats = () => json({ field: "elapsed_ms", statistic: "p95", operator: "gt", threshold: "10", displayThreshold: "10", unit: "ms", source: "data_p95", status: "proposed", rationale: null,
    evidence: { n: 30, min: 1, max: 20, p95: 10, period: { mode: "custom", from: "2026-10-01T00:00:00Z", until: "2026-10-02T00:00:00Z" } } });
  routes = baseRoutes({ "POST /api/rule-set/threshold-stats": () => stats() });
  const app = await mount();
  await wait(() => byData(app, "data-rule").length === 1, "rule rows");
  await app.click("수식 추천");
  await wait(() => app.button("계산")?.props.disabled === false, "formula row");
  const select = () => app.renderer.root.findByProps({ id: `formula-period-${softRule.id}`, "data-slot": "native-select" });
  const dateInput = (label) => app.renderer.root.findByProps({ "aria-label": label, "data-slot": "input" });
  await act(async () => select().props.onChange({ target: { value: "custom" } }));
  assert.equal(app.button("계산").props.disabled, true, "both times are required");
  await act(async () => dateInput("시작").props.onChange({ target: { value: "2026-10-01T09:00" } }));
  assert.equal(app.button("계산").props.disabled, true);
  await act(async () => dateInput("종료").props.onChange({ target: { value: "2026-10-02T09:30" } }));
  await app.click("계산");
  await wait(() => app.shown().includes("수식 추천 · 데이터 p95 10 ms"), "custom result");
  assert.deepEqual(calls("POST", "/api/rule-set/threshold-stats").at(-1).body.period, {
    mode: "custom", from: new Date("2026-10-01T09:00").toISOString(), until: new Date("2026-10-02T09:30").toISOString(), timeBasis: "persisted_at",
  });
  await app.click("닫기");
  await app.click("수식 추천");
  await wait(() => app.button("계산")?.props.disabled === false, "formula row reopened");
  await act(async () => select().props.onChange({ target: { value: "recent_3m" } }));
  await app.click("계산");
  await wait(() => calls("POST", "/api/rule-set/threshold-stats").at(-1).body.period.mode === "recent_3m", "recent 3 months requested");
  assert.deepEqual(calls("POST", "/api/rule-set/threshold-stats").at(-1).body.period, { mode: "recent_3m", timeBasis: "persisted_at" });
  assert.equal(stored(), undefined, "nothing applied: no draft");
  await app.cleanup();
}

// 5) AI 규칙 추천(룰 설정 이상 징후 규칙 머리에서 옮김): 계약 경로 요청, softRuleIds로만 연결, 제안만 선택, 적용은 룰 설정 입력칸(초안)만, 결과 검증,
// 작업 실패와 조회 실패 구분, 버전 변경, 서버 거절, 진행률, LLM 꺼짐.
{
  const { readFile } = await import("node:fs/promises");
  const doc = await readFile(new URL("../../contracts/llm-recommendation-job.md", import.meta.url), "utf8");
  const contractResult = JSON.parse(doc.slice(doc.indexOf("## 결과 JSON")).split("```json")[1].split("```")[0]);
  const v3Result = JSON.parse(doc.slice(doc.indexOf("### 결과 추가 필드")).split("```json")[1].split("```")[0]);
  const { basisLabel, heldWindowExcludedRows, parseRecommendation, progressText, recommendationProgress } = await import("../src/features/detection/rules/ruleAiApi.ts");
  assert.deepEqual([basisLabel("train_reference_80_p95"), basisLabel("train_p95"), basisLabel(undefined), basisLabel("other"), basisLabel("toString")],
    ["기준 · 학습 기간 앞 80% p95", null, null, null, null], "only the Rule v3 basis has a label");
  assert.deepEqual(recommendationProgress({ progress: { processed: 0, total: 3 } }), { processed: 0, total: 3 });
  for (const invalid of [null, {}, { progress: null }, { progress: { processed: 1 } }, { progress: { processed: 4, total: 3 } }, { progress: { processed: -1, total: 3 } },
    { progress: { processed: 0, total: 0 } }, { progress: { processed: 1.5, total: 3 } }, { progress: { processed: "1", total: 3 } }]) {
    assert.equal(recommendationProgress(invalid), null, JSON.stringify(invalid));
  }
  assert.equal(progressText({ processed: 2, total: 6 }), "처리 2/6", "approved progress wording (user 11/14)");
  assert.equal(heldWindowExcludedRows(v3Result), 9370);
  for (const hidden of [contractResult, {}, null, { heldWindowExclusion: null }, { heldWindowExclusion: { excludedRows: -1 } }, { heldWindowExclusion: { excludedRows: "9370" } }, { heldWindowExclusion: [] }]) {
    assert.equal(heldWindowExcludedRows(hidden), null, JSON.stringify(hidden));
  }

  const retryRule = { ...softRule, id: "soft:retry_count-91ab77e0", name: "재시도 3회 이상", field: "retry_count", operator: "gte", threshold: 3, unit: "count", severity: "info" };
  const offRule = { ...softRule, id: "soft:error_ratio-0000aaaa", name: "꺼진 비율 규칙", field: "error_ratio", threshold: 0.02, unit: "ratio", enabled: false };
  const noResultRule = { ...softRule, id: "soft:success_count-11112222", name: "결과 없는 규칙", field: "success_count", operator: "lt", threshold: 2, unit: "count" };
  const ratioRule = { ...softRule, id: "soft:error_ratio-5d2e0b19", name: "오류 비율 규칙", field: "error_ratio", threshold: 0.01, unit: "ratio" };
  const totalRule = { ...softRule, id: "soft:total_count-0a1b2c3d", name: "총 건수 규칙", field: "total_count", threshold: 10000, unit: "count" };
  const sameFieldRule = { ...softRule, id: "soft:elapsed_ms-99999999", name: "같은 필드 다른 규칙", threshold: 9000 };
  const current = state(12, { softRules: [softRule, ratioRule, retryRule, totalRule, offRule, noResultRule, sameFieldRule], counts: { hardEnabled: 1, hardTotal: 1, softEnabled: 6, softTotal: 7 } });
  assert.ok(parseRecommendation(contractResult, current), "contract example passes validation");
  assert.equal(parseRecommendation(contractResult, state(12, { softRules: [softRule] })), null, "ids are not aligned by field: another rule's id fails validation");

  const jobs = new Map();
  let nextJob = 0;
  const addJob = (body) => {
    const id = `job-${++nextJob}`;
    jobs.set(id, body);
    return id;
  };
  const succeeded = (resultSummary) => () => json({ kind: "recommend_thresholds", status: "succeeded", inputSpec: {}, resultSummary });
  const failedJob = (errorCode) => () => json({ kind: "recommend_thresholds", status: "failed", inputSpec: {}, errorCode });
  let jobResponse = succeeded(contractResult);
  routes = baseRoutes({
    "GET /api/rule-set": () => json(current),
    "POST /api/jobs": () => json({ id: addJob(jobResponse), kind: "recommend_thresholds", status: "queued", inputSpec: {} }, 202),
  });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const target = new URL(String(url), "http://local.test");
    const match = /^\/api\/jobs\/(job-\d+)$/.exec(target.pathname);
    if (!match) return realFetch(url, options);
    requested.push({ path: target.pathname, method: "GET" });
    return jobs.get(match[1])();
  };
  const jobCalls = () => calls("POST", "/api/jobs");
  const before = jobCalls().length;
  const app = await mount();
  await wait(() => app.button("AI 규칙 추천")?.props.disabled === false, "button enabled");
  const box = (name) => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-rule-ai"] === name)[0];
  const thresholdOf = (rule) => (storedSoft(rule.id) ?? { threshold: String(rule.threshold) }).threshold;
  await app.click("AI 규칙 추천");
  await wait(() => box("done"), "result table");
  const sent = jobCalls().at(-1).body;
  assert.equal(jobCalls().length, before + 1);
  assert.deepEqual([sent.kind, sent.ruleSetRevision, sent.scope, sent.mode, sent.data.snapshotId, sent.data.timeBasis],
    ["recommend_thresholds", 12, { softRuleIds: null }, "data_p95_with_llm_rationale", null, "persisted_at"]);
  assert.ok(Date.parse(sent.data.from) < Date.parse(sent.data.until) && Date.parse(sent.data.until) - Date.parse(sent.data.from) <= 92 * 86400000);
  const rows = () => app.renderer.root.findAll((node) => node.type === "tr" && node.props["data-ai-row"] !== undefined);
  const row = (id) => rows().find((node) => node.props["data-ai-row"] === id);
  assert.deepEqual(rows().map((node) => node.props["data-ai-row"]), [softRule.id, ratioRule.id, retryRule.id, totalRule.id], "rows are the enabled rules listed in softRuleIds");
  assert.ok(text(box("done").children).includes("켜진 이상 징후 규칙 4개 · 제안 2건"));
  assert.ok(!text(box("done").children).includes("보류 기간 제외"), "no held-window line when heldWindowExclusion is null");
  assert.deepEqual(box("done").findAll((node) => node.type === "th").map((node) => text(node.children)), ["선택", "규칙(대상 필드)", "임계값", "상태", "근거"], "five columns");
  const proposed = text(row(softRule.id).children);
  assert.ok(proposed.includes("> 5,000 ms → > 7,211 ms"), proposed);
  assert.ok(proposed.includes("데이터 p95 · 표본 46,980 · min 12 · max 184,233 · p95 7,210.5"), "source leads the evidence line");
  assert.ok(proposed.includes("The 95th percentile of 46,980 successful process durations"), "rationale stays as returned");
  assert.equal(box("done").findAll((node) => node.props["data-ai-basis"] !== undefined).length, 0, "train_p95 shows no basis line");
  for (const hidden of ["m_…", "b_…", "linear_(n-1)*p", "explicit anomaly labels", "reviews", "rule_v2"]) assert.ok(!text(box("done").children).includes(hidden), `${hidden} is not on the screen`);
  assert.ok(text(row(retryRule.id).children).includes("해당 없음") && text(row(retryRule.id).children).includes("≥ 3 count") && !text(row(retryRule.id).children).includes("→"));
  assert.ok(text(row(totalRule.id).children).includes("거부") && text(row(totalRule.id).children).includes("invalid_generation_output"));
  const checkbox = (id) => row(id).findAll((node) => node.props["aria-label"]?.endsWith(" 선택") && node.props.onCheckedChange)[0];
  assert.deepEqual([softRule, ratioRule, retryRule, totalRule].map((rule) => checkbox(rule.id).props.disabled), [false, false, true, true], "only proposed rows are selectable");
  await act(async () => checkbox(softRule.id).props.onCheckedChange(false));
  await act(async () => checkbox(ratioRule.id).props.onCheckedChange(false));
  assert.equal(app.button("적용").props.disabled, true);
  await act(async () => checkbox(softRule.id).props.onCheckedChange(true));
  const saves = calls("PUT", "/api/rule-set").length;
  await app.click("적용");
  // 적용 = 룰 설정 입력칸(세션 초안)만. 고른 규칙만 바뀌고 위험도는 그대로, 서버 저장 없음.
  assert.equal(thresholdOf(softRule), "7210.5");
  assert.equal(storedSoft(softRule.id).operator, "gt");
  assert.equal(thresholdOf(sameFieldRule), "9000", "same field but not listed: untouched");
  assert.equal(thresholdOf(retryRule), "3", "not proposed: untouched");
  assert.equal(thresholdOf(offRule), "0.02", "off rule untouched");
  assert.equal(thresholdOf(noResultRule), "2", "rule without a result untouched");
  assert.equal(thresholdOf(ratioRule), "0.01", "unselected proposal untouched");
  assert.equal(ruleDraft.draftChangeCount(stored().value), 1, "one change in the rules draft");
  assert.equal(storedSoft(softRule.id).severity, "warning", "severity stays with the person");
  assert.ok(text(box("applied").children).includes(`1건을 입력칸에 채움(${softRule.name}) · 저장해야 운영에 반영됩니다`));
  assert.equal(calls("PUT", "/api/rule-set").length, saves, "apply does not save");
  // 미저장 변경(룰 설정 초안)이 있으면 저장된 버전 기준 작업을 시작하지 않는다.
  assert.equal(app.button("AI 규칙 추천").props.disabled, true);
  assert.ok(app.shown().includes("저장 후 추천"));
  await app.click("닫기");
  await act(async () => useDraftStore.getState().discard(RULES));
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "clean draft can run again");

  // 작업 실패: 전체 실패 표시, 입력칸 그대로, 다시 시도는 새 작업. 시간 초과는 따로.
  jobResponse = failedJob("generation_service_unavailable");
  await app.click("AI 규칙 추천");
  await wait(() => box("failed"), "job failure");
  assert.ok(text(box("failed").children).includes("추천 실패 · generation_service_unavailable · 작업 전체가 실패해 부분 결과가 없습니다. 입력칸은 그대로입니다."));
  assert.equal(rows().length, 0, "no partial result");
  assert.equal(stored(), undefined);
  jobResponse = failedJob("generation_timeout");
  const postsBeforeRetry = jobCalls().length;
  await app.click("다시 시도");
  await wait(() => box("failed") && text(box("failed").children).includes("시간 초과 · generation_timeout"), "timeout is separate");
  assert.equal(jobCalls().length, postsBeforeRetry + 1, "retry after a failed job is a new request");

  // 결과가 계약과 다르면 실패로 끝나고 페이지 오류가 나지 않는다.
  const validItem = contractResult.items[0];
  for (const invalid of [{ ...contractResult, items: [] }, null, { ...contractResult, items: [{ ...validItem, status: "unknown" }] }, { ...contractResult, items: [{ ...validItem, softRuleIds: [offRule.id] }] }]) {
    assert.equal(parseRecommendation(invalid, current), null);
    jobResponse = succeeded(invalid);
    const posts = jobCalls().length;
    await app.click("다시 시도");
    await wait(() => jobCalls().length === posts + 1 && box("failed") && text(box("failed").children).includes("recommendation_result_invalid"), "invalid result fails");
    assert.equal(rows().length, 0);
  }

  // 충돌: 재조회로 버전이 바뀌어도 실패 표시가 남고, 다시 시도는 새 버전으로 요청한다.
  const acceptRoute = routes["POST /api/jobs"];
  const ruleSetReads = calls("GET", "/api/rule-set").length;
  routes["POST /api/jobs"] = () => {
    routes["GET /api/rule-set"] = () => json({ ...current, revision: 14 });
    return json({ code: "rule_set_revision_conflict", currentRevision: 14 }, 409);
  };
  await app.click("다시 시도");
  await wait(() => calls("GET", "/api/rule-set").length > ruleSetReads, "conflict reloads the rule set");
  await tick(30);
  assert.ok(box("failed") && text(box("failed").children).includes("추천 실패 · rule_set_revision_conflict"), "failure stays after the revision changes");
  routes["POST /api/jobs"] = acceptRoute;
  jobResponse = succeeded(contractResult);
  await app.click("다시 시도");
  await wait(() => box("done"), "result after conflict");
  assert.equal(jobCalls().at(-1).body.ruleSetRevision, 14, "retry uses the new revision");
  // 저장(다른 화면·승인)으로 버전이 바뀌면 이전 버전 기준 결과는 닫힌다.
  routes["GET /api/rule-set"] = () => json({ ...current, revision: 15 });
  await act(async () => app.client.invalidateQueries());
  await wait(() => !box("done"), "results of the previous revision are closed");

  // 진행 중은 5초마다 조회. 조회 실패는 작업 실패가 아니고 조회가 멈추며, 다시 시도는 같은 작업을 읽는다.
  let reads = 0;
  jobResponse = () => {
    reads += 1;
    if (reads === 1) return json({ kind: "recommend_thresholds", status: "running", inputSpec: {}, resultSummary: { progress: { processed: 2, total: 6 } } });
    if (reads === 2) return json({ code: "job_storage_unavailable" }, 503);
    return succeeded(contractResult)();
  };
  const postsBeforePoll = jobCalls().length;
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "ready");
  await app.click("AI 규칙 추천");
  await wait(() => reads === 1 && box("running"), "job is running");
  assert.ok(app.shown().includes("AI 규칙 추천 실행 중…"));
  assert.ok(text(box("running").children).includes("켜진 이상 징후 규칙 6개"));
  const bar = () => box("running").findAll((node) => node.type === "div" && node.props.role === "progressbar")[0];
  await wait(() => bar()?.props["aria-valuenow"] === 2, "progress value from the running job");
  assert.deepEqual([bar().props["aria-valuenow"], bar().props["aria-valuemax"], bar().props["aria-label"]], [2, 6, "AI 규칙 추천 실행 중"]);
  assert.deepEqual(box("running").findAll((node) => node.type === "span" && node.props["data-ai-progress"] !== undefined).map((node) => text(node.children)), ["처리 2/6"]);
  await wait(() => box("readFailed"), "a read failure after the 5 second poll", 800);
  assert.ok(text(box("readFailed").children).includes("AI 규칙 추천 조회 실패 · job_storage_unavailable"));
  assert.equal(app.button("AI 규칙 추천").props.disabled, true, "no new job while the old one may be alive");
  await app.click("다시 시도");
  await wait(() => box("done"), "same job is read again");
  assert.equal(reads, 3);
  assert.equal(jobCalls().length, postsBeforePoll + 1, "retry of a read creates no new job");

  // Rule v3 결과: 기준 문구·한국어 근거·보류 기간 제외. Rule v3 실패 코드는 기존 형식 그대로.
  await app.click("닫기");
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "closed before rule v3");
  jobResponse = succeeded(v3Result);
  await app.click("AI 규칙 추천");
  await wait(() => box("done"), "rule v3 result");
  assert.deepEqual(row(softRule.id).findAll((node) => node.type === "span" && node.props["data-ai-basis"] !== undefined).map((node) => text(node.children)), ["기준 · 학습 기간 앞 80% p95"]);
  assert.ok(text(row(softRule.id).children).includes("참조 기간 유효 값 30100개의 p95보다 큰"));
  assert.deepEqual(box("done").findAll((node) => node.type === "span" && node.props["data-ai-held"] !== undefined).map((node) => text(node.children)), ["보류 기간 제외 · 9,370행"]);
  for (const code of ["rule_v3_not_configured", "rule_v3_model_failure"]) {
    await app.click("닫기");
    await wait(() => app.button("AI 규칙 추천").props.disabled === false, `closed before ${code}`);
    jobResponse = failedJob(code);
    await app.click("AI 규칙 추천");
    await wait(() => box("failed") && text(box("failed").children).includes(`추천 실패 · ${code} · 작업 전체가 실패해 부분 결과가 없습니다.`), code);
  }
  // 서버 거절: 코드 그대로.
  routes["POST /api/jobs"] = () => json({ code: "invalid_job_request" }, 400);
  await app.click("닫기");
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "closed");
  await app.click("AI 규칙 추천");
  await wait(() => box("failed") && text(box("failed").children).includes("추천 실패 · invalid_job_request"), "server rejection shown");
  await app.cleanup();
  globalThis.fetch = realFetch;

  // 출처 조회 실패·출처 없음은 이유를 표시한다.
  routes = baseRoutes({ "GET /api/pipeline/sources": () => json({ code: "sources_unavailable" }, 503) });
  const noRead = await mount();
  await wait(() => noRead.shown().includes("출처 조회 실패 · sources_unavailable"), "source read failure reason");
  assert.equal(noRead.button("AI 규칙 추천").props.disabled, true);
  await noRead.cleanup();
  routes = baseRoutes({ "GET /api/pipeline/sources": () => json({ items: [] }) });
  const noSource = await mount();
  await wait(() => noSource.shown().includes("저장된 데이터 출처가 없습니다."), "no source reason");
  assert.equal(noSource.button("AI 규칙 추천").props.disabled, true);
  await noSource.cleanup();
  // LLM 생성 서비스 꺼짐: 버튼 비활성 + 기존 표기 `AI 규칙 추천 · <코드>`. 수식 추천(LLM 없음)은 막지 않는다.
  routes = baseRoutes({ "GET /api/llm/status": () => json({ available: false, checkedAt: "2026-11-14T01:00:00.000Z", errorCode: "generation_service_unavailable" }) });
  const postsBefore = jobCalls().length;
  const off = await mount();
  await wait(() => off.shown().includes("AI 규칙 추천 · generation_service_unavailable"), "unavailable code shown");
  assert.equal(off.button("AI 규칙 추천").props.disabled, true);
  assert.notEqual(off.button("수식 추천").props.disabled, true, "formula recommendation is not gated");
  assert.equal(jobCalls().length, postsBefore, "no job request");
  await off.cleanup();
}

// 5-1) AI 규칙 추천 + 처리 시간 후보(llm-recommendation-job.md 10차): 이상 징후 규칙 0개여도 지금 보이는 후보(candidate 상태)로 요청,
// 후보 결과 snapshot으로 계산, 후보만 있는 항목(softRuleIds 빈 배열)을 받음, 후보 행의 두 기준 표시, [승인] 저장 값(AI 제안이면 AI 값, 아니면 Trainer 값).
{
  const { parseRecommendation, candidateTargets } = await import("../src/features/detection/rules/ruleAiApi.ts");
  const SOURCE_ID = "11111111-1111-4111-8111-111111111111";
  const t0 = `${JOB}.t0`, t1 = `${JOB}.t1`, t2 = `${JOB}.t2`;
  const aiItem = (scope, candidateIds, extra) => ({ field: "elapsed_ms", scope, softRuleIds: [], candidateIds, currentThreshold: null, unit: "ms", source: "data_p95",
    basis: "train_reference_80_p95", attempts: 1, aiProposal: null, reviews: { schema: true, facts: true, expectedTarget: true },
    evidence: { n: 96, min: 3, max: 990, p95: 790, quantileMethod: "linear_(n-1)*p", currentN: 24 }, ...extra });
  const result = { recommendationContractVersion: "threshold-recommendation-v1", provider: "rule_v3", heldWindowExclusion: null,
    items: [
      aiItem({ processId: "P1", adapterType: "HTTP" }, [t0], { operator: "gt", threshold: "790", displayThreshold: "790", status: "proposed", rationale: "참조 기간 유효 값 96개의 p95보다 큰 처리 시간을 경고 후보로 봅니다." }),
      aiItem({ processId: "P2", adapterType: "HTTP" }, [t1], { operator: null, threshold: null, displayThreshold: null, status: "no_applicable", attempts: 0,
        rationale: "현재 기간에 이 범위의 유효 값이 없어 비교할 수 없습니다." }),
    ],
    counts: { targets: 2, proposed: 1, requestEvidence: 0, deferred: 0, noApplicable: 1, rejected: 0 } };
  const empty = state(12, { softRules: [], counts: { hardEnabled: 1, hardTotal: 1, softEnabled: 0, softTotal: 0 } });
  // 결과 검증: 후보만 있는 항목은 요청한 후보일 때만 통과, 규칙도 후보도 없는 항목은 실패.
  assert.ok(parseRecommendation(result, empty, [t0, t1]), "candidate-only items pass when the candidates were requested");
  assert.equal(parseRecommendation(result, empty, [t0]), null, "a candidate that was not requested fails");
  assert.equal(parseRecommendation(result, empty), null, "candidate-only items fail when no candidate was requested");
  assert.equal(parseRecommendation({ ...result, items: [{ ...result.items[0], candidateIds: [] }] }, empty, [t0]), null, "an item with neither rules nor candidates fails");
  assert.equal(parseRecommendation({ ...result, items: [{ ...result.items[0], candidateIds: [1] }] }, empty, [t0]), null);
  assert.equal(parseRecommendation({ ...result, items: [{ ...result.items[0], scope: { processId: 1 } }] }, empty, [t0]), null);
  // 보낼 후보: candidate 상태 + 제안 값 있음, 후보 결과 snapshot이 있고 고른 출처와 같을 때만.
  const list = { ...listBody().result, sourceId: SOURCE_ID };
  assert.deepEqual(candidateTargets(list, SOURCE_ID)?.items.map((item) => item.candidateId), [t0, t1]);
  assert.equal(candidateTargets({ ...list, sourceId: "other" }, SOURCE_ID), null, "another source's candidates are not sent");
  assert.equal(candidateTargets({ ...list, snapshotId: null }, SOURCE_ID), null);
  assert.equal(candidateTargets({ ...list, thresholdCandidates: [list.thresholdCandidates[2]] }, SOURCE_ID), null, "no eligible candidate");

  let current = empty;
  const detail = (index, processId, extra) => ({ ...thresholdDetail, threshold: threshold(index, processId, extra),
    ruleDraft: { field: "elapsed_ms", operator: "gt", threshold: extra?.threshold ?? 812.5, unit: "ms", enabled: true, scope: { processId, adapterType: "HTTP", interfaceId: "IF-A" } } });
  routes = baseRoutes({
    "GET /api/rule-set": () => json(current),
    "GET /api/rule-set/candidates": () => json({ ...listBody(), result: list }),
    "POST /api/jobs": () => json({ id: "job-c1", kind: "recommend_thresholds", status: "queued", inputSpec: {} }, 202),
    "GET /api/jobs/job-c1": () => json({ kind: "recommend_thresholds", status: "succeeded", inputSpec: {}, resultSummary: result }),
    [`GET /api/rule-set/candidates/${t0}`]: () => json(detail(0, "P1")),
    [`GET /api/rule-set/candidates/${t1}`]: () => json(detail(1, "P2", { threshold: 904, displayThreshold: "904" })),
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_added", id: null, clientRef: "candidate" }], warnings: [] }),
    "PUT /api/rule-set": (_target, options) => {
      const body = JSON.parse(options.body);
      const revision = current.revision + 1;
      current = state(revision, { softRules: body.softRules.map((rule, index) => ({ ...rule, id: rule.id ?? `soft:elapsed_ms-0000000${index}`, unit: "ms", createdAtRevision: revision, updatedAtRevision: revision })),
        counts: { hardEnabled: 1, hardTotal: 1, softEnabled: body.softRules.length, softTotal: body.softRules.length } });
      return json({ revision, contentHash: "e".repeat(64), appliedAt: "x", changes: [] });
    },
  });
  const app = await mount();
  await wait(() => byData(app, "data-threshold-row").length === 3 && app.button("AI 규칙 추천")?.props.disabled === false, "enabled with no anomaly-sign rule");
  const box = () => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-rule-ai"] === "done")[0];
  await app.click("AI 규칙 추천");
  await wait(() => box(), "candidate result");
  const sent = calls("POST", "/api/jobs").at(-1).body;
  assert.deepEqual([sent.scope, sent.data, sent.ruleSetRevision], [{ softRuleIds: null, candidateIds: [t0, t1] }, { snapshotId: "snap-1", from: null, until: null, timeBasis: null }, 12],
    "visible candidate-status candidates, computed on the candidate result snapshot");
  assert.ok(text(box().children).includes("켜진 이상 징후 규칙 0개 · 처리 시간(이상 징후) 후보 2개 · 제안 1건"), text(box().children));
  assert.deepEqual(box().findAll((node) => node.type === "tr" && node.props["data-ai-group"] !== undefined).map((node) => text(node.children)), ["처리 시간(이상 징후) 후보"]);
  const aiRow = (id) => rowOf(app, "data-ai-candidate-row", id);
  const first = text(aiRow(t0).children);
  for (const part of ["P1", "HTTP · 처리 시간 · elapsed_ms", "> 812.5 ms", "기준 · 학습 기간 전체 p95", "→ > 790 ms", "기준 · 학습 기간 앞 80% p95", "제안", "참조 기간 유효 값 96개"]) {
    assert.ok(first.includes(part), `${part} in ${first}`);
  }
  const second = text(aiRow(t1).children);
  assert.ok(second.includes("해당 없음") && second.includes("> 904 ms") && second.includes("기준 · 학습 기간 전체 p95") && !second.includes("→"), second);
  assert.ok(second.includes("현재 기간에 이 범위의 유효 값이 없어 비교할 수 없습니다."));
  assert.equal(aiRow(t2), undefined, "not-eligible candidates are not sent");
  assert.equal(app.button("적용"), undefined, "no rule rows, nothing to apply");
  // AI 제안 → AI 값으로 승인(기준 · 학습 기간 앞 80% p95).
  const ask = () => app.renderer.root.findAll((node) => typeof node.props?.onNext === "function" && node.props.kind === "threshold")[0];
  await act(async () => rowButton(aiRow(t0), "승인").props.onClick());
  await wait(() => Boolean(ask()), "approve dialog");
  assert.deepEqual([ask().props.defaultName, ask().props.note], ["처리 시간 P1 > 790 ms", "임계값 > 790 ms · 기준 · 학습 기간 앞 80% p95"]);
  await act(async () => ask().props.onNext({ name: "P1 처리 시간", severity: "warning" }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "approve confirm");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.softRules,
    [{ id: null, clientRef: "candidate", name: "P1 처리 시간", field: "elapsed_ms", operator: "gt", threshold: 790, severity: "warning", enabled: true, scope: { processId: "P1", adapterType: "HTTP", interfaceId: "IF-A" } }]);
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13"), "saved");
  // 승인 1건마다 새 버전 — 후보 결과는 닫히지 않고, 승인한 후보는 승인됨.
  await wait(() => aiRow(t0) && rowButton(aiRow(t0), "승인").props.disabled === true, "approved candidate is off");
  assert.ok(box(), "the candidate result stays after the version changes");
  assert.equal(aiRow(t0).findAll((node) => node.props["data-approved"] !== undefined && typeof node.type === "string").length, 1);
  // 제안이 아니면 Trainer 값으로 승인(기준 · 학습 기간 전체 p95, 지금 후보 승인 흐름과 같음).
  await act(async () => rowButton(aiRow(t1), "승인").props.onClick());
  await wait(() => Boolean(ask()), "second approve dialog");
  assert.deepEqual([ask().props.defaultName, ask().props.note], ["처리 시간 P2 > 904 ms", "임계값 > 904 ms · 기준 · 학습 기간 전체 p95"]);
  await act(async () => ask().props.onNext({ name: "P2 처리 시간", severity: "warning" }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "second confirm");
  const added = calls("POST", "/api/rule-set/preview").at(-1).body.softRules.at(-1);
  assert.deepEqual([added.threshold, added.operator, added.scope], [904, "gt", { processId: "P2", adapterType: "HTTP", interfaceId: "IF-A" }]);
  await act(async () => pendingOf(app).run(false));
  await app.cleanup();
  // 후보 결과가 다른 출처면 후보를 보내지 않고, 규칙도 없으니 버튼은 꺼진다.
  current = empty;
  routes["GET /api/rule-set/candidates"] = () => json(listBody());
  const other = await mount();
  await wait(() => byData(other, "data-threshold-row").length === 3 && other.button("AI 규칙 추천"), "loaded");
  await tick(30);
  assert.equal(other.button("AI 규칙 추천").props.disabled, true, "no target");
  await other.cleanup();
}

// 6) 실행 흐름 규칙 탭: 경로(관측 경로 초안 요약 + 등록 여부 → 프로토콜 상세), 단계 조건 행 [승인] → 저장, 이미 같은 조건이면 승인됨, 상세 조회 실패 코드
{
  let current = state(30, { protocolRules: [protocolEntry] });
  routes = baseRoutes({
    "GET /api/rule-set": () => json(current),
    [`GET /api/rule-set/candidates/${JOB}.r0`]: () => json(relationDetail),
    [`GET /api/rule-set/candidates/${JOB}.r1`]: () => json({ code: "rule_candidate_not_found" }, 404),
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "relation_added", id: null, clientRef: "candidate", kind: "precedes", interfaceId: "IF-A", from: "P1", to: "P2" }], warnings: [] }),
    "PUT /api/rule-set": () => {
      current = state(31, { protocolRules: [protocolEntry], relationRules: [{ ...relationDraft, id: "hard:rel-0000000c", severity: "critical", createdAtRevision: 31, updatedAtRevision: 31 }] });
      return json({ revision: 31, contentHash: "d".repeat(64), appliedAt: "x", changes: [] });
    },
  });
  const app = await mount(`${HUB}?tab=flow`);
  await wait(() => byData(app, "data-path-row").length === 3 && byData(app, "data-candidate-row").length === 3, "flow sections");
  const pathRows = byData(app, "data-path-row").map((row) => row.findAllByType("td").map((td) => text(td.children)));
  assert.deepEqual(pathRows, [
    ["IF_SYN_ORDER", "—", "미등록", "경로 3개 · 거래 157건"],
    ["IF_SYN_PAY", "1개", "켜짐", "경로 2개 · 거래 47건"],
    ["IF_SYN_THIN", "—", "미등록", "경로 1개 · 거래 4건 · 근거 부족"],
  ]);
  assert.ok(app.shown().includes("관측 경로 초안 · 후보 작업 완료"));
  const pathLink = rowOf(app, "data-path-row", "IF_SYN_ORDER").findAll((node) => typeof node.type !== "string" && node.props.to === "/detection/rules/protocols/IF_SYN_ORDER")[0];
  assert.deepEqual(pathLink.props.state, { returnTo: `${HUB}?tab=flow` }, "protocol detail returns to this tab");
  const search = app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "인터페이스 검색")[0];
  await act(async () => search.props.onChange({ target: { value: "pay" } }));
  assert.deepEqual(byData(app, "data-path-row").map((row) => row.props["data-path-row"]), ["IF_SYN_PAY"], "case-insensitive substring");
  // 단계 조건: 행 [승인] → 상세의 저장할 조건으로 입력창 → 미리보기 → 저장.
  const r0 = () => rowOf(app, "data-candidate-row", `${JOB}.r0`);
  assert.equal(rowButton(rowOf(app, "data-candidate-row", `${JOB}.r2`), "승인").props.disabled, true, "not approvable: off");
  await act(async () => rowButton(r0(), "승인").props.onClick());
  const ask = () => app.renderer.root.findAll((node) => typeof node.props?.onNext === "function" && node.props.kind === "relation")[0];
  await wait(() => Boolean(ask()), "relation approve dialog");
  await act(async () => ask().props.onNext({ name: "", severity: "critical" }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "approve confirm");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.relationRules, [{ ...relationDraft, id: null, clientRef: "candidate", severity: "critical", enabled: true }]);
  assert.deepEqual(pendingOf(app).changes.map(ruleDraft.changeLine), ["관계 규칙 추가 · 선행 관계 P1 → P2"]);
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 31"), "saved");
  // 다시 누르면 같은 조건이 있어 입력창 없이 승인됨(상세와 같은 문구).
  await act(async () => rowButton(r0(), "승인").props.onClick());
  await wait(() => r0().findAll((node) => node.props["data-approved"] !== undefined && typeof node.type === "string").length === 1, "approved note");
  assert.ok(text(r0().children).includes(labels.APPROVED_TEXT));
  assert.equal(ask(), undefined, "no dialog for an approved condition");
  // 상세 조회 실패는 그 행에 코드 그대로.
  await act(async () => rowButton(rowOf(app, "data-candidate-row", `${JOB}.r1`), "승인").props.onClick());
  await wait(() => text(rowOf(app, "data-candidate-row", `${JOB}.r1`).children).includes("후보 상세 조회 실패 · rule_candidate_not_found"), "detail failure code");
  await app.cleanup();
}

// 7) 룰 설정 카드·프로토콜 목록의 "추천 n건 보기" 건수: 이상 징후 = 처리 시간 후보(후보 상태), 관계 규칙 = 승인 가능 관계 후보, 프로토콜 = 근거 부족이 아닌 경로 초안
{
  routes = baseRoutes();
  const app = await mount(RULES);
  const links = () => app.renderer.root.findAll((node) => node.type === "a" && node.props["data-recommend-link"]);
  await wait(() => links().length === 3 && !links().some((node) => text(node.children).includes("—")), "counts loaded");
  assert.deepEqual(links().map((node) => [text(node.children), node.props.href]), [
    ["추천 2건 보기", `${HUB}?tab=anomaly`], ["추천 2건 보기", `${HUB}?tab=flow#recommend-relations`], ["추천 2건 보기", `${HUB}?tab=flow#recommend-paths`],
  ]);
  assert.ok(!app.shown().includes("AI 규칙 추천") && !app.shown().includes("수식 추천"), "recommendation commands left the rules page");
  await app.cleanup();
  // 결과 없음은 0건(값 없음이 아님).
  routes = baseRoutes({ "GET /api/rule-set/candidates": () => json({ latestJob: null, approval, result: null }), "GET /api/rule-set/protocol-drafts": () => json({ state: "protocol_drafts_unavailable", reason: "no_result", latestJob: null }) });
  const none = await mount(RULES);
  await wait(() => none.renderer.root.findAll((node) => node.type === "a" && node.props["data-recommend-link"]).every((node) => text(node.children) === "추천 0건 보기")
    && none.renderer.root.findAll((node) => node.type === "a" && node.props["data-recommend-link"]).length === 3, "zero counts");
  await none.cleanup();
}
console.log("rule recommendations page tests passed");
process.exit(0);
