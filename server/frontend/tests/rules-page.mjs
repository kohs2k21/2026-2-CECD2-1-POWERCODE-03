import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 룰 설정 화면 통합(로컬 가짜 fetch만, 외부 요청 없음): 현재 상태·이상 규칙·이상 징후 규칙 표시, 초안 변경 건수, 저장 확인창(서버 요약·확인 체크),
// 성공·실패·리비전 충돌, 되돌리기 본문, 수식 추천·AI 규칙 추천(적용은 입력칸만), 조회 실패, 판정 정책 미노출.
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
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
globalThis.HTMLFormElement = class {};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
const { ConfirmDialog, RuleAddBody, RuleSaveBody, RuleSaveDialog } = await import("../src/features/detection/rules/RuleDialogs.tsx");
const draft = await import("../src/features/detection/rules/ruleDraft.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const STATUS_DEFAULT = { PROCESS_STATUS_F: "critical", PROCESS_STATUS_P: "warning", PROCESS_STATUS_Z: "warning", PROCESS_STATUS_H: "info", PROCESS_STATUS_N: "info" };
const hardRules = (negativeOn = true) => [
  { id: "hard:process-status", methodVersion: "process-status-hr-v2", displayName: "상태가 S가 아님", conditionText: "STATUS ≠ S",
    severity: { byCode: STATUS_DEFAULT }, enabled: true, editable: ["enabled", "severity"] },
  { id: "hard:negative-duration", methodVersion: "negative-duration-v1", displayName: "시작 시각이 종료 시각보다 늦음",
    conditionText: "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)", severity: "critical", enabled: negativeOn, editable: ["enabled"] },
];
const softRule = { id: "soft:elapsed_ms-3f9a2c1d", name: "처리 시간 5초 초과", field: "elapsed_ms", operator: "gt", threshold: 5000, unit: "ms", severity: "warning", enabled: true, createdAtRevision: 7, updatedAtRevision: 12 };
const state = (revision = 12, overrides = {}) => ({
  contractVersion: "rule-set-v1", revision, contentHash: "a".repeat(64), contentHashVersion: 2, savedAt: "2026-11-02T08:12:33Z",
  savedBy: { actorId: "1", displayName: "admin@example.test" }, restoredFromRevision: null, catalogVersion: "method-catalog-v2",
  counts: { hardEnabled: 2, hardTotal: 2, softEnabled: 1, softTotal: 1 }, hardRules: hardRules(), softRules: [softRule], ...overrides,
});
const field = (fieldId, displayName, unit, valueType) => ({ fieldId, displayName, unit, valueType, operators: ["gte", "gt", "lte", "lt"], status: "supported" });
const capabilities = { methodCatalog: { softRuleFields: [field("retry_count", "재시도 수", "count", "integer"), field("elapsed_ms", "처리 시간", "ms", "number")] } };
const historyItem = (revision, changes = [], extra = {}) => ({ revision, savedAt: "2026-11-02T08:00:00Z", savedBy: { actorId: "1", displayName: "admin@example.test" }, note: null, restoredFromRevision: null, contentHash: "b".repeat(64), contentHashVersion: 2, summary: {}, changes, ...extra });
const history = (revision = 12) => ({
  items: [historyItem(revision, [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [4000, 5000] } }]),
    historyItem(10, [{ type: "hard_enabled", id: "hard:negative-duration", displayName: "시작 시각이 종료 시각보다 늦음" }], { restoredFromRevision: 7 }), historyItem(0)],
  pagination: { limit: 50, hasMore: false, nextCursor: null },
});

const requested = [];
let routes = {};
let currentUser;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, method: options.method ?? "GET", body: options.body ? JSON.parse(options.body) : undefined });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  const handler = routes[`${options.method ?? "GET"} ${target.pathname}`];
  return handler ? handler(target, options) : json({ code: "unavailable" }, 503);
};
const baseRoutes = (overrides = {}) => ({
  "GET /api/rule-set": () => json(state()),
  "GET /api/rule-set/history": () => json(history()),
  "GET /api/jobs/capabilities": () => json(capabilities),
  "GET /api/pipeline/sources": () => json({ items: [{ id: "11111111-1111-4111-8111-111111111111", datasetKey: "ds", displayName: "출처", createdAt: "2026-10-01T00:00:00Z" }] }),
  ...overrides,
});
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 120) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (userType = "admin", { keepDrafts = false, createNodeMock } = {}) => {
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  if (!keepDrafts) useDraftStore.getState().reset();
  let session;
  const SessionProbe = () => {
    session = useAuthSession();
    return null;
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/detection/rules"] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(SessionProbe), React.createElement(RouterProvider, { router }))), { createNodeMock: createNodeMock ?? (() => null) });
  });
  for (let i = 0; i < 20 && text(renderer.toJSON()).includes("로그인 상태를 확인하는 중"); i++) await tick();
  const shown = () => text(renderer.toJSON());
  const button = (label, index = 0) => renderer.root.findAll((node) => node.type === "button" && text(node.children) === label)[index];
  const click = async (label, index) => {
    assert.notEqual(button(label, index).props.disabled, true, `${label} is enabled`);
    await act(async () => button(label, index).props.onClick());
  };
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, button, click, cleanup, session: () => session };
};
const toggle = async (app, label, value) => act(async () => app.renderer.root.findByProps({ "aria-label": label, "data-slot": "switch" }).props.onCheckedChange(value));
const setThreshold = async (app, value) => act(async () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${softRule.name} 임계값`)[0].props.onChange({ target: { value } }));
const pendingOf = (app) => app.renderer.root.findByType(RuleSaveDialog).props.pending;
/** 확인창 본문은 Radix 포털 밖에서 따로 그린다(시험 렌더러에 포털이 없다). */
const renderSaveBody = async (app) => {
  let body;
  await act(async () => { body = create(React.createElement(RuleSaveBody, { pending: pendingOf(app), onCancel() {} })); });
  return body;
};
const HARD_WARNING = "명백한 이상을 놓칠 수 있습니다. 변경을 권장하지 않습니다.";
const hardWarning = (app) => app.renderer.root.findAll((node) => node.type === "p" && text(node.children) === HARD_WARNING);
/** 위험도 묶음 구조: 묶음마다 왼쪽 배지 1개(묶음 높이를 채움) + 오른쪽 코드 줄. */
const severityGroupsOf = (row) => row.findAll((node) => node.type === "span" && node.props["data-severity"]).map((group) => {
  const badge = group.findAll((node) => node.props["data-slot"] === "badge" && typeof node.type === "string");
  assert.equal(badge.length, 1, "one badge per group");
  return {
    level: group.props["data-severity"], badge: text(badge[0].children), stretched: badge[0].props.className.includes("self-stretch") && group.props.className.includes("grid-cols-"),
    codes: group.findAll((node) => node.type === "span" && node.props["data-severity-code"]).map((line) => text(line.children).replace(/위험주의참고$/, "")),
  };
});
/** 드롭다운 항목(위에서부터): 전송 값과 표시 문구. */
const SEVERITY_OPTIONS = [["critical", "위험"], ["warning", "주의"], ["info", "참고"]];
const optionsOf = (select) => select.findAll((node) => node.type === "option").map((option) => [option.props.value, text(option.children)]);
const calls = (method, path) => requested.filter((call) => call.method === method && call.path === path);

const { detectionToc } = await import("../src/features/detection/detectionToc.ts");
/** 목차 영역 id → 화면 영역 제목(없으면 null). 영역 제목은 목차 이름과 같아야 한다. */
const tocSections = (app, menu) => detectionToc[menu].map(({ id }) => {
  const card = app.renderer.root.findAll((node) => node.type === "div" && node.props.id === id && node.props["data-slot"] === "section-card")[0];
  return [id, card ? text(card.findByType("h2").children) : null];
});
// 1) 표시: 머리글, byCode 코드별 위험도, 읽기 전용 조건, 이상 징후 규칙 입력, 변경 기록, 판정 정책 없음.
{
  routes = baseRoutes();
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await wait(() => app.button("규칙 추가")?.props.disabled === false, "fields loaded");
  const shown = app.shown();
  // 이상 규칙 구역 하단 상시 경고(사용자 11/04): 모두 켜진 상태에서도 보이고, 규칙 이름이 없고, 경보 역할이 아니다.
  // 사이드바 목차 대상: 영역마다 안정 id, 제목은 목차 이름 그대로(응답 코드 카드는 거래 실패 규칙이 있을 때만)
  for (const [id, title] of tocSections(app, "rules")) {
    if (id === "rules-response-codes" && title === null) continue;
    assert.equal(title, detectionToc.rules.find((section) => section.id === id).label, id);
  }
  assert.equal(hardWarning(app).length, 1, "warning is visible while every rule is on");
  assert.equal(hardWarning(app)[0].props.role, undefined, "always-on text is not an alert");
  assert.ok(hardWarning(app)[0].props.className.includes("text-warning-text"));
  // 저장 줄은 이상 규칙 위에, 머리글 아래 고정.
  const stickyBox = app.renderer.root.find((node) => node.type === "div" && node.props["data-rule-sticky"] !== undefined);
  assert.ok(text(stickyBox.children).includes("변경 0건 · 저장하면 바로 운영에 적용됩니다") && stickyBox.findAll((node) => node.type === "button").length === 2, "save bar with its buttons");
  assert.ok(stickyBox.props.className.includes("sticky") && stickyBox.props.className.includes("bg-background"));
  assert.ok(shown.indexOf("변경 0건") < shown.indexOf("STATUS ≠ S"), "save bar is above the hard rules");
  assert.ok(!shown.includes("시스템 내장 목록"), "no caption line under the table");
  // 규칙 추천 진입 카드(rule-candidates-ui.md): 이상 징후 규칙 아래·변경 기록 위, 후보 목록으로 가는 링크만.
  const entry = app.renderer.root.find((node) => node.props?.["aria-label"] === "규칙 추천" && node.props["data-slot"] === "section-card");
  assert.equal(entry.findAllByType("a").map((a) => [a.props.href, text(a.children)]).join(), "/detection/rules/candidates,후보 보기");
  assert.equal(app.renderer.root.findAll((node) => node.type === "table" && node.props["aria-label"] === "이상 규칙").length, 1, "table keeps a hidden name");
  assert.ok(shown.indexOf("START_TIME > END_TIME") < shown.indexOf(HARD_WARNING) && shown.indexOf(HARD_WARNING) < shown.indexOf("AI 규칙 추천"), "bottom of the hard rule section");
  for (const expected of ["변경자 admin@example.test", "켜진 규칙 · 이상 규칙 2/2 · 이상 징후 규칙 1/1", "STATUS ≠ S", "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)",
    "처리 시간elapsed_ms", "변경 0건 · 저장하면 바로 운영에 적용됩니다"]) assert.ok(shown.includes(expected), expected);
  for (const forbidden of ["AI 임계값 추천", "판정 정책", "기준 k", "하드룰", "소프트룰", "리비전", "r12", "r10", "r0"]) assert.ok(!shown.includes(forbidden), `${forbidden} is not on the screen`);
  assert.equal(app.button("저장").props.disabled, true);
  assert.equal(app.button("변경 취소").props.disabled, true);
  const severityLines = (id) => app.renderer.root.find((node) => node.type === "tr" && node.props["data-rule"] === id)
    .findAll((node) => node.type === "span" && node.props["data-severity"]).map((node) => [node.props["data-severity"], text(node.children)]);
  assert.deepEqual(severityLines("hard:negative-duration"), [["critical", "위험"]], "single severity is one badge");
  assert.equal(app.renderer.root.find((node) => node.type === "tr" && node.props["data-rule"] === "hard:negative-duration").findAll((node) => node.type === "select").length, 0, "other rules have no severity input");
  // 편집 가능한 규칙: 코드마다 선택, 위험 → 주의 → 참고 순서, 코드 뜻을 옆에, 묶음마다 배지가 코드 줄 전체 높이를 채움.
  const statusRow = app.renderer.root.find((node) => node.type === "tr" && node.props["data-rule"] === "hard:process-status");
  const groups = severityGroupsOf(statusRow);
  assert.deepEqual(groups.map(({ level, badge, codes }) => [level, badge, codes]),
    [["critical", "위험", ["F 실패"]], ["warning", "주의", ["P 처리중", "Z 데이터없음"]], ["info", "참고", ["H 부분성공", "N 미처리"]]], "one group per level with its code lines");
  assert.ok(groups.every(({ stretched }) => stretched), "the badge fills the height of its group");
  assert.equal(app.renderer.root.findAll((node) => node.type === "select" && node.props["aria-label"] === "N 미처리 위험도")[0].props.value, "info");
  // 위험도 드롭다운은 위에서부터 위험 → 주의 → 참고(상태 규칙 코드별, 이상 징후 규칙 행 모두).
  const severitySelects = app.renderer.root.findAll((node) => node.type === "select" && node.props["aria-label"]?.endsWith(" 위험도"));
  assert.deepEqual(severitySelects.map((select) => select.props["aria-label"]),
    ["F 실패 위험도", "P 처리중 위험도", "Z 데이터없음 위험도", "H 부분성공 위험도", "N 미처리 위험도", `${softRule.name} 위험도`], "every severity dropdown is checked");
  for (const select of severitySelects) assert.deepEqual(optionsOf(select), SEVERITY_OPTIONS, select.props["aria-label"]);
  assert.equal(app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "규칙 이름")[0].props.value, softRule.name);
  await wait(() => app.shown().includes("이상 징후 규칙 수정 1건: 처리 시간 5초 초과(임계값 4,000 → 5,000)"), "history summary");
  assert.ok(app.shown().includes("버전 7 상태로 되돌림 · 이상 규칙 켜기 1건"), "restored revision shows its source");
  assert.ok(app.shown().includes("최초 적용"), "r0 summary");
  assert.equal(app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "이 상태로 되돌리기").length, 2, "every revision except the current one");
  await app.cleanup();
}

// 2) 저장: 끈 뒤에도 구역 하단 경고 한 줄, 서버 요약으로 확인창, 새로 끄면 확인 체크 필수, 성공 문구, 초안 정리.
{
  let current = state();
  routes = baseRoutes({
    "GET /api/rule-set": () => json(current),
    "GET /api/rule-set/history": () => json(history(current.revision)),
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: ["hard:negative-duration"],
      changes: [{ type: "hard_disabled", id: "hard:negative-duration", displayName: "시작 시각이 종료 시각보다 늦음" },
        { type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 8000] } }] }),
    "PUT /api/rule-set": () => {
      current = state(13, { hardRules: hardRules(false), softRules: [{ ...softRule, threshold: 8000 }] });
      return json({ revision: 13, previousRevision: 12, contentHash: "c".repeat(64), appliedAt: "2026-11-02T08:20:05Z", appliesFrom: "next_decision", changes: [], summary: {} });
    },
  });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await toggle(app, "시작 시각이 종료 시각보다 늦음 켜기", false);
  assert.equal(hardWarning(app).length, 1, "one warning line stays after turning a rule off");
  assert.ok(!app.shown().includes("끄면 명백한 이상을"), "no per-rule row warning");
  await setThreshold(app, "8000");
  assert.ok(app.shown().includes("변경 2건"));
  assert.equal(useDraftStore.getState().drafts["/detection/rules"].dirty, true, "leave guard sees the draft");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation opened");
  const preview = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.equal(preview.expectedRevision, 12);
  assert.deepEqual(preview.hardRules, [{ id: "hard:process-status", enabled: true, severity: { byCode: STATUS_DEFAULT } }, { id: "hard:negative-duration", enabled: false }]);
  assert.deepEqual(preview.softRules, [{ id: softRule.id, name: softRule.name, field: "elapsed_ms", operator: "gt", threshold: 8000, severity: "warning", enabled: true, scope: null }]);
  assert.deepEqual([preview.relationRules, preview.protocolRules], [[], []], "v3 body always carries both arrays");
  assert.equal(calls("PUT", "/api/rule-set").length, 0, "nothing is saved before the confirmation");
  const body = await renderSaveBody(app);
  const bodyText = () => text(body.toJSON());
  assert.ok(bodyText().includes("이상 규칙 끄기 · 시작 시각이 종료 시각보다 늦음 · 명백한 이상을 놓칠 수 있습니다"));
  assert.ok(bodyText().includes("이상 징후 규칙 수정 · 처리 시간 5초 초과 · 임계값 5,000 → 8,000"));
  assert.ok(bodyText().includes("꺼지는 이상 규칙 1개를 확인했고 이대로 적용합니다") && bodyText().includes("저장 이후 새 판정부터 적용됩니다."));
  const go = () => body.root.findAll((node) => node.type === "button" && text(node.children) === "저장하고 적용")[0];
  assert.equal(go().props.disabled, true, "confirmation check is required for a newly disabled rule");
  await act(async () => body.root.findByProps({ id: "rule-save-confirm", "data-slot": "checkbox" }).props.onCheckedChange(true));
  assert.equal(go().props.disabled, false);
  await act(async () => go().props.onClick());
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13"), "success text");
  assert.equal(calls("PUT", "/api/rule-set").at(-1).body.confirmHardRuleDisable, true);
  await wait(() => app.shown().includes("변경 0건"), "draft follows the saved state");
  assert.equal(useDraftStore.getState().drafts["/detection/rules"], undefined, "saved draft is cleared");
  await act(async () => body.unmount());
  await app.cleanup();
}

// 3) 이미 꺼진 규칙: 확인창 강조는 남고 체크는 요구하지 않는다.
{
  routes = baseRoutes({
    "GET /api/rule-set": () => json(state(12, { hardRules: hardRules(false) })),
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: ["hard:negative-duration"], changes: [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 6000] } }] }),
  });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  const body = await renderSaveBody(app);
  assert.ok(text(body.toJSON()).includes("이상 규칙 꺼짐 · 시작 시각이 종료 시각보다 늦음 · 명백한 이상을 놓칠 수 있습니다"));
  assert.ok(!text(body.toJSON()).includes("확인했고"));
  assert.equal(body.root.findAll((node) => node.type === "button" && text(node.children) === "저장하고 적용")[0].props.disabled, false);
  await act(async () => body.unmount());
  await app.cleanup();
}

// 4) 실패: 서버 검증 실패·저장 실패는 코드 그대로, 편집 유지. 리비전 충돌은 새로 불러오기 안내.
{
  let current = state();
  let previewResult = () => json({ valid: false, error: { code: "soft_rule_threshold_invalid:soft:elapsed_ms-3f9a2c1d" } });
  routes = baseRoutes({
    "GET /api/rule-set": () => json(current),
    "POST /api/rule-set/preview": () => previewResult(),
  });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "1.23456789012345678");
  await app.click("저장");
  await wait(() => app.shown().includes("저장 실패 · soft_rule_threshold_invalid:soft:elapsed_ms-3f9a2c1d · 운영 룰은 버전 12 그대로입니다. 편집 내용은 남아 있습니다."), "validation code shown");
  assert.equal(app.renderer.root.findAllByType(RuleSaveDialog).length, 0, "no confirmation on validation failure");
  assert.ok(app.shown().includes("변경 1건"), "edit kept");
  assert.ok(text(app.renderer.root.find((node) => node.type === "div" && node.props["data-rule-sticky"] !== undefined).children).includes("저장 실패 ·"), "save result stays with the save bar");
  previewResult = () => {
    current = state(14, { savedBy: { actorId: "2", displayName: "other@example.test" } });
    return json({ code: "rule_set_revision_conflict", currentRevision: 14 }, 409);
  };
  await app.click("저장");
  await wait(() => app.shown().includes("저장 거절 · rule_set_revision_conflict · 다른 사람이 먼저 저장했습니다(현재 버전 14). 운영 룰은 버전 14 그대로이며"), "conflict banner");
  assert.ok(app.shown().includes("현재 운영 버전 14") && app.shown().includes("변경 1건"), "header refreshed, edit kept until reload");
  await app.click("새로 불러오기");
  await wait(() => app.shown().includes("변경 0건"), "reload resets the draft");
  assert.ok(!app.shown().includes("저장 거절"));
  await app.cleanup();
}

// 3b) 코드별 위험도: 선택하면 줄이 위험도 순서로 옮기고, 변경 건수·저장 본문·확인창·취소·초안 복원에 반영된다.
{
  routes = baseRoutes({ "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [
    { type: "hard_updated", id: "hard:process-status", displayName: "상태가 S가 아님", diff: { severity: [{ byCode: STATUS_DEFAULT }, { byCode: { ...STATUS_DEFAULT, PROCESS_STATUS_N: "warning" } }] } }] }) });
  const focused = [];
  const app = await mount("admin", { createNodeMock: (element) => (element.type === "select" ? { focus: () => focused.push(element.props["aria-label"]) } : null) });
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  const select = (label) => app.renderer.root.findAll((node) => node.type === "select" && node.props["aria-label"] === label)[0];
  const order = () => app.renderer.root.findAll((node) => node.type === "span" && node.props["data-severity-code"]).map((node) => node.props["data-severity-code"].split("_").at(-1));
  await act(async () => select("N 미처리 위험도").props.onChange({ target: { value: "warning" } }));
  assert.ok(app.shown().includes("변경 1건"));
  assert.deepEqual(order(), ["F", "P", "Z", "N", "H"], "the line moves to its new level");
  assert.equal(focused.at(-1), "N 미처리 위험도", "focus returns to the moved select");
  assert.equal(select("N 미처리 위험도").props.value, "warning", "the selection stays");
  assert.deepEqual(severityGroupsOf(app.renderer.root.find((node) => node.type === "tr" && node.props["data-rule"] === "hard:process-status")).map(({ level, codes }) => [level, codes.length]),
    [["critical", 1], ["warning", 3], ["info", 1]], "group sizes follow the codes");
  assert.equal(useDraftStore.getState().drafts["/detection/rules"].dirty, true, "leave guard covers severity");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.hardRules, [{ id: "hard:process-status", enabled: true, severity: { byCode: { ...STATUS_DEFAULT, PROCESS_STATUS_N: "warning" } } }, { id: "hard:negative-duration", enabled: true }]);
  const body = await renderSaveBody(app);
  assert.ok(text(body.toJSON()).includes("이상 규칙 수정 · 상태가 S가 아님 · 위험도 N 미처리 참고 → 주의"), "only the changed code is listed");
  assert.ok(!text(body.toJSON()).includes("확인했고"), "severity changes need no off confirmation");
  await act(async () => body.unmount());
  await act(async () => app.renderer.root.findByType(RuleSaveDialog).props.onCancel());
  await toggle(app, "상태가 S가 아님 켜기", false);
  assert.ok(app.shown().includes("변경 2건"), "enabled and severity are two changes");
  await toggle(app, "상태가 S가 아님 켜기", true);
  await act(async () => select("N 미처리 위험도").props.onChange({ target: { value: "info" } }));
  assert.ok(app.shown().includes("변경 0건"), "back to the saved level is no change");
  await act(async () => select("H 부분성공 위험도").props.onChange({ target: { value: "critical" } }));
  await app.click("변경 취소");
  await act(async () => app.renderer.root.findAllByType(ConfirmDialog)[0].props.onConfirm());
  assert.equal(select("H 부분성공 위험도").props.value, "info", "cancel restores the level");
  await act(async () => select("Z 데이터없음 위험도").props.onChange({ target: { value: "critical" } }));
  await app.cleanup();
  const back = await mount("admin", { keepDrafts: true });
  await wait(() => back.shown().includes("현재 운영 버전 12"), "header after return");
  assert.equal(back.renderer.root.findAll((node) => node.type === "select" && node.props["aria-label"] === "Z 데이터없음 위험도")[0].props.value, "critical", "draft restore keeps the level");
  assert.ok(back.shown().includes("변경 1건"));
  await back.cleanup();
  // 읽기 전용 byCode·코드 추가: 코드 수정 없이 위험도별로 묶어 뜻과 함께 표시한다.
  const addedRule = { id: "hard:count-conservation", methodVersion: "count-conservation-v1", displayName: "건수 합계 불일치", conditionText: "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT", severity: "critical", enabled: false, editable: ["enabled"] };
  routes = baseRoutes({ "GET /api/rule-set": () => json(state(12, { hardRules: [{ ...hardRules()[0], editable: ["enabled"], severity: { byCode: { ...STATUS_DEFAULT, PROCESS_STATUS_Q: "critical" } } }, hardRules()[1], addedRule] })) });
  const readOnly = await mount();
  await wait(() => readOnly.shown().includes("현재 운영 버전 12"), "header");
  const readGroups = severityGroupsOf(readOnly.renderer.root.find((node) => node.type === "tr" && node.props["data-rule"] === "hard:process-status"));
  assert.deepEqual(readGroups.map(({ level, codes }) => [level, codes]), [["critical", ["F 실패", "Q"]], ["warning", ["P 처리중", "Z 데이터없음"]], ["info", ["H 부분성공", "N 미처리"]]]);
  assert.ok(readGroups.every(({ stretched }) => stretched), "read-only groups have the same structure");
  assert.ok(readOnly.shown().includes("건수 합계 불일치") && readOnly.shown().includes("TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT"), "a longer rule list needs no code change");
  await readOnly.cleanup();
}

// 4b) 충돌 뒤 재조회가 실패해도 응답의 현재 버전을 표시한다.
{
  routes = baseRoutes({ "POST /api/rule-set/preview": () => {
    routes["GET /api/rule-set"] = () => json({ code: "rule_set_storage_unavailable" }, 503);
    return json({ code: "rule_set_revision_conflict", currentRevision: 14 }, 409);
  } });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  await app.click("저장");
  await wait(() => app.shown().includes("다른 사람이 먼저 저장했습니다(현재 버전 14). 운영 룰은 버전 14 그대로이며"), "reported revision wins");
  await app.cleanup();
}

// 4b-2) 재조회 실패 뒤 새로 불러오기: 실제로 다시 읽고, 실패하면 편집을 두고, 성공해야 새 상태로 맞춘다.
{
  routes = baseRoutes({ "POST /api/rule-set/preview": () => {
    routes["GET /api/rule-set"] = () => json({ code: "rule_set_storage_unavailable" }, 503);
    return json({ code: "rule_set_revision_conflict", currentRevision: 14 }, 409);
  } });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  await app.click("저장");
  await wait(() => app.shown().includes("현재 버전 14"), "conflict banner");
  const reads = calls("GET", "/api/rule-set").length;
  await app.click("새로 불러오기");
  await wait(() => calls("GET", "/api/rule-set").length > reads, "reload reads the server");
  await tick(30);
  assert.ok(app.shown().includes("변경 1건") && app.shown().includes("현재 운영 룰 조회 실패 · rule_set_storage_unavailable"), "failed reload keeps the edit and shows the read failure");
  routes["GET /api/rule-set"] = () => json(state(14));
  await app.click("새로 불러오기");
  await wait(() => app.shown().includes("현재 운영 버전 14") && app.shown().includes("변경 0건"), "successful reload resets to the server state");
  assert.ok(!app.shown().includes("저장 거절") && !app.shown().includes("조회 실패"));
  await app.cleanup();
}

// 4b-3) 저장 뒤 재조회 실패: 저장된 버전만 알리고 옛 상태의 저장 버튼이 남지 않는다. 다시 조회하면 새 상태로 열린다.
{
  const saved = () => json({ revision: 13, previousRevision: 12, contentHash: "c".repeat(64), appliedAt: "2026-11-02T08:20:05Z", appliesFrom: "next_decision", changes: [], summary: {} });
  routes = baseRoutes({
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 6000] } }] }),
    "PUT /api/rule-set": () => {
      routes["GET /api/rule-set"] = () => json({ code: "rule_set_storage_unavailable" }, 503);
      return saved();
    },
  });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13"), "saved revision is reported");
  assert.ok(app.shown().includes("현재 운영 룰 조회 실패 · rule_set_storage_unavailable"), "existing read failure");
  assert.equal(app.button("저장"), undefined, "no save button on the old state");
  assert.ok(!app.shown().includes("현재 운영 버전 12") && !app.shown().includes("변경 1건"), "old header and draft are gone");
  assert.equal(useDraftStore.getState().drafts["/detection/rules"], undefined);
  routes["GET /api/rule-set"] = () => json(state(13, { softRules: [{ ...softRule, threshold: 6000 }] }));
  await app.click("다시 조회");
  await wait(() => app.shown().includes("현재 운영 버전 13") && app.shown().includes("변경 0건"), "editor reopens on the new state");
  await app.cleanup();
}

// 4b-4) 저장 실패 뒤 편집 잠금이 풀린다.
{
  routes = baseRoutes({
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 6000] } }] }),
    "PUT /api/rule-set": () => json({ code: "rule_set_storage_unavailable" }, 503),
  });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("저장 실패 · rule_set_storage_unavailable · 운영 룰은 버전 12 그대로입니다. 편집 내용은 남아 있습니다."), "save failure");
  const fieldset = app.renderer.root.findAll((node) => node.type === "fieldset")[0];
  assert.equal(fieldset.props.disabled, false, "editing is unlocked after a failed save");
  assert.equal(fieldset.props["aria-label"], "이상 규칙·이상 징후 규칙");
  assert.ok(app.shown().includes("변경 1건"));
  await app.cleanup();
}

// 4c) 미리보기 대기 중에는 편집 영역이 잠겨 그 사이 입력이 사라지지 않는다.
{
  let release;
  routes = baseRoutes({ "POST /api/rule-set/preview": () => new Promise((resolve) => { release = () => resolve(json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 6000] } }] })); }) });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  // 대기 중이 아닌 화면은 요청이 늘지 않는다(렌더링이 조회를 다시 일으키지 않음).
  const idleCount = requested.length;
  await tick(100);
  assert.equal(requested.length, idleCount, "idle page makes no further requests");
  const locked = () => app.renderer.root.findAll((node) => node.type === "fieldset")[0].props.disabled;
  assert.equal(locked(), false);
  await setThreshold(app, "6000");
  await app.click("저장");
  await wait(() => typeof release === "function", "preview pending");
  assert.equal(locked(), true, "rule tables are locked while the preview is pending");
  await act(async () => release());
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  assert.equal(locked(), false);
  await app.cleanup();
}

// 5) 입력 확인: 빈 임계값이면 저장 비활성·안내. 변경 건수는 저장 값 기준.
{
  routes = baseRoutes();
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "");
  assert.ok(app.shown().includes(`확인 필요 · ${softRule.name}: 임계값을 숫자로 입력해 주세요`));
  assert.equal(app.button("저장").props.disabled, true);
  await setThreshold(app, "5000.0");
  assert.ok(app.shown().includes("변경 0건"), "same number is no change");
  const nameInput = () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "규칙 이름")[0];
  await act(async () => nameInput().props.onChange({ target: { value: `  ${softRule.name} ` } }));
  assert.ok(app.shown().includes("변경 0건"), "surrounding spaces are no change");
  assert.equal(app.button("저장").props.disabled, true);
  assert.equal(useDraftStore.getState().drafts["/detection/rules"], undefined, "no draft to protect");
  await toggle(app, "시작 시각이 종료 시각보다 늦음 켜기", false);
  assert.ok(app.shown().includes("변경 1건"));
  await toggle(app, "시작 시각이 종료 시각보다 늦음 켜기", true);
  assert.ok(app.shown().includes("변경 0건"), "turning a rule back on restores the state");
  await app.cleanup();
}

// 5b) 변경 취소: 확인창 돌아가기는 유지, 변경 버리기는 현재 운영 룰로.
{
  routes = baseRoutes();
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  const confirm = () => app.renderer.root.findAllByType(ConfirmDialog)[0];
  const threshold = () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${softRule.name} 임계값`)[0].props.value;
  await app.click("변경 취소");
  assert.deepEqual([confirm().props.title, confirm().props.actionText, confirm().props.message], ["변경 취소", "변경 버리기", "편집한 변경 1건을 버리고 현재 운영 룰(버전 12)로 되돌립니다."]);
  await act(async () => confirm().props.onCancel());
  assert.equal(threshold(), "6000", "going back keeps the edit");
  await app.click("변경 취소");
  await act(async () => confirm().props.onConfirm());
  assert.equal(threshold(), "5000");
  assert.ok(app.shown().includes("변경 0건"));
  assert.equal(useDraftStore.getState().drafts["/detection/rules"], undefined);
  await app.cleanup();
}

// 5c) 초안 복원: 화면을 떠났다 돌아오면 세션 초안이 입력칸으로 돌아온다.
{
  routes = baseRoutes();
  const first = await mount();
  await wait(() => first.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(first, "6000");
  await first.cleanup();
  assert.equal(useDraftStore.getState().drafts["/detection/rules"].dirty, true, "leaving keeps the session draft");
  const back = await mount("admin", { keepDrafts: true });
  await wait(() => back.shown().includes("현재 운영 버전 12"), "header after return");
  assert.equal(back.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${softRule.name} 임계값`)[0].props.value, "6000");
  assert.ok(back.shown().includes("변경 1건"));
  await back.cleanup();
}

// 5d) 계정 전환: 이전 계정의 초안이 다음 계정으로 넘어가지 않는다.
{
  routes = baseRoutes();
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await setThreshold(app, "6000");
  assert.equal(useDraftStore.getState().drafts["/detection/rules"].dirty, true);
  const next = { id: "admin-2", email: "next@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" };
  currentUser = next;
  await act(async () => app.session().login({ token: "token-next", user: next }));
  await wait(() => app.shown().includes("변경 0건"), "new account starts from the server state");
  assert.equal(app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${softRule.name} 임계값`)[0].props.value, "5000");
  assert.equal(useDraftStore.getState().drafts["/detection/rules"], undefined, "old draft is gone");
  await app.cleanup();
}

// 5e) 변경 기록 더 보기: 마지막 쪽에서 버튼이 사라지고 행이 중복되지 않는다.
{
  const pages = {
    "": { items: [historyItem(12), historyItem(11)], pagination: { limit: 50, hasMore: true, nextCursor: "page-2" } },
    "page-2": { items: [historyItem(10)], pagination: { limit: 50, hasMore: true, nextCursor: "page-3" } },
    "page-3": { items: [historyItem(0)], pagination: { limit: 50, hasMore: false, nextCursor: null } },
  };
  routes = baseRoutes({ "GET /api/rule-set/history": (target) => json(pages[target.searchParams.get("cursor") ?? ""]) });
  const app = await mount();
  const rows = () => app.renderer.root.findAll((node) => node.type === "tr" && node.props["data-revision"] !== undefined).map((node) => node.props["data-revision"]);
  await wait(() => rows().length === 2, "first page");
  await app.click("더 보기");
  await wait(() => rows().length === 3, "second page");
  await app.click("더 보기");
  await wait(() => rows().length === 4, "last page");
  assert.deepEqual(rows(), [12, 11, 10, 0], "no duplicated rows");
  assert.equal(app.button("더 보기"), undefined, "button is gone at the end");
  await app.cleanup();
}

// 5e-2) 머리글·저장 줄의 실제 높이를 CSS 변수로 알리고, 크기가 바뀌면 갱신하며, 화면을 떠나면 지운다.
{
  const props = new Map();
  const style = { setProperty: (key, value) => props.set(key, value), removeProperty: (key) => props.delete(key) };
  const nodes = { header: { offsetHeight: 134, ownerDocument: { documentElement: { style } } }, sticky: { offsetHeight: 90, ownerDocument: { documentElement: { style } } } };
  const observers = [];
  const RealObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(node) { this.node = node; }
    unobserve() {}
    disconnect() { this.closed = true; }
  };
  routes = baseRoutes();
  const app = await mount("admin", { createNodeMock: (element) => (element.type === "header" ? nodes.header : element.props["data-rule-sticky"] !== undefined ? nodes.sticky : null) });
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await wait(() => props.get("--sticky-block-height") === "90px", "sticky height published");
  assert.equal(props.get("--top-nav-offset"), "134px", "real header height, not the token default");
  nodes.header.offsetHeight = 65;
  await act(async () => observers.find((observer) => observer.node === nodes.header).callback());
  assert.equal(props.get("--top-nav-offset"), "65px", "resize updates the offset");
  await app.cleanup();
  assert.ok(observers.filter((observer) => observer.node).every((observer) => observer.closed), "observers are released");
  assert.equal(props.size, 0, "variables fall back to the tokens");
  globalThis.ResizeObserver = RealObserver;
}

// 5f) 현재와 내용이 같은 기록은 되돌리기 비활성. hash가 같아도 이름이 다르면, hash 버전이 달라도 내용이 같으면 서버 판단을 따른다.
{
  const hash = "a".repeat(64);
  const snapshot = (name) => ({ hardRules: hardRules(), softRules: [{ ...softRule, name }] });
  const noop = new Set([11, 0]);
  routes = baseRoutes({
    "GET /api/rule-set/history": () => json({ items: [historyItem(12, [], { contentHash: hash }), historyItem(11, [], { contentHash: hash }), historyItem(10, [], { contentHash: hash }), historyItem(9),
      historyItem(1, [], { contentHashVersion: 1 }), historyItem(0, [], { contentHashVersion: 1 })], pagination: { limit: 50, hasMore: false, nextCursor: null } }),
    "GET /api/rule-set/history/11": () => json({ ...historyItem(11), snapshot: snapshot(softRule.name) }),
    "GET /api/rule-set/history/10": () => json({ ...historyItem(10), snapshot: snapshot("옛 이름") }),
    "GET /api/rule-set/history/1": () => json({ ...historyItem(1), snapshot: snapshot(softRule.name) }),
    "GET /api/rule-set/history/0": () => json({ ...historyItem(0), snapshot: snapshot(softRule.name) }),
    "POST /api/rule-set/preview": (_target, options) => {
      const { restoreFromRevision } = JSON.parse(options.body);
      return json(noop.has(restoreFromRevision) ? { valid: false, error: { code: "rule_set_unchanged" } } : { valid: true, summary: {}, hardDisabledAfterSave: [], changes: [] });
    },
  });
  const app = await mount();
  const reverts = () => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "이 상태로 되돌리기");
  await wait(() => reverts().length === 5 && reverts()[0].props.disabled === true && reverts()[4].props.disabled === true, "same content rows are disabled");
  await wait(() => calls("POST", "/api/rule-set/preview").length >= 4, "rows that need the server were asked");
  await tick();
  assert.deepEqual(reverts().map((button) => button.props.disabled), [true, false, false, false, true], "same hash and same content, name-only difference, other hash, other hash version with other content, other hash version with same content");
  assert.equal(calls("GET", "/api/rule-set/history/9").length, 0, "same version with another hash needs no check");
  await app.cleanup();
}

// 6) 되돌리기: 스냅샷 값 그대로 restoreFromRevision을 보내고 편집 중 변경이 버려짐을 안내한다.
{
  const savedSeverity = { byCode: { ...STATUS_DEFAULT, PROCESS_STATUS_N: "critical" } };
  const snapshot = { hardRules: [{ ...hardRules()[0], severity: savedSeverity }, hardRules()[1]], softRules: [{ ...softRule, threshold: 4000 }] };
  routes = baseRoutes({
    "GET /api/rule-set/history/10": () => json({ ...historyItem(10), snapshot }),
    "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 4000] } }] }),
  });
  const app = await mount();
  await wait(() => app.shown().includes("이 상태로 되돌리기"), "history loaded");
  await setThreshold(app, "6000");
  await app.click("이 상태로 되돌리기", 0);
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "restore confirmation");
  const pending = pendingOf(app);
  assert.equal(pending.title, "버전 10 상태로 되돌리기");
  assert.equal(pending.lead, "현재 버전 12에서 버전 10 상태를 새 버전으로 다시 적용합니다. 변경 1건 · 편집 중인 변경 1건은 버려집니다");
  assert.equal(pending.actionText, "되돌리고 적용");
  const sent = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.deepEqual([sent.expectedRevision, sent.restoreFromRevision], [12, 10]);
  assert.deepEqual(sent.hardRules, [{ id: "hard:process-status", enabled: true, severity: savedSeverity }, { id: "hard:negative-duration", enabled: true }], "severity only for the editable rule");
  assert.deepEqual(sent.softRules, [{ id: softRule.id, name: softRule.name, field: "elapsed_ms", operator: "gt", threshold: 4000, severity: "warning", enabled: true, scope: null }]);
  assert.deepEqual([sent.relationRules, sent.protocolRules], [[], []], "a v2 snapshot restores to no relation or protocol items");
  await app.cleanup();
}

// 6b) 되돌리기 실패: 코드는 그대로, 초안과 무관하므로 편집 유지 안내는 없다(저장 경로 안내는 4번에서 확인).
{
  routes = baseRoutes({
    "GET /api/rule-set/history/10": () => json({ ...historyItem(10), snapshot: { hardRules: hardRules(), softRules: [softRule] } }),
    "POST /api/rule-set/preview": () => json({ valid: false, error: { code: "rule_set_unchanged" } }),
  });
  const app = await mount();
  await wait(() => app.shown().includes("이 상태로 되돌리기"), "history loaded");
  await app.click("이 상태로 되돌리기", 0);
  await wait(() => app.shown().includes("저장 실패 · rule_set_unchanged · 운영 룰은 버전 12 그대로입니다."), "restore failure code");
  assert.ok(!app.shown().includes("편집 내용은 남아 있습니다"), "no edit notice on the restore path");
  routes["GET /api/rule-set/history/10"] = () => json({ code: "rule_set_storage_unavailable" }, 503);
  await app.click("이 상태로 되돌리기", 0);
  await wait(() => app.shown().includes("저장 실패 · rule_set_storage_unavailable · 운영 룰은 버전 12 그대로입니다."), "snapshot read failure");
  assert.ok(!app.shown().includes("편집 내용은 남아 있습니다"));
  await app.cleanup();
}

// 7) 수식 추천: 기간 선택 → 계산 → 적용은 입력칸만(서버 저장 없음). 해당 없음·오류는 코드·사유 표시.
{
  let stats = () => json({ field: "elapsed_ms", statistic: "p95", operator: "gt", threshold: "7210.5", displayThreshold: "7211", unit: "ms", source: "data_p95", status: "proposed", rationale: null,
    evidence: { n: 51230, min: 12, max: 184233, p95: 7210.5, quantileMethod: "percentile_cont", scope: "period", period: { mode: "recent_1m", from: "2026-10-04T00:00:00Z", until: "2026-11-04T00:00:00Z", timeBasis: "esb_occurred_at", resolvedFrom: null } } });
  routes = baseRoutes({ "POST /api/rule-set/threshold-stats": () => stats() });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  const savesBefore = calls("PUT", "/api/rule-set").length;
  await app.click("수식 추천");
  await wait(() => app.shown().includes("수식 추천 기준 기간") && app.button("계산")?.props.disabled === false, "formula row");
  const periods = app.renderer.root.findByProps({ id: `formula-period-${softRule.id}`, "data-slot": "native-select" });
  assert.deepEqual(periods.findAllByType("option").map((option) => text(option.children)), ["운영 머신러닝 모델 학습 기간 시작 ~ 현재", "최근 1개월", "최근 3개월", "직접 지정"]);
  await act(async () => periods.props.onChange({ target: { value: "recent_1m" } }));
  await app.click("계산");
  await wait(() => app.shown().includes("수식 추천 · 데이터 p95 7211 ms"), "result");
  assert.ok(app.shown().includes("표본 51,230건") && app.shown().includes("(최근 1개월) · LLM 미사용"));
  // 수식 추천 기준 문구(사용자 11/14): 기간 전체 p95.
  assert.deepEqual(app.renderer.root.findAll((node) => node.type === "span" && node.props["data-formula-basis"] !== undefined).map((node) => text(node.children)), ["기준 · 기간 전체 p95"]);
  const sent = calls("POST", "/api/rule-set/threshold-stats").at(-1).body;
  assert.deepEqual([sent.field, sent.statistic, sent.period], ["elapsed_ms", "p95", { mode: "recent_1m", timeBasis: "persisted_at" }]);
  assert.equal(app.renderer.root.find((node) => node.type === "strong" && node.props.title === "원값 7210.5") !== undefined, true, "raw value tooltip");
  await app.click("적용");
  assert.equal(app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${softRule.name} 임계값`)[0].props.value, "7210.5");
  assert.ok(app.shown().includes("변경 1건"));
  assert.equal(calls("PUT", "/api/rule-set").length, savesBefore, "apply only fills the input");
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
  await app.cleanup();
}

// 7b) 수식 추천 직접 지정: 두 시각을 넣어야 계산할 수 있고, 화면 입력이 오프셋 있는 시각과 저장 시각 기준으로 요청에 들어간다. 최근 3개월도 같은 기준.
{
  const stats = () => json({ field: "elapsed_ms", statistic: "p95", operator: "gt", threshold: "10", displayThreshold: "10", unit: "ms", source: "data_p95", status: "proposed", rationale: null,
    evidence: { n: 30, min: 1, max: 20, p95: 10, period: { mode: "custom", from: "2026-10-01T00:00:00Z", until: "2026-10-02T00:00:00Z" } } });
  routes = baseRoutes({ "POST /api/rule-set/threshold-stats": () => stats() });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 버전 12"), "header");
  await app.click("수식 추천");
  await wait(() => app.button("계산")?.props.disabled === false, "formula row");
  const select = () => app.renderer.root.findByProps({ id: `formula-period-${softRule.id}`, "data-slot": "native-select" });
  const dateInput = (label) => app.renderer.root.findByProps({ "aria-label": label, "data-slot": "input" });
  await act(async () => select().props.onChange({ target: { value: "custom" } }));
  assert.equal(app.button("계산").props.disabled, true, "both times are required");
  await act(async () => dateInput("시작").props.onChange({ target: { value: "2026-10-01T09:00" } }));
  assert.equal(app.button("계산").props.disabled, true);
  await act(async () => dateInput("종료").props.onChange({ target: { value: "2026-10-02T09:30" } }));
  assert.equal(app.button("계산").props.disabled, false);
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
  await app.cleanup();
}

// 7c) 삭제: 확인창 전에는 초안이 그대로고, 돌아가기는 유지, 삭제는 초안에서만 빼며 저장 요청에서 빠진다.
{
  routes = baseRoutes({ "POST /api/rule-set/preview": () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_removed", id: softRule.id, name: softRule.name }] }) });
  const app = await mount();
  await wait(() => app.button("규칙 추가")?.props.disabled === false, "fields loaded");
  const savesBefore = calls("PUT", "/api/rule-set").length;
  const confirm = () => app.renderer.root.findAllByType(ConfirmDialog)[0];
  await app.click("삭제");
  assert.deepEqual([confirm().props.title, confirm().props.actionText], ["이상 징후 규칙 삭제", "삭제"]);
  assert.equal(confirm().props.message, `${softRule.name} 규칙(처리 시간 > 5,000 ms)을 삭제합니다. 저장해야 운영에 반영됩니다.`);
  assert.ok(app.shown().includes("변경 0건"), "nothing changes before the confirmation");
  await act(async () => confirm().props.onCancel());
  assert.equal(confirm(), undefined);
  assert.ok(app.shown().includes("변경 0건") && app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === "규칙 이름").length === 1, "cancel keeps the rule");
  await app.click("삭제");
  await act(async () => confirm().props.onConfirm());
  assert.equal(confirm(), undefined);
  assert.ok(app.shown().includes("변경 1건") && app.shown().includes("이상 징후 규칙: 표시할 항목이 없습니다."), "rule leaves the draft");
  assert.equal(calls("PUT", "/api/rule-set").length, savesBefore, "delete alone saves nothing");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.softRules, [], "removed rule is not in the request");
  await app.cleanup();
}

// 7d) AI 규칙 추천: 계약 경로 요청, softRuleIds로만 연결, 제안만 선택, 적용은 입력칸만, 결과 검증, 작업 실패와 조회 실패 구분, 버전 변경, 서버 거절.
{
  const { readFile } = await import("node:fs/promises");
  const doc = await readFile(new URL("../../contracts/llm-recommendation-job.md", import.meta.url), "utf8");
  const contractResult = JSON.parse(doc.slice(doc.indexOf("## 결과 JSON")).split("```json")[1].split("```")[0]);
  const { basisLabel, heldWindowExcludedRows, parseRecommendation, progressText, recommendationProgress } = await import("../src/features/detection/rules/ruleAiApi.ts");
  const v3Result = JSON.parse(doc.slice(doc.indexOf("### 결과 추가 필드")).split("```json")[1].split("```")[0]);
  assert.deepEqual([basisLabel("train_reference_80_p95"), basisLabel("train_p95"), basisLabel(undefined), basisLabel("other"), basisLabel("toString")],
    ["기준 · 학습 기간 앞 80% p95", null, null, null, null], "only the Rule v3 basis has a label");
  assert.deepEqual(recommendationProgress({ progress: { processed: 0, total: 3 } }), { processed: 0, total: 3 });
  assert.deepEqual(recommendationProgress({ progress: { processed: 3, total: 3 } }), { processed: 3, total: 3 });
  for (const invalid of [null, {}, { progress: null }, { progress: { processed: 1 } }, { progress: { processed: 4, total: 3 } }, { progress: { processed: -1, total: 3 } },
    { progress: { processed: 0, total: 0 } }, { progress: { processed: 1.5, total: 3 } }, { progress: { processed: "1", total: 3 } }]) {
    assert.equal(recommendationProgress(invalid), null, JSON.stringify(invalid));
  }
  assert.equal(progressText({ processed: 2, total: 6 }), "처리 2/6", "approved progress wording (user 11/14)");
  // 보류 창 제외(결과 최상위 heldWindowExclusion): 객체일 때만 행 수, null·없음·형식 다름은 표시 안 함.
  assert.equal(heldWindowExcludedRows(v3Result), 9370);
  assert.equal(heldWindowExcludedRows({ heldWindowExclusion: { from: "x", until: "y", excludedRows: 0 } }), 0);
  for (const hidden of [contractResult, {}, null, { heldWindowExclusion: null }, { heldWindowExclusion: { excludedRows: -1 } }, { heldWindowExclusion: { excludedRows: "9370" } }, { heldWindowExclusion: [] }]) {
    assert.equal(heldWindowExcludedRows(hidden), null, JSON.stringify(hidden));
  }

  const retryRule = { ...softRule, id: "soft:retry_count-91ab77e0", name: "재시도 3회 이상", field: "retry_count", operator: "gte", threshold: 3, unit: "count", severity: "info" };
  const offRule = { ...softRule, id: "soft:error_ratio-0000aaaa", name: "꺼진 비율 규칙", field: "error_ratio", threshold: 0.02, unit: "ratio", enabled: false };
  const noResultRule = { ...softRule, id: "soft:success_count-11112222", name: "결과 없는 규칙", field: "success_count", operator: "lt", threshold: 2, unit: "count" };
  const ratioRule = { ...softRule, id: "soft:error_ratio-5d2e0b19", name: "오류 비율 규칙", field: "error_ratio", threshold: 0.01, unit: "ratio" };
  const totalRule = { ...softRule, id: "soft:total_count-0a1b2c3d", name: "총 건수 규칙", field: "total_count", threshold: 10000, unit: "count" };
  const sameFieldRule = { ...softRule, id: "soft:elapsed_ms-99999999", name: "같은 필드 다른 규칙", threshold: 9000 };
  const current = state(12, { softRules: [softRule, ratioRule, retryRule, totalRule, offRule, noResultRule, sameFieldRule], counts: { hardEnabled: 2, hardTotal: 2, softEnabled: 6, softTotal: 7 } });
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
  const jobGets = (id) => calls("GET", `/api/jobs/${id}`).length;
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
    const body = await jobs.get(match[1])();
    return body;
  };
  const before = calls("POST", "/api/jobs").length;
  const jobCalls = () => calls("POST", "/api/jobs");
  const app = await mount();
  await wait(() => app.button("AI 규칙 추천")?.props.disabled === false, "button enabled");
  const box = (name) => app.renderer.root.findAll((node) => node.type === "div" && node.props["data-rule-ai"] === name)[0];
  const thresholdOf = (name) => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${name} 임계값`)[0].props.value;
  await app.click("AI 규칙 추천");
  await wait(() => box("running") || box("done"), "started");
  await wait(() => box("done"), "result table");
  const sent = jobCalls().at(-1).body;
  assert.equal(jobCalls().length, before + 1);
  assert.deepEqual([sent.kind, sent.ruleSetRevision, sent.scope, sent.mode, sent.data.snapshotId, sent.data.timeBasis],
    ["recommend_thresholds", 12, { softRuleIds: null }, "data_p95_with_llm_rationale", null, "persisted_at"]);
  assert.ok(Date.parse(sent.data.from) < Date.parse(sent.data.until) && Date.parse(sent.data.until) - Date.parse(sent.data.from) <= 92 * 86400000);
  const rows = () => app.renderer.root.findAll((node) => node.type === "tr" && node.props["data-ai-row"] !== undefined);
  const row = (id) => rows().find((node) => node.props["data-ai-row"] === id);
  assert.deepEqual(rows().map((node) => node.props["data-ai-row"]), [softRule.id, ratioRule.id, retryRule.id, totalRule.id], "rows are the enabled rules listed in softRuleIds; same-field, off and result-less rules are not");
  assert.ok(text(box("done").children).includes("켜진 이상 징후 규칙 4개 · 제안 2건"));
  assert.equal(contractResult.heldWindowExclusion, null);
  assert.ok(!text(box("done").children).includes("보류 기간 제외") && box("done").findAll((node) => node.props["data-ai-held"] !== undefined).length === 0, "no held-window line when heldWindowExclusion is null");
  assert.equal(box("done").findAll((node) => node.type === "table" && node.props["aria-label"] === "AI 규칙 추천").length, 1);
  const heads = box("done").findAll((node) => node.type === "th").map((node) => text(node.children));
  assert.deepEqual(heads, ["선택", "규칙(대상 필드)", "임계값", "상태", "근거"], "five columns");
  const proposed = text(row(softRule.id).children);
  assert.ok(proposed.includes("> 5,000 ms → > 7,211 ms"), proposed);
  assert.ok(proposed.includes("데이터 p95 · 표본 46,980 · min 12 · max 184,233 · p95 7,210.5"), "source leads the evidence line");
  assert.ok(proposed.includes("The 95th percentile of 46,980 successful process durations"), "rationale stays as returned");
  assert.equal(contractResult.items[0].basis, "train_p95");
  assert.equal(box("done").findAll((node) => node.props["data-ai-basis"] !== undefined).length, 0, "train_p95 and a missing basis show no basis line");
  assert.ok(!text(box("done").children).includes("기준 ·"));
  for (const hidden of ["m_…", "b_…", "linear_(n-1)*p", "explicit anomaly labels", "reviews", "rule_v2"]) assert.ok(!text(box("done").children).includes(hidden), `${hidden} is not on the screen`);
  assert.ok(row(softRule.id).findAll((node) => node.type === "span" && node.props.title === "원값 7210.5").length === 1, "raw value tooltip");
  assert.ok(text(row(retryRule.id).children).includes("해당 없음") && text(row(retryRule.id).children).includes("All reference values are 0"));
  assert.ok(text(row(retryRule.id).children).includes("≥ 3 count") && !text(row(retryRule.id).children).includes("→"), "non-proposed rows show only the current value");
  assert.ok(text(row(totalRule.id).children).includes("거부") && text(row(totalRule.id).children).includes("invalid_generation_output"), "reason is in the status cell");
  const checkbox = (id) => row(id).findAll((node) => node.props["aria-label"]?.endsWith(" 선택") && node.props.onCheckedChange)[0];
  assert.deepEqual([checkbox(softRule.id).props.disabled, checkbox(ratioRule.id).props.disabled, checkbox(retryRule.id).props.disabled, checkbox(totalRule.id).props.disabled], [false, false, true, true], "only proposed rows are selectable");
  // 선택 해제하면 적용 불가, 다시 고르면 적용. 적용은 고른 규칙의 입력칸만 바꾼다.
  await act(async () => checkbox(softRule.id).props.onCheckedChange(false));
  await act(async () => checkbox(ratioRule.id).props.onCheckedChange(false));
  assert.equal(app.button("적용").props.disabled, true);
  assert.ok(text(box("done").children).includes("선택 0건"));
  await act(async () => checkbox(softRule.id).props.onCheckedChange(true));
  const saves = calls("PUT", "/api/rule-set").length;
  await app.click("적용");
  assert.equal(thresholdOf(softRule.name), "7210.5");
  assert.equal(thresholdOf(sameFieldRule.name), "9000", "same field but not listed: untouched");
  assert.equal(thresholdOf(retryRule.name), "3", "not proposed: input untouched");
  assert.equal(thresholdOf(offRule.name), "0.02", "off rule untouched");
  assert.equal(thresholdOf(noResultRule.name), "2", "rule without a result untouched");
  assert.equal(thresholdOf(ratioRule.name), "0.01", "unselected proposal untouched");
  assert.ok(app.shown().includes("변경 1건"));
  assert.ok(text(box("applied").children).includes(`1건을 입력칸에 채움(${softRule.name}) · 저장해야 운영에 반영됩니다`));
  assert.equal(calls("PUT", "/api/rule-set").length, saves, "apply does not save");
  assert.equal(app.renderer.root.findAll((node) => node.type === "select" && node.props["aria-label"] === `${softRule.name} 위험도`)[0].props.value, "warning", "severity stays with the person");
  // 미저장 변경이 있으면 저장된 버전 기준 작업을 시작하지 않는다.
  assert.equal(app.button("AI 규칙 추천").props.disabled, true);
  assert.ok(app.shown().includes("저장 후 추천"));
  await app.click("닫기");
  const setRuleThreshold = async (name, value) => act(async () => app.renderer.root.findAll((node) => node.type === "input" && node.props["aria-label"] === `${name} 임계값`)[0].props.onChange({ target: { value } }));
  await setRuleThreshold(softRule.name, "5000");
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "clean draft can run again");

  // 작업 실패: 전체 실패 표시, 입력칸 그대로, 다시 시도는 새 작업. 시간 초과는 따로.
  jobResponse = failedJob("generation_service_unavailable");
  await app.click("AI 규칙 추천");
  await wait(() => box("failed"), "job failure");
  assert.ok(text(box("failed").children).includes("추천 실패 · generation_service_unavailable · 작업 전체가 실패해 부분 결과가 없습니다. 입력칸은 그대로입니다."));
  assert.equal(rows().length, 0, "no partial result");
  assert.equal(thresholdOf(softRule.name), "5000");
  jobResponse = failedJob("generation_timeout");
  const postsBeforeRetry = jobCalls().length;
  await app.click("다시 시도");
  await wait(() => box("failed") && text(box("failed").children).includes("시간 초과 · generation_timeout"), "timeout is separate");
  assert.equal(jobCalls().length, postsBeforeRetry + 1, "retry after a failed job is a new request");

  // 결과가 계약과 다르면 실패로 끝나고 페이지 오류가 나지 않는다.
  const validItem = contractResult.items[0];
  const invalidResults = [
    { ...contractResult, items: [] }, { ...contractResult, items: [null] }, { ...contractResult, items: "x" }, { items: [validItem] }, null,
    { ...contractResult, items: [{ ...validItem, evidence: {} }] }, { ...contractResult, items: [{ ...validItem, evidence: null }] },
    { ...contractResult, items: [{ ...validItem, threshold: undefined }] }, { ...contractResult, items: [{ ...validItem, threshold: 7210.5 }] },
    { ...contractResult, items: [{ ...validItem, threshold: "1e5" }] }, { ...contractResult, items: [{ ...validItem, displayThreshold: null }] },
    { ...contractResult, items: [{ ...validItem, status: "unknown" }] }, { ...contractResult, items: [{ ...validItem, source: "unknown" }] },
    { ...contractResult, items: [{ ...validItem, operator: "eq" }] }, { ...contractResult, items: [{ ...validItem, softRuleIds: "x" }] },
    { ...contractResult, items: [{ ...validItem, status: "no_applicable" }] },
    { ...contractResult, items: [{ ...contractResult.items[2], status: "unknown" }] },
    { ...contractResult, items: [{ ...validItem, evidence: { ...validItem.evidence, n: "46980" } }] }, { ...contractResult, items: [{ ...validItem, evidence: { ...validItem.evidence, n: undefined } }] },
    { ...contractResult, items: [{ ...validItem, softRuleIds: [] }] }, { ...contractResult, items: [{ ...validItem, softRuleIds: ["soft:elapsed_ms-00000000"] }] },
    { ...contractResult, items: [{ ...validItem, softRuleIds: [offRule.id] }] },
  ];
  for (const invalid of invalidResults) {
    assert.equal(parseRecommendation(invalid, current), null, JSON.stringify(invalid)?.slice(0, 120));
    jobResponse = succeeded(invalid);
    const posts = jobCalls().length;
    await app.click("다시 시도");
    await wait(() => jobCalls().length === posts + 1 && box("failed") && text(box("failed").children).includes("recommendation_result_invalid"), "invalid result fails");
    assert.equal(rows().length, 0);
  }

  // 충돌: 재조회로 버전이 바뀌어도 실패 표시가 남고, 다시 시도는 새 버전으로 요청한다.
  const conflictRoute = routes["POST /api/jobs"];
  routes["POST /api/jobs"] = () => {
    routes["GET /api/rule-set"] = () => json({ ...current, revision: 14 });
    return json({ code: "rule_set_revision_conflict", currentRevision: 14 }, 409);
  };
  await app.click("다시 시도");
  await wait(() => app.shown().includes("현재 운영 버전 14"), "conflict reloads the rule set");
  await tick(30);
  assert.ok(box("failed") && text(box("failed").children).includes("추천 실패 · rule_set_revision_conflict"), "failure stays after the revision changes");
  routes["POST /api/jobs"] = conflictRoute;
  jobResponse = succeeded(contractResult);
  await app.click("다시 시도");
  await wait(() => box("done"), "result after conflict");
  assert.equal(jobCalls().at(-1).body.ruleSetRevision, 14, "retry uses the new revision");
  // 저장으로 버전이 바뀌면 이전 버전 기준 결과는 닫힌다.
  routes["GET /api/rule-set"] = () => json({ ...current, revision: 15 });
  routes["POST /api/rule-set/preview"] = () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes: [{ type: "soft_updated", id: softRule.id, name: softRule.name, diff: { threshold: [5000, 5100] } }] });
  routes["PUT /api/rule-set"] = () => json({ revision: 15, previousRevision: 14, contentHash: "c".repeat(64), appliedAt: "2026-11-02T08:20:05Z", appliesFrom: "next_decision", changes: [], summary: {} });
  await setRuleThreshold(softRule.name, "5100");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirmation");
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 15"), "saved");
  await tick(30);
  assert.equal(Boolean(box("done")), false, "results of the previous revision are closed");

  // 진행 중은 5초마다 조회한다. 조회 실패는 작업 실패가 아니고 조회가 멈추며, 다시 시도는 같은 작업을 읽고 새 작업을 만들지 않는다.
  let reads = 0;
  jobResponse = () => {
    reads += 1;
    if (reads === 1) return json({ kind: "recommend_thresholds", status: "running", inputSpec: {}, resultSummary: { progress: { processed: 2, total: 6 } } });
    if (reads === 2) return json({ code: "job_storage_unavailable" }, 503);
    return succeeded(contractResult)();
  };
  const postsBeforePoll = jobCalls().length;
  await app.click("AI 규칙 추천");
  await wait(() => reads === 1 && box("running"), "job is running");
  assert.ok(app.shown().includes("AI 규칙 추천 실행 중…") && !app.shown().includes("AI 임계값"), "running text uses the screen name");
  assert.ok(text(box("running").children).includes("켜진 이상 징후 규칙 6개"));
  // 진행률(llm-recommendation-job.md 7차): 막대 값 + 승인 문구 `처리 n/전체`(사용자 11/14).
  const bar = () => box("running").findAll((node) => node.type === "div" && node.props.role === "progressbar")[0];
  await wait(() => bar()?.props["aria-valuenow"] === 2, "progress value from the running job");
  assert.deepEqual([bar().props["aria-valuenow"], bar().props["aria-valuemax"], bar().props["aria-valuemin"], bar().props["aria-label"]], [2, 6, 0, "AI 규칙 추천 실행 중"]);
  assert.deepEqual(box("running").findAll((node) => node.type === "span" && node.props["data-ai-progress"] !== undefined).map((node) => text(node.children)), ["처리 2/6"], "progress number text");
  await wait(() => box("readFailed"), "a read failure after the 5 second poll", 800);
  assert.equal(reads, 2, "polled once more while running");
  assert.ok(text(box("readFailed").children).includes("AI 규칙 추천 조회 실패 · job_storage_unavailable"));
  assert.equal(Boolean(box("failed")), false, "a read failure is not a job failure");
  assert.equal(app.button("AI 규칙 추천").props.disabled, true, "no new job while the old one may be alive");
  await act(async () => new Promise((resolve) => setTimeout(resolve, 5600)));
  assert.equal(reads, 2, "polling stops after a read failure");
  await app.click("다시 시도");
  await wait(() => box("done"), "same job is read again");
  assert.equal(reads, 3);
  assert.equal(jobCalls().length, postsBeforePoll + 1, "retry of a read creates no new job");

  // Rule v3 결과(계약 7차 예시): 기준 문구와 한국어 근거. Rule v3 실패 코드는 기존 형식 그대로.
  await app.click("닫기");
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "closed before rule v3");
  jobResponse = succeeded(v3Result);
  await app.click("AI 규칙 추천");
  await wait(() => box("done"), "rule v3 result");
  assert.deepEqual(rows().map((node) => node.props["data-ai-row"]), [softRule.id]);
  const v3Row = text(row(softRule.id).children);
  assert.deepEqual(row(softRule.id).findAll((node) => node.type === "span" && node.props["data-ai-basis"] !== undefined).map((node) => text(node.children)), ["기준 · 학습 기간 앞 80% p95"]);
  assert.ok(v3Row.includes("> 5,000 ms → > 7,180 ms") && v3Row.includes("참조 기간 유효 값 30100개의 p95보다 큰"), v3Row);
  assert.deepEqual(box("done").findAll((node) => node.type === "span" && node.props["data-ai-held"] !== undefined).map((node) => text(node.children)), ["보류 기간 제외 · 9,370행"], "held-window exclusion in the result summary");
  for (const hidden of ["train_reference_80_p95", "rule_v3", "baseline_…", "attempts", "currentN"]) assert.ok(!text(box("done").children).includes(hidden), `${hidden} is not on the screen`);
  for (const code of ["rule_v3_not_configured", "recommendation_rule_input_rejected", "rule_v3_model_failure", "generation_adapter_version_mismatch"]) {
    await app.click("닫기");
    await wait(() => app.button("AI 규칙 추천").props.disabled === false, `closed before ${code}`);
    jobResponse = failedJob(code);
    await app.click("AI 규칙 추천");
    await wait(() => box("failed") && text(box("failed").children).includes(`추천 실패 · ${code} · 작업 전체가 실패해 부분 결과가 없습니다.`), code);
  }

  // 서버 거절: 코드 그대로, 예시로 대체하지 않음.
  routes["POST /api/jobs"] = () => json({ code: "invalid_job_request" }, 400);
  await app.click("닫기");
  await wait(() => app.button("AI 규칙 추천").props.disabled === false, "closed");
  await app.click("AI 규칙 추천");
  await wait(() => box("failed") && text(box("failed").children).includes("추천 실패 · invalid_job_request"), "server rejection shown");
  assert.equal(rows().length, 0, "a rejected request shows no result rows");
  assert.ok(!app.shown().includes("The 95th percentile"));
  await app.cleanup();
  globalThis.fetch = realFetch;

  // 켜진 규칙이 없으면 비활성.
  routes = baseRoutes({ "GET /api/rule-set": () => json(state(12, { softRules: [{ ...softRule, enabled: false }] })) });
  const none = await mount();
  await wait(() => none.shown().includes("현재 운영 버전 12"), "header");
  await wait(() => calls("GET", "/api/pipeline/sources").length > 0, "sources requested");
  await tick(30);
  assert.equal(none.button("AI 규칙 추천").props.disabled, true);
  await none.cleanup();
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
  // LLM 생성 서비스 꺼짐(GET /api/llm/status available=false, llm-recommendation-job.md '생성 서비스 가용성' 절): 버튼 비활성 + 코드 그대로.
  // 수식 추천(LLM 없음)은 막지 않는다. 상태 조회 실패(이 파일의 다른 시험처럼 경로 없음 → 503)면 위 시험처럼 막지 않는다.
  routes = baseRoutes({ "GET /api/llm/status": () => json({ available: false, checkedAt: "2026-11-14T01:00:00.000Z", errorCode: "generation_service_unavailable" }) });
  const postsBefore = calls("POST", "/api/jobs").length;
  const off = await mount();
  await wait(() => off.shown().includes("AI 규칙 추천 · generation_service_unavailable"), "unavailable code shown");
  assert.equal(off.button("AI 규칙 추천").props.disabled, true, "AI rule recommendation disabled while the generation service is off");
  assert.notEqual(off.button("수식 추천").props.disabled, true, "formula recommendation is not gated");
  assert.equal(calls("POST", "/api/jobs").length, postsBefore, "no job request");
  await off.cleanup();
}

// 8) 조회 실패는 빈 룰로 보이지 않는다.
{
  routes = baseRoutes({ "GET /api/rule-set": () => json({ code: "rule_set_storage_unavailable" }, 503) });
  const app = await mount();
  await wait(() => app.shown().includes("현재 운영 룰 조회 실패 · rule_set_storage_unavailable"), "error state");
  // 사이드바 목차(영역 이름)는 데이터와 무관하게 있으므로 본문만 본다.
  const body = app.shown().replace(text(app.renderer.root.findByProps({ id: "detection-menu" })), "");
  assert.ok(!body.includes("이상 규칙") && !body.includes("변경 0건"), "no editor without data");
  assert.ok(app.button("다시 조회"));
  await app.cleanup();
  routes = baseRoutes({ "GET /api/jobs/capabilities": () => json({ models: [] }) });
  const noCatalog = await mount();
  await wait(() => noCatalog.shown().includes("등록 필드 조회 실패 · method_catalog_missing"), "missing catalog is an error");
  assert.equal(noCatalog.button("규칙 추가").props.disabled, true);
  await noCatalog.cleanup();
}

// 9) 추가 입력: 이름·임계값 필수, 필드 단위 표시, 추가 값 전달. 대상 필드 안내.
{
  const added = [];
  let body;
  await act(async () => { body = create(React.createElement(RuleAddBody, { fields: capabilities.methodCatalog.softRuleFields, onAdd: (rule) => added.push(rule), onCancel() {} })); });
  const shown = () => text(body.toJSON());
  assert.ok(shown().includes("대상 필드는 추가한 뒤 바꿀 수 없습니다."));
  const addSeverity = body.root.findAll((node) => node.type === "select" && node.props.id === "rule-add-severity")[0];
  assert.deepEqual(optionsOf(addSeverity), SEVERITY_OPTIONS, "the add dialog lists severities in the same order");
  assert.equal(addSeverity.props.value, "warning", "the default does not follow the option order");
  const add = () => body.root.findAll((node) => node.type === "button" && text(node.children) === "추가")[0];
  await act(async () => add().props.onClick());
  assert.ok(shown().includes("규칙 이름과 임계값(숫자)을 입력해 주세요."));
  assert.equal(added.length, 0);
  await act(async () => body.root.findByProps({ id: "rule-add-name", "data-slot": "input" }).props.onChange({ target: { value: " 재시도 3회 " } }));
  await act(async () => body.root.findByProps({ id: "rule-add-threshold", "data-slot": "input" }).props.onChange({ target: { value: "3" } }));
  await act(async () => add().props.onClick());
  assert.deepEqual(added, [{ name: "재시도 3회", field: "retry_count", operator: "gt", threshold: "3", severity: "warning" }]);
  await act(async () => body.unmount());
}

// 10) 초안 순수 규칙: 변경 건수 단위, 새 규칙 clientRef, 삭제.
{
  const base = draft.draftFromState(state());
  assert.equal(draft.draftChangeCount(base), 0);
  const edited = { ...base, soft: [], hard: base.hard.map((rule) => ({ ...rule, enabled: false })) };
  assert.equal(draft.draftChangeCount(edited), 3, "two hard toggles and one removal");
  const added = { ...base, soft: [...base.soft, { key: "new-1", id: null, name: "x", field: "retry_count", operator: "gt", threshold: "3", severity: "info", enabled: true }] };
  assert.deepEqual(draft.saveRequest(added, false).softRules.at(-1), { id: null, clientRef: "new-1", name: "x", field: "retry_count", operator: "gt", threshold: 3, severity: "info", enabled: true, scope: null });
  assert.equal(draft.numberLabel(0.00003125), "0.00003125", "small thresholds stay visible");
  assert.deepEqual(draft.severityGroups("critical"), [{ level: "critical", codes: [] }]);
  // byCode는 코드를 자동으로 묶는다: 코드가 늘어도 수정 없이 위험 → 주의 → 참고 순서.
  assert.deepEqual(draft.severityGroups({ byCode: { PROCESS_STATUS_Z: "info", PROCESS_STATUS_F: "critical", PROCESS_STATUS_P: "warning", PROCESS_STATUS_H: "critical", PROCESS_STATUS_N: "warning" } }),
    [{ level: "critical", codes: ["PROCESS_STATUS_F", "PROCESS_STATUS_H"] }, { level: "warning", codes: ["PROCESS_STATUS_P", "PROCESS_STATUS_N"] }, { level: "info", codes: ["PROCESS_STATUS_Z"] }]);
}
console.log("rules page ok");

// 11) v3 초안: 관계 규칙 켜기·위험도 편집, 단일·기본 단계 위험도, 프로토콜 항목은 그대로 실린다.
{
  const relation = { id: "hard:rel-0000000a", kind: "precedes", scope: { interfaceId: "IF_A", adapterType: "HTTP" }, from: { processId: "P1" }, to: { processId: "P2" },
    match: null, severity: "critical", enabled: true, source: { jobId: "j" }, createdAtRevision: 3, updatedAtRevision: 3 };
  const protocol = { id: "hard:proto-0000000b", interfaceId: "IF_A", paths: [{ channels: ["CH_A", "CH_B"], source: { kind: "draft" } }], severity: "critical", enabled: true,
    source: null, createdAtRevision: 3, updatedAtRevision: 3 };
  const v3Hard = [...hardRules(),
    { id: "hard:transaction-failed", methodVersion: "transaction-failed-v1", displayName: "거래 실패", conditionText: "x", severity: { byCode: {}, default: "warning" }, enabled: false, editable: ["enabled", "severity"] },
    { id: "hard:unregistered-interface", methodVersion: "unregistered-interface-v1", displayName: "등록되지 않은 인터페이스", conditionText: "y", severity: "critical", enabled: true, editable: ["enabled", "severity"] }];
  const base = draft.draftFromState(state(12, { hardRules: v3Hard, relationRules: [relation], protocolRules: [protocol], softRules: [{ ...softRule, scope: { processId: "P2", interfaceId: "IF_A" } }] }));
  const request = draft.saveRequest(base, false);
  assert.deepEqual(request.relationRules, [{ id: relation.id, kind: "precedes", scope: relation.scope, from: relation.from, to: relation.to, match: null, severity: "critical", enabled: true }], "source is not resent");
  assert.deepEqual(request.protocolRules, [{ id: protocol.id, interfaceId: "IF_A", paths: [{ channels: ["CH_A", "CH_B"] }], severity: "critical", enabled: true }]);
  assert.deepEqual(request.softRules[0].scope, { processId: "P2", interfaceId: "IF_A" }, "stored scope is resent unchanged");
  assert.deepEqual(request.hardRules.slice(2).map((rule) => rule.severity), [{ byCode: {}, default: "warning" }, "critical"]);
  const edited = { ...base, relation: [{ id: relation.id, enabled: false, severity: "info" }],
    hard: base.hard.map((rule) => (rule.id === "hard:unregistered-interface" ? { ...rule, severity: "warning" } : rule.id === "hard:transaction-failed" ? { ...rule, severity: { byCode: {}, default: "critical" } } : rule)) };
  assert.equal(draft.draftChangeCount(edited), 4, "relation toggle + relation severity + two hard severities");
  assert.deepEqual(draft.saveRequest(edited, false).relationRules[0], { id: relation.id, kind: "precedes", scope: relation.scope, from: relation.from, to: relation.to, match: null, severity: "info", enabled: false });
  assert.equal(draft.changeLine({ type: "protocol_updated", id: protocol.id, interfaceId: "IF_A", diff: { pathsAdded: [["CH_A", "CH_C"]], pathsRemoved: [], severity: ["critical", "warning"] } }),
    "정상 프로토콜 수정 · IF_A · 허용 경로 추가 CH_A → CH_C, 위험도 위험 → 주의");
  assert.equal(draft.changeLine({ type: "relation_added", id: null, clientRef: "c", kind: "required_step", interfaceId: "IF_A", from: "P1", to: null }), "관계 규칙 추가 · 필수 단계 P1");
  assert.equal(draft.changeLine({ type: "hard_updated", id: "hard:transaction-failed", displayName: "거래 실패", diff: { severity: [{ byCode: {}, default: "warning" }, { byCode: { E901: "critical" }, default: "warning" }] } }),
    "이상 규칙 수정 · 거래 실패 · 위험도 E901 — → 위험");
  const restore = draft.restoreRequest({ ...historyItem(9), snapshot: { hardRules: v3Hard, softRules: [], relationRules: [relation], protocolRules: [protocol] } }, state(12, { hardRules: v3Hard }), false);
  assert.deepEqual([restore.relationRules.length, restore.protocolRules[0].paths, restore.hardRules[3].severity], [1, [{ channels: ["CH_A", "CH_B"] }], "critical"]);
}
console.log("rules page v3 ok");
