import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 응답 코드(contracts/response-codes-ui.md) 화면 시험(로컬 가짜 fetch만, 합성 코드 T001·ZZ9·10): 룰 설정 진입 카드, 목록(위험도 코드별·기본값, 사전에 없음, 검색·유형 필터 조회 조건),
// 상세(코드 정보·위험도 바뀐 버전·메시지 기록, 위험도 변경 → 서버 미리보기 확인창 → 룰 세트 저장 본문, 기본값 따름), 메시지 편집(저장·충돌·사전에 없는 코드), 이상 분석 원인 줄 메시지, 일반 사용자 접근 불가.
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
const { isAppPath, paths } = await import("../src/app/routes.ts");
const { RuleSaveDialog } = await import("../src/features/detection/rules/RuleDialogs.tsx");
const labels = await import("../src/features/detection/rules/responsecodes/responseCodeLabels.ts");
const analysisLabels = await import("../src/features/analysis/labels.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const STATUS_DEFAULT = { PROCESS_STATUS_F: "critical", PROCESS_STATUS_P: "warning", PROCESS_STATUS_Z: "warning", PROCESS_STATUS_H: "info", PROCESS_STATUS_N: "info" };
const hardRules = (failed = { byCode: { T001: "critical" }, default: "warning" }, failedOn = true) => [
  { id: "hard:process-status", methodVersion: "process-status-hr-v2", displayName: "상태가 S가 아님", conditionText: "STATUS ≠ S", severity: { byCode: STATUS_DEFAULT }, enabled: true, editable: ["enabled", "severity"] },
  { id: "hard:negative-duration", methodVersion: "negative-duration-v1", displayName: "시작 시각이 종료 시각보다 늦음", conditionText: "x", severity: "critical", enabled: true, editable: ["enabled"] },
  { id: "hard:transaction-failed", methodVersion: "transaction-failed-v1", displayName: "거래 실패", conditionText: "y", severity: failed, enabled: failedOn, editable: ["enabled", "severity"] },
  { id: "hard:unregistered-interface", methodVersion: "unregistered-interface-v1", displayName: "등록되지 않은 인터페이스", conditionText: "z", severity: "critical", enabled: false, editable: ["enabled", "severity"] },
];
const state = (revision = 12, extra = {}) => ({
  contractVersion: "rule-set-v1", revision, contentHash: "a".repeat(64), contentHashVersion: 3, savedAt: "2026-11-13T08:00:00Z", savedBy: { actorId: "1", displayName: "admin@example.test" },
  restoredFromRevision: null, catalogVersion: "method-catalog-v2",
  counts: { hardEnabled: 3, hardTotal: 4, softEnabled: 0, softTotal: 0, relationEnabled: 0, relationTotal: 0, protocolEnabled: 0, protocolTotal: 0 },
  hardRules: hardRules(), softRules: [], relationRules: [], protocolRules: [], ...extra,
});
const ruleRef = (revision = 12, extra = {}) => ({ revision, ruleId: "hard:transaction-failed", enabled: true, defaultSeverity: "warning", registeredCodes: 2, ...extra });
const item = (code, extra = {}) => ({ code, inDictionary: true, enumName: `SYN_${code}`, codeType: "SYN_A", httpStatus: "500 SYN", messageKey: `syn.${code}`,
  message: `합성 메시지 ${code}`, origin: "import", severity: { level: "warning", source: "default" }, ...extra });
const listBody = (items, extra = {}) => ({ ruleSet: ruleRef(), counts: { dictionary: 3, edited: 0, missingFromDictionary: 1 },
  types: [{ codeType: "SYN_A", count: 2 }, { codeType: "SYN_B", count: 1 }], items, pagination: { limit: 200, hasMore: false, nextCursor: null }, ...extra });
const adminRef = { actorId: "1", displayName: "admin@example.test" };
const importRef = { actorId: "system:import", displayName: "system:import" };
const detailBody = (extra = {}) => ({ ...item("T001", { severity: { level: "critical", source: "code" } }), revision: 3, updatedAt: "2026-11-13T07:00:00.000000Z",
  updatedBy: importRef, ruleSet: ruleRef(),
  messageHistory: [{ kind: "edit", messageBefore: "이전 합성 메시지", messageAfter: "합성 메시지 T001", changedAt: "2026-11-13T07:00:00.000000Z", changedBy: adminRef },
    { kind: "import", messageBefore: null, messageAfter: "이전 합성 메시지", changedAt: "2026-11-12T07:00:00.000000Z", changedBy: importRef }],
  severityHistory: [{ revision: 12, savedAt: "2026-11-13T08:00:00.000000Z", savedBy: adminRef, restoredFromRevision: 9,
    before: { level: "warning", source: "default" }, after: { level: "critical", source: "code" } }], ...extra });

let routes = {};
const requested = [];
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const method = options.method ?? "GET";
  requested.push({ method, path: target.pathname, search: target.search, body: options.body ? JSON.parse(options.body) : undefined });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  if (target.pathname === "/api/pipeline/events") return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "text/event-stream" } });
  const handler = routes[`${method} ${target.pathname}`];
  return handler ? handler(target, options) : json({ code: "rule_set_storage_unavailable" }, 503);
};
const calls = (method, path) => requested.filter((item) => item.method === method && item.path === path);
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 200) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
let currentUser;
const mount = async (path, userType = "admin", locationState = undefined) => {
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [locationState ? { pathname: path, state: locationState } : path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))));
  });
  const shown = () => text(renderer.toJSON());
  const cleanup = async () => { await act(async () => renderer.unmount()); router.dispose(); client.clear(); };
  const click = async (label, index = 0) => {
    const buttons = renderer.root.findAll((n) => n.type === "button" && text(n.children).includes(label) && !n.props.disabled);
    assert.ok(buttons[index], `button ${label}`);
    await act(async () => buttons[index].props.onClick?.({ stopPropagation() {}, preventDefault() {} }));
  };
  return { renderer, router, shown, cleanup, click };
};
const byData = (app, key) => app.renderer.root.findAll((node) => node.props?.[key] !== undefined && typeof node.type === "string");
const rowsOf = (app, key) => byData(app, key).map((row) => row.findAllByType("td").map((td) => text(td.children)));
const pendingOf = (app) => app.renderer.root.findByType(RuleSaveDialog).props.pending;
const previewOk = (changes, warnings = []) => () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes, warnings });

const select = (app, label) => app.renderer.root.findAll((n) => n.type === "select" && n.props["aria-label"] === label)[0];
const textarea = (app) => app.renderer.root.findAll((n) => n.type === "textarea")[0];

// 1) 주소·문구·저장 요청·이상 분석 원인 줄
{
  assert.equal(paths.detectionRuleResponseCode("T/0 1%"), "/detection/rules/response-codes/T%2F0%201%25");
  assert.ok(isAppPath("/detection/rules/response-codes") && isAppPath("/detection/rules/response-codes/T001"));
  const failed = labels.failedRule(state());
  assert.deepEqual(failed.severity, { byCode: { T001: "critical" }, default: "warning" });
  assert.deepEqual(labels.codeSeverityIn(failed.severity, "T001"), { level: "critical", source: "code" });
  assert.equal(labels.codeSeverityText(labels.codeSeverityIn(failed.severity, "E500")), "주의(기본값)");
  const set = labels.severityRequest(state(), "E500", "info", false);
  assert.deepEqual(set.hardRules.find((rule) => rule.id === "hard:transaction-failed").severity, { byCode: { T001: "critical", E500: "info" }, default: "warning" });
  assert.deepEqual(set.hardRules.find((rule) => rule.id === "hard:process-status").severity, { byCode: STATUS_DEFAULT }, "other rules unchanged");
  assert.equal(set.expectedRevision, 12);
  const base = state();
  const cleared = labels.severityRequest(base, "T001", null, true);
  assert.deepEqual(cleared.hardRules.find((rule) => rule.id === "hard:transaction-failed").severity, { byCode: {}, default: "warning" });
  assert.equal(cleared.confirmHardRuleDisable, true);
  assert.equal(base.hardRules[2].severity.byCode.T001, "critical", "the state object is not mutated");
  assert.equal(labels.failedRule(state(12, { hardRules: hardRules("warning") })), null, "a single level is not edited here");
  const cause = { category: "hard", methodId: "hard:process-status", code: "PROCESS_STATUS_F" };
  assert.equal(analysisLabels.causeText(cause, "E500", "합성 서버 오류"), "상태 F 실패 · 응답 코드 E500 (합성 서버 오류)");
  assert.equal(analysisLabels.causeText(cause, "E500", null), "상태 F 실패 · 응답 코드 E500");
  assert.equal(analysisLabels.causesLine({ causes: [cause], responseCode: "E500", responseCodeMessage: "합성 서버 오류" }), "상태 F 실패 · 응답 코드 E500 (합성 서버 오류)");
}

// 2) 룰 설정 진입 카드
{
  routes = {
    "GET /api/rule-set": () => json(state(12, { hardRules: hardRules({ byCode: { T001: "critical", ZZ9: "info" }, default: "warning" }, false) })),
    "GET /api/rule-set/history": () => json({ items: [], pagination: { limit: 50, hasMore: false, nextCursor: null } }),
    "GET /api/jobs/capabilities": () => json({ methodCatalog: { softRuleFields: [] } }),
  };
  const app = await mount(paths.detectionRules);
  await wait(() => app.shown().includes("목록 보기"), "entry card");
  assert.ok(app.shown().includes("거래 실패 규칙의 코드별 위험도 · 코드별 2개 · 그 밖의 코드 주의 · 규칙 꺼짐"), app.shown().slice(0, 600));
  assert.equal(app.renderer.root.findAll((n) => n.type === "a" && n.props.href === "/detection/rules/response-codes").length, 1);
  await app.cleanup();
}

// 3) 목록: 행(위험도 코드별/기본값, 사전에 없음), 검색·유형 필터는 서버 조회 조건, 빈 사전 안내
{
  let empty = false;
  routes = {
    "GET /api/response-codes": (target) => {
      if (empty) return json(listBody([], { counts: { dictionary: 0, edited: 0, missingFromDictionary: 0 }, types: [] }));
      const q = target.searchParams.get("q"), type = target.searchParams.get("type");
      const all = [item("10", { codeType: "SYN_B" }), item("T001", { severity: { level: "critical", source: "code" } }),
        item("ZZ9", { inDictionary: false, enumName: null, codeType: null, httpStatus: null, messageKey: null, message: null, origin: null, severity: { level: "info", source: "code" } })];
      return json(listBody(all.filter((row) => (!q || row.code.toLowerCase().includes(q.toLowerCase())) && (!type || row.codeType === type))));
    },
  };
  requested.length = 0;
  const app = await mount(paths.detectionRuleResponseCodes);
  await wait(() => byData(app, "data-response-code-row").length === 3, "rows");
  assert.equal(calls("GET", "/api/response-codes")[0].search, "?limit=200");
  assert.deepEqual(rowsOf(app, "data-response-code-row"), [
    ["10", "SYN_B", "주의기본값", "합성 메시지 10"], ["T001", "SYN_A", "위험", "합성 메시지 T001"], ["ZZ9사전에 없음", "—", "참고", "—"],
  ]);
  assert.equal(text(byData(app, "data-response-code-head")[0].children), "현재 운영 버전 12 · 사전 3개 · 코드별 위험도 2개 · 그 밖의 코드 주의 · 사전에 없음 1개");
  assert.equal(byData(app, "data-rule-off").length, 0);
  assert.ok(app.shown().includes(labels.RESPONSE_CODE_LIST_LEAD));
  const search = app.renderer.root.findAll((n) => n.type === "input" && n.props["aria-label"] === "응답 코드 검색")[0];
  await act(async () => search.props.onChange({ target: { value: "t00" } }));
  await wait(() => byData(app, "data-response-code-row").length === 1, "search");
  assert.equal(calls("GET", "/api/response-codes").at(-1).search, "?limit=200&q=t00");
  await act(async () => search.props.onChange({ target: { value: "" } }));
  await act(async () => select(app, "유형").props.onChange({ target: { value: "SYN_B" } }));
  await wait(() => byData(app, "data-response-code-row").length === 1 && calls("GET", "/api/response-codes").at(-1).search === "?limit=200&type=SYN_B", "type filter");
  assert.deepEqual(select(app, "유형").findAllByType("option").map((n) => text(n.children)), ["전체 유형", "SYN_A (2)", "SYN_B (1)"]);
  assert.equal(app.renderer.root.findAll((n) => n.type === "a" && n.props.href === "/detection/rules/response-codes/10").length, 1);
  await app.cleanup();
  empty = true;
  const none = await mount(paths.detectionRuleResponseCodes);
  await wait(() => none.shown().includes(labels.DICTIONARY_EMPTY), "empty dictionary sentence");
  await none.cleanup();
}

// 4) 상세: 코드 정보·기록, 위험도 변경 → 서버 미리보기 → 확인창(바뀌는 단계) → 룰 세트 저장 본문, 기본값 따름 = byCode에서 뺌
{
  let current = state(12);
  let detail = detailBody();
  routes = {
    "GET /api/rule-set": () => json(current),
    "GET /api/response-codes/T001": () => json(detail),
    "POST /api/rule-set/preview": previewOk([{ type: "hard_updated", id: "hard:transaction-failed", displayName: "거래 실패",
      diff: { severity: [{ byCode: { T001: "critical" }, default: "warning" }, { byCode: { T001: "info" }, default: "warning" }] } }]),
    "PUT /api/rule-set": () => {
      current = state(13, { hardRules: hardRules({ byCode: { T001: "info" }, default: "warning" }) });
      detail = detailBody({ severity: { level: "info", source: "code" }, ruleSet: ruleRef(13) });
      return json({ revision: 13, contentHash: "b".repeat(64), appliedAt: "x", changes: [] });
    },
  };
  requested.length = 0;
  const app = await mount(paths.detectionRuleResponseCode("T001"), "admin", { returnTo: paths.detectionRuleResponseCodes });
  await wait(() => app.shown().includes("메시지 키"), "detail");
  for (const part of ["코드명SYN_T001", "유형SYN_A", "HTTP 상태500 SYN", "출처가져온 값", "거래 실패(hard:transaction-failed)"]) assert.ok(app.shown().includes(part), part);
  const back = app.renderer.root.findAll((n) => n.type === "a" && n.props["aria-label"]?.startsWith("뒤로"))[0];
  assert.equal(back.props.href, "/detection/rules/response-codes");
  assert.deepEqual(rowsOf(app, "data-severity-history").map((row) => [row[0], row[2], row[3]]),
    [["버전 12 · 버전 9 상태로 되돌림", "admin@example.test", "주의(기본값) → 위험"]]);
  assert.deepEqual(rowsOf(app, "data-message-history").map((row) => row.slice(1)), [
    ["admin@example.test", "화면에서 수정", "이전 합성 메시지 → 합성 메시지 T001"], ["system:import", "가져오기", "이전 합성 메시지"]]);
  await wait(() => select(app, "T001 위험도") !== undefined, "severity select");
  assert.equal(select(app, "T001 위험도").props.value, "critical");
  assert.deepEqual(select(app, "T001 위험도").findAllByType("option").map((n) => text(n.children)), ["위험", "주의", "참고", "기본값 따름 (주의)"]);
  await act(async () => select(app, "T001 위험도").props.onChange({ target: { value: "info" } }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirm dialog");
  assert.equal(pendingOf(app).lead, "응답 코드 T001 위험도 위험 → 참고");
  assert.equal(pendingOf(app).changes.length, 1, "server change summary goes to the dialog");
  const previewBody = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.deepEqual(previewBody.hardRules.find((rule) => rule.id === "hard:transaction-failed"), { id: "hard:transaction-failed", enabled: true, severity: { byCode: { T001: "info" }, default: "warning" } });
  assert.equal(previewBody.expectedRevision, 12);
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13"), "applied");
  assert.equal(calls("PUT", "/api/rule-set").length, 1);
  assert.deepEqual(calls("PUT", "/api/rule-set")[0].body.hardRules.find((rule) => rule.id === "hard:transaction-failed").severity, { byCode: { T001: "info" }, default: "warning" });
  await wait(() => calls("GET", "/api/response-codes/T001").length >= 2, "detail re-read after save (history)");
  await wait(() => select(app, "T001 위험도").props.value === "info", "select shows the saved level");
  await act(async () => select(app, "T001 위험도").props.onChange({ target: { value: "" } }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirm dialog 2");
  assert.equal(pendingOf(app).lead, "응답 코드 T001 위험도 참고 → 주의(기본값)");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.hardRules.find((rule) => rule.id === "hard:transaction-failed").severity, { byCode: {}, default: "warning" },
    "follow default = remove the code from byCode");
  await app.cleanup();
}

// 5) 메시지 편집: 저장 본문(앞뒤 공백 제거·줄바꿈 → 공백·수정 번호), 충돌 409 안내, 사전에 없는 코드는 expectedRevision null, 규칙 꺼짐 안내
{
  let detail = detailBody();
  let conflict = false;
  routes = {
    "GET /api/rule-set": () => json(state(12, { hardRules: hardRules({ byCode: { T001: "critical", ZZ9: "info" }, default: "warning" }, false) })),
    "GET /api/response-codes/T001": () => json(detail),
    "GET /api/response-codes/ZZ9": () => json(detailBody({ code: "ZZ9", inDictionary: false, enumName: null, codeType: null, httpStatus: null, messageKey: null, message: null, origin: null,
      revision: null, updatedAt: null, updatedBy: null, messageHistory: [], severityHistory: [], severity: { level: "info", source: "code" } })),
    "PUT /api/response-codes/T001": (_target, options) => {
      if (conflict) return json({ code: "response_code_revision_conflict", currentRevision: 5 }, 409);
      detail = detailBody({ message: JSON.parse(options.body).message, origin: "edited", revision: 4 });
      return json(detail);
    },
    "PUT /api/response-codes/ZZ9": (_target, options) => json(detailBody({ code: "ZZ9", message: JSON.parse(options.body).message, origin: "edited", revision: 1 })),
  };
  requested.length = 0;
  const app = await mount(paths.detectionRuleResponseCode("T001"));
  await wait(() => app.shown().includes("메시지 저장") && byData(app, "data-rule-off").length === 1, "message card and rule-off note");
  assert.equal(textarea(app).props.value, "합성 메시지 T001");
  await act(async () => textarea(app).props.onChange({ target: { value: "  고친 합성\n메시지  " } }));
  await app.click("메시지 저장");
  await wait(() => byData(app, "data-message-saved").length === 1, "saved");
  assert.deepEqual(calls("PUT", "/api/response-codes/T001")[0].body, { message: "고친 합성 메시지", expectedRevision: 3 });
  await wait(() => app.shown().includes("출처화면에서 수정"), "re-read detail");
  conflict = true;
  await act(async () => textarea(app).props.onChange({ target: { value: "늦은 편집" } }));
  await app.click("메시지 저장");
  await wait(() => app.shown().includes(labels.MESSAGE_CONFLICT), "conflict notice");
  await app.cleanup();
  const missing = await mount(paths.detectionRuleResponseCode("ZZ9"));
  await wait(() => missing.shown().includes(labels.NOT_IN_DICTIONARY_NOTE), "not-in-dictionary note");
  assert.ok(missing.shown().includes("출처사전에 없음"));
  await act(async () => textarea(missing).props.onChange({ target: { value: "새 합성 설명" } }));
  await missing.click("메시지 저장");
  await wait(() => calls("PUT", "/api/response-codes/ZZ9").length === 1, "create");
  assert.deepEqual(calls("PUT", "/api/response-codes/ZZ9")[0].body, { message: "새 합성 설명", expectedRevision: null });
  await missing.cleanup();
}

// 6) 일반 사용자는 탐지 설정에 들어올 수 없다
{
  requested.length = 0;
  const app = await mount(paths.detectionRuleResponseCodes, "user");
  await tick(30);
  assert.equal(calls("GET", "/api/response-codes").length, 0, "no admin request as a user");
  assert.ok(!app.shown().includes("응답 코드 목록"));
  await app.cleanup();
}

console.log("response code pages tests passed");
